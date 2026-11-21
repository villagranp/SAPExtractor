"""Casos de uso de los orígenes SAP: inspección, alta, edición, estado, esquema y vista previa.

Las llamadas a SAP se hacen sin transacción abierta (el explorador confirma la sesión antes de
llamar); lo que se guarda va después, junto con su evento de auditoría.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from sapextract.core.pagination import PageParams
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sap_connections.application.explorer_service import SapExplorerService
from sapextract.sap_connections.application.system_service import SapSystemService
from sapextract.sap_connections.application.toolkit import Dependent, SapToolkit
from sapextract.sap_connections.domain.catalog import CatalogService
from sapextract.sap_connections.domain.errors import SapError
from sapextract.sap_connections.domain.metadata import EdmEntitySet, OdpRole
from sapextract.sap_connections.domain.model import SystemStatus, normalize_tags
from sapextract.sap_connections.infrastructure.models import SapSystem
from sapextract.sap_connections.infrastructure.odata_client import SapODataClient
from sapextract.sources.domain.delta_processes import SemanticsHint, hint_for
from sapextract.sources.domain.filters import FilterCondition, check_filters, render_filter
from sapextract.sources.domain.model import (
    CONTEXT_OF,
    ODP_TYPES,
    DeltaConfig,
    DeltaMethod,
    DeltaSemantics,
    SourceStatus,
    SourceType,
    check_delta,
    check_keys,
    check_transition,
    check_type,
    issue,
    normalize_code,
    normalize_page_size,
    suggest_type,
)
from sapextract.sources.domain.preview import PREVIEW_ROWS, PreviewData, display_rows
from sapextract.sources.domain.schema import (
    DetectedSchema,
    SchemaDiff,
    column_names,
    detect_schema,
    diff_schemas,
    with_keys,
)
from sapextract.sources.infrastructure.models import Source, SourceSchemaVersion

PREVIEW_TIMEOUT_SECONDS = 30
MAX_PREVIEW_PAGES = 10
SORTABLE = {"code", "name", "source_type", "status", "created_at", "updated_at"}
_WATERMARK_EDM = frozenset({"Edm.DateTime", "Edm.DateTimeOffset", "Edm.Date", "Edm.Decimal"})


# ====================================================================== datos de entrada/salida
@dataclass(frozen=True, kw_only=True)
class Inspection:
    """Lo que el asistente necesita saber de un entity set antes de crear el origen."""

    system: SapSystem
    service: CatalogService
    entity_set: EdmEntitySet
    odata_version: str
    odp_name: str | None
    suggested_type: SourceType
    supports_delta: bool
    detected: DetectedSchema
    semantics_hint: SemanticsHint | None
    watermark_candidates: list[str]

    @property
    def odp_role(self) -> OdpRole | None:
        return self.entity_set.odp_role


@dataclass(frozen=True, kw_only=True)
class NewSource:
    sap_system_id: uuid.UUID
    code: str
    name: str
    service_name: str
    entity_set: str
    service_version: int = 1
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    source_type: SourceType | None = None  # None: el sugerido por el entity set
    key_fields: list[str] | None = None  # None: las claves del $metadata
    delta: DeltaConfig | None = None  # None: delta ODQ si el servicio lo admite
    default_filters: list[FilterCondition] = field(default_factory=list)
    page_size: int | None = None
    preview_enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class SourceChanges:
    version: int | None = None
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    key_fields: list[str] | None = None
    delta: DeltaConfig | None = None
    default_filters: list[FilterCondition] | None = None
    page_size: int | None = None
    preview_enabled: bool | None = None


@dataclass(frozen=True, kw_only=True)
class SourceFilters:
    q: str | None = None
    sap_system_id: uuid.UUID | None = None
    source_type: SourceType | None = None
    status: SourceStatus | None = None
    delta_method: DeltaMethod | None = None


@dataclass(frozen=True, kw_only=True)
class SourceView:
    source: Source
    system: SapSystem
    schema: SourceSchemaVersion


@dataclass(frozen=True, kw_only=True)
class RefreshResult:
    view: SourceView
    changed: bool
    previous_version: int
    diff: SchemaDiff


# ====================================================================== servicio
class SourceService:
    def __init__(self, session: Session, toolkit: SapToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.systems = SapSystemService(session, toolkit)
        self.explorer = SapExplorerService(session, toolkit)

    # -------------------------------------------------------------- consultas
    def search(self, filters: SourceFilters, page: PageParams) -> tuple[list[SourceView], int]:
        stmt = (
            select(Source, SapSystem)
            .join(SapSystem, SapSystem.id == Source.sap_system_id)
            .where(Source.deleted_at.is_(None))
        )
        if filters.q:
            like = f"%{filters.q.strip()}%"
            stmt = stmt.where(
                or_(
                    Source.code.ilike(like),
                    Source.name.ilike(like),
                    Source.odp_name.ilike(like),
                    Source.service_name.ilike(like),
                    Source.entity_set.ilike(like),
                )
            )
        if filters.sap_system_id:
            stmt = stmt.where(Source.sap_system_id == filters.sap_system_id)
        if filters.source_type:
            stmt = stmt.where(Source.source_type == filters.source_type.value)
        if filters.status:
            stmt = stmt.where(Source.status == filters.status.value)
        if filters.delta_method:
            stmt = stmt.where(Source.delta_method == filters.delta_method.value)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "code"
        name = sort.lstrip("-")
        column = getattr(Source, name) if name in SORTABLE else Source.code
        stmt = stmt.order_by(column.desc() if sort.startswith("-") else column.asc(), Source.code)
        rows = self.session.execute(stmt.offset(page.offset).limit(page.page_size)).all()
        latest = self._latest_versions([s.id for s, _ in rows])
        return [SourceView(source=s, system=sys, schema=latest[s.id]) for s, sys in rows], total

    def get(self, source_id: uuid.UUID) -> Source:
        source = self.session.get(Source, source_id)
        if source is None or source.deleted_at is not None:
            msg = "El origen no existe."
            raise NotFoundError(msg, code="SX-SRC-NOT-FOUND")
        return source

    def view(self, source_id: uuid.UUID) -> SourceView:
        source = self.get(source_id)
        system = self.session.get(SapSystem, source.sap_system_id)
        if system is None:  # la FK lo impide; el sistema puede estar dado de baja, no borrado
            msg = "El sistema SAP del origen no existe."
            raise NotFoundError(msg, code="SX-SAP-SYSTEM-NOT-FOUND")
        return SourceView(source=source, system=system, schema=self._latest(source.id))

    def effective_schema(self, view: SourceView) -> EntitySchema:
        """Esquema de SAP con las claves confirmadas en el origen (el que usará el motor)."""
        detected = DetectedSchema.from_json(view.schema.columns).schema
        return with_keys(detected, tuple(view.source.key_fields))

    def versions(self, source_id: uuid.UUID) -> list[SourceSchemaVersion]:
        self.get(source_id)
        stmt = (
            select(SourceSchemaVersion)
            .where(SourceSchemaVersion.source_id == source_id)
            .order_by(SourceSchemaVersion.version.desc())
        )
        return list(self.session.scalars(stmt))

    def diff(self, source_id: uuid.UUID, from_version: int, to_version: int) -> SchemaDiff:
        versions = {v.version: v for v in self.versions(source_id)}
        missing = [v for v in (from_version, to_version) if v not in versions]
        if missing:
            msg = f"No existe la versión {missing[0]} del esquema."
            raise NotFoundError(msg, code="SX-SRC-SCHEMA-VERSION-NOT-FOUND")
        old = DetectedSchema.from_json(versions[from_version].columns).schema
        new = DetectedSchema.from_json(versions[to_version].columns).schema
        return diff_schemas(old, new)

    # -------------------------------------------------------------- asistente: inspección
    def inspect(
        self, system_id: uuid.UUID, service_name: str, entity_set: str, service_version: int = 1
    ) -> Inspection:
        """Lee el ``$metadata`` y propone tipo, claves, delta y campos de marca de agua."""
        system = self.systems.get(system_id)
        detail = self.explorer.service_detail(system_id, service_name, service_version)
        info = next((e for e in detail.entity_sets if e.entity_set.name == entity_set), None)
        if info is None:
            msg = (
                f"El servicio {detail.service.technical_name} no tiene el entity set {entity_set}."
            )
            raise issue("entity_set", msg, "SX-SRC-ENTITY-SET-NOT-FOUND")
        if info.entity_type is None:
            msg = f"El $metadata no describe el tipo de {entity_set}."
            raise issue("entity_set", msg, "SX-SRC-ENTITY-SET-INVALID")
        if info.odp_role is OdpRole.DELTA_LINKS:
            check_type(SourceType.CDS_ODATA, info.odp_role)  # lanza el error explicativo
        detected = detect_schema(info.entity_type)
        candidates = [
            c.name
            for c in detected.schema.columns
            if c.edm_type in _WATERMARK_EDM and c.name not in ("ODQ_ENTITYCNTR",)
        ]
        return Inspection(
            system=system,
            service=detail.service,
            entity_set=info.entity_set,
            odata_version=detail.metadata.odata_version,
            odp_name=info.odp_name,
            suggested_type=suggest_type(info.odp_role),
            supports_delta=info.supports_delta,
            detected=detected,
            semantics_hint=hint_for(info.odp_name),
            watermark_candidates=candidates,
        )

    # -------------------------------------------------------------- alta
    def create(self, data: NewSource, actor: Principal, request: RequestInfo) -> SourceView:
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        name = data.name.strip()
        if not name:
            raise issue("name", "Indique un nombre.", "SX-SRC-NAME-REQUIRED")
        inspection = self.inspect(
            data.sap_system_id, data.service_name, data.entity_set, data.service_version
        )
        system = inspection.system
        if system.status != SystemStatus.ACTIVE.value:
            msg = "El sistema SAP debe estar activo (con una prueba de conexión correcta)."
            raise issue("sap_system_id", msg, "SX-SRC-SYSTEM-INACTIVE")

        source_type = data.source_type or inspection.suggested_type
        check_type(source_type, inspection.odp_role)
        schema = inspection.detected.schema
        keys = check_keys(tuple(data.key_fields or schema.keys), schema)
        delta = check_delta(
            data.delta or _default_delta(source_type, inspection),
            source_type=source_type,
            supports_delta=inspection.supports_delta,
            schema=schema,
        )
        filters = check_filters(
            data.default_filters,
            schema,
            filterable=inspection.detected.filterable,
            odata_version=inspection.odata_version,
        )
        service = inspection.service
        source = Source(
            sap_system_id=system.id,
            code=code,
            name=name,
            description=(data.description or "").strip() or None,
            source_type=source_type.value,
            odp_context=CONTEXT_OF[source_type].value if source_type in ODP_TYPES else None,
            odp_name=inspection.odp_name,
            service_name=service.technical_name,
            service_version=service.version,
            service_path=service.service_path or "",
            entity_set=inspection.entity_set.name,
            odata_version=inspection.odata_version,
            supports_delta=inspection.supports_delta,
            key_fields=list(keys),
            default_filters=[f.to_dict() for f in filters],
            page_size=normalize_page_size(data.page_size),
            preview_enabled=data.preview_enabled,
            tags=normalize_tags(data.tags),
            status=SourceStatus.DRAFT.value,
            schema_checked_at=utcnow(),
            created_by=actor.username,
            updated_by=actor.username,
        )
        _apply_delta(source, delta)
        if not source.service_path:
            msg = "El catálogo no informa la ruta del servicio."
            raise issue("service_name", msg, "SX-SRC-SERVICE-PATH")
        self.session.add(source)
        self.session.flush()
        version = self._add_version(source, inspection.detected, 1, actor)
        self._audit("source.created", source, actor, request, diff(None, _snapshot(source)))
        self.session.commit()
        return SourceView(source=source, system=system, schema=version)

    # -------------------------------------------------------------- edición
    def update(
        self, source_id: uuid.UUID, changes: SourceChanges, actor: Principal, request: RequestInfo
    ) -> SourceView:
        view = self.view(source_id)
        source = view.source
        if changes.version is not None and changes.version != source.version:
            msg = "El origen fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        before = _snapshot(source)
        detected = DetectedSchema.from_json(view.schema.columns)
        schema = detected.schema
        if changes.name is not None:
            if not changes.name.strip():
                raise issue("name", "Indique un nombre.", "SX-SRC-NAME-REQUIRED")
            source.name = changes.name.strip()
        if changes.description is not None:
            source.description = changes.description.strip() or None
        if changes.tags is not None:
            source.tags = normalize_tags(changes.tags)
        if changes.key_fields is not None:
            source.key_fields = list(check_keys(tuple(changes.key_fields), schema))
        if changes.delta is not None:
            delta = check_delta(
                changes.delta,
                source_type=SourceType(source.source_type),
                supports_delta=source.supports_delta,
                schema=schema,
            )
            _apply_delta(source, delta)
        if changes.default_filters is not None:
            filters = check_filters(
                changes.default_filters,
                schema,
                filterable=detected.filterable,
                odata_version=source.odata_version,
            )
            source.default_filters = [f.to_dict() for f in filters]
        if changes.page_size is not None:
            source.page_size = normalize_page_size(changes.page_size)
        if changes.preview_enabled is not None:
            source.preview_enabled = changes.preview_enabled
        delta_changes = diff(before, _snapshot(source))
        if not delta_changes:
            return view
        source.updated_by = actor.username
        source.updated_at = utcnow()
        self.session.flush()
        self._audit("source.updated", source, actor, request, delta_changes)
        self.session.commit()
        return self.view(source.id)

    # -------------------------------------------------------------- estado y baja
    def activate(self, source_id: uuid.UUID, actor: Principal, request: RequestInfo) -> SourceView:
        view = self.view(source_id)
        _require_active_system(view.system)
        return self._set_status(
            view,
            SourceStatus.ACTIVE,
            reason=None,
            action="source.activated",
            actor=actor,
            request=request,
        )

    def deprecate(
        self, source_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> SourceView:
        view = self.view(source_id)
        return self._set_status(
            view,
            SourceStatus.DEPRECATED,
            reason=reason,
            action="source.deprecated",
            actor=actor,
            request=request,
        )

    def delete(
        self, source_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> None:
        source = self.get(source_id)
        self._ensure_unused(source)
        self._audit(
            "source.deleted", source, actor, request, diff(_snapshot(source), None), reason=reason
        )
        source.deleted_at = utcnow()
        source.status = SourceStatus.DEPRECATED.value
        source.updated_by = actor.username
        self.session.commit()

    def _ensure_unused(self, source: Source) -> None:
        """Un origen con pipelines vivos no se puede dar de baja (plan 05 §1)."""
        codes = list(
            self.session.scalars(
                select(Pipeline.code).where(
                    Pipeline.source_id == source.id, Pipeline.deleted_at.is_(None)
                )
            )
        )
        if codes:
            msg = f"El origen lo usan los pipelines {', '.join(sorted(codes))}: elimínelos antes."
            raise ConflictError(msg, code="SX-SRC-IN-USE")

    # -------------------------------------------------------------- esquema
    def refresh_schema(
        self, source_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> RefreshResult:
        """Relee el ``$metadata``: si el hash cambia, crea una versión nueva y devuelve el diff."""
        view = self.view(source_id)
        _require_active_system(view.system)
        source = view.source
        inspection = self.inspect(
            source.sap_system_id, source.service_name, source.entity_set, source.service_version
        )
        source = self.get(source_id)  # el explorador confirmó la sesión: se recarga
        latest = self._latest(source.id)
        detected = inspection.detected
        old = DetectedSchema.from_json(latest.columns).schema
        changes = diff_schemas(old, detected.schema)
        # display_format no entra en schema_hash() pero cambia el tipo (fecha o fecha y hora):
        # también cuenta como cambio si lo detecta el diff.
        changed = detected.schema.schema_hash() != latest.hash or not changes.empty
        if changed:
            latest = self._add_version(source, detected, latest.version + 1, actor)
        else:
            latest.columns = detected.to_json()  # p. ej. sap:filterable, que no entra en el hash
        source.schema_checked_at = utcnow()
        self.session.flush()
        self._audit(
            "source.schema_refreshed",
            source,
            actor,
            request,
            {"schema_version": {"old": view.schema.version, "new": latest.version}}
            if changed
            else {},
            details={
                "changed": changed,
                "breaking": changes.breaking,
                "added": [c.name for c in changes.added],
                "removed": [c.name for c in changes.removed],
                "modified": [c.name for c in changes.changed],
            },
        )
        self.session.commit()
        system = self.systems.get(source.sap_system_id)
        return RefreshResult(
            view=SourceView(source=source, system=system, schema=latest),
            changed=changed,
            previous_version=view.schema.version,
            diff=changes,
        )

    # -------------------------------------------------------------- vista previa
    def preview(self, source_id: uuid.UUID, actor: Principal, request: RequestInfo) -> PreviewData:
        """Primeras filas **sin** ``Prefer: odata.track-changes``: no abre suscripción en la ODQ."""
        view = self.view(source_id)
        _require_active_system(view.system)
        source = view.source
        if not source.preview_enabled:
            msg = "La vista previa está desactivada para este origen."
            raise BusinessRuleError(msg, code="SX-SRC-PREVIEW-DISABLED")
        schema = self.effective_schema(view)
        filters = [FilterCondition.from_dict(f) for f in source.default_filters]
        flt = render_filter(filters, schema, source.odata_version)
        spec = self.systems.spec_for(view.system)
        spec = replace(spec, timeout_seconds=min(spec.timeout_seconds, PREVIEW_TIMEOUT_SECONDS))
        snapshot_id, code, odp = source.id, source.code, source.source_type
        path = f"{source.service_path.rstrip('/')}/{source.entity_set}"
        self.session.commit()  # sin transacción abierta durante la llamada a SAP

        params = {"$top": str(PREVIEW_ROWS)} | ({"$filter": flt} if flt else {})
        started = time.perf_counter()
        raw: list[dict[str, Any]] = []
        with self.tk.client_factory(spec, self.tk.explorer_retry) as client:
            # Gateway puede paginar en el servidor y devolver menos filas que $top por página.
            url: str | None = path
            page_params: dict[str, str] | None = params
            for _ in range(MAX_PREVIEW_PAGES):
                if url is None or len(raw) >= PREVIEW_ROWS:
                    break
                rows_page, url = _page(client.get_json(url, params=page_params))
                raw += rows_page
                page_params = None  # el enlace siguiente ya trae la consulta completa
            count = self._count(client, path, flt) if odp == SourceType.CDS_ODATA else None
        elapsed = int((time.perf_counter() - started) * 1000)
        rows = display_rows(schema, raw[:PREVIEW_ROWS])

        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="source.previewed",
                resource_type="source",
                resource_id=str(snapshot_id),
                resource_name=code,
                details={"rows": len(rows), "filtered": bool(flt), "elapsed_ms": elapsed},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return PreviewData(
            columns=list(column_names(schema)),
            rows=rows,
            truncated=len(raw) >= PREVIEW_ROWS,
            count=count,
            elapsed_ms=elapsed,
        )

    @staticmethod
    def _count(client: SapODataClient, path: str, flt: str) -> int | None:
        """``$count`` solo en CDS vía OData: en ODP podría lanzar la extracción completa (H16)."""
        try:
            response = client.get(
                f"{path}/$count", params={"$filter": flt} if flt else None, json_format=False
            )
            return int(response.content.decode().strip())
        except SapError, ValueError:
            return None

    # -------------------------------------------------------------- utilidades
    def _set_status(
        self,
        view: SourceView,
        status: SourceStatus,
        *,
        reason: str | None,
        action: str,
        actor: Principal,
        request: RequestInfo,
    ) -> SourceView:
        source = view.source
        current = SourceStatus(source.status)
        check_transition(current, status)
        source.status = status.value
        source.status_reason = (reason or "").strip() or None
        source.updated_by = actor.username
        source.updated_at = utcnow()
        self.session.flush()
        self._audit(
            action,
            source,
            actor,
            request,
            {"status": {"old": current.value, "new": status.value}},
            reason=reason,
        )
        self.session.commit()
        return self.view(source.id)

    def _add_version(
        self, source: Source, detected: DetectedSchema, number: int, actor: Principal
    ) -> SourceSchemaVersion:
        version = SourceSchemaVersion(
            source_id=source.id,
            version=number,
            columns=detected.to_json(),
            hash=detected.schema.schema_hash(),
            detected_at=utcnow(),
            detected_by=actor.username,
        )
        self.session.add(version)
        self.session.flush()
        return version

    def _latest(self, source_id: uuid.UUID) -> SourceSchemaVersion:
        stmt = (
            select(SourceSchemaVersion)
            .where(SourceSchemaVersion.source_id == source_id)
            .order_by(SourceSchemaVersion.version.desc())
            .limit(1)
        )
        version = self.session.scalar(stmt)
        if version is None:
            msg = "El origen no tiene esquema."
            raise NotFoundError(msg, code="SX-SRC-SCHEMA-NOT-FOUND")
        return version

    def _latest_versions(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, SourceSchemaVersion]:
        if not ids:
            return {}
        newest = (
            select(
                SourceSchemaVersion.source_id,
                func.max(SourceSchemaVersion.version).label("version"),
            )
            .where(SourceSchemaVersion.source_id.in_(ids))
            .group_by(SourceSchemaVersion.source_id)
            .subquery()
        )
        stmt = select(SourceSchemaVersion).join(
            newest,
            (SourceSchemaVersion.source_id == newest.c.source_id)
            & (SourceSchemaVersion.version == newest.c.version),
        )
        return {v.source_id: v for v in self.session.scalars(stmt)}

    def _ensure_code_free(self, code: str) -> None:
        taken = self.session.scalar(
            select(Source.id).where(Source.code == code, Source.deleted_at.is_(None))
        )
        if taken is not None:
            msg = f"Ya existe un origen con el código «{code}»."
            raise ConflictError(msg, code="SX-SRC-CODE-TAKEN")

    def _audit(
        self,
        action: str,
        source: Source,
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
                resource_type="source",
                resource_id=str(source.id),
                resource_name=source.code,
                changes=changes or None,
                reason=reason,
                details=details or {},
            ),
            actor=actor.actor,
            request=request,
        )


# ====================================================================== funciones auxiliares
def _default_delta(source_type: SourceType, inspection: Inspection) -> DeltaConfig:
    if source_type in ODP_TYPES and inspection.supports_delta:
        hint = inspection.semantics_hint
        return DeltaConfig(
            method=DeltaMethod.ODQ,
            semantics=hint.semantics if hint else DeltaSemantics.UNKNOWN,
        )
    return DeltaConfig(method=DeltaMethod.NONE)


def _require_active_system(system: SapSystem) -> None:
    """Un sistema desactivado o dado de baja no se usa para leer de SAP ni para activar."""
    if system.deleted_at is not None or system.status != SystemStatus.ACTIVE.value:
        msg = "El sistema SAP del origen no está activo."
        raise BusinessRuleError(msg, code="SX-SRC-SYSTEM-INACTIVE")


def _apply_delta(source: Source, delta: DeltaConfig) -> None:
    source.delta_method = delta.method.value
    source.delta_semantics = delta.semantics.value if delta.semantics else None
    source.watermark_field = delta.watermark_field
    source.watermark_kind = delta.watermark_kind.value if delta.watermark_kind else None
    source.watermark_timezone = delta.watermark_timezone
    source.watermark_overlap_seconds = delta.watermark_overlap_seconds


def _page(body: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Filas y enlace siguiente de una respuesta V2 (``d.results``) o V4 (``value``)."""
    d = body.get("d")
    if isinstance(d, dict):
        rows, nxt = d.get("results", []), d.get("__next")
    elif isinstance(d, list):
        rows, nxt = d, None
    else:
        rows, nxt = body.get("value", []), body.get("@odata.nextLink")
    clean = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return clean, nxt if isinstance(nxt, str) else None


