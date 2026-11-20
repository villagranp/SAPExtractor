"""Puertos del motor (ADR 0006 §7): conectores de origen y escritores de destino.

El motor solo conoce estas interfaces; cada origen (ODP, CDS) y cada destino (CSV, Snowflake,
SQL Server) es un plugin que las implementa.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import pyarrow as pa

from sapextract.dataflow.domain.batch import BatchInfo, LoadMode
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.dataflow.domain.spool import Spool

# ------------------------------------------------------------------ origen


@dataclass(frozen=True, kw_only=True)
class PageMetrics:
    rows: int
    bytes: int
    latency_ms: int


@dataclass(frozen=True, kw_only=True)
class ReadContext:
    run_id: uuid.UUID
    batch_id: str
    page_size: int
    page_timeout_seconds: int
    is_cancelled: Callable[[], bool]
    on_page: Callable[[PageMetrics], None]


@dataclass(frozen=True, kw_only=True)
class Window:
    field: str
    start: datetime
    end: datetime


type Filters = Mapping[str, Any]


class ReadResult(Protocol):
    def batches(self) -> Iterator[pa.RecordBatch]:
        """Lotes con las columnas técnicas salvo ``_sx_batch_id`` y ``_sx_run_id``."""
        ...

    def next_token(self) -> str | None:
        """Token devuelto por SAP; disponible al agotar ``batches``. ``None`` en FULL/WATERMARK."""
        ...


class SourceConnector(Protocol):
    def schema(self) -> EntitySchema: ...

    def read_full(self, filters: Filters, ctx: ReadContext) -> ReadResult: ...

    def read_delta_init(self, filters: Filters, ctx: ReadContext) -> ReadResult: ...

    def read_delta(self, token: str, ctx: ReadContext) -> ReadResult: ...

    def read_window(self, window: Window, ctx: ReadContext) -> ReadResult: ...


# ------------------------------------------------------------------ destino


class SchemaEvolution(StrEnum):
    ADD_COLUMNS = "add_columns"
    IGNORE = "ignore"
    FAIL = "fail"


class DeleteHandling(StrEnum):
    SOFT = "soft"  # _sx_deleted = true
    HARD = "hard"  # se borra la fila
    IGNORE = "ignore"  # los registros D se descartan


@dataclass(frozen=True, kw_only=True)
class LoadOptions:
    """Opciones de carga de un pipeline (plan 07 §2)."""

    load_mode: LoadMode
    target_name: str
    create_if_missing: bool = True
    schema_evolution: SchemaEvolution = SchemaEvolution.ADD_COLUMNS
    delete_handling: DeleteHandling = DeleteHandling.SOFT
    technical_columns: bool = True
    column_mapping: Mapping[str, str | None] = field(default_factory=dict)  # None = excluir


@dataclass(frozen=True, kw_only=True)
class PreparedTarget:
    """Destino listo para cargar: lo que ``prepare`` dejó creado y cómo se llaman las columnas."""

    target: str  # nombre totalmente cualificado o ruta
    schema: EntitySchema
    options: LoadOptions
    column_names: Mapping[str, str]  # columna de origen → columna en destino (sin excluidas)
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class AppliedBatch:
    pipeline_id: uuid.UUID
    batch_id: str
    run_id: uuid.UUID
    next_token: str | None
    window_end: datetime | None
    applied_at: datetime


@dataclass(frozen=True, kw_only=True)
class StageInfo:
    target: PreparedTarget
    spool_rows: int
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class LoadResult:
    rows_inserted: int
    rows_updated: int = 0
    rows_deleted: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


class DestinationWriter(Protocol):
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        """Todo el DDL (tabla, columnas nuevas, tabla de control). Idempotente."""
        ...

    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None:
        """Lee la tabla de control: si el lote ya está aplicado, no se vuelve a cargar."""
        ...

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        """Lleva el spool al destino sin tocar la tabla final. Puede repetirse."""
        ...

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        """Una transacción: aplica el modo de carga y escribe la tabla de control."""
        ...

    def cleanup(self, stage: StageInfo) -> None:
        """Borra el staging. Idempotente."""
        ...
