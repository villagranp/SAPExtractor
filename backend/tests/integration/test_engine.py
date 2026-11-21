"""Motor de extracción sobre la cola de la orquestación: API + PostgreSQL + Gateway simulado + CSV.

Criterios de aceptación del plan 06 que se comprueban aquí, con el worker y la recuperación de
runs caídos reales de la orquestación (plan 08):

* Matar el worker en cualquier paso y relanzar no pierde ni duplica datos.
* Un fallo de carga se reintenta sin volver a llamar a SAP cuando el spool sigue en el worker.
* Un worker dado por caído que vuelve no puede confirmar el token.
* Un token inválido deja el pipeline en ``BROKEN`` sin avanzar estado.

La «muerte» del worker se simula con una excepción que no es ``Exception`` (``Crash``): ni el
motor ni el worker la capturan, el intento queda abierto sin latido y se recupera como
``stale``. Cada prueba usa su propio *pool* de workers: solo su worker toma sus runs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select

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
from sapextract.extraction.application.delta_service import DeltaHooks
from sapextract.extraction.application.engine import ExtractionEngine
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.store import Claim, EngineStore
from sapextract.extraction.infrastructure.models import DeltaState
from sapextract.extraction.infrastructure.odata_connector import (
    ODataSourceConnector,
    ODataSourceRef,
)
from sapextract.main_api import create_app
from sapextract.orchestration.application.lifecycle import reap_stale
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.application.worker import WorkerRuntime
from sapextract.orchestration.domain.model import AttemptOutcome
from sapextract.orchestration.domain.ports import RunContext, RunStoppedError, StopReason
from sapextract.orchestration.infrastructure.models import RunAttempt
from sapextract.orchestration.infrastructure.queue import ClaimedRun, claim
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
    """Muerte del worker: ni el motor ni el worker la capturan (no es ``Exception``)."""


# ====================================================================== dobles con fallos
type Fault = BaseException | Callable[[], None]


@dataclass
class Faults:
    """Puntos de fallo: nombre → excepciones a lanzar o acciones a hacer, en orden."""

    plan: dict[str, list[Fault]] = field(default_factory=dict)
    reads: int = 0

    def hit(self, point: str) -> None:
        pending = self.plan.get(point)
        if pending:
            fault = pending.pop(0)
            if isinstance(fault, BaseException):
                raise fault
            fault()


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

    def drop_test_target(self, target: PreparedTarget) -> None:
        self.inner.drop_test_target(target)


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


class FaultyStore(EngineStore):
    def __init__(self, db: Database, faults: Faults) -> None:
        super().__init__(db)
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
def engine_app(
    app_settings: Settings,
    platform_db: Database,
    tmp_path_factory: pytest.TempPathFactory,
    csv_root: Path,
) -> FastAPI:
    settings = app_settings.model_copy(
        update={
            "csv_roots": [csv_root],
            "spool_dir": Path(tmp_path_factory.mktemp("spool")),
            "worker_heartbeat_seconds": 0.05,
            "run_stale_seconds": 0.2,
            "watermark_safety_lag_seconds": 0,
        }
    )
    return create_app(settings)


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
    pool: str = field(default_factory=lambda: f"t{uuid.uuid4().hex[:10]}")
    faults: Faults = field(default_factory=Faults)

    @property
    def settings(self) -> Settings:
        settings: Settings = self.app.state.settings
        return settings

    def extraction(self) -> ExtractionToolkit:
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

    def worker(self) -> WorkerRuntime:
        """Un worker nuevo (otro proceso: otro identificador y su propio spool)."""
        engine = ExtractionEngine(
            self.db, self.extraction(), store=FaultyStore(self.db, self.faults)
        )
        orchestration: OrchestrationToolkit = self.app.state.orchestration
        runtime = WorkerRuntime(
            self.db,
            replace(orchestration, random=lambda: 0.0),  # sin jitter en los reintentos
            executor=engine,
            pools=[self.pool],
        )
        runtime.register()
        return runtime

    def take(self, runtime: WorkerRuntime) -> ClaimedRun:
        """Toma un run sin ejecutarlo (un worker que se queda colgado)."""
        with self.db.session() as session:
            claimed = claim(session, worker_id=runtime.worker_id, pools=[self.pool], now=utcnow())
            session.commit()
        assert claimed is not None
        return claimed

    def mock(self, path: str, body: dict[str, Any] | None = None) -> None:
        self.gateway.control(path, body)


@pytest.fixture
def env(
    client: ApiClient,
    gateway: RunningGateway,
    engine_app: FastAPI,
    platform_db: Database,
    csv_root: Path,
) -> Env:
    return Env(client, gateway, engine_app, platform_db, csv_root)


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
    source_id = str(response.json()["id"])
    activated = env.client.post(f"/sources/{source_id}/activate")
    assert activated.status_code == 200, activated.text
    return source_id


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
    return str(response.json()["destination"]["id"])


def _pipeline(
    env: Env, source_id: str, destination_id: str, *, activate: bool = True, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": unique("PPL_").upper(),
        "name": "GL hacia CSV",
        "source_id": source_id,
        "destination_id": destination_id,
        "extraction_mode": "DELTA",
        "load": {"load_mode": "append", "target_name": "GL_ITEMS", "delete_handling": "soft"},
        "worker_pool": env.pool,
        "retry_policy": {"max_attempts": 3, "backoff_minutes": [0], "jitter": False},
    }
    body.update(extra)
    response = env.client.post("/pipelines", body)
    assert response.status_code == 201, response.text
    pipeline: dict[str, Any] = response.json()
    if activate:
        assert _try_out(env, pipeline["id"])["ok"] is True
        activated = env.client.post(f"/pipelines/{pipeline['id']}/activate", {})
        assert activated.status_code == 200, activated.text
        pipeline = activated.json()
    pipeline["_target"] = body["load"]["target_name"]
    return pipeline


def _try_out(env: Env, pipeline_id: str) -> dict[str, Any]:
    """«Probar pipeline» (plan 08 §6)."""
    response = env.client.post(f"/pipelines/{pipeline_id}/test-run")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _tested_event(env: Env, pipeline_id: str) -> dict[str, Any]:
    events = audit_actions(env.client, resource_id=pipeline_id)
    event: dict[str, Any] = next(e for e in events if e["action"] == "pipeline.tested")
    return event


def _steps(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(s["step"], s["status"]) for s in result["steps"]]


def _leftovers(env: Env, pipeline: dict[str, Any]) -> list[str]:
    """Carpetas de prueba que quedaron en el destino."""
    return sorted(p.name for p in _target(env, pipeline).parent.glob("SXTEST_*"))


def _gl_pipeline(env: Env, **extra: Any) -> dict[str, Any]:
    system = _system(env)
    source = _source(
        env, system, GL_SERVICE, GL_SET, delta={"method": "ODQ", "semantics": "after_image"}
    )
    return _pipeline(env, source, _csv(env), **extra)


def _gl_draft(env: Env, **extra: Any) -> dict[str, Any]:
    return _gl_pipeline(env, activate=False, **extra)


def _run_now(env: Env, pipeline_id: str) -> dict[str, Any]:
    response = env.client.post(f"/pipelines/{pipeline_id}/run", {})
    assert response.status_code == 201, response.text
    run: dict[str, Any] = response.json()
    return run


def _run(env: Env, run_id: str) -> dict[str, Any]:
    response = env.client.get(f"/runs/{run_id}")
    assert response.status_code == 200, response.text
    run: dict[str, Any] = response.json()
    return run


def _outcomes(env: Env, run_id: str) -> list[str | None]:
    return [a["outcome"] for a in _run(env, run_id)["attempts"]]


def _delta(env: Env, pipeline_id: str) -> dict[str, Any]:
    response = env.client.get(f"/pipelines/{pipeline_id}/delta")
    assert response.status_code == 200, response.text
    state: dict[str, Any] = response.json()
    return state


def _drain(runtime: WorkerRuntime, limit: int = 10) -> int:
    done = 0
    while done < limit and runtime.poll_once():
        done += 1
    return done


def _target(env: Env, pipeline: dict[str, Any]) -> Path:
    destination = env.client.get(f"/destinations/{pipeline['destination']['id']}").json()
    return env.csv_root / str(destination["config"]["path"]) / str(pipeline["_target"])


def _applied(env: Env, pipeline: dict[str, Any]) -> set[str]:
    return {p.stem for p in (_target(env, pipeline) / "_sx_load_state").glob("*.json")}


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
    assert run["mode"] == "DELTA"  # la cola encola DELTA; el motor lo convierte en init
    assert _drain(env.worker()) == 1
    done = _run(env, run["id"])
    assert done["status"] == "succeeded", done
    assert done["mode"] == "DELTA_INIT"
    return done


def _reap(env: Env) -> int:
    hooks: DeltaHooks = env.app.state.orchestration.hooks
    now = utcnow()
    with env.db.session() as session:
        reaped = reap_stale(
            session,
            now=now,
            cutoff=now + timedelta(seconds=5),
            random=0.0,
            on_final=hooks.run_finished,
        )
        session.commit()
    return reaped


# ====================================================================== flujo principal
def test_init_then_delta_and_repeat_to_csv(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    assert init["rows_extracted"] == ROWS
    assert init["batch_id"].startswith("i-")
    steps = env.client.get(f"/runs/{init['id']}/steps").json()
    assert [s["step"] for s in steps] == ["prepare", "extract", "load", "commit_delta", "cleanup"]
    state = _delta(env, pipeline["id"])
    assert state["status"] == "INITIALIZED"
    assert state["generation"] == 1
    assert state["current_token"] == init["next_token"] == _mock_tokens(env)[-1]
    assert [h["event"] for h in state["history"]] == ["initialized", "init_started"]
    assert _batch_rows(env, pipeline, init["batch_id"]) == ROWS

    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 5, "updates": 3, "deletes": 2})
    delta = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == 1
    done = _run(env, delta["id"])
    assert done["mode"] == "DELTA"
    assert done["rows_extracted"] == 10
    assert done["start_token"] == init["next_token"]
    assert done["next_token"] == _delta(env, pipeline["id"])["current_token"]
    assert done["next_token"] != init["next_token"]
    assert done["batch_id"].startswith("d-")
    assert _batch_rows(env, pipeline, done["batch_id"]) == 10

    # Un delta sin cambios también confirma token (0 filas).
    empty = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == 1
    assert _run(env, empty["id"])["rows_extracted"] == 0

    logs = env.client.get(f"/runs/{delta['id']}/logs").json()
    messages = [line["message"] for line in logs["items"]]
    assert logs["finished"] is True
    assert any("Token de delta confirmado" in m for m in messages)
    assert all(done["next_token"] not in m for m in messages)  # los tokens no van al log
    after = env.client.get(f"/runs/{delta['id']}/logs", params={"after": logs["next_after"]})
    assert after.json()["items"] == []


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

    env.faults.plan[point] = [Crash()]
    with pytest.raises(Crash):
        env.worker().poll_once()
    assert _run(env, run["id"])["status"] == "running"  # nadie sabe aún que murió
    assert _reap(env) == 1
    assert _run(env, run["id"])["status"] == "queued"

    reads = env.faults.reads
    assert _drain(env.worker()) == 1  # otro worker (otro proceso, otro spool)
    assert (env.faults.reads > reads) is sap_again

    done = _run(env, run["id"])
    assert done["status"] == "succeeded"
    assert _outcomes(env, run["id"]) == ["stale", "succeeded"]
    assert done["start_token"] == init["next_token"]
    state = _delta(env, pipeline["id"])
    assert state["current_token"] == done["next_token"] == _mock_tokens(env)[-1]
    assert state["pending_run_id"] is None
    deltas = [b for b in _applied(env, pipeline) if b.startswith("d-")]
    assert deltas == [done["batch_id"]]  # un único lote, con las 7 filas una sola vez
    assert _batch_rows(env, pipeline, done["batch_id"]) == 7


def test_load_failure_retries_on_same_worker_without_calling_sap(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 3})
    run = _run_now(env, pipeline["id"])
    env.faults.plan["stage"] = [
        DataflowError("Bloqueo en el destino", kind=ErrorKind.TRANSIENT, code="SX-DST-LOCK")
    ]
    reads = env.faults.reads
    assert _drain(env.worker()) == 2  # el mismo worker hace los dos intentos
    assert env.faults.reads == reads + 1  # el segundo intento no habló con SAP

    done = _run(env, run["id"])
    assert done["status"] == "succeeded"
    assert _outcomes(env, run["id"]) == ["failed_transient", "succeeded"]
    assert done["attempts"][1]["has_spool"] is True
    logs = [line["message"] for line in env.client.get(f"/runs/{run['id']}/logs").json()["items"]]
    assert any("Se reutiliza el spool" in m for m in logs)
    assert _batch_rows(env, pipeline, done["batch_id"]) == 3


def test_retries_are_exhausted_and_the_manual_retry_repeats_the_batch(env: Env) -> None:
    pipeline = _gl_pipeline(
        env, retry_policy={"max_attempts": 2, "backoff_minutes": [0], "jitter": False}
    )
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 2})
    run = _run_now(env, pipeline["id"])
    error = DataflowError("Destino caído", kind=ErrorKind.TRANSIENT, code="SX-DST-DOWN")
    env.faults.plan["before_apply"] = [error, error]
    assert _drain(env.worker()) == 2
    failed = _run(env, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SX-DST-DOWN"
    state = _delta(env, pipeline["id"])
    assert state["current_token"] == init["next_token"]
    assert state["pending_run_id"] is None  # el gancho soltó el estado
    assert state["status"] == "INITIALIZED"

    # El reintento manual de la orquestación repite el mismo lote con el mismo token.
    retried = env.client.post(f"/runs/{run['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert _drain(env.worker()) == 1
    repeated = _run(env, run["id"])
    assert repeated["status"] == "succeeded"
    assert repeated["start_token"] == init["next_token"]
    assert repeated["batch_id"] == failed["batch_id"]
    assert repeated["rows_extracted"] == 2


def test_zombie_worker_cannot_confirm(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 2})
    run = _run_now(env, pipeline["id"])

    zombie = env.worker()
    claimed = env.take(zombie)
    assert _reap(env) == 1  # sin latido: se da por caído y sube el epoch
    assert _drain(env.worker()) == 1
    confirmed = _delta(env, pipeline["id"])["current_token"]

    assert zombie.run(claimed, started=utcnow()) is AttemptOutcome.FENCED
    assert _delta(env, pipeline["id"])["current_token"] == confirmed
    assert _run(env, run["id"])["status"] == "succeeded"
    with env.db.session() as session:
        attempts = session.scalars(
            select(RunAttempt).where(RunAttempt.worker_id == zombie.worker_id)
        ).all()
        assert [a.outcome for a in attempts] == ["stale"]  # su cierre no pisa el anterior


# ====================================================================== token inválido y reinicio
def test_invalid_token_breaks_pipeline_and_reset_flow(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/invalidate-tokens")
    run = _run_now(env, pipeline["id"])
    assert _drain(env.worker()) == 1
    failed = _run(env, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SX-SAP-DELTA-INVALID"
    assert _outcomes(env, run["id"]) == ["failed"]  # un error de delta no se reintenta
    state = _delta(env, pipeline["id"])
    assert state["status"] == "BROKEN"
    assert state["current_token"] == init["next_token"]
    broken = audit_actions(env.client, action="pipeline.delta_broken")
    assert any(e["resource_id"] == pipeline["id"] for e in broken)

    blocked = env.client.post(f"/pipelines/{pipeline['id']}/run", {})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SX-PIPE-DELTA-BROKEN"

    no_reason = env.client.post(f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "x"})
    assert no_reason.status_code == 422
    reset = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "Suscripción borrada en ODQMON"}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["status"] == "NOT_INITIALIZED"
    assert reset.json()["sap_close_pending"] is True

    pending = env.client.post(f"/pipelines/{pipeline['id']}/run", {})
    assert pending.status_code == 409
    assert pending.json()["code"] == "SX-PIPE-SAP-CLOSE-PENDING"

    closed = env.client.post(
        f"/pipelines/{pipeline['id']}/delta/sap-subscription", {"action": "closed"}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["sap_close_pending"] is False

    _init(env, pipeline)
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
    assert _drain(env.worker()) == 1  # H10: la init no se reintenta sola
    failed = _run(env, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "SX-SAP-SERVER"
    assert failed["error_kind"] == "transient"
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
    _init(env, pipeline)


def test_init_failure_in_prepare_does_not_flag_subscription(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    run = _run_now(env, pipeline["id"])
    runtime = env.worker()
    engine = runtime.executor
    assert isinstance(engine, ExtractionEngine)
    engine.store = FaultyStore(env.db, env.faults)
    env.faults.plan["read"] = [
        DataflowError("Sin red", kind=ErrorKind.CONFIGURATION, code="SX-TEST-CONFIG")
    ]
    assert _drain(runtime) == 1
    assert _run(env, run["id"])["status"] == "failed"
    # Llegó a pedir la lectura: la init puede haber quedado abierta en SAP.
    assert _delta(env, pipeline["id"])["sap_close_pending"] is True

    other = _gl_pipeline(env)
    with env.db.session() as session:  # el destino deja de estar activo: falla en prepare
        from sapextract.destinations.infrastructure.models import Destination  # noqa: PLC0415

        destination = session.get(Destination, uuid.UUID(other["destination"]["id"]))
        assert destination is not None
        destination.status = "inactive"
        session.commit()
    run = _run_now(env, other["id"])
    assert _drain(env.worker()) == 1
    assert _run(env, run["id"])["error_code"] == "SX-DST-INACTIVE"
    assert _delta(env, other["id"])["sap_close_pending"] is False


# ====================================================================== cancelación
def test_cancel_queued_run_leaves_delta_untouched(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    run = _run_now(env, pipeline["id"])
    cancelled = env.client.post(f"/runs/{run['id']}/cancel", {})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert _drain(env.worker()) == 0
    state = _delta(env, pipeline["id"])
    assert state["status"] == "INITIALIZED"
    assert state["pending_run_id"] is None


def test_cancel_running_delta_keeps_token(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    init = _init(env, pipeline)
    env.mock(f"/_mock/services/{GL_SERVICE}/changes", {"inserts": 3})
    run = _run_now(env, pipeline["id"])

    def cancel_and_wait() -> None:  # durante la lectura: el latido trae la cancelación
        response = env.client.post(f"/runs/{run['id']}/cancel", {})
        assert response.status_code == 200
        time.sleep(0.3)

    env.faults.plan["read"] = [cancel_and_wait]
    assert _drain(env.worker()) == 1
    assert _run(env, run["id"])["status"] == "cancelled"
    assert _outcomes(env, run["id"]) == ["cancelled"]
    state = _delta(env, pipeline["id"])
    assert state["current_token"] == init["next_token"]
    assert state["pending_run_id"] is None
    assert _applied(env, pipeline) == {init["batch_id"]}


def test_stop_requested_before_extracting_does_not_call_sap(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    run = _run_now(env, pipeline["id"])
    runtime = env.worker()
    claimed = env.take(runtime)
    engine = runtime.executor
    assert isinstance(engine, ExtractionEngine)
    ctx = RunContext(
        run_id=claimed.run_id,
        pipeline_id=claimed.pipeline_id,
        mode=claimed.mode,
        attempt=claimed.attempt,
        lease_epoch=claimed.lease_epoch,
        worker_id=runtime.worker_id,
        stop_reason=lambda: StopReason.SHUTDOWN,
    )
    reads = env.faults.reads
    with pytest.raises(RunStoppedError):
        engine.execute(ctx)
    assert env.faults.reads == reads
    assert _run(env, run["id"])["status"] == "running"
    # Sin worker que cierre el intento: se recupera y se cancela para no dejar nada en curso.
    assert _reap(env) == 1
    assert env.client.post(f"/runs/{run['id']}/cancel", {}).status_code == 200


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
        load={"load_mode": "replace", "target_name": "MATERIAL_FULL"},
    )
    run = _run_now(env, full["id"])
    assert _drain(env.worker()) == 1
    done = _run(env, run["id"])
    assert done["status"] == "succeeded", done
    assert done["rows_extracted"] == ROWS
    assert done["batch_id"].startswith("f-")
    assert _delta(env, full["id"])["status"] == "NOT_INITIALIZED"  # sin estado de delta

    wm = _pipeline(
        env,
        material,
        csv,
        extraction_mode="WATERMARK",
        load={"load_mode": "append", "target_name": "MATERIAL_WM"},
    )
    first = _run_now(env, wm["id"])
    assert _drain(env.worker()) == 1
    first_done = _run(env, first["id"])
    assert first_done["status"] == "succeeded", first_done
    assert first_done["rows_extracted"] == ROWS
    assert first_done["batch_id"].startswith("w-")
    assert _delta(env, wm["id"])["watermark_value"]["end"]

    env.mock(f"/_mock/services/{MATERIAL_SERVICE}/changes", {"inserts": 2, "updates": 3})
    time.sleep(2.1)  # la ventana acaba en el segundo de la cabecera Date (uvicorn la cachea)
    second = _run_now(env, wm["id"])
    assert _drain(env.worker()) == 1
    second_done = _run(env, second["id"])
    assert second_done["rows_extracted"] == 5
    assert second_done["window_start"] is not None
    assert second_done["batch_id"] != first_done["batch_id"]


def test_deleting_an_initialized_pipeline_leaves_the_subscription_pending(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    _init(env, pipeline)
    deleted = env.client.delete(f"/pipelines/{pipeline['id']}", params={"reason": "Prueba"})
    assert deleted.status_code == 204, deleted.text
    with env.db.session() as session:
        state = session.get(DeltaState, uuid.UUID(pipeline["id"]))
        assert state is not None
        assert state.sap_close_pending is True
        assert state.status == "NOT_INITIALIZED"


def test_viewer_reads_delta_but_cannot_reset(env: Env) -> None:
    pipeline = _gl_pipeline(env)
    user = create_user(env.client, "viewer")
    viewer = ApiClient(env.app)
    activate_user(viewer, user["user"]["username"], user["temporary_password"])
    assert viewer.get(f"/pipelines/{pipeline['id']}/delta").status_code == 200
    assert viewer.get(f"/runs/{_run_now(env, pipeline['id'])['id']}/logs").status_code == 200
    reset = viewer.post(f"/pipelines/{pipeline['id']}/delta/reset", {"reason": "Sin permiso"})
    assert reset.status_code == 403
    assert viewer.post(f"/pipelines/{pipeline['id']}/test-run").status_code == 403


# ====================================================================== probar pipeline
def test_trial_loads_a_sample_without_touching_sap_state_or_the_target(env: Env) -> None:
    pipeline = _gl_draft(env)
    pid = pipeline["id"]

    result = _try_out(env, pid)
    assert result["ok"] is True
    assert _steps(result) == [
        ("configuration", "succeeded"),
        ("read", "succeeded"),
        ("load", "succeeded"),
        ("cleanup", "succeeded"),
    ]
    # La muestra es de 100 filas aunque el servicio tenga 250.
    assert (result["rows_read"], result["rows_loaded"]) == (100, 100)
    assert result["target"].startswith("SXTEST_")
    assert result["target"].endswith("_GL_ITEMS")

    # Ni suscripción en SAP, ni estado de delta, ni run, ni datos en el destino real.
    assert _mock_tokens(env) == []
    assert _delta(env, pid)["status"] == "NOT_INITIALIZED"
    assert env.client.get("/runs", params={"pipeline": pid}).json()["total"] == 0
    assert not _target(env, pipeline).exists()
    assert _leftovers(env, pipeline) == []

    detail = env.client.get(f"/pipelines/{pid}").json()
    assert detail["last_test_ok"] is True
    assert detail["test_outdated"] is False
    assert detail["version"] == pipeline["version"]  # probar no invalida una edición abierta
    event = _tested_event(env, pid)
    assert event["outcome"] == "success"
    assert event["context"]["rows_read"] == 100

    # Cambiar lo que la prueba comprueba la deja sin vigencia; el nombre no.
    renamed = env.client.patch(f"/pipelines/{pid}", {"name": "GL hacia CSV, revisado"})
    assert renamed.json()["test_outdated"] is False
    changed = env.client.patch(
        f"/pipelines/{pid}", {"load": {"load_mode": "append", "target_name": "GL_ITEMS_V2"}}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["test_outdated"] is True
    refused = env.client.post(f"/pipelines/{pid}/activate", {})
    assert refused.status_code == 422
    assert [e["field"] for e in refused.json()["errors"]] == ["test_run"]
    assert _try_out(env, pid)["ok"] is True
    assert env.client.post(f"/pipelines/{pid}/activate", {}).status_code == 200


def test_trial_reports_the_step_that_failed(env: Env) -> None:
    pipeline = _gl_draft(env)
    pid = pipeline["id"]

    # SAP caído: falla la lectura y no se llega al destino.
    env.gateway.scenario(status=503, times=5)
    down = _try_out(env, pid)
    env.mock("/_mock/reset")
    assert down["ok"] is False
    assert _steps(down) == [
        ("configuration", "succeeded"),
        ("read", "failed"),
        ("load", "skipped"),
        ("cleanup", "skipped"),
    ]
    failed = next(s for s in down["steps"] if s["step"] == "read")
    assert failed["error_code"].startswith("SX-SAP-")
    assert env.client.get(f"/pipelines/{pid}").json()["last_test_ok"] is False
    report = env.client.post(f"/pipelines/{pid}/validate", {}).json()
    assert {c["code"]: c["ok"] for c in report["checks"]}["test_run"] is False
    event = _tested_event(env, pid)
    assert (event["outcome"], event["context"]["failed_step"]) == ("failure", "read")
    assert _leftovers(env, pipeline) == []


def test_trial_drops_the_test_target_when_the_load_fails(env: Env) -> None:
    pipeline = _gl_draft(env)
    env.faults.plan["before_apply"] = [
        DataflowError("Disco lleno", kind=ErrorKind.TRANSIENT, code="SX-DST-IO")
    ]
    original = env.app.state.extraction
    env.app.state.extraction = env.extraction()  # destino con el fallo inyectado
    try:
        result = _try_out(env, pipeline["id"])
    finally:
        env.app.state.extraction = original
    assert result["ok"] is False
    assert _steps(result) == [
        ("configuration", "succeeded"),
        ("read", "succeeded"),
        ("load", "failed"),
        ("cleanup", "succeeded"),
    ]
    assert next(s for s in result["steps"] if s["step"] == "load")["error_code"] == "SX-DST-IO"
    assert _leftovers(env, pipeline) == []
    assert not _target(env, pipeline).exists()


def test_trial_is_not_required_when_the_source_forbids_sampling(env: Env) -> None:
    system = _system(env)
    source = _source(
        env,
        system,
        GL_SERVICE,
        GL_SET,
        delta={"method": "ODQ", "semantics": "after_image"},
        preview_enabled=False,
    )
    pipeline = _pipeline(env, source, _csv(env), activate=False)
    refused = env.client.post(f"/pipelines/{pipeline['id']}/test-run")
    assert refused.status_code == 422
    assert refused.json()["code"] == "SX-PIPE-TEST-DISABLED"
    report = env.client.post(f"/pipelines/{pipeline['id']}/validate", {}).json()
    assert report["ok"] is True
    assert {c["code"]: c["ok"] for c in report["checks"]}["test_run"] is True
    assert _mock_tokens(env) == []
