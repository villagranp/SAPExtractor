"""Destino SQL Server (plan 07 §3, It4b): ODBC Driver 18 + ``pyodbc`` y carga transaccional.

Carga de un lote: tabla de staging ``__sx_stg_<lote>_<destino>`` cargada con
``fast_executemany`` desde los lotes Arrow del spool → en una transacción: ``MERGE`` /
``DELETE``+``INSERT`` / ``TRUNCATE``+``INSERT`` y la fila de ``__sx_load_state``. El DDL de la
tabla final va en ``prepare``, fuera de esa transacción.
"""

from __future__ import annotations

import contextlib
import re
import socket
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC
from typing import Any, ClassVar, Literal

import pyarrow as pa
import structlog
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.core.network import HostNotAllowedError
from sapextract.dataflow.domain.batch import BatchInfo, LoadMode
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    AppliedBatch,
    LoadOptions,
    LoadResult,
    PreparedTarget,
    SchemaEvolution,
    StageInfo,
    require_test_target,
)
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.dataflow.domain.spool import Spool
from sapextract.destinations.application.plugins import PluginContext, wrong_model
from sapextract.destinations.domain.model import (
    SQL_IDENTIFIER,
    DestinationType,
    check_load_options,
    resolve_columns,
)
from sapextract.destinations.domain.testing import (
    CheckReport,
    CheckStatus,
    CheckStep,
    skipped_after_failure,
)
from sapextract.destinations.infrastructure import sqlserver_sql as sql

STEPS = ("network", "authentication", "context", "privileges")
HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*$"
)
INSERT_CHUNK = 10_000  # filas por llamada a executemany
_ODBC_PREFIX = re.compile(r"^(\[[^\]]*\])+\s*")
# Errores nativos de SQL Server: permisos (229, 230, 262, 297, 300, 1088, 15247) y login (18456).
_PERMISSION_ERRORS = frozenset({229, 230, 262, 297, 300, 1088, 15247})
_NATIVE = re.compile(r"\((\d{3,6})\)")

log = structlog.get_logger(__name__)


ENTRA_SP = "entra_service_principal"


class SqlServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", title="SQL Server")

    host: str = Field(title="Servidor", description="Nombre DNS o IP, sin puerto", max_length=253)
    port: int = Field(1433, ge=1, le=65535, title="Puerto")
    database: str = Field(title="Base de datos", max_length=128)
    schema_name: str = Field("dbo", title="Esquema", max_length=128)
    authentication: Literal["sql", "entra_service_principal"] = Field(
        "sql",
        title="Autenticación",
        description="Usuario SQL o service principal de Entra ID",
    )
    user: str = Field(
        title="Usuario",
        description="Inicio de sesión SQL o ID de aplicación (client ID) del service principal",
        max_length=128,
    )
    trust_server_certificate: bool = Field(
        default=False,
        title="Confiar en el certificado del servidor",
        description="Solo para servidores de prueba con certificado autofirmado",
    )
    login_timeout_seconds: int = Field(30, ge=5, le=300, title="Timeout de conexión (s)")
    query_timeout_seconds: int = Field(
        7200,
        ge=60,
        le=86400,
        title="Timeout por sentencia (s)",
        description="Tiempo máximo de cada sentencia; una carga grande puede tardar horas",
    )

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip()
        if not HOST_PATTERN.match(value):
            msg = "Escriba solo el nombre o la IP del servidor, sin tcp:, puerto ni instancia."
            raise ValueError(msg)
        return value.lower()

    @field_validator("database", "schema_name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        value = value.strip()
        if not SQL_IDENTIFIER.match(value):
            msg = "Use letras, números y guion bajo, empezando por letra o guion bajo."
            raise ValueError(msg)
        return value

    @field_validator("user")
    @classmethod
    def _user(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch in value for ch in ";{}\r\n\t\0"):
            msg = "El usuario no puede estar vacío ni contener ; { } o saltos de línea."
            raise ValueError(msg)
        return value


class SqlServerSecret(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Credencial")

    password: SecretStr = Field(
        title="Contraseña o secreto de cliente", min_length=1, max_length=1024
    )


def _odbc_value(value: str) -> str:
    """Valor de la cadena de conexión ODBC entre llaves (``}`` se duplica)."""
    return "{" + value.replace("}", "}}") + "}"


def connection_string(config: SqlServerConfig, secret: SqlServerSecret, driver: str) -> str:
    """Cadena ODBC. Contiene el secreto: nunca se registra ni se devuelve."""
    parts = {
        "DRIVER": _odbc_value(driver),
        "SERVER": f"tcp:{config.host},{config.port}",
        "DATABASE": _odbc_value(config.database),
        "Encrypt": "yes",
        "TrustServerCertificate": "yes" if config.trust_server_certificate else "no",
        "APP": "SAPExtract",
        "UID": _odbc_value(config.user),
        "PWD": _odbc_value(secret.password.get_secret_value()),
    }
    if config.authentication == ENTRA_SP:
        parts["Authentication"] = "ActiveDirectoryServicePrincipal"
    return ";".join(f"{k}={v}" for k, v in parts.items())


def _clean_message(exc: Exception) -> tuple[str, str]:
    args: tuple[Any, ...] = tuple(getattr(exc, "args", ()))
    sqlstate = str(args[0]) if len(args) > 1 else ""
    raw = str(args[1]) if len(args) > 1 else str(exc)
    first = raw.split("\n")[0]
    message = _ODBC_PREFIX.sub("", first).strip()
    return sqlstate, message[:500]


def map_error(exc: Exception, what: str) -> DataflowError:  # noqa: PLR0911 - tabla de errores
    """Clase del error de SQL Server → reintento o no (plan 06 §8)."""
    sqlstate, detail = _clean_message(exc)
    message = f"SQL Server rechazó {what}: {detail}"
    natives = {int(n) for n in _NATIVE.findall(str(getattr(exc, "args", ("", ""))[-1]))}
    if sqlstate == "IM002":
        message = f"No está instalado el driver ODBC para SQL Server ({detail})."
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-ODBC-DRIVER")
    if sqlstate == "28000" or sqlstate.startswith("FA") or 18456 in natives:
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-AUTH")
    if natives & _PERMISSION_ERRORS or "permission" in detail.lower():
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-PRIVILEGES")
    if sqlstate == "40001" or sqlstate.startswith(("08", "HYT")):
        return DataflowError(message, kind=ErrorKind.TRANSIENT, code="SX-DST-SQLSERVER")
    if sqlstate.startswith(("22", "23")):
        return DataflowError(message, kind=ErrorKind.DATA, code="SX-DST-DATA")
    if sqlstate.startswith("42"):
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-SQL")
    return DataflowError(message, kind=ErrorKind.TRANSIENT, code="SX-DST-SQLSERVER")


def _connect(config: SqlServerConfig, secret: SqlServerSecret, driver: str) -> Any:
    import pyodbc  # noqa: PLC0415 - carga perezosa: necesita el driver ODBC del sistema

    conn = pyodbc.connect(
        connection_string(config, secret, driver),
        autocommit=False,
        timeout=config.login_timeout_seconds,
    )
    conn.timeout = config.query_timeout_seconds
    return conn


# ------------------------------------------------------------------ conversión Arrow → pyodbc
type Converter = Callable[[list[Any]], list[Any]]


def _naive_utc(values: list[Any]) -> list[Any]:
    return [v.astimezone(UTC).replace(tzinfo=None) if v is not None else None for v in values]


def _guid(values: list[Any]) -> list[Any]:
    return [uuid.UUID(str(v)) if v not in (None, "") else None for v in values]


def converters(schema: EntitySchema) -> list[tuple[str, Converter | None]]:
    """(columna del spool, conversión) en el orden de ``staging_columns``; ``None`` = tal cual."""
    guids = {c.name for c in schema.columns if c.edm_type == "Edm.Guid"}
    result: list[tuple[str, Converter | None]] = []
    for field in schema.arrow_schema():
        dtype = field.type
        if field.name in guids:
            result.append((field.name, _guid))
        elif pa.types.is_timestamp(dtype) and dtype.tz is not None:
            result.append((field.name, _naive_utc))
        else:
            result.append((field.name, None))
    return result


def batch_rows(
    batch: pa.RecordBatch, convs: Sequence[tuple[str, Converter | None]]
) -> list[tuple[Any, ...]]:
    columns = []
    for name, conv in convs:
        values = batch.column(name).to_pylist()
        columns.append(conv(values) if conv else values)
    return list(zip(*columns, strict=True))


class SqlServerWriter:
    supported_modes = frozenset(LoadMode)

    def __init__(self, connection: Any, config: SqlServerConfig) -> None:
        self.conn = connection
        self.config = config

    def _execute(self, statement: str, params: Sequence[Any] | None = None) -> Any:
        cursor = self.conn.cursor()
        if params is None:
            cursor.execute(statement)
        else:
            cursor.execute(statement, *params)
        return cursor

    def _target(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        check_load_options(options, self.supported_modes)
        names = resolve_columns(schema, options.column_mapping)
        return PreparedTarget(
            target=sql.qualified(self.config.schema_name, options.target_name),
            schema=schema,
            options=options,
            column_names=names,
            details={"table": options.target_name},
        )

    # -------------------------------------------------------------- contrato
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        target = self._target(schema, options)
        cols = sql.target_columns(target)
        sch, table = self.config.schema_name, options.target_name
        try:
            self._execute(sql.create_control_table(sch))
            rows = self._execute(sql.EXISTING_COLUMNS, (sch, table)).fetchall()
            existing = {str(r[0]).upper() for r in rows}
            if not existing:
                if not options.create_if_missing:
                    msg = f"La tabla {target.target} no existe y no se puede crear automáticamente."
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-TABLE-MISSING"
                    )
                for statement in ddl_statements(target):
                    self._execute(statement)
                self.conn.commit()
                return target
            wanted = [(n, t) for n, t, _ in cols.data] + list(cols.technical)
            missing = [(n, t) for n, t in wanted if n.upper() not in existing]
            if missing and options.schema_evolution is SchemaEvolution.FAIL:
                msg = "El destino no tiene las columnas " + ", ".join(n for n, _ in missing)
                raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-SCHEMA")
            if missing and options.schema_evolution is SchemaEvolution.ADD_COLUMNS:
                for name, type_ in missing:
                    self._execute(sql.add_column(target.target, name, type_))
            if missing and options.schema_evolution is SchemaEvolution.IGNORE:
                kept = {s: d for s, d in target.column_names.items() if d.upper() in existing}
                target = PreparedTarget(
                    target=target.target,
                    schema=schema,
                    options=options,
                    column_names=kept,
                    details=target.details,
                )
            self.conn.commit()
        except DataflowError:
            self._rollback()
            raise
        except Exception as exc:  # errores del driver
            self._rollback()
            raise map_error(exc, "la preparación de la tabla") from exc
        return target

    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None:
        try:
            row = self._execute(
                sql.select_control(self.config.schema_name),
                (batch_id, target.details["table"]),
            ).fetchone()
            self.conn.commit()
        except Exception as exc:
            self._rollback()
            raise map_error(exc, "la consulta de la tabla de control") from exc
        if row is None:
            return None
        pipeline_id, bid, run_id, next_token, window_end, applied_at = row
        return AppliedBatch(
            pipeline_id=uuid.UUID(pipeline_id),
            batch_id=bid,
            run_id=uuid.UUID(run_id),
            next_token=next_token,
            window_end=window_end.replace(tzinfo=UTC) if window_end else None,
            applied_at=applied_at.replace(tzinfo=UTC),
        )

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        schema = spool.manifest.schema
        staging = sql.qualified(
            self.config.schema_name,
            sql.staging_name(spool.manifest.batch_id, target.details["table"]),
        )
        insert = sql.insert_staging(staging, schema)
        convs = converters(schema)
        rows = 0
        started = time.perf_counter()
        try:
            self._execute(sql.create_staging(staging, schema))
            self.conn.commit()
            cursor = self.conn.cursor()
            cursor.fast_executemany = True
            for batch in spool.batches():
                for offset in range(0, batch.num_rows, INSERT_CHUNK):
                    chunk = batch.slice(offset, INSERT_CHUNK)
                    cursor.executemany(insert, batch_rows(chunk, convs))
                    rows += chunk.num_rows
                self.conn.commit()  # el staging no es la tabla final: se confirma por lote Arrow
        except Exception as exc:
            self._rollback()
            raise map_error(exc, "la subida del lote") from exc
        elapsed = time.perf_counter() - started
        return StageInfo(
            target=target,
            spool_rows=spool.manifest.rows,
            details={
                "staging": staging,
                "staged_rows": rows,
                "rows_per_second": int(rows / elapsed) if elapsed > 0 else rows,
            },
        )

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        target = stage.target
        options = target.options
        cols = sql.target_columns(target)
        staging = stage.details["staging"]
        table = target.details["table"]
        schema = self.config.schema_name
        inserted = updated = deleted = 0
        try:
            if options.load_mode is LoadMode.APPEND:
                delete_stmt, insert_stmt = sql.append_statements(
                    target.target, staging, cols, options.delete_handling
                )
                self._execute(delete_stmt, (batch.batch_id,))
                inserted = max(self._execute(insert_stmt).rowcount, 0)
            elif options.load_mode is LoadMode.REPLACE:
                truncate, insert_stmt = sql.replace_statements(
                    target.target, staging, cols, options.delete_handling
                )
                self._execute(truncate)
                inserted = max(self._execute(insert_stmt).rowcount, 0)
            else:
                cursor = self._execute(
                    sql.merge_statement(target.target, staging, cols, options.delete_handling)
                )
                counts = cursor.fetchone()
                inserted, updated, deleted = (int(v or 0) for v in counts)
            key = (table, batch.batch_id, str(batch.pipeline_id))
            self._execute(sql.delete_control_row(schema), key)
            self._execute(
                sql.insert_control(schema),
                (
                    str(batch.pipeline_id),
                    batch.batch_id,
                    str(batch.run_id),
                    table,
                    options.load_mode.value,
                    batch.next_token,
                    batch.window_end.astimezone(UTC).replace(tzinfo=None)
                    if batch.window_end
                    else None,
                    inserted,
                    updated,
                    deleted,
                    batch.schema_hash,
                ),
            )
            self.conn.commit()
        except Exception as exc:
            self._rollback()
            if isinstance(exc, DataflowError):
                raise
            raise map_error(exc, "la carga del lote") from exc
        return LoadResult(rows_inserted=inserted, rows_updated=updated, rows_deleted=deleted)

    def cleanup(self, stage: StageInfo) -> None:
        try:
            self._execute(f"DROP TABLE IF EXISTS {stage.details['staging']}")
            self.conn.commit()
        except Exception:
            self._rollback()
            log.warning("sqlserver_cleanup_failed")

    def drop_test_target(self, target: PreparedTarget) -> None:
        require_test_target(target)
        try:
            self._execute(f"DROP TABLE IF EXISTS {target.target}")
            self._execute(sql.delete_control(self.config.schema_name), (target.details["table"],))
            self.conn.commit()
        except Exception as exc:
            self._rollback()
            raise map_error(exc, "el borrado de la tabla de prueba") from exc

    def _rollback(self) -> None:
        with contextlib.suppress(Exception):  # el error original es el que importa
            self.conn.rollback()


def ddl_statements(target: PreparedTarget) -> list[str]:
    """Tabla final con su clave (solo ``upsert``) y el índice por lote."""
    cols = sql.target_columns(target)
    key = sql.key_plan(cols) if target.options.load_mode is LoadMode.UPSERT else None
    return [sql.create_table(target.target, cols, key), sql.create_batch_index(target.target)]


# ------------------------------------------------------------------ prueba de conexión
def _step(name: str, t0: float, status: CheckStatus, message: str, **kw: Any) -> CheckStep:
    elapsed = int((time.perf_counter() - t0) * 1000)
    return CheckStep(name=name, status=status, message=message, duration_ms=elapsed, **kw)


def _network_step(config: SqlServerConfig, ctx: PluginContext) -> CheckStep:
    t0 = time.perf_counter()
    try:
        addresses = ctx.host_policy.resolve(config.host, config.port)
        socket.create_connection((addresses[0], config.port), timeout=10).close()
    except HostNotAllowedError as exc:
        return _step("network", t0, CheckStatus.FAILED, exc.detail, error_code=exc.error_code)
    except OSError as exc:
        code = "SX-DST-DNS" if isinstance(exc, socket.gaierror) else "SX-DST-NETWORK"
        message = f"No se pudo conectar con {config.host}:{config.port} ({exc})."
        return _step("network", t0, CheckStatus.FAILED, message, error_code=code)
    details = {"host": config.host, "port": config.port, "addresses": addresses}
    message = f"{config.host}:{config.port} accesible."
    return _step("network", t0, CheckStatus.OK, message, details=details)


_CONTEXT_QUERY = (
    "SELECT CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128)), DB_NAME(), SUSER_SNAME(), "
    "SCHEMA_ID(?)"
)


def _auth_label(config: SqlServerConfig) -> str:
    if config.authentication == ENTRA_SP:
        return f"Autenticado con el service principal {config.user}."
    return f"Autenticado como {config.user} con usuario SQL."


def _session_steps(
    config: SqlServerConfig, secret: SqlServerSecret, ctx: PluginContext
) -> list[CheckStep]:
    t0 = time.perf_counter()
    try:
        conn = _connect(config, secret, ctx.odbc_driver)
    except Exception as exc:  # errores del driver
        err = map_error(exc, "la conexión")
        return [_step("authentication", t0, CheckStatus.FAILED, err.message, error_code=err.code)]
    try:
        steps = [_step("authentication", t0, CheckStatus.OK, _auth_label(config))]
        t0 = time.perf_counter()
        version, database, login, schema_id = (
            conn.cursor().execute(_CONTEXT_QUERY, config.schema_name).fetchone()
        )
        context = {"version": version, "database": database, "login": login}
        if schema_id is None:
            message = f"El esquema {config.schema_name} no existe en {database} o no es visible."
            steps.append(
                _step(
                    "context",
                    t0,
                    CheckStatus.FAILED,
                    message,
                    error_code="SX-DST-SCHEMA-MISSING",
                    details=context,
                )
            )
            return steps
        message = f"Base {database}, esquema {config.schema_name} (SQL Server {version})."
        steps.append(_step("context", t0, CheckStatus.OK, message, details=context))
        steps.append(_privileges_step(conn, config.schema_name))
        return steps
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        conn.close()


def _privileges_step(conn: Any, schema: str) -> CheckStep:
    """Crea, escribe y altera una tabla de prueba en una transacción que se deshace."""
    t0 = time.perf_counter()
    probe = sql.qualified(schema, f"__sx_test_{uuid.uuid4().hex[:8]}")
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE {probe} ([id] INT NOT NULL)")
        cur.execute(f"INSERT INTO {probe} ([id]) VALUES (1)")  # noqa: S608  # nosec B608
        cur.execute(f"ALTER TABLE {probe} ADD [extra] INT NULL")
        conn.rollback()  # el DDL de SQL Server es transaccional: no queda nada
    except Exception as exc:  # errores del driver
        with contextlib.suppress(Exception):
            conn.rollback()
        err = map_error(exc, "la creación de objetos")
        return _step("privileges", t0, CheckStatus.FAILED, err.message, error_code=err.code)
    message = f"Puede crear, escribir y modificar tablas en el esquema {schema}."
    return _step("privileges", t0, CheckStatus.OK, message)


class SqlServerPlugin:
    kind: ClassVar[DestinationType] = DestinationType.SQLSERVER
    label: ClassVar[str] = "SQL Server"
    config_model: ClassVar[type[BaseModel]] = SqlServerConfig
    secret_model: ClassVar[type[BaseModel] | None] = SqlServerSecret
    supported_modes: ClassVar[frozenset[LoadMode]] = SqlServerWriter.supported_modes

    def validate(self, config: BaseModel, ctx: PluginContext) -> None:
        if not isinstance(config, SqlServerConfig):
            raise wrong_model(config, SqlServerConfig)
        try:
            ctx.host_policy.check_host(config.host)
        except HostNotAllowedError as exc:
            raise BusinessRuleError(
                exc.detail,
                code=exc.error_code,
                errors=[FieldIssue("config.host", exc.detail, exc.error_code)],
            ) from exc

    def credential_info(self, secret: BaseModel | None) -> dict[str, Any]:
        return {}  # usuario y tipo de autenticación ya están en la configuración

    def test(self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext) -> CheckReport:
        """Red → autenticación → contexto (base y esquema) → privilegios (crear y alterar)."""
        if not isinstance(config, SqlServerConfig):
            raise wrong_model(config, SqlServerConfig)
        if not isinstance(secret, SqlServerSecret):
            raise wrong_model(secret, SqlServerSecret)
        started = utcnow()
        steps = [_network_step(config, ctx)]
        if steps[-1].status is not CheckStatus.FAILED:
            steps += _session_steps(config, secret, ctx)
        return CheckReport(
            started_at=started,
            finished_at=utcnow(),
            steps=tuple(skipped_after_failure(steps, STEPS)),
        )

    @contextmanager
    def open_writer(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> Iterator[SqlServerWriter]:
        if not isinstance(config, SqlServerConfig):
            raise wrong_model(config, SqlServerConfig)
        if not isinstance(secret, SqlServerSecret):
            raise wrong_model(secret, SqlServerSecret)
        self.validate(config, ctx)
        ctx.host_policy.resolve(config.host, config.port)
        try:
            conn = _connect(config, secret, ctx.odbc_driver)
        except Exception as exc:
            raise map_error(exc, "la conexión") from exc
        try:
            yield SqlServerWriter(conn, config)
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()
            conn.close()

    def ddl(self, config: BaseModel, schema: EntitySchema, options: LoadOptions) -> list[str]:
        if not isinstance(config, SqlServerConfig):
            raise wrong_model(config, SqlServerConfig)
        check_load_options(options, self.supported_modes)
        target = PreparedTarget(
            target=sql.qualified(config.schema_name, options.target_name),
            schema=schema,
            options=options,
            column_names=resolve_columns(schema, options.column_mapping),
        )
        return [*ddl_statements(target), sql.create_control_table(config.schema_name)]

    def config_json_schema(self, ctx: PluginContext) -> dict[str, Any]:
        return SqlServerConfig.model_json_schema()


__all__ = [
    "SqlServerConfig",
    "SqlServerPlugin",
    "SqlServerSecret",
    "SqlServerWriter",
    "connection_string",
    "map_error",
]
