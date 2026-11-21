"""Casos de uso de ejecución: ejecutar ahora, cancelar, estado de delta y su reinicialización.

Plan 06 §3.3 y §3.6, plan 08 §5. Lo consume la API; el worker usa ``RunStore`` y el motor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import Actor, AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from sapextract.core.pagination import PageParams
from sapextract.dataflow.domain.batch import RunMode
from sapextract.destinations.infrastructure.models import Destination
from sapextract.extraction.application.resolve import ExtractionToolkit, pipeline_filter
from sapextract.extraction.application.store import release_delta
from sapextract.extraction.domain.model import (
    ACTIVE_RUN_STATUSES,
    DeltaEvent,
    DeltaSnapshot,
    DeltaStatus,
    LogLevel,
    RunStatus,
    RunTrigger,
    run_mode_for,
)
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    Run,
    RunAttempt,
    RunLog,
    RunStep,
)
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.application.pipeline_service import DeleteGuard
from sapextract.orchestration.domain.model import ExtractionMode
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sap_connections.infrastructure.models import SapSystem
from sapextract.sources.infrastructure.models import Source

MANUAL_PRIORITY = 1  # «ejecutar ahora» va por delante de lo programado (plan 08 §5)
MAX_LOG_LINES = 500


class SubscriptionAction(StrEnum):
    CLOSED = "closed"  # el usuario la cerró en ODQMON
    LEFT_OPEN = "left_open"  # la deja abierta a sabiendas


@dataclass(frozen=True, kw_only=True)
class RunFilters:
    pipeline_id: uuid.UUID | None = None
    status: RunStatus | None = None


@dataclass(frozen=True, kw_only=True)
class RunDetail:
    run: Run
    pipeline: Pipeline
    attempts: list[RunAttempt]
    steps: list[RunStep]


class RunService:
    def __init__(self, session: Session, toolkit: ExtractionToolkit) -> None:
        self.session = session
        self.tk = toolkit

    # ================================================================ consultas
    def search(
        self, filters: RunFilters, page: PageParams
    ) -> tuple[list[tuple[Run, Pipeline]], int]:
        stmt = select(Run, Pipeline).join(Pipeline, Pipeline.id == Run.pipeline_id)
        if filters.pipeline_id:
            stmt = stmt.where(Run.pipeline_id == filters.pipeline_id)
        if filters.status:
            stmt = stmt.where(Run.status == filters.status.value)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.session.execute(
            stmt.order_by(Run.created_at.desc()).offset(page.offset).limit(page.page_size)
        ).all()
        return [(r, p) for r, p in rows], total

    def get(self, run_id: uuid.UUID) -> Run:
        run = self.session.get(Run, run_id)
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        return run

    def detail(self, run_id: uuid.UUID) -> RunDetail:
        run = self.get(run_id)
        pipeline = self.session.get(Pipeline, run.pipeline_id)
        if pipeline is None:  # pragma: no cover - FK
            msg = "El pipeline de la ejecución no existe."
            raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
        attempts = list(
            self.session.scalars(
                select(RunAttempt).where(RunAttempt.run_id == run_id).order_by(RunAttempt.number)
            )
        )
        steps = list(
            self.session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run_id)
                .order_by(RunStep.attempt, RunStep.started_at)
            )
        )
        return RunDetail(run=run, pipeline=pipeline, attempts=attempts, steps=steps)

    def logs(self, run_id: uuid.UUID, *, after: int | None, limit: int) -> list[RunLog]:
        """Líneas desde el cursor ``after`` (sondeo en vivo, plan 03 §2)."""
        self.get(run_id)
        stmt = select(RunLog).where(RunLog.run_id == run_id)
        if after is not None:
            stmt = stmt.where(RunLog.id > after)
        return list(self.session.scalars(stmt.order_by(RunLog.id).limit(min(limit, MAX_LOG_LINES))))

    def delta_state(self, pipeline_id: uuid.UUID) -> DeltaState | None:
        return self.session.get(DeltaState, pipeline_id)

    def delta_history(self, pipeline_id: uuid.UUID, limit: int = 50) -> list[DeltaStateHistory]:
        return list(
            self.session.scalars(
                select(DeltaStateHistory)
                .where(DeltaStateHistory.pipeline_id == pipeline_id)
                .order_by(DeltaStateHistory.id.desc())
                .limit(limit)
            )
        )

    def active_run(self, pipeline_id: uuid.UUID) -> Run | None:
        return self.session.scalars(
            select(Run).where(
                Run.pipeline_id == pipeline_id,
                Run.status.in_([s.value for s in ACTIVE_RUN_STATUSES]),
            )
        ).first()

    def requires_reinit(self, pipeline: Pipeline) -> bool:
        """Filtros cambiados desde la init: se calcula, no se guarda (plan 06 §3.6)."""
        state = self.session.get(DeltaState, pipeline.id)
        if state is None or state.init_filters_hash is None:
            return False
        if pipeline.extraction_mode != ExtractionMode.DELTA.value:
            return False
        _, current = pipeline_filter(self.session, pipeline, self.tk.sap)
        return current != state.init_filters_hash

    # ================================================================ ejecutar ahora
    def request_run(self, pipeline_id: uuid.UUID, actor: Principal, request: RequestInfo) -> Run:
        pipeline = self._lock(pipeline_id)
        if self.active_run(pipeline_id) is not None:
            msg = "El pipeline ya tiene una ejecución en cola o en curso."
            raise ConflictError(msg, code="SX-PPL-RUN-ACTIVE")
        self._check_ready(pipeline)
        mode = ExtractionMode(pipeline.extraction_mode)
        rendered, fhash = pipeline_filter(self.session, pipeline, self.tk.sap)
        state: DeltaState | None = None
        if mode in (ExtractionMode.DELTA, ExtractionMode.WATERMARK):
            state = self._ensure_delta_state(pipeline.id)
        snapshot = (
            DeltaSnapshot(
                status=DeltaStatus(state.status),
                sap_close_pending=state.sap_close_pending,
                init_filters_hash=state.init_filters_hash,
            )
            if state
            else None
        )
        run_mode = run_mode_for(mode, snapshot, fhash)
        run = Run(
            pipeline_id=pipeline.id,
            mode=run_mode.value,
            status=RunStatus.QUEUED.value,
            trigger=RunTrigger.MANUAL.value,
            triggered_by=actor.username,
            priority=MANUAL_PRIORITY,
            filters_hash=fhash,
            lease_epoch=0,
            available_at=utcnow(),
            attempts_used=0,
            max_attempts=pipeline.max_attempts,
            timeout_minutes=pipeline.timeout_minutes,
            cancel_requested=False,
            created_at=utcnow(),
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError:  # otra petición encoló a la vez (índice único parcial)
            self.session.rollback()
            msg = "El pipeline ya tiene una ejecución en cola o en curso."
            raise ConflictError(msg, code="SX-PPL-RUN-ACTIVE") from None
        self.session.add(
            RunLog(
                run_id=run.id,
                level=LogLevel.INFO.value,
                message=f"En cola: {_mode_label(run_mode)} pedida por {actor.username}.",
                data={"filtered": bool(rendered)},
                ts=utcnow(),
            )
        )
        self._audit(
            "pipeline.run_requested",
            pipeline,
            actor.actor,
            request,
            details={"run_id": str(run.id), "mode": run_mode.value},
        )
        self.session.commit()
        return run

    def _check_ready(self, pipeline: Pipeline) -> None:
        source = self.session.get(Source, pipeline.source_id)
        if source is None or source.deleted_at is not None:
            msg = "El origen del pipeline ya no existe."
            raise BusinessRuleError(msg, code="SX-PPL-SOURCE-MISSING")
        system = self.session.get(SapSystem, source.sap_system_id)
        if system is None or system.status != "active":
            msg = "El sistema SAP del origen no está activo: pruébelo y actívelo antes de ejecutar."
            raise BusinessRuleError(msg, code="SX-SAP-SYSTEM-INACTIVE")
        destination = self.session.get(Destination, pipeline.destination_id)
        if destination is None or destination.status != "active":
            msg = "El destino del pipeline no está activo: pruébelo y actívelo antes de ejecutar."
            raise BusinessRuleError(msg, code="SX-DST-INACTIVE")

    def _ensure_delta_state(self, pipeline_id: uuid.UUID) -> DeltaState:
        self.session.execute(
            insert(DeltaState)
            .values(
                pipeline_id=pipeline_id,
                status=DeltaStatus.NOT_INITIALIZED.value,
                generation=0,
                lease_epoch=0,
                sap_close_pending=False,
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["pipeline_id"])
        )
        state = self.session.get(DeltaState, pipeline_id, populate_existing=True)
        if state is None:  # pragma: no cover - recién insertado
            msg = "No se pudo crear el estado de delta."
            raise RuntimeError(msg)
        return state

    # ================================================================ cancelar
    def cancel(self, run_id: uuid.UUID, actor: Principal, request: RequestInfo) -> Run:
        run = self.session.scalars(select(Run).where(Run.id == run_id).with_for_update()).first()
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        pipeline = self.session.get(Pipeline, run.pipeline_id)
        if run.status not in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
            msg = "La ejecución ya terminó."
            raise ConflictError(msg, code="SX-RUN-FINISHED")
        run.cancel_requested = True
        run.cancel_requested_by = actor.username
        if run.status == RunStatus.QUEUED.value:  # nadie la está ejecutando: se cierra ya
            run.status = RunStatus.CANCELLED.value
            run.finished_at = utcnow()
            run.error_code = "SX-RUN-CANCELLED"
            release_delta(self.session, run, broken=False, actor=actor.actor, audit=self.tk.audit)
        self.session.add(
            RunLog(
                run_id=run.id,
                level=LogLevel.WARNING.value,
                message=f"Cancelación pedida por {actor.username}.",
                data={},
                ts=utcnow(),
            )
        )
        if pipeline is not None:
            self._audit(
                "run.cancel_requested",
                pipeline,
                actor.actor,
                request,
                details={"run_id": str(run.id)},
                resource=("run", str(run.id)),
            )
        self.session.commit()
        return run

    # ================================================================ estado de delta
    def reset_delta(
        self, pipeline_id: uuid.UUID, reason: str, actor: Principal, request: RequestInfo
    ) -> DeltaState:
        """Reinicializa: el próximo run será una init nueva (plan 06 §3.6, pasos 1, 2 y 4).

        El cierre de la suscripción anterior en SAP queda como tarea manual guiada hasta que el
        spike confirme ``TerminateDeltasFor…`` (H12).
        """
        text = _reason(reason)
        pipeline = self._lock(pipeline_id)
        if pipeline.extraction_mode != ExtractionMode.DELTA.value:
            msg = "Solo los pipelines delta tienen suscripción que reinicializar."
            raise BusinessRuleError(msg, code="SX-PPL-NOT-DELTA")
        if self.active_run(pipeline_id) is not None:
            msg = "Hay una ejecución en cola o en curso: cancélela antes de reinicializar."
            raise ConflictError(msg, code="SX-PPL-RUN-ACTIVE")
        state = self._ensure_delta_state(pipeline_id)
        if state.status == DeltaStatus.NOT_INITIALIZED.value:
            msg = "El delta no está inicializado: no hay nada que reinicializar."
            raise BusinessRuleError(msg, code="SX-PPL-DELTA-NOT-INITIALIZED")
        before, token = state.status, state.current_token
        state.status = DeltaStatus.NOT_INITIALIZED.value
        state.current_token = state.pending_token = None
        state.pending_run_id = None
        state.init_filters_hash = None
        state.sap_close_pending = True
        state.updated_at = utcnow()
        self._history(state, DeltaEvent.RESET, before, actor, text, token_before=token)
        self._audit(
            "pipeline.delta_reset",
            pipeline,
            actor.actor,
            request,
            details={"from_status": before, "generation": state.generation},
            reason=text,
        )
        self.session.commit()
        return state

    def resolve_subscription(
        self,
        pipeline_id: uuid.UUID,
        action: SubscriptionAction,
        reason: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> DeltaState:
        """El usuario confirma que cerró la suscripción en ODQMON o que la deja abierta."""
        pipeline = self._lock(pipeline_id)
        state = self.session.get(DeltaState, pipeline_id)
        if state is None or not state.sap_close_pending:
            msg = "No hay ninguna suscripción pendiente de cerrar en SAP."
            raise BusinessRuleError(msg, code="SX-PPL-NO-SAP-CLOSE-PENDING")
        text = _reason(reason) if action is SubscriptionAction.LEFT_OPEN else (reason or "").strip()
        state.sap_close_pending = False
        state.updated_at = utcnow()
        event = (
            DeltaEvent.SAP_SUBSCRIPTION_CLOSED
            if action is SubscriptionAction.CLOSED
            else DeltaEvent.SAP_SUBSCRIPTION_LEFT_OPEN
        )
        self._history(state, event, state.status, actor, text or None)
        self._audit(
            f"pipeline.{event.value}",
            pipeline,
            actor.actor,
            request,
            details={"generation": state.generation},
            reason=text or None,
        )
        self.session.commit()
        return state

    # ================================================================ utilidades
    def _lock(self, pipeline_id: uuid.UUID) -> Pipeline:
        pipeline = self.session.scalars(
            select(Pipeline).where(Pipeline.id == pipeline_id).with_for_update()
        ).first()
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
        return pipeline

    def _history(
        self,
        state: DeltaState,
        event: DeltaEvent,
        before: str,
        actor: Principal,
        reason: str | None,
        *,
        token_before: str | None = None,
    ) -> None:
        self.session.add(
            DeltaStateHistory(
                pipeline_id=state.pipeline_id,
                at=utcnow(),
                event=event.value,
                from_status=before,
                to_status=state.status,
                generation=state.generation,
                actor=actor.username,
                reason=reason,
                token_before=token_before,
            )
        )

    def _audit(
        self,
        action: str,
        pipeline: Pipeline,
        actor: Actor,
        request: RequestInfo,
        *,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
        resource: tuple[str, str] | None = None,
    ) -> None:
        kind, ident = resource or ("pipeline", str(pipeline.id))
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type=kind,
                resource_id=ident,
                resource_name=pipeline.code,
                reason=reason,
                details={"pipeline_id": str(pipeline.id), **(details or {})},
            ),
            actor=actor,
            request=request,
        )


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) < 5:
        msg = "Indique el motivo (al menos 5 caracteres): queda en la auditoría."
        raise BusinessRuleError(msg, code="SX-PPL-REASON-REQUIRED")
    return text


def _mode_label(mode: RunMode) -> str:
    return {
        RunMode.FULL: "carga completa",
        RunMode.DELTA_INIT: "inicialización del delta",
        RunMode.DELTA: "delta",
        RunMode.WATERMARK: "marca de agua",
    }[mode]


def delete_guard(toolkit: ExtractionToolkit) -> DeleteGuard:
    """Comprobación de baja que el motor registra en el módulo de pipelines."""

    def guard(session: Session, pipeline: Pipeline, actor: Principal, request: RequestInfo) -> None:
        service = RunService(session, toolkit)
        if service.active_run(pipeline.id) is not None:
            msg = "Hay una ejecución en cola o en curso: cancélela antes de eliminar el pipeline."
            raise ConflictError(msg, code="SX-PPL-RUN-ACTIVE")
        state = session.get(DeltaState, pipeline.id)
        if state is not None and state.status in (
            DeltaStatus.INITIALIZED.value,
            DeltaStatus.BROKEN.value,
        ):
            # La suscripción sigue abierta en SAP: se conserva el estado y se avisa (plan 06 §3.6).
            before = state.status
            state.status = DeltaStatus.NOT_INITIALIZED.value
            state.current_token = None
            state.sap_close_pending = True
            state.updated_at = utcnow()
            service._history(state, DeltaEvent.RESET, before, actor, "Pipeline eliminado")

    return guard
