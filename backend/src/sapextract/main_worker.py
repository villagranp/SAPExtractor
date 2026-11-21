"""Entrypoint del worker: ``python -m sapextract.main_worker``.

Toma runs de la cola (``SELECT … FOR UPDATE SKIP LOCKED``) y los ejecuta con el motor
(plan 06). Al recibir SIGTERM termina la página en curso y devuelve el run a la cola.
"""

from __future__ import annotations

import os
import socket

import structlog

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.runtime import GracefulShutdown
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.worker import Worker
from sapextract.sap_connections.application.toolkit import SapToolkit

log = structlog.get_logger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def build_worker(settings: Settings, db: Database, shutdown: GracefulShutdown) -> Worker:
    audit = AuditService()
    sap = SapToolkit.build(settings, audit=audit)
    destinations = DestinationsToolkit.build(settings, audit=audit)
    toolkit = ExtractionToolkit.build(settings, sap=sap, destinations=destinations, audit=audit)
    return Worker(db, toolkit, worker_id=worker_id(), shutdown=lambda: shutdown.requested)


def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    worker = build_worker(settings, db, shutdown)
    structlog.contextvars.bind_contextvars(worker_id=worker_id())
    log.info("worker_started")

    try:
        while not shutdown.requested:
            try:
                db.ping()
                if worker.poll_once() is None:
                    log.debug("worker_idle")
                    shutdown.wait(settings.worker_poll_seconds)
            except Exception:
                log.exception("worker_loop_error")
                shutdown.wait(settings.worker_poll_seconds)
    finally:
        db.dispose()
        log.info("worker_stopped")


if __name__ == "__main__":
    run()
