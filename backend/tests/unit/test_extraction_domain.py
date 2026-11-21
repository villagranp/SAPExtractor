"""Reglas puras del motor: conversión EDM → Arrow, token de delta, modo del run y pipelines."""
# ruff: noqa: DTZ001 - las horas SAP clásicas no llevan zona (H15): se comparan sin ella.

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pyarrow as pa
import pytest

from sapextract.core.exceptions import BusinessRuleError, ConflictError
from sapextract.dataflow.domain.batch import LoadMode, RunMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.dataflow.domain.schema import CHANGE_MODE, EXTRACTED_AT, Column, EntitySchema
from sapextract.extraction.domain.convert import ConversionError, PageConverter
from sapextract.extraction.domain.model import (
    AttemptOutcome,
    DeltaSnapshot,
    DeltaStatus,
    backoff,
    filters_hash,
    outcome_for,
    run_mode_for,
)
from sapextract.extraction.infrastructure.odata_connector import _bound, local_moment, token_of
from sapextract.orchestration.domain.model import (
    ExtractionMode,
    check_attempts,
    check_extraction_mode,
    check_load_mode,
    check_timeout,
    load_options_from_json,
    load_options_to_json,
    normalize_code,
)
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics, WatermarkKind

SCHEMA = EntitySchema(
    columns=(
        Column(name="BELNR", edm_type="Edm.String", nullable=False, is_key=True, max_length=10),
        Column(name="BUDAT", edm_type="Edm.DateTime", display_format="Date"),
        Column(name="CPUDT", edm_type="Edm.DateTime"),
        Column(name="CHANGED", edm_type="Edm.DateTimeOffset"),
        Column(name="DMBTR", edm_type="Edm.Decimal", precision=17, scale=2),
        Column(name="CNT", edm_type="Edm.Int64"),
        Column(name="FLAG", edm_type="Edm.Boolean"),
        Column(name="CPUTM", edm_type="Edm.Time"),
        Column(name="RATE", edm_type="Edm.Double"),
        Column(name="RAW", edm_type="Edm.Binary"),
        Column(name="ODQ_CHANGEMODE", edm_type="Edm.String", max_length=1),
    )
)
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "BELNR": "0000000042",
        "BUDAT": "/Date(1763596800000)/",  # 2025-11-20
        "CPUDT": "/Date(1763643600000)/",  # 2025-11-20 13:00 hora SAP
        "CHANGED": "/Date(1763643600000+0060)/",  # 13:00 en +01:00 → 12:00 UTC
        "DMBTR": "1234.50",
        "CNT": "9007199254740993",  # Int64 llega como texto en V2
        "FLAG": True,
        "CPUTM": "PT12H30M05S",
        "RATE": 1.5,
        "RAW": "AAE=",
        "ODQ_CHANGEMODE": "",
    }
    row.update(overrides)
    return row


def test_v2_values_are_converted_without_shifting_local_times() -> None:
    batch = PageConverter(SCHEMA, change_mode=None).convert([_row()], NOW)
    data = batch.to_pylist()[0]
    assert data["BELNR"] == "0000000042"  # los ceros a la izquierda se conservan
    assert data["BUDAT"] == date(2025, 11, 20)
    assert data["CPUDT"] == datetime(2025, 11, 20, 13, 0)  # sin zona (H15)
    assert data["CHANGED"] == datetime(2025, 11, 20, 12, 0, tzinfo=UTC)
    assert data["DMBTR"] == Decimal("1234.50")
    assert data["CNT"] == 9007199254740993
    assert data["CPUTM"] == time(12, 30, 5)
    assert data["RAW"] == b"\x00\x01"
    assert data[CHANGE_MODE] is None
    assert data[EXTRACTED_AT] == NOW
    assert batch.schema.field("DMBTR").type == pa.decimal128(17, 2)


