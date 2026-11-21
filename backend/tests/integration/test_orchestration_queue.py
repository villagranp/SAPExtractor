"""Cola, scheduler y workers contra PostgreSQL real (plan 08 §2 a §4 y sus criterios de aceptación).

El motor de extracción (It5) no existe todavía: un ``ScriptedExecutor`` ocupa su lugar. Lo que
se prueba aquí es lo que la orquestación garantiza sea cual sea el ejecutor: quién toma qué,
cuántos a la vez, qué pasa cuando falla, se cancela, se cuelga o se apaga el worker.

Cada prueba usa su propio *pool* de workers, así no ve los runs de las demás.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update

import sapextract.main_scheduler as scheduler_main
import sapextract.main_worker as worker_main
import sapextract.orchestration.application.worker as worker_module
from sapextract.core.config import Settings
from sapextract.core.maintenance import SystemTask
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.extraction.application.engine import ExtractionEngine
from sapextract.orchestration.application.lifecycle import Verdict, VerdictKind, settle
from sapextract.orchestration.application.scheduler import SchedulerService
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.application.worker import WorkerRuntime
from sapextract.orchestration.domain.model import AttemptOutcome
from sapextract.orchestration.domain.ports import RunContext, RunStoppedError, StopReason
from sapextract.orchestration.infrastructure.models import Run, Worker
from sapextract.orchestration.infrastructure.queue import QueueListener, claim, heartbeat
from tests.integration.conftest import ApiClient
from tests.support.orchestration import OrchEnv, ScriptedExecutor, unique

pytestmark = pytest.mark.integration


def _pool() -> str:
    return unique("p").lower()


def _transient(ctx: RunContext) -> None:
    raise DataflowError("Gateway no disponible (503)", kind=ErrorKind.TRANSIENT, code="SX-SAP-503")


def _config_error(ctx: RunContext) -> None:
    raise DataflowError(
        "Tabla destino sin permisos", kind=ErrorKind.CONFIGURATION, code="SX-DST-FORBIDDEN"
    )


def _outcomes(orch: OrchEnv, run_id: str) -> list[str | None]:
    return [a.outcome for a in orch.attempts(run_id)]


def _schedule(api: ApiClient, pipeline_id: str, **schedule: Any) -> dict[str, Any]:
    response = api.put(f"/pipelines/{pipeline_id}/schedules", {"schedules": [schedule]})
    assert response.status_code == 200, response.text
    saved: dict[str, Any] = response.json()[0]
    return saved


# ====================================================================== scheduler
def test_schedule_fires_once_and_skips_while_a_run_is_live(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pipeline = orch.pipeline(orch_admin, worker_pool=_pool())
    pid = pipeline["id"]
    _schedule(orch_admin, pid, type="interval", interval_minutes=30)
    scheduler = orch.scheduler()

    scheduler.fire_due()
    assert orch.runs_of(pid) == []  # todavía no toca

    orch.clock.advance(minutes=31)
    scheduler.fire_due()
    scheduler.fire_due()  # un segundo ciclo (u otro líder) no duplica la ejecución
    runs = orch.runs_of(pid)
    assert [(r.status, r.trigger, r.triggered_by, r.mode) for r in runs] == [
        ("queued", "schedule", "scheduler", "FULL")
    ]
    assert runs[0].priority == pipeline["priority"]
    schedule = orch.schedules_of(pid)[0]
    assert schedule.last_fire_at == orch.clock.now
    assert schedule.next_fire_at is not None
    assert schedule.next_fire_at > orch.clock.now
    assert runs[0].schedule_id == schedule.id

    # El run anterior sigue en la cola: el siguiente disparo se omite y queda anotado.
    orch.clock.advance(minutes=30)
    scheduler.fire_due()
    assert len(orch.runs_of(pid)) == 1
    schedule = orch.schedules_of(pid)[0]
    assert schedule.last_skip_reason == "already_live"
    assert schedule.last_skip_at == orch.clock.now
    shown = orch_admin.get(f"/pipelines/{pid}/schedules").json()[0]
    assert shown["last_skip_reason"] == "already_live"


def test_misfire_policy_after_the_scheduler_was_down(orch: OrchEnv, orch_admin: ApiClient) -> None:
    skipping = orch.pipeline(orch_admin, worker_pool=_pool())
    recovering = orch.pipeline(orch_admin, worker_pool=_pool())
    _schedule(orch_admin, skipping["id"], type="interval", interval_minutes=10)
    _schedule(
        orch_admin,
        recovering["id"],
        type="interval",
        interval_minutes=10,
        misfire_policy="run_once",
    )

    orch.clock.advance(hours=3)  # el scheduler estuvo caído: 18 disparos perdidos
    orch.scheduler().fire_due()

    assert orch.runs_of(skipping["id"]) == []
    assert orch.schedules_of(skipping["id"])[0].last_skip_reason == "misfire"
    # «run_once» lanza una sola ejecución de recuperación, no una por disparo perdido.
    assert len(orch.runs_of(recovering["id"])) == 1
    for pid in (skipping["id"], recovering["id"]):
        next_fire = orch.schedules_of(pid)[0].next_fire_at
        assert next_fire is not None
        assert timedelta(0) < next_fire - orch.clock.now <= timedelta(minutes=10)


def test_blackout_window_and_pause_stop_the_schedule(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    blocked = orch.pipeline(api, worker_pool=_pool())
    paused = orch.pipeline(api, worker_pool=_pool())
    window = {
        "kind": "range",
        "label": "Cierre contable",
        "start_at": (orch.clock.now + timedelta(minutes=5)).isoformat(),
        "end_at": (orch.clock.now + timedelta(hours=2)).isoformat(),
    }
    _schedule(api, blocked["id"], type="interval", interval_minutes=20, blackout_windows=[window])
    _schedule(api, paused["id"], type="interval", interval_minutes=20)
    assert api.post(f"/pipelines/{paused['id']}/pause", {}).status_code == 200
    assert orch.schedules_of(paused["id"])[0].next_fire_at is None

    orch.clock.advance(minutes=21)
    orch.scheduler().fire_due()
    assert orch.runs_of(blocked["id"]) == []
    assert orch.schedules_of(blocked["id"])[0].last_skip_reason == "blackout"
    assert orch.runs_of(paused["id"]) == []

    # Al salir de la ventana vuelve a lanzar; al reanudar, el disparo se recalcula desde ahora.
    orch.clock.advance(hours=2)
    assert api.post(f"/pipelines/{paused['id']}/resume").status_code == 200
    resumed_fire = orch.schedules_of(paused["id"])[0].next_fire_at
    assert resumed_fire is not None
    assert resumed_fire > orch.clock.now
    orch.scheduler().fire_due()
    assert orch.runs_of(paused["id"]) == []  # sin ejecución de golpe por la pausa
    orch.clock.now = resumed_fire + timedelta(seconds=5)
    orch.scheduler().fire_due()
    assert len(orch.runs_of(blocked["id"])) == 1
    assert len(orch.runs_of(paused["id"])) == 1


def test_leader_failover_does_not_duplicate_runs(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pipeline = orch.pipeline(orch_admin, worker_pool=_pool())
    _schedule(orch_admin, pipeline["id"], type="interval", interval_minutes=5)
    lock = scheduler_main.SCHEDULER_LEADER_LOCK + 999  # otro número: no choca con otras pruebas
    engine = orch.db.engine
    leader = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    standby = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        acquire = text("SELECT pg_try_advisory_lock(:k)")
        assert leader.execute(acquire, {"k": lock}).scalar() is True
        assert standby.execute(acquire, {"k": lock}).scalar() is False

        orch.clock.advance(minutes=6)
        orch.scheduler().fire_due()  # el líder dispara…
        leader.invalidate()  # …y muere: la BD libera el bloqueo al cerrarse su conexión
        leader.close()
        deadline = time.monotonic() + 30
        while not standby.execute(acquire, {"k": lock}).scalar():
            assert time.monotonic() < deadline, "El scheduler en espera no tomó el relevo"
            time.sleep(0.1)
        orch.scheduler().fire_due()  # el nuevo líder repite el ciclo sin duplicar
        assert len(orch.runs_of(pipeline["id"])) == 1
    finally:
        standby.close()


# ====================================================================== worker: resultados
def test_worker_runs_a_queued_run_to_success(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])
    executor = ScriptedExecutor()
    worker = orch.worker(executor, pool)

    assert worker.poll_once() is True
    assert worker.poll_once() is False  # cola vacía
    context = executor.calls[0]
    assert (str(context.run_id), context.attempt, context.lease_epoch) == (run["id"], 1, 1)
    assert context.worker_id == worker.worker_id
    assert context.mode.value == "FULL"

    detail = orch_admin.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "succeeded"
    assert detail["started_at"] is not None
    assert detail["finished_at"] is not None
    assert [(a["number"], a["outcome"], a["worker_id"]) for a in detail["attempts"]] == [
        (1, "succeeded", str(worker.worker_id))
    ]
    listed = {w["id"]: w for w in orch_admin.get("/workers").json()}
    mine = listed[str(worker.worker_id)]
    assert (mine["status"], mine["has_executor"], mine["current_run_id"]) == ("online", True, None)
    assert mine["pools"] == [pool]
    assert orch_admin.get(f"/pipelines/{pipeline['id']}").json()["last_run"]["status"] == (
        "succeeded"
    )


def test_transient_errors_retry_with_backoff_then_fail(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)  # 3 intentos, esperas de 1, 5 y 15 min
    run = orch.run_now(orch_admin, pipeline["id"])
    worker = orch.worker(ScriptedExecutor(_transient, _transient, _transient), pool)

    assert worker.poll_once() is True
    first = orch.run(run["id"])
    assert first.status == "queued"
    assert first.available_at == orch.clock.now + timedelta(minutes=1)
    assert first.error_code is None  # el error vive en el intento mientras quedan reintentos
    assert worker.poll_once() is False  # aún no pasó la espera

    orch.clock.advance(minutes=1)
    assert worker.poll_once() is True
    assert orch.run(run["id"]).available_at == orch.clock.now + timedelta(minutes=5)
    orch.clock.advance(minutes=5)
    assert worker.poll_once() is True

    failed = orch_admin.get(f"/runs/{run['id']}").json()
    assert failed["status"] == "failed"
    assert (failed["error_code"], failed["error_kind"]) == ("SX-SAP-503", "transient")
    assert [a["outcome"] for a in failed["attempts"]] == [
        "failed_transient",
        "failed_transient",
        "failed",
    ]
    assert failed["attempts"][0]["error_message"] == "Gateway no disponible (503)"

    # Reintento manual: un intento más sobre el mismo run, sin crear otro.
    retried = orch_admin.post(f"/runs/{run['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert retried.json()["error_code"] is None
    assert worker.poll_once() is True  # el guion se agotó: esta vez termina bien
    final = orch_admin.get(f"/runs/{run['id']}").json()
    assert final["status"] == "succeeded"
    assert [a["outcome"] for a in final["attempts"]][-1] == "succeeded"
    assert len(orch.runs_of(pipeline["id"])) == 1


def test_non_transient_errors_fail_at_once(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)

    def boom(ctx: RunContext) -> None:
        msg = "clave=Secreta.2026 en el mensaje"
        raise ValueError(msg)

    worker = orch.worker(ScriptedExecutor(_config_error, boom), pool)
    configuration = orch.run_now(orch_admin, pipeline["id"])
    assert worker.poll_once() is True
    detail = orch_admin.get(f"/runs/{configuration['id']}").json()
    assert (detail["status"], detail["error_code"], detail["error_kind"]) == (
        "failed",
        "SX-DST-FORBIDDEN",
        "configuration",
    )
    assert [a["outcome"] for a in detail["attempts"]] == ["failed"]

    # Solo se reintenta la última ejecución del pipeline.
    unexpected = orch.run_now(orch_admin, pipeline["id"])
    assert worker.poll_once() is True
    detail = orch_admin.get(f"/runs/{unexpected['id']}").json()
    assert (detail["status"], detail["error_code"], detail["error_kind"]) == (
        "failed",
        "SX-RUN-UNEXPECTED",
        None,
    )
    # El texto de una excepción inesperada no se guarda: podría llevar datos.
    assert "Secreta" not in detail["error_message"]
    assert "ValueError" in detail["error_message"]
    stale_retry = orch_admin.post(f"/runs/{configuration['id']}/retry")
    assert stale_retry.status_code == 409
    assert stale_retry.json()["code"] == "SX-RUN-NOT-LATEST"


class _LogRecorder:
    """Sustituye al logger del worker: guarda cada evento con sus campos."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        def record(event: str, **fields: Any) -> None:
            self.events.append((level, event, fields))

        return record