def _snapshot(source: Source) -> dict[str, Any]:
    return snapshot(
        {
            "code": source.code,
            "name": source.name,
            "description": source.description,
            "source_type": source.source_type,
            "service": f"{source.service_name};v={source.service_version}",
            "entity_set": source.entity_set,
            "delta_method": source.delta_method,
            "delta_semantics": source.delta_semantics,
            "watermark_field": source.watermark_field,
            "watermark_kind": source.watermark_kind,
            "watermark_timezone": source.watermark_timezone,
            "watermark_overlap_seconds": source.watermark_overlap_seconds,
            "key_fields": list(source.key_fields),
            "default_filters": list(source.default_filters),
            "page_size": source.page_size,
            "preview_enabled": source.preview_enabled,
            "tags": list(source.tags),
            "status": source.status,
        }
    )


def sap_system_dependents(session: Session, system_id: uuid.UUID) -> list[Dependent]:
    """Orígenes que usan un sistema SAP: los no obsoletos impiden darlo de baja."""
    stmt = select(Source).where(Source.sap_system_id == system_id, Source.deleted_at.is_(None))
    return [
        Dependent(
            kind="origen",
            id=str(s.id),
            name=s.code,
            active=s.status != SourceStatus.DEPRECATED.value,
        )
        for s in session.scalars(stmt)
    ]


__all__ = [
    "Inspection",
    "NewSource",
    "RefreshResult",
    "SourceChanges",
    "SourceFilters",
    "SourceService",
    "SourceView",
    "sap_system_dependents",
]
