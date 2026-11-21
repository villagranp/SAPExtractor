"""Destino Snowflake (plan 07 §5): autenticación por par de claves y carga transaccional.

Carga de un lote: ``PUT`` de los Parquet del spool tal cual a un *stage* temporal →
``COPY INTO`` una tabla temporal de staging → en una transacción: ``MERGE`` / ``INSERT`` /
``INSERT OVERWRITE`` y la fila de ``__SX_LOAD_STATE``. El DDL va siempre fuera de esa
transacción (en Snowflake una sentencia DDL confirma la transacción en curso).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import socket
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC
from typing import Any, ClassVar

import structlog
from cryptography.hazmat.primitives import serialization
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
from sapextract.destinations.infrastructure import snowflake_sql as sql

ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,254}(\.[A-Za-z0-9_-]{1,63}){0,3}$")
STEPS = ("network", "authentication", "context", "privileges")
# Errores de Snowflake que significan credenciales o clave incorrectas.
_AUTH_ERRNOS = frozenset({250001, 251005, 390100, 390144, 394304})

log = structlog.get_logger(__name__)


class SnowflakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Snowflake")

    account: str = Field(
        title="Cuenta", description="Identificador <organización>-<cuenta>", max_length=255
    )
    user: str = Field(title="Usuario de servicio", max_length=255)
    role: str = Field(title="Rol", max_length=255)
    warehouse: str = Field(title="Warehouse", max_length=255)
    database: str = Field(title="Base de datos", max_length=255)
    schema_name: str = Field(title="Esquema", max_length=255)
    login_timeout_seconds: int = Field(30, ge=5, le=300, title="Timeout de conexión (s)")

    @field_validator("account")
    @classmethod
    def _account(cls, value: str) -> str:
        value = value.strip()
        if not ACCOUNT_PATTERN.match(value):
            msg = (
                "Use el identificador de cuenta <organización>-<cuenta> (sin https:// ni dominio)."
            )
            raise ValueError(msg)
        return value

    @field_validator("user", "role", "warehouse", "database", "schema_name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        value = value.strip()
        if not SQL_IDENTIFIER.match(value):  # ValueError: Pydantic lo convierte en error de campo
            msg = "Use letras, números y guion bajo, empezando por letra o guion bajo."
            raise ValueError(msg)
        return value.upper()

    @property
    def host(self) -> str:
        return f"{self.account.lower().replace('_', '-')}.snowflakecomputing.com"


class SnowflakeSecret(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Par de claves")

    private_key_pem: SecretStr = Field(
        title="Clave privada (PEM)", description="-----BEGIN PRIVATE KEY-----"
    )
    private_key_passphrase: SecretStr | None = Field(None, title="Contraseña de la clave")


def _load_key(secret: SnowflakeSecret) -> Any:
    password = (
        secret.private_key_passphrase.get_secret_value().encode()
        if secret.private_key_passphrase
        else None
    )
    try:
        return serialization.load_pem_private_key(
            secret.private_key_pem.get_secret_value().encode(), password=password
        )
    except (ValueError, TypeError) as exc:
        msg = "La clave privada no es válida o su contraseña no es correcta."
        raise BusinessRuleError(
            msg,
            code="SX-DST-KEY-INVALID",
            errors=[FieldIssue("secret.private_key_pem", msg, "SX-DST-KEY-INVALID")],
        ) from exc


def public_key_fingerprint(secret: SnowflakeSecret) -> str:
    """Huella como la muestra Snowflake en ``DESC USER`` (``RSA_PUBLIC_KEY_FP``)."""
    der = (
        _load_key(secret)
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()


def map_error(exc: Exception, what: str) -> DataflowError:
    """Clase del error de Snowflake → reintento o no (plan 06 §8)."""
    errno = getattr(exc, "errno", None)
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    detail = str(getattr(exc, "msg", "") or exc)[:500]
    message = f"Snowflake rechazó {what}: {detail}"
    if errno in _AUTH_ERRNOS or "JWT" in detail:
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-AUTH")
    if sqlstate.startswith("42") or "does not exist or not authorized" in detail:
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code="SX-DST-PRIVILEGES")
    if sqlstate.startswith("22"):
        return DataflowError(message, kind=ErrorKind.DATA, code="SX-DST-DATA")
    return DataflowError(message, kind=ErrorKind.TRANSIENT, code="SX-DST-SNOWFLAKE")


def _connect(config: SnowflakeConfig, secret: SnowflakeSecret) -> Any:
    import snowflake.connector  # noqa: PLC0415 - carga perezosa: el conector es pesado

    der = _load_key(secret).private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        account=config.account,
        user=config.user,
        private_key=der,
        role=config.role,
        warehouse=config.warehouse,
        database=config.database,
        schema=config.schema_name,
        login_timeout=config.login_timeout_seconds,
        network_timeout=config.login_timeout_seconds * 4,
        application="SAPExtract",
        session_parameters={"QUERY_TAG": "sapextract", "TIMEZONE": "UTC"},
        autocommit=True,
        client_session_keep_alive=False,
    )


class SnowflakeWriter:
    supported_modes = frozenset(LoadMode)

    def __init__(self, connection: Any, config: SnowflakeConfig) -> None:
        self.conn = connection
        self.config = config

    def _execute(self, statement: str, params: Mapping[str, Any] | None = None) -> Any:
        cursor = self.conn.cursor()
        cursor.execute(statement, dict(params) if params else None)
        return cursor

    # -------------------------------------------------------------- contrato
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget:
        check_load_options(options, self.supported_modes)
        names = {
            src: dest.upper()
            for src, dest in resolve_columns(schema, options.column_mapping).items()
        }
        db, sch = self.config.database, self.config.schema_name
        table = options.target_name.upper()
        target = PreparedTarget(
            target=sql.qualified(db, sch, table),
            schema=schema,
            options=options,
            column_names=names,
            details={"table": table},
        )
        cols = sql.target_columns(target)
        try:
            self._execute(sql.create_control_table(db, sch))
            existing = {
                r[0]
                for r in self._execute(
                    sql.existing_columns(db), {"schema": sch, "table": table}
                ).fetchall()
            }
            if not existing:
                if not options.create_if_missing:
                    msg = f"La tabla {target.target} no existe y no se puede crear automáticamente."
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-TABLE-MISSING"
                    )
                self._execute(sql.create_table(target.target, cols))
                return target
            wanted = [(n.upper(), t) for n, t, _ in cols.data] + [
                (n.upper(), t) for n, t in cols.technical
            ]
            missing = [(n, t) for n, t in wanted if n not in existing]
            if missing and options.schema_evolution is SchemaEvolution.FAIL:
                msg = "El destino no tiene las columnas " + ", ".join(n for n, _ in missing)
                raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-DST-SCHEMA")
            if missing and options.schema_evolution is SchemaEvolution.ADD_COLUMNS:
                for name, type_ in missing:
                    self._execute(sql.add_column(target.target, name, type_))
            if missing and options.schema_evolution is SchemaEvolution.IGNORE:
                kept = {s: d for s, d in names.items() if d.upper() in existing}
                target = PreparedTarget(
                    target=target.target,
                    schema=schema,
                    options=options,
                    column_names=kept,
                    details=target.details,
                )
        except DataflowError:
            raise
        except Exception as exc:  # errores del conector
            raise map_error(exc, "la preparación de la tabla") from exc
        return target

    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None:
        try:
            row = self._execute(
                sql.select_control(self.config.database, self.config.schema_name),
                {"batch_id": batch_id, "target": target.details["table"]},
            ).fetchone()
        except Exception as exc:
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
            applied_at=applied_at.replace(tzinfo=UTC) if applied_at.tzinfo is None else applied_at,
        )

    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo:
        staging, stage = sql.staging_names(spool.manifest.batch_id)
        try:
            self._execute(sql.create_staging(staging, spool.manifest.schema))
            self._execute(sql.create_stage(stage))
            for path in spool.parquet_files():
                self._execute(sql.put_file(str(path.resolve()), stage))
            self._execute(sql.copy_into_staging(staging, stage))
        except Exception as exc:
            raise map_error(exc, "la subida del lote") from exc
        return StageInfo(
            target=target,
            spool_rows=spool.manifest.rows,
            details={"staging": staging, "stage": stage},
        )

    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult:
        target = stage.target
        options = target.options
        cols = sql.target_columns(target)
        staging = stage.details["staging"]
        inserted = updated = deleted = 0
        try:
            self._execute("BEGIN")
            if options.load_mode is LoadMode.APPEND:
                delete_stmt, insert_stmt = sql.append_statements(
                    target.target, staging, cols, options.delete_handling
                )
                self._execute(delete_stmt, {"batch_id": batch.batch_id})
                inserted = self._execute(insert_stmt).rowcount or 0
            elif options.load_mode is LoadMode.REPLACE:
                inserted = (
                    self._execute(
                        sql.replace_statement(target.target, staging, cols, options.delete_handling)
                    ).rowcount
                    or 0
                )
            else:
                cursor = self._execute(
                    sql.merge_statement(target.target, staging, cols, options.delete_handling)
                )
                counts = _merge_counts(cursor)
                inserted, updated, deleted = (
                    counts["inserted"],
                    counts["updated"],
                    counts["deleted"],
                )
            self._execute(
                sql.insert_control(self.config.database, self.config.schema_name),
                {
                    "pipeline_id": str(batch.pipeline_id),
                    "batch_id": batch.batch_id,
                    "run_id": str(batch.run_id),
                    "target": target.details["table"],
                    "mode": options.load_mode.value,
                    "next_token": batch.next_token,
                    "window_end": batch.window_end.astimezone(UTC).replace(tzinfo=None)
                    if batch.window_end
                    else None,
                    "inserted": inserted,
                    "updated": updated,
                    "deleted": deleted,
                    "schema_hash": batch.schema_hash,
                },
            )
            self._execute("COMMIT")
        except Exception as exc:
            with contextlib.suppress(Exception):  # el error original es el que importa
                self._execute("ROLLBACK")
            if isinstance(exc, DataflowError):
                raise
            raise map_error(exc, "la carga del lote") from exc
        return LoadResult(rows_inserted=inserted, rows_updated=updated, rows_deleted=deleted)

    def cleanup(self, stage: StageInfo) -> None:
        for statement in (
            f"DROP TABLE IF EXISTS {sql.ident(stage.details['staging'])}",
            f"DROP STAGE IF EXISTS {sql.ident(stage.details['stage'])}",
        ):
            try:
                self._execute(statement)
            except Exception:
                log.warning("snowflake_cleanup_failed", statement=statement.split(" ", 3)[:3])

    def drop_test_target(self, target: PreparedTarget) -> None:
        require_test_target(target)
        try:
            self._execute(f"DROP TABLE IF EXISTS {target.target}")
            self._execute(
                sql.delete_control(self.config.database, self.config.schema_name),
                {"target": target.details["table"]},
            )
        except Exception as exc:
            raise map_error(exc, "el borrado de la tabla de prueba") from exc


def _merge_counts(cursor: Any) -> dict[str, int]:
    row = cursor.fetchone() or ()
    names = [str(d[0]).lower() for d in cursor.description or []]
    counts = {"inserted": 0, "updated": 0, "deleted": 0}
    for name, value in zip(names, row, strict=False):
        for key in counts:
            if key in name:
                counts[key] = int(value or 0)
    return counts


def _step(name: str, t0: float, status: CheckStatus, message: str, **kw: Any) -> CheckStep:
    elapsed = int((time.perf_counter() - t0) * 1000)
    return CheckStep(name=name, status=status, message=message, duration_ms=elapsed, **kw)


def _network_step(config: SnowflakeConfig, ctx: PluginContext) -> CheckStep:
    t0 = time.perf_counter()
    try:
        addresses = ctx.host_policy.resolve(config.host, 443)
        socket.create_connection((addresses[0], 443), timeout=10).close()
    except HostNotAllowedError as exc:
        return _step("network", t0, CheckStatus.FAILED, exc.detail, error_code=exc.error_code)
    except OSError as exc:
        code = "SX-DST-DNS" if isinstance(exc, socket.gaierror) else "SX-DST-NETWORK"
        message = f"No se pudo conectar con {config.host}:443 ({exc})."
        return _step("network", t0, CheckStatus.FAILED, message, error_code=code)
    details = {"host": config.host, "addresses": addresses}
    return _step("network", t0, CheckStatus.OK, f"{config.host}:443 accesible.", details=details)


_CONTEXT_QUERY = (
    "SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA(), "
    "CURRENT_VERSION()"
)


def _session_steps(config: SnowflakeConfig, secret: SnowflakeSecret) -> list[CheckStep]:
    t0 = time.perf_counter()
    try:
        conn = _connect(config, secret)
    except BusinessRuleError as exc:
        return [
            _step("authentication", t0, CheckStatus.FAILED, exc.detail, error_code=exc.error_code)
        ]
    except Exception as exc:  # errores del conector
        err = map_error(exc, "la conexión")
        return [_step("authentication", t0, CheckStatus.FAILED, err.message, error_code=err.code)]
    try:
        steps = [
            _step(
                "authentication",
                t0,
                CheckStatus.OK,
                f"Autenticado como {config.user} con par de claves.",
                details={"public_key_fingerprint": public_key_fingerprint(secret)},
            )
        ]
        t0 = time.perf_counter()
        role, warehouse, database, schema, version = (
            conn.cursor().execute(_CONTEXT_QUERY).fetchone()
        )
        context = {
            "role": role,
            "warehouse": warehouse,
            "database": database,
            "schema": schema,
            "version": version,
        }
        missing = [k for k in ("role", "warehouse", "database", "schema") if not context[k]]
        if missing:
            message = "El rol no puede usar: " + ", ".join(missing) + ". Revise los GRANT de USAGE."
            steps.append(
                _step(
                    "context",
                    t0,
                    CheckStatus.FAILED,
                    message,
                    error_code="SX-DST-PRIVILEGES",
                    details=context,
                )
            )
            return steps
        message = f"Rol {role}, warehouse {warehouse}, {database}.{schema} (Snowflake {version})."
        steps.append(_step("context", t0, CheckStatus.OK, message, details=context))
        steps.append(_privileges_step(conn))
        return steps
    finally:
        conn.close()


def _privileges_step(conn: Any) -> CheckStep:
    t0 = time.perf_counter()
    probe = sql.ident(f"__SX_TEST_{uuid.uuid4().hex[:8].upper()}")
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE TEMPORARY TABLE {probe} (ID NUMBER)")
        cur.execute(f"CREATE TEMPORARY STAGE {probe}")
        cur.execute(f"DROP STAGE IF EXISTS {probe}")
        cur.execute(f"DROP TABLE IF EXISTS {probe}")
    except Exception as exc:  # errores del conector
        err = map_error(exc, "la creación de objetos")
        return _step("privileges", t0, CheckStatus.FAILED, err.message, error_code=err.code)
    return _step("privileges", t0, CheckStatus.OK, "Puede crear tablas y stages en el esquema.")


class SnowflakePlugin:
    kind: ClassVar[DestinationType] = DestinationType.SNOWFLAKE
    label: ClassVar[str] = "Snowflake"
    config_model: ClassVar[type[BaseModel]] = SnowflakeConfig
    secret_model: ClassVar[type[BaseModel] | None] = SnowflakeSecret
    supported_modes: ClassVar[frozenset[LoadMode]] = SnowflakeWriter.supported_modes

    def validate(self, config: BaseModel, ctx: PluginContext) -> None:
        if not isinstance(config, SnowflakeConfig):
            raise wrong_model(config, SnowflakeConfig)
        try:
            ctx.host_policy.check_host(config.host)
        except HostNotAllowedError as exc:
            raise BusinessRuleError(
                exc.detail,
                code=exc.error_code,
                errors=[FieldIssue("config.account", exc.detail, exc.error_code)],
            ) from exc

    def credential_info(self, secret: BaseModel | None) -> dict[str, Any]:
        if not isinstance(secret, SnowflakeSecret):
            return {}
        return {"public_key_fingerprint": public_key_fingerprint(secret)}

    def test(self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext) -> CheckReport:
        """Red → autenticación (par de claves) → contexto (rol, warehouse, base) → privilegios."""
        if not isinstance(config, SnowflakeConfig):
            raise wrong_model(config, SnowflakeConfig)
        if not isinstance(secret, SnowflakeSecret):
            raise wrong_model(secret, SnowflakeSecret)
        started = utcnow()
        steps = [_network_step(config, ctx)]
        if steps[-1].status is not CheckStatus.FAILED:
            steps += _session_steps(config, secret)
        return CheckReport(
            started_at=started,
            finished_at=utcnow(),
            steps=tuple(skipped_after_failure(steps, STEPS)),
        )

    @contextmanager
    def open_writer(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> Iterator[SnowflakeWriter]:
        if not isinstance(config, SnowflakeConfig):
            raise wrong_model(config, SnowflakeConfig)
        if not isinstance(secret, SnowflakeSecret):
            raise wrong_model(secret, SnowflakeSecret)
        self.validate(config, ctx)
        ctx.host_policy.resolve(config.host, 443)
        try:
            conn = _connect(config, secret)
        except BusinessRuleError:
            raise
        except Exception as exc:
            raise map_error(exc, "la conexión") from exc
        try:
            yield SnowflakeWriter(conn, config)
        finally:
            conn.close()

    def ddl(self, config: BaseModel, schema: EntitySchema, options: LoadOptions) -> list[str]:
        if not isinstance(config, SnowflakeConfig):
            raise wrong_model(config, SnowflakeConfig)
        check_load_options(options, self.supported_modes)
        names = {
            src: dest.upper()
            for src, dest in resolve_columns(schema, options.column_mapping).items()
        }
        target = PreparedTarget(
            target=sql.qualified(config.database, config.schema_name, options.target_name),
            schema=schema,
            options=options,
            column_names=names,
        )
        return [
            sql.create_table(target.target, sql.target_columns(target)),
            sql.create_control_table(config.database, config.schema_name),
        ]

    def config_json_schema(self, ctx: PluginContext) -> dict[str, Any]:
        return SnowflakeConfig.model_json_schema()


__all__ = ["SnowflakeConfig", "SnowflakePlugin", "SnowflakeSecret", "SnowflakeWriter", "map_error"]
