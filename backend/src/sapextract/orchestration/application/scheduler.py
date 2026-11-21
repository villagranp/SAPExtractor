"""Trabajo del scheduler en cada ciclo (plan 08 §2): disparar, recuperar huérfanos y limpiar.

Solo lo ejecuta el líder, pero cada paso es seguro aunque dos procesos coincidan: las filas
se toman con ``FOR UPDATE SKIP LOCKED`` y el próximo disparo avanza en la misma transacción
que encola el run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from sapextract.core.db import Database
from sapextract.orchestration.application.lifecycle import enqueue_run, reap_stale
from sapextract.orchestration.application.schedule_service import spec_of
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.domain.model import (
    ExtractionMode,
    PipelineStatus,
    RunTrigger,
    WorkerStatus,
    run_mode_for,
)
from sapextract.orchestration.domain.schedule import MisfirePolicy, SkipReason, next_fire
from sapextract.orchestration.infrastructure.models import Pipeline, Schedule, Worker

log = structlog.get_logger(__name__)

BATCH = 200
SCHEDULER_ACTOR = "scheduler"
WORKER_RETENTION = timedelta(days=7)


@dataclass(frozen=True, kw_only=True)
class TickResult:
    fired: int = 0
    skipped: int = 0
    reaped: int = 0
    workers_offline: int = 0


class SchedulerService:
    def __init__(self, db: Database, toolkit: OrchestrationToolkit) -> None:
        self.db = db
        self.tk = toolkit

    def tick(self) -> TickResult:
        fired, skipped = self.fire_due()
        return TickResult(
            fired=fired, skipped=skipped, reaped=self.reap(), workers_offline=self.sweep_workers()
        )

    # -------------------------------------------------------------- programación
    def fire_due(self) -> tuple[int, int]:
        """Encola los runs de las programaciones vencidas. Devuelve (lanzadas, omitidas)."""
        now = self.tk.clock()
        fired = skipped = 0
        with self.db.session() as session:
            stmt = (
                select(Schedule, Pipeline)
                .join(Pipeline, Pipeline.id == Schedule.pipeline_id)
                .where(
                    Schedule.enabled,
                    Schedule.next_fire_at <= now,
                    Pipeline.status == PipelineStatus.ACTIVE.value,
                    Pipeline.deleted_at.is_(None),
                )
                .order_by(Schedule.next_fire_at)
                .limit(BATCH)
                .with_for_update(of=Schedule, skip_locked=True)
            )
            for schedule, pipeline in session.execute(stmt).all():
                reason = self._fire(session, schedule, pipeline, now)
                if reason is None:
                    fired += 1
                    schedule.last_fire_at = now
                else:
                    skipped += 1
                    schedule.last_skip_at, schedule.last_skip_reason = now, reason.value
                    log.info(
                        "schedule_skipped",
                        pipeline=pipeline.code,
                        schedule_id=str(schedule.id),
                        reason=reason.value,
                    )
                schedule.next_fire_at = next_fire(
                    spec_of(schedule), now, anchor=schedule.created_at
                )
            session.commit()
        return fired, skipped

    def _fire(
        self, session: Session, schedule: Schedule, pipeline: Pipeline, now: datetime
    ) -> SkipReason | None:
        spec = spec_of(schedule)
        due = schedule.next_fire_at or now
        if spec.blackout_at(due) or spec.blackout_at(now):
            return SkipReason.BLACKOUT
        if now - due > self.tk.misfire_grace and spec.misfire_policy is MisfirePolicy.SKIP:
            return SkipReason.MISFIRE
        run = enqueue_run(
            session,
            pipeline,
            mode=run_mode_for(ExtractionMode(pipeline.extraction_mode)),
            trigger=RunTrigger.SCHEDULE,
            triggered_by=SCHEDULER_ACTOR,
            priority=pipeline.priority,
            now=now,
            schedule_id=schedule.id,
        )
        return SkipReason.ALREADY_LIVE if run is None else None

    # -------------------------------------------------------------- huérfanos
    def reap(self) -> int:
        """Intentos sin latido: se cierran como ``stale`` y el run vuelve a la cola."""
        now = self.tk.clock()
        with self.db.session() as session:
            reaped = reap_stale(
                session, now=now, cutoff=now - self.tk.stale_after, random=self.tk.random()
            )
            session.commit()
        if reaped:
            log.warning("stale_runs_recovered", count=reaped)
        return reaped

    def sweep_workers(self) -> int:
        """Marca como caídos los workers sin latido y olvida los que llevan días así."""
        now = self.tk.clock()
        with self.db.session() as session:
            gone = session.scalars(
                update(Worker)
                .where(
                    Worker.status != WorkerStatus.OFFLINE.value,
                    Worker.heartbeat_at < now - self.tk.stale_after,
                )
                .values(status=WorkerStatus.OFFLINE.value, current_run_id=None)
                .returning(Worker.id)
            ).all()
            session.execute(
                delete(Worker).where(
                    Worker.status == WorkerStatus.OFFLINE.value,
                    Worker.heartbeat_at < now - WORKER_RETENTION,
                )
            )
            session.commit()
        return len(gone)
