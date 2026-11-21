"""Bucle del worker: toma un run de la cola y lo ejecuta con el motor (plan 08 §3, reducido).

La It6 añade los límites por sistema SAP y por grupo, los pools y ``LISTEN/NOTIFY``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import structlog

from sapextract.core.db import Database
from sapextract.extraction.application.engine import ExtractionEngine
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.store import RunStore
from sapextract.extraction.domain.model import AttemptOutcome

log = structlog.get_logger(__name__)


class Worker:
    def __init__(
        self,
        db: Database,
        toolkit: ExtractionToolkit,
        *,
        worker_id: str,
        spool_root: Path | None = None,
        shutdown: Callable[[], bool] = lambda: False,
    ) -> None:
        self.worker_id = worker_id
        self.store = RunStore(db, toolkit.audit)
        root = spool_root or toolkit.settings.spool_dir
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = ExtractionEngine(
            db, toolkit, self.store, worker_id=worker_id, spool_root=root, shutdown=shutdown
        )

    def poll_once(self) -> AttemptOutcome | None:
        """Toma y ejecuta un run. ``None`` si la cola estaba vacía."""
        claim = self.store.claim(self.worker_id)
        if claim is None:
            return None
        with structlog.contextvars.bound_contextvars(
            run_id=str(claim.run_id), attempt=claim.attempt
        ):
            log.info("run_attempt_started", mode=claim.mode.value)
            outcome = self.engine.execute(claim)
            log.info("run_attempt_finished", outcome=outcome.value)
        return outcome


def reap_stale_runs(db: Database, toolkit: ExtractionToolkit) -> int:
    """Lo invoca el scheduler en cada ciclo: intentos sin latido vuelven a la cola."""
    store = RunStore(db, toolkit.audit)
    return store.reap_stale(timedelta(seconds=toolkit.settings.worker_stale_seconds))
