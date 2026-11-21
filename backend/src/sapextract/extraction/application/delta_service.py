"""Estado de delta y log de ejecución vistos desde la API, y el enganche del motor con la cola.

Plan 06 §3.3 y §3.6, plan 03 §2. El run, la cola, «ejecutar ahora», la cancelación y los
reintentos son de la orquestación (plan 08); aquí solo lo que es del motor.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import Actor, AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from sapextract.dataflow.domain.batch import RunMode
from sapextract.extraction.application.resolve import ExtractionToolkit, pipeline_filter
from sapextract.extraction.application.store import (
    ensure_delta_state,
    history,
    release_delta,
    snapshot,
)
from sapextract.extraction.domain.model import (
    DeltaEvent,
    DeltaStatus,
    check_admission,
)
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    RunLog,
    RunStep,
)
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.application.lifecycle import live_run
from sapextract.orchestration.domain.model import ExtractionMode
from sapextract.orchestration.infrastructure.models import Pipeline, Run

MAX_LOG_LINES = 500
SCHEDULER = Actor.system("scheduler")


class SubscriptionAction(StrEnum):
    CLOSED = "closed"  # el usuario la cerró en ODQMON
    LEFT_OPEN = "left_open"  # la deja abierta a sabiendas


class DeltaService:
    def __init__(self, session: Session, toolkit: ExtractionToolkit) -> None:
        self.session = session
        self.tk = toolkit

    # ================================================================ consultas
    def run(self, run_id: uuid.UUID) -> Run:
        run = self.session.get(Run, run_id)
        if run is None:
            msg = "La ejecución no existe."
            raise NotFoundError(msg, code="SX-RUN-NOT-FOUND")
        return run

    def steps(self, run_id: uuid.UUID) -> list[RunStep]:
        self.run(run_id)
        return list(
            self.session.scalars(
                select(RunStep)
                .where(RunStep.run_id == run_id)
                .order_by(RunStep.attempt, RunStep.started_at)
            )
        )

    def logs(self, run_id: uuid.UUID, *, after: int | None, limit: int) -> list[RunLog]:
        """Líneas desde el cursor ``after`` (sondeo en vivo, plan 03 §2)."""
        self.run(run_id)
        stmt = select(RunLog).where(RunLog.run_id == run_id)
        if after is not None:
            stmt = stmt.where(RunLog.id > after)
        return list(self.session.scalars(stmt.order_by(RunLog.id).limit(min(limit, MAX_LOG_LINES))))

    def pipeline(self, pipeline_id: uuid.UUID) -> Pipeline:
        pipeline = self.session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        return pipeline

    def state(self, pipeline_id: uuid.UUID) -> DeltaState | None:
        return self.session.get(DeltaState, pipeline_id)

    def history(self, pipeline_id: uuid.UUID, limit: int = 50) -> list[DeltaStateHistory]:
        return list(
            self.session.scalars(
                select(DeltaStateHistory)
                .where(DeltaStateHistory.pipeline_id == pipeline_id)
                .order_by(DeltaStateHistory.id.desc())
                .limit(limit)
            )
        )

    def requires_reinit(self, pipeline: Pipeline) -> bool:
        """Filtros cambiados desde la init: se calcula, no se guarda (plan 06 §3.6)."""
        state = self.session.get(DeltaState, pipeline.id)
        if state is None or state.init_filters_hash is None:
            return False
        if pipeline.extraction_mode != ExtractionMode.DELTA.value:
            return False
        _, current = pipeline_filter(self.session, pipeline, self.tk.sap)
        return current != state.init_filters_hash

    # ================================================================ reinicialización
    def reset(
        self, pipeline_id: uuid.UUID, reason: str, actor: Principal, request: RequestInfo
    ) -> DeltaState:
        """La próxima ejecución hará una init nueva (plan 06 §3.6, pasos 1, 2 y 4).

        El cierre de la suscripción anterior en SAP es manual (ODQMON) hasta que el spike
        confirme ``TerminateDeltasFor…`` (H12).
        """
        text = _reason(reason)
        pipeline = self._lock(pipeline_id)
        if pipeline.extraction_mode != ExtractionMode.DELTA.value:
            msg = "Solo los pipelines delta tienen suscripción que reinicializar."
            raise BusinessRuleError(msg, code="SX-PIPE-NOT-DELTA")
        if live_run(self.session, pipeline_id) is not None:
            msg = "Hay una ejecución en cola o en curso: cancélela antes de reinicializar."
            raise ConflictError(msg, code="SX-PIPE-RUN-LIVE")
        state = ensure_delta_state(self.session, pipeline_id)
        if state.status == DeltaStatus.NOT_INITIALIZED.value:
            msg = "El delta no está inicializado: no hay nada que reinicializar."
            raise BusinessRuleError(msg, code="SX-PIPE-DELTA-NOT-INITIALIZED")
        before, token = state.status, state.current_token
        state.status = DeltaStatus.NOT_INITIALIZED.value
        state.current_token = state.pending_token = None
        state.pending_run_id = None
        state.init_filters_hash = None
        state.sap_close_pending = True
        state.updated_at = utcnow()
        history(
            self.session,
            state,
            DeltaEvent.RESET,
            before,
            actor=actor.username,
            reason=text,
            token_before=token,
        )
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
            raise BusinessRuleError(msg, code="SX-PIPE-NO-SAP-CLOSE-PENDING")
        text = _reason(reason) if action is SubscriptionAction.LEFT_OPEN else (reason or "").strip()
        state.sap_close_pending = False
        state.updated_at = utcnow()
        event = (
            DeltaEvent.SAP_SUBSCRIPTION_CLOSED
            if action is SubscriptionAction.CLOSED
            else DeltaEvent.SAP_SUBSCRIPTION_LEFT_OPEN
        )
        history(self.session, state, event, state.status, actor=actor.username, reason=text or None)
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
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        return pipeline

    def _audit(
        self,
        action: str,
        pipeline: Pipeline,
        actor: Actor,
        request: RequestInfo,
        *,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="pipeline",
                resource_id=str(pipeline.id),
                resource_name=pipeline.code,
                reason=reason,
                details=details or {},
            ),
            actor=actor,
            request=request,
        )


class DeltaHooks:
    """``EngineHooks`` de la orquestación: el estado de delta acompaña a la cola."""

    def __init__(self, toolkit: ExtractionToolkit) -> None:
        self.tk = toolkit

    def check_run(self, session: Session, pipeline: Pipeline, mode: RunMode) -> None:
        if mode is not RunMode.DELTA:
            return
        state = session.get(DeltaState, pipeline.id)
        if state is None:
            return
        _, current = pipeline_filter(session, pipeline, self.tk.sap)
        check_admission(snapshot(state), current)

    def run_finished(self, session: Session, run: Run) -> None:
        release_delta(session, run, actor=SCHEDULER, audit=self.tk.audit)

    def pipeline_deleted(self, session: Session, pipeline: Pipeline, actor: Principal) -> None:
        """La suscripción sigue abierta en SAP: se conserva el estado y se avisa (plan 06 §3.6)."""
        state = session.get(DeltaState, pipeline.id)
        if state is None or state.status not in (
            DeltaStatus.INITIALIZED.value,
            DeltaStatus.BROKEN.value,
        ):
            return
        before = state.status
        state.status = DeltaStatus.NOT_INITIALIZED.value
        state.current_token = None
        state.sap_close_pending = True
        state.updated_at = utcnow()
        history(
            session,
            state,
            DeltaEvent.RESET,
            before,
            actor=actor.username,
            reason="Pipeline eliminado",
        )


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) < 5:
        msg = "Indique el motivo (al menos 5 caracteres): queda en la auditoría."
        raise BusinessRuleError(msg, code="SX-PIPE-REASON-REQUIRED")
    return text
