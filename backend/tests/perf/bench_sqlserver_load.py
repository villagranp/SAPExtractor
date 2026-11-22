"""Medición de la carga en SQL Server con ``fast_executemany`` (plan 07 §3, It4b).

No es una prueba de pytest: se ejecuta a mano y arranca su propio SQL Server en Docker.

    uv run python -m tests.perf.bench_sqlserver_load --rows 10000000

Genera un spool con el esquema de ejemplo de GL (clave compuesta, decimal, fechas, instante
UTC, booleano), lo sube a staging y lo aplica en modo ``append``, y escribe los tiempos.
"""

from __future__ import annotations

import argparse
import tempfile
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from sapextract.dataflow.domain.batch import BatchInfo, LoadMode, RunMode, batch_id
from sapextract.dataflow.domain.ports import LoadOptions
from sapextract.dataflow.domain.schema import CHANGE_MODE, EXTRACTED_AT, RECORD_SEQ
from sapextract.dataflow.infrastructure.parquet_spool import DirectorySpool, SpoolWriter
from sapextract.destinations.infrastructure.sqlserver_plugin import SqlServerPlugin
from tests.support.dataflow import GL_SCHEMA
from tests.support.mssql import start_sqlserver

CHUNK = 100_000


def _batch(start: int, size: int) -> pa.RecordBatch:
    idx = pa.array(range(start, start + size), pa.int64())
    belnr = pc.utf8_lpad(pc.cast(idx, pa.string()), width=10, padding="0")
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    base = datetime(2026, 1, 2, 8, 30)  # noqa: DTZ001 - hora local SAP sin zona
    data = {
        "RBUKRS": pa.array(["1000"] * size, pa.string()),
        "BELNR": belnr,
        "BUDAT": pa.array([base.date()] * size, pa.date32()),
        "DMBTR": pa.array([Decimal("10.50")] * size, pa.decimal128(17, 2)),
        "CPUDT": pa.array([base] * size, pa.timestamp("us")),
        "CHANGED": pa.array([now] * size, pa.timestamp("us", tz="UTC")),
        "XBLOCK": pa.array([i % 2 == 0 for i in range(start, start + size)], pa.bool_()),
        "ODQ_CHANGEMODE": pa.array(["C"] * size, pa.string()),
        EXTRACTED_AT: pa.array([now] * size, pa.timestamp("us", tz="UTC")),
        CHANGE_MODE: pa.array(["C"] * size, pa.string()),
        RECORD_SEQ: pa.array([0] * size, pa.int64()),
    }
    return pa.RecordBatch.from_pydict(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    args = parser.parse_args()
    rows: int = args.rows

    with tempfile.TemporaryDirectory() as tmp, start_sqlserver() as server:
        pid, rid = uuid.uuid4(), uuid.uuid4()
        bid = batch_id(RunMode.FULL, pipeline_id=pid, run_id=rid)
        t0 = time.perf_counter()
        with SpoolWriter(
            Path(tmp),
            batch_id=bid,
            pipeline_id=pid,
            run_id=rid,
            mode=RunMode.FULL,
            schema=GL_SCHEMA,
            worker_id="bench",
            start_token=None,
            max_rows_per_file=1_000_000,
        ) as writer:
            for start in range(0, rows, CHUNK):
                writer.write(_batch(start, min(CHUNK, rows - start)))
            manifest = writer.finish(next_token=None, created_at=datetime.now(UTC))
        spool = DirectorySpool.open(Path(tmp), bid, schema_hash=GL_SCHEMA.schema_hash())
        t_spool = time.perf_counter() - t0

        ctx = server.context(Path(tmp))
        info = BatchInfo(
            pipeline_id=pid,
            run_id=rid,
            batch_id=bid,
            mode=RunMode.FULL,
            schema_hash=manifest.schema_hash,
        )
        options = LoadOptions(load_mode=LoadMode.APPEND, target_name="BENCH_GL")
        with SqlServerPlugin().open_writer(server.config, server.secret, ctx) as dest:
            target = dest.prepare(GL_SCHEMA, options)
            t1 = time.perf_counter()
            stage = dest.stage(target, spool)
            t_stage = time.perf_counter() - t1
            print(f"staging: {t_stage:,.1f} s = {rows / t_stage:,.0f} filas/s", flush=True)  # noqa: T201
            t2 = time.perf_counter()
            result = dest.apply(stage, info)
            t_apply = time.perf_counter() - t2
            dest.cleanup(stage)

    print(f"filas: {rows:,} (driver {server.driver})")  # noqa: T201
    print(f"spool: {t_spool:,.1f} s")  # noqa: T201
    print(f"staging (fast_executemany): {t_stage:,.1f} s = {rows / t_stage:,.0f} filas/s")  # noqa: T201
    print(f"apply (INSERT…SELECT + control): {t_apply:,.1f} s")  # noqa: T201
    print(f"insertadas: {result.rows_inserted:,}")  # noqa: T201


if __name__ == "__main__":
    main()
