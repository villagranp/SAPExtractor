"""Manifiesto del spool (ADR 0006 §6) y protocolo de lectura que usan los destinos."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from sapextract.dataflow.domain.batch import RunMode
from sapextract.dataflow.domain.schema import EntitySchema

MANIFEST_NAME = "_spool.json"
MANIFEST_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class SpoolFile:
    name: str
    rows: int
    bytes: int
    sha256: str


@dataclass(frozen=True, kw_only=True)
class SpoolManifest:
    batch_id: str
    pipeline_id: uuid.UUID
    run_id: uuid.UUID
    mode: RunMode
    schema: EntitySchema
    files: tuple[SpoolFile, ...]
    worker_id: str
    created_at: datetime
    start_token: str | None = None
    next_token: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    version: int = MANIFEST_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(f.rows for f in self.files)

    @property
    def schema_hash(self) -> str:
        return self.schema.schema_hash()

    def to_json(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "version": self.version,
            "batch_id": self.batch_id,
            "pipeline_id": str(self.pipeline_id),
            "run_id": str(self.run_id),
            "mode": self.mode.value,
            "start_token": self.start_token,
            "next_token": self.next_token,
            "window_start": iso(self.window_start),
            "window_end": iso(self.window_end),
            "schema_hash": self.schema_hash,
            "schema": self.schema.as_list(),
            "files": [f.__dict__ for f in self.files],
            "rows": self.rows,
            "worker_id": self.worker_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SpoolManifest:
        if data.get("version") != MANIFEST_VERSION:
            msg = f"Versión de manifiesto no soportada: {data.get('version')!r}"
            raise ValueError(msg)

        def dt(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        manifest = cls(
            batch_id=data["batch_id"],
            pipeline_id=uuid.UUID(data["pipeline_id"]),
            run_id=uuid.UUID(data["run_id"]),
            mode=RunMode(data["mode"]),
            schema=EntitySchema.from_list(data["schema"]),
            files=tuple(SpoolFile(**f) for f in data["files"]),
            worker_id=data["worker_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            start_token=data.get("start_token"),
            next_token=data.get("next_token"),
            window_start=dt(data.get("window_start")),
            window_end=dt(data.get("window_end")),
        )
        if manifest.schema_hash != data.get("schema_hash"):
            msg = "El hash de esquema del manifiesto no coincide con su esquema"
            raise ValueError(msg)
        return manifest


class Spool(Protocol):
    """Spool válido de un lote: lo que ``DestinationWriter.stage`` recibe."""

    @property
    def manifest(self) -> SpoolManifest: ...

    def parquet_files(self) -> Sequence[Path]: ...

    def batches(self) -> Iterator[pa.RecordBatch]: ...
