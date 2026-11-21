"""Endpoints de pipelines: alta, consulta, edición y baja (plan 08, reducido en la It5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.api.schemas import (
    PipelineCreateRequest,
    PipelineDetail,
    PipelineSummary,
    PipelineUpdateRequest,
)
from sapextract.orchestration.application.pipeline_service import (
    NewPipeline,
    PipelineChanges,
    PipelineFilters,
    PipelineService,
    PipelinesToolkit,
)
from sapextract.orchestration.domain.model import ExtractionMode

router = APIRouter(tags=["Pipelines"])


def get_pipelines_toolkit(request: Request) -> PipelinesToolkit:
    toolkit: PipelinesToolkit = request.app.state.pipelines
    return toolkit


ToolkitDep = Annotated[PipelinesToolkit, Depends(get_pipelines_toolkit)]
Reader = Annotated[Principal, Depends(require_permission("pipeline:read"))]
Manager = Annotated[Principal, Depends(require_permission("pipeline:manage"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("/pipelines", response_model=Page[PipelineSummary], summary="Pipelines")
def list_pipelines(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    source_id: uuid.UUID | None = None,
    destination_id: uuid.UUID | None = None,
    extraction_mode: ExtractionMode | None = None,
) -> Page[PipelineSummary]:
    items, total = PipelineService(session, toolkit).search(
        PipelineFilters(
            q=q,
            source_id=source_id,
            destination_id=destination_id,
            extraction_mode=extraction_mode,
        ),
        page,
    )
    return Page[PipelineSummary].of([PipelineSummary.of(v) for v in items], total, page)


@router.post(
    "/pipelines",
    response_model=PipelineDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de pipeline (un origen hacia un destino)",
    openapi_extra=audited("pipeline.created"),
)
def create_pipeline(
    body: PipelineCreateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> PipelineDetail:
    view = PipelineService(session, toolkit).create(
        NewPipeline(
            code=body.code,
            name=body.name,
            description=body.description,
            source_id=body.source_id,
            destination_id=body.destination_id,
            extraction_mode=body.extraction_mode,
            load_options=body.load_options.to_input(),
            filters=[f.model_dump() for f in body.filters],
            timeout_minutes=body.timeout_minutes,
            max_attempts=body.max_attempts,
            tags=body.tags,
        ),
        actor,
        info,
    )
    return PipelineDetail.of(view)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineDetail)
def get_pipeline(
    pipeline_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> PipelineDetail:
    return PipelineDetail.of(PipelineService(session, toolkit).view(pipeline_id))


@router.patch(
    "/pipelines/{pipeline_id}",
    response_model=PipelineDetail,
    summary="Edita nombre, opciones de carga, filtros, tiempo máximo e intentos",
    openapi_extra=audited("pipeline.updated"),
)
def update_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineUpdateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> PipelineDetail:
    data = body.model_dump(exclude_unset=True)
    changes = PipelineChanges(
        version=body.version,
        name=data.get("name"),
        description=data.get("description"),
        load_options=body.load_options.to_input() if body.load_options else None,
        filters=[f.model_dump() for f in body.filters] if body.filters is not None else None,
        timeout_minutes=data.get("timeout_minutes"),
        max_attempts=data.get("max_attempts"),
        tags=data.get("tags"),
    )
    view = PipelineService(session, toolkit).update(pipeline_id, changes, actor, info)
    return PipelineDetail.of(view)


@router.delete(
    "/pipelines/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Baja lógica; una suscripción de delta abierta queda pendiente de cerrar en SAP",
    openapi_extra=audited("pipeline.deleted"),
)
def delete_pipeline(
    pipeline_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    PipelineService(session, toolkit).delete(pipeline_id, reason, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
