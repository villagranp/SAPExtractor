"""Contratos del flujo de datos (ADR 0006): tipos, esquema, batch_id y spool."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from sapextract.dataflow.domain.batch import BatchInfo, RunMode, batch_id, is_batch_id
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.schema import (
    RECORD_SEQ,
    TECHNICAL_NAMES,
    Column,
    EntitySchema,
)
from sapextract.dataflow.domain.spool import MANIFEST_NAME
from sapextract.dataflow.domain.types import arrow_type
from sapextract.dataflow.infrastructure.parquet_spool import (
    DirectorySpool,
    InvalidSpoolError,
    spool_path,
)
from tests.support.dataflow import GL_SCHEMA, gl_row, make_spool

PIPELINE = uuid.UUID("01900000-0000-7000-8000-000000000001")
RUN = uuid.UUID("01900000-0000-7000-8000-000000000002")


@pytest.mark.parametrize(
    ("edm", "kwargs", "expected"),
    [
        ("Edm.String", {}, pa.string()),
        ("Edm.Int32", {}, pa.int32()),
        ("Edm.Int64", {}, pa.int64()),
        ("Edm.Decimal", {"precision": 17, "scale": 2}, pa.decimal128(17, 2)),
        ("Edm.Decimal", {"precision": 60, "scale": 3}, pa.decimal128(38, 3)),
        ("Edm.Decimal", {}, pa.decimal128(38, 10)),
        ("Edm.DateTime", {"display_format": "Date"}, pa.date32()),
        ("Edm.DateTime", {}, pa.timestamp("us")),  # hora local SAP, sin zona
        ("Edm.DateTimeOffset", {}, pa.timestamp("us", tz="UTC")),
        ("Edm.Time", {}, pa.time64("us")),
        ("Edm.Boolean", {}, pa.bool_()),
        ("Edm.Guid", {}, pa.string()),
        ("Edm.Binary", {}, pa.binary()),
        ("Edm.Desconocido", {}, pa.string()),
    ],
)
def test_edm_to_arrow(edm: str, kwargs: dict[str, object], expected: pa.DataType) -> None:
    assert arrow_type(edm, **kwargs) == expected  # type: ignore[arg-type]


def test_schema_hash_ignores_labels_but_not_types() -> None:
    labelled = EntitySchema(
        columns=tuple(Column(**{**c.__dict__, "label": "otra"}) for c in GL_SCHEMA.columns)
    )
    assert labelled.schema_hash() == GL_SCHEMA.schema_hash()
    retyped = EntitySchema(
        columns=(Column(name="RBUKRS", edm_type="Edm.Int32", is_key=True), *GL_SCHEMA.columns[1:])
    )
    assert retyped.schema_hash() != GL_SCHEMA.schema_hash()
    assert EntitySchema.from_list(GL_SCHEMA.as_list()) == GL_SCHEMA
    assert GL_SCHEMA.keys == ("RBUKRS", "BELNR")


def test_schema_rejects_duplicates_and_reserved_names() -> None:
    with pytest.raises(ValueError, match="repetidas"):
        EntitySchema(columns=(Column(name="A", edm_type="Edm.String"),) * 2)
    with pytest.raises(ValueError, match="reservadas"):
        EntitySchema(columns=(Column(name="_sx_deleted", edm_type="Edm.Boolean"),))
    arrow = GL_SCHEMA.arrow_schema()
    assert set(arrow.names) >= TECHNICAL_NAMES - {"_sx_deleted"}


def test_batch_id_is_deterministic_per_mode() -> None:
    a = batch_id(RunMode.DELTA, pipeline_id=PIPELINE, generation=1, start_token="D1")
    assert a == batch_id(RunMode.DELTA, pipeline_id=PIPELINE, generation=1, start_token="D1")
    assert a != batch_id(RunMode.DELTA, pipeline_id=PIPELINE, generation=2, start_token="D1")
    assert a.startswith("d-")
    assert is_batch_id(a)
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    w = batch_id(RunMode.WATERMARK, pipeline_id=PIPELINE, window_start=start, window_end=end)
    assert w.startswith("w-")
    assert batch_id(RunMode.FULL, pipeline_id=PIPELINE, run_id=RUN).startswith("f-")
    init = batch_id(RunMode.DELTA_INIT, pipeline_id=PIPELINE, run_id=RUN, generation=1)
    assert init.startswith("i-")
    with pytest.raises(ValueError, match="token"):
        batch_id(RunMode.DELTA, pipeline_id=PIPELINE, generation=1)
    with pytest.raises(ValueError, match="batch_id"):
        BatchInfo(
            pipeline_id=PIPELINE, run_id=RUN, batch_id="../x", mode=RunMode.FULL, schema_hash=""
        )


def test_errors_know_if_they_are_retryable() -> None:
    assert DataflowError("x", kind=ErrorKind.TRANSIENT, code="SX-DST-X").retryable
    assert not DataflowError("x", kind=ErrorKind.DATA, code="SX-DST-X").retryable


def test_spool_roundtrip_with_files_and_sequence(tmp_path: Path) -> None:
    rows = [gl_row(i) for i in range(1, 2501)]
    sample = make_spool(tmp_path, rows, max_rows_per_file=1000, chunk=700)
    manifest = sample.spool.manifest
    assert [f.rows for f in manifest.files] == [1000, 1000, 500]
    assert manifest.rows == 2500
    assert manifest.next_token == "D000000000002"
    read = pa.Table.from_batches(list(sample.spool.batches()))
    assert read.num_rows == 2500
    assert read.column(RECORD_SEQ).to_pylist() == list(range(1, 2501))
    assert set(read.column("_sx_batch_id").to_pylist()) == {sample.info.batch_id}
    assert read.schema.field("CPUDT").type == pa.timestamp("us")
    assert read.column("DMBTR").to_pylist()[0] == rows[0]["DMBTR"]
    verified = DirectorySpool.open(tmp_path, sample.info.batch_id, verify_hashes=True)
    assert len(verified.parquet_files()) == 3


def test_spool_without_manifest_or_tampered_is_invalid(tmp_path: Path) -> None:
    sample = make_spool(tmp_path, [gl_row(1), gl_row(2)])
    bid = sample.info.batch_id
    with pytest.raises(InvalidSpoolError, match="esquema"):
        DirectorySpool.open(tmp_path, bid, schema_hash="otro")
    part = sample.spool.parquet_files()[0]
    part.write_bytes(part.read_bytes() + b"x")
    with pytest.raises(InvalidSpoolError, match="cambió"):
        DirectorySpool.open(tmp_path, bid)
    (spool_path(tmp_path, bid) / MANIFEST_NAME).unlink()
    with pytest.raises(InvalidSpoolError, match="manifiesto"):
        DirectorySpool.open(tmp_path, bid)


def test_manifest_hash_must_match_its_schema(tmp_path: Path) -> None:
    sample = make_spool(tmp_path, [gl_row(1)])
    path = spool_path(tmp_path, sample.info.batch_id) / MANIFEST_NAME
    data = json.loads(path.read_text("utf-8"))
    data["schema"][0]["edm_type"] = "Edm.Int32"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InvalidSpoolError, match="hash"):
        DirectorySpool.open(tmp_path, sample.info.batch_id)


def test_spool_path_never_accepts_free_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="batch_id"):
        spool_path(tmp_path, "../../etc")
