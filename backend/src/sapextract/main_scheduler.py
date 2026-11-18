"""Entrypoint del scheduler: ``python -m sapextract.main_scheduler``.

Solo un scheduler es líder a la vez (``pg_try_advisory_lock`` sobre una conexión dedicada);
las demás réplicas esperan y toman el relevo si el líder cae (la BD libera el bloqueo al
cerrarse su conexión). La lógica de programación se añade en la iteración 6.
"""

from __future__ import annotations

import structlog
from sqlalchemy import Connection, text

from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.runtime import GracefulShutdown

log = structlog.get_logger(__name__)

# Clave fija del advisory lock de liderazgo del scheduler.
SCHEDULER_LEADER_LOCK = 7_215_001


def try_acquire_leadership(conn: Connection) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LEADER_LOCK}).scalar()
    )


def tick() -> None:
    """Un ciclo del scheduler. Iteración 6: encolar ejecuciones vencidas y tareas de sistema."""
    log.debug("scheduler_tick")


def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    log.info("scheduler_started")

    try:
        while not shutdown.requested:
            try:
                with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    while not shutdown.requested and not try_acquire_leadership(conn):
                        log.debug("scheduler_standby")
                        shutdown.wait(settings.scheduler_tick_seconds)
                    if shutdown.requested:
                        break
                    log.info("scheduler_leadership_acquired")
                    while not shutdown.requested:
                        conn.execute(text("SELECT 1"))  # mantiene viva la conexión del bloqueo
                        tick()
                        shutdown.wait(settings.scheduler_tick_seconds)
            except Exception:
                log.exception("scheduler_loop_error")
                shutdown.wait(settings.scheduler_tick_seconds)
    finally:
        db.dispose()
        log.info("scheduler_stopped")


if __name__ == "__main__":
    run()
