"""Contratos HTTP de los destinos (plan 07).

La configuración no sensible es un objeto libre que valida el modelo Pydantic de cada
plugin (su JSON Schema se publica en ``/destination-types/{type}/schema``). El secreto
solo se recibe (``writeOnly``) y nunca se devuelve: las respuestas muestran
``credential_info`` (p. ej. la huella de la clave pública).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, SchemaEvolution
from sapextract.destinations.application.destination_service import DdlPreview
from sapextract.destinations.application.plugins import DestinationPlugin
from sapextract.destinations.application.toolkit import DestinationDependent
from sapextract.destinations.domain.model import (
    DestinationEnvironment,
    DestinationStatus,
    DestinationType,
)
from sapextract.destinations.domain.testing import CheckReport
from sapextract.destinations.infrastructure.models import Destination

SECRET_FIELD = Field(
    None,
    description="Credenciales del destino (solo escritura; nunca se devuelven).",
    json_schema_extra={"writeOnly": True},
)


# ------------------------------------------------------------------ tipos
class DestinationTypeResponse(BaseModel):
    type: DestinationType
    label: str
    requires_secret: bool
    supported_modes: list[LoadMode]

    @classmethod
    def of(cls, plugin: DestinationPlugin) -> DestinationTypeResponse:
        return cls(
            type=plugin.kind,
            label=plugin.label,
            requires_secret=plugin.secret_model is not None,
            supported_modes=sorted(plugin.supported_modes),
        )


class DestinationTypeSchemaResponse(DestinationTypeResponse):
    config_schema: dict[str, Any] = Field(description="JSON Schema de la configuración")
    secret_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema de las credenciales (si el tipo las usa)"
    )


# ------------------------------------------------------------------ peticiones
class DestinationCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=30, examples=["SNOW_FIN_PRD"])
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    type: DestinationType
    environment: DestinationEnvironment
    tags: list[str] = Field(default_factory=list, max_length=20)
    config: dict[str, Any]
    secret: dict[str, Any] | None = SECRET_FIELD
    run_test: bool = Field(True, description="Probar el destino al guardar")
    activate: bool = Field(False, description="Activar si la prueba es correcta")


class DestinationTestRequest(BaseModel):
    """Prueba del asistente sin guardar."""

    type: DestinationType
    code: str | None = Field(None, max_length=30)
    config: dict[str, Any]
    secret: dict[str, Any] | None = SECRET_FIELD
    reuse_credentials_from: uuid.UUID | None = Field(
        None, description="Usar las credenciales guardadas de este destino (edición)"
    )


class DestinationUpdateRequest(BaseModel):
    version: int | None = Field(None, description="Versión leída (bloqueo optimista)")
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    environment: DestinationEnvironment | None = None
    tags: list[str] | None = Field(None, max_length=20)
    config: dict[str, Any] | None = None


class SecretRotateRequest(BaseModel):
    version: int | None = None
    secret: dict[str, Any] = Field(
        description="Credenciales nuevas (solo escritura).", json_schema_extra={"writeOnly": True}
    )


class StatusReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class SourceRef(BaseModel):
    sap_system_id: uuid.UUID
    service: str = Field(min_length=1, max_length=200, description="Nombre técnico del servicio")
    version: int = Field(1, ge=1, le=999)
    entity_set: str = Field(min_length=1, max_length=200)


class DdlPreviewRequest(BaseModel):
    source: SourceRef
    target_name: str = Field(min_length=1, max_length=128, examples=["GL_ITEMS"])
    load_mode: LoadMode = LoadMode.UPSERT
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling = DeleteHandling.SOFT
    technical_columns: bool = True
    column_mapping: dict[str, str | None] = Field(
        default_factory=dict, description="Origen → nombre en destino (null = excluir)"
    )

    def options(self) -> LoadOptions:
        return LoadOptions(
            load_mode=self.load_mode,
            target_name=self.target_name,
            schema_evolution=self.schema_evolution,
            delete_handling=self.delete_handling,
            technical_columns=self.technical_columns,
            column_mapping=self.column_mapping,
        )


# ------------------------------------------------------------------ respuestas
class CheckStepResponse(BaseModel):
    name: str
    status: str
    message: str
    duration_ms: int
    error_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CheckReportResponse(BaseModel):
    ok: bool
    summary: str
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime
    steps: list[CheckStepResponse]

    @classmethod
    def of(cls, report: CheckReport) -> CheckReportResponse:
        return cls.model_validate(report.as_dict())

    @classmethod
    def from_stored(cls, data: dict[str, Any] | None) -> CheckReportResponse | None:
        return cls.model_validate(data) if data else None


class DependentResponse(BaseModel):
    kind: str
    id: str
    name: str
    active: bool

    @classmethod
    def of(cls, d: DestinationDependent) -> DependentResponse:
        return cls(kind=d.kind, id=d.id, name=d.name, active=d.active)


class DestinationSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    type: DestinationType
    environment: DestinationEnvironment
    status: DestinationStatus
    tags: list[str]
    target: str = Field(description="Resumen legible del destino (cuenta/base o carpeta)")
    credential_info: dict[str, Any]
    last_test_at: datetime | None
    last_test_ok: bool | None
    test_outdated: bool
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, d: Destination) -> DestinationSummary:
        return cls(**_summary_fields(d))


class DestinationDetail(DestinationSummary):
    config: dict[str, Any]
    status_reason: str | None
    last_test_result: CheckReportResponse | None
    dependents: list[DependentResponse] = Field(default_factory=list)
    created_at: datetime
    created_by: str | None
    updated_by: str | None

    @classmethod
    def of_detail(
        cls, d: Destination, dependents: list[DestinationDependent] | None = None
    ) -> DestinationDetail:
        return cls(
            **_summary_fields(d),
            config=d.config,
            status_reason=d.status_reason,
            last_test_result=CheckReportResponse.from_stored(d.last_test_result),
            dependents=[DependentResponse.of(x) for x in dependents or []],
            created_at=d.created_at,
            created_by=d.created_by,
            updated_by=d.updated_by,
        )


class DestinationWithTestResponse(BaseModel):
    destination: DestinationDetail
    test: CheckReportResponse | None = None


class DdlColumnResponse(BaseModel):
    source: str
    target: str | None = Field(description="null = columna excluida")
    edm_type: str
    arrow_type: str
    is_key: bool
    label: str | None


class DdlPreviewResponse(BaseModel):
    statements: list[str]
    columns: list[DdlColumnResponse]
    keys: list[str]

    @classmethod
    def of(cls, preview: DdlPreview) -> DdlPreviewResponse:
        return cls(
            statements=preview.statements,
            columns=[DdlColumnResponse.model_validate(c) for c in preview.columns],
            keys=preview.keys,
        )


def _target(d: Destination) -> str:
    c = d.config
    if d.type == DestinationType.SNOWFLAKE:
        return f"{c.get('account', '')} · {c.get('database', '')}.{c.get('schema_name', '')}"
    return f"{c.get('root', '')}/{c.get('path', '')}".rstrip("/")


def _summary_fields(d: Destination) -> dict[str, Any]:
    return {
        "id": d.id,
        "code": d.code,
        "name": d.name,
        "description": d.description,
        "type": d.type,
        "environment": d.environment,
        "status": d.status,
        "tags": list(d.tags),
        "target": _target(d),
        "credential_info": d.credential_info,
        "last_test_at": d.last_test_at,
        "last_test_ok": d.last_test_ok,
        "test_outdated": d.test_outdated,
        "updated_at": d.updated_at,
        "version": d.version,
    }
