"""Endpoints de orígenes SAP (plan 05 §6): inspección, CRUD, estado, esquema y vista previa."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from sapextract.audit.api.openapi import audited, not_audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.sap_connections.api.routes import SapToolkitDep
from sapextract.sources.api.schemas import (
    InspectionResponse,
    PreviewResponse,
    SchemaDiffResponse,
    SchemaRefreshResponse,
    SchemaVersionResponse,
    SourceCreateRequest,
    SourceDetail,
    SourceInspectRequest,
    SourceReasonRequest,
    SourceSummary,
    SourceUpdateRequest,
)
from sapextract.sources.application.source_service import (
    NewSource,
    SourceChanges,
    SourceFilters,
    SourceService,
)
from sapextract.sources.domain.model import DeltaMethod, SourceStatus, SourceType

router = APIRouter(prefix="/sources", tags=["Orígenes SAP"])

Reader = Annotated[Principal, Depends(require_permission("source:read"))]
Manager = Annotated[Principal, Depends(require_permission("source:manage"))]
Previewer = Annotated[Principal, Depends(require_permission("source:preview"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[SourceSummary], summary="Orígenes SAP")
def list_sources(
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    system: Annotated[uuid.UUID | None, Query(description="Sistema SAP")] = None,
    source_type: Annotated[SourceType | None, Query(alias="type")] = None,
    status_filter: Annotated[SourceStatus | None, Query(alias="status")] = None,
    delta_method: DeltaMethod | None = None,
) -> Page[SourceSummary]:
    items, total = SourceService(session, toolkit).search(
        SourceFilters(
            q=q,
            sap_system_id=system,
            source_type=source_type,
            status=status_filter,
            delta_method=delta_method,
        ),
        page,
    )
    return Page[SourceSummary].of([SourceSummary.of(v) for v in items], total, page)


@router.post(
    "/inspect",
    response_model=InspectionResponse,
    summary="Esquema y capacidades de un entity set, sin guardar (asistente)",
    openapi_extra=not_audited("Solo lee el $metadata de SAP; no guarda ni lee datos"),
)
def inspect_entity_set(
    body: SourceInspectRequest, _: Manager, session: SessionDep, toolkit: SapToolkitDep
) -> InspectionResponse:
    inspection = SourceService(session, toolkit).inspect(
        body.sap_system_id, body.service_name, body.entity_set, body.service_version
    )
    return InspectionResponse.of(inspection)


@router.post(
    "",
    response_model=SourceDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de origen (queda en borrador)",
    openapi_extra=audited("source.created"),
)
def create_source(
    body: SourceCreateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SourceDetail:
    view = SourceService(session, toolkit).create(
        NewSource(
            sap_system_id=body.sap_system_id,
            code=body.code,
            name=body.name,
            description=body.description,
            tags=body.tags,
            service_name=body.service_name,
            service_version=body.service_version,
            entity_set=body.entity_set,
            source_type=body.source_type,
            key_fields=body.key_fields,
            delta=body.delta.to_domain() if body.delta else None,
            default_filters=[f.to_domain() for f in body.default_filters],
            page_size=body.page_size,
            preview_enabled=body.preview_enabled,
        ),
        actor,
        info,
    )
    return SourceDetail.of_view(view)


@router.get("/{source_id}", response_model=SourceDetail, summary="Detalle de origen")
def get_source(
    source_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: SapToolkitDep
) -> SourceDetail:
    return SourceDetail.of_view(SourceService(session, toolkit).view(source_id))


@router.patch(
    "/{source_id}",
    response_model=SourceDetail,
    summary="Edición de origen",
    openapi_extra=audited("source.updated"),
)
def update_source(
    source_id: uuid.UUID,
    body: SourceUpdateRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SourceDetail:
    view = SourceService(session, toolkit).update(
        source_id,
        SourceChanges(
            version=body.version,
            name=body.name,
            description=body.description,
            tags=body.tags,
            key_fields=body.key_fields,
            delta=body.delta.to_domain() if body.delta else None,
            default_filters=[f.to_domain() for f in body.default_filters]
            if body.default_filters is not None
            else None,
            page_size=body.page_size,
            preview_enabled=body.preview_enabled,
        ),
        actor,
        info,
    )
    return SourceDetail.of_view(view)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Baja lógica de origen",
    openapi_extra=audited("source.deleted"),
)
def delete_source(
    source_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    SourceService(session, toolkit).delete(source_id, reason, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{source_id}/activate",
    response_model=SourceDetail,
    summary="Activar origen (borrador u obsoleto → activo)",
    openapi_extra=audited("source.activated"),
)
def activate_source(
    source_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SourceDetail:
    return SourceDetail.of_view(SourceService(session, toolkit).activate(source_id, actor, info))


@router.post(
    "/{source_id}/deprecate",
    response_model=SourceDetail,
    summary="Marcar origen como obsoleto",
    openapi_extra=audited("source.deprecated"),
)
def deprecate_source(
    source_id: uuid.UUID,
    body: SourceReasonRequest,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SourceDetail:
    view = SourceService(session, toolkit).deprecate(source_id, body.reason, actor, info)
    return SourceDetail.of_view(view)


@router.post(
    "/{source_id}/preview",
    response_model=PreviewResponse,
    summary="Primeras 100 filas sin abrir suscripción en la ODQ",
    openapi_extra=audited("source.previewed"),
)
def preview_source(
    source_id: uuid.UUID,
    actor: Previewer,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> PreviewResponse:
    return PreviewResponse.of(SourceService(session, toolkit).preview(source_id, actor, info))


@router.post(
    "/{source_id}/schema/refresh",
    response_model=SchemaRefreshResponse,
    summary="Releer el $metadata y versionar el esquema si cambió",
    openapi_extra=audited("source.schema_refreshed"),
)
def refresh_schema(
    source_id: uuid.UUID,
    actor: Manager,
    session: SessionDep,
    toolkit: SapToolkitDep,
    info: RequestInfoDep,
) -> SchemaRefreshResponse:
    result = SourceService(session, toolkit).refresh_schema(source_id, actor, info)
    return SchemaRefreshResponse.of(result)


@router.get(
    "/{source_id}/schema/versions",
    response_model=list[SchemaVersionResponse],
    summary="Versiones del esquema (la más reciente primero)",
)
def schema_versions(
    source_id: uuid.UUID, _: Reader, session: SessionDep, toolkit: SapToolkitDep
) -> list[SchemaVersionResponse]:
    return [
        SchemaVersionResponse(
            version=v.version,
            hash=v.hash,
            detected_at=v.detected_at,
            detected_by=v.detected_by,
            column_count=len(v.columns),
        )
        for v in SourceService(session, toolkit).versions(source_id)
    ]


@router.get(
    "/{source_id}/schema/diff",
    response_model=SchemaDiffResponse,
    summary="Diferencias entre dos versiones del esquema",
)
def schema_diff(
    source_id: uuid.UUID,
    _: Reader,
    session: SessionDep,
    toolkit: SapToolkitDep,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> SchemaDiffResponse:
    result = SourceService(session, toolkit).diff(source_id, from_version, to_version)
    return SchemaDiffResponse.of(result, from_version, to_version)
