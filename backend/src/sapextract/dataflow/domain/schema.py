"""Esquema de entidad y columnas técnicas (ADR 0006 §2 y §3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from sapextract.dataflow.domain.types import arrow_type

# Columnas técnicas del spool (ADR 0006 §3). `_sx_deleted` solo existe en el destino.
BATCH_ID = "_sx_batch_id"
RUN_ID = "_sx_run_id"
EXTRACTED_AT = "_sx_extracted_at"
CHANGE_MODE = "_sx_change_mode"
RECORD_SEQ = "_sx_record_seq"
DELETED = "_sx_deleted"

TECHNICAL_FIELDS: tuple[pa.Field[Any], ...] = (
    pa.field(BATCH_ID, pa.string(), nullable=False),
    pa.field(RUN_ID, pa.string(), nullable=False),
    pa.field(EXTRACTED_AT, pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field(CHANGE_MODE, pa.string(), nullable=True),
    pa.field(RECORD_SEQ, pa.int64(), nullable=False),
)
TECHNICAL_NAMES: frozenset[str] = frozenset(f.name for f in TECHNICAL_FIELDS) | {DELETED}


@dataclass(frozen=True)
class Column:
    name: str  # nombre técnico en SAP; en el spool no se renombra
    edm_type: str
    nullable: bool = True
    is_key: bool = False
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    label: str | None = None
    display_format: str | None = None

    @property
    def arrow_type(self) -> pa.DataType:
        return arrow_type(
            self.edm_type,
            precision=self.precision,
            scale=self.scale,
            display_format=self.display_format,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "edm_type": self.edm_type,
            "arrow_type": str(self.arrow_type),
            "nullable": self.nullable,
            "is_key": self.is_key,
            "max_length": self.max_length,
            "precision": self.precision,
            "scale": self.scale,
            "label": self.label,
            "display_format": self.display_format,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Column:
        return cls(
            name=data["name"],
            edm_type=data["edm_type"],
            nullable=bool(data.get("nullable", True)),
            is_key=bool(data.get("is_key", False)),
            max_length=data.get("max_length"),
            precision=data.get("precision"),
            scale=data.get("scale"),
            label=data.get("label"),
            display_format=data.get("display_format"),
        )


@dataclass(frozen=True)
class EntitySchema:
    """Columnas de origen en el orden del ``$metadata`` (sin columnas técnicas)."""

    columns: tuple[Column, ...]
    _by_name: dict[str, Column] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            msg = "El esquema tiene columnas repetidas"
            raise ValueError(msg)
        clashes = sorted(set(names) & TECHNICAL_NAMES)
        if clashes:
            msg = f"Columnas reservadas para uso técnico: {', '.join(clashes)}"
            raise ValueError(msg)
        self._by_name.update({c.name: c for c in self.columns})

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.is_key)

    def column(self, name: str) -> Column:
        return self._by_name[name]

    def arrow_schema(self) -> pa.Schema:
        """Esquema del spool: columnas de origen + técnicas."""
        source = [pa.field(c.name, c.arrow_type, nullable=c.nullable) for c in self.columns]
        return pa.schema([*source, *TECHNICAL_FIELDS])

    def schema_hash(self) -> str:
        """SHA-256 del JSON canónico de lo que afecta a la carga (no las etiquetas)."""
        canonical = [
            [c.name, c.edm_type, c.nullable, c.is_key, c.max_length, c.precision, c.scale]
            for c in self.columns
        ]
        raw = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def as_list(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.columns]

    @classmethod
    def from_list(cls, data: Iterable[dict[str, Any]]) -> EntitySchema:
        return cls(columns=tuple(Column.from_dict(d) for d in data))
