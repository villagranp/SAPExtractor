from __future__ import annotations

import pytest

from sapextract.core.exceptions import BusinessRuleError
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.sap_connections.domain.metadata import EdmEntityType, EdmProperty, OdpRole
from sapextract.sources.domain.delta_processes import hint_for, semantics_for_process
from sapextract.sources.domain.filters import (
    FilterCondition,
    FilterOperator,
    check_filters,
    literal,
    render_filter,
)
from sapextract.sources.domain.model import (
    DeltaConfig,
    DeltaMethod,
    DeltaSemantics,
    SourceStatus,
    SourceType,
    WatermarkKind,
    check_delta,
    check_keys,
    check_transition,
    check_type,
    normalize_code,
    normalize_page_size,
    suggest_type,
)
from sapextract.sources.domain.preview import display_value
from sapextract.sources.domain.schema import (
    DetectedSchema,
    column_names,
    detect_schema,
    diff_schemas,
    with_keys,
)


def _entity_type(*props: EdmProperty, keys: tuple[str, ...] = ("BELNR",)) -> EdmEntityType:
    return EdmEntityType(name="T", namespace="NS", keys=keys, properties=props)


def _gl() -> DetectedSchema:
    return detect_schema(
        _entity_type(
            EdmProperty(name="BELNR", edm_type="Edm.String", max_length=10, nullable=False),
            EdmProperty(name="RBUKRS", edm_type="Edm.String", max_length=4),
            EdmProperty(name="BUDAT", edm_type="Edm.DateTime", display_format="Date"),
            EdmProperty(name="CHANGED_AT", edm_type="Edm.DateTime"),
            EdmProperty(name="STAMP", edm_type="Edm.DateTimeOffset"),
            EdmProperty(name="TS", edm_type="Edm.Decimal", precision=15, scale=0),
            EdmProperty(name="DMBTR", edm_type="Edm.Decimal", precision=17, scale=2),
            EdmProperty(name="GJAHR", edm_type="Edm.Int32"),
            EdmProperty(name="XREVERSAL", edm_type="Edm.Boolean"),
            EdmProperty(name="NOTE", edm_type="Edm.String", filterable=False),
        )
    )


def _code(exc: pytest.ExceptionInfo[BusinessRuleError]) -> str:
    return exc.value.error_code


# ------------------------------------------------------------------ modelo
def test_code_and_page_size_rules() -> None:
    assert normalize_code(" fi_gl_items ") == "FI_GL_ITEMS"
    with pytest.raises(BusinessRuleError) as exc:
        normalize_code("1X")
    assert _code(exc) == "SX-SRC-CODE-INVALID"
    assert normalize_page_size(None) == 5000
    with pytest.raises(BusinessRuleError):
        normalize_page_size(50)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OdpRole.ENTITY, SourceType.ODP_EXTRACTOR),
        (OdpRole.FACTS, SourceType.CDS_ODP),
        (OdpRole.ATTRIBUTES, SourceType.CDS_ODP),
        (None, SourceType.CDS_ODATA),
    ],
)
def test_type_suggestion_follows_entity_set(role: OdpRole | None, expected: SourceType) -> None:
    assert suggest_type(role) is expected
    check_type(expected, role)


@pytest.mark.parametrize(
    ("source_type", "role", "code"),
    [
        (SourceType.CDS_ODATA, OdpRole.ENTITY, "SX-SRC-TYPE-MISMATCH"),
        (SourceType.ODP_EXTRACTOR, None, "SX-SRC-TYPE-MISMATCH"),
        (SourceType.ODP_EXTRACTOR, OdpRole.DELTA_LINKS, "SX-SRC-ENTITY-SET-INVALID"),
        (SourceType.CDS_ODP, OdpRole.HIERARCHY, "SX-SRC-ENTITY-SET-INVALID"),
    ],
)
def test_type_mismatch_is_rejected(
    source_type: SourceType, role: OdpRole | None, code: str
) -> None:
    with pytest.raises(BusinessRuleError) as exc:
        check_type(source_type, role)
    assert _code(exc) == code


def test_keys_are_required_and_must_exist() -> None:
    schema = _gl().schema
    assert check_keys(("RBUKRS", "BELNR", "RBUKRS"), schema) == ("RBUKRS", "BELNR")
    with pytest.raises(BusinessRuleError) as exc:
        check_keys((), schema)
    assert _code(exc) == "SX-SRC-KEYS-REQUIRED"
    with pytest.raises(BusinessRuleError) as exc:
        check_keys(("NOPE",), schema)
    assert _code(exc) == "SX-SRC-KEYS-UNKNOWN"


