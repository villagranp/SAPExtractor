"""Spool en disco (ADR 0006 §6): ``<SX_SPOOL_DIR>/<batch_id>/part-NNNNN.parquet`` + ``_spool.json``.

Escritura atómica: cada archivo se escribe como ``*.tmp``, se sincroniza y se renombra; el
manifiesto es lo último. Sin manifiesto, el spool no es válido.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

import pyarrow as pa
import pyarrow.parquet as pq

from sapextract.dataflow.domain.batch import RunMode, is_batch_id
from sapextract.dataflow.domain.schema import BATCH_ID, RECORD_SEQ, RUN_ID, EntitySchema
from sapextract.dataflow.domain.spool import MANIFEST_NAME, SpoolFile, SpoolManifest

MAX_ROWS_PER_FILE = 1_000_000
ROW_GROUP_ROWS = 128_000
COMPRESSION: Final = "zstd"


class InvalidSpoolError(Exception):
    """El spool no existe, está incompleto o no corresponde al lote."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_atomically(tmp: Path, final: Path) -> None:
    with tmp.open("r+b") as handle:  # Windows no permite fsync de un archivo de solo lectura
        os.fsync(handle.fileno())
    tmp.replace(final)


def spool_path(root: Path, batch_id: str) -> Path:
    if not is_batch_id(batch_id):  # el batch_id es parte de la ruta: nunca texto libre
        msg = f"batch_id no válido: {batch_id!r}"
        raise ValueError(msg)
    return root / batch_id


class SpoolWriter:
    """Escribe los lotes Arrow de un run en Parquet y, al cerrar, el manifiesto."""

    def __init__(
        self,
        root: Path,
        *,
        batch_id: str,
        pipeline_id: uuid.UUID,
        run_id: uuid.UUID,
        mode: RunMode,
        schema: EntitySchema,
        worker_id: str,
        start_token: str | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        max_rows_per_file: int = MAX_ROWS_PER_FILE,
    ) -> None:
        self.directory = spool_path(root, batch_id)
        if self.directory.exists():
            shutil.rmtree(self.directory)  # restos de un intento anterior sin manifiesto
        self.directory.mkdir(parents=True, mode=0o700)
        self._meta = {
            "batch_id": batch_id,
            "pipeline_id": pipeline_id,
            "run_id": run_id,
            "mode": mode,
            "schema": schema,
            "worker_id": worker_id,
            "start_token": start_token,
            "window_start": window_start,
            "window_end": window_end,
        }
        self._arrow_schema = schema.arrow_schema()
        self._max_rows = max_rows_per_file
        self._files: list[SpoolFile] = []
        self._writer: pq.ParquetWriter | None = None
        self._current: Path | None = None
        self._current_rows = 0
        self._seq = 0
        self._batch_id = batch_id
        self._run_id = str(run_id)

    # -------------------------------------------------------------- escritura
    def write(self, batch: pa.RecordBatch) -> None:
        """Añade un lote del conector; completa ``_sx_batch_id``, ``_sx_run_id`` y la secuencia."""
        batch = self._complete(batch)
        offset = 0
        while offset < batch.num_rows:
            writer = self._writer or self._open()
            room = self._max_rows - self._current_rows
            chunk = batch.slice(offset, room)
            writer.write_batch(chunk, row_group_size=ROW_GROUP_ROWS)
            self._current_rows += chunk.num_rows
            offset += chunk.num_rows
            if self._current_rows >= self._max_rows:
                self._close_file()

    def finish(self, *, next_token: str | None, created_at: datetime) -> SpoolManifest:
        self._close_file()
        manifest = SpoolManifest(
            files=tuple(self._files),
            next_token=next_token,
            created_at=created_at,
            **self._meta,  # type: ignore[arg-type]
        )
        tmp = self.directory / f"{MANIFEST_NAME}.tmp"
        tmp.write_text(json.dumps(manifest.to_json(), ensure_ascii=False), encoding="utf-8")
        _replace_atomically(tmp, self.directory / MANIFEST_NAME)
        return manifest

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    # -------------------------------------------------------------- internos
    def _complete(self, batch: pa.RecordBatch) -> pa.RecordBatch:
        n = batch.num_rows
        columns = {name: batch.column(name) for name in batch.schema.names}
        columns[BATCH_ID] = pa.array([self._batch_id] * n, pa.string())
        columns[RUN_ID] = pa.array([self._run_id] * n, pa.string())
        columns[RECORD_SEQ] = pa.array(range(self._seq + 1, self._seq + n + 1), pa.int64())
        self._seq += n
        try:
            return pa.RecordBatch.from_arrays(
                [columns[f.name] for f in self._arrow_schema], schema=self._arrow_schema
            )
        except (KeyError, pa.ArrowInvalid, pa.ArrowTypeError) as exc:
            msg = f"El lote no coincide con el esquema del spool: {exc}"
            raise ValueError(msg) from exc

    def _open(self) -> pq.ParquetWriter:
        name = f"part-{len(self._files) + 1:05d}.parquet"
        self._current = self.directory / name
        self._writer = pq.ParquetWriter(
            str(self._current.with_suffix(".parquet.tmp")),
            self._arrow_schema,
            compression=COMPRESSION,
        )
        self._current_rows = 0
        return self._writer

    def _close_file(self) -> None:
        if self._writer is None or self._current is None:
            return
        self._writer.close()
        self._writer = None
        tmp = self._current.with_suffix(".parquet.tmp")
        _replace_atomically(tmp, self._current)
        self._files.append(
            SpoolFile(
                name=self._current.name,
                rows=self._current_rows,
                bytes=self._current.stat().st_size,
                sha256=_sha256(self._current),
            )
        )
        self._current = None


