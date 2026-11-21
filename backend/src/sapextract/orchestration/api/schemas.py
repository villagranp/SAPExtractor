"""Contratos HTTP de los pipelines (plan 08, reducido a la ejecución manual de la It5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, SchemaEvolution
from sapextract.orchestration.application.pipeline_service import LoadOptionsInput, PipelineView
from sapextract.orchestration.domain.model import ExtractionMode, PipelineStatus


class LoadOptionsBody(BaseModel):
    load_mode: LoadMode
    target_name: str = Field(min_length=1, max_length=128, examples=["FI_GL_ITEMS"])
    create_if_missing: bool = True
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling = DeleteHandling.SOFT
    technical_columns: bool = True
    column_mapping: dict[str, str | None] = Field(
        default_factory=dict, description="Columna SAP → nombre en destino; null la excluye"
    )

    def to_input(self) -> LoadOptionsInput:
        return LoadOptionsInput(**self.model_dump())


class FilterBody(BaseModel):
    field: str = Field(min_length=1, max_length=120)
    op: str = Field(pattern=r"^(eq|ne|gt|ge|lt|le)$")
    value: str | int | float | bool


class PipelineCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40, examples=["FI_GL_SNOW_QAS"])
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    source_id: uuid.UUID
    destination_id: uuid.UUID
    extraction_mode: ExtractionMode
    load_options: LoadOptionsBody
    filters: list[FilterBody] = Field(default_factory=list, max_length=20)
    timeout_minutes: int | None = Field(None, ge=1)
    max_attempts: int | None = Field(None, ge=1)
    tags: list[str] = Field(default_factory=list, max_length=20)


class PipelineUpdateRequest(BaseModel):
    version: int
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    load_options: LoadOptionsBody | None = None
    filters: list[FilterBody] | None = Field(None, max_length=20)
    timeout_minutes: int | None = Field(None, ge=1)
    max_attempts: int | None = Field(None, ge=1)
    tags: list[str] | None = Field(None, max_length=20)


class PipelineEndpoint(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    kind: str = Field(description="Tipo de origen (ODP_EXTRACTOR…) o de destino (csv…)")


class PipelineSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    extraction_mode: ExtractionMode
    status: PipelineStatus
    source: PipelineEndpoint
    destination: PipelineEndpoint
    load_options: dict[str, Any]
    tags: list[str]
    updated_at: datetime
    version: int

    @classmethod
    def fields_of(cls, view: PipelineView) -> dict[str, Any]:
        p, s, d = view.pipeline, view.source, view.destination
        return {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "extraction_mode": ExtractionMode(p.extraction_mode),
            "status": PipelineStatus(p.status),
            "source": PipelineEndpoint(id=s.id, code=s.code, name=s.name, kind=s.source_type),
            "destination": PipelineEndpoint(id=d.id, code=d.code, name=d.name, kind=d.type),
            "load_options": p.load_options,
            "tags": list(p.tags),
            "updated_at": p.updated_at,
            "version": p.version,
        }

    @classmethod
    def of(cls, view: PipelineView) -> PipelineSummary:
        return cls(**cls.fields_of(view))


class PipelineDetail(PipelineSummary):
    filters: list[dict[str, Any]]
    timeout_minutes: int
    max_attempts: int
    created_at: datetime
    created_by: str | None
    updated_by: str | None

    @classmethod
    def of(cls, view: PipelineView) -> PipelineDetail:
        p = view.pipeline
        return cls(
            **PipelineSummary.fields_of(view),
            filters=list(p.filters),
            timeout_minutes=p.timeout_minutes,
            max_attempts=p.max_attempts,
            created_at=p.created_at,
            created_by=p.created_by,
            updated_by=p.updated_by,
        )
