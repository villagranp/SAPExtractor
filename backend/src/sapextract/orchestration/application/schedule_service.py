"""Programación de un pipeline: lectura, sustitución y vista previa (plan 08 §6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.system_settings import SystemSetting
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.domain.model import PipelineStatus, issue
from sapextract.orchestration.domain.schedule import (
    BlackoutWindow,
    MisfirePolicy,
    ScheduleSpec,
    ScheduleType,
    check_dependency,
    check_schedule,
    check_schedule_set,
    next_fire,
    upcoming,
)
from sapextract.orchestration.infrastructure.models import Pipeline, Schedule

TIMEZONE_SETTING = "platform.timezone"


@dataclass(frozen=True, kw_only=True)
class ScheduleInput:
    """Programación tal como llega de la API; ``timezone=None`` usa la de la plataforma."""

    type: ScheduleType
    timezone: str | None = None
    cron_expression: str | None = None
    interval_minutes: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    blackout_windows: tuple[BlackoutWindow, ...] = ()
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP
    enabled: bool = True
    upstream_pipeline_id: uuid.UUID | None = None


def spec_of(schedule: Schedule) -> ScheduleSpec:
    return ScheduleSpec(
        type=ScheduleType(schedule.type),
        timezone=schedule.timezone,
        cron_expression=schedule.cron_expression,
        interval_minutes=schedule.interval_minutes,
        start_at=schedule.start_at,
        end_at=schedule.end_at,
        blackout_windows=tuple(BlackoutWindow.from_dict(w) for w in schedule.blackout_windows),
        misfire_policy=MisfirePolicy(schedule.misfire_policy),
        upstream_pipeline_id=schedule.upstream_pipeline_id,
    )


def platform_timezone(session: Session) -> str:
    setting = session.get(SystemSetting, TIMEZONE_SETTING)
    return str(setting.value) if setting is not None and setting.value else "UTC"


def reset_next_fire(session: Session, pipeline: Pipeline, now: datetime) -> None:
    """Recalcula el próximo disparo desde ahora (al activar, reanudar o cambiar la programación).

    Así una pausa larga no deja disparos atrasados que se lanzarían de golpe al reanudar. Lo
    mismo vale para una dependencia: solo cuentan las ejecuciones del pipeline anterior que
    terminen a partir de ahora.
    """
    active = pipeline.status == PipelineStatus.ACTIVE.value
    for schedule in session.scalars(select(Schedule).where(Schedule.pipeline_id == pipeline.id)):
        live = active and schedule.enabled
        schedule.next_fire_at = (
            next_fire(spec_of(schedule), now, anchor=schedule.created_at) if live else None
        )
        dependent = schedule.type == ScheduleType.DEPENDENCY.value
        schedule.upstream_cursor_at = now if live and dependent else None


def upstream_ids(session: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Pipeline anterior de cada pipeline que depende de otro."""
    stmt = (
        select(Schedule.pipeline_id, Schedule.upstream_pipeline_id)
        .join(Pipeline, Pipeline.id == Schedule.pipeline_id)
        .where(
            Schedule.type == ScheduleType.DEPENDENCY.value,
            Schedule.upstream_pipeline_id.is_not(None),
            Pipeline.deleted_at.is_(None),
        )
    )
    return {child: parent for child, parent in session.execute(stmt).all() if parent is not None}


def upstream_pipelines(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, Pipeline]:
    """Pipeline anterior de cada uno de ``ids`` (los que dependen de otro)."""
    if not ids:
        return {}
    stmt = (
        select(Schedule.pipeline_id, Pipeline)
        .join(Pipeline, Pipeline.id == Schedule.upstream_pipeline_id)
        .where(Schedule.pipeline_id.in_(ids), Schedule.type == ScheduleType.DEPENDENCY.value)
    )
    return {row[0]: row[1] for row in session.execute(stmt).all()}


def downstream_of(session: Session, pipeline_id: uuid.UUID) -> list[Pipeline]:
    """Pipelines que se ejecutan después de este."""
    stmt = (
        select(Pipeline)
        .join(Schedule, Schedule.pipeline_id == Pipeline.id)
        .where(
            Schedule.type == ScheduleType.DEPENDENCY.value,
            Schedule.upstream_pipeline_id == pipeline_id,
            Pipeline.deleted_at.is_(None),
        )
        .order_by(Pipeline.code)
    )
    return list(session.scalars(stmt))