def test_odq_delta_needs_odp_and_change_tracking() -> None:
    schema = _gl().schema
    odq = DeltaConfig(method=DeltaMethod.ODQ)
    result = check_delta(
        odq, source_type=SourceType.ODP_EXTRACTOR, supports_delta=True, schema=schema
    )
    assert result.semantics is DeltaSemantics.UNKNOWN  # sin declarar: solo append
    for source_type, supports in ((SourceType.CDS_ODATA, True), (SourceType.CDS_ODP, False)):
        with pytest.raises(BusinessRuleError) as exc:
            check_delta(odq, source_type=source_type, supports_delta=supports, schema=schema)
        assert _code(exc) == "SX-SRC-DELTA-UNSUPPORTED"


def test_watermark_only_for_cds_odata() -> None:
    config = DeltaConfig(
        method=DeltaMethod.WATERMARK,
        watermark_field="CHANGED_AT",
        watermark_kind=WatermarkKind.DATETIME,
        watermark_timezone="America/Guatemala",
    )
    schema = _gl().schema
    result = check_delta(
        config, source_type=SourceType.CDS_ODATA, supports_delta=False, schema=schema
    )
    assert result.watermark_overlap_seconds == 300
    assert result.semantics is None
    with pytest.raises(BusinessRuleError) as exc:
        check_delta(config, source_type=SourceType.CDS_ODP, supports_delta=True, schema=schema)
    assert _code(exc) == "SX-SRC-DELTA-UNSUPPORTED"


@pytest.mark.parametrize(
    ("field", "kind", "timezone", "code"),
    [
        (None, WatermarkKind.DATETIME, "UTC", "SX-SRC-WATERMARK-REQUIRED"),
        ("NOPE", WatermarkKind.DATETIME, "UTC", "SX-SRC-WATERMARK-UNKNOWN"),
        ("BUDAT", WatermarkKind.DATETIME, "UTC", "SX-SRC-WATERMARK-TYPE"),
        ("RBUKRS", WatermarkKind.DATE, "UTC", "SX-SRC-WATERMARK-TYPE"),
        ("CHANGED_AT", WatermarkKind.DATETIME, None, "SX-SRC-TIMEZONE-REQUIRED"),
        ("CHANGED_AT", WatermarkKind.DATETIME, "Mars/Olympus", "SX-SRC-TIMEZONE-INVALID"),
    ],
)
def test_watermark_errors(
    field: str | None, kind: WatermarkKind, timezone: str | None, code: str
) -> None:
    config = DeltaConfig(
        method=DeltaMethod.WATERMARK,
        watermark_field=field,
        watermark_kind=kind,
        watermark_timezone=timezone,
    )
    with pytest.raises(BusinessRuleError) as exc:
        check_delta(
            config, source_type=SourceType.CDS_ODATA, supports_delta=False, schema=_gl().schema
        )
    assert _code(exc) == code


def test_datetimeoffset_watermark_is_always_utc() -> None:
    config = DeltaConfig(
        method=DeltaMethod.WATERMARK,
        watermark_field="STAMP",
        watermark_kind=WatermarkKind.DATETIME,
        watermark_timezone="Europe/Madrid",
    )
    result = check_delta(
        config, source_type=SourceType.CDS_ODATA, supports_delta=False, schema=_gl().schema
    )
    assert result.watermark_timezone == "UTC"


def test_decimal_timestamp_and_date_watermarks() -> None:
    schema = _gl().schema
    for field, kind in (("TS", WatermarkKind.DECIMAL_TIMESTAMP), ("BUDAT", WatermarkKind.DATE)):
        config = DeltaConfig(
            method=DeltaMethod.WATERMARK,
            watermark_field=field,
            watermark_kind=kind,
            watermark_timezone="UTC",
        )
        check_delta(config, source_type=SourceType.CDS_ODATA, supports_delta=False, schema=schema)


def test_status_transitions() -> None:
    check_transition(SourceStatus.DRAFT, SourceStatus.ACTIVE)
    check_transition(SourceStatus.ACTIVE, SourceStatus.DEPRECATED)
    check_transition(SourceStatus.DEPRECATED, SourceStatus.ACTIVE)
    with pytest.raises(BusinessRuleError):
        check_transition(SourceStatus.DRAFT, SourceStatus.DEPRECATED)


