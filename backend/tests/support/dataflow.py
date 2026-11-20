"""Dobles de prueba del flujo de datos (ADR 0006 §9): spools de muestra y destino en memoria."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa

from sapextract.dataflow.domain.batch import BatchInfo, LoadMode, RunMode, batch_id
from sapextract.dataflow.domain.ports import (
    AppliedBatch,
    DeleteHandling,
    LoadOptions,
    LoadResult,
    PreparedTarget,
    StageInfo,
)
from sapextract.dataflow.domain.schema import (
    CHANGE_MODE,
    EXTRACTED_AT,
    RECORD_SEQ,
    Column,
    EntitySchema,
)
from sapextract.dataflow.domain.spool import Spool
from sapextract.dataflow.infrastructure.parquet_spool import DirectorySpool, SpoolWriter

# Esquema de ejemplo con los tipos que más guerra dan: clave compuesta, decimal, fecha,
# fecha-hora sin zona, instante UTC, booleano y nulos.
GL_SCHEMA = EntitySchema(
    columns=(
        Column(name="RBUKRS", edm_type="Edm.String", nullable=False, is_key=True, max_length=4),
        Column(name="BELNR", edm_type="Edm.String", nullable=False, is_key=True, max_length=10),
        Column(name="BUDAT", edm_type="Edm.DateTime", display_format="Date"),
        Column(name="DMBTR", edm_type="Edm.Decimal", precision=17, scale=2),
        Column(name="CPUDT", edm_type="Edm.DateTime"),
        Column(name="CHANGED", edm_type="Edm.DateTimeOffset"),
        Column(name="XBLOCK", edm_type="Edm.Boolean"),
        Column(name="ODQ_CHANGEMODE", edm_type="Edm.String", max_length=1),
    )
)


def gl_row(
    belnr: int, *, amount: str = "10.50", mode: str = "", bukrs: str = "1000"
) -> dict[str, Any]:
    return {
        "RBUKRS": bukrs,
        "BELNR": f"{belnr:010d}",
        "BUDAT": date(2026, 1, 1 + belnr % 28),
        "DMBTR": Decimal(amount),
        # Hora local SAP sin zona (Edm.DateTime): naive a propósito.
        "CPUDT": datetime(2026, 1, 2, 8, 30, belnr % 60),  # noqa: DTZ001
        "CHANGED": datetime(2026, 1, 2, 7, 30, tzinfo=UTC),
        "XBLOCK": belnr % 2 == 0,
        "ODQ_CHANGEMODE": mode,
    }


def to_batch(schema: EntitySchema, rows: list[dict[str, Any]]) -> pa.RecordBatch:
    """Lote como lo entrega un conector: columnas de origen + técnicas del conector."""
    source = {c.name: pa.array([r.get(c.name) for r in rows], c.arrow_type) for c in schema.columns}
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    modes = [(r.get("ODQ_CHANGEMODE") or "C") if "ODQ_CHANGEMODE" in r else None for r in rows]
    source[EXTRACTED_AT] = pa.array([now] * len(rows), pa.timestamp("us", tz="UTC"))
    source[CHANGE_MODE] = pa.array(modes, pa.string())
    source[RECORD_SEQ] = pa.array([0] * len(rows), pa.int64())  # el SpoolWriter la numera
    return pa.RecordBatch.from_pydict(source)


@dataclass
class SampleBatch:
    spool: DirectorySpool
    info: BatchInfo


def make_spool(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    schema: EntitySchema = GL_SCHEMA,
    pipeline_id: uuid.UUID | None = None,
    mode: RunMode = RunMode.DELTA,
    start_token: str | None = None,
    next_token: str | None = "D000000000002",  # noqa: S107 - token de delta, no contraseña
    max_rows_per_file: int = 1_000_000,
    chunk: int = 1000,
) -> SampleBatch:
    """Spool válido en disco, como lo dejaría el paso ``extract``."""
    pid = pipeline_id or uuid.uuid4()
    rid = uuid.uuid4()
    token = start_token or f"D{uuid.uuid4().int % 10**12:012d}"
    bid = (
        batch_id(mode, pipeline_id=pid, generation=1, start_token=token)
        if mode is RunMode.DELTA
        else batch_id(mode, pipeline_id=pid, run_id=rid, generation=1)
    )
    with SpoolWriter(
        root,
        batch_id=bid,
        pipeline_id=pid,
        run_id=rid,
        mode=mode,
        schema=schema,
        worker_id="test-worker",
        start_token=token if mode is RunMode.DELTA else None,
        max_rows_per_file=max_rows_per_file,
    ) as writer:
        for i in range(0, len(rows), chunk):
            writer.write(to_batch(schema, rows[i : i + chunk]))
        manifest = writer.finish(
            next_token=next_token if mode is not RunMode.FULL else None,
            created_at=datetime(2026, 11, 20, 10, 5, tzinfo=UTC),
        )
    spool = DirectorySpool.open(root, bid, schema_hash=schema.schema_hash())
    info = BatchInfo(
        pipeline_id=pid,
        run_id=rid,
        batch_id=bid,
        mode=mode,
        schema_hash=manifest.schema_hash,
        next_token=manifest.next_token,
    )
    return SampleBatch(spool=spool, info=info)


# ------------------------------------------------------------------ destino en memoria


@dataclass
class InMemoryTarget:
    rows: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)
    appended: list[dict[str, Any]] = field(default_factory=list)
    control: dict[tuple[uuid.UUID, str], AppliedBatch] = field(default_factory=dict)


class InMemoryWriter:
    """Destino de referencia que cumple el contrato; sirve para probar la extracción."""

    def __init__(self) -> None:
        self.target = InMemoryTarget()
        self.fail_inside_apply = False

    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        names = {c.name: c.name for c in schema.columns}
        return PreparedTarget(target="memory", schema=schema, options=options, column_names=names)

    def applied_batch(self, target: PreparedTarget, batch_id_: str) -> AppliedBatch | None:
        return next((a for (_, b), a in self.target.control.items() if b == batch_id_), None)

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        rows = [r for b in spool.batches() for r in b.to_pylist()]
        return StageInfo(target=target, spool_rows=len(rows), details={"rows": rows})

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        rows: list[dict[str, Any]] = list(stage.details["rows"])
        options = stage.target.options
        keys = stage.target.schema.keys
        snapshot = (dict(self.target.rows), list(self.target.appended))
        try:
            inserted, updated, deleted = self._apply(rows, options, keys, batch.batch_id)
            if self.fail_inside_apply:
                msg = "fallo inyectado dentro de apply"
                raise RuntimeError(msg)
            self.target.control[(batch.pipeline_id, batch.batch_id)] = AppliedBatch(
                pipeline_id=batch.pipeline_id,
                batch_id=batch.batch_id,
                run_id=batch.run_id,
                next_token=batch.next_token,
                window_end=batch.window_end,
                applied_at=datetime.now(UTC),
            )
        except Exception:
            self.target.rows, self.target.appended = snapshot  # "rollback"
            raise
        return LoadResult(rows_inserted=inserted, rows_updated=updated, rows_deleted=deleted)

    def cleanup(self, stage: StageInfo) -> None:
        return

    # -------------------------------------------------------------- lógica de carga
    def _apply(
        self,
        rows: list[dict[str, Any]],
        options: LoadOptions,
        keys: Iterable[str],
        bid: str,
    ) -> tuple[int, int, int]:
        key_names = tuple(keys)
        if options.load_mode is LoadMode.APPEND:
            self.target.appended = [r for r in self.target.appended if r["_sx_batch_id"] != bid]
            self.target.appended.extend(rows)
            return len(rows), 0, 0
        if options.load_mode is LoadMode.REPLACE:
            self.target.rows = {tuple(r[k] for k in key_names): r for r in rows}
            return len(rows), 0, 0
        latest: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in sorted(rows, key=lambda r: r["_sx_record_seq"]):
            latest[tuple(r[k] for k in key_names)] = r
        inserted = updated = deleted = 0
        for key, r in latest.items():
            exists = key in self.target.rows
            if r.get("_sx_change_mode") == "D":
                if options.delete_handling is DeleteHandling.HARD and exists:
                    del self.target.rows[key]
                    deleted += 1
                elif options.delete_handling is DeleteHandling.SOFT and exists:
                    self.target.rows[key] = {**self.target.rows[key], "_sx_deleted": True}
                    deleted += 1
                continue
            self.target.rows[key] = {**r, "_sx_deleted": False}
            updated += exists
            inserted += not exists
        return inserted, updated, deleted
