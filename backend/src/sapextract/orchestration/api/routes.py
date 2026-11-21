"""Endpoints de orquestación (plan 08 §7): pipelines, programación, ejecuciones y workers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from sapextract.audit.api.openapi import audited, not_audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.api.schemas import (
    ActivationReportResponse,
    CloneResponse,
    ConcurrencyGroupPutRequest,
    ConcurrencyGroupResponse,
    FindingResponse,
    PauseResponse,
    PipelineActivateRequest,
    PipelineCloneRequest,
    PipelineCreateRequest,
    PipelineDetail,
    PipelineReasonRequest,
    PipelineReviewRequest,
    PipelineSummary,
    PipelineUpdateRequest,
    RunDetail,
    RunSummary,
    RunTriggerRequest,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    ScheduleResponse,
    SchedulesPutRequest,
    WorkerResponse,
)
from sapextract.orchestration.application.pipeline_service import (
    CloneRequest,
    NewPipeline,
    PipelineChanges,
    PipelineFilters,
    PipelineService,
)
from sapextract.orchestration.application.run_service import RunFilters, RunService
from sapextract.orchestration.application.schedule_service import ScheduleService
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.domain.model import PipelineStatus, RunStatus, RunTrigger
from sapextract.sap_connections.domain.model import SapEnvironment


def get_orchestration(request: Request) -> OrchestrationToolkit:
    toolkit: OrchestrationToolkit = request.app.state.orchestration
    return toolkit


ToolkitDep = Annotated[OrchestrationToolkit, Depends(get_orchestration)]
Reader = Annotated[Principal, Depends(require_permission("pipeline:read"))]
Manager = Annotated[Principal, Depends(require_permission("pipeline:manage"))]
Runner = Annotated[Principal, Depends(require_permission("pipeline:run"))]
Pauser = Annotated[Principal, Depends(require_permission("pipeline:pause"))]
Paging = Annotated[PageParams, Depends(page_params)]

pipelines = APIRouter(prefix="/pipelines", tags=["Pipelines"])
schedules = APIRouter(prefix="/schedules", tags=["Pipelines"])
runs = APIRouter(prefix="/runs", tags=["Ejecuciones"])
workers = APIRouter(prefix="/workers", tags=["Ejecuciones"])
groups = APIRouter(prefix="/concurrency-groups", tags=["Ejecuciones"])


# ====================================================================== pipelines
@pipelines.get("", response_model=Page[PipelineSummary], summary="Pipelines")
def list_pipelines(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[PipelineStatus | None, Query(alias="status")] = None,
    source: Annotated[uuid.UUID | None, Query(description="Origen")] = None,
    destination: Annotated[uuid.UUID | None, Query(description="Destino")] = None,
    system: Annotated[uuid.UUID | None, Query(description="Sistema SAP")] = None,
    environment: Annotated[
        SapEnvironment | None, Query(description="Entorno del sistema SAP")
    ] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[PipelineSummary]:
    items, total = PipelineService(session, toolkit).search(
        PipelineFilters(
            q=q,
            status=status_filter,
            source_id=source,
            destination_id=destination,
            sap_system_id=system,
            environment=environment.value if environment else None,
            tag=tag,
        ),
        page,
    )
    return Page[PipelineSummary].of([PipelineSummary.of(v) for v in items], total, page)


@pipelines.post(
    "/review",
    response_model=list[FindingResponse],
    summary="Avisos de una combinación origen, destino y carga, sin guardar (asistente)",
    openapi_extra=not_audited("Solo compara configuración ya guardada; no guarda ni llama a SAP"),
)
def review_pipeline(
    body: PipelineReviewRequest, _: Manager, session: SessionDep, toolkit: ToolkitDep
) -> list[FindingResponse]:
    findings = PipelineService(session, toolkit).review_draft(
        body.source_id, body.destination_id, body.extraction_mode, body.load_mode
    )
    return FindingResponse.list_of(findings)


@pipelines.post(
    "",
    response_model=PipelineDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de pipeline (queda en borrador)",
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
            load=body.load.to_domain(),
            filters=[f.to_domain() for f in body.filters] if body.filters is not None else None,
            priority=body.priority,
            timeout_minutes=body.timeout_minutes,
            retry_policy=body.retry_policy.to_domain(),
            concurrency_group=body.concurrency_group,
            worker_pool=body.worker_pool,
            log_level=body.log_level,
            tags=body.tags,
        ),
        actor,
        info,
    )
    return PipelineDetail.of_view(view)


@pipelines.get("/{pipeline_id}", response_model=PipelineDetail, summary="Detalle de pipeline")
def get_pipeline(
    pipeline_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> PipelineDetail:
    return PipelineDetail.of_view(PipelineService(session, toolkit).view(pipeline_id))


@pipelines.patch(
    "/{pipeline_id}",
    response_model=PipelineDetail,
    summary="Edición de pipeline",
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
    view = PipelineService(session, toolkit).update(
        pipeline_id,
        PipelineChanges(
            version=body.version,
            name=body.name,
            description=body.description,
            source_id=body.source_id,
            destination_id=body.destination_id,
            extraction_mode=body.extraction_mode,
            load=body.load.to_domain() if body.load else None,
            filters=[f.to_domain() for f in body.filters] if body.filters is not None else None,
            use_source_filters=body.use_source_filters,
            priority=body.priority,
            timeout_minutes=body.timeout_minutes,
            retry_policy=body.retry_policy.to_domain() if body.retry_policy else None,
            concurrency_group=body.concurrency_group,
            worker_pool=body.worker_pool,
            log_level=body.log_level,
            tags=body.tags,
        ),
        actor,
        info,
    )
    return PipelineDetail.of_view(view)


@pipelines.delete(
    "/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Baja lógica de pipeline; no se permite con una ejecución en cola o en curso",
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


@pipelines.post(
    "/{pipeline_id}/validate",
    response_model=ActivationReportResponse,
    summary="Comprobación previa a activar, sin cambiar nada (lee el $metadata en SAP)",
    openapi_extra=not_audited("Solo lee el $metadata de SAP y el estado guardado"),
)
def validate_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineActivateRequest,
    _: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
) -> ActivationReportResponse:
    report = PipelineService(session, toolkit).check(pipeline_id, body.cross_environment_reason)
    return ActivationReportResponse.of(report)


@pipelines.post(
    "/{pipeline_id}/activate",
    response_model=PipelineDetail,
    summary="Activar pipeline (exige pasar la comprobación previa)",
    openapi_extra=audited("pipeline.activated"),
)
def activate_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineActivateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> PipelineDetail:
    view = PipelineService(session, toolkit).activate(
        pipeline_id, body.cross_environment_reason, actor, info
    )
    return PipelineDetail.of_view(view)


@pipelines.post(
    "/{pipeline_id}/pause",
    response_model=PauseResponse,
    summary="Pausar: suspende la programación; la ejecución en curso termina",
    openapi_extra=audited("pipeline.paused"),
)
def pause_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineReasonRequest,
    actor: Pauser,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> PauseResponse:
    view, warnings = PipelineService(session, toolkit).pause(pipeline_id, body.reason, actor, info)
    return PauseResponse(pipeline=PipelineDetail.of_view(view), warnings=warnings)


@pipelines.post(
    "/{pipeline_id}/resume",
    response_model=PipelineDetail,
    summary="Reanudar un pipeline en pausa",
    openapi_extra=audited("pipeline.resumed"),
)
def resume_pipeline(
    pipeline_id: uuid.UUID,
    actor: Pauser,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> PipelineDetail:
    return PipelineDetail.of_view(
        PipelineService(session, toolkit).resume(pipeline_id, actor, info)
    )


@pipelines.post(
    "/{pipeline_id}/clone",
    response_model=CloneResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Clonar sin estado de delta; admite otro sistema SAP y otro destino",
    # En otro sistema SAP puede crear el origen equivalente (exige source:manage).
    openapi_extra=audited("pipeline.cloned", "source.created"),
)
def clone_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineCloneRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> CloneResponse:
    result = PipelineService(session, toolkit).clone(
        pipeline_id,
        CloneRequest(
            code=body.code,
            name=body.name,
            sap_system_id=body.sap_system_id,
            destination_id=body.destination_id,
            source_code=body.source_code,
        ),
        actor,
        info,
    )
    return CloneResponse.of(result)


@pipelines.post(
    "/{pipeline_id}/run",
    response_model=RunDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Ejecutar ahora (prioridad alta)",
    openapi_extra=audited("run.triggered_manual"),
)
def run_pipeline(
    pipeline_id: uuid.UUID,
    body: RunTriggerRequest,
    actor: Runner,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RunDetail:
    view = RunService(session, toolkit).trigger(pipeline_id, body.mode, body.reason, actor, info)
    return RunDetail.of_view(view)


@pipelines.get(
    "/{pipeline_id}/schedules",
    response_model=list[ScheduleResponse],
    summary="Programación del pipeline",
)
def get_schedules(
    pipeline_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> list[ScheduleResponse]:
    pipeline = PipelineService(session, toolkit).get(pipeline_id)
    return [ScheduleResponse.of(s) for s in ScheduleService(session, toolkit).list_for(pipeline.id)]


@pipelines.put(
    "/{pipeline_id}/schedules",
    response_model=list[ScheduleResponse],
    summary="Sustituir la programación del pipeline",
    openapi_extra=audited("schedule.updated"),
)
def put_schedules(
    pipeline_id: uuid.UUID,
    body: SchedulesPutRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> list[ScheduleResponse]:
    pipeline = PipelineService(session, toolkit).get(pipeline_id)
    created = ScheduleService(session, toolkit).replace(
        pipeline, [s.to_domain() for s in body.schedules], actor, info
    )
    return [ScheduleResponse.of(s) for s in created]


# ====================================================================== programación
@schedules.post(
    "/preview",
    response_model=SchedulePreviewResponse,
    summary="Próximas ejecuciones de una programación, sin guardarla",
    openapi_extra=not_audited("Cálculo sin efectos: no guarda nada"),
)
def preview_schedule(
    body: SchedulePreviewRequest, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> SchedulePreviewResponse:
    spec, fires = ScheduleService(session, toolkit).preview(body.to_domain(), count=body.count)
    return SchedulePreviewResponse(timezone=spec.timezone, fires=fires)


# ====================================================================== ejecuciones
@runs.get("", response_model=Page[RunSummary], summary="Ejecuciones")
def list_runs(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    pipeline: Annotated[uuid.UUID | None, Query(description="Pipeline")] = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    trigger: RunTrigger | None = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> Page[RunSummary]:
    items, total = RunService(session, toolkit).search(
        RunFilters(
            pipeline_id=pipeline,
            status=status_filter,
            trigger=trigger,
            created_from=created_from,
            created_to=created_to,
        ),
        page,
    )
    return Page[RunSummary].of([RunSummary.of(v) for v in items], total, page)


@runs.get("/{run_id}", response_model=RunDetail, summary="Detalle de ejecución con sus intentos")
def get_run(run_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep) -> RunDetail:
    return RunDetail.of_view(RunService(session, toolkit).view(run_id))


@runs.post(
    "/{run_id}/cancel",
    response_model=RunDetail,
    summary="Cancelar: una ejecución en curso para entre páginas",
    openapi_extra=audited("run.cancelled"),
)
def cancel_run(
    run_id: uuid.UUID,
    body: PipelineReasonRequest,
    actor: Runner,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RunDetail:
    return RunDetail.of_view(RunService(session, toolkit).cancel(run_id, body.reason, actor, info))


@runs.post(
    "/{run_id}/retry",
    response_model=RunDetail,
    summary="Reintentar una ejecución fallida (añade un intento al mismo run)",
    openapi_extra=audited("run.retried"),
)
def retry_run(
    run_id: uuid.UUID,
    actor: Runner,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RunDetail:
    return RunDetail.of_view(RunService(session, toolkit).retry(run_id, actor, info))


# ====================================================================== workers y grupos
@workers.get("", response_model=list[WorkerResponse], summary="Workers registrados")
def list_workers(_: Reader, session: SessionDep, toolkit: ToolkitDep) -> list[WorkerResponse]:
    return [WorkerResponse.of(w) for w in RunService(session, toolkit).workers()]


@groups.get("", response_model=list[ConcurrencyGroupResponse], summary="Grupos de concurrencia")
def list_groups(
    _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> list[ConcurrencyGroupResponse]:
    return [ConcurrencyGroupResponse.of(g) for g in RunService(session, toolkit).groups()]


@groups.put(
    "/{code}",
    response_model=ConcurrencyGroupResponse,
    summary="Crear o cambiar un grupo de concurrencia",
    openapi_extra=audited("concurrency_group.updated"),
)
def put_group(
    code: str,
    body: ConcurrencyGroupPutRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> ConcurrencyGroupResponse:
    service = RunService(session, toolkit)
    group = service.put_group(code, body.max_parallel, body.description, actor, info)
    return ConcurrencyGroupResponse.of(
        next(g for g in service.groups() if g.group.code == group.code)
    )


@groups.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un grupo de concurrencia sin pipelines",
    openapi_extra=audited("concurrency_group.deleted"),
)
def delete_group(
    code: str, actor: Manager, session: SessionDep, toolkit: ToolkitDep, info: RequestInfoDep
) -> Response:
    RunService(session, toolkit).delete_group(code, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


routers = (pipelines, schedules, runs, workers, groups)
