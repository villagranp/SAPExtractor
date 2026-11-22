"""Destino SQL Server sin conexión: configuración, T-SQL generado y errores (plan 07 §3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pytest
from pydantic import SecretStr, ValidationError

from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, PreparedTarget
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.destinations.domain.model import resolve_columns
from sapextract.destinations.infrastructure import sqlserver_sql as sql
from sapextract.destinations.infrastructure.sqlserver_plugin import (
    SqlServerConfig,
    SqlServerPlugin,
    SqlServerSecret,
    batch_rows,
    connection_string,
    converters,
    map_error,
)
from tests.support.dataflow import GL_SCHEMA


def _config(**kw: Any) -> SqlServerConfig:
    base: dict[str, Any] = {"host": "SQL01.empresa.local", "database": "DWH", "user": "sx_load"}
    return SqlServerConfig(**(base | kw))


def _target(
    mode: LoadMode = LoadMode.UPSERT,
    *,
    schema: EntitySchema = GL_SCHEMA,
    delete: DeleteHandling = DeleteHandling.SOFT,
    technical: bool = True,
) -> PreparedTarget:
    options = LoadOptions(
        load_mode=mode, target_name="GL_ITEMS", delete_handling=delete, technical_columns=technical
    )
    return PreparedTarget(
        target=sql.qualified("dbo", "GL_ITEMS"),
        schema=schema,
        options=options,
        column_names=resolve_columns(schema, {}),
    )


# ------------------------------------------------------------------ configuración
def test_config_normalizes_host_and_keeps_identifiers() -> None:
    config = _config(schema_name="stage")
    assert config.host == "sql01.empresa.local"
    assert config.port == 1433
    assert config.schema_name == "stage"
    assert config.query_timeout_seconds == 7200  # una carga de 10 M filas supera 10 min
    assert config.authentication == "sql"


@pytest.mark.parametrize(
    "host", ["tcp:sql01,1433", "sql01:1433", r"sql01\INST", "https://sql01", "", "a b"]
)
def test_config_rejects_hosts_with_port_instance_or_scheme(host: str) -> None:
    with pytest.raises(ValidationError):
        _config(host=host)


@pytest.mark.parametrize(
    "field", [{"database": "dwh;DROP"}, {"schema_name": "x]"}, {"user": "a;PWD=x"}]
)
def test_config_rejects_injection_in_names(field: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        _config(**field)


def test_connection_string_escapes_secret_and_forces_encryption() -> None:
    secret = SqlServerSecret(password=SecretStr("p}a;ss{"))
    conn = connection_string(_config(), secret, "ODBC Driver 18 for SQL Server")
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in conn
    assert "SERVER=tcp:sql01.empresa.local,1433" in conn
    assert "Encrypt=yes" in conn
    assert "TrustServerCertificate=no" in conn
    assert "PWD={p}}a;ss{}" in conn
    assert "Authentication" not in conn

    entra = _config(authentication="entra_service_principal", user="0000-app", port=14330)
    conn = connection_string(entra, secret, "ODBC Driver 18 for SQL Server")
    assert "Authentication=ActiveDirectoryServicePrincipal" in conn
    assert "UID={0000-app}" in conn
    assert ",14330" in conn


def test_secret_is_write_only_in_the_form_schema() -> None:
    schema = SqlServerSecret.model_json_schema()
    assert schema["properties"]["password"]["writeOnly"] is True
    form = SqlServerConfig.model_json_schema()
    assert set(form["properties"]) >= {"host", "port", "database", "schema_name", "user"}
    assert form["properties"]["authentication"]["enum"] == ["sql", "entra_service_principal"]


# ------------------------------------------------------------------ identificadores y tipos
def test_identifiers_are_validated_and_bracketed() -> None:
    assert sql.ident("GL_ITEMS") == "[GL_ITEMS]"
    assert sql.qualified("dbo", "T") == "[dbo].[T]"
    assert sql.source_ident("/BIC/ZFIELD") == "[/BIC/ZFIELD]"
    for bad in ["x]; DROP TABLE t; --", "a b", "1abc", "a" * 129]:
        with pytest.raises(ValueError, match="no válido"):
            sql.ident(bad)
    with pytest.raises(ValueError, match="no válido"):
        sql.source_ident("x]; --")


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (Column(name="A", edm_type="Edm.String", max_length=10), "NVARCHAR(10)"),
        (Column(name="A", edm_type="Edm.String", max_length=5000), "NVARCHAR(MAX)"),
        (Column(name="A", edm_type="Edm.String"), "NVARCHAR(MAX)"),
        (Column(name="A", edm_type="Edm.Int16"), "SMALLINT"),
        (Column(name="A", edm_type="Edm.Int32"), "INT"),
        (Column(name="A", edm_type="Edm.Int64"), "BIGINT"),
        (Column(name="A", edm_type="Edm.Decimal", precision=17, scale=2), "DECIMAL(17,2)"),
        (Column(name="A", edm_type="Edm.Double"), "FLOAT"),
        (Column(name="A", edm_type="Edm.Boolean"), "BIT"),
        (Column(name="A", edm_type="Edm.DateTime", display_format="Date"), "DATE"),
        (Column(name="A", edm_type="Edm.DateTime"), "DATETIME2(6)"),
        (Column(name="A", edm_type="Edm.DateTimeOffset"), "DATETIME2(6)"),
        (Column(name="A", edm_type="Edm.Time"), "TIME(6)"),
        (Column(name="A", edm_type="Edm.Guid"), "UNIQUEIDENTIFIER"),
        (Column(name="A", edm_type="Edm.Binary"), "VARBINARY(MAX)"),
    ],
)
def test_type_mapping(column: Column, expected: str) -> None:
    assert sql.sqlserver_type(column) == expected


def test_create_table_with_clustered_key_for_upsert() -> None:
    target = _target()
    ddl = SqlServerPlugin().ddl(_config(), GL_SCHEMA, target.options)
    create, index, control = ddl
    assert create.startswith("CREATE TABLE [dbo].[GL_ITEMS]")
    assert "[RBUKRS] NVARCHAR(4) NOT NULL" in create
    assert "[DMBTR] DECIMAL(17,2)," in create
    assert "[_sx_deleted] BIT" in create
    assert "PRIMARY KEY CLUSTERED ([RBUKRS], [BELNR])" in create
    assert index == "CREATE INDEX [IX__sx_batch_id] ON [dbo].[GL_ITEMS] ([_sx_batch_id])"
    assert "IF OBJECT_ID(N'[dbo].[__sx_load_state]', N'U') IS NULL" in control
    assert "PRIMARY KEY ([target_table], [batch_id], [pipeline_id])" in control


def test_append_and_replace_have_no_primary_key() -> None:
    for mode in (LoadMode.APPEND, LoadMode.REPLACE):
        options = LoadOptions(load_mode=mode, target_name="GL_ITEMS")
        create = SqlServerPlugin().ddl(_config(), GL_SCHEMA, options)[0]
        assert "PRIMARY KEY" not in create


def _key_schema(*lengths: int | None) -> EntitySchema:
    return EntitySchema(
        columns=tuple(
            Column(name=f"K{i}", edm_type="Edm.String", is_key=True, max_length=n)
            for i, n in enumerate(lengths)
        )
    )


@pytest.mark.parametrize(
    ("lengths", "clustered", "create", "warning"),
    [
        ((450,), True, True, None),  # 900 bytes: agrupada
        ((451,), False, True, None),  # 902 bytes: no agrupada
        ((850,), False, True, None),  # 1700 bytes: límite del índice no agrupado
        ((851,), False, False, "1702 bytes"),
        ((10, None), False, False, "K1"),
    ],
)
def test_key_width_limits(
    lengths: tuple[int | None, ...], clustered: bool, create: bool, warning: str | None
) -> None:
    plan = sql.key_plan(sql.target_columns(_target(schema=_key_schema(*lengths))))
    assert plan.clustered is clustered
    assert plan.create is create
    if warning:
        assert plan.warning
        assert warning in plan.warning
        ddl = sql.create_table("[dbo].[T]", sql.target_columns(_target()), plan)
        assert ddl.startswith("-- Sin clave primaria")
        assert "PRIMARY KEY" not in ddl


def test_key_bytes() -> None:
    assert sql.key_bytes("NVARCHAR(10)") == 20
    assert sql.key_bytes("DECIMAL(17,2)") == 9
    assert sql.key_bytes("DECIMAL(38,0)") == 17
    assert sql.key_bytes("DATE") == 3
    assert sql.key_bytes("NVARCHAR(MAX)") is None


def test_add_column_and_staging() -> None:
    assert sql.add_column("[dbo].[T]", "NEW", "INT") == "ALTER TABLE [dbo].[T] ADD [NEW] INT NULL"
    staging = sql.staging_name("d-" + "a" * 24, "GL_ITEMS")
    assert staging == "__sx_stg_d_" + "a" * 24 + "_GL_ITEMS"
    assert len(sql.staging_name("d-" + "a" * 24, "X" * 128)) == 128
    create = sql.create_staging("[dbo].[S]", GL_SCHEMA)
    assert create.startswith("DROP TABLE IF EXISTS [dbo].[S];")
    assert "[_sx_extracted_at] DATETIME2(6) NULL" in create
    insert = sql.insert_staging("[dbo].[S]", GL_SCHEMA)
    assert insert.count("?") == len(GL_SCHEMA.columns) + 5


# ------------------------------------------------------------------ carga
def test_merge_soft_delete_keeps_data_and_counts_deletes() -> None:
    target = _target()
    cols = sql.target_columns(target)
    merge = sql.merge_statement(target.target, "[dbo].[S]", cols, DeleteHandling.SOFT)
    assert "PARTITION BY [RBUKRS], [BELNR] ORDER BY [_sx_record_seq] DESC" in merge
    assert "WITH (HOLDLOCK)" in merge
    assert merge.count("WHEN MATCHED") == 1  # SQL Server no admite dos UPDATE
    assert "t.[DMBTR] = CASE WHEN s.[_sx_change_mode] = 'D' THEN t.[DMBTR] ELSE s.[DMBTR] END" in (
        merge
    )
    assert "t.[_sx_batch_id] = s.[_sx_batch_id]" in merge
    assert "THEN 'DELETE' ELSE $action END INTO @sx_actions" in merge
    assert "WHEN NOT MATCHED BY TARGET AND ISNULL(s.[_sx_change_mode], '') <> 'D'" in merge


def test_merge_hard_and_ignore() -> None:
    target = _target(delete=DeleteHandling.HARD)
    cols = sql.target_columns(target)
    hard = sql.merge_statement(target.target, "[dbo].[S]", cols, DeleteHandling.HARD)
    assert "WHEN MATCHED AND s.[_sx_change_mode] = 'D' THEN DELETE" in hard
    ignore = sql.merge_statement(target.target, "[dbo].[S]", cols, DeleteHandling.IGNORE)
    assert "THEN DELETE" not in ignore
    assert "FROM [dbo].[S] WHERE ISNULL([_sx_change_mode], '') <> 'D'" in ignore
    no_keys = _target(schema=EntitySchema(columns=(Column(name="A", edm_type="Edm.String"),)))
    with pytest.raises(ValueError, match="clave"):
        sql.merge_statement(
            "[dbo].[T]", "[dbo].[S]", sql.target_columns(no_keys), DeleteHandling.SOFT
        )


def test_append_and_replace_bind_values() -> None:
    target = _target(LoadMode.APPEND, delete=DeleteHandling.IGNORE)
    cols = sql.target_columns(target)
    delete, insert = sql.append_statements(target.target, "[dbo].[S]", cols, DeleteHandling.IGNORE)
    assert delete == "DELETE FROM [dbo].[GL_ITEMS] WHERE [_sx_batch_id] = ?"
    assert insert.endswith("FROM [dbo].[S] s WHERE ISNULL(s.[_sx_change_mode], '') <> 'D'")
    truncate, overwrite = sql.replace_statements(
        target.target, "[dbo].[S]", cols, DeleteHandling.SOFT
    )
    assert truncate == "TRUNCATE TABLE [dbo].[GL_ITEMS]"
    assert overwrite.startswith("INSERT INTO [dbo].[GL_ITEMS] WITH (TABLOCK)")
    assert "sp_rename" not in overwrite


def test_without_technical_columns_only_batch_id_remains() -> None:
    cols = sql.target_columns(_target(technical=False, delete=DeleteHandling.IGNORE))
    assert [n for n, _ in cols.technical] == ["_sx_batch_id"]


def test_control_statements_use_parameters() -> None:
    assert sql.insert_control("dbo").count("?") == 11
    assert "WHERE [batch_id] = ? AND [target_table] = ?" in sql.select_control("dbo")
    assert sql.delete_control_row("dbo").count("?") == 3


def test_rows_are_converted_for_the_driver() -> None:
    schema = EntitySchema(
        columns=(
            Column(name="ID", edm_type="Edm.Guid"),
            Column(name="AT", edm_type="Edm.DateTimeOffset"),
        )
    )
    guid = uuid.uuid4()
    at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    data = {
        "ID": [str(guid), None],
        "AT": [at, None],
        "_sx_batch_id": ["d-x", "d-x"],
        "_sx_run_id": ["r", "r"],
        "_sx_extracted_at": [at, at],
        "_sx_change_mode": [None, "D"],
        "_sx_record_seq": [1, 2],
    }
    batch = pa.RecordBatch.from_pydict(data, schema=schema.arrow_schema())
    rows = batch_rows(batch, converters(schema))
    assert rows[0][0] == guid
    assert rows[0][1] == datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001 - UTC sin zona
    assert rows[1][:2] == (None, None)


# ------------------------------------------------------------------ errores
class _FakeOdbcError(Exception):
    pass


@pytest.mark.parametrize(
    ("args", "kind", "code"),
    [
        (("IM002", "[Microsoft][ODBC Driver Manager] Data source name not found"), "c", "DRIVER"),
        (("28000", "[Microsoft][SQL Server]Login failed for user 'x'. (18456)"), "c", "AUTH"),
        (("42000", "[SQL Server]CREATE TABLE permission denied (262)"), "c", "PRIVILEGES"),
        (("42S02", "[SQL Server]Invalid object name 'x'. (208)"), "c", "SQL"),
        (("22003", "[SQL Server]Arithmetic overflow (8115)"), "d", "DATA"),
        (("23000", "[SQL Server]Violation of PRIMARY KEY (2627)"), "d", "DATA"),
        (("40001", "[SQL Server]deadlock victim (1205)"), "t", "SQLSERVER"),
        (("08S01", "[Microsoft]Communication link failure"), "t", "SQLSERVER"),
        (("HYT00", "[Microsoft]Query timeout expired"), "t", "SQLSERVER"),
    ],
)
def test_errors_are_classified(args: tuple[str, str], kind: str, code: str) -> None:
    kinds = {"c": ErrorKind.CONFIGURATION, "d": ErrorKind.DATA, "t": ErrorKind.TRANSIENT}
    err = map_error(_FakeOdbcError(*args), "la carga")
    assert err.kind is kinds[kind]
    assert err.code.endswith(code)
    assert "[Microsoft]" not in err.message