def test_unexpected_error_text_never_reaches_the_log(
    orch: OrchEnv, orch_admin: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    recorder = _LogRecorder()
    monkeypatch.setattr(worker_module, "log", recorder)

    def boom(ctx: RunContext) -> None:
        try:
            msg = "fila con IBAN ES7620770024003102575766"
            raise KeyError(msg)
        except KeyError as exc:
            msg = "clave=Secreta.2026 en el mensaje"
            raise ValueError(msg) from exc

    orch.run_now(orch_admin, pipeline["id"])
    assert orch.worker(ScriptedExecutor(boom), pool).poll_once() is True

    level, _, fields = next(e for e in recorder.events if e[1] == "run_unexpected_error")
    # Sin ``exc_info``: el formateador escribiría el mensaje de la excepción.
    assert level == "error"
    assert "exc_info" not in fields
    assert fields["error_type"] == "ValueError"
    assert [line for line in fields["trace"] if ":" not in line] == ["ValueError", "KeyError"]
    assert any(line.endswith(" boom") for line in fields["trace"])
    assert "Secreta" not in repr(recorder.events)
    assert "IBAN" not in repr(recorder.events)


def test_paused_pipeline_holds_its_runs(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pid = orch.pipeline(orch_admin, worker_pool=pool)["id"]
    worker = orch.worker(ScriptedExecutor(_config_error), pool)
    failed = orch.run_now(orch_admin, pid)
    assert worker.poll_once() is True

    # Reintentar no se salta la pausa, igual que el run manual.
    assert orch_admin.post(f"/pipelines/{pid}/pause", {}).status_code == 200
    retry = orch_admin.post(f"/runs/{failed['id']}/retry")
    assert retry.status_code == 409
    assert retry.json()["code"] == "SX-PIPE-NOT-ACTIVE"
    assert orch.run(failed["id"]).status == "failed"

    # Un run que ya estaba en cola espera a que el pipeline se reanude.
    assert orch_admin.post(f"/pipelines/{pid}/resume").status_code == 200
    queued = orch.run_now(orch_admin, pid)
    assert orch_admin.post(f"/pipelines/{pid}/pause", {}).status_code == 200
    assert worker.poll_once() is False
    assert orch.run(queued["id"]).status == "queued"
    assert orch_admin.post(f"/pipelines/{pid}/resume").status_code == 200
    assert worker.poll_once() is True
    assert orch.run(queued["id"]).status == "succeeded"


# ====================================================================== worker: paradas
def test_cancelling_a_running_run_is_cooperative(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])

    def until_cancelled(ctx: RunContext) -> None:
        response = orch_admin.post(f"/runs/{run['id']}/cancel", {"reason": "Ventana de backup"})
        assert response.json()["status"] == "running"  # se pide; el worker para entre páginas
        assert response.json()["cancel_requested_at"] is not None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ctx.checkpoint()
            time.sleep(0.01)
        pytest.fail("La cancelación no llegó al ejecutor")

    worker = orch.worker(ScriptedExecutor(until_cancelled), pool)
    assert worker.poll_once() is True
    detail = orch_admin.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "cancelled"
    assert detail["cancel_requested_by"] == "root"
    assert [a["outcome"] for a in detail["attempts"]] == ["cancelled"]
    assert worker.poll_once() is False  # un run cancelado no vuelve a la cola


def test_shutdown_returns_the_run_without_spending_an_attempt(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])
    stopping = orch.worker(None, pool, register=False)

    def sigterm(ctx: RunContext) -> None:
        stopping.shutdown.set()  # llega SIGTERM a mitad del run
        ctx.checkpoint()

    stopping.executor = ScriptedExecutor(sigterm)
    stopping.register()
    assert stopping.poll_once() is True
    interrupted = orch.run(run["id"])
    assert interrupted.status == "queued"
    assert interrupted.available_at == orch.clock.now  # sin espera: no fue un fallo
    assert _outcomes(orch, run["id"]) == ["interrupted"]
    stopping.unregister()

    # Otro worker lo retoma y aún dispone de todos los intentos de la política.
    other = orch.worker(ScriptedExecutor(_transient, _transient), pool)
    assert other.poll_once() is True
    orch.clock.advance(minutes=1)
    assert other.poll_once() is True
    assert orch.run(run["id"]).status == "queued"  # dos fallos gastados de tres
    assert _outcomes(orch, run["id"]) == ["interrupted", "failed_transient", "failed_transient"]
    assert orch.run(run["id"]).lease_epoch == 3


