"""Generación de T-SQL para SQL Server (plan 07 §3), pura y probada sin conexión.

Reglas de seguridad: todo identificador se valida contra un patrón y se cita con corchetes;
los valores (lote, run, token) van siempre como parámetros ``?``, nunca interpolados.

* La tabla final y la de control viven en ``[esquema]``; la base es la de la conexión.
* La tabla de staging conserva los nombres exactos del spool (nombres SAP, incluido ``/``).
* SQL Server distingue entre mayúsculas y minúsculas según la intercalación de la base: los
  nombres se escriben tal cual y las comparaciones con el catálogo se hacen sin distinguirlas.
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
    Column,
    EntitySchema,
)
from sapextract.destinations.domain.model import SQL_IDENTIFIER

CONTROL_TABLE = "__sx_load_state"
MAX_NVARCHAR = 4000  # por encima, NVARCHAR(MAX)
CLUSTERED_KEY_BYTES = 900
NONCLUSTERED_KEY_BYTES = 1700
_SOURCE_NAME = re.compile(r"^[A-Za-z0-9_/]{1,128}$")  # nombres SAP (incluye /BIC/…)

TECHNICAL_TYPES: dict[str, str] = {
    BATCH_ID: "VARCHAR(32)",
    RUN_ID: "VARCHAR(36)",
    EXTRACTED_AT: "DATETIME2(6)",
    CHANGE_MODE: "VARCHAR(1)",
    RECORD_SEQ: "BIGINT",
    DELETED: "BIT",
}


def ident(name: str) -> str:
    """Identificador de destino validado y citado: ``[nombre]``."""
    if not SQL_IDENTIFIER.match(name):
        msg = f"Identificador no válido: {name!r}"
        raise ValueError(msg)
    return f"[{name}]"


def source_ident(name: str) -> str:
    """Columna de staging con el nombre exacto del spool (nombres SAP), citada."""
    if not _SOURCE_NAME.match(name) and not name.startswith("_sx_"):
        msg = f"Nombre de columna de origen no válido: {name!r}"
        raise ValueError(msg)
    return "[" + name.replace("]", "]]") + "]"


def qualified(schema: str, table: str) -> str:
    return f"{ident(schema)}.{ident(table)}"


def _literal(value: str) -> str:
    """Literal N'…' para ``OBJECT_ID``; solo recibe nombres ya validados por ``ident``."""
    return "N'" + value.replace("'", "''") + "'"


def sqlserver_type(column: Column) -> str:  # noqa: PLR0911 - tabla de tipos (plan 07 §6)
    dtype = column.arrow_type
    if column.edm_type == "Edm.Guid":
        return "UNIQUEIDENTIFIER"
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        n = column.max_length
        return f"NVARCHAR({n})" if n and 0 < n <= MAX_NVARCHAR else "NVARCHAR(MAX)"
    if pa.types.is_boolean(dtype):
        return "BIT"
    if pa.types.is_int8(dtype) or pa.types.is_int16(dtype):
        return "SMALLINT"
    if pa.types.is_int32(dtype):
        return "INT"
    if pa.types.is_int64(dtype):
        return "BIGINT"
    if pa.types.is_decimal(dtype):
        return f"DECIMAL({min(dtype.precision, 38)},{dtype.scale})"
    if pa.types.is_floating(dtype):
        return "FLOAT"
    if pa.types.is_date(dtype):
        return "DATE"
    if pa.types.is_timestamp(dtype):
        return "DATETIME2(6)"  # sin zona (ABAP) o en UTC (DateTimeOffset): plan 07 §6
    if pa.types.is_time(dtype):
        return "TIME(6)"
    if pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype):
        return "VARBINARY(MAX)"
    return "NVARCHAR(MAX)"


_FIXED_BYTES = {
    "BIT": 1,
    "SMALLINT": 2,
    "INT": 4,
    "BIGINT": 8,
    "FLOAT": 8,
    "DATE": 3,
    "DATETIME2(6)": 8,
    "TIME(6)": 5,
    "UNIQUEIDENTIFIER": 16,
}
_NVARCHAR = re.compile(r"^NVARCHAR\((\d+)\)$")
_DECIMAL = re.compile(r"^DECIMAL\((\d+),\d+\)$")


def key_bytes(type_: str) -> int | None:
    """Bytes que ocupa una columna en una clave de índice; ``None`` si no puede ser clave."""
    if type_ in _FIXED_BYTES:
        return _FIXED_BYTES[type_]
    if m := _NVARCHAR.match(type_):
        return 2 * int(m.group(1))
    if m := _DECIMAL.match(type_):
        p = int(m.group(1))
        return 5 if p <= 9 else 9 if p <= 19 else 13 if p <= 28 else 17
    return None  # (MAX): no admite índice


@dataclass(frozen=True)
class KeyPlan:
    """Clave primaria que se crea para ``upsert`` (o por qué no se crea)."""

    columns: tuple[str, ...]  # nombres en destino
    clustered: bool
    bytes: int | None
    warning: str | None = None

    @property
    def create(self) -> bool:
        return self.warning is None and bool(self.columns)


@dataclass(frozen=True)
class TargetColumns:
    """Columnas de la tabla final: (nombre en destino, tipo, columna de staging)."""

    data: tuple[tuple[str, str, str], ...]
    technical: tuple[tuple[str, str], ...]  # destino, tipo
    keys: tuple[tuple[str, str], ...]  # destino, columna de staging

    def type_of(self, name: str) -> str:
        found = [t for n, t, _ in self.data if n == name]
        return found[0]


def target_columns(target: PreparedTarget) -> TargetColumns:
    schema = target.schema
    data = tuple(
        (dest, sqlserver_type(schema.column(src)), src) for src, dest in target.column_names.items()
    )
    technical: list[tuple[str, str]] = [(BATCH_ID, TECHNICAL_TYPES[BATCH_ID])]
    if target.options.technical_columns:
        technical += [(n, TECHNICAL_TYPES[n]) for n in (RUN_ID, EXTRACTED_AT, CHANGE_MODE)]
        technical.append((RECORD_SEQ, TECHNICAL_TYPES[RECORD_SEQ]))
    if target.options.technical_columns or target.options.delete_handling is DeleteHandling.SOFT:
        technical.append((DELETED, TECHNICAL_TYPES[DELETED]))
    keys = tuple((target.column_names[k], k) for k in schema.keys)
    return TargetColumns(data=data, technical=tuple(technical), keys=keys)


def key_plan(cols: TargetColumns) -> KeyPlan:
    """Clave primaria agrupada si cabe en 900 bytes; no agrupada hasta 1700; si no, ninguna."""
    names = tuple(dest for dest, _ in cols.keys)
    if not names:
        return KeyPlan(columns=(), clustered=False, bytes=None)
    sizes = [key_bytes(cols.type_of(n)) for n in names]
    if any(s is None for s in sizes):
        unsized = [n for n, s in zip(names, sizes, strict=True) if s is None]
        warning = (
            "Sin clave primaria: las columnas clave "
            + ", ".join(unsized)
            + " no tienen longitud máxima (NVARCHAR(MAX)). El MERGE recorrerá la tabla entera; "
            "fije la longitud en el mapeo de tipos."
        )
        return KeyPlan(columns=names, clustered=False, bytes=None, warning=warning)
    total = sum(s for s in sizes if s is not None)
    if total <= CLUSTERED_KEY_BYTES:
        return KeyPlan(columns=names, clustered=True, bytes=total)
    if total <= NONCLUSTERED_KEY_BYTES:
        return KeyPlan(columns=names, clustered=False, bytes=total)
    warning = (
        f"Sin clave primaria: la clave ocupa {total} bytes y SQL Server admite "
        f"{NONCLUSTERED_KEY_BYTES} en un índice no agrupado. El MERGE recorrerá la tabla entera."
    )
    return KeyPlan(columns=names, clustered=False, bytes=total, warning=warning)


# ------------------------------------------------------------------ DDL
def create_table(table: str, cols: TargetColumns, key: KeyPlan | None = None) -> str:
    key_names = set(key.columns) if key and key.create else set()
    lines = [f"{ident(n)} {t}{' NOT NULL' if n in key_names else ''}" for n, t, _ in cols.data] + [
        f"{ident(n)} {t}" for n, t in cols.technical
    ]
    if key and key.create:
        kind = "CLUSTERED" if key.clustered else "NONCLUSTERED"
        lines.append(f"PRIMARY KEY {kind} ({', '.join(ident(n) for n in key.columns)})")
    body = ",\n  ".join(lines)
    comment = f"-- {key.warning}\n" if key and key.warning else ""
    return f"{comment}CREATE TABLE {table} (\n  {body}\n)"


def create_batch_index(table: str) -> str:
    return f"CREATE INDEX [IX__sx_batch_id] ON {table} ({ident(BATCH_ID)})"


def add_column(table: str, name: str, type_: str) -> str:
    return f"ALTER TABLE {table} ADD {ident(name)} {type_} NULL"


def create_control_table(schema: str) -> str:
    table = qualified(schema, CONTROL_TABLE)
    return (
        f"IF OBJECT_ID({_literal(table)}, N'U') IS NULL\n"
        f"CREATE TABLE {table} (\n"
        "  [pipeline_id] VARCHAR(36) NOT NULL,\n"
        "  [batch_id] VARCHAR(32) NOT NULL,\n"
        "  [run_id] VARCHAR(36) NOT NULL,\n"
        "  [target_table] NVARCHAR(128) NOT NULL,\n"
        "  [load_mode] VARCHAR(10) NOT NULL,\n"
        "  [next_token] NVARCHAR(MAX) NULL,\n"
        "  [window_end] DATETIME2(6) NULL,\n"
        "  [rows_inserted] BIGINT NULL,\n"
        "  [rows_updated] BIGINT NULL,\n"
        "  [rows_deleted] BIGINT NULL,\n"
        "  [schema_hash] VARCHAR(64) NULL,\n"
        "  [applied_at] DATETIME2(6) NOT NULL,\n"
        "  PRIMARY KEY ([target_table], [batch_id], [pipeline_id])\n"
        ")"
    )


def staging_name(batch_id: str, target_table: str) -> str:
    """Tabla de staging de un lote: ``__sx_stg_<lote>_<destino>`` (máx. 128)."""
    return f"__sx_stg_{batch_id.replace('-', '_')}_{target_table}"[:128]


def staging_columns(schema: EntitySchema) -> list[tuple[str, str]]:
    """(nombre en el spool, tipo) de la tabla de staging, en el orden del spool."""
    cols = [(c.name, sqlserver_type(c)) for c in schema.columns]
    cols += [(f.name, TECHNICAL_TYPES[f.name]) for f in TECHNICAL_FIELDS]
    return cols


def create_staging(table: str, schema: EntitySchema) -> str:
    body = ",\n  ".join(f"{source_ident(n)} {t} NULL" for n, t in staging_columns(schema))
    return f"DROP TABLE IF EXISTS {table};\nCREATE TABLE {table} (\n  {body}\n)"


def insert_staging(table: str, schema: EntitySchema) -> str:
    cols = staging_columns(schema)
    names = ", ".join(source_ident(n) for n, _ in cols)
    marks = ", ".join("?" for _ in cols)
    return f"INSERT INTO {table} ({names}) VALUES ({marks})"  # nosec B608


# ------------------------------------------------------------------ carga
def _is_delete(alias: str = "s") -> str:
    return f"{alias}.{source_ident(CHANGE_MODE)} = 'D'"


def _select_list(cols: TargetColumns, alias: str = "s") -> tuple[list[str], list[str]]:
    """(columnas destino, expresiones) para INSERT … SELECT desde staging."""
    names = [ident(n) for n, _, _ in cols.data]
    exprs = [f"{alias}.{source_ident(src)}" for _, _, src in cols.data]
    for name, _ in cols.technical:
        names.append(ident(name))
        if name == DELETED:
            exprs.append(f"CAST(CASE WHEN {_is_delete(alias)} THEN 1 ELSE 0 END AS BIT)")
        else:
            exprs.append(f"{alias}.{source_ident(name)}")
    return names, exprs


def _skip_deletes(delete: DeleteHandling, alias: str = "s") -> str:
    if delete is DeleteHandling.IGNORE:
        return f" WHERE ISNULL({alias}.{source_ident(CHANGE_MODE)}, '') <> 'D'"
    return ""


def append_statements(
    table: str, staging: str, cols: TargetColumns, delete: DeleteHandling
) -> list[str]:
    """Borra lo que hubiera del mismo lote (repetición) e inserta el lote."""
    names, exprs = _select_list(cols)
    return [
        f"DELETE FROM {table} WHERE {ident(BATCH_ID)} = ?",  # nosec B608
        f"INSERT INTO {table} ({', '.join(names)}) "  # nosec B608
        f"SELECT {', '.join(exprs)} FROM {staging} s{_skip_deletes(delete)}",
    ]


def replace_statements(
    table: str, staging: str, cols: TargetColumns, delete: DeleteHandling
) -> list[str]:
    """``TRUNCATE`` + ``INSERT … SELECT`` en la transacción de carga, sobre la tabla estable.

    ``TRUNCATE`` toma un bloqueo Sch-M hasta el ``COMMIT``: los consumidores esperan y ven la
    tabla anterior o la nueva, nunca vacía. La tabla no cambia, así que conserva permisos,
    índices y vistas dependientes (no se usa ``sp_rename``).
    """
    names, exprs = _select_list(cols)
    return [
        f"TRUNCATE TABLE {table}",
        f"INSERT INTO {table} WITH (TABLOCK) ({', '.join(names)}) "  # nosec B608
        f"SELECT {', '.join(exprs)} FROM {staging} s{_skip_deletes(delete)}",
    ]


def merge_statement(table: str, staging: str, cols: TargetColumns, delete: DeleteHandling) -> str:
    """``MERGE`` con deduplicación previa por clave; devuelve un conteo por acción.

    SQL Server solo admite dos ``WHEN MATCHED`` si uno actualiza y otro borra: el borrado
    lógico (``soft``) es un único ``UPDATE`` que, para los registros D, conserva los datos y
    solo marca ``_sx_deleted`` y el lote.
    """
    if not cols.keys:
        msg = "upsert necesita columnas clave"
        raise ValueError(msg)
    partition = ", ".join(source_ident(src) for _, src in cols.keys)
    where = (
        f" WHERE ISNULL({source_ident(CHANGE_MODE)}, '') <> 'D'"
        if delete is DeleteHandling.IGNORE
        else ""
    )
    source = (
        "(SELECT * FROM (SELECT *, ROW_NUMBER() OVER ("  # nosec B608
        f"PARTITION BY {partition} ORDER BY {source_ident(RECORD_SEQ)} DESC) AS [__sx_rn] "
        f"FROM {staging}{where}) d WHERE d.[__sx_rn] = 1)"
    )
    on = " AND ".join(f"t.{ident(dest)} = s.{source_ident(src)}" for dest, src in cols.keys)
    names, exprs = _select_list(cols)
    if delete is DeleteHandling.SOFT:
        updates = []
        for name, expr in zip(names, exprs, strict=True):
            if name in {ident(DELETED), ident(BATCH_ID)}:
                updates.append(f"t.{name} = {expr}")
            else:
                updates.append(f"t.{name} = CASE WHEN {_is_delete()} THEN t.{name} ELSE {expr} END")
    else:
        updates = [f"t.{n} = {e}" for n, e in zip(names, exprs, strict=True)]
    clauses: list[str] = []
    if delete is DeleteHandling.HARD:
        clauses.append(f"WHEN MATCHED AND {_is_delete()} THEN DELETE")
    clauses.append(f"WHEN MATCHED THEN UPDATE SET {', '.join(updates)}")  # nosec B608
    clauses.append(
        f"WHEN NOT MATCHED BY TARGET AND ISNULL(s.{source_ident(CHANGE_MODE)}, '') <> 'D' "
        f"THEN INSERT ({', '.join(names)}) VALUES ({', '.join(exprs)})"
    )
    action = f"CASE WHEN $action = 'UPDATE' AND {_is_delete()} THEN 'DELETE' ELSE $action END"
    return (
        "SET NOCOUNT ON;\n"
        "DECLARE @sx_actions TABLE ([action] NVARCHAR(10) NOT NULL);\n"
        f"MERGE {table} WITH (HOLDLOCK) AS t USING {source} AS s ON {on}\n"
        + "\n".join(clauses)
        + f"\nOUTPUT {action} INTO @sx_actions;\n"
        "SELECT ISNULL(SUM(CASE WHEN [action] = 'INSERT' THEN 1 ELSE 0 END), 0) AS inserted, "
        "ISNULL(SUM(CASE WHEN [action] = 'UPDATE' THEN 1 ELSE 0 END), 0) AS updated, "
        "ISNULL(SUM(CASE WHEN [action] = 'DELETE' THEN 1 ELSE 0 END), 0) AS deleted "
        "FROM @sx_actions;"
    )


# ------------------------------------------------------------------ tabla de control
_CONTROL_KEY = "[target_table] = ? AND [batch_id] = ? AND [pipeline_id] = ?"


def delete_control_row(schema: str) -> str:
    """Repetir un lote reescribe su fila de control (la clave primaria la haría fallar)."""
    return f"DELETE FROM {qualified(schema, CONTROL_TABLE)} WHERE {_CONTROL_KEY}"  # nosec B608


def insert_control(schema: str) -> str:
    return (
        f"INSERT INTO {qualified(schema, CONTROL_TABLE)} "  # nosec B608
        "([pipeline_id], [batch_id], [run_id], [target_table], [load_mode], [next_token], "
        "[window_end], [rows_inserted], [rows_updated], [rows_deleted], [schema_hash], "
        "[applied_at]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())"
    )


def select_control(schema: str) -> str:
    return (
        "SELECT TOP (1) [pipeline_id], [batch_id], [run_id], [next_token], [window_end], "  # nosec B608
        f"[applied_at] FROM {qualified(schema, CONTROL_TABLE)} "
        "WHERE [batch_id] = ? AND [target_table] = ? ORDER BY [applied_at] DESC"
    )


def delete_control(schema: str) -> str:
    """Filas de control de una tabla (solo se usa con las tablas de una ejecución de prueba)."""
    return f"DELETE FROM {qualified(schema, CONTROL_TABLE)} WHERE [target_table] = ?"  # nosec B608


EXISTING_COLUMNS = (
    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?"
)
