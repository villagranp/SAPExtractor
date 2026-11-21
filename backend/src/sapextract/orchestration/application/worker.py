"""Worker (plan 08 §3): toma runs de la cola, los ejecuta con latido y cierra cada intento.

El worker no sabe extraer: entrega el run a un ``RunExecutor`` (el motor, It5). Mientras
trabaja, un hilo mantiene el latido y comprueba el arrendamiento; si lo pierde, la
cancelación llega al ejecutor por ``RunContext.stop_reason``.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import update

from sapextract.core.db import Database, new_id
from sapextract.core.logging import safe_trace
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.orchestration.application.lifecycle import Verdict, VerdictKind, settle
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.domain.model import (
    ERROR_TIMEOUT,
    ERROR_UNEXPECTED,
    AttemptOutcome,
    WorkerStatus,
    normalize_pool,
)
from sapextract.orchestration.domain.ports import (
    RunContext,
    RunExecutor,
    RunStoppedError,
    StopReason,
)
from sapextract.orchestration.infrastructure.models import Run, Worker
from sapextract.orchestration.infrastructure.queue import ClaimedRun, claim, heartbeat

log = structlog.get_logger(__name__)

# Intentos que dejan el run terminado sin éxito: el motor suelta su estado de delta.
FINAL_FAILURES = (AttemptOutcome.FAILED, AttemptOutcome.CANCELLED)


class LeaseGuard:
    """Latido de un run en curso y motivos para dejarlo (cancelación, *fencing*, plazo, apagado)."""

    def __init__(
        self,
        db: Database,
        toolkit: OrchestrationToolkit,
        claimed: ClaimedRun,
        *,
        worker_id: uuid.UUID,
        deadline: datetime,
        shutdown: threading.Event,
    ) -> None:
        self._db = db
        self._tk = toolkit
        self._claimed = claimed
        self._worker_id = worker_id
        self._deadline = deadline
        self._shutdown = shutdown
        self._stop = threading.Event()
        self._fenced = threading.Event()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"lease-{claimed.run_id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._tk.settings.worker_heartbeat_seconds + 5)

    def beat(self) -> None:
        """Un latido: renueva el run y el worker, y lee si pidieron cancelar."""
        now = self._tk.clock()
        with self._db.session() as session:
            lease = heartbeat(session, self._claimed, now=now)
            session.execute(
                update(Worker).where(Worker.id == self._worker_id).values(heartbeat_at=now)
            )
            session.commit()
        if not lease.alive:
            self._fenced.set()
        elif lease.cancel_requested:
            self._cancelled.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._tk.settings.worker_heartbeat_seconds):
            try:
                self.beat()
            except Exception:
                # Sin latido el scheduler recuperará el run; aquí solo se deja constancia.
                log.exception("heartbeat_failed", run_id=str(self._claimed.run_id))
            if self._fenced.is_set():
                return

    def stop_reason(self) -> StopReason | None:
        if self._fenced.is_set():
            return StopReason.FENCED
        if self._cancelled.is_set():
            return StopReason.CANCELLED
        if self._tk.clock() >= self._deadline:
            return StopReason.TIMEOUT
        if self._shutdown.is_set():
            return StopReason.SHUTDOWN
        return None


def _verdict_for(reason: StopReason) -> Verdict:
    match reason:
        case StopReason.CANCELLED:
            return Verdict(kind=VerdictKind.CANCELLED)
        case StopReason.SHUTDOWN:
            return Verdict(kind=VerdictKind.INTERRUPTED)
        case StopReason.FENCED:
            return Verdict(kind=VerdictKind.FENCED)
        case StopReason.TIMEOUT:
            return Verdict(
                kind=VerdictKind.TRANSIENT,
                error_code=ERROR_TIMEOUT,
                error_kind=ErrorKind.TRANSIENT,
                error_message="El run superó el tiempo máximo configurado en el pipeline.",
            )


class WorkerRuntime:
    def __init__(
        self,
        db: Database,
        toolkit: OrchestrationToolkit,
        *,
        executor: RunExecutor | None,
        pools: list[str] | None = None,
        worker_id: uuid.UUID | None = None,
        shutdown: threading.Event | None = None,
    ) -> None:
        self.db = db
        self.tk = toolkit
        self.executor = executor
        self.pools = [normalize_pool(p) for p in (pools or toolkit.settings.worker_pools)]
        self.worker_id = worker_id or new_id()
        self.shutdown = shutdown or threading.Event()

    # -------------------------------------------------------------- registro
    def register(self) -> None:
        now = self.tk.clock()
        with self.db.session() as session:
            session.merge(
                Worker(
                    id=self.worker_id,
                    hostname=socket.gethostname()[:200],
                    pid=os.getpid(),
                    pools=self.pools,
                    status=WorkerStatus.ONLINE.value,
                    has_executor=self.executor is not None,
                    current_run_id=None,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            session.commit()

    def beat(self) -> None:
        """Latido del worker ocioso (con un run en curso lo da ``LeaseGuard``)."""
        self._update(heartbeat_at=self.tk.clock())

    def unregister(self) -> None:
        self._update(status=WorkerStatus.OFFLINE.value, current_run_id=None)

    def _update(self, **values: object) -> None:
        with self.db.session() as session:
            session.execute(update(Worker).where(Worker.id == self.worker_id).values(**values))
            session.commit()

    # -------------------------------------------------------------- trabajo
    def poll_once(self) -> bool:
        """Toma un run y lo ejecuta hasta cerrar el intento. True si procesó alguno."""
        if self.executor is None:
            return False  # sin motor registrado no se toma trabajo: los runs esperan en la cola
        now = self.tk.clock()
        with self.db.session() as session:
            claimed = claim(session, worker_id=self.worker_id, pools=self.pools, now=now)
            if claimed is not None:
                session.execute(
                    update(Worker)
                    .where(Worker.id == self.worker_id)
                    .values(current_run_id=claimed.run_id, heartbeat_at=now)
                )
            session.commit()
        if claimed is None:
            return False
        self.run(claimed, started=now)
        return True

    def run(self, claimed: ClaimedRun, *, started: datetime) -> AttemptOutcome:
        executor = self.executor
        if executor is None:  # lo impide poll_once
            msg = "Worker sin ejecutor"
            raise RuntimeError(msg)
        guard = LeaseGuard(
            self.db,
            self.tk,
            claimed,
            worker_id=self.worker_id,
            deadline=started + timedelta(minutes=claimed.timeout_minutes),
            shutdown=self.shutdown,
        )
        context = RunContext(
            run_id=claimed.run_id,
            pipeline_id=claimed.pipeline_id,
            mode=claimed.mode,
            attempt=claimed.attempt,
            lease_epoch=claimed.lease_epoch,
            worker_id=self.worker_id,
            stop_reason=guard.stop_reason,
        )
        structlog.contextvars.bind_contextvars(run_id=str(claimed.run_id), attempt=claimed.attempt)
        log.info("run_started", pipeline_id=str(claimed.pipeline_id), mode=claimed.mode.value)
        guard.start()
        try:
            executor.execute(context)
            verdict = Verdict(kind=VerdictKind.SUCCEEDED)
        except RunStoppedError as exc:
            verdict = _verdict_for(exc.reason)
        except DataflowError as exc:
            verdict = Verdict(
                kind=VerdictKind.TRANSIENT if exc.retryable else VerdictKind.FAILED,
                error_code=exc.code,
                error_kind=exc.kind,
                error_message=exc.message,
            )
        except Exception as exc:
            # El texto de una excepción inesperada puede llevar datos (filas, credenciales):
            # ni el run ni el log lo guardan, solo el tipo y dónde ocurrió.
            log.error(
                "run_unexpected_error",
                error_type=type(exc).__name__,
                trace=safe_trace(exc),
            )
            verdict = Verdict(
                kind=VerdictKind.FAILED,
                error_code=ERROR_UNEXPECTED,
                error_message=(
                    f"Error inesperado ({type(exc).__name__}). Revise los logs del worker."
                ),
            )
        finally:
            guard.stop()
        try:
            outcome = self._settle(claimed, verdict)
            log.info("run_attempt_finished", outcome=outcome.value, error_code=verdict.error_code)
            return outcome
        finally:
            structlog.contextvars.unbind_contextvars("run_id", "attempt")

    def _settle(self, claimed: ClaimedRun, verdict: Verdict) -> AttemptOutcome:
        now = self.tk.clock()
        with self.db.session() as session:
            outcome = settle(session, claimed, verdict, now=now, random=self.tk.random())
            if self.tk.hooks is not None and outcome in FINAL_FAILURES:
                run = session.get(Run, claimed.run_id)
                if run is not None and run.lease_epoch == claimed.lease_epoch:
                    self.tk.hooks.run_finished(session, run)
            session.execute(
                update(Worker)
                .where(Worker.id == self.worker_id)
                .values(current_run_id=None, heartbeat_at=now)
            )
            session.commit()
        return outcome
