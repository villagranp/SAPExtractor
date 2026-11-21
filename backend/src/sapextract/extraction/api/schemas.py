"""Contratos HTTP del motor: pasos y log de cada run y estado de delta (plan 06, plan 03 §2).

El run y sus intentos se publican en ``/runs`` (orquestación, plan 08).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sapextract.extraction.application.delta_service import SubscriptionAction
from sapextract.extraction.domain.model import DeltaStatus, StepName, StepStatus
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    RunLog,
    RunStep,
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