def test_run_timeout_counts_as_a_transient_failure(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool, timeout_minutes=10)
    run = orch.run_now(orch_admin, pipeline["id"])

    def too_slow(ctx: RunContext) -> None:
        ctx.checkpoint()
        orch.clock.advance(minutes=11)
        ctx.checkpoint()

    assert orch.worker(ScriptedExecutor(too_slow), pool).poll_once() is True
    attempt = orch.attempts(run["id"])[0]
    assert (attempt.outcome, attempt.error_code) == ("failed_transient", "SX-RUN-TIMEOUT")
    assert orch.run(run["id"]).status == "queued"


# ====================================================================== latido y fencing
def test_stale_run_is_recovered_and_the_zombie_is_fenced(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])
    zombie = orch.worker(ScriptedExecutor(), pool)
    with orch.db.session() as session:
        lease = claim(session, worker_id=zombie.worker_id, pools=[pool], now=orch.clock.now)
        session.commit()
    assert lease is not None
    with orch.db.session() as session:
        assert heartbeat(session, lease, now=orch.clock.now).alive is True
        session.commit()

    scheduler = orch.scheduler()
    orch.clock.advance(seconds=30)
    assert scheduler.reap() == 0  # aún dentro del margen
    orch.clock.advance(seconds=120)  # el worker se colgó: sin latido
    scheduler.reap()
    recovered = orch.run(run["id"])
    assert recovered.status == "queued"
    assert recovered.lease_epoch == lease.lease_epoch + 1
    assert recovered.available_at == orch.clock.now + timedelta(minutes=1)
    stale = orch.attempts(run["id"])[0]
    assert (stale.outcome, stale.error_code) == ("stale", "SX-RUN-STALE")

    # Otro worker lo toma y lo termina.
    orch.clock.advance(minutes=1)
    assert orch.worker(ScriptedExecutor(), pool).poll_once() is True
    assert orch.run(run["id"]).status == "succeeded"

    # El worker dado por caído vuelve: ni su latido ni su confirmación tienen efecto.
    with orch.db.session() as session:
        assert heartbeat(session, lease, now=orch.clock.now).alive is False
        outcome = settle(
            session,
            lease,
            Verdict(kind=VerdictKind.FAILED, error_code="SX-ZOMBIE"),
            now=orch.clock.now,
            random=0.0,
        )
        session.commit()
    assert outcome is AttemptOutcome.FENCED
    final = orch.run(run["id"])
    assert (final.status, final.error_code) == ("succeeded", None)
    assert _outcomes(orch, run["id"]) == ["stale", "succeeded"]