# ------------------------------------------------------------------ semántica del delta
def test_delta_process_codes_map_to_semantics() -> None:
    assert semantics_for_process("abr") is DeltaSemantics.BEFORE_AFTER
    assert semantics_for_process("ADDD") is DeltaSemantics.ADDITIVE
    assert semantics_for_process("AIMD") is DeltaSemantics.AFTER_IMAGE
    assert semantics_for_process("ZZZ") is DeltaSemantics.UNKNOWN
    hint = hint_for("2lis_11_vaitm")
    assert hint is not None
    assert hint.semantics is DeltaSemantics.BEFORE_AFTER
    assert hint_for("ZFI_CUSTOM") is None


# ------------------------------------------------------------------ filtros
@pytest.mark.parametrize(
    ("field", "value", "version", "expected"),
    [
        ("RBUKRS", "1000", "2.0", "'1000'"),
        ("RBUKRS", "O'B", "2.0", "'O''B'"),
        ("GJAHR", 2026, "2.0", "2026"),
        ("DMBTR", "12.50", "2.0", "12.50M"),
        ("DMBTR", "12.50", "4.0", "12.50"),
        ("XREVERSAL", True, "2.0", "true"),
        ("BUDAT", "2026-01-31", "2.0", "datetime'2026-01-31T00:00:00'"),
        ("CHANGED_AT", "2026-01-31T08:30:00", "2.0", "datetime'2026-01-31T08:30:00'"),
        ("STAMP", "2026-01-31T08:30:00Z", "2.0", "datetimeoffset'2026-01-31T08:30:00Z'"),
        ("STAMP", "2026-01-31T08:30:00Z", "4.0", "2026-01-31T08:30:00Z"),
    ],
)
def test_filter_literals(field: str, value: object, version: str, expected: str) -> None:
    column = _gl().schema.column(field)
    assert column is not None
    assert literal(column, value, odata_version=version) == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("RBUKRS", "12345"),  # más largo que MaxLength 4
        ("GJAHR", "20.5"),
        ("GJAHR", True),
        ("XREVERSAL", "yes"),
        ("BUDAT", "31/01/2026"),
        ("CHANGED_AT", "2026-01-31T08:30:00+02:00"),  # hora local SAP: sin desfase
        ("STAMP", "2026-01-31T08:30:00"),
        ("DMBTR", "NaN"),
        ("RBUKRS", ""),
    ],
)
def test_invalid_filter_values(field: str, value: object) -> None:
    column = _gl().schema.column(field)
    assert column is not None
    with pytest.raises(BusinessRuleError) as exc:
        literal(column, value, odata_version="2.0")
    assert _code(exc) == "SX-SRC-FILTER-VALUE"


def test_filters_check_fields_and_render() -> None:
    detected = _gl()
    conditions = [
        FilterCondition(field="RBUKRS", op=FilterOperator.EQ, value="1000"),
        FilterCondition(field="GJAHR", op=FilterOperator.GE, value=2025),
    ]
    checked = check_filters(
        conditions, detected.schema, filterable=detected.filterable, odata_version="2.0"
    )
    assert render_filter(checked, detected.schema, "2.0") == "RBUKRS eq '1000' and GJAHR ge 2025"
    assert render_filter([], detected.schema, "2.0") == ""
    for field in ("NOTE", "NOPE"):
        with pytest.raises(BusinessRuleError) as exc:
            check_filters(
                [FilterCondition(field=field, op=FilterOperator.EQ, value="x")],
                detected.schema,
                filterable=detected.filterable,
                odata_version="2.0",
            )
        assert _code(exc) == "SX-SRC-FILTER-FIELD"
        assert exc.value.errors[0].field == "default_filters[0]"


def test_filter_round_trip() -> None:
    condition = FilterCondition(field="RBUKRS", op=FilterOperator.NE, value="2000")
    assert FilterCondition.from_dict(condition.to_dict()) == condition


# ------------------------------------------------------------------ esquema
def test_detect_schema_keeps_order_keys_and_filterable() -> None:
    detected = _gl()
    assert column_names(detected.schema)[:3] == ("BELNR", "RBUKRS", "BUDAT")
    assert detected.schema.keys == ("BELNR",)
    assert "NOTE" not in detected.filterable
    restored = DetectedSchema.from_json(detected.to_json())
    assert restored == detected
    assert restored.schema.schema_hash() == detected.schema.schema_hash()


