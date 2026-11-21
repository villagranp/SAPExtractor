"""Contratos HTTP de ejecuciones, log en vivo y estado de delta (plan 06, plan 03 §2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sapextract.dataflow.domain.batch import RunMode
from sapextract.extraction.application.run_service import RunDetail, SubscriptionAction
from sapextract.extraction.domain.model import (
    AttemptOutcome,
    DeltaStatus,
    RunStatus,
    RunTrigger,
    StepName,
    StepStatus,
)
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    Run,
    RunAttempt,
    RunLog,
    RunStep,
)
from sapextract.orchestration.infrastructure.models import Pipeline


class RunSummary(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    pipeline_code: str
    mode: RunMode
    status: RunStatus
    trigger: RunTrigger
    triggered_by: str | None
    batch_id: str | None
    attempts_used: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    rows_extracted: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_deleted: int | None
    created_at: datetime
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @staticmethod
    def fields_of(run: Run, pipeline: Pipeline) -> dict[str, Any]:
        return {
            "id": run.id,
            "pipeline_id": run.pipeline_id,
            "pipeline_code": pipeline.code,
            "mode": RunMode(run.mode),
            "status": RunStatus(run.status),
            "trigger": RunTrigger(run.trigger),
            "triggered_by": run.triggered_by,
            "batch_id": run.batch_id,
            "attempts_used": run.attempts_used,
            "max_attempts": run.max_attempts,
            "cancel_requested": run.cancel_requested,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "rows_extracted": run.rows_extracted,
            "rows_inserted": run.rows_inserted,
            "rows_updated": run.rows_updated,
            "rows_deleted": run.rows_deleted,
            "created_at": run.created_at,
            "available_at": run.available_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }

    @classmethod
    def of(cls, run: Run, pipeline: Pipeline) -> RunSummary:
        return cls(**cls.fields_of(run, pipeline))


class AttemptResponse(BaseModel):
    number: int
    worker_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: AttemptOutcome | None
    error_code: str | None
    error_message: str | None
    has_spool: bool = Field(
        description="El worker de este intento tiene un spool válido del lote (un reintento allí "
        "no vuelve a llamar a SAP)"
    )

    @classmethod
    def of(cls, a: RunAttempt) -> AttemptResponse:
        return cls(
            number=a.number,
            worker_id=a.worker_id,
            started_at=a.started_at,
            finished_at=a.finished_at,
            outcome=AttemptOutcome(a.outcome) if a.outcome else None,
            error_code=a.error_code,
            error_message=a.error_message,
            has_spool=a.spool_worker_id is not None,
        )


class StepResponse(BaseModel):
    attempt: int
    step: StepName
    status: StepStatus
    started_at: datetime
    finished_at: datetime | None
    metrics: dict[str, Any]

    @classmethod
    def of(cls, s: RunStep) -> StepResponse:
        return cls(
            attempt=s.attempt,
            step=StepName(s.step),
            status=StepStatus(s.status),
            started_at=s.started_at,
            finished_at=s.finished_at,
            metrics=s.metrics,
        )


class RunDetailResponse(RunSummary):
    delta_generation: int | None
    start_token: str | None = Field(description="Token de partida (solo en el detalle, no en logs)")
    next_token: str | None = Field(description="Token confirmado por este run")
    window_start: datetime | None
    window_end: datetime | None
    timeout_minutes: int
    attempts: list[AttemptResponse]
    steps: list[StepResponse]

    @classmethod
    def of_detail(cls, d: RunDetail) -> RunDetailResponse:
        r = d.run
        return cls(
            **RunSummary.fields_of(r, d.pipeline),
            delta_generation=r.delta_generation,
            start_token=r.start_token,
            next_token=r.next_token,
            window_start=r.window_start,
            window_end=r.window_end,
            timeout_minutes=r.timeout_minutes,
            attempts=[AttemptResponse.of(a) for a in d.attempts],
            steps=[StepResponse.of(s) for s in d.steps],
        )


class RunLogLine(BaseModel):
    id: int
    ts: datetime
    attempt: int | None
    step: StepName | None
    level: str
    message: str
    data: dict[str, Any]

    @classmethod
    def of(cls, line: RunLog) -> RunLogLine:
        return cls(
            id=line.id,
            ts=line.ts,
            attempt=line.attempt,
            step=StepName(line.step) if line.step else None,
            level=line.level,
            message=line.message,
            data=line.data,
        )


class RunLogPage(BaseModel):
    items: list[RunLogLine]
    next_after: int | None = Field(description="Cursor para la siguiente consulta (after)")
    finished: bool = Field(description="El run terminó: no llegarán más líneas")


class DeltaHistoryEntry(BaseModel):
    at: datetime
    event: str
    from_status: DeltaStatus | None
    to_status: DeltaStatus | None
    generation: int
    run_id: uuid.UUID | None
    actor: str | None
    reason: str | None

    @classmethod
    def of(cls, h: DeltaStateHistory) -> DeltaHistoryEntry:
        return cls(
            at=h.at,
            event=h.event,
            from_status=DeltaStatus(h.from_status) if h.from_status else None,
            to_status=DeltaStatus(h.to_status) if h.to_status else None,
            generation=h.generation,
            run_id=h.run_id,
            actor=h.actor,
            reason=h.reason,
        )


class DeltaStateResponse(BaseModel):
    status: DeltaStatus
    generation: int
    current_token: str | None
    pending_run_id: uuid.UUID | None
    sap_close_pending: bool = Field(description="La suscripción anterior sigue abierta en SAP")
    requires_reinit: bool = Field(description="Los filtros cambiaron desde la inicialización")
    watermark_value: dict[str, Any] | None
    initialized_at: datetime | None
    last_delta_at: datetime | None
    history: list[DeltaHistoryEntry]

    @classmethod
    def of(
        cls,
        state: DeltaState | None,
        history: list[DeltaStateHistory],
        *,
        requires_reinit: bool,
    ) -> DeltaStateResponse:
        if state is None:
            return cls(
                status=DeltaStatus.NOT_INITIALIZED,
                generation=0,
                current_token=None,
                pending_run_id=None,
                sap_close_pending=False,
                requires_reinit=False,
                watermark_value=None,
                initialized_at=None,
                last_delta_at=None,
                history=[],
            )
        return cls(
            status=DeltaStatus(state.status),
            generation=state.generation,
            current_token=state.current_token,
            pending_run_id=state.pending_run_id,
            sap_close_pending=state.sap_close_pending,
            requires_reinit=requires_reinit,
            watermark_value=state.watermark_value,
            initialized_at=state.initialized_at,
            last_delta_at=state.last_delta_at,
            history=[DeltaHistoryEntry.of(h) for h in history],
        )


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class SubscriptionRequest(BaseModel):
    action: SubscriptionAction
    reason: str | None = Field(None, max_length=500)
