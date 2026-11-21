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
    MAX_SCHEDULES,
    BlackoutWindow,
    MisfirePolicy,
    ScheduleSpec,
    ScheduleType,
    check_schedule,
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
    )


def platform_timezone(session: Session) -> str:
    setting = session.get(SystemSetting, TIMEZONE_SETTING)
    return str(setting.value) if setting is not None and setting.value else "UTC"


def reset_next_fire(session: Session, pipeline: Pipeline, now: datetime) -> None:
    """Recalcula el próximo disparo desde ahora (al activar, reanudar o cambiar la programación).

    Así una pausa larga no deja disparos atrasados que se lanzarían de golpe al reanudar.
    """
    active = pipeline.status == PipelineStatus.ACTIVE.value
    for schedule in session.scalars(select(Schedule).where(Schedule.pipeline_id == pipeline.id)):
        schedule.next_fire_at = (
            next_fire(spec_of(schedule), now, anchor=schedule.created_at)
            if active and schedule.enabled
            else None
        )


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
            )
        )

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
        if len(items) > MAX_SCHEDULES:
            msg = f"Un pipeline admite como máximo {MAX_SCHEDULES} programaciones."
            raise issue("schedules", msg, "SX-SCHED-TOO-MANY")
        specs = [(self._spec(item), item.enabled) for item in items]
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
