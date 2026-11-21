"""Entrypoint del scheduler: ``python -m sapextract.main_scheduler``.

Solo un scheduler es líder a la vez (``pg_try_advisory_lock`` sobre una conexión dedicada);
las demás réplicas esperan y toman el relevo si el líder cae (la BD libera el bloqueo al
cerrarse su conexión). En cada ciclo el líder encola las programaciones vencidas, recupera
los runs sin latido y marca los workers caídos (plan 08 §2).

Las tareas diarias de sistema (particiones, retención del log de ejecución, verificación de la
cadena de auditoría y refresco de los catálogos SAP) corren en un hilo aparte: una llamada lenta
a SAP no retrasa la programación.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import Connection, text

from sapextract.audit.application.maintenance import ensure_partitions
from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.model import Actor
from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.logging import configure_logging
from sapextract.core.maintenance import MaintenanceRunner, SystemTask
from sapextract.core.runtime import GracefulShutdown
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.extraction.application.delta_service import DeltaHooks
from sapextract.extraction.application.maintenance import apply_run_log_retention
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.monitoring.application.evaluator import AlertEngine
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.orchestration.application.scheduler import SchedulerService, TickResult
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.sap_connections.application.explorer_service import SapExplorerService
from sapextract.sap_connections.application.toolkit import SapToolkit

log = structlog.get_logger(__name__)

# Clave fija del advisory lock de liderazgo del scheduler.
SCHEDULER_LEADER_LOCK = 7_215_001
# Cada cuánto se mira si a alguna tarea diaria le toca (o si falló y hay que repetirla).
MAINTENANCE_CHECK_SECONDS = 3600.0
MAINTENANCE_JOIN_SECONDS = 10.0
SYSTEM_ACTOR = "scheduler"


def try_acquire_leadership(conn: Connection) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LEADER_LOCK}).scalar()
    )


def system_tasks(
    db: Database,
    toolkit: OrchestrationToolkit,
    *,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[SystemTask]:
    """Tareas diarias de sistema (plan 03 §4, plan 08 §2), en el orden en que se ejecutan."""
    settings = toolkit.settings

    def partitions() -> dict[str, Any]:
        with db.session() as session:
            created = ensure_partitions(session)
            run_log = session.execute(text("SELECT sx.run_log_ensure_partitions(3)")).scalar()
            session.commit()
        return {"audit_partitions_created": created, "run_log_partitions_created": run_log}

    def run_log_retention() -> dict[str, Any]:
        with db.session() as session:
            result = apply_run_log_retention(
                session, toolkit.audit, days=settings.run_log_retention_days, now=toolkit.clock()
            )
        return {
            "partitions_dropped": list(result.dropped),
            "default_rows_deleted": result.default_rows,
        }

    def audit_verification() -> dict[str, Any]:
        with db.session() as session:
            result = toolkit.audit.verify_and_record(session, actor=Actor.system(SYSTEM_ACTOR))
        return {"ok": result.ok, "checked": result.checked}

    def sap_catalogs() -> dict[str, Any]:
        with db.session() as session:
            result = SapExplorerService(session, toolkit.sap).refresh_all(should_stop=should_stop)
        return {"refreshed": len(result.refreshed), "failed": list(result.failed)}

    return [
        SystemTask("partitions", partitions),
        SystemTask("run_log_retention", run_log_retention),
        SystemTask("audit_verification", audit_verification),
        SystemTask("sap_catalogs", sap_catalogs),
    ]


class MaintenanceThread:
    """Lanza las tareas diarias en segundo plano, nunca dos pasadas a la vez."""

    def __init__(self, runner: MaintenanceRunner) -> None:
        self.runner = runner
        self._thread: threading.Thread | None = None

    def start_if_idle(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self._run, name="sx-maintenance", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self.runner.run_due()
        except Exception:
            log.exception("maintenance_failed")

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


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
    toolkit = build_toolkit(settings)
    service = SchedulerService(db, toolkit)
    alerts = AlertEngine(db, MonitoringToolkit.build(settings))
    maintenance = MaintenanceThread(
        MaintenanceRunner(
            db,
            system_tasks(db, toolkit, should_stop=lambda: shutdown.requested),
            clock=toolkit.clock,
            should_stop=lambda: shutdown.requested,
        )
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
                    last_alerts = 0.0
                    while not shutdown.requested:
                        conn.execute(text("SELECT 1"))  # mantiene viva la conexión del bloqueo
                        if (
                            last_maintenance is None
                            or time.monotonic() - last_maintenance >= MAINTENANCE_CHECK_SECONDS
                        ):
                            maintenance.start_if_idle()
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
        maintenance.join(MAINTENANCE_JOIN_SECONDS)  # deja terminar la tarea en curso
        db.dispose()
        log.info("scheduler_stopped")


if __name__ == "__main__":
    run()
