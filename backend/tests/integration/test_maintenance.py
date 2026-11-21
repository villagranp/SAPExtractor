"""Tareas diarias de sistema del scheduler (plan 03 §4, plan 08 §2).

Retención del log de ejecución, verificación periódica de la cadena de auditoría, refresco de
los catálogos SAP y el ejecutor que decide a cuál le toca.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

import sapextract.main_scheduler as scheduler_main
from sapextract.audit.application.service import AuditService
from sapextract.audit.infrastructure.models import AuditEventRow
from sapextract.core.db import Database
from sapextract.core.maintenance import MaintenanceRunner, SystemTask
from sapextract.extraction.application.maintenance import apply_run_log_retention
from sapextract.sap_connections.application.explorer_service import SapExplorerService
from sapextract.sap_connections.infrastructure.models import SapCatalogCache
from tests.integration.conftest import ApiClient
from tests.support.orchestration import OrchEnv

pytestmark = pytest.mark.integration

OLD_PARTITION = "run_log_200001"


def _exists(db: Database, table: str) -> bool:
    with db.session() as session:
        found = session.execute(text("SELECT to_regclass(:t)"), {"t": f"sx.{table}"}).scalar()
    return found is not None


def _retention_events(db: Database) -> list[AuditEventRow]:
    with db.session() as session:
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.action == "system.retention_applied")
            .order_by(AuditEventRow.seq)
        )
        return list(session.scalars(stmt))


def test_run_log_retention_drops_only_expired_partitions(platform_db: Database) -> None:
    db = platform_db
    recent = uuid.uuid4()
    with db.session() as session:
        session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS sx.{OLD_PARTITION} PARTITION OF sx.run_log "
                "FOR VALUES FROM ('2000-01-01+00') TO ('2000-02-01+00')"
            )
        )
        insert = text(
            "INSERT INTO sx.run_log (ts, run_id, level, message) VALUES (:ts, :run, 'INFO', :m)"
        )
        old_line = {"ts": datetime(2000, 1, 15, tzinfo=UTC), "run": uuid.uuid4(), "m": "vencida"}
        session.execute(insert, old_line)
        session.execute(insert, {"ts": datetime.now(UTC), "run": recent, "m": "vigente"})
        session.commit()
    before = len(_retention_events(db))

    with db.session() as session:
        result = apply_run_log_retention(session, AuditService(), days=90, now=datetime.now(UTC))
    assert result.dropped == (OLD_PARTITION,)
    assert not _exists(db, OLD_PARTITION)
    with db.session() as session:
        kept = session.execute(
            text("SELECT count(*) FROM sx.run_log WHERE run_id = :run"), {"run": recent}
        ).scalar()
    assert kept == 1

    events = _retention_events(db)
    assert len(events) == before + 1
    assert events[-1].actor_type == "system"
    assert events[-1].context["partitions_dropped"] == [OLD_PARTITION]
    assert events[-1].context["retention_days"] == 90

    # Sin nada vencido no borra ni deja evento.
    with db.session() as session:
        again = apply_run_log_retention(session, AuditService(), days=90, now=datetime.now(UTC))
    assert again.changed is False
    assert len(_retention_events(db)) == before + 1


def test_runner_runs_each_task_once_a_day_and_retries_failures(platform_db: Database) -> None:
    calls: list[str] = []
    now = [datetime(2026, 11, 21, 8, 0, tzinfo=UTC)]
    failing, working = f"falla_{uuid.uuid4().hex[:8]}", f"va_{uuid.uuid4().hex[:8]}"

    def boom() -> dict[str, Any]:
        calls.append("boom")
        msg = "fallo de la tarea"
        raise RuntimeError(msg)

    def fine() -> dict[str, Any]:
        calls.append("fine")
        return {"done": 1}

    runner = MaintenanceRunner(
        platform_db,
        [SystemTask(failing, boom), SystemTask(working, fine)],
        clock=lambda: now[0],
    )
    # Una tarea que falla no impide la siguiente.
    assert runner.run_due() == [working]
    assert calls == ["boom", "fine"]
    # Una hora después: la que falló se repite; la que terminó bien espera a mañana.
    now[0] += timedelta(hours=1)
    assert runner.run_due() == []
    assert calls == ["boom", "fine", "boom"]
    # Un scheduler nuevo (otro proceso) lee de la base cuándo se ejecutó cada una.
    restarted = MaintenanceRunner(platform_db, [SystemTask(working, fine)], clock=lambda: now[0])
    assert restarted.run_due() == []
    now[0] += timedelta(hours=24)
    assert restarted.run_due() == [working]
    # Con el apagado pedido no empieza ninguna.
    now[0] += timedelta(hours=48)
    stopping = MaintenanceRunner(
        platform_db, [SystemTask(working, fine)], clock=lambda: now[0], should_stop=lambda: True
    )
    assert stopping.run_due() == []


def test_scheduler_declares_the_daily_system_tasks(orch: OrchEnv) -> None:
    tasks = {t.name: t for t in scheduler_main.system_tasks(orch.db, orch.toolkit)}
    assert list(tasks) == ["partitions", "run_log_retention", "audit_verification", "sap_catalogs"]

    assert set(tasks["partitions"].run()) == {
        "audit_partitions_created",
        "run_log_partitions_created",
    }
    assert set(tasks["run_log_retention"].run()) == {"partitions_dropped", "default_rows_deleted"}

    verified = tasks["audit_verification"].run()
    assert verified["checked"] > 0
    with orch.db.session() as session:
        last = AuditService().last_verification(session)
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.action.in_(("audit.verified", "audit.verification_failed")))
            .order_by(AuditEventRow.seq.desc())
            .limit(1)
        )
        event = session.scalars(stmt).one()
    assert last is not None
    assert last["ok"] is verified["ok"]
    assert (event.actor_type, event.actor_name) == ("system", "scheduler")


def test_catalog_refresh_keeps_the_cache_of_a_system_that_fails(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    system = orch.system(orch_admin)
    system_id = uuid.UUID(system["id"])

    def fetched_at() -> datetime | None:
        with orch.db.session() as session:
            cache = session.get(SapCatalogCache, system_id)
            return cache.fetched_at if cache else None

    with orch.db.session() as session:
        first = SapExplorerService(session, orch.toolkit.sap).refresh_all(only=[system_id])
    assert (first.refreshed, first.failed) == ((system["code"],), ())
    stamp = fetched_at()
    assert stamp is not None

    orch.gateway.scenario(status=503, times=5)
    try:
        with orch.db.session() as session:
            second = SapExplorerService(session, orch.toolkit.sap).refresh_all(only=[system_id])
    finally:
        orch.gateway.control("/_mock/reset")
    assert (second.refreshed, second.failed) == ((), (system["code"],))
    assert fetched_at() == stamp  # conserva el catálogo anterior

    with orch.db.session() as session:
        stopped = SapExplorerService(session, orch.toolkit.sap).refresh_all(
            only=[system_id], should_stop=lambda: True
        )
    assert (stopped.refreshed, stopped.failed) == ((), ())