def _snapshot(schedule: Schedule) -> dict[str, Any]:
    return {
        "type": schedule.type,
        "cron_expression": schedule.cron_expression,
        "interval_minutes": schedule.interval_minutes,
        "timezone": schedule.timezone,
        "start_at": schedule.start_at.isoformat() if schedule.start_at else None,
        "end_at": schedule.end_at.isoformat() if schedule.end_at else None,
        "blackout_windows": schedule.blackout_windows,
        "misfire_policy": schedule.misfire_policy,
        "enabled": schedule.enabled,
        "upstream_pipeline_id": str(schedule.upstream_pipeline_id)
        if schedule.upstream_pipeline_id
        else None,
    }


class ScheduleService:
    def __init__(self, session: Session, toolkit: OrchestrationToolkit) -> None:
        self.session = session
        self.tk = toolkit

    def _spec(self, data: ScheduleInput) -> ScheduleSpec:
        return check_schedule(
            ScheduleSpec(
                type=data.type,
                timezone=data.timezone or platform_timezone(self.session),
                cron_expression=data.cron_expression,
                interval_minutes=data.interval_minutes,
                start_at=data.start_at,
                end_at=data.end_at,
                blackout_windows=data.blackout_windows,
                misfire_policy=data.misfire_policy,
                upstream_pipeline_id=data.upstream_pipeline_id,
            )
        )

    def _check_upstream(self, pipeline: Pipeline, spec: ScheduleSpec) -> None:
        """El pipeline anterior existe y la dependencia no cierra un ciclo."""
        upstream_id = spec.upstream_pipeline_id
        if upstream_id is None:
            return
        upstream = self.session.get(Pipeline, upstream_id)
        if upstream is None or upstream.deleted_at is not None:
            msg = "El pipeline tras el que se ejecuta no existe."
            raise issue("upstream_pipeline_id", msg, "SX-SCHED-UPSTREAM-NOT-FOUND")
        parents = upstream_ids(self.session)
        parents.pop(pipeline.id, None)  # su dependencia actual se sustituye
        check_dependency(pipeline.id, upstream_id, parents.get)

    def upstreams(self, schedules: list[Schedule]) -> dict[uuid.UUID, Pipeline]:
        """Pipelines anteriores de unas programaciones, por su identificador."""
        ids = {s.upstream_pipeline_id for s in schedules if s.upstream_pipeline_id is not None}
        if not ids:
            return {}
        return {p.id: p for p in self.session.scalars(select(Pipeline).where(Pipeline.id.in_(ids)))}

    def list_for(self, pipeline_id: uuid.UUID) -> list[Schedule]:
        stmt = (
            select(Schedule).where(Schedule.pipeline_id == pipeline_id).order_by(Schedule.id.asc())
        )
        return list(self.session.scalars(stmt))

    def preview(self, data: ScheduleInput, *, count: int) -> tuple[ScheduleSpec, list[datetime]]:
        """Próximas ejecuciones de una programación sin guardarla (asistente)."""
        spec = self._spec(data)
        now = self.tk.clock()
        return spec, upcoming(spec, now, anchor=now, count=count)

    def replace(
        self,
        pipeline: Pipeline,
        items: list[ScheduleInput],
        actor: Principal,
        request: RequestInfo,
    ) -> list[Schedule]:
        """Sustituye toda la programación del pipeline y lo deja auditado."""
        specs = [(self._spec(item), item.enabled) for item in items]
        check_schedule_set([spec for spec, _ in specs])
        for spec, _ in specs:
            self._check_upstream(pipeline, spec)
        now = self.tk.clock()
        before = [_snapshot(s) for s in self.list_for(pipeline.id)]
        self.session.execute(delete(Schedule).where(Schedule.pipeline_id == pipeline.id))
        created: list[Schedule] = []
        for spec, enabled in specs:
            schedule = Schedule(
                pipeline_id=pipeline.id,
                type=spec.type.value,
                cron_expression=spec.cron_expression,
                interval_minutes=spec.interval_minutes,
                timezone=spec.timezone,
                start_at=spec.start_at,
                end_at=spec.end_at,
                blackout_windows=[w.to_dict() for w in spec.blackout_windows],
                enabled=enabled,
                misfire_policy=spec.misfire_policy.value,
                upstream_pipeline_id=spec.upstream_pipeline_id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(schedule)
            created.append(schedule)
        self.session.flush()
        reset_next_fire(self.session, pipeline, now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="schedule.updated",
                resource_type="pipeline",
                resource_id=str(pipeline.id),
                resource_name=pipeline.code,
                changes={"schedules": {"before": before, "after": [_snapshot(s) for s in created]}},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return created
