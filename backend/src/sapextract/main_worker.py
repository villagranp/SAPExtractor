"""Entrypoint del worker: ``python -m sapextract.main_worker``.

Iteración 0: proceso vivo con latido y apagado ordenado. La toma de trabajos de la cola
(``SELECT … FOR UPDATE SKIP LOCKED``) y el motor de extracción llegan en las iteraciones 5 y 6.
"""

from __future__ import annotations

import os
import socket

import structlog

from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.runtime import GracefulShutdown

log = structlog.get_logger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def poll_once() -> bool:
    """Intenta tomar y ejecutar un trabajo. Devuelve True si procesó alguno."""
    return False


def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    structlog.contextvars.bind_contextvars(worker_id=worker_id())
    log.info("worker_started")

    try:
        while not shutdown.requested:
            try:
                db.ping()
                if not poll_once():
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
