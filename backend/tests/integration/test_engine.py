"""Motor de extracción de punta a punta: API + PostgreSQL + Gateway simulado + carpeta CSV.

Criterios de aceptación del plan 06 que se comprueban aquí:

* Matar el worker en cualquier paso y relanzar no pierde ni duplica datos.
* Un fallo de carga se reintenta sin volver a llamar a SAP cuando el spool sigue en el worker.
* Un worker dado por caído que vuelve no puede confirmar el token.
* Un token inválido deja el pipeline en ``BROKEN`` sin avanzar estado.

La «muerte» del worker se simula con una excepción que no es ``Exception`` (``Crash``): el
motor no la captura, el intento queda ``running`` sin latido y el scheduler lo da por caído.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select, update

from sapextract.core.clock import utcnow
from sapextract.core.config import Settings
from sapextract.core.db import Database
from sapextract.dataflow.domain.batch import BatchInfo
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    AppliedBatch,
    DestinationWriter,
    LoadOptions,
    LoadResult,
    PreparedTarget,
    StageInfo,
)
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.dataflow.domain.spool import Spool
from sapextract.destinations.application.plugins import PluginContext, PluginRegistry
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.destinations.infrastructure.csv_plugin import CsvPlugin
from sapextract.extraction.application.engine import ExtractionEngine
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.store import Claim, RunStore
from sapextract.extraction.application.worker import Worker
from sapextract.extraction.domain.model import AttemptOutcome
from sapextract.extraction.infrastructure.models import DeltaState, Run, RunAttempt
from sapextract.extraction.infrastructure.odata_connector import (
    ODataSourceConnector,
    ODataSourceRef,
)
from sapextract.main_api import create_app
from sapextract.sap_connections.domain.model import ConnectionSpec
from tests.integration.conftest import (
    ADMIN_USERNAME,
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    unique,
)
from tests.support.sap_gateway import MOCK_PASSWORD, MOCK_USER, RunningGateway, start_gateway

pytestmark = pytest.mark.integration

GL_SERVICE, GL_SET = "ZFI_WS_GL_ITEMS_SRV", "EntityOf0FI_GL_14"
MATERIAL_SERVICE, MATERIAL_SET = "ZMM_CV_MATERIAL_CDS", "ZMM_CV_MATERIAL"
ROWS = 250  # tres páginas de 100


class Crash(BaseException):
    """Muerte del worker: el motor no la captura (no es ``Exception``)."""


# ====================================================================== dobles con fallos
@dataclass
class Faults:
    """Puntos de fallo: nombre → excepciones a lanzar, en orden (se consumen)."""

    plan: dict[str, list[BaseException]] = field(default_factory=dict)
    reads: int = 0

    def hit(self, point: str) -> None:
        pending = self.plan.get(point)
        if pending:
            raise pending.pop(0)


class FaultyWriter:
    def __init__(self, inner: DestinationWriter, faults: Faults) -> None:
        self.inner, self.faults = inner, faults

    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        return self.inner.prepare(schema, options)

    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None:
        return self.inner.applied_batch(target, batch_id)

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        self.faults.hit("stage")
        return self.inner.stage(target, spool)

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        self.faults.hit("before_apply")
        result = self.inner.apply(stage, batch)
        self.faults.hit("after_apply")
        return result

    def cleanup(self, stage: StageInfo) -> None:
        self.inner.cleanup(stage)


class FaultyCsvPlugin(CsvPlugin):
    def __init__(self, faults: Faults) -> None:
        self.faults = faults

    @contextmanager
    def _wrapped(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> Iterator[DestinationWriter]:
        with super().open_writer(config, secret, ctx) as writer:
            yield FaultyWriter(writer, self.faults)

    def open_writer(  # type: ignore[override]  # envuelve el escritor CSV en uno con fallos
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> AbstractContextManager[DestinationWriter]:
        return self._wrapped(config, secret, ctx)


class FaultyStore(RunStore):
    def __init__(self, db: Database, audit: Any, faults: Faults) -> None:
        super().__init__(db, audit)
        self.faults = faults

    def set_pending_token(self, claim: Claim, token: str) -> None:
        self.faults.hit("pending_token")
        super().set_pending_token(claim, token)

    def commit(self, claim: Claim, **kwargs: Any) -> None:
        self.faults.hit("commit")
        super().commit(claim, **kwargs)


class CountingConnector:
    """Conector real que cuenta las lecturas a SAP."""

    def __init__(self, inner: ODataSourceConnector, faults: Faults) -> None:
        self.inner, self.faults = inner, faults

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if name.startswith("read_"):

            def counted(*args: Any, **kwargs: Any) -> Any:
                self.faults.reads += 1
                self.faults.hit("read")
                return attr(*args, **kwargs)

            return counted
        return attr


# ====================================================================== entorno
@pytest.fixture(scope="module")
def csv_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tmp_path_factory.mktemp("engine-csv"))


@pytest.fixture(scope="module")
def engine_settings(
    app_settings: Settings, tmp_path_factory: pytest.TempPathFactory, csv_root: Path
) -> Settings:
    return app_settings.model_copy(
        update={
            "csv_roots": [csv_root],
            "spool_dir": Path(tmp_path_factory.mktemp("spool")),
            "worker_heartbeat_seconds": 0.05,
            "run_retry_backoff_seconds": [0, 0, 0],
            "watermark_safety_lag_seconds": 0,
        }
    )


@pytest.fixture(scope="module")
def engine_app(engine_settings: Settings, platform_db: Database) -> FastAPI:
    return create_app(engine_settings)


@pytest.fixture
def client(engine_app: FastAPI, admin_password: str) -> ApiClient:
    api = ApiClient(engine_app)
    assert api.login(ADMIN_USERNAME, admin_password) == "active"
    return api


@pytest.fixture
def gateway() -> Iterator[RunningGateway]:
    running = start_gateway(rows=ROWS)
    yield running
    running.stop()


@dataclass
class Env:
    client: ApiClient
    gateway: RunningGateway
    app: FastAPI
    db: Database
    csv_root: Path
    spool_root: Path
    faults: Faults = field(default_factory=Faults)

    @property
    def settings(self) -> Settings:
        settings: Settings = self.app.state.settings
        return settings

    def toolkit(self) -> ExtractionToolkit:
        base: ExtractionToolkit = self.app.state.extraction
        destinations = DestinationsToolkit.build(
            self.settings,
            audit=base.audit,
            registry=PluginRegistry([FaultyCsvPlugin(self.faults)]),
        )
        faults = self.faults

        def factory(ref: ODataSourceRef, spec: ConnectionSpec) -> Any:
            return CountingConnector(base.connector_factory(ref, spec), faults)

        return replace(base, destinations=destinations, connector_factory=factory)

    def engine(self, worker_id: str) -> tuple[ExtractionEngine, RunStore]:
        toolkit = self.toolkit()
        store = FaultyStore(self.db, toolkit.audit, self.faults)
        engine = ExtractionEngine(
            self.db, toolkit, store, worker_id=worker_id, spool_root=self.spool_root
        )
        return engine, store

    def worker(self, worker_id: str = "w1") -> Worker:
        worker = Worker(self.db, self.toolkit(), worker_id=worker_id, spool_root=self.spool_root)
        worker.store = FaultyStore(self.db, worker.store.audit, self.faults)
        worker.engine.store = worker.store
        return worker

    def mock(self, path: str, body: dict[str, Any] | None = None) -> None:
        self.gateway.control(path, body)


def _clear_queue(db: Database) -> None:
    """Cada prueba empieza con la cola vacía: el worker no debe tomar runs de otra prueba."""
    with db.session() as session:
        session.execute(update(DeltaState).values(pending_run_id=None, pending_token=None))
        session.execute(
            update(Run)
            .where(Run.status.in_(["queued", "running"]))
            .values(status="cancelled", finished_at=utcnow())
        )
        session.commit()


@pytest.fixture
def env(  # noqa: PLR0917 - fixtures de pytest
    client: ApiClient,
    gateway: RunningGateway,
    engine_app: FastAPI,
    platform_db: Database,
    csv_root: Path,
    engine_settings: Settings,
) -> Iterator[Env]:
    _clear_queue(platform_db)
    yield Env(client, gateway, engine_app, platform_db, csv_root, engine_settings.spool_dir)
    _clear_queue(platform_db)


# ====================================================================== altas por la API
def _system(env: Env) -> str:
    response = env.client.post(
        "/sap-systems",
        {
            "code": unique("ENG_").upper(),
            "name": "S/4HANA pruebas del motor",
            "environment": "DEV",
            "system_type": "S4HANA_ONPREM",
            "base_url": env.gateway.base_url,
            "sap_client": "100",
            "language": "ES",
            "timeout_seconds": 10,
            "credentials": {"auth_type": "basic", "username": MOCK_USER, "password": MOCK_PASSWORD},
            "activate": True,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["system"]["id"])


def _source(env: Env, system_id: str, service: str, entity_set: str, **extra: Any) -> str:
    response = env.client.post(
        "/sources",
        {
            "sap_system_id": system_id,
            "code": unique("SRC_").upper(),
            "name": f"Origen {entity_set}",
            "service_name": service,
            "entity_set": entity_set,
            "page_size": 100,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _csv(env: Env) -> str:
    response = env.client.post(
        "/destinations",
        {
            "code": unique("CSV_").upper(),
            "name": "Carpeta del motor",
            "type": "csv",
            "environment": "DEV",
            "config": {"root": str(env.csv_root), "path": unique("p")},
            "run_test": True,
            "activate": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["destination"]["status"] == "active"
    return str(response.json()["destination"]["id"])


def _pipeline(env: Env, source_id: str, destination_id: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": unique("PPL_").upper(),
        "name": "GL hacia CSV",
        "source_id": source_id,
        "destination_id": destination_id,
        "extraction_mode": "DELTA",
        "load_options": {"load_mode": "append", "target_name": "GL_ITEMS"},
        "max_attempts": 3,
    }
    body.update(extra)
    response = env.client.post("/pipelines", body)
    assert response.status_code == 201, response.text
    pipeline: dict[str, Any] = response.json()
    return pipeline


def _gl_pipeline(env: Env, **extra: Any) -> dict[str, Any]:
    system = _system(env)
    source = _source(
        env, system, GL_SERVICE, GL_SET, delta={"method": "ODQ", "semantics": "after_image"}
    )
    return _pipeline(env, source, _csv(env), **extra)


def _run_now(env: Env, pipeline_id: str) -> dict[str, Any]:
    response = env.client.post(f"/pipelines/{pipeline_id}/runs")
    assert response.status_code == 202, response.text
    run: dict[str, Any] = response.json()
    return run


def _run(env: Env, run_id: str) -> dict[str, Any]:
    response = env.client.get(f"/runs/{run_id}")
    assert response.status_code == 200, response.text
    run: dict[str, Any] = response.json()
    return run


def _delta(env: Env, pipeline_id: str) -> dict[str, Any]:
    response = env.client.get(f"/pipelines/{pipeline_id}/delta")
    assert response.status_code == 200, response.text
    state: dict[str, Any] = response.json()
    return state


def _drain(worker: Worker, limit: int = 10) -> list[AttemptOutcome]:
    outcomes = []
    for _ in range(limit):
        outcome = worker.poll_once()
        if outcome is None:
            break
        outcomes.append(outcome)
    return outcomes


def _target(env: Env, pipeline: dict[str, Any]) -> Path:
    destination = env.client.get(f"/destinations/{pipeline['destination']['id']}").json()
    return env.csv_root / str(destination["config"]["path"]) / "GL_ITEMS"


def _applied(env: Env, pipeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    control = _target(env, pipeline) / "_sx_load_state"
    return {p.stem: json.loads(p.read_text("utf-8")) for p in sorted(control.glob("*.json"))}


def _batch_rows(env: Env, pipeline: dict[str, Any], batch_id: str) -> int:
    folder = _target(env, pipeline) / "batches" / batch_id
    return sum(len(p.read_text("utf-8").splitlines()) - 1 for p in folder.glob("part-*.csv"))


def _mock_tokens(env: Env) -> list[str]:
    response = httpx.get(f"{env.gateway.base_url}/_mock/services", verify=env.gateway.verify)
    by_name = {s["service"]: s for s in response.json()}
    tokens: list[str] = by_name[GL_SERVICE]["tokens"]
    return tokens


def _init(env: Env, pipeline: dict[str, Any]) -> dict[str, Any]:
    run = _run_now(env, pipeline["id"])
    assert run["mode"] == "DELTA_INIT"
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    return _run(env, run["id"])


def _reap(env: Env) -> int:
    store = RunStore(env.db, env.app.state.identity.audit)
    return store.reap_stale(timedelta(seconds=0), now=utcnow() + timedelta(seconds=5))


# ====================================================================== flujo principal
def test_init_then_delta_and_repeat_to_csv(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    assert init["status"] == "succeeded"
    assert init["rows_extracted"] == ROWS
    assert init["batch_id"].startswith("i-")
    assert [s["step"] for s in init["steps"]] == [
        "prepare",
        "extract",
        "load",
        "commit_delta",
        "cleanup",
    ]
    state = _delta(env, pipeline["id"])
    assert state["status"] == "INITIALIZED"
    assert state["generation"] == 1
    assert state["current_token"] == _mock_tokens(env)[-1]
    assert [h["event"] for h in state["history"]] == ["initialized", "init_started"]
    assert _batch_rows(env, pipeline, init["batch_id"]) == ROWS

    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 5, "updates": 3, "deletes": 2})
    delta = _run_now(env, pipeline["id"])
    assert delta["mode"] == "DELTA"
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    done = _run(env, delta["id"])
    assert done["rows_extracted"] == 10
    assert done["start_token"] == init["next_token"]
    assert done["next_token"] == _delta(env, pipeline["id"])["current_token"]
    assert done["next_token"] != init["next_token"]
    assert done["batch_id"].startswith("d-")
    assert _batch_rows(env, pipeline, done["batch_id"]) == 10

    # Un delta sin cambios también confirma token (0 filas).
    empty = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    assert _run(env, empty["id"])["rows_extracted"] == 0

    logs = env.client.get(f"/runs/{delta['id']}/logs").json()
    messages = [line["message"] for line in logs["items"]]
    assert logs["finished"] is True
    assert any("Token de delta confirmado" in m for m in messages)
    assert all(done["next_token"] not in m for m in messages)  # los tokens no van al log
    after = env.client.get(f"/runs/{delta['id']}/logs", params={"after": logs["next_after"]})
    assert after.json()["items"] == []

    actions = {e["action"] for e in audit_actions(env.client, resource_id=delta["id"])}
    assert {"run.succeeded"} <= actions
    requested = audit_actions(env.client, resource_id=pipeline["id"])
    assert [e["action"] for e in requested].count("pipeline.run_requested") == 3


def test_second_run_is_rejected_while_one_is_queued(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _run_now(env, pipeline["id"])
    again = env.client.post(f"/pipelines/{pipeline['id']}/runs")
    assert again.status_code == 409
    assert again.json()["code"] == "SX-PPL-RUN-ACTIVE"


# ====================================================================== caídas en cada paso
@pytest.mark.parametrize(
    ("point", "sap_again"),
    [
        ("read", True),  # muere al empezar a leer de SAP
        ("pending_token", True),  # spool escrito, token sin guardar: otro worker re-extrae
        ("stage", True),  # spool en el worker caído: el otro vuelve a pedir el mismo delta
        ("after_apply", False),  # cargado sin confirmar: el otro ve la tabla de control
        ("commit", False),  # igual, justo antes de commit_delta
    ],
)
def test_worker_crash_at_each_step_neither_loses_nor_duplicates(
    env: Env, point: str, sap_again: bool
) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 4, "updates": 2, "deletes": 1})
    run = _run_now(env, pipeline["id"])

    first, first_store = env.engine("worker-a")
    claim = first_store.claim("worker-a")
    assert claim is not None
    env.faults.plan[point] = [Crash()]
    reads_before = env.faults.reads
    with pytest.raises(Crash):
        first.execute(claim)

    assert _run(env, run["id"])["status"] == "running"  # nadie sabe aún que murió
    assert _reap(env) == 1
    assert _run(env, run["id"])["status"] == "queued"

    reads_mid = env.faults.reads
    assert _drain(env.worker("worker-b")) == [AttemptOutcome.SUCCEEDED]
    assert (env.faults.reads > reads_mid) is sap_again

    done = _run(env, run["id"])
    assert done["status"] == "succeeded"
    assert [a["outcome"] for a in done["attempts"]] == ["stale", "succeeded"]
    assert done["start_token"] == init["next_token"]
    state = _delta(env, pipeline["id"])
    assert state["current_token"] == done["next_token"] == _mock_tokens(env)[-1]
    assert state["pending_run_id"] is None
    # Un único lote delta aplicado, con las 7 filas una sola vez.
    applied = _applied(env, pipeline)
    deltas = [b for b in applied if b.startswith("d-")]
    assert deltas == [done["batch_id"]]
    assert _batch_rows(env, pipeline, done["batch_id"]) == 7
    assert env.faults.reads >= reads_before


def test_load_failure_retries_on_same_worker_without_calling_sap(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 3})
    run = _run_now(env, pipeline["id"])
    env.faults.plan["stage"] = [
        DataflowError("Bloqueo en el destino", kind=ErrorKind.TRANSIENT, code="SX-DST-LOCK")
    ]
    reads = env.faults.reads
    worker = env.worker("w-same")
    assert _drain(worker) == [AttemptOutcome.FAILED_TRANSIENT, AttemptOutcome.SUCCEEDED]
    assert env.faults.reads == reads + 1  # el segundo intento no habló con SAP

    done = _run(env, run["id"])
    assert done["status"] == "succeeded"
    assert [a["outcome"] for a in done["attempts"]] == ["failed_transient", "succeeded"]
    assert done["attempts"][1]["has_spool"] is True
    logs = [line["message"] for line in env.client.get(f"/runs/{run['id']}/logs").json()["items"]]
    assert any("Se reutiliza el spool" in m for m in logs)
    assert _batch_rows(env, pipeline, done["batch_id"]) == 3


def test_retries_are_exhausted_and_token_does_not_move(env: Env) -> None:
    pipeline = _gl_pipeline(env, max_attempts=2)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 2})
    run = _run_now(env, pipeline["id"])
    error = DataflowError("Destino caído", kind=ErrorKind.TRANSIENT, code="SX-DST-DOWN")
    env.faults.plan["before_apply"] = [error, error]
    assert _drain(env.worker()) == [AttemptOutcome.FAILED_TRANSIENT, AttemptOutcome.FAILED]
    failed = _run(env, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SX-DST-DOWN"
    state = _delta(env, pipeline["id"])
    assert state["current_token"] == init["next_token"]
    assert state["pending_run_id"] is None
    assert state["status"] == "INITIALIZED"
    # El siguiente delta repite el mismo token de partida: mismo lote.
    again = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    repeated = _run(env, again["id"])
    assert repeated["start_token"] == init["next_token"]
    assert repeated["batch_id"] == failed["batch_id"]
    assert repeated["rows_extracted"] == 2


def test_zombie_worker_cannot_confirm(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 2})
    run = _run_now(env, pipeline["id"])

    zombie, zombie_store = env.engine("zombie")
    claim = zombie_store.claim("zombie")
    assert claim is not None
    assert _reap(env) == 1  # sin latido: el scheduler lo da por caído
    assert _drain(env.worker("healthy")) == [AttemptOutcome.SUCCEEDED]
    confirmed = _delta(env, pipeline["id"])["current_token"]

    assert zombie.execute(claim) is AttemptOutcome.FENCED
    assert _delta(env, pipeline["id"])["current_token"] == confirmed
    done = _run(env, run["id"])
    assert done["status"] == "succeeded"
    assert [a["outcome"] for a in done["attempts"]] == ["stale", "succeeded"]
    with env.db.session() as session:
        attempts = session.scalars(select(RunAttempt).where(RunAttempt.worker_id == "zombie")).all()
        assert [a.outcome for a in attempts] == ["stale"]  # su cierre no pisa el del scheduler


# ====================================================================== token inválido y reinicio
def test_invalid_token_breaks_pipeline_and_reset_flow(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/invalidate-tokens")
    run = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == [AttemptOutcome.FAILED]
    failed = _run(env, run["id"])
    assert failed["error_code"] == "SX-SAP-DELTA-INVALID"
    assert failed["attempts_used"] == 1  # un error de delta no se reintenta
    state = _delta(env, pipeline["id"])
    assert state["status"] == "BROKEN"
    assert state["current_token"] == init["next_token"]
    broken = audit_actions(env.client, action="pipeline.delta_broken")
    assert any(e["resource_id"] == pipeline["id"] for e in broken)

    blocked = env.client.post(f"/pipelines/{pipeline['id']}/runs")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SX-PPL-DELTA-BROKEN"

    no_reason = env.client.post(f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "x"})
    assert no_reason.status_code == 422
    reset = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "Suscripción borrada en ODQMON"}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["status"] == "NOT_INITIALIZED"
    assert reset.json()["sap_close_pending"] is True

    pending = env.client.post(f"/pipelines/{pipeline['id']}/runs")
    assert pending.status_code == 409
    assert pending.json()["code"] == "SX-PPL-SAP-CLOSE-PENDING"

    closed = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/sap-subscription", {"action": "closed"}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["sap_close_pending"] is False

    reinit = _run_now(env, pipeline["id"])
    assert reinit["mode"] == "DELTA_INIT"
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    state = _delta(env, pipeline["id"])
    assert state["status"] == "INITIALIZED"
    assert state["generation"] == 2
    events = [h["event"] for h in state["history"]]
    assert events[:4] == ["initialized", "init_started", "sap_subscription_closed", "reset"]
    actions = {e["action"] for e in audit_actions(env.client, resource_id=pipeline["id"])}
    assert {"pipeline.delta_reset", "pipeline.sap_subscription_closed"} <= actions


def test_failed_init_is_not_retried_and_leaves_subscription_pending(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    env.gateway.scenario(status=503, times=1)
    run = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == [AttemptOutcome.FAILED]  # H10: la init no se reintenta sola
    failed = _run(env, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SX-SAP-SERVER"
    state = _delta(env, pipeline["id"])
    assert state["status"] == "NOT_INITIALIZED"
    assert state["sap_close_pending"] is True
    assert state["history"][0]["event"] == "init_aborted"

    left_open = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/sap-subscription", {"action": "left_open"}
    )
    assert left_open.status_code == 422  # dejarla abierta exige motivo
    left_open = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/sap-subscription",
        {"action": "left_open", "reason": "Revisado en ODQMON: no se abrió"},
    )
    assert left_open.status_code == 200, left_open.text
    assert _run_now(env, pipeline["id"])["mode"] == "DELTA_INIT"


def test_init_failure_before_calling_sap_does_not_flag_subscription(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    run = _run_now(env, pipeline["id"])
    env.faults.plan["read"] = [
        DataflowError("Sin red", kind=ErrorKind.CONFIGURATION, code="SX-TEST-CONFIG")
    ]
    # El fallo ocurre al pedir la lectura: el intento ya había marcado la petición a SAP.
    assert _drain(env.worker()) == [AttemptOutcome.FAILED]
    assert _run(env, run["id"])["status"] == "failed"
    assert _delta(env, pipeline["id"])["sap_close_pending"] is True


# ====================================================================== cancelación
def test_cancel_queued_run_releases_nothing_in_sap(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    run = _run_now(env, pipeline["id"])
    cancelled = env.client.post(f"/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert _drain(env.worker()) == []
    again = env.client.post(f"/runs/{run['id']}/cancel")
    assert again.status_code == 409
    assert _delta(env, pipeline["id"])["status"] == "NOT_INITIALIZED"
    assert _delta(env, pipeline["id"])["sap_close_pending"] is False


def test_cancel_running_delta_keeps_token(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 3})
    run = _run_now(env, pipeline["id"])
    engine, store = env.engine("w-cancel")
    claim = store.claim("w-cancel")
    assert claim is not None
    response = env.client.post(f"/runs/{run['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["cancel_requested"] is True
    assert engine.execute(claim) is AttemptOutcome.CANCELLED
    done = _run(env, run["id"])
    assert done["status"] == "cancelled"
    assert _delta(env, pipeline["id"])["current_token"] == init["next_token"]
    assert _applied(env, pipeline).keys() == {init["batch_id"]}


# ====================================================================== completa y marca de agua
def test_full_replace_and_watermark_windows(env: Env) -> None:
    system = _system(env)
    material = _source(
        env,
        system,
        MATERIAL_SERVICE,
        MATERIAL_SET,
        delta={
            "method": "WATERMARK",
            "watermark_field": "LastChangeDateTime",
            "watermark_kind": "datetime",
            "watermark_timezone": "UTC",
            "watermark_overlap_seconds": 0,
        },
    )
    csv = _csv(env)
    full = _pipeline(
        env,
        material,
        csv,
        extraction_mode="FULL",
        load_options={"load_mode": "replace", "target_name": "MATERIAL_FULL"},
    )
    run = _run_now(env, full["id"])
    assert run["mode"] == "FULL"
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    done = _run(env, run["id"])
    assert done["rows_extracted"] == ROWS
    assert done["batch_id"].startswith("f-")
    assert _delta(env, full["id"])["status"] == "NOT_INITIALIZED"  # sin estado de delta

    wm = _pipeline(
        env,
        material,
        csv,
        extraction_mode="WATERMARK",
        load_options={"load_mode": "append", "target_name": "MATERIAL_WM"},
    )
    first = _run_now(env, wm["id"])
    assert first["mode"] == "WATERMARK"
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    first_done = _run(env, first["id"])
    assert first_done["rows_extracted"] == ROWS
    assert first_done["batch_id"].startswith("w-")
    state = _delta(env, wm["id"])
    assert state["status"] == "INITIALIZED"
    assert state["watermark_value"]["end"]

    env.mock(f"/_mock/services/{MATERIAL_SERVICE}/changes", {"inserts": 2, "updates": 3})
    time.sleep(2.1)  # la ventana acaba en el segundo de la cabecera Date (uvicorn la cachea)
    second = _run_now(env, wm["id"])
    assert _drain(env.worker()) == [AttemptOutcome.SUCCEEDED]
    second_done = _run(env, second["id"])
    assert second_done["rows_extracted"] == 5
    assert second_done["window_start"] is not None
    assert second_done["batch_id"] != first_done["batch_id"]


# ====================================================================== validaciones del pipeline
def test_pipeline_validations(env: Env) -> None:
    system = _system(env)
    gl = _source(env, system, GL_SERVICE, GL_SET, delta={"method": "ODQ", "semantics": "additive"})
    csv = _csv(env)
    upsert = env.client.post(
        "/pipelines",
        {
            "code": unique("PPL_").upper(),
            "name": "Upsert en CSV",
            "source_id": gl,
            "destination_id": csv,
            "extraction_mode": "DELTA",
            "load_options": {"load_mode": "upsert", "target_name": "GL"},
        },
    )
    assert upsert.status_code == 422
    assert upsert.json()["code"] == "SX-DST-MODE-UNSUPPORTED"
    assert upsert.json()["errors"][0]["field"] == "load_options.load_mode"

    replace_delta = env.client.post(
        "/pipelines",
        {
            "code": unique("PPL_").upper(),
            "name": "Reemplazo en delta",
            "source_id": gl,
            "destination_id": csv,
            "extraction_mode": "DELTA",
            "load_options": {"load_mode": "replace", "target_name": "GL"},
        },
    )
    assert replace_delta.json()["code"] == "SX-PPL-LOAD-MODE"

    first = _pipeline(env, gl, csv)
    second = env.client.post(
        "/pipelines",
        {
            "code": unique("PPL_").upper(),
            "name": "Segundo delta",
            "source_id": gl,
            "destination_id": csv,
            "extraction_mode": "DELTA",
            "load_options": {"load_mode": "append", "target_name": "GL2"},
        },
    )
    assert second.status_code == 422
    assert second.json()["code"] == "SX-PPL-DELTA-SHARED-SOURCE"
    full_ok = _pipeline(
        env,
        gl,
        csv,
        extraction_mode="FULL",
        load_options={"load_mode": "replace", "target_name": "GL_FULL"},
    )
    assert full_ok["extraction_mode"] == "FULL"

    in_use = env.client.delete(f"/sources/{gl}")
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "SX-SRC-IN-USE"
    dest_in_use = env.client.delete(f"/destinations/{csv}")
    assert dest_in_use.status_code == 409

    updated = env.client.patch(
        f"/pipelines/{first['id']}",
        {"version": first["version"], "name": "Nuevo nombre", "max_attempts": 5},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["max_attempts"] == 5
    stale = env.client.patch(
        f"/pipelines/{first['id']}", {"version": first["version"], "name": "X"}
    )
    assert stale.status_code == 409

    deleted = env.client.delete(f"/pipelines/{first['id']}", params={"reason": "Prueba"})
    assert deleted.status_code == 204
    assert env.client.get(f"/pipelines/{first['id']}").status_code == 404
    listing = env.client.get("/pipelines", params={"source_id": gl}).json()
    assert [p["id"] for p in listing["items"]] == [full_ok["id"]]
    actions = {e["action"] for e in audit_actions(env.client, resource_id=first["id"])}
    assert {"pipeline.created", "pipeline.updated", "pipeline.deleted"} <= actions


def test_viewer_cannot_run_or_reset(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    user = create_user(env.client, "viewer")
    viewer = ApiClient(env.app)
    activate_user(viewer, user["user"]["username"], user["temporary_password"])
    assert viewer.get(f"/pipelines/{pipeline['id']}").status_code == 200
    assert viewer.post(f"/pipelines/{pipeline['id']}/runs").status_code == 403
    assert (
        viewer.post(
            f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "Sin permiso"}
        ).status_code
        == 403
    )
