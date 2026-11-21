"""Casos de uso de pipelines (plan 08 §5 y §6): alta, edición, comprobación, estado y clonado.

Un pipeline es un origen hacia un destino. Las llamadas a SAP (comprobación previa, clonado
a otro sistema) se hacen sin transacción abierta; lo que se guarda va después, con su evento
de auditoría.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    DomainError,
    FieldIssue,
    NotFoundError,
    PermissionDeniedError,
)
from sapextract.core.pagination import PageParams
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, SchemaEvolution
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.destinations.application.toolkit import DestinationDependent
from sapextract.destinations.domain.model import (
    DestinationStatus,
    DestinationType,
    check_identifier,
    default_delete_handling,
    resolve_columns,
)
from sapextract.destinations.infrastructure.models import Destination
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.application.lifecycle import live_run
from sapextract.orchestration.application.schedule_service import reset_next_fire
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.application.trial import trial_outdated
from sapextract.orchestration.domain.model import (
    DEFAULT_PRIORITY,
    DEFAULT_TIMEOUT_MINUTES,
    ExtractionMode,
    Finding,
    LogLevel,
    PipelineFacts,
    PipelineStatus,
    RetryPolicy,
    Severity,
    check_priority,
    check_timeout,
    has_errors,
    issue,
    normalize_code,
    normalize_pool,
    review,
)
from sapextract.orchestration.infrastructure.models import (
    ConcurrencyGroup,
    Pipeline,
    Run,
    Schedule,
)
from sapextract.sap_connections.application.toolkit import Dependent
from sapextract.sap_connections.domain.model import SystemStatus, normalize_tags
from sapextract.sap_connections.infrastructure.models import SapSystem
from sapextract.sources.application.source_service import NewSource, SourceService
from sapextract.sources.domain.filters import FilterCondition, check_filters
from sapextract.sources.domain.model import (
    DeltaConfig,
    DeltaMethod,
    DeltaSemantics,
    SourceStatus,
    SourceType,
    WatermarkKind,
)
from sapextract.sources.domain.schema import DetectedSchema, SchemaDiff, diff_schemas, with_keys
from sapextract.sources.infrastructure.models import Source, SourceSchemaVersion

SORTABLE = {"code", "name", "status", "extraction_mode", "priority", "created_at", "updated_at"}
MIN_REASON = 10
PAUSE_DELTA_WARNING = (
    "Mientras el pipeline está en pausa la cola de delta sigue creciendo en SAP; "
    "una pausa larga puede obligar a reinicializar."
)


# ====================================================================== datos de entrada/salida
@dataclass(frozen=True, kw_only=True)
class LoadConfig:
    """Opciones de carga del pipeline (plan 07 §2). ``None`` = valor por defecto."""

    load_mode: LoadMode
    target_name: str | None = None  # por defecto «<sistema>_<origen>»
    create_if_missing: bool = True
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling | None = None
    technical_columns: bool = True
    column_mapping: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class NewPipeline:
    code: str
    name: str
    source_id: uuid.UUID
    destination_id: uuid.UUID
    extraction_mode: ExtractionMode
    load: LoadConfig
    description: str | None = None
    filters: list[FilterCondition] | None = None  # None: los filtros por defecto del origen
    priority: int = DEFAULT_PRIORITY
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    concurrency_group: str | None = None
    worker_pool: str | None = None
    log_level: LogLevel = LogLevel.INFO
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class PipelineChanges:
    """Campos a modificar; ``None`` = sin cambios."""

    version: int | None = None
    name: str | None = None
    description: str | None = None
    source_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    extraction_mode: ExtractionMode | None = None
    load: LoadConfig | None = None
    filters: list[FilterCondition] | None = None
    use_source_filters: bool = False  # True: vuelve a los filtros por defecto del origen
    priority: int | None = None
    timeout_minutes: int | None = None
    retry_policy: RetryPolicy | None = None
    concurrency_group: str | None = None  # "" quita el grupo
    worker_pool: str | None = None
    log_level: LogLevel | None = None
    tags: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class CloneRequest:
    code: str
    name: str | None = None
    sap_system_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    source_code: str | None = None  # código del origen nuevo si hay que crearlo en otro sistema


@dataclass(frozen=True, kw_only=True)
class PipelineFilters:
    q: str | None = None
    status: PipelineStatus | None = None
    source_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    sap_system_id: uuid.UUID | None = None
    environment: str | None = None
    tag: str | None = None


@dataclass(frozen=True, kw_only=True)
class PipelineView:
    pipeline: Pipeline
    source: Source
    system: SapSystem
    destination: Destination
    next_fire_at: datetime | None = None
    live_run: Run | None = None
    last_run: Run | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def cross_environment(self) -> bool:
        return self.system.environment != self.destination.environment


@dataclass(frozen=True)
class Check:
    code: str
    ok: bool
    message: str


@dataclass(frozen=True, kw_only=True)
class ActivationReport:
    """Resultado de la comprobación previa a activar (plan 08 §5)."""

    checks: list[Check]
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


@dataclass(frozen=True, kw_only=True)
class CloneResult:
    view: PipelineView
    source_created: bool
    schema_diff: SchemaDiff | None  # None si el origen es el mismo


# ====================================================================== servicio
class PipelineService:
    def __init__(self, session: Session, toolkit: OrchestrationToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.sources = SourceService(session, toolkit.sap)

    # -------------------------------------------------------------- consultas
    def search(self, filters: PipelineFilters, page: PageParams) -> tuple[list[PipelineView], int]:
        stmt = (
            select(Pipeline, Source, SapSystem, Destination)
            .join(Source, Source.id == Pipeline.source_id)
            .join(SapSystem, SapSystem.id == Source.sap_system_id)
            .join(Destination, Destination.id == Pipeline.destination_id)
            .where(Pipeline.deleted_at.is_(None))
        )
        if filters.q:
            like = f"%{filters.q.strip()}%"
            stmt = stmt.where(or_(Pipeline.code.ilike(like), Pipeline.name.ilike(like)))
        if filters.status:
            stmt = stmt.where(Pipeline.status == filters.status.value)
        if filters.source_id:
            stmt = stmt.where(Pipeline.source_id == filters.source_id)
        if filters.destination_id:
            stmt = stmt.where(Pipeline.destination_id == filters.destination_id)
        if filters.sap_system_id:
            stmt = stmt.where(Source.sap_system_id == filters.sap_system_id)
        if filters.environment:
            stmt = stmt.where(SapSystem.environment == filters.environment)
        if filters.tag:
            stmt = stmt.where(Pipeline.tags.contains(normalize_tags([filters.tag])))
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "code"
        name = sort.lstrip("-")
        column = getattr(Pipeline, name) if name in SORTABLE else Pipeline.code
        stmt = stmt.order_by(column.desc() if sort.startswith("-") else column.asc(), Pipeline.code)
        rows = self.session.execute(stmt.offset(page.offset).limit(page.page_size)).all()
        ids = [p.id for p, *_ in rows]
        next_fires, lives, lasts = self._next_fires(ids), self._live_runs(ids), self._last_runs(ids)
        views = [
            PipelineView(
                pipeline=p,
                source=s,
                system=y,
                destination=d,
                next_fire_at=next_fires.get(p.id),
                live_run=lives.get(p.id),
                last_run=lasts.get(p.id),
            )
            for p, s, y, d in rows
        ]
        return views, total

    def get(self, pipeline_id: uuid.UUID) -> Pipeline:
        pipeline = self.session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        return pipeline

    def view(self, pipeline_id: uuid.UUID) -> PipelineView:
        pipeline = self.get(pipeline_id)
        source, system, destination = self._parts(pipeline.source_id, pipeline.destination_id)
        ids = [pipeline.id]
        return PipelineView(
            pipeline=pipeline,
            source=source,
            system=system,
            destination=destination,
            next_fire_at=self._next_fires(ids).get(pipeline.id),
            live_run=self._live_runs(ids).get(pipeline.id),
            last_run=self._last_runs(ids).get(pipeline.id),
            findings=self._findings(pipeline, source, system, destination),
        )

    def _next_fires(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        if not ids:
            return {}
        stmt = (
            select(Schedule.pipeline_id, func.min(Schedule.next_fire_at))
            .where(
                Schedule.pipeline_id.in_(ids), Schedule.enabled, Schedule.next_fire_at.is_not(None)
            )
            .group_by(Schedule.pipeline_id)
        )
        rows = self.session.execute(stmt).all()
        return {row[0]: row[1] for row in rows if row[1] is not None}

    def _live_runs(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
        if not ids:
            return {}
        stmt = select(Run).where(Run.pipeline_id.in_(ids), Run.status.in_(("queued", "running")))
        return {r.pipeline_id: r for r in self.session.scalars(stmt)}

    def _last_runs(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
        """Último run terminado de cada pipeline."""
        if not ids:
            return {}
        latest = (
            select(Run.pipeline_id, func.max(Run.finished_at).label("finished_at"))
            .where(Run.pipeline_id.in_(ids), Run.finished_at.is_not(None))
            .group_by(Run.pipeline_id)
            .subquery()
        )
        stmt = select(Run).join(
            latest,
            (Run.pipeline_id == latest.c.pipeline_id) & (Run.finished_at == latest.c.finished_at),
        )
        return {r.pipeline_id: r for r in self.session.scalars(stmt)}

    # -------------------------------------------------------------- piezas y validación
    def _parts(
        self, source_id: uuid.UUID, destination_id: uuid.UUID
    ) -> tuple[Source, SapSystem, Destination]:
        source = self.session.get(Source, source_id)
        if source is None or source.deleted_at is not None:
            raise issue("source_id", "El origen no existe.", "SX-SRC-NOT-FOUND")
        system = self.session.get(SapSystem, source.sap_system_id)
        if system is None:  # la FK lo impide
            raise issue("source_id", "El sistema SAP del origen no existe.", "SX-SAP-NOT-FOUND")
        destination = self.session.get(Destination, destination_id)
        if destination is None or destination.deleted_at is not None:
            raise issue("destination_id", "El destino no existe.", "SX-DST-NOT-FOUND")
        return source, system, destination

    def _detected(self, source: Source) -> DetectedSchema:
        version = self.session.scalar(
            select(SourceSchemaVersion)
            .where(SourceSchemaVersion.source_id == source.id)
            .order_by(SourceSchemaVersion.version.desc())
            .limit(1)
        )
        if version is None:
            raise issue("source_id", "El origen no tiene esquema.", "SX-SRC-SCHEMA-NOT-FOUND")
        return DetectedSchema.from_json(version.columns)

    def _schema(self, source: Source) -> EntitySchema:
        """Esquema de SAP con las claves confirmadas en el origen (el que usará el motor)."""
        return with_keys(self._detected(source).schema, tuple(source.key_fields))

    def _load_options(
        self, load: LoadConfig, source: Source, system: SapSystem, schema: EntitySchema
    ) -> tuple[dict[str, Any], dict[str, str | None]]:
        target = (load.target_name or "").strip() or f"{system.code}_{source.code}"[:128]
        check_identifier(target, "load.target_name")
        resolve_columns(schema, load.column_mapping)  # nombres válidos, sin colisiones
        options = {
            "load_mode": load.load_mode.value,
            "target_name": target,
            "create_if_missing": load.create_if_missing,
            "schema_evolution": load.schema_evolution.value,
            "delete_handling": (
                load.delete_handling or default_delete_handling(load.load_mode)
            ).value,
            "technical_columns": load.technical_columns,
        }
        return options, dict(load.column_mapping)

    def _filters(
        self, filters: list[FilterCondition] | None, source: Source
    ) -> list[dict[str, Any]] | None:
        if filters is None:
            return None
        detected = self._detected(source)
        checked = check_filters(
            filters,
            detected.schema,
            filterable=detected.filterable,
            odata_version=source.odata_version,
        )
        return [f.to_dict() for f in checked]

    def _check_group(self, code: str | None) -> str | None:
        value = (code or "").strip().upper() or None
        if value is not None and self.session.get(ConcurrencyGroup, value) is None:
            msg = f"El grupo de concurrencia «{value}» no existe."
            raise issue("concurrency_group", msg, "SX-PIPE-GROUP-NOT-FOUND")
        return value

    def load_options(self, pipeline: Pipeline) -> LoadOptions:
        """Opciones de carga tal como las recibirá el destino (ADR 0006 §7)."""
        raw = pipeline.load_options
        return LoadOptions(
            load_mode=LoadMode(raw["load_mode"]),
            target_name=raw["target_name"],
            create_if_missing=bool(raw.get("create_if_missing", True)),
            schema_evolution=SchemaEvolution(raw.get("schema_evolution", "add_columns")),
            delete_handling=DeleteHandling(raw.get("delete_handling", "soft")),
            technical_columns=bool(raw.get("technical_columns", True)),
            column_mapping=dict(pipeline.column_mapping),
        )

    def _review(
        self,
        source: Source,
        system: SapSystem,
        destination: Destination,
        mode: ExtractionMode,
        load_mode: LoadMode,
        *,
        exclude: uuid.UUID | None,
    ) -> list[Finding]:
        others: tuple[str, ...] = ()
        if mode is ExtractionMode.DELTA:
            stmt = select(Pipeline.code).where(
                Pipeline.source_id == source.id,
                Pipeline.extraction_mode == ExtractionMode.DELTA.value,
                Pipeline.deleted_at.is_(None),
            )
            if exclude is not None:
                stmt = stmt.where(Pipeline.id != exclude)
            others = tuple(self.session.scalars(stmt.order_by(Pipeline.code)))
        plugin = self.tk.destinations.registry.get(DestinationType(destination.type))
        return review(
            PipelineFacts(
                mode=mode,
                load_mode=load_mode,
                supported_modes=plugin.supported_modes,
                delta_method=DeltaMethod(source.delta_method),
                delta_semantics=DeltaSemantics(source.delta_semantics)
                if source.delta_semantics
                else None,
                source_environment=system.environment,
                destination_environment=destination.environment,
                other_delta_pipelines=others,
            )
        )

    def review_draft(
        self,
        source_id: uuid.UUID,
        destination_id: uuid.UUID,
        mode: ExtractionMode,
        load_mode: LoadMode,
    ) -> list[Finding]:
        """Avisos de una combinación aún sin guardar (validación en vivo del asistente)."""
        source, system, destination = self._parts(source_id, destination_id)
        return self._review(source, system, destination, mode, load_mode, exclude=None)

    def _findings(
        self, pipeline: Pipeline, source: Source, system: SapSystem, destination: Destination
    ) -> list[Finding]:
        """Avisos y bloqueos de la configuración guardada, contra el esquema vigente."""
        findings = self._review(
            source,
            system,
            destination,
            ExtractionMode(pipeline.extraction_mode),
            LoadMode(pipeline.load_options["load_mode"]),
            exclude=pipeline.id,
        )
        # El esquema del origen pudo cambiar después de guardar el pipeline.
        try:
            detected = self._detected(source)
            resolve_columns(detected.schema, pipeline.column_mapping)
            if pipeline.filters is not None:
                check_filters(
                    [FilterCondition.from_dict(f) for f in pipeline.filters],
                    detected.schema,
                    filterable=detected.filterable,
                    odata_version=source.odata_version,
                )
        except BusinessRuleError as exc:
            fieldname = exc.errors[0].field if exc.errors else "source_id"
            findings.append(Finding(Severity.ERROR, exc.error_code, fieldname, exc.detail))
        return findings

    # -------------------------------------------------------------- alta
    def create(self, data: NewPipeline, actor: Principal, request: RequestInfo) -> PipelineView:
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        name = data.name.strip()
        if not name:
            raise issue("name", "Indique un nombre.", "SX-PIPE-NAME-REQUIRED")
        source, system, destination = self._parts(data.source_id, data.destination_id)
        options, mapping = self._load_options(data.load, source, system, self._schema(source))
        pipeline = Pipeline(
            code=code,
            name=name,
            description=(data.description or "").strip() or None,
            source_id=source.id,
            destination_id=destination.id,
            extraction_mode=data.extraction_mode.value,
            load_options=options,
            column_mapping=mapping,
            filters=self._filters(data.filters, source),
            priority=check_priority(data.priority),
            timeout_minutes=check_timeout(data.timeout_minutes),
            retry_policy=data.retry_policy.to_dict(),
            concurrency_group=self._check_group(data.concurrency_group),
            worker_pool=normalize_pool(data.worker_pool),
            log_level=data.log_level.value,
            status=PipelineStatus.DRAFT.value,
            tags=normalize_tags(data.tags),
            owner_id=actor.user_id,
            created_by=actor.username,
            updated_by=actor.username,
        )
        self.session.add(pipeline)
        self.session.flush()
        self._audit("pipeline.created", pipeline, actor, request, diff(None, _snapshot(pipeline)))
        self.session.commit()
        return self.view(pipeline.id)

    # -------------------------------------------------------------- edición
    def update(
        self,
        pipeline_id: uuid.UUID,
        changes: PipelineChanges,
        actor: Principal,
        request: RequestInfo,
    ) -> PipelineView:
        pipeline = self.get(pipeline_id)
        if changes.version is not None and changes.version != pipeline.version:
            msg = "El pipeline fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        before = _snapshot(pipeline)
        structural = (
            (changes.source_id is not None and changes.source_id != pipeline.source_id)
            or (
                changes.destination_id is not None
                and changes.destination_id != pipeline.destination_id
            )
            or (
                changes.extraction_mode is not None
                and changes.extraction_mode.value != pipeline.extraction_mode
            )
        )
        if structural and pipeline.status != PipelineStatus.DRAFT.value:
            msg = (
                "El origen, el destino y el modo de extracción solo se cambian en borrador. "
                "Clone el pipeline para probar otra combinación."
            )
            raise ConflictError(msg, code="SX-PIPE-NOT-DRAFT")
        source, system, destination = self._parts(
            changes.source_id or pipeline.source_id,
            changes.destination_id or pipeline.destination_id,
        )
        draft = pipeline.status == PipelineStatus.DRAFT.value
        known = (
            set() if draft else _error_codes(self._findings(pipeline, source, system, destination))
        )
        pipeline.source_id, pipeline.destination_id = source.id, destination.id
        self._apply(pipeline, changes, source, system)
        delta = diff(before, _snapshot(pipeline))
        if not delta:
            return self.view(pipeline.id)
        if not draft:
            # Un pipeline que ya puede ejecutarse no admite un cambio que lo deje bloqueado.
            # Los bloqueos que ya tenía (p. ej. el esquema cambió en SAP) no impiden editarlo.
            findings = self._findings(pipeline, source, system, destination)
            _raise_findings([f for f in findings if f.code not in known])
        pipeline.updated_by = actor.username
        pipeline.updated_at = self.tk.clock()
        self.session.flush()
        self._audit("pipeline.updated", pipeline, actor, request, delta)
        self.session.commit()
        return self.view(pipeline.id)

    def _apply(  # noqa: PLR0912
        self, pipeline: Pipeline, changes: PipelineChanges, source: Source, system: SapSystem
    ) -> None:
        """Copia al pipeline los campos indicados, ya validados."""
        if changes.extraction_mode is not None:
            pipeline.extraction_mode = changes.extraction_mode.value
        if changes.name is not None:
            if not changes.name.strip():
                raise issue("name", "Indique un nombre.", "SX-PIPE-NAME-REQUIRED")
            pipeline.name = changes.name.strip()
        if changes.description is not None:
            pipeline.description = changes.description.strip() or None
        if changes.load is not None:
            pipeline.load_options, pipeline.column_mapping = self._load_options(
                changes.load, source, system, self._schema(source)
            )
        if changes.use_source_filters:
            pipeline.filters = None
        elif changes.filters is not None:
            pipeline.filters = self._filters(changes.filters, source)
        if changes.priority is not None:
            pipeline.priority = check_priority(changes.priority)
        if changes.timeout_minutes is not None:
            pipeline.timeout_minutes = check_timeout(changes.timeout_minutes)
        if changes.retry_policy is not None:
            pipeline.retry_policy = changes.retry_policy.to_dict()
        if changes.concurrency_group is not None:
            pipeline.concurrency_group = self._check_group(changes.concurrency_group)
        if changes.worker_pool is not None:
            pipeline.worker_pool = normalize_pool(changes.worker_pool)
        if changes.log_level is not None:
            pipeline.log_level = changes.log_level.value
        if changes.tags is not None:
            pipeline.tags = normalize_tags(changes.tags)

    # -------------------------------------------------------------- comprobación previa
    def check(
        self, pipeline_id: uuid.UUID, cross_environment_reason: str | None = None
    ) -> ActivationReport:
        """Comprobación antes de activar: la misma en cualquier entorno (plan 08 §5).

        Lee el ``$metadata`` del servicio en su sistema SAP; no guarda nada.
        """
        pipeline = self.get(pipeline_id)
        source, system, destination = self._parts(pipeline.source_id, pipeline.destination_id)
        findings = self._findings(pipeline, source, system, destination)
        errors = [f for f in findings if f.severity is Severity.ERROR]
        checks = [
            Check(
                "configuration",
                not errors,
                "La configuración no tiene bloqueos."
                if not errors
                else " ".join(f.message for f in errors),
            ),
            _source_check(source),
            _system_check(system),
            _destination_check(destination),
            _trial_check(pipeline, source),
        ]
        origin = self._clone_origin_schema(pipeline)
        stored = self._schema(source)
        source_ref = (source.sap_system_id, source.service_name, source.entity_set)
        service_version = source.service_version
        # Desde aquí no se usa la sesión hasta terminar la llamada a SAP.
        live: EntitySchema | None = None
        try:
            inspection = self.sources.inspect(*source_ref, service_version)
            live = inspection.detected.schema
            published = Check("service_published", True, "El servicio responde en este sistema.")
        except DomainError as exc:
            published = Check("service_published", False, exc.detail)
        checks.append(published)
        if live is not None:
            drift = diff_schemas(stored, with_keys(live, stored.keys))
            checks.append(
                Check(
                    "schema_current",
                    not drift.breaking,
                    "El esquema guardado coincide con el de SAP."
                    if not drift.breaking
                    else "El esquema en SAP cambió de forma incompatible "
                    f"({_describe(drift)}): refresque el esquema del origen.",
                )
            )
        if origin is not None:
            code, schema = origin
            drift = diff_schemas(schema, stored)
            checks.append(
                Check(
                    "clone_schema",
                    not drift.breaking,
                    f"El esquema es compatible con el del pipeline de partida ({code})."
                    if not drift.breaking
                    else f"El esquema difiere del pipeline de partida {code} ({_describe(drift)}).",
                )
            )
        if system.environment != destination.environment:
            confirmed = len((cross_environment_reason or "").strip()) >= MIN_REASON
            checks.append(
                Check(
                    "cross_environment",
                    confirmed,
                    f"SAP {system.environment} hacia un destino {destination.environment}, "
                    "confirmado con motivo."
                    if confirmed
                    else f"El sistema SAP es {system.environment} y el destino es "
                    f"{destination.environment}: confírmelo con un motivo de al menos "
                    f"{MIN_REASON} caracteres.",
                )
            )
        return ActivationReport(checks=checks, findings=findings)

    def _clone_origin_schema(self, pipeline: Pipeline) -> tuple[str, EntitySchema] | None:
        if pipeline.cloned_from_id is None:
            return None
        origin = self.session.get(Pipeline, pipeline.cloned_from_id)
        if origin is None or origin.source_id == pipeline.source_id:
            return None
        origin_source = self.session.get(Source, origin.source_id)
        if origin_source is None:
            return None
        return origin.code, self._schema(origin_source)

    # -------------------------------------------------------------- estado
    def activate(
        self,
        pipeline_id: uuid.UUID,
        cross_environment_reason: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> PipelineView:
        pipeline = self.get(pipeline_id)
        if pipeline.status == PipelineStatus.PAUSED.value:
            msg = "El pipeline está en pausa: reanúdelo."
            raise ConflictError(msg, code="SX-PIPE-PAUSED")
        if pipeline.status == PipelineStatus.ACTIVE.value:
            return self.view(pipeline.id)
        reason = (cross_environment_reason or "").strip() or None
        report = self.check(pipeline_id, reason)
        if not report.ok:
            failed = [c for c in report.checks if not c.ok]
            msg = "El pipeline no pasó la comprobación previa: " + " ".join(
                c.message for c in failed
            )
            raise BusinessRuleError(
                msg,
                code="SX-PIPE-CHECK-FAILED",
                errors=[FieldIssue(c.code, c.message, "SX-PIPE-CHECK-FAILED") for c in failed],
            )
        pipeline = self.get(pipeline_id)  # la comprobación llamó a SAP: se recarga
        view = self.view(pipeline.id)
        now = self.tk.clock()
        pipeline.status = PipelineStatus.ACTIVE.value
        pipeline.status_reason = None
        pipeline.activated_at = now
        pipeline.cross_environment_reason = reason if view.cross_environment else None
        pipeline.updated_by, pipeline.updated_at = actor.username, now
        reset_next_fire(self.session, pipeline, now)
        self.session.flush()
        self._audit(
            "pipeline.activated",
            pipeline,
            actor,
            request,
            {"status": {"before": PipelineStatus.DRAFT.value, "after": pipeline.status}},
            reason=pipeline.cross_environment_reason,
            details={
                "checks": [c.code for c in report.checks],
                "cross_environment": view.cross_environment,
                "sap_environment": view.system.environment,
                "destination_environment": view.destination.environment,
            },
        )
        self.session.commit()
        return self.view(pipeline.id)

    def pause(
        self, pipeline_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> tuple[PipelineView, list[str]]:
        """Suspende la programación; el run en curso termina. Devuelve los avisos de la pausa."""
        pipeline = self.get(pipeline_id)
        if pipeline.status != PipelineStatus.ACTIVE.value:
            msg = "Solo se puede pausar un pipeline activo."
            raise ConflictError(msg, code="SX-PIPE-NOT-ACTIVE")
        self._set_status(
            pipeline,
            PipelineStatus.PAUSED,
            reason=reason,
            action="pipeline.paused",
            actor=actor,
            request=request,
        )
        warnings = (
            [PAUSE_DELTA_WARNING] if pipeline.extraction_mode == ExtractionMode.DELTA.value else []
        )
        return self.view(pipeline.id), warnings

    def resume(
        self, pipeline_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> PipelineView:
        pipeline = self.get(pipeline_id)
        if pipeline.status != PipelineStatus.PAUSED.value:
            msg = "Solo se puede reanudar un pipeline en pausa."
            raise ConflictError(msg, code="SX-PIPE-NOT-PAUSED")
        source, system, destination = self._parts(pipeline.source_id, pipeline.destination_id)
        failed = [
            c
            for c in (_source_check(source), _system_check(system), _destination_check(destination))
            if not c.ok
        ]
        if failed:
            msg = "No se puede reanudar: " + " ".join(c.message for c in failed)
            raise BusinessRuleError(msg, code="SX-PIPE-CHECK-FAILED")
        self._set_status(
            pipeline,
            PipelineStatus.ACTIVE,
            reason=None,
            action="pipeline.resumed",
            actor=actor,
            request=request,
        )
        return self.view(pipeline.id)

    def delete(
        self, pipeline_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> None:
        pipeline = self.get(pipeline_id)
        if live_run(self.session, pipeline.id) is not None:
            msg = "El pipeline tiene un run en cola o en curso: cancélelo antes de eliminarlo."
            raise ConflictError(msg, code="SX-PIPE-RUN-LIVE")
        self._audit(
            "pipeline.deleted",
            pipeline,
            actor,
            request,
            diff(_snapshot(pipeline), None),
            reason=reason,
        )
        self.session.execute(delete(Schedule).where(Schedule.pipeline_id == pipeline.id))
        if self.tk.hooks is not None:
            self.tk.hooks.pipeline_deleted(self.session, pipeline, actor)
        pipeline.deleted_at = self.tk.clock()
        pipeline.updated_by = actor.username
        self.session.commit()

    # -------------------------------------------------------------- clonado
    def clone(
        self, pipeline_id: uuid.UUID, data: CloneRequest, actor: Principal, request: RequestInfo
    ) -> CloneResult:
        """Copia sin estado de delta; admite otro sistema SAP y otro destino (DEV → QAS → PRD).

        En otro sistema vuelve a leer el ``$metadata`` y devuelve el diff de esquema contra el
        origen de partida. El clon queda en borrador: activarlo exige la comprobación previa.
        """
        original = self.get(pipeline_id)
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        source, system, _ = self._parts(original.source_id, original.destination_id)
        destination_id = data.destination_id or original.destination_id
        destination = self.session.get(Destination, destination_id)
        if destination is None or destination.deleted_at is not None:
            raise issue("destination_id", "El destino no existe.", "SX-DST-NOT-FOUND")
        origin_schema = self._schema(source)
        schedules = [_copy_schedule(s) for s in self._schedules(original.id)]
        fields = {
            "name": (data.name or "").strip() or f"{original.name} (copia)"[:200],
            "description": original.description,
            "extraction_mode": original.extraction_mode,
            "load_options": dict(original.load_options),
            "column_mapping": dict(original.column_mapping),
            "filters": list(original.filters) if original.filters is not None else None,
            "priority": original.priority,
            "timeout_minutes": original.timeout_minutes,
            "retry_policy": dict(original.retry_policy),
            "concurrency_group": original.concurrency_group,
            "worker_pool": original.worker_pool,
            "log_level": original.log_level,
            "tags": list(original.tags),
        }
        original_id, original_code = original.id, original.code

        target_source, created, schema_diff = source, False, None
        if data.sap_system_id is not None and data.sap_system_id != system.id:
            target_source, created = self._source_in_system(
                source, data.sap_system_id, data.source_code, actor, request
            )
            schema_diff = diff_schemas(origin_schema, self._schema(target_source))

        now = self.tk.clock()
        clone = Pipeline(
            code=code,
            source_id=target_source.id,
            destination_id=destination_id,
            status=PipelineStatus.DRAFT.value,
            owner_id=actor.user_id,
            cloned_from_id=original_id,
            created_by=actor.username,
            updated_by=actor.username,
            **fields,
        )
        self.session.add(clone)
        self.session.flush()
        for values in schedules:
            self.session.add(
                Schedule(pipeline_id=clone.id, created_at=now, updated_at=now, **values)
            )
        self._audit(
            "pipeline.cloned",
            clone,
            actor,
            request,
            diff(None, _snapshot(clone)),
            details={
                "cloned_from": original_code,
                "source_created": created,
                "schema_breaking": schema_diff.breaking if schema_diff else False,
            },
        )
        self.session.commit()
        return CloneResult(
            view=self.view(clone.id), source_created=created, schema_diff=schema_diff
        )

    def _schedules(self, pipeline_id: uuid.UUID) -> list[Schedule]:
        return list(
            self.session.scalars(select(Schedule).where(Schedule.pipeline_id == pipeline_id))
        )

    def _source_in_system(
        self,
        source: Source,
        system_id: uuid.UUID,
        source_code: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> tuple[Source, bool]:
        """Origen equivalente en otro sistema SAP: el que ya exista o uno nuevo leído de SAP."""
        target = self.session.get(SapSystem, system_id)
        if target is None or target.deleted_at is not None:
            raise issue("sap_system_id", "El sistema SAP no existe.", "SX-SAP-NOT-FOUND")
        existing = self.session.scalar(
            select(Source)
            .where(
                Source.sap_system_id == system_id,
                Source.service_name == source.service_name,
                Source.service_version == source.service_version,
                Source.entity_set == source.entity_set,
                Source.deleted_at.is_(None),
            )
            .order_by(Source.code)
            .limit(1)
        )
        if existing is not None:
            return existing, False
        if not actor.has("source:manage"):
            msg = (
                f"El sistema {target.code} aún no tiene un origen para {source.entity_set} "
                "y crearlo exige el permiso source:manage."
            )
            raise PermissionDeniedError(msg, code="SX-AUTH-FORBIDDEN")
        view = self.sources.create(
            NewSource(
                sap_system_id=system_id,
                code=source_code or f"{source.code}_{target.code}"[:40],
                name=source.name,
                description=source.description,
                tags=list(source.tags),
                service_name=source.service_name,
                service_version=source.service_version,
                entity_set=source.entity_set,
                source_type=SourceType(source.source_type),
                key_fields=list(source.key_fields),
                delta=_delta_config(source),
                default_filters=[FilterCondition.from_dict(f) for f in source.default_filters],
                page_size=source.page_size,
                preview_enabled=source.preview_enabled,
            ),
            actor,
            request,
        )
        return view.source, True

    # -------------------------------------------------------------- utilidades
    def _set_status(
        self,
        pipeline: Pipeline,
        status: PipelineStatus,
        *,
        reason: str | None,
        action: str,
        actor: Principal,
        request: RequestInfo,
    ) -> None:
        before = pipeline.status
        now = self.tk.clock()
        pipeline.status = status.value
        pipeline.status_reason = (reason or "").strip() or None
        pipeline.updated_by, pipeline.updated_at = actor.username, now
        reset_next_fire(self.session, pipeline, now)
        self.session.flush()
        self._audit(
            action,
            pipeline,
            actor,
            request,
            {"status": {"before": before, "after": status.value}},
            reason=pipeline.status_reason,
        )
        self.session.commit()

    def _ensure_code_free(self, code: str) -> None:
        taken = self.session.scalar(
            select(Pipeline.id).where(Pipeline.code == code, Pipeline.deleted_at.is_(None))
        )
        if taken is not None:
            msg = f"Ya existe un pipeline con el código «{code}»."
            raise ConflictError(msg, code="SX-PIPE-CODE-TAKEN")

    def _audit(
        self,
        action: str,
        pipeline: Pipeline,
        actor: Principal,
        request: RequestInfo,
        changes: dict[str, Any],
        *,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
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
                details=details or {},
            ),
            actor=actor.actor,
            request=request,
        )


# ====================================================================== funciones auxiliares
def _error_codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings if f.severity is Severity.ERROR}


def _raise_findings(findings: list[Finding]) -> None:
    errors = [f for f in findings if f.severity is Severity.ERROR]
    if has_errors(findings):
        raise BusinessRuleError(
            " ".join(f.message for f in errors),
            code=errors[0].code,
            errors=[FieldIssue(f.field, f.message, f.code) for f in errors],
        )


def _source_check(source: Source) -> Check:
    ok = source.deleted_at is None and source.status == SourceStatus.ACTIVE.value
    return Check(
        "source_active",
        ok,
        "El origen está activo." if ok else f"El origen {source.code} no está activo.",
    )


def _system_check(system: SapSystem) -> Check:
    ok = (
        system.deleted_at is None
        and system.status == SystemStatus.ACTIVE.value
        and bool(system.last_test_ok)
        and not system.test_outdated
    )
    return Check(
        "sap_system_test",
        ok,
        "El sistema SAP está activo y su prueba de conexión sigue vigente."
        if ok
        else f"El sistema SAP {system.code} no está activo o su prueba de conexión no está "
        "vigente: pruébelo de nuevo.",
    )


def _destination_check(destination: Destination) -> Check:
    ok = (
        destination.deleted_at is None
        and destination.status == DestinationStatus.ACTIVE.value
        and bool(destination.last_test_ok)
        and not destination.test_outdated
    )
    return Check(
        "destination_test",
        ok,
        "El destino está activo y su prueba sigue vigente."
        if ok
        else f"El destino {destination.code} no está activo o su prueba no está vigente: "
        "pruébelo de nuevo.",
    )


def _trial_check(pipeline: Pipeline, source: Source) -> Check:
    """Ejecución de prueba correcta y de la configuración actual (plan 08 §5, punto 3)."""
    code = "test_run"
    if not source.preview_enabled:
        # Sin lectura de muestra (H16) no hay forma de probar: no se exige.
        msg = "El origen tiene desactivada la lectura de muestra: la prueba no se exige."
        return Check(code, True, msg)
    if pipeline.last_test_at is None:
        return Check(code, False, "El pipeline aún no se ha probado: pruébelo antes de activar.")
    if trial_outdated(pipeline, source):
        msg = "La configuración cambió después de la última prueba: pruebe el pipeline de nuevo."
        return Check(code, False, msg)
    if not pipeline.last_test_ok:
        msg = "La última prueba del pipeline falló: corrija el problema y pruébelo de nuevo."
        return Check(code, False, msg)
    return Check(code, True, "La prueba del pipeline fue correcta y sigue vigente.")


def _describe(drift: SchemaDiff) -> str:
    parts = []
    if drift.removed:
        parts.append("faltan " + ", ".join(c.name for c in drift.removed[:5]))
    changed = [c.name for c in drift.changed if c.breaking]
    if changed:
        parts.append("cambian " + ", ".join(changed[:5]))
    return "; ".join(parts) or "cambios incompatibles"


def _delta_config(source: Source) -> DeltaConfig:
    return DeltaConfig(
        method=DeltaMethod(source.delta_method),
        semantics=DeltaSemantics(source.delta_semantics) if source.delta_semantics else None,
        watermark_field=source.watermark_field,
        watermark_kind=WatermarkKind(source.watermark_kind) if source.watermark_kind else None,
        watermark_timezone=source.watermark_timezone,
        watermark_overlap_seconds=source.watermark_overlap_seconds,
    )


def _copy_schedule(schedule: Schedule) -> dict[str, Any]:
    return {
        "type": schedule.type,
        "cron_expression": schedule.cron_expression,
        "interval_minutes": schedule.interval_minutes,
        "timezone": schedule.timezone,
        "start_at": schedule.start_at,
        "end_at": schedule.end_at,
        "blackout_windows": list(schedule.blackout_windows),
        "enabled": schedule.enabled,
        "misfire_policy": schedule.misfire_policy,
    }


def _snapshot(pipeline: Pipeline) -> dict[str, Any]:
    return snapshot(
        {
            "code": pipeline.code,
            "name": pipeline.name,
            "description": pipeline.description,
            "source_id": str(pipeline.source_id),
            "destination_id": str(pipeline.destination_id),
            "extraction_mode": pipeline.extraction_mode,
            "load_options": dict(pipeline.load_options),
            "column_mapping": dict(pipeline.column_mapping),
            "filters": pipeline.filters,
            "priority": pipeline.priority,
            "timeout_minutes": pipeline.timeout_minutes,
            "retry_policy": dict(pipeline.retry_policy),
            "concurrency_group": pipeline.concurrency_group,
            "worker_pool": pipeline.worker_pool,
            "log_level": pipeline.log_level,
            "tags": list(pipeline.tags),
            "status": pipeline.status,
        }
    )


# ---------------------------------------------------------------------- dependencias
def _pipelines_of(session: Session, *conditions: Any) -> list[Pipeline]:
    stmt = select(Pipeline).where(Pipeline.deleted_at.is_(None), *conditions)
    return list(session.scalars(stmt.order_by(Pipeline.code)))


def sap_system_dependents(session: Session, system_id: uuid.UUID) -> list[Dependent]:
    """Pipelines que leen de un sistema SAP: los activos impiden darlo de baja."""
    sources = select(Source.id).where(Source.sap_system_id == system_id)
    return [
        Dependent(
            kind="pipeline",
            id=str(p.id),
            name=p.code,
            active=p.status == PipelineStatus.ACTIVE.value,
        )
        for p in _pipelines_of(session, Pipeline.source_id.in_(sources))
    ]


def source_dependents(session: Session, source_id: uuid.UUID) -> list[Dependent]:
    """Pipelines de un origen: cualquiera, también en borrador, impide eliminarlo."""
    return [
        Dependent(kind="pipeline", id=str(p.id), name=p.code, active=True)
        for p in _pipelines_of(session, Pipeline.source_id == source_id)
    ]


def destination_dependents(
    session: Session, destination_id: uuid.UUID
) -> list[DestinationDependent]:
    """Pipelines que cargan en un destino: los activos impiden eliminarlo."""
    return [
        DestinationDependent(
            kind="pipeline",
            id=str(p.id),
            name=p.code,
            active=p.status == PipelineStatus.ACTIVE.value,
        )
        for p in _pipelines_of(session, Pipeline.destination_id == destination_id)
    ]