def test_detect_schema_rejects_reserved_and_repeated_columns() -> None:
    for props in (
        (EdmProperty(name="BELNR", edm_type="Edm.String", nullable=False),) * 2,
        (
            EdmProperty(name="BELNR", edm_type="Edm.String", nullable=False),
            EdmProperty(name="_sx_batch_id", edm_type="Edm.String"),
        ),
    ):
        with pytest.raises(BusinessRuleError) as exc:
            detect_schema(_entity_type(*props))
        assert _code(exc) == "SX-SRC-SCHEMA-INVALID"


def test_unknown_edm_types_become_text() -> None:
    # Contrato común (dataflow/types.py): un tipo no previsto se lleva como texto.
    detected = detect_schema(
        _entity_type(
            EdmProperty(name="BELNR", edm_type="Edm.String", nullable=False),
            EdmProperty(name="GEO", edm_type="Edm.GeographyPoint"),
        )
    )
    assert str(detected.schema.column("GEO").arrow_type) == "string"


def test_with_keys_moves_key_flags() -> None:
    schema = with_keys(_gl().schema, ("RBUKRS", "GJAHR"))
    assert schema.keys == ("RBUKRS", "GJAHR")
    assert not schema.column("GJAHR").nullable
    assert not schema.column("BELNR").is_key


def _schema(*columns: Column) -> EntitySchema:
    return EntitySchema(columns=columns)


def _col(name: str, edm: str = "Edm.String", **kwargs: object) -> Column:
    return Column(name=name, edm_type=edm, is_key=name == "ID", **kwargs)  # type: ignore[arg-type]


def test_schema_diff_classifies_changes() -> None:
    old = _schema(
        _col("ID", max_length=10),
        _col("NAME", max_length=20),
        _col("AMOUNT", "Edm.Decimal", precision=13, scale=2),
        _col("GONE"),
    )
    new = _schema(
        _col("ID", max_length=10),
        _col("NAME", max_length=40),  # ensanchado: compatible
        _col("AMOUNT", "Edm.Decimal", precision=15, scale=3),  # otra escala: incompatible
        _col("NEW"),
    )
    d = diff_schemas(old, new)
    assert [c.name for c in d.added] == ["NEW"]
    assert [c.name for c in d.removed] == ["GONE"]
    assert {c.name: c.breaking for c in d.changed} == {"NAME": False, "AMOUNT": True}
    assert d.breaking
    assert not d.reordered


def test_schema_diff_added_column_is_not_breaking() -> None:
    old = _schema(_col("ID"), _col("A"))
    new = _schema(_col("ID"), _col("B"), _col("A"))
    d = diff_schemas(old, new)
    assert not d.breaking
    assert not d.empty
    assert diff_schemas(old, old).empty
    assert diff_schemas(old, _schema(_col("A"), _col("ID"))).reordered


def test_type_change_is_breaking() -> None:
    d = diff_schemas(_schema(_col("ID"), _col("X")), _schema(_col("ID"), _col("X", "Edm.Int32")))
    assert d.breaking


# ------------------------------------------------------------------ vista previa
@pytest.mark.parametrize(
    ("edm", "fmt", "raw", "shown"),
    [
        ("Edm.DateTime", "Date", "/Date(1767139200000)/", "2025-12-31"),
        ("Edm.DateTime", None, "/Date(1767184200000)/", "2025-12-31T12:30:00"),
        ("Edm.DateTimeOffset", None, "/Date(1767184200000+0000)/", "2025-12-31T12:30:00Z"),
        ("Edm.Time", None, "PT08H05M09S", "08:05:09"),
        ("Edm.DateTimeOffset", None, "2025-12-31T12:30:00Z", "2025-12-31T12:30:00Z"),
        ("Edm.Decimal", None, "12.50", "12.50"),
        ("Edm.Int32", None, 7, 7),
    ],
)
def test_preview_values_are_readable(edm: str, fmt: str | None, raw: object, shown: object) -> None:
    assert display_value(edm, fmt, raw) == shown


@pytest.mark.parametrize("value", ["1e1000000", "1E5", "1" * 39, "1234567890123456.5", "1.234"])
def test_decimal_filter_values_cannot_expand(value: str) -> None:
    # DMBTR es Edm.Decimal(17,2): 15 dígitos enteros y 2 decimales como máximo.
    column = _gl().schema.column("DMBTR")
    assert column is not None
    with pytest.raises(BusinessRuleError) as exc:
        literal(column, value, odata_version="2.0")
    assert _code(exc) == "SX-SRC-FILTER-VALUE"


def test_decimal_filter_within_precision_is_accepted() -> None:
    column = _gl().schema.column("DMBTR")
    assert column is not None
    assert literal(column, "123456789012345.99", odata_version="2.0") == "123456789012345.99M"
