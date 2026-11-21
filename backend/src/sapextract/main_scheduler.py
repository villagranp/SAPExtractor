"""Entrypoint del scheduler: ``python -m sapextract.main_scheduler``.

Solo un scheduler es líder a la vez (``pg_try_advisory_lock`` sobre una conexión dedicada);
las demás réplicas esperan y toman el relevo si el líder cae (la BD libera el bloqueo al
cerrarse su conexión). La lógica de programación se añade en la iteración 6.
"""

from __future__ import annotations

import time

import structlog
from sqlalchemy import Connection, text

from sapextract.audit.application.maintenance import ensure_partitions
from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.runtime import GracefulShutdown
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.worker import reap_stale_runs
from sapextract.sap_connections.application.toolkit import SapToolkit

log = structlog.get_logger(__name__)

# Clave fija del advisory lock de liderazgo del scheduler.
SCHEDULER_LEADER_LOCK = 7_215_001
MAINTENANCE_INTERVAL_SECONDS = 24 * 3600


def try_acquire_leadership(conn: Connection) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LEADER_LOCK}).scalar()
    )


def run_maintenance(conn: Connection) -> None:
    """Tareas diarias de sistema (particiones de auditoría y del log de ejecución)."""
    created = ensure_partitions(conn)
    run_log = conn.execute(text("SELECT sx.run_log_ensure_partitions(3)")).scalar()
    log.info(
        "maintenance_done", audit_partitions_created=created, run_log_partitions_created=run_log
    )


def tick(db: Database | None = None, toolkit: ExtractionToolkit | None = None) -> None:
    """Un ciclo: los intentos sin latido vuelven a la cola. La It6 encola lo programado."""
    if db is not None and toolkit is not None:
        reaped = reap_stale_runs(db, toolkit)
        if reaped:
            log.warning("stale_runs_requeued", count=reaped)
    log.debug("scheduler_tick")


def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    audit = AuditService()
    toolkit = ExtractionToolkit.build(
        settings,
        sap=SapToolkit.build(settings, audit=audit),
        destinations=DestinationsToolkit.build(settings, audit=audit),
        audit=audit,
    )
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
                    last_maintenance: float | None = None
                    while not shutdown.requested:
                        conn.execute(text("SELECT 1"))  # mantiene viva la conexión del bloqueo
                        if (
                            last_maintenance is None
                            or time.monotonic() - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS
                        ):
                            try:
                                run_maintenance(conn)
                            except Exception:
                                log.exception("maintenance_failed")
                            last_maintenance = time.monotonic()
                        try:
                            tick(db, toolkit)
                        except Exception:
                            log.exception("scheduler_tick_failed")
                        shutdown.wait(settings.scheduler_tick_seconds)
            except Exception:
                log.exception("scheduler_loop_error")
                shutdown.wait(settings.scheduler_tick_seconds)
    finally:
        db.dispose()
        log.info("scheduler_stopped")


if __name__ == "__main__":
    run()
