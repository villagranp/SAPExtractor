"""Endpoints del motor: pasos y log en vivo de cada run, y estado de delta del pipeline.

«Ejecutar ahora», cancelar, reintentar y el detalle del run son de la orquestación (plan 08).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.extraction.api.schemas import (
    DeltaStateResponse,
    ReasonRequest,
    RunLogLine,
    RunLogPage,
    StepResponse,
    SubscriptionRequest,
)
from sapextract.extraction.application.delta_service import DeltaService
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.domain.model import RunStatus

router = APIRouter(tags=["Ejecuciones"])
FINAL = frozenset({RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value})


def get_extraction_toolkit(request: Request) -> ExtractionToolkit:
    toolkit: ExtractionToolkit = request.app.state.extraction
    return toolkit


ToolkitDep = Annotated[ExtractionToolkit, Depends(get_extraction_toolkit)]
Reader = Annotated[Principal, Depends(require_permission("pipeline:read"))]
Resetter = Annotated[Principal, Depends(require_permission("pipeline:reset_delta"))]


@router.get(
    "/runs/{run_id}/steps",
    response_model=list[StepResponse],
    summary="Pasos del run por intento (prepare, extract, load, commit_delta, cleanup)",
)
def run_steps(
    run_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> list[StepResponse]:
    return [StepResponse.of(s) for s in DeltaService(session, toolkit).steps(run_id)]


@router.get(
    "/runs/{run_id}/logs",
    response_model=RunLogPage,
    summary="Log del run desde un cursor (la pantalla lo consulta cada segundo)",
)
def run_logs(
    run_id: uuid.UUID,
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> RunLogPage:
    service = DeltaService(session, toolkit)
    lines = service.logs(run_id, after=after, limit=limit)
    return RunLogPage(
        items=[RunLogLine.of(line) for line in lines],
        next_after=lines[-1].id if lines else after,
        finished=service.run(run_id).status in FINAL,
    )


@router.get(
    "/pipelines/{pipeline_id}/delta",
    response_model=DeltaStateResponse,
    summary="Estado de delta o marca de agua del pipeline, con su historial",
)
def delta_state(
    pipeline_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> DeltaStateResponse:
    service = DeltaService(session, toolkit)
    pipeline = service.pipeline(pipeline_id)
    return DeltaStateResponse.of(
        service.state(pipeline_id),
        service.history(pipeline_id),
        requires_reinit=service.requires_reinit(pipeline),
    )


@router.post(
    "/pipelines/{pipeline_id}/delta/reset",
    response_model=DeltaStateResponse,
    summary="Reinicializa el delta: el próximo run será una inicialización nueva",
    openapi_extra=audited("pipeline.delta_reset"),
)
def reset_delta(
    pipeline_id: uuid.UUID,
    body: ReasonRequest,
    actor: Resetter,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DeltaStateResponse:
    service = DeltaService(session, toolkit)
    state = service.reset(pipeline_id, body.reason, actor, info)
    return DeltaStateResponse.of(state, service.history(pipeline_id), requires_reinit=False)


@router.post(
    "/pipelines/{pipeline_id}/delta/sap-subscription",
    response_model=DeltaStateResponse,
    summary="Confirma que la suscripción anterior se cerró en ODQMON o que se deja abierta",
    openapi_extra=audited(
        "pipeline.sap_subscription_closed", "pipeline.sap_subscription_left_open"
    ),
)
def resolve_subscription(
    pipeline_id: uuid.UUID,
    body: SubscriptionRequest,
    actor: Resetter,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DeltaStateResponse:
    service = DeltaService(session, toolkit)
    state = service.resolve_subscription(pipeline_id, body.action, body.reason, actor, info)
    return DeltaStateResponse.of(state, service.history(pipeline_id), requires_reinit=False)
