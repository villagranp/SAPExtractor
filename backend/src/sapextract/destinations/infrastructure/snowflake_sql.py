"""Generación de SQL para Snowflake (plan 07 §5), pura y probada sin conexión.

Reglas de seguridad: todo identificador se valida contra un patrón y se cita; los valores
(lote, run, token) van siempre como parámetros ligados, nunca interpolados.

* Identificadores de destino (tabla, columnas mapeadas) en MAYÚSCULAS y citados: citar un
  nombre en minúsculas lo haría sensible a mayúsculas para los consumidores.
* Columnas de staging con el nombre exacto del Parquet (nombres SAP), citadas, para que
  ``COPY … MATCH_BY_COLUMN_NAME = CASE_SENSITIVE`` las case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pyarrow as pa

from sapextract.dataflow.domain.ports import DeleteHandling, PreparedTarget
from sapextract.dataflow.domain.schema import (
    BATCH_ID,
    CHANGE_MODE,
    DELETED,
    EXTRACTED_AT,
    RECORD_SEQ,
    RUN_ID,
    TECHNICAL_FIELDS,
    EntitySchema,
)
from sapextract.destinations.domain.model import SQL_IDENTIFIER

CONTROL_TABLE = "__SX_LOAD_STATE"
_SOURCE_NAME = re.compile(r"^[A-Za-z0-9_/]{1,128}$")  # nombres SAP (incluye /BIC/…)


def ident(name: str) -> str:
    """Identificador de destino: validado, en mayúsculas y citado."""
    if not SQL_IDENTIFIER.match(name):
        msg = f"Identificador no válido: {name!r}"
        raise ValueError(msg)
    return f'"{name.upper()}"'


def source_ident(name: str) -> str:
    """Columna de staging con el nombre exacto del spool (nombres SAP), citada."""
    if not _SOURCE_NAME.match(name) and not name.startswith("_sx_"):
        msg = f"Nombre de columna de origen no válido: {name!r}"
        raise ValueError(msg)
    return '"' + name.replace('"', '""') + '"'


def qualified(database: str, schema: str, table: str) -> str:
    return f"{ident(database)}.{ident(schema)}.{ident(table)}"


def snowflake_type(dtype: pa.DataType) -> str:  # noqa: PLR0911 - tabla de tipos
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "VARCHAR"
    if pa.types.is_boolean(dtype):
        return "BOOLEAN"
    if pa.types.is_int16(dtype) or pa.types.is_int8(dtype):
        return "NUMBER(5,0)"
    if pa.types.is_int32(dtype):
        return "NUMBER(10,0)"
    if pa.types.is_int64(dtype):
        return "NUMBER(19,0)"
    if pa.types.is_decimal(dtype):
        return f"NUMBER({dtype.precision},{dtype.scale})"
    if pa.types.is_floating(dtype):
        return "FLOAT"
    if pa.types.is_date(dtype):
        return "DATE"
    if pa.types.is_timestamp(dtype):
        return "TIMESTAMP_NTZ"  # sin zona (ABAP) o UTC (DateTimeOffset): plan 07 §6
    if pa.types.is_time(dtype):
        return "TIME"
    if pa.types.is_binary(dtype):
        return "BINARY"
    return "VARCHAR"


@dataclass(frozen=True)
class TargetColumns:
    """Columnas de la tabla final: (nombre en destino, tipo, nombre en staging o expresión)."""

    data: tuple[tuple[str, str, str], ...]  # destino, tipo, columna de staging
    technical: tuple[tuple[str, str], ...]  # destino, tipo
    keys: tuple[tuple[str, str], ...]  # destino, columna de staging


def target_columns(target: PreparedTarget) -> TargetColumns:
    schema = target.schema
    data = tuple(
        (dest, snowflake_type(schema.column(src).arrow_type), src)
        for src, dest in target.column_names.items()
    )
    technical: list[tuple[str, str]] = [(BATCH_ID, "VARCHAR(32)")]
    if target.options.technical_columns:
        technical += [
            (RUN_ID, "VARCHAR(36)"),
            (EXTRACTED_AT, "TIMESTAMP_NTZ"),
            (CHANGE_MODE, "VARCHAR(1)"),
            (RECORD_SEQ, "NUMBER(19,0)"),
        ]
    if target.options.technical_columns or target.options.delete_handling is DeleteHandling.SOFT:
        technical.append((DELETED, "BOOLEAN"))
    keys = tuple((target.column_names[k], k) for k in schema.keys)
    return TargetColumns(data=data, technical=tuple(technical), keys=keys)


# ------------------------------------------------------------------ DDL
def create_table(table: str, cols: TargetColumns) -> str:
    lines = [f"{ident(n)} {t}" for n, t, _ in cols.data]
    lines += [f"{ident(n)} {t}" for n, t in cols.technical]
    body = ",\n  ".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n)"


def add_column(table: str, name: str, type_: str) -> str:
    return f"ALTER TABLE {table} ADD COLUMN {ident(name)} {type_}"


def create_control_table(database: str, schema: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {qualified(database, schema, CONTROL_TABLE)} (\n"
        "  PIPELINE_ID VARCHAR(36) NOT NULL,\n"
        "  BATCH_ID VARCHAR(32) NOT NULL,\n"
        "  RUN_ID VARCHAR(36) NOT NULL,\n"
        "  TARGET_TABLE VARCHAR NOT NULL,\n"
        "  LOAD_MODE VARCHAR(10) NOT NULL,\n"
        "  NEXT_TOKEN VARCHAR,\n"
        "  WINDOW_END TIMESTAMP_NTZ,\n"
        "  ROWS_INSERTED NUMBER(19,0),\n"
        "  ROWS_UPDATED NUMBER(19,0),\n"
        "  ROWS_DELETED NUMBER(19,0),\n"
        "  SCHEMA_HASH VARCHAR(64),\n"
        "  APPLIED_AT TIMESTAMP_NTZ NOT NULL,\n"
        "  PRIMARY KEY (PIPELINE_ID, BATCH_ID)\n"
        ")"
    )


def staging_names(batch_id: str) -> tuple[str, str]:
    """Nombres de la tabla y del stage temporales de un lote."""
    suffix = batch_id.replace("-", "_").upper()
    return f"__SX_STG_{suffix}", f"__SX_STAGE_{suffix}"


def create_staging(table: str, schema: EntitySchema) -> str:
    cols = [f"{source_ident(c.name)} {snowflake_type(c.arrow_type)}" for c in schema.columns]
    cols += [f"{source_ident(f.name)} {snowflake_type(f.type)}" for f in TECHNICAL_FIELDS]
    body = ",\n  ".join(cols)
    return f"CREATE OR REPLACE TEMPORARY TABLE {ident(table)} (\n  {body}\n)"


def create_stage(stage: str) -> str:
    return f"CREATE OR REPLACE TEMPORARY STAGE {ident(stage)} FILE_FORMAT = (TYPE = PARQUET)"


def put_file(path: str, stage: str) -> str:
    # PUT no admite parámetros ligados: la ruta la genera la plataforma (spool) y se escapa.
    safe = path.replace("\\", "/").replace("'", "\\'")
    return (
        f"PUT 'file://{safe}' @{ident(stage)} AUTO_COMPRESS = FALSE OVERWRITE = TRUE PARALLEL = 4"
    )


def copy_into_staging(table: str, stage: str) -> str:
    return (
        f"COPY INTO {ident(table)} FROM @{ident(stage)} "
        "FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_SENSITIVE "
        "ON_ERROR = ABORT_STATEMENT PURGE = TRUE"
    )


# ------------------------------------------------------------------ carga
def _select_list(cols: TargetColumns, alias: str = "s") -> tuple[list[str], list[str]]:
    """(columnas destino, expresiones) para INSERT … SELECT desde staging."""
    names = [ident(n) for n, _, _ in cols.data]
    exprs = [f"{alias}.{source_ident(src)}" for _, _, src in cols.data]
    for name, _ in cols.technical:
        names.append(ident(name))
        if name == DELETED:
            exprs.append(f"COALESCE({alias}.{source_ident(CHANGE_MODE)} = 'D', FALSE)")
        else:
            exprs.append(f"{alias}.{source_ident(name)}")
    return names, exprs


def _skip_deletes(options_delete: DeleteHandling, alias: str = "s") -> str:
    if options_delete is DeleteHandling.IGNORE:
        return f" WHERE COALESCE({alias}.{source_ident(CHANGE_MODE)}, '') <> 'D'"
    return ""


def append_statements(
    table: str, staging: str, cols: TargetColumns, delete: DeleteHandling
) -> list[str]:
    names, exprs = _select_list(cols)
    return [
        f"DELETE FROM {table} WHERE {ident(BATCH_ID)} = %(batch_id)s",  # nosec B608
        f"INSERT INTO {table} ({', '.join(names)}) "  # nosec B608
        f"SELECT {', '.join(exprs)} FROM {ident(staging)} s{_skip_deletes(delete)}",
    ]


def replace_statement(table: str, staging: str, cols: TargetColumns, delete: DeleteHandling) -> str:
    names, exprs = _select_list(cols)
    return (
        f"INSERT OVERWRITE INTO {table} ({', '.join(names)}) "  # nosec B608
        f"SELECT {', '.join(exprs)} FROM {ident(staging)} s{_skip_deletes(delete)}"
    )


def merge_statement(table: str, staging: str, cols: TargetColumns, delete: DeleteHandling) -> str:
    if not cols.keys:
        msg = "upsert necesita columnas clave"
        raise ValueError(msg)
    partition = ", ".join(source_ident(src) for _, src in cols.keys)
    # Con IGNORE los registros D no llegan al MERGE; si no, gana el último por clave.
    where = (
        f" WHERE COALESCE({source_ident(CHANGE_MODE)}, '') <> 'D'"
        if delete is DeleteHandling.IGNORE
        else ""
    )
    source = (
        f"(SELECT * FROM {ident(staging)}{where} "  # nosec B608
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition} "
        f"ORDER BY {source_ident(RECORD_SEQ)} DESC) = 1)"
    )
    on = " AND ".join(f"t.{ident(dest)} = s.{source_ident(src)}" for dest, src in cols.keys)
    names, exprs = _select_list(cols)
    is_delete = f"s.{source_ident(CHANGE_MODE)} = 'D'"
    updates = ", ".join(f"t.{n} = {e}" for n, e in zip(names, exprs, strict=True))
    clauses: list[str] = []
    if delete is DeleteHandling.HARD:
        clauses.append(f"WHEN MATCHED AND {is_delete} THEN DELETE")
    elif delete is DeleteHandling.SOFT:
        soft = [f"t.{ident(DELETED)} = TRUE", f"t.{ident(BATCH_ID)} = s.{source_ident(BATCH_ID)}"]
        clauses.append(f"WHEN MATCHED AND {is_delete} THEN UPDATE SET {', '.join(soft)}")  # nosec B608
    clauses.append(f"WHEN MATCHED THEN UPDATE SET {updates}")  # nosec B608
    clauses.append(
        f"WHEN NOT MATCHED AND COALESCE(s.{source_ident(CHANGE_MODE)}, '') <> 'D' "
        f"THEN INSERT ({', '.join(names)}) VALUES ({', '.join(exprs)})"
    )
    return f"MERGE INTO {table} t USING {source} s ON {on} " + " ".join(clauses)


def insert_control(database: str, schema: str) -> str:
    return (
        f"INSERT INTO {qualified(database, schema, CONTROL_TABLE)} "
        "(PIPELINE_ID, BATCH_ID, RUN_ID, TARGET_TABLE, LOAD_MODE, NEXT_TOKEN, WINDOW_END, "
        "ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED, SCHEMA_HASH, APPLIED_AT) "
        "SELECT %(pipeline_id)s, %(batch_id)s, %(run_id)s, %(target)s, %(mode)s, "
        "%(next_token)s, %(window_end)s, %(inserted)s, %(updated)s, %(deleted)s, "
        "%(schema_hash)s, SYSDATE()"
    )


def select_control(database: str, schema: str) -> str:
    return (
        "SELECT PIPELINE_ID, BATCH_ID, RUN_ID, NEXT_TOKEN, WINDOW_END, APPLIED_AT "  # nosec B608
        f"FROM {qualified(database, schema, CONTROL_TABLE)} "
        "WHERE BATCH_ID = %(batch_id)s AND TARGET_TABLE = %(target)s"
    )


def existing_columns(database: str) -> str:
    return (
        f"SELECT COLUMN_NAME FROM {ident(database)}.INFORMATION_SCHEMA.COLUMNS "  # nosec B608
        "WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s"
    )
