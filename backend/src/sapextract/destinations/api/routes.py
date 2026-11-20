"""Endpoints de destinos: tipos, CRUD, prueba, credenciales, estado y vista previa de DDL."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from sapextract.audit.api.openapi import audited, not_audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.destinations.api.schemas import (
    CheckReportResponse,
    DdlPreviewRequest,
    DdlPreviewResponse,
    DestinationCreateRequest,
    DestinationDetail,
    DestinationSummary,
    DestinationTestRequest,
    DestinationTypeResponse,
    DestinationTypeSchemaResponse,
    DestinationUpdateRequest,
    DestinationWithTestResponse,
    SecretRotateRequest,
    StatusReasonRequest,
)
from sapextract.destinations.application.destination_service import (
    DestinationChanges,
    DestinationFilters,
    DestinationService,
    DestinationWithTest,
    NewDestination,
)
from sapextract.destinations.application.source_schema import source_entity_schema
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.destinations.domain.model import (
    DestinationEnvironment,
    DestinationStatus,
    DestinationType,
)
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.sap_connections.api.routes import SapToolkitDep

router = APIRouter(tags=["Destinos"])


def get_destinations_toolkit(request: Request) -> DestinationsToolkit:
    toolkit: DestinationsToolkit = request.app.state.destinations
    return toolkit


ToolkitDep = Annotated[DestinationsToolkit, Depends(get_destinations_toolkit)]
Reader = Annotated[Principal, Depends(require_permission("destination:read"))]
Manager = Annotated[Principal, Depends(require_permission("destination:manage"))]
Tester = Annotated[Principal, Depends(require_permission("destination:test"))]
Paging = Annotated[PageParams, Depends(page_params)]


def _with_test(result: DestinationWithTest) -> DestinationWithTestResponse:
    return DestinationWithTestResponse(
        destination=DestinationDetail.of_detail(result.destination, result.dependents),
        test=CheckReportResponse.of(result.report) if result.report else None,
    )


# ================================================================== tipos
@router.get(
    "/destination-types",
    response_model=list[DestinationTypeResponse],
    summary="Tipos de destino disponibles",
)
def list_types(_: Reader, toolkit: ToolkitDep) -> list[DestinationTypeResponse]:
    return [DestinationTypeResponse.of(p) for p in toolkit.registry.all()]


@router.get(
    "/destination-types/{destination_type}/schema",
    response_model=DestinationTypeSchemaResponse,
    summary="JSON Schema del formulario de configuración y credenciales",
)
def type_schema(
    destination_type: DestinationType, _: Reader, toolkit: ToolkitDep
) -> DestinationTypeSchemaResponse:
    plugin = toolkit.registry.get(destination_type)
    base = DestinationTypeResponse.of(plugin)
    return DestinationTypeSchemaResponse(
        **base.model_dump(),
        config_schema=plugin.config_json_schema(toolkit.context),
        secret_schema=plugin.secret_model.model_json_schema() if plugin.secret_model else None,
    )


# ================================================================== destinos
@router.get("/destinations", response_model=Page[DestinationSummary], summary="Destinos")
def list_destinations(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    destination_type: Annotated[DestinationType | None, Query(alias="type")] = None,
    environment: DestinationEnvironment | None = None,
    status_filter: Annotated[DestinationStatus | None, Query(alias="status")] = None,
) -> Page[DestinationSummary]:
    items, total = DestinationService(session, toolkit).search(
        DestinationFilters(
            q=q, type=destination_type, environment=environment, status=status_filter
        ),
        page,
    )
    return Page[DestinationSummary].of([DestinationSummary.of(d) for d in items], total, page)


@router.post(
    "/destinations",
    response_model=DestinationWithTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de destino (y prueba/activación opcionales)",
    openapi_extra=audited("destination.created", "destination.tested", "destination.activated"),
)
def create_destination(
    body: DestinationCreateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationWithTestResponse:
    result = DestinationService(session, toolkit).create(
        NewDestination(
            code=body.code,
            name=body.name,
            description=body.description,
            type=body.type,
            environment=body.environment,
            tags=body.tags,
            config=body.config,
            secret=body.secret,
            run_test=body.run_test,
            activate=body.activate,
        ),
        actor,
        info,
    )
    return _with_test(result)


@router.post(
    "/destinations/test",
    response_model=CheckReportResponse,
    summary="Prueba del destino sin guardar (asistente)",
    openapi_extra=audited("destination.tested"),
)
def test_unsaved(
    body: DestinationTestRequest,
    actor: Tester,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> CheckReportResponse:
    report = DestinationService(session, toolkit).test_unsaved(
        body.type,
        code=body.code,
        config_data=body.config,
        secret_data=body.secret,
        actor=actor,
        request=info,
        reuse_from=body.reuse_credentials_from,
    )
    return CheckReportResponse.of(report)


@router.get("/destinations/{destination_id}", response_model=DestinationDetail)
def get_destination(
    destination_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: ToolkitDep
) -> DestinationDetail:
    service = DestinationService(session, toolkit)
    return DestinationDetail.of_detail(
        service.get(destination_id), service.dependents(destination_id)
    )


@router.patch(
    "/destinations/{destination_id}",
    response_model=DestinationDetail,
    summary="Edita datos generales y configuración (las credenciales tienen su propio endpoint)",
    openapi_extra=audited("destination.updated"),
)
def update_destination(
    destination_id: uuid.UUID,
    body: DestinationUpdateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationDetail:
    data = body.model_dump(exclude_unset=True)
    destination = DestinationService(session, toolkit).update(
        destination_id, DestinationChanges(**data), actor, info
    )
    return DestinationDetail.of_detail(destination)


@router.delete(
    "/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Baja lógica (destruye las credenciales); no se permite con pipelines activos",
    openapi_extra=audited("destination.deleted"),
)
def delete_destination(
    destination_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    DestinationService(session, toolkit).delete(destination_id, reason, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/destinations/{destination_id}/test",
    response_model=DestinationWithTestResponse,
    summary="Prueba del destino por pasos (guarda el resultado)",
    openapi_extra=audited("destination.tested"),
)
def test_destination(
    destination_id: uuid.UUID,
    actor: Tester,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationWithTestResponse:
    return _with_test(DestinationService(session, toolkit).test(destination_id, actor, info))


@router.post(
    "/destinations/{destination_id}/credentials/rotate",
    response_model=DestinationWithTestResponse,
    summary="Sustituye las credenciales y prueba el destino",
    openapi_extra=audited("destination.credentials_rotated", "destination.tested"),
)
def rotate_credentials(
    destination_id: uuid.UUID,
    body: SecretRotateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationWithTestResponse:
    result = DestinationService(session, toolkit).rotate_credentials(
        destination_id, body.secret, actor, info, version=body.version
    )
    return _with_test(result)


@router.post(
    "/destinations/{destination_id}/activate",
    response_model=DestinationDetail,
    summary="Activa el destino (exige una prueba correcta y vigente)",
    openapi_extra=audited("destination.activated"),
)
def activate_destination(
    destination_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationDetail:
    service = DestinationService(session, toolkit)
    return DestinationDetail.of_detail(service.activate(destination_id, actor, info))


@router.post(
    "/destinations/{destination_id}/deactivate",
    response_model=DestinationDetail,
    summary="Desactiva el destino: impide nuevas cargas",
    openapi_extra=audited("destination.deactivated"),
)
def deactivate_destination(
    destination_id: uuid.UUID,
    body: StatusReasonRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> DestinationDetail:
    result = DestinationService(session, toolkit).deactivate(
        destination_id, body.reason, actor, info
    )
    return DestinationDetail.of_detail(result.destination, result.dependents)


@router.post(
    "/destinations/{destination_id}/ddl-preview",
    response_model=DdlPreviewResponse,
    summary="Vista previa del DDL para un entity set SAP (no ejecuta nada)",
    openapi_extra=not_audited("Solo lectura: calcula sentencias sin ejecutarlas"),
)
def ddl_preview(
    destination_id: uuid.UUID,
    body: DdlPreviewRequest,
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    sap: SapToolkitDep,
) -> DdlPreviewResponse:
    service = DestinationService(session, toolkit)
    service.get(destination_id)  # 404 antes de leer SAP
    schema = source_entity_schema(
        session,
        sap,
        sap_system_id=body.source.sap_system_id,
        service=body.source.service,
        version=body.source.version,
        entity_set=body.source.entity_set,
    )
    return DdlPreviewResponse.of(service.ddl_preview(destination_id, schema, body.options()))
