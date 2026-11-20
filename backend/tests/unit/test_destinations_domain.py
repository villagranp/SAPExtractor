"""Dominio de destinos y SQL de Snowflake, sin conexión (plan 07)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from sapextract.core.exceptions import BusinessRuleError
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, PreparedTarget
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.destinations.domain.model import (
    CsvRoots,
    check_identifier,
    check_load_options,
    resolve_columns,
    target_column_name,
)
from sapextract.destinations.infrastructure import snowflake_sql as sql
from sapextract.destinations.infrastructure.snowflake_plugin import (
    SnowflakeConfig,
    SnowflakeSecret,
    map_error,
    public_key_fingerprint,
)
from tests.support.dataflow import GL_SCHEMA


def _code(exc: pytest.ExceptionInfo[BusinessRuleError]) -> str:
    return exc.value.error_code


# ------------------------------------------------------------------ nombres
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("BELNR", "BELNR"),
        ("/BIC/ZFIELD", "BIC_ZFIELD"),
        ("CompanyCode", "COMPANYCODE"),
        ("0CALDAY", "C_0CALDAY"),
    ],
)
def test_target_column_names(source: str, target: str) -> None:
    assert target_column_name(source) == target


@pytest.mark.parametrize(
    "bad",
    ['x"; DROP TABLE t; --', "a b", "1abc", "tabla-con-guion", "", "ñandú", "a" * 129],
)
def test_malicious_or_invalid_identifiers_are_rejected(bad: str) -> None:
    with pytest.raises(BusinessRuleError) as exc:
        check_identifier(bad, "target_name")
    assert _code(exc) == "SX-DST-IDENTIFIER-INVALID"


def test_column_mapping_renames_excludes_and_detects_clashes() -> None:
    mapped = resolve_columns(GL_SCHEMA, {"DMBTR": "AMOUNT", "XBLOCK": None})
    assert mapped["DMBTR"] == "AMOUNT"
    assert "XBLOCK" not in mapped
    with pytest.raises(BusinessRuleError) as exc:
        resolve_columns(GL_SCHEMA, {"BELNR": None})
    assert _code(exc) == "SX-DST-MAPPING-KEY"
    with pytest.raises(BusinessRuleError) as exc:
        resolve_columns(GL_SCHEMA, {"DMBTR": "BELNR"})
    assert _code(exc) == "SX-DST-MAPPING-CLASH"
    with pytest.raises(BusinessRuleError) as exc:
        resolve_columns(GL_SCHEMA, {"NOEXISTE": "X"})
    assert _code(exc) == "SX-DST-MAPPING-UNKNOWN"
    with pytest.raises(BusinessRuleError):
        resolve_columns(GL_SCHEMA, {"DMBTR": "a;b"})


def test_load_options_check_mode_and_target() -> None:
    check_load_options(
        LoadOptions(load_mode=LoadMode.APPEND, target_name="FI_GL"), frozenset(LoadMode)
    )
    with pytest.raises(BusinessRuleError) as exc:
        check_load_options(
            LoadOptions(load_mode=LoadMode.UPSERT, target_name="FI_GL"),
            frozenset({LoadMode.APPEND}),
        )
    assert _code(exc) == "SX-DST-MODE-UNSUPPORTED"


# ------------------------------------------------------------------ rutas CSV
def test_csv_paths_stay_inside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "csv"
    root.mkdir()
    roots = CsvRoots((root,))
    assert roots.resolve(str(root), "finanzas/gl") == (root / "finanzas" / "gl").resolve()
    assert roots.resolve(str(root), "") == root.resolve()
    for bad in ("../fuera", "a/../../b", "/etc", "C:\\Windows", "a//b"):
        with pytest.raises(BusinessRuleError) as exc:
            roots.resolve(str(root), bad)
        assert _code(exc) == "SX-DST-CSV-PATH", bad
    with pytest.raises(BusinessRuleError) as exc:
        roots.resolve(str(tmp_path / "otra"), "x")
    assert _code(exc) == "SX-DST-CSV-ROOT"


def test_csv_symlink_leaving_the_root_is_rejected(tmp_path: Path) -> None:
    root, outside = tmp_path / "csv", tmp_path / "fuera"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "enlace").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Sin permiso para crear enlaces simbólicos")
    with pytest.raises(BusinessRuleError):
        CsvRoots((root,)).resolve(str(root), "enlace")


# ------------------------------------------------------------------ Snowflake
def _target(**options: object) -> PreparedTarget:
    opts = LoadOptions(load_mode=LoadMode.UPSERT, target_name="FI_GL", **options)  # type: ignore[arg-type]
    names = {s: d.upper() for s, d in resolve_columns(GL_SCHEMA, opts.column_mapping).items()}
    return PreparedTarget(
        target=sql.qualified("SAPX", "RAW", "FI_GL"),
        schema=GL_SCHEMA,
        options=opts,
        column_names=names,
    )


def test_snowflake_identifiers_are_quoted_and_validated() -> None:
    assert sql.ident("fi_gl") == '"FI_GL"'
    assert sql.qualified("db", "raw", "t") == '"DB"."RAW"."T"'
    with pytest.raises(ValueError, match="no válido"):
        sql.ident('x" OR 1=1')
    assert sql.source_ident("/BIC/ZFIELD") == '"/BIC/ZFIELD"'
    with pytest.raises(ValueError, match="no válido"):
        sql.source_ident('A"; DROP')


def test_snowflake_types_and_create_table() -> None:
    ddl = sql.create_table('"SAPX"."RAW"."FI_GL"', sql.target_columns(_target()))
    assert '"DMBTR" NUMBER(17,2)' in ddl
    assert '"BUDAT" DATE' in ddl
    assert '"CPUDT" TIMESTAMP_NTZ' in ddl
    assert '"XBLOCK" BOOLEAN' in ddl
    assert '"_SX_BATCH_ID" VARCHAR(32)' in ddl
    assert '"_SX_DELETED" BOOLEAN' in ddl
    minimal = sql.target_columns(
        _target(technical_columns=False, delete_handling=DeleteHandling.HARD)
    )
    assert [n for n, _ in minimal.technical] == ["_sx_batch_id"]


def test_merge_respects_delete_handling() -> None:
    cols = sql.target_columns(_target())
    soft = sql.merge_statement('"T"', "STG", cols, DeleteHandling.SOFT)
    assert (
        'QUALIFY ROW_NUMBER() OVER (PARTITION BY "RBUKRS", "BELNR" '
        'ORDER BY "_sx_record_seq" DESC) = 1' in soft
    )
    assert 'THEN UPDATE SET t."_SX_DELETED" = TRUE' in soft
    hard = sql.merge_statement('"T"', "STG", cols, DeleteHandling.HARD)
    assert "THEN DELETE" in hard
    ignore = sql.merge_statement('"T"', "STG", cols, DeleteHandling.IGNORE)
    assert "WHERE COALESCE(\"_sx_change_mode\", '') <> 'D'" in ignore
    assert "THEN DELETE" not in ignore
    no_keys = sql.target_columns(
        PreparedTarget(
            target='"T"',
            schema=EntitySchema(columns=(Column(name="A", edm_type="Edm.String"),)),
            options=LoadOptions(load_mode=LoadMode.UPSERT, target_name="T"),
            column_names={"A": "A"},
        )
    )
    with pytest.raises(ValueError, match="clave"):
        sql.merge_statement('"T"', "STG", no_keys, DeleteHandling.SOFT)


def test_append_and_replace_bind_values() -> None:
    cols = sql.target_columns(_target())
    delete, insert = sql.append_statements('"T"', "STG", cols, DeleteHandling.SOFT)
    assert delete.endswith('"_SX_BATCH_ID" = %(batch_id)s')
    assert "COALESCE(s.\"_sx_change_mode\" = 'D', FALSE)" in insert
    assert sql.replace_statement('"T"', "STG", cols, DeleteHandling.IGNORE).startswith(
        'INSERT OVERWRITE INTO "T"'
    )
    assert "%(next_token)s" in sql.insert_control("SAPX", "RAW")
    assert sql.staging_names("d-0123456789abcdef01234567") == (
        "__SX_STG_D_0123456789ABCDEF01234567",
        "__SX_STAGE_D_0123456789ABCDEF01234567",
    )
    assert sql.put_file("C:\\spool\\it's\\part.parquet", "STG").startswith(
        "PUT 'file://C:/spool/it\\'s/part.parquet' @\"STG\""
    )


def test_snowflake_config_and_key() -> None:
    config = SnowflakeConfig(
        account="myorg-ag_test",
        user="svc",
        role="loader",
        warehouse="wh",
        database="db",
        schema_name="raw",
    )
    assert config.host == "myorg-ag-test.snowflakecomputing.com"
    assert config.user == "SVC"
    with pytest.raises(ValidationError):
        SnowflakeConfig(
            account="https://x.snowflakecomputing.com",
            user="u",
            role="r",
            warehouse="w",
            database="d",
            schema_name="s",
        )
    with pytest.raises(ValidationError):
        SnowflakeConfig(
            account="org-acc",
            user="u; drop",
            role="r",
            warehouse="w",
            database="d",
            schema_name="s",
        )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"Frase-1"),
    ).decode()
    good = SnowflakeSecret(
        private_key_pem=SecretStr(pem), private_key_passphrase=SecretStr("Frase-1")
    )
    assert public_key_fingerprint(good).startswith("SHA256:")
    with pytest.raises(BusinessRuleError) as exc:
        public_key_fingerprint(SnowflakeSecret(private_key_pem=SecretStr(pem)))
    assert _code(exc) == "SX-DST-KEY-INVALID"


class _FakeSnowflakeError(Exception):
    def __init__(self, msg: str, errno: int = 0, sqlstate: str = "") -> None:
        super().__init__(msg)
        self.msg, self.errno, self.sqlstate = msg, errno, sqlstate


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (
            _FakeSnowflakeError("JWT token is invalid", 390144),
            ErrorKind.CONFIGURATION,
            "SX-DST-AUTH",
        ),
        (
            _FakeSnowflakeError("Object does not exist or not authorized", 2003, "02000"),
            ErrorKind.CONFIGURATION,
            "SX-DST-PRIVILEGES",
        ),
        (
            _FakeSnowflakeError("Insufficient privileges", 3001, "42501"),
            ErrorKind.CONFIGURATION,
            "SX-DST-PRIVILEGES",
        ),
        (
            _FakeSnowflakeError("Numeric value out of range", 100038, "22003"),
            ErrorKind.DATA,
            "SX-DST-DATA",
        ),
        (_FakeSnowflakeError("Connection reset", 250003), ErrorKind.TRANSIENT, "SX-DST-SNOWFLAKE"),
    ],
)
def test_snowflake_errors_are_classified(error: Exception, kind: ErrorKind, code: str) -> None:
    mapped = map_error(error, "la carga")
    assert (mapped.kind, mapped.code) == (kind, code)