def test_v4_iso_values_and_empty_dates() -> None:
    row = _row(
        BUDAT="2025-11-20",
        CPUDT="2025-11-20T13:00:00",
        CHANGED="2025-11-20T12:00:00Z",
        CPUTM="12:30:05",
    )
    data = PageConverter(SCHEMA, change_mode=None).convert([row], NOW).to_pylist()[0]
    assert data["BUDAT"] == date(2025, 11, 20)
    assert data["CHANGED"] == datetime(2025, 11, 20, 12, 0, tzinfo=UTC)
    empty = PageConverter(SCHEMA, change_mode=None).convert([_row(BUDAT="00000000")], NOW)
    assert empty.to_pylist()[0]["BUDAT"] is None


def test_change_mode_from_odq() -> None:
    rows = [_row(ODQ_CHANGEMODE=m) for m in ("", "U", "D", "x")]
    init = PageConverter(SCHEMA, change_mode="init").convert(rows, NOW).column(CHANGE_MODE)
    assert init.to_pylist() == ["C", "U", "D", "C"]
    delta = PageConverter(SCHEMA, change_mode="delta").convert(rows, NOW).column(CHANGE_MODE)
    assert delta.to_pylist() == [None, "U", "D", None]


@pytest.mark.parametrize(
    ("field", "value"),
    [("DMBTR", "1.234"), ("DMBTR", "abc"), ("CNT", "1.5"), ("FLAG", "quizá"), ("BELNR", None)],
)
def test_conversion_errors_are_data_errors(field: str, value: object) -> None:
    with pytest.raises(ConversionError) as info:
        PageConverter(SCHEMA, change_mode=None).convert([_row(**{field: value})], NOW)
    assert info.value.kind is ErrorKind.DATA
    assert info.value.column == field


def test_trailing_zero_decimals_are_accepted() -> None:
    data = PageConverter(SCHEMA, change_mode=None).convert([_row(DMBTR="10.500")], NOW)
    assert data.to_pylist()[0]["DMBTR"] == Decimal("10.50")


def test_delta_token_from_links() -> None:
    base = "https://sap:44300/sap/opu/odata/sap/ZSRV/EntityOfX"
    assert token_of(f"{base}?!deltatoken='D000000000123'") == "D000000000123"
    assert token_of(f"{base}?%21deltatoken=%27D20261120%27") == "D20261120"
    assert token_of(f"{base}?$deltatoken=abc123") == "abc123"
    assert token_of(None) is None
    assert token_of(f"{base}?$skiptoken=10") is None


def test_watermark_bounds_and_local_time() -> None:
    moment = datetime(2026, 11, 20, 10, 15, 30, 999)
    assert _bound(WatermarkKind.DATE, moment) == "2026-11-20"
    assert _bound(WatermarkKind.DECIMAL_TIMESTAMP, moment) == "20261120101530"
    assert _bound(WatermarkKind.DATETIME, moment) == "2026-11-20T10:15:30"
    instant = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    assert local_moment(instant, "America/Guatemala") == datetime(2026, 11, 20, 12, 0)
    assert local_moment(instant, "UTC") == datetime(2026, 11, 20, 18, 0)


def test_run_mode_follows_delta_state() -> None:
    fhash = filters_hash("RBUKRS eq '1000'")
    initialized = DeltaSnapshot(
        status=DeltaStatus.INITIALIZED, sap_close_pending=False, init_filters_hash=fhash
    )
    assert run_mode_for(ExtractionMode.FULL, None, fhash) is RunMode.FULL
    assert run_mode_for(ExtractionMode.WATERMARK, None, fhash) is RunMode.WATERMARK
    assert run_mode_for(ExtractionMode.DELTA, None, fhash) is RunMode.DELTA_INIT
    assert run_mode_for(ExtractionMode.DELTA, initialized, fhash) is RunMode.DELTA
    cases = {
        "SX-PPL-DELTA-FILTERS-CHANGED": (initialized, filters_hash("")),
        "SX-PPL-DELTA-BROKEN": (
            DeltaSnapshot(
                status=DeltaStatus.BROKEN, sap_close_pending=False, init_filters_hash=None
            ),
            fhash,
        ),
        "SX-PPL-SAP-CLOSE-PENDING": (
            DeltaSnapshot(
                status=DeltaStatus.NOT_INITIALIZED, sap_close_pending=True, init_filters_hash=None
            ),
            fhash,
        ),
        "SX-PPL-RUN-ACTIVE": (
            DeltaSnapshot(
                status=DeltaStatus.INITIALIZING, sap_close_pending=False, init_filters_hash=None
            ),
            fhash,
        ),
    }
    for code, (snapshot, current) in cases.items():
        with pytest.raises(ConflictError) as info:
            run_mode_for(ExtractionMode.DELTA, snapshot, current)
        assert info.value.error_code == code


