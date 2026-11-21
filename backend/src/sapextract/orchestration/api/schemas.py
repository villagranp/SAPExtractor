"""Esquemas de la API de orquestación (plan 08 §7): pipelines, programación, runs y workers."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field

from sapextract.dataflow.domain.batch import LoadMode, RunMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.dataflow.domain.ports import DeleteHandling, SchemaEvolution
from sapextract.destinations.domain.model import DestinationEnvironment, DestinationType
from sapextract.destinations.infrastructure.models import Destination
from sapextract.orchestration.application.pipeline_service import (
    ActivationReport,
    CloneResult,
    LoadConfig,
    PipelineView,
)
from sapextract.orchestration.application.run_service import GroupView, RunView
from sapextract.orchestration.application.schedule_service import ScheduleInput
from sapextract.orchestration.domain.model import (
    DEFAULT_PRIORITY,
    DEFAULT_TIMEOUT_MINUTES,
    AttemptOutcome,
    ExtractionMode,
    Finding,
    LogLevel,
    PipelineStatus,
    RetryPolicy,
    RunStatus,
    RunTrigger,
    Severity,
    WorkerStatus,
)
from sapextract.orchestration.domain.schedule import (
    BlackoutKind,
    BlackoutWindow,
    MisfirePolicy,
    ScheduleType,
    SkipReason,
)
from sapextract.orchestration.infrastructure.models import Run, RunAttempt, Schedule, Worker
from sapextract.sources.api.schemas import (
    ColumnChangeResponse,
    DiffColumnResponse,
    FilterConditionBody,
    FilterConditionResponse,
    SourceSystemRef,
)
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics
from sapextract.sources.domain.schema import SchemaDiff
from sapextract.sources.infrastructure.models import Source

# ------------------------------------------------------------------ peticiones: pipeline


class LoadBody(BaseModel):
    load_mode: LoadMode
    target_name: str | None = Field(
        None, max_length=128, description="Tabla o prefijo de archivo; por defecto sistema_origen"
    )
    create_if_missing: bool = True
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling | None = Field(
        None, description="Por defecto «soft» con upsert e «ignore» en el resto"
    )
    technical_columns: bool = True
    column_mapping: dict[str, str | None] = Field(
        default_factory=dict, description="Columna SAP → nombre en destino; null la excluye"
    )

    def to_domain(self) -> LoadConfig:
        return LoadConfig(**self.model_dump())


class RetryPolicyBody(BaseModel):
    max_attempts: int = Field(3, ge=1, le=10)
    backoff_minutes: list[int] = Field(
        default_factory=lambda: [1, 5, 15], min_length=1, max_length=10
    )
    jitter: bool = True

    def to_domain(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts,
            backoff_minutes=tuple(self.backoff_minutes),
            jitter=self.jitter,
        )


class PipelineCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    source_id: uuid.UUID
    destination_id: uuid.UUID
    extraction_mode: ExtractionMode
    load: LoadBody
    filters: list[FilterConditionBody] | None = Field(
        None, max_length=20, description="Si se omiten, los filtros por defecto del origen"
    )
    priority: int = Field(DEFAULT_PRIORITY, ge=1, le=5, description="1 es la más urgente")
    timeout_minutes: int = Field(DEFAULT_TIMEOUT_MINUTES, ge=1, le=2880)
    retry_policy: RetryPolicyBody = Field(default_factory=RetryPolicyBody)
    concurrency_group: str | None = Field(None, max_length=30)
    worker_pool: str | None = Field(None, max_length=30)
    log_level: LogLevel = LogLevel.INFO
    tags: list[str] = Field(default_factory=list, max_length=20)


class PipelineUpdateRequest(BaseModel):
    version: int | None = Field(None, description="Versión leída (control de concurrencia)")
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    source_id: uuid.UUID | None = Field(None, description="Solo en borrador")
    destination_id: uuid.UUID | None = Field(None, description="Solo en borrador")
    extraction_mode: ExtractionMode | None = Field(None, description="Solo en borrador")
    load: LoadBody | None = None
    filters: list[FilterConditionBody] | None = Field(None, max_length=20)
    use_source_filters: bool = Field(
        False, description="Vuelve a los filtros por defecto del origen"
    )
    priority: int | None = Field(None, ge=1, le=5)
    timeout_minutes: int | None = Field(None, ge=1, le=2880)
    retry_policy: RetryPolicyBody | None = None
    concurrency_group: str | None = Field(None, max_length=30, description="'' quita el grupo")
    worker_pool: str | None = Field(None, max_length=30)
    log_level: LogLevel | None = None
    tags: list[str] | None = Field(None, max_length=20)


class PipelineReviewRequest(BaseModel):
    source_id: uuid.UUID
    destination_id: uuid.UUID
    extraction_mode: ExtractionMode
    load_mode: LoadMode


class PipelineActivateRequest(BaseModel):
    cross_environment_reason: str | None = Field(
        None,
        max_length=500,
        description="Obligatorio si el entorno del sistema SAP y el del destino no coinciden",
    )


class PipelineReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PipelineCloneRequest(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    name: str | None = Field(None, max_length=200)
    sap_system_id: uuid.UUID | None = Field(None, description="Otro sistema SAP (DEV → QAS → PRD)")
    destination_id: uuid.UUID | None = None
    source_code: str | None = Field(
        None, max_length=40, description="Código del origen si hay que crearlo en ese sistema"
    )


class RunTriggerRequest(BaseModel):
    mode: RunMode | None = Field(
        None, description="FULL sobre un pipeline incremental lanza una recarga completa"
    )
    reason: str | None = Field(None, max_length=500)


# ------------------------------------------------------------------ peticiones: programación


class BlackoutWindowBody(BaseModel):
    kind: BlackoutKind
    label: str | None = Field(None, max_length=100)
    days: list[int] = Field(
        default_factory=list, max_length=7, description="Semanal: 0 = lunes … 6 = domingo"
    )
    start: time | None = Field(None, description="Semanal: hora local de inicio (HH:MM)")
    end: time | None = Field(
        None, description="Semanal: hora local de fin; puede ser del día siguiente"
    )
    start_at: datetime | None = Field(None, description="Por fechas: inicio")
    end_at: datetime | None = Field(None, description="Por fechas: fin")

    def to_domain(self) -> BlackoutWindow:
        return BlackoutWindow(
            kind=self.kind,
            label=(self.label or "").strip() or None,
            days=tuple(sorted(set(self.days))),
            start=self.start.replace(tzinfo=None) if self.start else None,
            end=self.end.replace(tzinfo=None) if self.end else None,
            start_at=self.start_at,
            end_at=self.end_at,
        )


class ScheduleBody(BaseModel):
    type: ScheduleType
    cron_expression: str | None = Field(None, max_length=120)
    interval_minutes: int | None = Field(None, ge=1, le=44_640)
    timezone: str | None = Field(
        None, max_length=60, description="Zona IANA; por defecto la de la plataforma"
    )
    start_at: datetime | None = None
    end_at: datetime | None = None
    blackout_windows: list[BlackoutWindowBody] = Field(default_factory=list, max_length=20)
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP
    enabled: bool = True

    def to_domain(self) -> ScheduleInput:
        return ScheduleInput(
            type=self.type,
            timezone=self.timezone,
            cron_expression=self.cron_expression,
            interval_minutes=self.interval_minutes,
            start_at=self.start_at,
            end_at=self.end_at,
            blackout_windows=tuple(w.to_domain() for w in self.blackout_windows),
            misfire_policy=self.misfire_policy,
            enabled=self.enabled,
        )


class SchedulesPutRequest(BaseModel):
    schedules: list[ScheduleBody] = Field(max_length=5)


class SchedulePreviewRequest(ScheduleBody):
    count: int = Field(10, ge=1, le=50)


class ConcurrencyGroupPutRequest(BaseModel):
    max_parallel: int = Field(ge=1, le=64)
    description: str | None = Field(None, max_length=500)


# ------------------------------------------------------------------ respuestas


class FindingResponse(BaseModel):
    severity: Severity = Field(description="«error» impide activar")
    code: str
    field: str
    message: str

    @classmethod
    def list_of(cls, findings: list[Finding]) -> list[FindingResponse]:
        return [
            cls(severity=f.severity, code=f.code, field=f.field, message=f.message)
            for f in findings
        ]


class PipelineSourceRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    delta_method: DeltaMethod
    delta_semantics: DeltaSemantics | None

    @classmethod
    def of(cls, s: Source) -> PipelineSourceRef:
        return cls(
            id=s.id,
            code=s.code,
            name=s.name,
            delta_method=DeltaMethod(s.delta_method),
            delta_semantics=DeltaSemantics(s.delta_semantics) if s.delta_semantics else None,
        )


class PipelineDestinationRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    type: DestinationType
    environment: DestinationEnvironment

    @classmethod
    def of(cls, d: Destination) -> PipelineDestinationRef:
        return cls(
            id=d.id,
            code=d.code,
            name=d.name,
            type=DestinationType(d.type),
            environment=DestinationEnvironment(d.environment),
        )


class RunBrief(BaseModel):
    id: uuid.UUID
    mode: RunMode
    status: RunStatus
    trigger: RunTrigger
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None

    @classmethod
    def of(cls, r: Run | None) -> RunBrief | None:
        if r is None:
            return None
        return cls(
            id=r.id,
            mode=RunMode(r.mode),
            status=RunStatus(r.status),
            trigger=RunTrigger(r.trigger),
            created_at=r.created_at,
            started_at=r.started_at,
            finished_at=r.finished_at,
            error_code=r.error_code,
        )


class PipelineSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: PipelineStatus
    extraction_mode: ExtractionMode
    load_mode: LoadMode
    source: PipelineSourceRef
    sap_system: SourceSystemRef
    destination: PipelineDestinationRef
    cross_environment: bool = Field(
        description="El entorno del sistema SAP y el del destino no coinciden"
    )
    priority: int
    worker_pool: str
    concurrency_group: str | None
    tags: list[str]
    next_fire_at: datetime | None
    live_run: RunBrief | None = Field(description="Ejecución en cola o en curso")
    last_run: RunBrief | None = Field(description="Última ejecución terminada")
    updated_at: datetime

    @classmethod
    def of(cls, v: PipelineView) -> PipelineSummary:
        p = v.pipeline
        return cls(
            id=p.id,
            code=p.code,
            name=p.name,
            status=PipelineStatus(p.status),
            extraction_mode=ExtractionMode(p.extraction_mode),
            load_mode=LoadMode(p.load_options["load_mode"]),
            source=PipelineSourceRef.of(v.source),
            sap_system=SourceSystemRef.of(v.system),
            destination=PipelineDestinationRef.of(v.destination),
            cross_environment=v.cross_environment,
            priority=p.priority,
            worker_pool=p.worker_pool,
            concurrency_group=p.concurrency_group,
            tags=list(p.tags),
            next_fire_at=v.next_fire_at,
            live_run=RunBrief.of(v.live_run),
            last_run=RunBrief.of(v.last_run),
            updated_at=p.updated_at,
        )


class LoadResponse(BaseModel):
    load_mode: LoadMode
    target_name: str
    create_if_missing: bool
    schema_evolution: SchemaEvolution
    delete_handling: DeleteHandling
    technical_columns: bool
    column_mapping: dict[str, str | None]


class RetryPolicyResponse(BaseModel):
    max_attempts: int
    backoff_minutes: list[int]
    jitter: bool


class PipelineDetail(PipelineSummary):
    description: str | None
    load: LoadResponse
    filters: list[FilterConditionResponse] | None = Field(
        description="null: se usan los filtros por defecto del origen"
    )
    timeout_minutes: int
    retry_policy: RetryPolicyResponse
    log_level: LogLevel
    status_reason: str | None
    cross_environment_reason: str | None
    cloned_from_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    activated_at: datetime | None
    created_at: datetime
    created_by: str | None
    updated_by: str | None
    version: int
    findings: list[FindingResponse] = Field(description="Avisos y bloqueos de la configuración")

    @classmethod
    def of_view(cls, v: PipelineView) -> PipelineDetail:
        p = v.pipeline
        return cls(
            **PipelineSummary.of(v).model_dump(),
            description=p.description,
            load=LoadResponse(**p.load_options, column_mapping=dict(p.column_mapping)),
            filters=[FilterConditionResponse.model_validate(f) for f in p.filters]
            if p.filters is not None
            else None,
            timeout_minutes=p.timeout_minutes,
            retry_policy=RetryPolicyResponse(**RetryPolicy.from_dict(p.retry_policy).to_dict()),
            log_level=LogLevel(p.log_level),
            status_reason=p.status_reason,
            cross_environment_reason=p.cross_environment_reason,
            cloned_from_id=p.cloned_from_id,
            owner_id=p.owner_id,
            activated_at=p.activated_at,
            created_at=p.created_at,
            created_by=p.created_by,
            updated_by=p.updated_by,
            version=p.version,
            findings=FindingResponse.list_of(v.findings),
        )


class CheckResponse(BaseModel):
    code: str
    ok: bool
    message: str


class ActivationReportResponse(BaseModel):
    ok: bool = Field(description="El pipeline puede activarse")
    checks: list[CheckResponse]
    findings: list[FindingResponse]

    @classmethod
    def of(cls, r: ActivationReport) -> ActivationReportResponse:
        return cls(
            ok=r.ok,
            checks=[CheckResponse(code=c.code, ok=c.ok, message=c.message) for c in r.checks],
            findings=FindingResponse.list_of(r.findings),
        )


class PauseResponse(BaseModel):
    pipeline: PipelineDetail
    warnings: list[str]


class CloneSchemaDiff(BaseModel):
    breaking: bool = Field(description="Columna eliminada o cambio incompatible")
    added: list[DiffColumnResponse]
    removed: list[DiffColumnResponse]
    changed: list[ColumnChangeResponse]

    @classmethod
    def of(cls, d: SchemaDiff | None) -> CloneSchemaDiff | None:
        if d is None:
            return None

        def col(c: Any) -> DiffColumnResponse:
            return DiffColumnResponse(
                name=c.name, edm_type=c.edm_type, is_key=c.is_key, label=c.label
            )

        return cls(
            breaking=d.breaking,
            added=[col(c) for c in d.added],
            removed=[col(c) for c in d.removed],
            changed=[
                ColumnChangeResponse(
                    name=c.name, before=c.before, after=c.after, breaking=c.breaking
                )
                for c in d.changed
            ],
        )


class CloneResponse(BaseModel):
    pipeline: PipelineDetail
    source_created: bool = Field(description="Se creó un origen nuevo en el sistema elegido")
    schema_diff: CloneSchemaDiff | None = Field(
        description="Diferencias de esquema contra el origen de partida (otro sistema SAP)"
    )

    @classmethod
    def of(cls, r: CloneResult) -> CloneResponse:
        return cls(
            pipeline=PipelineDetail.of_view(r.view),
            source_created=r.source_created,
            schema_diff=CloneSchemaDiff.of(r.schema_diff),
        )


class ScheduleResponse(BaseModel):
    id: uuid.UUID
    type: ScheduleType
    cron_expression: str | None
    interval_minutes: int | None
    timezone: str
    start_at: datetime | None
    end_at: datetime | None
    blackout_windows: list[dict[str, Any]]
    misfire_policy: MisfirePolicy
    enabled: bool
    next_fire_at: datetime | None
    last_fire_at: datetime | None
    last_skip_at: datetime | None
    last_skip_reason: SkipReason | None

    @classmethod
    def of(cls, s: Schedule) -> ScheduleResponse:
        return cls(
            id=s.id,
            type=ScheduleType(s.type),
            cron_expression=s.cron_expression,
            interval_minutes=s.interval_minutes,
            timezone=s.timezone,
            start_at=s.start_at,
            end_at=s.end_at,
            blackout_windows=list(s.blackout_windows),
            misfire_policy=MisfirePolicy(s.misfire_policy),
            enabled=s.enabled,
            next_fire_at=s.next_fire_at,
            last_fire_at=s.last_fire_at,
            last_skip_at=s.last_skip_at,
            last_skip_reason=SkipReason(s.last_skip_reason) if s.last_skip_reason else None,
        )


class SchedulePreviewResponse(BaseModel):
    timezone: str
    fires: list[datetime] = Field(description="Próximas ejecuciones, sin las que caen en exclusión")


class RunPipelineRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class RunSummary(BaseModel):
    id: uuid.UUID
    pipeline: RunPipelineRef
    mode: RunMode
    status: RunStatus
    trigger: RunTrigger
    triggered_by: str | None
    reason: str | None
    priority: int
    created_at: datetime
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    error_kind: ErrorKind | None
    error_message: str | None

    @classmethod
    def of(cls, v: RunView) -> RunSummary:
        r, p = v.run, v.pipeline
        return cls(
            id=r.id,
            pipeline=RunPipelineRef(id=p.id, code=p.code, name=p.name),
            mode=RunMode(r.mode),
            status=RunStatus(r.status),
            trigger=RunTrigger(r.trigger),
            triggered_by=r.triggered_by,
            reason=r.reason,
            priority=r.priority,
            created_at=r.created_at,
            available_at=r.available_at,
            started_at=r.started_at,
            finished_at=r.finished_at,
            cancel_requested_at=r.cancel_requested_at,
            error_code=r.error_code,
            error_kind=ErrorKind(r.error_kind) if r.error_kind else None,
            error_message=r.error_message,
        )


class AttemptResponse(BaseModel):
    number: int
    worker_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    outcome: AttemptOutcome | None = Field(description="null: intento en curso")
    error_code: str | None
    error_kind: ErrorKind | None
    error_message: str | None

    @classmethod
    def of(cls, a: RunAttempt) -> AttemptResponse:
        return cls(
            number=a.number,
            worker_id=a.worker_id,
            started_at=a.started_at,
            finished_at=a.finished_at,
            outcome=AttemptOutcome(a.outcome) if a.outcome else None,
            error_code=a.error_code,
            error_kind=ErrorKind(a.error_kind) if a.error_kind else None,
            error_message=a.error_message,
        )


class RunDetail(RunSummary):
    attempts: list[AttemptResponse]
    attempts_budget: int = Field(description="Intentos fallidos que el run puede gastar")
    claimed_by: uuid.UUID | None
    heartbeat_at: datetime | None
    cancel_requested_by: str | None
    batch_id: str | None
    delta_generation: int | None
    start_token: str | None
    window_start: datetime | None
    window_end: datetime | None

    @classmethod
    def of_view(cls, v: RunView) -> RunDetail:
        r = v.run
        return cls(
            **RunSummary.of(v).model_dump(),
            attempts=[AttemptResponse.of(a) for a in v.attempts],
            attempts_budget=r.attempts_budget,
            claimed_by=r.claimed_by,
            heartbeat_at=r.heartbeat_at,
            cancel_requested_by=r.cancel_requested_by,
            batch_id=r.batch_id,
            delta_generation=r.delta_generation,
            start_token=r.start_token,
            window_start=r.window_start,
            window_end=r.window_end,
        )


class WorkerResponse(BaseModel):
    id: uuid.UUID
    hostname: str
    pid: int
    pools: list[str]
    status: WorkerStatus
    has_executor: bool = Field(description="false: el worker está vivo pero no toma ejecuciones")
    current_run_id: uuid.UUID | None
    started_at: datetime
    heartbeat_at: datetime

    @classmethod
    def of(cls, w: Worker) -> WorkerResponse:
        return cls(
            id=w.id,
            hostname=w.hostname,
            pid=w.pid,
            pools=list(w.pools),
            status=WorkerStatus(w.status),
            has_executor=w.has_executor,
            current_run_id=w.current_run_id,
            started_at=w.started_at,
            heartbeat_at=w.heartbeat_at,
        )


class ConcurrencyGroupResponse(BaseModel):
    code: str
    description: str | None
    max_parallel: int
    pipelines: int = Field(description="Pipelines que pertenecen al grupo")
    running: int = Field(description="Ejecuciones del grupo en curso")
    updated_at: datetime
    updated_by: str | None

    @classmethod
    def of(cls, v: GroupView) -> ConcurrencyGroupResponse:
        g = v.group
        return cls(
            code=g.code,
            description=g.description,
            max_parallel=g.max_parallel,
            pipelines=v.pipelines,
            running=v.running,
            updated_at=g.updated_at,
            updated_by=g.updated_by,
        )
