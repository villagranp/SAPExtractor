"""Casos de uso sobre runs: ejecutar ahora, consultar, cancelar y reintentar (plan 08 §4 y §5).

También los grupos de concurrencia y la lista de workers, que solo existen para la cola.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.exceptions import ConflictError, NotFoundError
from sapextract.core.pagination import PageParams
from sapextract.dataflow.domain.batch import RunMode
from sapextract.identity.domain.model import AuthMethod, Principal
from sapextract.orchestration.application.lifecycle import (
    consumed_attempts,
    enqueue_run,
    live_run,
)
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.domain.model import (
    MANUAL_PRIORITY,
    ExtractionMode,
    PipelineStatus,
    RunStatus,
    RunTrigger,
    WorkerStatus,
    check_group_limit,
    check_manual_mode,
    normalize_group_code,
)
from sapextract.orchestration.infrastructure.models import (
    ConcurrencyGroup,
    Pipeline,
    Run,
    RunAttempt,
    Worker,
)
from sapextract.orchestration.infrastructure.queue import notify_queued
from sapextract.sources.domain.model import DeltaSemantics
from sapextract.sources.infrastructure.models import Source

SORTABLE = {"created_at", "started_at", "finished_at", "status", "priority"}


@dataclass(frozen=True, kw_only=True)
class RunFilters:
    pipeline_id: uuid.UUID | None = None
    status: RunStatus | None = None
    trigger: RunTrigger | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class RunView:
    run: Run
    pipeline: Pipeline
    attempts: list[RunAttempt]


@dataclass(frozen=True, kw_only=True)
class GroupView:
    group: ConcurrencyGroup
    pipelines: int
    running: int


class RunService:
    def __init__(self, session: Session, toolkit: OrchestrationToolkit) -> None:
        self.session = session
        self.tk = toolkit

    # -------------------------------------------------------------- consultas
    def search(self, filters: RunFilters, page: PageParams) -> tuple[list[RunView], int]:
        stmt = select(Run, Pipeline).join(Pipeline, Pipeline.id == Run.pipeline_id)
        if filters.pipeline_id:
            stmt = stmt.where(Run.pipeline_id == filters.pipeline_id)
        if filters.status:
            stmt = stmt.where(Run.status == filters.status.value)
        if filters.trigger:
            stmt = stmt.where(Run.trigger == filters.trigger.value)
        if filters.created_from:
            stmt = stmt.where(Run.created_at >= filters.created_from)
        if filters.created_to:
            stmt = stmt.where(Run.created_at < filters.created_to)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "-created_at"
        name = sort.lstrip("-")
        column = getattr(Run, name) if name in SORTABLE else Run.created_at
        order = column.desc().nulls_last() if sort.startswith("-") else column.asc().nulls_last()
        rows = self.session.execute(
            stmt.order_by(order, Run.id.desc()).offset(page.offset).limit(page.page_size)
        ).all()
        return [RunView(run=r, pipeline=p, attempts=[]) for r, p in rows], total

    def get(self, run_id: uuid.UUID) -> Run:
        run = self.session.get(Run, run_id)
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        return run

    def view(self, run_id: uuid.UUID) -> RunView:
        run = self.get(run_id)
        pipeline = self.session.get(Pipeline, run.pipeline_id)
        if pipeline is None:  # la FK lo impide
            msg = "El pipeline de la ejecución no existe."
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        attempts = list(
            self.session.scalars(
                select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.number)
            )
        )
        return RunView(run=run, pipeline=pipeline, attempts=attempts)

    # -------------------------------------------------------------- ejecutar ahora
    def trigger(
        self,
        pipeline_id: uuid.UUID,
        mode: RunMode | None,
        reason: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> RunView:
        """Run manual con prioridad alta. ``FULL`` sobre un pipeline incremental es una recarga."""
        pipeline = self.session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        if pipeline.status != PipelineStatus.ACTIVE.value:
            msg = "Solo se ejecutan pipelines activos: actívelo o reanúdelo antes."
            raise ConflictError(msg, code="SX-PIPE-NOT-ACTIVE")
        source = self.session.get(Source, pipeline.source_id)
        semantics = (
            DeltaSemantics(source.delta_semantics) if source and source.delta_semantics else None
        )
        run_mode = check_manual_mode(
            mode,
            ExtractionMode(pipeline.extraction_mode),
            delta_semantics=semantics,
            can_manage=actor.has("pipeline:manage"),
        )
        if self.tk.hooks is not None:  # el motor decide si el estado de delta lo permite
            self.tk.hooks.check_run(self.session, pipeline, run_mode)
        api = actor.auth_method is AuthMethod.API_TOKEN
        run = enqueue_run(
            self.session,
            pipeline,
            mode=run_mode,
            trigger=RunTrigger.API if api else RunTrigger.MANUAL,
            triggered_by=actor.username,
            priority=min(pipeline.priority, MANUAL_PRIORITY),
            now=self.tk.clock(),
            reason=(reason or "").strip() or None,
        )
        if run is None:
            msg = "El pipeline ya tiene una ejecución en cola o en curso."
            raise ConflictError(msg, code="SX-RUN-ALREADY-LIVE")
        self._audit(
            "run.triggered_manual",
            run,
            pipeline,
            actor,
            request,
            reason=run.reason,
            details={"mode": run.mode, "trigger": run.trigger},
        )
        self.session.commit()
        return self.view(run.id)

    # -------------------------------------------------------------- cancelar y reintentar
    def cancel(
        self, run_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> RunView:
        """Cancelación cooperativa: un run en curso para entre páginas; el token no avanza."""
        run = self.session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        pipeline = self.session.get(Pipeline, run.pipeline_id)
        now = self.tk.clock()
        if run.status == RunStatus.QUEUED.value:
            run.status, run.finished_at = RunStatus.CANCELLED.value, now
            if self.tk.hooks is not None:
                self.tk.hooks.run_finished(self.session, run)
        elif run.status != RunStatus.RUNNING.value:
            msg = "La ejecución ya terminó."
            raise ConflictError(msg, code="SX-RUN-FINISHED")
        if run.cancel_requested_at is None:
            run.cancel_requested_at, run.cancel_requested_by = now, actor.username
            self._audit(
                "run.cancelled",
                run,
                pipeline,
                actor,
                request,
                reason=(reason or "").strip() or None,
                details={"was_running": run.status == RunStatus.RUNNING.value},
            )
        self.session.commit()
        return self.view(run.id)

    def retry(self, run_id: uuid.UUID, actor: Principal, request: RequestInfo) -> RunView:
        """Añade un intento a un run fallido: el mismo run y el mismo lote, no uno nuevo."""
        run = self.session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        if run.status != RunStatus.FAILED.value:
            msg = "Solo se reintenta una ejecución fallida."
            raise ConflictError(msg, code="SX-RUN-NOT-FAILED")
        pipeline = self.session.get(Pipeline, run.pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline de esta ejecución ya no existe."
            raise ConflictError(msg, code="SX-PIPE-NOT-FOUND")
        if pipeline.status != PipelineStatus.ACTIVE.value:
            # Igual que el run manual: reintentar no puede saltarse una pausa.
            msg = "Solo se reintenta en un pipeline activo: reanúdelo antes."
            raise ConflictError(msg, code="SX-PIPE-NOT-ACTIVE")
        if live_run(self.session, run.pipeline_id) is not None:
            msg = "El pipeline ya tiene una ejecución en cola o en curso."
            raise ConflictError(msg, code="SX-RUN-ALREADY-LIVE")
        latest = self.session.scalar(
            select(Run.id)
            .where(Run.pipeline_id == run.pipeline_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        if latest != run.id:
            # Repetir un lote antiguo después de otros posteriores rompería el orden del delta.
            msg = "Solo se reintenta la última ejecución del pipeline."
            raise ConflictError(msg, code="SX-RUN-NOT-LATEST")
        now = self.tk.clock()
        run.status = RunStatus.QUEUED.value
        run.available_at, run.finished_at = now, None
        run.attempts_budget = consumed_attempts(self.session, run.id) + 1
        run.cancel_requested_at = run.cancel_requested_by = None
        previous = run.error_code
        run.error_code = run.error_kind = run.error_message = None
        self.session.flush()
        notify_queued(self.session)
        self._audit("run.retried", run, pipeline, actor, request, details={"error": previous})
        self.session.commit()
        return self.view(run.id)

    def _audit(
        self,
        action: str,
        run: Run,
        pipeline: Pipeline | None,
        actor: Principal,
        request: RequestInfo,
        *,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="run",
                resource_id=str(run.id),
                resource_name=pipeline.code if pipeline else None,
                reason=reason,
                details={"pipeline_id": str(run.pipeline_id), **(details or {})},
            ),
            actor=actor.actor,
            request=request,
        )

    # -------------------------------------------------------------- workers
    def workers(self) -> list[Worker]:
        stmt = select(Worker).order_by(Worker.status != WorkerStatus.ONLINE.value, Worker.hostname)
        return list(self.session.scalars(stmt))

    # -------------------------------------------------------------- grupos de concurrencia
    def groups(self) -> list[GroupView]:
        pipelines = dict(
            self.session.execute(
                select(Pipeline.concurrency_group, func.count())
                .where(Pipeline.deleted_at.is_(None), Pipeline.concurrency_group.is_not(None))
                .group_by(Pipeline.concurrency_group)
            ).all()
        )
        running = dict(
            self.session.execute(
                select(Pipeline.concurrency_group, func.count())
                .join(Run, Run.pipeline_id == Pipeline.id)
                .where(Run.status == RunStatus.RUNNING.value)
                .group_by(Pipeline.concurrency_group)
            ).all()
        )
        groups = self.session.scalars(select(ConcurrencyGroup).order_by(ConcurrencyGroup.code))
        return [
            GroupView(group=g, pipelines=pipelines.get(g.code, 0), running=running.get(g.code, 0))
            for g in groups
        ]

    def put_group(
        self,
        code: str,
        max_parallel: int,
        description: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> ConcurrencyGroup:
        code = normalize_group_code(code)
        check_group_limit(max_parallel)
        group = self.session.get(ConcurrencyGroup, code)
        before = (
            {"max_parallel": group.max_parallel, "description": group.description}
            if group
            else None
        )
        if group is None:
            group = ConcurrencyGroup(code=code, max_parallel=max_parallel)
            self.session.add(group)
        group.max_parallel = max_parallel
        group.description = (description or "").strip() or None
        group.updated_by = actor.username
        after = {"max_parallel": group.max_parallel, "description": group.description}
        if before != after:
            self.tk.audit.record(
                self.session,
                AuditEntry(
                    action="concurrency_group.updated",
                    resource_type="concurrency_group",
                    resource_id=code,
                    resource_name=code,
                    changes={"group": {"before": before, "after": after}},
                ),
                actor=actor.actor,
                request=request,
            )
        self.session.commit()
        return group

    def delete_group(self, code: str, actor: Principal, request: RequestInfo) -> None:
        group = self.session.get(ConcurrencyGroup, code.strip().upper())
        if group is None:
            msg = "El grupo de concurrencia no existe."
            raise NotFoundError(msg, code="SX-PIPE-GROUP-NOT-FOUND")
        used = self.session.scalars(
            select(Pipeline.code)
            .where(Pipeline.concurrency_group == group.code, Pipeline.deleted_at.is_(None))
            .order_by(Pipeline.code)
            .limit(5)
        ).all()
        if used:
            msg = f"No se puede eliminar: lo usan pipelines ({', '.join(used)})."
            raise ConflictError(msg, code="SX-PIPE-GROUP-IN-USE")
        # Los pipelines eliminados conservan el código por la FK: se les quita antes.
        for pipeline in self.session.scalars(
            select(Pipeline).where(Pipeline.concurrency_group == group.code)
        ):
            pipeline.concurrency_group = None
        self.session.flush()
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="concurrency_group.deleted",
                resource_type="concurrency_group",
                resource_id=group.code,
                resource_name=group.code,
                changes={"group": {"before": {"max_parallel": group.max_parallel}, "after": None}},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.delete(group)
        self.session.commit()
