"""Endpoints de ejecución: ejecutar ahora, cancelar, detalle, log en vivo y estado de delta."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.exceptions import NotFoundError
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.extraction.api.schemas import (
    DeltaStateResponse,
    ReasonRequest,
    RunDetailResponse,
    RunLogLine,
    RunLogPage,
    RunSummary,
    SubscriptionRequest,
)
from sapextract.extraction.application.resolve import ExtractionToolkit
from sapextract.extraction.application.run_service import RunFilters, RunService
from sapextract.extraction.domain.model import FINAL_RUN_STATUSES, RunStatus
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.infrastructure.models import Pipeline

router = APIRouter(tags=["Ejecuciones"])


def get_extraction_toolkit(request: Request) -> ExtractionToolkit:
    toolkit: ExtractionToolkit = request.app.state.extraction
    return toolkit


ToolkitDep = Annotated[ExtractionToolkit, Depends(get_extraction_toolkit)]
Reader = Annotated[Principal, Depends(require_permission("pipeline:read"))]
Runner = Annotated[Principal, Depends(require_permission("pipeline:run"))]
Resetter = Annotated[Principal, Depends(require_permission("pipeline:reset_delta"))]
Paging = Annotated[PageParams, Depends(page_params)]


def _pipeline(session: Session, pipeline_id: uuid.UUID, *, deleted: bool = False) -> Pipeline:
    pipeline = session.get(Pipeline, pipeline_id)
    if pipeline is None or (pipeline.deleted_at is not None and not deleted):
        msg = "El pipeline no existe."
        raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
    return pipeline


@router.get("/runs", response_model=Page[RunSummary], summary="Ejecuciones (más recientes primero)")
def list_runs(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    pipeline_id: uuid.UUID | None = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> Page[RunSummary]:
    rows, total = RunService(session, toolkit).search(
        RunFilters(pipeline_id=pipeline_id, status=status_filter), page
    )
    return Page[RunSummary].of([RunSummary.of(r, p) for r, p in rows], total, page)


@router.post(
    "/pipelines/{pipeline_id}/runs",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ejecutar ahora: encola un run (init, delta, marca de agua o carga completa)",
    openapi_extra=audited("pipeline.run_requested"),
)
def request_run(
    pipeline_id: uuid.UUID,
    actor: Runner,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RunSummary:
    run = RunService(session, toolkit).request_run(pipeline_id, actor, info)
    return RunSummary.of(run, _pipeline(session, run.pipeline_id))


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> RunDetailResponse:
    return RunDetailResponse.of_detail(RunService(session, toolkit).detail(run_id))


@router.get(
    "/runs/{run_id}/logs",
    response_model=RunLogPage,
    summary="Log de la ejecución desde un cursor (la pantalla lo consulta cada segundo)",
)
def run_logs(
    run_id: uuid.UUID,
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> RunLogPage:
    service = RunService(session, toolkit)
    lines = service.logs(run_id, after=after, limit=limit)
    run = service.get(run_id)
    return RunLogPage(
        items=[RunLogLine.of(line) for line in lines],
        next_after=lines[-1].id if lines else after,
        finished=RunStatus(run.status) in FINAL_RUN_STATUSES,
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunSummary,
    summary="Cancelación cooperativa: el worker se detiene entre páginas y el token no avanza",
    openapi_extra=audited("run.cancel_requested"),
)
def cancel_run(
    run_id: uuid.UUID,
    actor: Runner,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RunSummary:
    run = RunService(session, toolkit).cancel(run_id, actor, info)
    return RunSummary.of(run, _pipeline(session, run.pipeline_id, deleted=True))


@router.get("/pipelines/{pipeline_id}/delta", response_model=DeltaStateResponse)
def delta_state(
    pipeline_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> DeltaStateResponse:
    service = RunService(session, toolkit)
    pipeline = _pipeline(session, pipeline_id)
    return DeltaStateResponse.of(
        service.delta_state(pipeline_id),
        service.delta_history(pipeline_id),
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
    service = RunService(session, toolkit)
    state = service.reset_delta(pipeline_id, body.reason, actor, info)
    return DeltaStateResponse.of(state, service.delta_history(pipeline_id), requires_reinit=False)


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
    service = RunService(session, toolkit)
    state = service.resolve_subscription(pipeline_id, body.action, body.reason, actor, info)
    return DeltaStateResponse.of(state, service.delta_history(pipeline_id), requires_reinit=False)