class DirectorySpool:
    """Spool válido leído del disco (implementa el protocolo ``Spool``)."""

    def __init__(self, directory: Path, manifest: SpoolManifest) -> None:
        self.directory = directory
        self._manifest = manifest

    @classmethod
    def open(
        cls,
        root: Path,
        batch_id: str,
        *,
        schema_hash: str | None = None,
        verify_hashes: bool = False,
    ) -> DirectorySpool:
        directory = spool_path(root, batch_id)
        manifest_path = directory / MANIFEST_NAME
        if not manifest_path.is_file():
            msg = f"El spool del lote {batch_id} no existe o está incompleto (sin manifiesto)"
            raise InvalidSpoolError(msg)
        try:
            manifest = SpoolManifest.from_json(json.loads(manifest_path.read_text("utf-8")))
        except (ValueError, KeyError, TypeError) as exc:
            msg = f"Manifiesto de spool no válido: {exc}"
            raise InvalidSpoolError(msg) from exc
        if manifest.batch_id != batch_id:
            msg = "El manifiesto pertenece a otro lote"
            raise InvalidSpoolError(msg)
        if schema_hash is not None and manifest.schema_hash != schema_hash:
            msg = "El esquema del spool no coincide con el vigente"
            raise InvalidSpoolError(msg)
        for f in manifest.files:
            path = directory / f.name
            if not path.is_file() or path.stat().st_size != f.bytes:
                msg = f"Falta o cambió el archivo {f.name} del spool"
                raise InvalidSpoolError(msg)
            if verify_hashes and _sha256(path) != f.sha256:
                msg = f"El archivo {f.name} del spool está dañado"
                raise InvalidSpoolError(msg)
        return cls(directory, manifest)

    @property
    def manifest(self) -> SpoolManifest:
        return self._manifest

    def parquet_files(self) -> Sequence[Path]:
        return [self.directory / f.name for f in self._manifest.files]

    def batches(self) -> Iterator[pa.RecordBatch]:
        for path in self.parquet_files():
            yield from pq.ParquetFile(str(path)).iter_batches()

    def remove(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


__all__ = [
    "COMPRESSION",
    "DirectorySpool",
    "InvalidSpoolError",
    "SpoolWriter",
    "spool_path",
]
