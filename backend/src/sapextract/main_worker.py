"""Entrypoint del worker: ``python -m sapextract.main_worker``.

Toma runs de la cola de sus *pools* (``SX_WORKER_POOLS``), los entrega al ejecutor y mantiene
el latido (plan 08 §3). Al recibir SIGTERM el run en curso se detiene entre páginas, su
intento se cierra como interrumpido y el run vuelve a la cola.

El ejecutor es el motor de extracción (It5, ``ExtractionEngine``); su enganche con la cola
(``DeltaHooks``) suelta el estado de delta cuando un run termina sin éxito.
"""

from __future__ import annotations

import os
import socket
import time

import structlog

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.runtime import GracefulShutdown
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.extraction.application.delta_service import DeltaHooks
from sapextract.extraction.application.engine import ExtractionEngine
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.application.worker import WorkerRuntime
from sapextract.orchestration.domain.ports import RunExecutor
from sapextract.orchestration.infrastructure.queue import QueueListener
from sapextract.sap_connections.application.toolkit import SapToolkit

log = structlog.get_logger(__name__)

LISTEN_SLICE_SECONDS = 1.0


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def build_toolkit(settings: Settings) -> tuple[OrchestrationToolkit, ExtractionToolkit]:
    audit = AuditService()
    sap = SapToolkit.build(settings, audit=audit)
    destinations = DestinationsToolkit.build(settings, audit=audit)
    extraction = ExtractionToolkit.build(settings, sap=sap, destinations=destinations, audit=audit)
    orchestration = OrchestrationToolkit.build(
        settings, audit=audit, sap=sap, destinations=destinations, hooks=DeltaHooks(extraction)
    )
    return orchestration, extraction


def build_executor(db: Database, extraction: ExtractionToolkit) -> RunExecutor:
    """Ejecutor de runs de este proceso: el motor de extracción (It5)."""
    return ExtractionEngine(db, extraction)


def idle(listener: QueueListener | None, shutdown: GracefulShutdown, seconds: float) -> None:
    """Espera trabajo: un aviso de la cola (``NOTIFY``) corta la espera sin agotar el sondeo."""
    if listener is None:
        shutdown.wait(seconds)
        return
    deadline = time.monotonic() + seconds
    while not shutdown.requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or listener.wait(min(remaining, LISTEN_SLICE_SECONDS)):
            return


def open_listener(db: Database) -> QueueListener | None:
    try:
        return QueueListener(db.engine)
    except Exception:
        log.exception("queue_listener_unavailable")
        return None


def run(settings: Settings | None = None, executor: RunExecutor | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    orchestration, extraction = build_toolkit(settings)
    runtime = WorkerRuntime(
        db,
        orchestration,
        executor=executor or build_executor(db, extraction),
        shutdown=shutdown.event,
    )
    structlog.contextvars.bind_contextvars(worker_id=str(runtime.worker_id), host=worker_id())
    log.info("worker_started", pools=runtime.pools, has_executor=runtime.executor is not None)
    if runtime.executor is None:
        log.warning("worker_without_executor")

    listener: QueueListener | None = None
    registered = False
    try:
        while not shutdown.requested:
            try:
                if not registered:
                    runtime.register()
                    registered = True
                runtime.beat()
                if runtime.poll_once():
                    continue
                if listener is None and runtime.executor is not None:
                    listener = open_listener(db)
                log.debug("worker_idle")
                idle(listener, shutdown, settings.worker_poll_seconds)
            except Exception:
                log.exception("worker_loop_error")
                if listener is not None:
                    _close(listener)
                    listener = None
                shutdown.wait(settings.worker_poll_seconds)
    finally:
        if listener is not None:
            _close(listener)
        if registered:
            try:
                runtime.unregister()
            except Exception:
                log.exception("worker_unregister_failed")
        db.dispose()
        log.info("worker_stopped")


def _close(listener: QueueListener) -> None:
    try:
        listener.close()
    except Exception:
        log.exception("queue_listener_close_failed")


if __name__ == "__main__":
    run()
