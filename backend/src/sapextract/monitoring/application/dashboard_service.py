"""Consultas del panel de monitoreo y de ``/metrics`` (plan 09 §1 y §4).

Todo son consultas agregadas sobre ``run`` (con índices por fecha de fin) para que el panel
cargue rápido con cientos de pipelines; no hay tablas de resumen todavía.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from sapextract.core.config import Settings
from sapextract.extraction.infrastructure.models import DeltaState, RunLog, RunStep
from sapextract.monitoring.domain.model import AlertStatus, AlertType, RuleScope
from sapextract.monitoring.infrastructure.models import Alert, AlertRule
from sapextract.orchestration.domain.model import PipelineStatus, RunStatus, WorkerStatus
from sapextract.orchestration.infrastructure.models import Pipeline, Run, Worker
from sapextract.sap_connections.infrastructure.models import SapSystem
from sapextract.sources.infrastructure.models import Source

LIVE = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
MAX_TIMELINE_HOURS = 168


@dataclass(frozen=True, kw_only=True)
class DashboardFilters:
    environment: str | None = None
    sap_system_id: uuid.UUID | None = None
    tag: str | None = None


class DashboardService:
    def __init__(self, session: Session, settings: Settings, now: datetime) -> None:
        self.session = session
        self.settings = settings
        self.now = now

    # ================================================================ filtros
    def _pipeline_ids(self, filters: DashboardFilters) -> Any:
        stmt = (
            select(Pipeline.id)
            .join(Source, Source.id == Pipeline.source_id)
            .join(SapSystem, SapSystem.id == Source.sap_system_id)
            .where(Pipeline.deleted_at.is_(None))
        )
        if filters.environment:
            stmt = stmt.where(SapSystem.environment == filters.environment)
        if filters.sap_system_id:
            stmt = stmt.where(SapSystem.id == filters.sap_system_id)
        if filters.tag:
            stmt = stmt.where(Pipeline.tags.contains([filters.tag]))
        return stmt

    # ================================================================ resumen
    def summary(self, filters: DashboardFilters) -> dict[str, Any]:
        ids = self._pipeline_ids(filters).scalar_subquery()
        day = self.now - timedelta(hours=24)
        week = self.now - timedelta(days=7)
        in_scope = Run.pipeline_id.in_(ids)
        active = self.session.scalar(
            select(func.count())
            .select_from(Pipeline)
            .where(Pipeline.id.in_(ids), Pipeline.status == PipelineStatus.ACTIVE.value)
        )
        by_status = dict(
            self.session.execute(
                select(Run.status, func.count())
                .where(in_scope, (Run.finished_at >= day) | Run.status.in_(LIVE))
                .group_by(Run.status)
            ).all()
        )
        rows_24h = self.session.scalar(
            select(func.coalesce(func.sum(Run.rows_extracted), 0)).where(
                in_scope, Run.status == RunStatus.SUCCEEDED.value, Run.finished_at >= day
            )
        )
        week_ok, week_failed = self.session.execute(
            select(
                func.count().filter(Run.status == RunStatus.SUCCEEDED.value),
                func.count().filter(Run.status == RunStatus.FAILED.value),
            ).where(in_scope, Run.finished_at >= week)
        ).one()
        broken = self.session.scalar(
            select(func.count())
            .select_from(DeltaState)
            .where(DeltaState.pipeline_id.in_(ids), DeltaState.status == "BROKEN")
        )
        alerts = dict(
            self.session.execute(
                select(Alert.severity, func.count())
                .where(Alert.status == AlertStatus.OPEN.value)
                .group_by(Alert.severity)
            ).all()
        )
        finished = (week_ok or 0) + (week_failed or 0)
        return {
            "pipelines_active": active or 0,
            "runs_24h": {
                "succeeded": by_status.get(RunStatus.SUCCEEDED.value, 0),
                "failed": by_status.get(RunStatus.FAILED.value, 0),
                "running": by_status.get(RunStatus.RUNNING.value, 0),
                "queued": by_status.get(RunStatus.QUEUED.value, 0),
            },
            "rows_24h": int(rows_24h or 0),
            "pipelines_broken": broken or 0,
            "success_rate_7d": round(week_ok / finished, 4) if finished else None,
            "alerts_open": {str(k): int(v) for k, v in alerts.items()},
            "live_runs": self.live_runs(ids),
            "recent_failures": self.failures(ids),
            "workers": self.workers(),
            "sap_systems": self.systems(filters),
        }

    def live_runs(self, ids: Any) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Run, Pipeline.code)
            .join(Pipeline, Pipeline.id == Run.pipeline_id)
            .where(Run.status.in_(LIVE), Run.pipeline_id.in_(ids))
            .order_by(Run.status.desc(), Run.created_at)
            .limit(50)
        ).all()
        result = []
        for run, code in rows:
            step = self.session.scalar(
                select(RunStep.step)
                .where(RunStep.run_id == run.id, RunStep.status == "running")
                .order_by(RunStep.started_at.desc())
                .limit(1)
            )
            progress = self.session.scalar(
                select(RunLog.data)
                .where(RunLog.run_id == run.id, RunLog.data.has_key("rows"))
                .order_by(RunLog.id.desc())
                .limit(1)
            )
            result.append(
                {
                    "id": str(run.id),
                    "pipeline_id": str(run.pipeline_id),
                    "pipeline_code": code,
                    "mode": run.mode,
                    "status": run.status,
                    "step": step,
                    "rows": int(progress["rows"]) if progress else None,
                    "pages": int(progress.get("pages", 0)) if progress else None,
                    "created_at": run.created_at,
                    "started_at": run.started_at,
                    "cancel_requested_at": run.cancel_requested_at,
                }
            )
        return result

    def failures(self, ids: Any, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Run, Pipeline.code)
            .join(Pipeline, Pipeline.id == Run.pipeline_id)
            .where(Run.status == RunStatus.FAILED.value, Run.pipeline_id.in_(ids))
            .order_by(Run.finished_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": str(run.id),
                "pipeline_id": str(run.pipeline_id),
                "pipeline_code": code,
                "mode": run.mode,
                "error_code": run.error_code,
                "error_message": run.error_message,
                "finished_at": run.finished_at,
            }
            for run, code in rows
        ]

    def workers(self) -> list[dict[str, Any]]:
        alive_after = self.now - timedelta(seconds=self.settings.run_stale_seconds)
        rows = self.session.scalars(
            select(Worker)
            .where(Worker.status != WorkerStatus.OFFLINE.value)
            .order_by(Worker.hostname, Worker.started_at)
        )
        return [
            {
                "id": str(w.id),
                "hostname": w.hostname,
                "pools": list(w.pools),
                "status": w.status if w.heartbeat_at >= alive_after else "silent",
                "busy": w.current_run_id is not None,
                "current_run_id": str(w.current_run_id) if w.current_run_id else None,
                "heartbeat_at": w.heartbeat_at,
            }
            for w in rows
        ]

    def systems(self, filters: DashboardFilters) -> list[dict[str, Any]]:
        stmt = select(SapSystem).where(SapSystem.deleted_at.is_(None))
        if filters.environment:
            stmt = stmt.where(SapSystem.environment == filters.environment)
        if filters.sap_system_id:
            stmt = stmt.where(SapSystem.id == filters.sap_system_id)
        live = dict(
            self.session.execute(
                select(Source.sap_system_id, func.count())
                .select_from(Run)
                .join(Pipeline, Pipeline.id == Run.pipeline_id)
                .join(Source, Source.id == Pipeline.source_id)
                .where(Run.status == RunStatus.RUNNING.value)
                .group_by(Source.sap_system_id)
            ).all()
        )
        return [
            {
                "id": str(s.id),
                "code": s.code,
                "environment": s.environment,
                "status": s.status,
                "last_test_ok": s.last_test_ok,
                "last_test_at": s.last_test_at,
                "running": int(live.get(s.id, 0)),
            }
            for s in self.session.scalars(stmt.order_by(SapSystem.code))
        ]

    # ================================================================ línea de tiempo
    def timeline(self, filters: DashboardFilters, hours: int) -> list[dict[str, Any]]:
        span = max(1, min(hours, MAX_TIMELINE_HOURS))
        since = self.now - timedelta(hours=span)
        ids = self._pipeline_ids(filters).scalar_subquery()
        rows = self.session.execute(
            select(Run, Pipeline.code)
            .join(Pipeline, Pipeline.id == Run.pipeline_id)
            .where(
                Run.pipeline_id.in_(ids),
                Run.started_at.is_not(None),
                (Run.finished_at >= since) | (Run.status == RunStatus.RUNNING.value),
            )
            .order_by(Pipeline.code, Run.started_at)
            .limit(2000)
        ).all()
        return [
            {
                "id": str(run.id),
                "pipeline_id": str(run.pipeline_id),
                "pipeline_code": code,
                "mode": run.mode,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run, code in rows
        ]

    # ================================================================ frescura
    def freshness(self, filters: DashboardFilters) -> list[dict[str, Any]]:
        ids = self._pipeline_ids(filters).scalar_subquery()
        last_ok = (
            select(Run.pipeline_id, func.max(Run.finished_at).label("last_ok"))
            .where(Run.status == RunStatus.SUCCEEDED.value)
            .group_by(Run.pipeline_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Pipeline, last_ok.c.last_ok)
            .outerjoin(last_ok, last_ok.c.pipeline_id == Pipeline.id)
            .where(Pipeline.id.in_(ids), Pipeline.status != PipelineStatus.DRAFT.value)
            .order_by(last_ok.c.last_ok.asc().nulls_first(), Pipeline.code)
        ).all()
        slas = self._slas()
        result = []
        for pipeline, last in rows:
            sla = _sla_for(pipeline, slas)
            age = (self.now - last).total_seconds() / 60 if last else None
            if sla is None or pipeline.status != PipelineStatus.ACTIVE.value:
                state = "unknown" if last is None else "no_sla"
            elif age is None or age > sla:
                state = "late"
            elif age > sla * 0.8:
                state = "warning"
            else:
                state = "ok"
            result.append(
                {
                    "pipeline_id": str(pipeline.id),
                    "pipeline_code": pipeline.code,
                    "status": pipeline.status,
                    "last_success_at": last,
                    "minutes_since": round(age) if age is not None else None,
                    "sla_minutes": sla,
                    "state": state,
                }
            )
        return result

    def _slas(self) -> list[AlertRule]:
        return list(
            self.session.scalars(
                select(AlertRule).where(
                    AlertRule.enabled, AlertRule.type == AlertType.FRESHNESS.value
                )
            )
        )

    # ================================================================ métricas
    def metrics(self) -> str:
        """Texto en formato de exposición de Prometheus (sin librería: son gauges de la BD)."""
        day = self.now - timedelta(hours=24)
        lines: list[str] = []

        def gauge(name: str, help_: str, samples: list[tuple[dict[str, str], float]]) -> None:
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} gauge")
            for labels, value in samples or [({}, 0)]:
                label = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
                lines.append(f"{name}{{{label}}} {value:g}" if label else f"{name} {value:g}")

        runs = self.session.execute(
            select(Run.status, func.count())
            .where((Run.finished_at >= day) | Run.status.in_(LIVE))
            .group_by(Run.status)
        ).all()
        gauge(
            "sx_runs",
            "Runs vivos y terminados en las últimas 24 h, por estado",
            [({"status": s}, n) for s, n in runs],
        )
        pending = self.session.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.status == RunStatus.QUEUED.value, Run.available_at <= self.now)
        )
        gauge("sx_queue_pending", "Runs en cola listos para tomarse", [({}, pending or 0)])
        duration = self.session.execute(
            select(
                func.avg(func.extract("epoch", Run.finished_at - Run.started_at)),
                func.coalesce(func.sum(Run.rows_extracted), 0),
            ).where(
                Run.status == RunStatus.SUCCEEDED.value,
                Run.finished_at >= day,
                Run.started_at.is_not(None),
            )
        ).one()
        gauge(
            "sx_run_duration_seconds_avg",
            "Duración media de los runs correctos (24 h)",
            [({}, float(duration[0] or 0))],
        )
        gauge(
            "sx_rows_extracted",
            "Filas leídas de SAP por runs correctos (24 h)",
            [({}, float(duration[1] or 0))],
        )
        alive_after = self.now - timedelta(seconds=self.settings.run_stale_seconds)
        workers = self.session.execute(
            select(
                func.count().filter(
                    and_(
                        Worker.status == WorkerStatus.ONLINE.value,
                        Worker.heartbeat_at >= alive_after,
                    )
                ),
                func.count().filter(Worker.current_run_id.is_not(None)),
            )
        ).one()
        gauge("sx_workers_alive", "Workers con latido reciente", [({}, workers[0] or 0)])
        gauge("sx_workers_busy", "Workers ejecutando un run", [({}, workers[1] or 0)])
        alerts = self.session.execute(
            select(Alert.severity, func.count())
            .where(Alert.status == AlertStatus.OPEN.value)
            .group_by(Alert.severity)
        ).all()
        gauge(
            "sx_alerts_open",
            "Alertas abiertas por gravedad",
            [({"severity": s}, n) for s, n in alerts],
        )
        broken = self.session.scalar(
            select(func.count()).select_from(DeltaState).where(DeltaState.status == "BROKEN")
        )
        gauge("sx_pipelines_broken", "Pipelines con el delta roto", [({}, broken or 0)])
        return "\n".join(lines) + "\n"


def _sla_for(pipeline: Pipeline, rules: list[AlertRule]) -> int | None:
    """SLA de frescura del pipeline: la regla más concreta (pipeline > etiqueta > global)."""
    best: tuple[int, int] | None = None
    for rule in rules:
        scope = RuleScope(rule.scope)
        if scope is RuleScope.PIPELINE and rule.scope_value in (str(pipeline.id), pipeline.code):
            rank = 0
        elif scope is RuleScope.TAG and rule.scope_value in pipeline.tags:
            rank = 1
        elif scope is RuleScope.GLOBAL:
            rank = 2
        else:
            continue
        minutes = int(rule.params.get("minutes", 240))
        if best is None or rank < best[0]:
            best = (rank, minutes)
    return best[1] if best else None
