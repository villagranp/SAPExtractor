"""Endpoints de sistemas SAP: CRUD, prueba de conexión, credenciales y explorador de servicios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.sap_connections.api.schemas import (
    CatalogPageResponse,
    CredentialRotateRequest,
    SapSystemCreateRequest,
    SapSystemDetail,
    SapSystemDuplicateRequest,
    SapSystemSummary,
    SapSystemTestRequest,
    SapSystemUpdateRequest,
    SapSystemWithTestResponse,
    ServiceEntitySetsResponse,
    ServiceMetadataResponse,
    StatusReasonRequest,
    TestReportResponse,
)
from sapextract.sap_connections.application.explorer_service import SapExplorerService
from sapextract.sap_connections.application.system_service import (
    DuplicateData,
    NewSapSystem,
    SapSystemService,
    SystemChanges,
    SystemFilters,
    SystemWithTest,
)
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sap_connections.domain.catalog import CatalogQuery, ServiceKind
from sapextract.sap_connections.domain.model import SapEnvironment, SystemStatus, SystemType

router = APIRouter(prefix="/sap-systems", tags=["Sistemas SAP"])


def get_sap_toolkit(request: Request) -> SapToolkit:
    toolkit: SapToolkit = request.app.state.sap
    return toolkit


SapToolkitDep = Annotated[SapToolkit, Depends(get_sap_toolkit)]
Reader = Annotated[Principal, Depends(require_permission("sap_connection:read"))]
Manager = Annotated[Principal, Depends(require_permission("sap_connection:manage"))]
Tester = Annotated[Principal, Depends(require_permission("sap_connection:test"))]
Paging = Annotated[PageParams, Depends(page_params)]


def _with_test(result: SystemWithTest) -> SapSystemWithTestResponse:
    return SapSystemWithTestResponse(
        system=SapSystemDetail.of_detail(result.system, result.dependents),
        test=TestReportResponse.of(result.report) if result.report else None,
    )


# ================================================================== sistemas
@router.get("", response_model=Page[SapSystemSummary], summary="Sistemas SAP")
def list_systems(
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    environment: SapEnvironment | None = None,
    status_filter: Annotated[SystemStatus | None, Query(alias="status")] = None,
    system_type: SystemType | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[SapSystemSummary]:
    items, total = SapSystemService(session, toolkit).search(
        SystemFilters(
            q=q, environment=environment, status=status_filter, system_type=system_type, tag=tag
        ),
        page,
    )
    return Page[SapSystemSummary].of([SapSystemSummary.of(s) for s in items], total, page)


@router.post(
    "",
    response_model=SapSystemWithTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de sistema SAP (y prueba/activación opcionales)",
    openapi_extra=audited(
        "sap_connection.created", "sap_connection.tested", "sap_connection.activated"
    ),
)
def create_system(
    body: SapSystemCreateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemWithTestResponse:
    result = SapSystemService(session, toolkit).create(
        NewSapSystem(
            code=body.code,
            name=body.name,
            description=body.description,
            environment=body.environment,
            system_type=body.system_type,
            tags=body.tags,
            connection=body.connection(),
            credentials=body.credentials.to_domain(),
            run_test=body.run_test,
            activate=body.activate,
        ),
        actor,
        info,
    )
    return _with_test(result)


@router.post(
    "/test",
    response_model=TestReportResponse,
    summary="Prueba de conexión sin guardar (asistente)",
    openapi_extra=audited("sap_connection.tested"),
)
def test_unsaved(
    body: SapSystemTestRequest,
    actor: Tester,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> TestReportResponse:
    report = SapSystemService(session, toolkit).test_unsaved(
        body.code,
        body.connection(),
        body.credentials.to_domain() if body.credentials else None,
        actor,
        info,
        reuse_from=body.reuse_credentials_from,
    )
    return TestReportResponse.of(report)


@router.get("/{system_id}", response_model=SapSystemDetail)
def get_system(
    system_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: SapToolkitDep
) -> SapSystemDetail:
    service = SapSystemService(session, toolkit)
    return SapSystemDetail.of_detail(service.get(system_id), service.dependents(system_id))


@router.patch(
    "/{system_id}",
    response_model=SapSystemDetail,
    summary="Edita datos generales y de conexión (las credenciales tienen su propio endpoint)",
    openapi_extra=audited("sap_connection.updated"),
)
def update_system(
    system_id: uuid.UUID,
    body: SapSystemUpdateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemDetail:
    data = body.model_dump(exclude_unset=True)
    system = SapSystemService(session, toolkit).update(
        system_id, SystemChanges(**data), actor, info
    )
    return SapSystemDetail.of_detail(system)


@router.delete(
    "/{system_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Baja lógica (destruye las credenciales); no se permite con objetos activos",
    openapi_extra=audited("sap_connection.deleted"),
)
def delete_system(
    system_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    SapSystemService(session, toolkit).delete(system_id, reason, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{system_id}/test",
    response_model=SapSystemWithTestResponse,
    summary="Prueba de conexión por pasos (guarda el resultado)",
    openapi_extra=audited("sap_connection.tested"),
)
def test_system(
    system_id: uuid.UUID,
    actor: Tester,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemWithTestResponse:
    return _with_test(SapSystemService(session, toolkit).test(system_id, actor, info))


@router.post(
    "/{system_id}/credentials/rotate",
    response_model=SapSystemWithTestResponse,
    summary="Sustituye la credencial (o el tipo de autenticación) y prueba la conexión",
    openapi_extra=audited("sap_connection.credentials_rotated", "sap_connection.tested"),
)
def rotate_credentials(
    system_id: uuid.UUID,
    body: CredentialRotateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemWithTestResponse:
    result = SapSystemService(session, toolkit).rotate_credentials(
        system_id, body.credentials.to_domain(), actor, info, version=body.version
    )
    return _with_test(result)


@router.post(
    "/{system_id}/duplicate",
    response_model=SapSystemWithTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplica la conexión (p. ej. de QAS a PRD cambiando URL y credenciales)",
    openapi_extra=audited("sap_connection.created"),
)
def duplicate_system(
    system_id: uuid.UUID,
    body: SapSystemDuplicateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemWithTestResponse:
    result = SapSystemService(session, toolkit).duplicate(
        system_id,
        DuplicateData(
            code=body.code,
            name=body.name,
            environment=body.environment,
            base_url=body.base_url,
            sap_client=body.sap_client,
            credentials=body.credentials.to_domain() if body.credentials else None,
        ),
        actor,
        info,
    )
    return _with_test(result)


@router.post(
    "/{system_id}/activate",
    response_model=SapSystemDetail,
    summary="Activa el sistema (exige una prueba correcta y vigente)",
    openapi_extra=audited("sap_connection.activated"),
)
def activate_system(
    system_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemDetail:
    service = SapSystemService(session, toolkit)
    return SapSystemDetail.of_detail(service.activate(system_id, actor, info))


@router.post(
    "/{system_id}/deactivate",
    response_model=SapSystemDetail,
    summary="Desactiva el sistema: impide nuevas ejecuciones",
    openapi_extra=audited("sap_connection.deactivated"),
)
def deactivate_system(
    system_id: uuid.UUID,
    body: StatusReasonRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SapSystemDetail:
    result = SapSystemService(session, toolkit).deactivate(system_id, body.reason, actor, info)
    return SapSystemDetail.of_detail(result.system, result.dependents)


# ================================================================== explorador
@router.get(
    "/{system_id}/services",
    response_model=CatalogPageResponse,
    tags=["Explorador SAP"],
    summary="Catálogo de servicios de Gateway clasificado (ODP / CDS / otros)",
)
def list_services(
    system_id: uuid.UUID,
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    page: Paging,
    search: Annotated[str | None, Query(max_length=100)] = None,
    service_type: Annotated[
        list[ServiceKind] | None, Query(alias="type", description="Uno o varios tipos")
    ] = None,
    refresh: Annotated[bool, Query(description="Ignorar la caché y releer de SAP")] = False,
) -> CatalogPageResponse:
    result = SapExplorerService(session, toolkit).services(
        system_id,
        CatalogQuery(search=search, kinds=frozenset(service_type or ())),
        page,
        refresh=refresh,
    )
    return CatalogPageResponse.of(result, page.page, page.page_size)


@router.get(
    "/{system_id}/services/{service_name:path}/entity-sets",
    response_model=ServiceEntitySetsResponse,
    tags=["Explorador SAP"],
    summary="Entity sets del servicio, con propiedades y soporte de delta",
)
def service_entity_sets(
    system_id: uuid.UUID,
    service_name: str,
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    version: Annotated[int, Query(ge=1, le=999)] = 1,
) -> ServiceEntitySetsResponse:
    detail = SapExplorerService(session, toolkit).service_detail(system_id, service_name, version)
    return ServiceEntitySetsResponse.of(detail)


@router.get(
    "/{system_id}/services/{service_name:path}/metadata",
    response_model=ServiceMetadataResponse,
    tags=["Explorador SAP"],
    summary="$metadata del servicio interpretado (tipos de entidad y propiedades)",
)
def service_metadata(
    system_id: uuid.UUID,
    service_name: str,
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    version: Annotated[int, Query(ge=1, le=999)] = 1,
) -> ServiceMetadataResponse:
    detail = SapExplorerService(session, toolkit).service_detail(system_id, service_name, version)
    return ServiceMetadataResponse.of(detail)
