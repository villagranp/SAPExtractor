"""Esquemas de la API de orígenes SAP (plan 05 §6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sapextract.core.exceptions import BusinessRuleError
from sapextract.sap_connections.domain.metadata import OdpRole
from sapextract.sap_connections.domain.model import SapEnvironment, SystemStatus
from sapextract.sap_connections.infrastructure.models import SapSystem
from sapextract.sources.application.source_service import (
    Inspection,
    RefreshResult,
    SourceView,
)
from sapextract.sources.domain.delta_processes import DELTA_PROCESS_SEMANTICS
from sapextract.sources.domain.filters import FilterCondition, FilterOperator, render_filter
from sapextract.sources.domain.model import (
    DeltaConfig,
    DeltaMethod,
    DeltaSemantics,
    OdpContext,
    SourceStatus,
    SourceType,
    WatermarkKind,
)
from sapextract.sources.domain.preview import PreviewData
from sapextract.sources.domain.schema import DetectedSchema, SchemaDiff

# ------------------------------------------------------------------ peticiones


class DeltaConfigBody(BaseModel):
    method: DeltaMethod
    semantics: DeltaSemantics | None = None
    watermark_field: str | None = Field(None, max_length=120)
    watermark_kind: WatermarkKind | None = None
    watermark_timezone: str | None = Field(None, max_length=60)
    watermark_overlap_seconds: int | None = Field(None, ge=0, le=86_400)

    def to_domain(self) -> DeltaConfig:
        return DeltaConfig(**self.model_dump())


class FilterConditionBody(BaseModel):
    field: str = Field(min_length=1, max_length=120)
    op: FilterOperator
    value: bool | int | float | str = Field(
        description="Valor según el tipo del campo: texto, número, booleano o fecha ISO 8601"
    )

    def to_domain(self) -> FilterCondition:
        return FilterCondition(field=self.field, op=self.op, value=self.value)


class SourceInspectRequest(BaseModel):
    sap_system_id: uuid.UUID
    service_name: str = Field(min_length=1, max_length=100)
    service_version: int = Field(1, ge=1, le=999)
    entity_set: str = Field(min_length=1, max_length=120)


class SourceCreateRequest(SourceInspectRequest):
    code: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_type: SourceType | None = Field(
        None, description="Si se omite, el que corresponde al entity set"
    )
    key_fields: list[str] | None = Field(
        None, max_length=50, description="Si se omiten, las claves del $metadata"
    )
    delta: DeltaConfigBody | None = Field(
        None, description="Si se omite, delta ODQ cuando el servicio lo admite"
    )
    default_filters: list[FilterConditionBody] = Field(default_factory=list, max_length=20)
    page_size: int | None = Field(None, ge=100, le=50_000)
    preview_enabled: bool = True


class SourceUpdateRequest(BaseModel):
    version: int | None = Field(None, description="Versión leída (control de concurrencia)")
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    tags: list[str] | None = Field(None, max_length=20)
    key_fields: list[str] | None = Field(None, min_length=1, max_length=50)
    delta: DeltaConfigBody | None = None
    default_filters: list[FilterConditionBody] | None = Field(None, max_length=20)
    page_size: int | None = Field(None, ge=100, le=50_000)
    preview_enabled: bool | None = None


class SourceReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


# ------------------------------------------------------------------ respuestas


class SourceSystemRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    environment: SapEnvironment
    status: SystemStatus

    @classmethod
    def of(cls, s: SapSystem) -> SourceSystemRef:
        return cls(
            id=s.id,
            code=s.code,
            name=s.name,
            environment=SapEnvironment(s.environment),
            status=SystemStatus(s.status),
        )


class SchemaColumnResponse(BaseModel):
    name: str
    label: str | None
    edm_type: str
    arrow_type: str
    nullable: bool
    is_key: bool = Field(description="Clave según el $metadata de SAP")
    max_length: int | None
    precision: int | None
    scale: int | None
    display_format: str | None
    filterable: bool

    @classmethod
    def list_of(cls, detected: DetectedSchema) -> list[SchemaColumnResponse]:
        return [cls.model_validate(c) for c in detected.to_json()]


class SemanticsHintResponse(BaseModel):
    delta_process: str = Field(description="Proceso de delta habitual (RSA2); confírmelo en SAP")
    semantics: DeltaSemantics


class InspectionResponse(BaseModel):
    sap_system: SourceSystemRef
    service_name: str
    service_version: int
    service_title: str | None
    service_description: str | None
    entity_set: str
    entity_set_label: str | None
    odp_role: OdpRole | None
    odp_name: str | None
    odata_version: str
    suggested_type: SourceType
    supports_delta: bool
    keys: list[str] = Field(description="Claves según el $metadata")
    columns: list[SchemaColumnResponse]
    watermark_candidates: list[str]
    semantics_hint: SemanticsHintResponse | None
    delta_processes: dict[str, DeltaSemantics] = Field(
        description="Proceso de delta de RSA2 → semántica, para la ayuda del asistente"
    )

    @classmethod
    def of(cls, i: Inspection) -> InspectionResponse:
        hint = i.semantics_hint
        return cls(
            sap_system=SourceSystemRef.of(i.system),
            service_name=i.service.technical_name,
            service_version=i.service.version,
            service_title=i.service.title,
            service_description=i.service.description,
            entity_set=i.entity_set.name,
            entity_set_label=i.entity_set.label,
            odp_role=i.odp_role,
            odp_name=i.odp_name,
            odata_version=i.odata_version,
            suggested_type=i.suggested_type,
            supports_delta=i.supports_delta,
            keys=list(i.detected.schema.keys),
            columns=SchemaColumnResponse.list_of(i.detected),
            watermark_candidates=i.watermark_candidates,
            semantics_hint=SemanticsHintResponse(
                delta_process=hint.delta_process, semantics=hint.semantics
            )
            if hint
            else None,
            delta_processes=dict(DELTA_PROCESS_SEMANTICS),
        )


class FilterConditionResponse(BaseModel):
    field: str
    op: FilterOperator
    value: bool | int | float | str


class SchemaVersionResponse(BaseModel):
    version: int
    hash: str
    detected_at: datetime
    detected_by: str | None
    column_count: int


class SourceSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    source_type: SourceType
    status: SourceStatus
    sap_system: SourceSystemRef
    odp_name: str | None
    service_name: str
    entity_set: str
    delta_method: DeltaMethod
    delta_semantics: DeltaSemantics | None
    schema_version: int
    tags: list[str]
    updated_at: datetime

    @classmethod
    def of(cls, v: SourceView) -> SourceSummary:
        s = v.source
        return cls(
            id=s.id,
            code=s.code,
            name=s.name,
            source_type=SourceType(s.source_type),
            status=SourceStatus(s.status),
            sap_system=SourceSystemRef.of(v.system),
            odp_name=s.odp_name,
            service_name=s.service_name,
            entity_set=s.entity_set,
            delta_method=DeltaMethod(s.delta_method),
            delta_semantics=DeltaSemantics(s.delta_semantics) if s.delta_semantics else None,
            schema_version=v.schema.version,
            tags=list(s.tags),
            updated_at=s.updated_at,
        )


class SourceDetail(SourceSummary):
    description: str | None
    odp_context: OdpContext | None
    service_version: int
    service_path: str
    odata_version: str
    supports_delta: bool
    watermark_field: str | None
    watermark_kind: WatermarkKind | None
    watermark_timezone: str | None
    watermark_overlap_seconds: int | None
    key_fields: list[str] = Field(description="Claves confirmadas para el destino")
    default_filters: list[FilterConditionResponse]
    filter_expression: str = Field(description="$filter que se enviará a SAP (vacío si no hay)")
    page_size: int
    preview_enabled: bool
    status_reason: str | None
    schema_checked_at: datetime | None
    schema_info: SchemaVersionResponse = Field(alias="schema")
    columns: list[SchemaColumnResponse]
    created_at: datetime
    created_by: str | None
    updated_by: str | None
    version: int

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def of_view(cls, v: SourceView) -> SourceDetail:
        s = v.source
        detected = DetectedSchema.from_json(v.schema.columns)
        filters = [FilterCondition.from_dict(f) for f in s.default_filters]
        try:
            expression = render_filter(filters, detected.schema, s.odata_version)
        except BusinessRuleError:  # el esquema cambió y el filtro ya no aplica
            expression = ""
        return cls(
            **SourceSummary.of(v).model_dump(),
            description=s.description,
            odp_context=OdpContext(s.odp_context) if s.odp_context else None,
            service_version=s.service_version,
            service_path=s.service_path,
            odata_version=s.odata_version,
            supports_delta=s.supports_delta,
            watermark_field=s.watermark_field,
            watermark_kind=WatermarkKind(s.watermark_kind) if s.watermark_kind else None,
            watermark_timezone=s.watermark_timezone,
            watermark_overlap_seconds=s.watermark_overlap_seconds,
            key_fields=list(s.key_fields),
            default_filters=[FilterConditionResponse.model_validate(f) for f in s.default_filters],
            filter_expression=expression,
            page_size=s.page_size,
            preview_enabled=s.preview_enabled,
            status_reason=s.status_reason,
            schema_checked_at=s.schema_checked_at,
            schema_info=SchemaVersionResponse(
                version=v.schema.version,
                hash=v.schema.hash,
                detected_at=v.schema.detected_at,
                detected_by=v.schema.detected_by,
                column_count=len(v.schema.columns),
            ),
            columns=SchemaColumnResponse.list_of(detected),
            created_at=s.created_at,
            created_by=s.created_by,
            updated_by=s.updated_by,
            version=s.version,
        )


class ColumnChangeResponse(BaseModel):
    name: str
    before: dict[str, Any]
    after: dict[str, Any]
    breaking: bool


class DiffColumnResponse(BaseModel):
    name: str
    edm_type: str
    is_key: bool
    label: str | None


class SchemaDiffResponse(BaseModel):
    from_version: int
    to_version: int
    breaking: bool = Field(description="Columna eliminada o cambio incompatible")
    reordered: bool
    added: list[DiffColumnResponse]
    removed: list[DiffColumnResponse]
    changed: list[ColumnChangeResponse]

    @classmethod
    def of(cls, d: SchemaDiff, from_version: int, to_version: int) -> SchemaDiffResponse:
        def col(c: Any) -> DiffColumnResponse:
            return DiffColumnResponse(
                name=c.name, edm_type=c.edm_type, is_key=c.is_key, label=c.label
            )

        return cls(
            from_version=from_version,
            to_version=to_version,
            breaking=d.breaking,
            reordered=d.reordered,
            added=[col(c) for c in d.added],
            removed=[col(c) for c in d.removed],
            changed=[
                ColumnChangeResponse(
                    name=c.name, before=c.before, after=c.after, breaking=c.breaking
                )
                for c in d.changed
            ],
        )


class SchemaRefreshResponse(BaseModel):
    changed: bool
    source: SourceDetail
    diff: SchemaDiffResponse

    @classmethod
    def of(cls, r: RefreshResult) -> SchemaRefreshResponse:
        return cls(
            changed=r.changed,
            source=SourceDetail.of_view(r.view),
            diff=SchemaDiffResponse.of(r.diff, r.previous_version, r.view.schema.version),
        )


class PreviewResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = Field(description="SAP tiene más filas que las mostradas")
    count: int | None = Field(description="Recuento total (solo CDS vía OData)")
    elapsed_ms: int

    @classmethod
    def of(cls, p: PreviewData) -> PreviewResponse:
        return cls(
            columns=p.columns,
            rows=p.rows,
            truncated=p.truncated,
            count=p.count,
            elapsed_ms=p.elapsed_ms,
        )
