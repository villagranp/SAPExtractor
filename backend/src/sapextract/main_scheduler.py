"""Entrypoint del scheduler: ``python -m sapextract.main_scheduler``.

Solo un scheduler es líder a la vez (``pg_try_advisory_lock`` sobre una conexión dedicada);
las demás réplicas esperan y toman el relevo si el líder cae (la BD libera el bloqueo al
cerrarse su conexión). En cada ciclo el líder encola las programaciones vencidas, recupera
los runs sin latido y marca los workers caídos (plan 08 §2).
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
from sapextract.extraction.application.delta_service import DeltaHooks
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.monitoring.application.evaluator import AlertEngine
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.orchestration.application.scheduler import SchedulerService, TickResult
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
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
    log.info("maintenance_done", audit_partitions_created=created, run_log_partitions=run_log)


def build_toolkit(settings: Settings) -> OrchestrationToolkit:
    """Toolkit con el motor enganchado: al recuperar runs caídos suelta su estado de delta."""
    audit = AuditService()
    sap = SapToolkit.build(settings, audit=audit)
    destinations = DestinationsToolkit.build(settings, audit=audit)
    extraction = ExtractionToolkit.build(settings, sap=sap, destinations=destinations, audit=audit)
    return OrchestrationToolkit.build(
        settings, audit=audit, sap=sap, destinations=destinations, hooks=DeltaHooks(extraction)
    )


def tick(service: SchedulerService) -> TickResult:
    """Un ciclo del scheduler: programación, runs huérfanos y workers caídos."""
    result = service.tick()
    if result.fired or result.skipped or result.reaped or result.workers_offline:
        log.info(
            "scheduler_tick",
            fired=result.fired,
            skipped=result.skipped,
            reaped=result.reaped,
            workers_offline=result.workers_offline,
        )
    return result


def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings)
    shutdown = GracefulShutdown()
    shutdown.install()
    db = Database(settings)
    service = SchedulerService(db, build_toolkit(settings))
    alerts = AlertEngine(db, MonitoringToolkit.build(settings))
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
                    last_alerts = 0.0
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
                            tick(service)
                        except Exception:
                            # Un ciclo fallido no suelta el liderazgo: se reintenta en el siguiente.
                            log.exception("scheduler_tick_failed")
                        if time.monotonic() - last_alerts >= settings.alert_eval_seconds:
                            try:
                                alerts.evaluate()
                            except Exception:
                                log.exception("alert_evaluation_failed")
                            last_alerts = time.monotonic()
                        shutdown.wait(settings.scheduler_tick_seconds)
            except Exception:
                log.exception("scheduler_loop_error")
                shutdown.wait(settings.scheduler_tick_seconds)
    finally:
        db.dispose()
        log.info("scheduler_stopped")


if __name__ == "__main__":
    run()
