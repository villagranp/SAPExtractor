"""Destino CSV (plan 07 §4): archivos por lote, renombrado atómico y manifiesto como control.

Estructura bajo ``<raíz>/<subcarpeta>/<target>/``::

    batches/<batch_id>/part-0001.csv …     append: una carpeta por lote (repetir la sobrescribe)
    current/part-0001.csv …                replace: la carpeta vigente se sustituye entera
    _sx_load_state/<batch_id>.json         tabla de control (lote aplicado), escrita la última

Cada carpeta de datos lleva ``_manifest.json`` (archivos, filas, SHA-256, esquema, modo, token)
y ``_SUCCESS``. Los datos se escriben primero en ``.tmp-<batch_id>/`` y se publican con un
renombrado, así los consumidores nunca ven un lote a medias.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
from pydantic import BaseModel, ConfigDict, Field

from sapextract.core.clock import utcnow
from sapextract.dataflow.domain.batch import BatchInfo, LoadMode, is_batch_id
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    AppliedBatch,
    DeleteHandling,
    LoadOptions,
    LoadResult,
    PreparedTarget,
    StageInfo,
    require_test_target,
)
from sapextract.dataflow.domain.schema import (
    BATCH_ID,
    CHANGE_MODE,
    DELETED,
    EXTRACTED_AT,
    RECORD_SEQ,
    RUN_ID,
    EntitySchema,
)
from sapextract.dataflow.domain.spool import Spool
from sapextract.destinations.application.plugins import PluginContext, wrong_model
from sapextract.destinations.domain.model import (
    DestinationType,
    check_load_options,
    resolve_columns,
)
from sapextract.destinations.domain.testing import CheckReport, CheckStatus, CheckStep

CONTROL_DIR = "_sx_load_state"
MANIFEST = "_manifest.json"
SUCCESS = "_SUCCESS"
LOW_SPACE_BYTES = 1 << 30  # aviso si quedan menos de 1 GB libres
TECHNICAL_OUT = (BATCH_ID, RUN_ID, EXTRACTED_AT, CHANGE_MODE, RECORD_SEQ)


class CsvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", title="CSV")

    root: str = Field(
        title="Carpeta raíz", description="Una de las carpetas permitidas (SX_CSV_ROOTS)"
    )
    path: str = Field("", title="Subcarpeta", max_length=300, description="Relativa a la raíz")
    delimiter: Literal[",", ";", "|", "\t"] = Field(",", title="Separador")
    encoding: Literal["utf-8", "utf-8-sig"] = Field(
        "utf-8", title="Codificación", description="utf-8-sig añade BOM (Excel)"
    )
    header: bool = Field(True, title="Fila de encabezado")
    line_terminator: Literal["\n", "\r\n"] = Field("\n", title="Fin de línea")
    compression: Literal["none", "gzip"] = Field("none", title="Compresión")
    max_rows_per_file: int = Field(
        1_000_000, ge=1_000, le=20_000_000, title="Filas máximas por archivo"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with tmp.open("r+b") as handle:
        os.fsync(handle.fileno())
    tmp.replace(path)


def _io_error(exc: OSError, what: str) -> DataflowError:
    return DataflowError(
        f"No se pudo {what}: {exc.strerror or exc}", kind=ErrorKind.TRANSIENT, code="SX-DST-CSV-IO"
    )


class CsvWriter:
    """Implementa ``DestinationWriter`` sobre una carpeta (local o recurso compartido montado)."""

    supported_modes = frozenset({LoadMode.APPEND, LoadMode.REPLACE})

    def __init__(self, base: Path, config: CsvConfig) -> None:
        self.base = base
        self.config = config

    # -------------------------------------------------------------- contrato
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        check_load_options(options, self.supported_modes)
        if options.delete_handling is DeleteHandling.HARD:
            msg = "CSV solo añade archivos: no admite el borrado físico de filas."
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-CSV-DELETE")
        columns = resolve_columns(schema, options.column_mapping)
        target = self.base / options.target_name
        try:
            (target / CONTROL_DIR).mkdir(parents=True, exist_ok=True)
            (target / "batches").mkdir(exist_ok=True)
        except OSError as exc:
            raise _io_error(exc, f"crear la carpeta {target}") from exc
        return PreparedTarget(
            target=str(target), schema=schema, options=options, column_names=columns
        )

    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None:
        path = Path(target.target) / CONTROL_DIR / f"{self._safe(batch_id)}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text("utf-8"))
        return AppliedBatch(
            pipeline_id=uuid.UUID(data["pipeline_id"]),
            batch_id=data["batch_id"],
            run_id=uuid.UUID(data["run_id"]),
            next_token=data.get("next_token"),
            window_end=datetime.fromisoformat(data["window_end"])
            if data.get("window_end")
            else None,
            applied_at=datetime.fromisoformat(data["applied_at"]),
        )

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        batch_id = self._safe(spool.manifest.batch_id)
        tmp = Path(target.target) / f".tmp-{batch_id}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            tmp.mkdir(parents=True)
            files, rows = self._write_parts(tmp, target, spool)
        except OSError as exc:
            raise _io_error(exc, "escribir los archivos CSV") from exc
        return StageInfo(
            target=target,
            spool_rows=spool.manifest.rows,
            details={
                "tmp": str(tmp),
                "files": files,
                "rows": rows,
                "schema": spool.manifest.schema.as_list(),
            },
        )

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        target = Path(stage.target.target)
        tmp = Path(stage.details["tmp"])
        mode = stage.target.options.load_mode
        manifest = {
            "batch_id": batch.batch_id,
            "pipeline_id": str(batch.pipeline_id),
            "run_id": str(batch.run_id),
            "mode": mode.value,
            "run_mode": batch.mode.value,
            "next_token": batch.next_token,
            "window_end": batch.window_end.isoformat() if batch.window_end else None,
            "schema_hash": batch.schema_hash,
            "schema": stage.details["schema"],
            "columns": dict(stage.target.column_names),
            "files": stage.details["files"],
            "rows": stage.details["rows"],
            "written_at": utcnow().isoformat(),
        }
        try:
            _write_json_atomic(tmp / MANIFEST, manifest)
            (tmp / SUCCESS).touch()
            final = (
                target / "current"
                if mode is LoadMode.REPLACE
                else target / "batches" / batch.batch_id
            )
            self._publish(tmp, final)
            control = {
                **{
                    k: manifest[k]
                    for k in (
                        "batch_id",
                        "pipeline_id",
                        "run_id",
                        "next_token",
                        "window_end",
                        "rows",
                    )
                },
                "applied_at": utcnow().isoformat(),
                "folder": str(final.relative_to(target)),
            }
            _write_json_atomic(target / CONTROL_DIR / f"{batch.batch_id}.json", control)
        except OSError as exc:
            raise _io_error(exc, "publicar el lote") from exc
        return LoadResult(rows_inserted=int(stage.details["rows"]))

    def cleanup(self, stage: StageInfo) -> None:
        shutil.rmtree(stage.details.get("tmp", ""), ignore_errors=True)

    def drop_test_target(self, target: PreparedTarget) -> None:
        require_test_target(target)
        folder = Path(target.target)
        if folder.parent != self.base:  # la carpeta de prueba cuelga de la del destino
            msg = "La carpeta de prueba no está en la carpeta del destino: no se borra."
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-NOT-TEST-TARGET")
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError as exc:
            raise _io_error(exc, f"borrar la carpeta de prueba {folder}") from exc

    # -------------------------------------------------------------- internos
    @staticmethod
    def _safe(batch_id: str) -> str:
        if not is_batch_id(batch_id):
            msg = f"batch_id no válido: {batch_id!r}"
            raise DataflowError(msg, kind=ErrorKind.DATA, code="SX-DST-BATCH-ID")
        return batch_id

    @staticmethod
    def _publish(tmp: Path, final: Path) -> None:
        """Sustituye ``final`` por ``tmp`` con renombrados (la ventana sin carpeta es mínima)."""
        old = final.with_name(f".old-{final.name}-{uuid.uuid4().hex[:8]}")
        if final.exists():
            final.replace(old)
        tmp.replace(final)
        shutil.rmtree(old, ignore_errors=True)

    def _table(self, batch: pa.RecordBatch, target: PreparedTarget) -> pa.Table:
        options = target.options
        table = pa.Table.from_batches([batch])
        if options.delete_handling is DeleteHandling.IGNORE and CHANGE_MODE in table.column_names:
            keep = pc.fill_null(pc.not_equal(table[CHANGE_MODE], pa.scalar("D")), pa.scalar(True))
            table = table.filter(keep)
        out: dict[str, Any] = {dest: table[src] for src, dest in target.column_names.items()}
        if options.technical_columns:
            for name in TECHNICAL_OUT:
                out[name] = table[name]
            out[DELETED] = pc.fill_null(
                pc.equal(table[CHANGE_MODE], pa.scalar("D")), pa.scalar(False)
            )
        else:
            out[BATCH_ID] = table[BATCH_ID]
        return pa.table(out)

    def _write_parts(
        self, folder: Path, target: PreparedTarget, spool: Spool
    ) -> tuple[list[dict[str, Any]], int]:
        cfg = self.config
        files: list[dict[str, Any]] = []
        total = 0
        writer: pacsv.CSVWriter | None = None
        handle: Any = None
        current: Path | None = None
        current_rows = 0

        def close() -> None:
            nonlocal writer, handle, current
            if writer is None or current is None:
                return
            writer.close()
            handle.close()
            files.append({"name": current.name, "rows": current_rows, "sha256": _sha256(current)})
            writer = handle = current = None

        options = pacsv.WriteOptions(  # type: ignore[call-arg]  # eol existe; faltan los stubs
            include_header=cfg.header, delimiter=cfg.delimiter, eol=cfg.line_terminator
        )
        for batch in self._batches(spool):
            table = self._table(batch, target)
            offset = 0
            while offset < table.num_rows:
                if writer is None:
                    suffix = ".csv.gz" if cfg.compression == "gzip" else ".csv"
                    current = folder / f"part-{len(files) + 1:04d}{suffix}"
                    handle = (
                        gzip.open(current, "wb")  # noqa: SIM115 - se cierra en close()
                        if cfg.compression == "gzip"
                        else current.open("wb")
                    )
                    if cfg.encoding == "utf-8-sig":
                        handle.write("﻿".encode())
                    writer = pacsv.CSVWriter(handle, table.schema, write_options=options)
                    current_rows = 0
                chunk = table.slice(offset, cfg.max_rows_per_file - current_rows)
                writer.write_table(chunk)
                current_rows += chunk.num_rows
                total += chunk.num_rows
                offset += chunk.num_rows
                if current_rows >= cfg.max_rows_per_file:
                    close()
        close()
        return files, total

    @staticmethod
    def _batches(spool: Spool) -> Iterator[pa.RecordBatch]:
        yield from spool.batches()


class CsvPlugin:
    kind: ClassVar[DestinationType] = DestinationType.CSV
    label: ClassVar[str] = "Archivos CSV"
    config_model: ClassVar[type[BaseModel]] = CsvConfig
    secret_model: ClassVar[type[BaseModel] | None] = None
    supported_modes: ClassVar[frozenset[LoadMode]] = CsvWriter.supported_modes

    def _base(self, config: BaseModel, ctx: PluginContext) -> Path:
        if not isinstance(config, CsvConfig):
            raise wrong_model(config, CsvConfig)
        return ctx.csv_roots.resolve(config.root, config.path)

    def validate(self, config: BaseModel, ctx: PluginContext) -> None:
        self._base(config, ctx)

    def credential_info(self, secret: BaseModel | None) -> dict[str, Any]:
        return {}

    def test(self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext) -> CheckReport:
        started = utcnow()
        steps: list[CheckStep] = []
        t0 = time.perf_counter()
        try:
            base = self._base(config, ctx)
            base.mkdir(parents=True, exist_ok=True)
            steps.append(
                CheckStep(
                    name="path",
                    status=CheckStatus.OK,
                    message=f"Carpeta {base} disponible.",
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    details={"path": str(base)},
                )
            )
        except Exception as exc:  # BusinessRuleError u OSError: se informa en el paso
            message = getattr(exc, "detail", None) or f"No se pudo usar la carpeta: {exc}"
            steps.append(
                CheckStep(
                    name="path",
                    status=CheckStatus.FAILED,
                    message=message,
                    error_code=getattr(exc, "error_code", "SX-DST-CSV-IO"),
                )
            )
            steps.append(
                CheckStep(
                    name="write",
                    status=CheckStatus.SKIPPED,
                    message="No se ejecutó por un fallo anterior.",
                )
            )
            return CheckReport(started_at=started, finished_at=utcnow(), steps=tuple(steps))
        t0 = time.perf_counter()
        probe = base / f".sx-test-{uuid.uuid4().hex[:8]}.csv"
        try:
            probe.write_text("prueba\n", encoding="utf-8")
            ok = probe.read_text(encoding="utf-8") == "prueba\n"
            probe.unlink()
            free = shutil.disk_usage(base).free
            status = CheckStatus.WARNING if free < LOW_SPACE_BYTES else CheckStatus.OK
            message = (
                "Se puede crear, leer y borrar un archivo."
                if ok
                else "El archivo leído no coincide."
            )
            if status is CheckStatus.WARNING:
                message += f" Quedan solo {free // (1 << 20)} MB libres."
            steps.append(
                CheckStep(
                    name="write",
                    status=status if ok else CheckStatus.FAILED,
                    message=message,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    details={"free_bytes": free},
                )
            )
        except OSError as exc:
            with contextlib.suppress(OSError):
                probe.unlink()
            steps.append(
                CheckStep(
                    name="write",
                    status=CheckStatus.FAILED,
                    message=f"No se pudo escribir en la carpeta: {exc.strerror or exc}",
                    error_code="SX-DST-CSV-IO",
                )
            )
        return CheckReport(started_at=started, finished_at=utcnow(), steps=tuple(steps))

    def open_writer(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> AbstractContextManager[CsvWriter]:
        if not isinstance(config, CsvConfig):
            raise wrong_model(config, CsvConfig)
        return contextlib.nullcontext(CsvWriter(self._base(config, ctx), config))

    def ddl(self, config: BaseModel, schema: EntitySchema, options: LoadOptions) -> list[str]:
        columns = resolve_columns(schema, options.column_mapping)
        header = ", ".join(
            [
                *columns.values(),
                *(TECHNICAL_OUT if options.technical_columns else (BATCH_ID,)),
                *((DELETED,) if options.technical_columns else ()),
            ]
        )
        return [f"-- Archivos CSV en <carpeta>/{options.target_name}/", f"-- Columnas: {header}"]

    def config_json_schema(self, ctx: PluginContext) -> dict[str, Any]:
        schema = CsvConfig.model_json_schema()
        schema["properties"]["root"]["enum"] = [str(r) for r in ctx.csv_roots.roots]
        return schema


__all__ = ["CsvConfig", "CsvPlugin", "CsvWriter"]
