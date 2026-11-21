"""Del pipeline guardado a lo que ejecuta el motor: conector, escritor, esquema y opciones."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings
from sapextract.core.exceptions import NotFoundError
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    DeleteHandling,
    DestinationWriter,
    LoadOptions,
    SchemaEvolution,
)
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.destinations.application.destination_service import DestinationService
from sapextract.destinations.application.plugins import DestinationPlugin, PluginContext
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.destinations.domain.model import DestinationType
from sapextract.extraction.domain.model import filters_hash
from sapextract.extraction.infrastructure.odata_connector import (
    ODataSourceConnector,
    ODataSourceRef,
    WatermarkSpec,
)
from sapextract.orchestration.domain.model import ExtractionMode
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sap_connections.domain.model import ConnectionSpec
from sapextract.sap_connections.infrastructure.odata_client import NO_RETRY
from sapextract.sources.application.source_service import SourceService
from sapextract.sources.domain.filters import FilterCondition, render_filter
from sapextract.sources.domain.model import DeltaSemantics, WatermarkKind
from sapextract.sources.infrastructure.models import Source

type ConnectorFactory = Callable[[ODataSourceRef, ConnectionSpec], ODataSourceConnector]


@dataclass(frozen=True, kw_only=True)
class ExtractionToolkit:
    settings: Settings
    audit: AuditService
    sap: SapToolkit
    destinations: DestinationsToolkit
    connector_factory: ConnectorFactory

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        sap: SapToolkit,
        destinations: DestinationsToolkit,
        audit: AuditService | None = None,
        connector_factory: ConnectorFactory | None = None,
    ) -> ExtractionToolkit:
        def default_factory(ref: ODataSourceRef, spec: ConnectionSpec) -> ODataSourceConnector:
            # Sin reintento HTTP en ninguna lectura del motor (plan 06 §3.2, C-05).
            page_spec = replace(spec, timeout_seconds=settings.page_timeout_seconds)
            return ODataSourceConnector(ref, lambda: sap.client_factory(page_spec, NO_RETRY))

        return cls(
            settings=settings,
            audit=audit or sap.audit,
            sap=sap,
            destinations=destinations,
            connector_factory=connector_factory or default_factory,
        )


@dataclass(frozen=True, kw_only=True)
class ResolvedPipeline:
    pipeline_id: uuid.UUID
    code: str
    mode: ExtractionMode
    schema: EntitySchema
    rendered_filter: str
    page_size: int
    semantics: DeltaSemantics | None
    connector: ODataSourceConnector
    plugin: DestinationPlugin
    config: BaseModel
    secret: BaseModel | None
    plugin_context: PluginContext
    options: LoadOptions
    watermark: WatermarkSpec | None
    watermark_overlap_seconds: int
    destination_label: str

    @property
    def filters_hash(self) -> str:
        return filters_hash(self.rendered_filter)

    def open_writer(self) -> AbstractContextManager[DestinationWriter]:
        return self.plugin.open_writer(self.config, self.secret, self.plugin_context)


def load_options(pipeline: Pipeline) -> LoadOptions:
    """Opciones de carga tal como las recibe el destino (``column_mapping`` va aparte)."""
    raw: dict[str, Any] = pipeline.load_options
    return LoadOptions(
        load_mode=LoadMode(raw["load_mode"]),
        target_name=str(raw["target_name"]),
        create_if_missing=bool(raw.get("create_if_missing", True)),
        schema_evolution=SchemaEvolution(raw.get("schema_evolution", "add_columns")),
        delete_handling=DeleteHandling(raw.get("delete_handling", "soft")),
        technical_columns=bool(raw.get("technical_columns", True)),
        column_mapping=dict(pipeline.column_mapping or {}),
    )


def _conditions(pipeline: Pipeline, source: Source) -> list[FilterCondition]:
    """Los filtros del pipeline sustituyen a los del origen; sin ellos, los del origen."""
    raw = source.default_filters if pipeline.filters is None else pipeline.filters
    return [FilterCondition.from_dict(f) for f in raw]


def pipeline_filter(session: Session, pipeline: Pipeline, sap: SapToolkit) -> tuple[str, str]:
    """``$filter`` efectivo y su huella."""
    sources = SourceService(session, sap)
    view = sources.view(pipeline.source_id)
    schema = sources.effective_schema(view)
    rendered = render_filter(_conditions(pipeline, view.source), schema, view.source.odata_version)
    return rendered, filters_hash(rendered)


def resolve(session: Session, pipeline_id: uuid.UUID, tk: ExtractionToolkit) -> ResolvedPipeline:
    """Carga el pipeline y prepara conector y destino. Errores de configuración, no transitorios."""
    pipeline = session.get(Pipeline, pipeline_id)
    if pipeline is None or pipeline.deleted_at is not None:
        msg = "El pipeline no existe."
        raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
    sources = SourceService(session, tk.sap)
    view = sources.view(pipeline.source_id)
    source = view.source
    if view.system.status != "active":
        msg = f"El sistema SAP {view.system.code} no está activo."
        raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-SAP-SYSTEM-INACTIVE")
    schema = sources.effective_schema(view)
    rendered = render_filter(_conditions(pipeline, source), schema, source.odata_version)
    watermark = (
        WatermarkSpec(
            field=source.watermark_field,
            kind=WatermarkKind(source.watermark_kind),
            timezone=source.watermark_timezone or "UTC",
        )
        if source.watermark_field and source.watermark_kind
        else None
    )
    ref = ODataSourceRef(
        service_path=source.service_path,
        entity_set=source.entity_set,
        odata_version=source.odata_version,
        schema=schema,
        supports_delta=source.supports_delta,
        base_filter=rendered,
        watermark=watermark,
    )
    spec = sources.systems.spec_for(view.system)
    destinations = DestinationService(session, tk.destinations)
    destination = destinations.get(pipeline.destination_id)
    if destination.status != "active":
        msg = f"El destino {destination.code} no está activo."
        raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-INACTIVE")
    plugin = destinations.plugin(DestinationType(destination.type))
    return ResolvedPipeline(
        pipeline_id=pipeline.id,
        code=pipeline.code,
        mode=ExtractionMode(pipeline.extraction_mode),
        schema=schema,
        rendered_filter=rendered,
        page_size=source.page_size,
        semantics=DeltaSemantics(source.delta_semantics) if source.delta_semantics else None,
        connector=tk.connector_factory(ref, spec),
        plugin=plugin,
        config=destinations.stored_config(destination),
        secret=destinations.stored_secret(destination),
        plugin_context=tk.destinations.context,
        options=load_options(pipeline),
        watermark=watermark,
        watermark_overlap_seconds=source.watermark_overlap_seconds or 0,
        destination_label=destination.code,
    )