def test_a_run_that_keeps_killing_workers_ends_as_failed(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(
        orch_admin, worker_pool=pool, retry_policy={"max_attempts": 2, "backoff_minutes": [0]}
    )
    run = orch.run_now(orch_admin, pipeline["id"])
    worker = orch.worker(ScriptedExecutor(), pool)
    for _ in range(2):
        with orch.db.session() as session:
            assert claim(session, worker_id=worker.worker_id, pools=[pool], now=orch.clock.now)
            session.commit()
        orch.clock.advance(seconds=200)
        orch.scheduler().reap()
    final = orch.run(run["id"])
    assert (final.status, final.error_code) == ("failed", "SX-RUN-STALE")
    assert _outcomes(orch, run["id"]) == ["stale", "stale"]


def test_fenced_executor_stops_and_cannot_settle(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])

    def lose_the_lease(ctx: RunContext) -> None:
        # Mientras trabaja, el scheduler lo da por caído y otro worker toma el run.
        with orch.db.session() as session:
            session.execute(
                update(Run).where(Run.id == ctx.run_id).values(lease_epoch=Run.lease_epoch + 1)
            )
            session.commit()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ctx.checkpoint()
            time.sleep(0.01)
        pytest.fail("El worker no notó que perdió el arrendamiento")

    executor = ScriptedExecutor(lose_the_lease)
    worker = orch.worker(executor, pool)
    assert worker.poll_once() is True
    assert _outcomes(orch, run["id"]) == ["fenced"]
    assert orch.run(run["id"]).status == "running"  # el run es del nuevo dueño, no se toca

    # El ejecutor también puede notarlo él mismo, al fallar una escritura con su arrendamiento.
    def notices(ctx: RunContext) -> None:
        raise RunStoppedError(StopReason.FENCED)

    other = orch.pipeline(orch_admin, worker_pool=pool)
    second = orch.run_now(orch_admin, other["id"])
    assert orch.worker(ScriptedExecutor(notices), pool).poll_once() is True
    assert _outcomes(orch, second["id"]) == ["fenced"]