def test_retry_rules() -> None:
    assert outcome_for(ErrorKind.TRANSIENT, mode=RunMode.DELTA, attempts_left=1) is (
        AttemptOutcome.FAILED_TRANSIENT
    )
    assert outcome_for(ErrorKind.TRANSIENT, mode=RunMode.DELTA, attempts_left=0) is (
        AttemptOutcome.FAILED
    )
    # H10: una init no se reintenta sola.
    assert outcome_for(ErrorKind.TRANSIENT, mode=RunMode.DELTA_INIT, attempts_left=2) is (
        AttemptOutcome.FAILED
    )
    for kind in (ErrorKind.CONFIGURATION, ErrorKind.DATA, ErrorKind.DELTA):
        assert outcome_for(kind, mode=RunMode.FULL, attempts_left=5) is AttemptOutcome.FAILED
    assert backoff(1) == timedelta(minutes=1)
    assert backoff(2) == timedelta(minutes=5)
    assert backoff(9) == timedelta(minutes=15)
    assert backoff(1, ()) == timedelta(0)


def test_pipeline_rules() -> None:
    assert normalize_code(" fi_gl_snow ") == "FI_GL_SNOW"
    with pytest.raises(BusinessRuleError):
        normalize_code("1X")
    check_extraction_mode(ExtractionMode.DELTA, DeltaMethod.ODQ)
    check_extraction_mode(ExtractionMode.FULL, DeltaMethod.NONE)
    for mode, method in (
        (ExtractionMode.DELTA, DeltaMethod.WATERMARK),
        (ExtractionMode.WATERMARK, DeltaMethod.ODQ),
        (ExtractionMode.DELTA, DeltaMethod.NONE),
    ):
        with pytest.raises(BusinessRuleError) as info:
            check_extraction_mode(mode, method)
        assert info.value.error_code == "SX-PPL-MODE-UNSUPPORTED"
    check_load_mode(ExtractionMode.DELTA, LoadMode.UPSERT, DeltaSemantics.AFTER_IMAGE)
    check_load_mode(ExtractionMode.DELTA, LoadMode.APPEND, DeltaSemantics.ADDITIVE)
    check_load_mode(ExtractionMode.FULL, LoadMode.REPLACE, None)
    for mode, load, semantics in (
        (ExtractionMode.DELTA, LoadMode.UPSERT, DeltaSemantics.ADDITIVE),
        (ExtractionMode.DELTA, LoadMode.UPSERT, DeltaSemantics.UNKNOWN),
        (ExtractionMode.WATERMARK, LoadMode.REPLACE, None),
    ):
        with pytest.raises(BusinessRuleError):
            check_load_mode(mode, load, semantics)
    assert check_timeout(None) == 240
    assert check_attempts(None) == 3
    for fn, value in ((check_timeout, 1), (check_attempts, 11)):
        with pytest.raises(BusinessRuleError):
            fn(value)
    options = load_options_from_json(
        {"load_mode": "upsert", "target_name": "GL", "column_mapping": {"X": None}}
    )
    assert load_options_from_json(load_options_to_json(options)) == options
