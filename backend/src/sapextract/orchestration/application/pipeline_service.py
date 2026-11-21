"""Casos de uso de pipelines (plan 08), reducidos a lo que necesita la ejecución manual (It5).

El asistente, la programación, la activación con comprobaciones, las pausas y el clonado
llegan en la It6. Este módulo no conoce el motor: el motor registra sus comprobaciones de
baja en ``PipelinesToolkit.before_delete``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    FieldIssue,
    NotFoundError,
)
from sapextract.core.pagination import PageParams
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, SchemaEvolution
from sapextract.destinations.application.destination_service import DestinationService
from sapextract.destinations.application.toolkit import DestinationDependent, DestinationsToolkit
from sapextract.destinations.domain.model import (
    DestinationStatus,
    DestinationType,
    check_load_options,
    resolve_columns,
)
from sapextract.destinations.infrastructure.models import Destination
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.domain.model import (
    ALLOW_SHARED_DELTA_SOURCE,
    ExtractionMode,
    PipelineStatus,
    check_attempts,
    check_extraction_mode,
    check_load_mode,
    check_timeout,
    issue,
    load_options_from_json,
    load_options_to_json,
    normalize_code,
)
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sources.application.source_service import SourceService, SourceView
from sapextract.sources.domain.filters import FilterCondition, check_filters
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics, SourceStatus
from sapextract.sources.domain.schema import DetectedSchema
from sapextract.sources.infrastructure.models import Source

SORTABLE = frozenset({"code", "name", "created_at", "updated_at"})

type DeleteGuard = Callable[[Session, Pipeline, Principal, RequestInfo], None]


@dataclass(frozen=True, kw_only=True)
class PipelinesToolkit:
    audit: AuditService
    sap: SapToolkit
    destinations: DestinationsToolkit
    before_delete: list[DeleteGuard] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class LoadOptionsInput:
    load_mode: LoadMode
    target_name: str
    create_if_missing: bool = True
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling = DeleteHandling.SOFT
    technical_columns: bool = True
    column_mapping: dict[str, str | None] = field(default_factory=dict)

    def options(self) -> LoadOptions:
        return LoadOptions(
            load_mode=self.load_mode,
            target_name=self.target_name.strip(),
            create_if_missing=self.create_if_missing,
            schema_evolution=self.schema_evolution,
            delete_handling=self.delete_handling,
            technical_columns=self.technical_columns,
            column_mapping=dict(self.column_mapping),
        )


@dataclass(frozen=True, kw_only=True)
class NewPipeline:
    code: str
    name: str
    source_id: uuid.UUID
    destination_id: uuid.UUID
    extraction_mode: ExtractionMode
    load_options: LoadOptionsInput
    description: str | None = None
    filters: list[dict[str, Any]] = field(default_factory=list)
    timeout_minutes: int | None = None
    max_attempts: int | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class PipelineChanges:
    version: int
    name: str | None = None
    description: str | None = None
    load_options: LoadOptionsInput | None = None
    filters: list[dict[str, Any]] | None = None
    timeout_minutes: int | None = None
    max_attempts: int | None = None
    tags: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class PipelineFilters:
    q: str | None = None
    source_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    extraction_mode: ExtractionMode | None = None


@dataclass(frozen=True, kw_only=True)
class PipelineView:
    pipeline: Pipeline
    source: Source
    destination: Destination


class PipelineService:
    def __init__(self, session: Session, toolkit: PipelinesToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.sources = SourceService(session, toolkit.sap)
        self.destinations = DestinationService(session, toolkit.destinations)

    # -------------------------------------------------------------- consultas
    def search(self, filters: PipelineFilters, page: PageParams) -> tuple[list[PipelineView], int]:
        stmt = (
            select(Pipeline, Source, Destination)
            .join(Source, Source.id == Pipeline.source_id)
            .join(Destination, Destination.id == Pipeline.destination_id)
            .where(Pipeline.deleted_at.is_(None))
        )
        if filters.q:
            like = f"%{filters.q.strip()}%"
            stmt = stmt.where(
                or_(Pipeline.code.ilike(like), Pipeline.name.ilike(like), Source.code.ilike(like))
            )
        if filters.source_id:
            stmt = stmt.where(Pipeline.source_id == filters.source_id)
        if filters.destination_id:
            stmt = stmt.where(Pipeline.destination_id == filters.destination_id)
        if filters.extraction_mode:
            stmt = stmt.where(Pipeline.extraction_mode == filters.extraction_mode.value)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "code"
        name = sort.lstrip("-")
        column = getattr(Pipeline, name) if name in SORTABLE else Pipeline.code
        stmt = stmt.order_by(column.desc() if sort.startswith("-") else column.asc(), Pipeline.code)
        rows = self.session.execute(stmt.offset(page.offset).limit(page.page_size)).all()
        return [PipelineView(pipeline=p, source=s, destination=d) for p, s, d in rows], total

    def get(self, pipeline_id: uuid.UUID) -> Pipeline:
        pipeline = self.session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
        return pipeline

    def view(self, pipeline_id: uuid.UUID) -> PipelineView:
        pipeline = self.get(pipeline_id)
        source = self.session.get(Source, pipeline.source_id)
        destination = self.session.get(Destination, pipeline.destination_id)
        if source is None or destination is None:  # las FK lo impiden
            msg = "El origen o el destino del pipeline no existen."
            raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
        return PipelineView(pipeline=pipeline, source=source, destination=destination)

    # -------------------------------------------------------------- alta y edición
    def create(self, data: NewPipeline, actor: Principal, request: RequestInfo) -> PipelineView:
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        name = _required(data.name, "name")
        source_view = self._source(data.source_id)
        destination = self._destination(data.destination_id)
        mode = data.extraction_mode
        check_extraction_mode(mode, DeltaMethod(source_view.source.delta_method))
        options = self._check_options(data.load_options.options(), mode, source_view, destination)
        filters = self._check_filters(data.filters, source_view)
        if mode is ExtractionMode.DELTA:
            self._ensure_delta_source_free(source_view.source)
        pipeline = Pipeline(
            code=code,
            name=name,
            description=(data.description or "").strip() or None,
            source_id=source_view.source.id,
            destination_id=destination.id,
            extraction_mode=mode.value,
            load_options=load_options_to_json(options),
            filters=filters,
            timeout_minutes=check_timeout(data.timeout_minutes),
            max_attempts=check_attempts(data.max_attempts),
            status=PipelineStatus.DRAFT.value,
            tags=_tags(data.tags),
            created_by=actor.username,
            updated_by=actor.username,
        )
        self.session.add(pipeline)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise self._code_taken(code) from None
        self._audit("pipeline.created", pipeline, actor, request, diff(None, _snapshot(pipeline)))
        self.session.commit()
        return PipelineView(pipeline=pipeline, source=source_view.source, destination=destination)

    def update(
        self,
        pipeline_id: uuid.UUID,
        changes: PipelineChanges,
        actor: Principal,
        request: RequestInfo,
    ) -> PipelineView:
        pipeline = self.get(pipeline_id)
        if pipeline.version != changes.version:
            msg = "Otra persona modificó el pipeline. Recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-PPL-VERSION-CONFLICT")
        before = _snapshot(pipeline)
        view = self.view(pipeline_id)
        source_view = self._source(pipeline.source_id)
        mode = ExtractionMode(pipeline.extraction_mode)
        if changes.name is not None:
            pipeline.name = _required(changes.name, "name")
        if changes.description is not None:
            pipeline.description = changes.description.strip() or None
        if changes.load_options is not None:
            options = self._check_options(
                changes.load_options.options(), mode, source_view, view.destination
            )
            pipeline.load_options = load_options_to_json(options)
        if changes.filters is not None:
            pipeline.filters = self._check_filters(changes.filters, source_view)
        if changes.timeout_minutes is not None:
            pipeline.timeout_minutes = check_timeout(changes.timeout_minutes)
        if changes.max_attempts is not None:
            pipeline.max_attempts = check_attempts(changes.max_attempts)
        if changes.tags is not None:
            pipeline.tags = _tags(changes.tags)
        pipeline.updated_by = actor.username
        pipeline.updated_at = utcnow()
        self.session.flush()
        changed = diff(before, _snapshot(pipeline))
        if changed:
            self._audit("pipeline.updated", pipeline, actor, request, changed)
        self.session.commit()
        return PipelineView(pipeline=pipeline, source=view.source, destination=view.destination)

    def delete(
        self, pipeline_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> None:
        pipeline = self.get(pipeline_id)
        for guard in self.tk.before_delete:
            guard(self.session, pipeline, actor, request)
        self._audit(
            "pipeline.deleted",
            pipeline,
            actor,
            request,
            diff(_snapshot(pipeline), None),
            reason=(reason or "").strip() or None,
        )
        pipeline.deleted_at = utcnow()
        pipeline.updated_by = actor.username
        self.session.commit()

    def lock(self, pipeline_id: uuid.UUID) -> Pipeline:
        """Bloquea la fila del pipeline (encolar un run, reinicializar el delta)."""
        pipeline = self.session.scalars(
            select(Pipeline).where(Pipeline.id == pipeline_id).with_for_update()
        ).first()
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PPL-NOT-FOUND")
        return pipeline

    # -------------------------------------------------------------- validaciones
    def _source(self, source_id: uuid.UUID) -> SourceView:
        view = self.sources.view(source_id)
        if view.source.status == SourceStatus.DEPRECATED.value:
            msg = "El origen está obsoleto: no se pueden crear ni editar pipelines con él."
            raise issue("source_id", msg, "SX-PPL-SOURCE-DEPRECATED")
        return view

    def _destination(self, destination_id: uuid.UUID) -> Destination:
        destination = self.destinations.get(destination_id)
        if destination.status == DestinationStatus.INACTIVE.value:
            msg = "El destino está inactivo: actívelo antes de usarlo en un pipeline."
            raise issue("destination_id", msg, "SX-PPL-DESTINATION-INACTIVE")
        return destination

    def _check_options(
        self,
        options: LoadOptions,
        mode: ExtractionMode,
        source_view: SourceView,
        destination: Destination,
    ) -> LoadOptions:
        plugin = self.destinations.plugin(DestinationType(destination.type))
        try:
            check_load_options(options, plugin.supported_modes)
        except BusinessRuleError as exc:
            raise _prefixed(exc, lambda f: f"load_options.{f}") from None
        semantics = source_view.source.delta_semantics
        check_load_mode(mode, options.load_mode, DeltaSemantics(semantics) if semantics else None)
        schema = self.sources.effective_schema(source_view)
        if options.load_mode is LoadMode.UPSERT and not schema.keys:
            msg = "«Upsert» necesita campos clave en el origen."
            raise issue("load_options.load_mode", msg, "SX-PPL-KEYS-REQUIRED")
        try:
            resolve_columns(schema, options.column_mapping)
        except BusinessRuleError as exc:
            raise _prefixed(exc, lambda f: f"load_options.{f}") from None
        return options

    def _check_filters(
        self, raw: list[dict[str, Any]], source_view: SourceView
    ) -> list[dict[str, Any]]:
        try:
            conditions = [FilterCondition.from_dict(item) for item in raw]
        except KeyError, ValueError, TypeError:
            msg = "Cada filtro necesita campo, operador y valor."
            raise issue("filters", msg, "SX-PPL-FILTER-INVALID") from None
        detected = DetectedSchema.from_json(source_view.schema.columns)
        try:
            checked = check_filters(
                conditions,
                detected.schema,
                filterable=detected.filterable,
                odata_version=source_view.source.odata_version,
            )
        except BusinessRuleError as exc:
            raise _prefixed(exc, lambda f: f.replace("default_filters", "filters")) from None
        return [c.to_dict() for c in checked]

    def _ensure_delta_source_free(self, source: Source) -> None:
        """Un solo pipeline delta por entity set hasta que el spike confirme H6 y H18."""
        if ALLOW_SHARED_DELTA_SOURCE:
            return
        other = self.session.execute(
            select(Pipeline.code)
            .join(Source, Source.id == Pipeline.source_id)
            .where(
                Pipeline.deleted_at.is_(None),
                Pipeline.extraction_mode == ExtractionMode.DELTA.value,
                Source.sap_system_id == source.sap_system_id,
                Source.service_name == source.service_name,
                Source.entity_set == source.entity_set,
            )
            .limit(1)
        ).scalar()
        if other:
            msg = (
                f"El pipeline {other} ya lee el delta de este entity set. Todavía no se sabe si "
                "SAP admite dos suscripciones de delta sobre el mismo origen y usuario técnico "
                "(pendiente del spike): use carga completa o un segundo servicio en SAP."
            )
            raise issue("extraction_mode", msg, "SX-PPL-DELTA-SHARED-SOURCE")

    def _ensure_code_free(self, code: str) -> None:
        exists = self.session.scalar(
            select(func.count())
            .select_from(Pipeline)
            .where(Pipeline.code == code, Pipeline.deleted_at.is_(None))
        )
        if exists:
            raise self._code_taken(code)

    @staticmethod
    def _code_taken(code: str) -> ConflictError:
        msg = f"Ya existe un pipeline con el código «{code}»."
        return ConflictError(msg, code="SX-PPL-CODE-TAKEN")

    def _audit(
        self,
        action: str,
        pipeline: Pipeline,
        actor: Principal,
        request: RequestInfo,
        changes: dict[str, Any] | None,
        *,
        reason: str | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="pipeline",
                resource_id=str(pipeline.id),
                resource_name=pipeline.code,
                changes=changes or None,
                reason=reason,
            ),
            actor=actor.actor,
            request=request,
        )


def _prefixed(exc: BusinessRuleError, rename: Callable[[str], str]) -> BusinessRuleError:
    exc.errors = [FieldIssue(rename(e.field), e.message, e.code) for e in exc.errors]
    return exc


def _required(value: str, fieldname: str) -> str:
    text = value.strip()
    if not text:
        msg = "Campo obligatorio."
        raise issue(fieldname, msg, "SX-PPL-REQUIRED")
    return text


def _tags(tags: list[str]) -> list[str]:
    return sorted({t.strip() for t in tags if t.strip()})


def _snapshot(pipeline: Pipeline) -> dict[str, Any]:
    return snapshot(
        {
            "code": pipeline.code,
            "name": pipeline.name,
            "description": pipeline.description,
            "source_id": str(pipeline.source_id),
            "destination_id": str(pipeline.destination_id),
            "extraction_mode": pipeline.extraction_mode,
            "load_options": pipeline.load_options,
            "filters": pipeline.filters,
            "timeout_minutes": pipeline.timeout_minutes,
            "max_attempts": pipeline.max_attempts,
            "tags": list(pipeline.tags),
        }
    )


def destination_dependents(
    session: Session, destination_id: uuid.UUID
) -> list[DestinationDependent]:
    """Pipelines que usan un destino: impiden darlo de baja (plan 07)."""
    stmt = select(Pipeline).where(
        Pipeline.destination_id == destination_id, Pipeline.deleted_at.is_(None)
    )
    return [
        DestinationDependent(
            kind="pipeline",
            id=str(p.id),
            name=p.code,
            active=p.status != PipelineStatus.PAUSED.value,
        )
        for p in session.scalars(stmt)
    ]


def options_of(pipeline: Pipeline) -> LoadOptions:
    return load_options_from_json(pipeline.load_options)