def test_run_confirmed_by_the_engine_counts_as_success(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])

    def commit_delta(ctx: RunContext) -> None:
        # El motor marca el run como exitoso en la transacción que avanza el token (plan 06).
        with orch.db.session() as session:
            session.execute(
                update(Run)
                .where(Run.id == ctx.run_id, Run.lease_epoch == ctx.lease_epoch)
                .values(status="succeeded", finished_at=orch.clock.now)
            )
            session.commit()

    assert orch.worker(ScriptedExecutor(commit_delta), pool).poll_once() is True
    assert orch.run(run["id"]).status == "succeeded"
    assert _outcomes(orch, run["id"]) == ["succeeded"]


# ====================================================================== cola: límites
def test_three_workers_respect_pipeline_system_and_group_limits(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    """Criterio de aceptación: 3 workers y 20 pipelines, sin superar ningún límite."""
    api = orch_admin
    pool = _pool()
    group = unique("GRP_")
    assert api.put(f"/concurrency-groups/{group}", {"max_parallel": 1}).status_code == 200
    limited = orch.system(api, max_concurrent_runs=2)
    roomy = orch.system(api, max_concurrent_runs=3)
    destination = orch.destination(api)
    base_limited = orch.pipeline(
        api, source=orch.source(api, limited["id"]), destination=destination, worker_pool=pool
    )
    base_roomy = orch.pipeline(
        api, source=orch.source(api, roomy["id"]), destination=destination, worker_pool=pool
    )
    on_limited = [uuid.UUID(base_limited["id"]), *orch.copies(base_limited["id"], 11)]
    grouped = orch.copies(base_roomy["id"], 4, concurrency_group=group)
    on_roomy = [uuid.UUID(base_roomy["id"]), *orch.copies(base_roomy["id"], 3), *grouped]
    pipelines = [*on_limited, *on_roomy]
    system_of = dict.fromkeys(on_limited, "limited") | dict.fromkeys(on_roomy, "roomy")
    for pid in pipelines:
        orch.run_now(api, str(pid))

    lock = threading.Lock()
    running: Counter[str] = Counter()
    peak: Counter[str] = Counter()
    executed: list[uuid.UUID] = []

    def work(ctx: RunContext) -> None:
        keys = [system_of[ctx.pipeline_id], f"pipeline:{ctx.pipeline_id}"]
        if ctx.pipeline_id in grouped:
            keys.append("group")
        with lock:
            executed.append(ctx.run_id)
            for key in keys:
                running[key] += 1
                peak[key] = max(peak[key], running[key])
        time.sleep(0.05)
        with lock:
            for key in keys:
                running[key] -= 1

    class Busy(ScriptedExecutor):
        def execute(self, ctx: RunContext) -> None:
            work(ctx)

    def drain(worker: WorkerRuntime) -> None:
        idle = 0
        while idle < 40:  # deja de sondear cuando la cola lleva un rato vacía
            if worker.poll_once():
                idle = 0
            else:
                idle += 1
                time.sleep(0.01)

    threads = [threading.Thread(target=drain, args=(orch.worker(Busy(), pool),)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert (len(executed), len(set(executed))) == (20, 20)  # ningún run lo tomaron dos workers
    # Usa todo el hueco del sistema limitado, sin pasarse; el grupo, de uno en uno.
    assert (peak["limited"], peak["group"], peak["roomy"] <= 3) == (2, 1, True)
    assert all(peak[f"pipeline:{pid}"] == 1 for pid in pipelines)
    with orch.db.session() as session:
        statuses = session.scalars(select(Run.status).where(Run.pipeline_id.in_(pipelines))).all()
    assert Counter(statuses) == Counter({"succeeded": 20})


def test_pools_and_priority_decide_what_a_worker_takes(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    api = orch_admin
    prd_pool, dev_pool = _pool(), _pool()
    system = orch.system(api, max_concurrent_runs=5)
    source = orch.source(api, system["id"])
    destination = orch.destination(api)
    urgent = orch.pipeline(api, source=source, destination=destination, worker_pool=prd_pool)
    routine = orch.pipeline(
        api, source=source, destination=destination, worker_pool=prd_pool, priority=4
    )
    testing = orch.pipeline(api, source=source, destination=destination, worker_pool=dev_pool)
    for pid in (routine["id"], testing["id"]):
        _schedule(api, pid, type="interval", interval_minutes=5)
    orch.clock.advance(minutes=6)
    orch.scheduler().fire_due()  # ejecuciones programadas, con la prioridad del pipeline
    orch.clock.advance(seconds=1)
    manual = orch.run_now(api, urgent["id"])  # manual: entra después pero con prioridad alta

    executor = ScriptedExecutor()
    worker = orch.worker(executor, prd_pool)
    assert worker.poll_once() is True
    assert worker.poll_once() is True
    assert worker.poll_once() is False  # el run del pool de DEV no es suyo
    assert [str(c.pipeline_id) for c in executor.calls] == [urgent["id"], routine["id"]]
    assert str(executor.calls[0].run_id) == manual["id"]
    assert orch.runs_of(testing["id"])[0].status == "queued"
    # Las pruebas sobre DEV no retrasan a PRD: cada pool tiene sus workers.
    assert orch.worker(ScriptedExecutor(), dev_pool).poll_once() is True


def test_worker_without_executor_leaves_runs_queued(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])
    idle = orch.worker(None, pool)
    assert idle.poll_once() is False
    assert orch.run(run["id"]).status == "queued"
    listed = {w["id"]: w for w in orch_admin.get("/workers").json()}
    assert listed[str(idle.worker_id)]["has_executor"] is False


# ====================================================================== workers y avisos
def test_dead_workers_are_marked_offline(orch: OrchEnv) -> None:
    worker = orch.worker(ScriptedExecutor(), _pool())
    scheduler = orch.scheduler()
    worker.beat()
    scheduler.sweep_workers()
    with orch.db.session() as session:
        assert session.get(Worker, worker.worker_id).status == "online"  # type: ignore[union-attr]

    orch.clock.advance(seconds=200)
    scheduler.sweep_workers()
    with orch.db.session() as session:
        assert session.get(Worker, worker.worker_id).status == "offline"  # type: ignore[union-attr]
    orch.clock.advance(days=8)
    scheduler.sweep_workers()
    with orch.db.session() as session:
        assert session.get(Worker, worker.worker_id) is None

    leaving = orch.worker(ScriptedExecutor(), _pool())
    leaving.unregister()
    with orch.db.session() as session:
        assert session.get(Worker, leaving.worker_id).status == "offline"  # type: ignore[union-attr]


def test_queue_listener_wakes_up_on_a_new_run(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pipeline = orch.pipeline(orch_admin, worker_pool=_pool())
    listener = QueueListener(orch.db.engine)
    try:
        assert listener.wait(0.2) is False
        orch.run_now(orch_admin, pipeline["id"])
        assert listener.wait(5) is True
    finally:
        listener.close()


def test_scheduler_tick_reports_what_it_did(orch: OrchEnv, orch_admin: ApiClient) -> None:
    pipeline = orch.pipeline(orch_admin, worker_pool=_pool())
    _schedule(orch_admin, pipeline["id"], type="cron", cron_expression="* * * * *")
    orch.clock.advance(minutes=1, seconds=1)
    result = scheduler_main.tick(SchedulerService(orch.db, orch.toolkit))
    assert result.fired >= 1
    assert len(orch.runs_of(pipeline["id"])) == 1


# ====================================================================== entrypoints
class _Shutdown:
    """``GracefulShutdown`` sin señales del sistema: la prueba decide cuándo parar."""

    def __init__(self) -> None:
        self.event = threading.Event()

    def install(self) -> None:
        return None

    @property
    def requested(self) -> bool:
        return self.event.is_set()

    def wait(self, seconds: float) -> bool:
        return self.event.wait(min(seconds, 0.05))


def _process_settings(orch: OrchEnv, pool: str) -> Settings:
    return orch.toolkit.settings.model_copy(
        update={"worker_pools": [pool], "worker_poll_seconds": 0.2, "scheduler_tick_seconds": 0.05}
    )


def test_worker_process_takes_work_and_stops_cleanly(
    orch: OrchEnv, orch_admin: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _pool()
    pipeline = orch.pipeline(orch_admin, worker_pool=pool)
    run = orch.run_now(orch_admin, pipeline["id"])
    shutdown = _Shutdown()
    monkeypatch.setattr(worker_main, "GracefulShutdown", lambda: shutdown)
    # El proceso usa el reloj de la prueba: los runs se encolaron con él.
    monkeypatch.setattr(
        OrchestrationToolkit,
        "build",
        classmethod(lambda cls, settings, **_: replace(orch.toolkit, settings=settings)),
    )
    failsafe = threading.Timer(20, shutdown.event.set)  # si algo falla, la prueba no se cuelga
    failsafe.start()

    def then_stop(ctx: RunContext) -> None:
        threading.Timer(0.3, shutdown.event.set).start()  # SIGTERM con el worker ya ocioso

    worker_main.run(_process_settings(orch, pool), ScriptedExecutor(then_stop))
    failsafe.cancel()
    assert orch.run(run["id"]).status == "succeeded"
    with orch.db.session() as session:
        mine = session.scalars(select(Worker).where(Worker.pools.contains([pool]))).one()
    assert mine.status == "offline"

    # El proceso registra el motor de extracción (It5) como ejecutor y lo engancha a la cola.
    _, extraction = worker_main.build_toolkit(orch.toolkit.settings)
    assert isinstance(worker_main.build_executor(orch.db, extraction), ExtractionEngine)
    assert ":" in worker_main.worker_id()


def test_scheduler_process_leads_ticks_and_stops(
    orch: OrchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown = _Shutdown()
    monkeypatch.setattr(scheduler_main, "GracefulShutdown", lambda: shutdown)
    ticks: list[int] = []

    def counting_tick(service: SchedulerService) -> Any:
        ticks.append(1)
        if len(ticks) == 1:
            msg = "fallo puntual"
            raise RuntimeError(msg)  # un ciclo fallido no tumba al líder
        if len(ticks) >= 3:
            shutdown.event.set()
        return service.tick()

    monkeypatch.setattr(scheduler_main, "tick", counting_tick)
    # Las tareas diarias reales llaman a SAP: aquí solo importa que el líder las lance.
    maintained = threading.Event()

    def daily() -> dict[str, Any]:
        maintained.set()
        return {}

    task = SystemTask(f"prueba_{uuid.uuid4().hex[:8]}", daily)
    monkeypatch.setattr(scheduler_main, "system_tasks", lambda *_, **__: [task])
    failsafe = threading.Timer(20, shutdown.event.set)
    failsafe.start()
    scheduler_main.run(_process_settings(orch, "default"))
    failsafe.cancel()
    assert len(ticks) == 3
    assert maintained.is_set()
