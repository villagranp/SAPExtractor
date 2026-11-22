"""Batería de conformidad: memoria (referencia), CSV, SQL Server en contenedor y Snowflake real."""

from __future__ import annotations

import csv
import gzip
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import SecretStr
from snowflake.connector import DictCursor

from sapextract.core.network import HostPolicy
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.destinations.application.plugins import PluginContext
from sapextract.destinations.domain.model import CsvRoots
from sapextract.destinations.infrastructure import snowflake_sql, sqlserver_sql
from sapextract.destinations.infrastructure.csv_plugin import CsvConfig, CsvWriter
from sapextract.destinations.infrastructure.snowflake_plugin import (
    SnowflakeConfig,
    SnowflakePlugin,
    SnowflakeSecret,
    SnowflakeWriter,
)
from sapextract.destinations.infrastructure.sqlserver_plugin import (
    SqlServerPlugin,
    SqlServerWriter,
)
from tests.contract.destination_contract import DestinationContract
from tests.support.dataflow import InMemoryWriter
from tests.support.mssql import SqlServerInstance


def _upper(row: dict[str, Any]) -> dict[str, Any]:
    return {k.upper(): v for k, v in row.items()}


class MemoryHarness:
    supported_modes = frozenset(LoadMode)

    def __init__(self) -> None:
        self.writer = InMemoryWriter()

    def rows(self, target: str) -> list[dict[str, Any]]:
        t = self.writer.target
        return [_upper(r) for r in [*t.rows.values(), *t.appended]]

    @contextmanager
    def failure_inside_apply(self) -> Iterator[None]:
        self.writer.fail_inside_apply = True
        try:
            yield
        finally:
            self.writer.fail_inside_apply = False


class CsvHarness:
    supported_modes = CsvWriter.supported_modes

    def __init__(self, base: Path, *, compression: Literal["none", "gzip"] = "none") -> None:
        config = CsvConfig(root=str(base), compression=compression)
        self.writer = CsvWriter(base, config)

    def rows(self, target: str) -> list[dict[str, Any]]:
        root = Path(target)
        folders = [root / "current"] if (root / "current").exists() else []
        folders += sorted((root / "batches").glob("*")) if (root / "batches").exists() else []
        rows: list[dict[str, Any]] = []
        for folder in folders:
            for part in sorted(folder.glob("part-*")):
                opener = gzip.open if part.suffix == ".gz" else open
                with opener(part, "rt", encoding="utf-8", newline="") as handle:
                    rows += [_upper(r) for r in csv.DictReader(handle)]
        return rows

    @contextmanager
    def failure_inside_apply(self) -> Iterator[None]:
        original = CsvWriter.__dict__["_publish"]  # el staticmethod, no la función

        def broken(tmp: Path, final: Path) -> None:
            msg = "fallo inyectado al publicar"
            raise OSError(msg)

        CsvWriter._publish = staticmethod(broken)  # type: ignore[method-assign]
        try:
            yield
        finally:
            setattr(CsvWriter, "_publish", original)  # noqa: B010


class TestMemoryDestination(DestinationContract):
    @pytest.fixture
    def harness(self) -> MemoryHarness:
        return MemoryHarness()


class TestCsvDestination(DestinationContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> CsvHarness:
        return CsvHarness(tmp_path / "csv")


class TestCsvGzipDestination(DestinationContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> CsvHarness:
        return CsvHarness(tmp_path / "csv", compression="gzip")


# ------------------------------------------------------------------ Snowflake real (opcional)
SNOWFLAKE_ENV = ("ACCOUNT", "USER", "ROLE", "WAREHOUSE", "DATABASE", "SCHEMA", "PRIVATE_KEY_PATH")


def _snowflake_env() -> dict[str, str] | None:
    values = {k: os.environ.get(f"SX_TEST_SNOWFLAKE_{k}", "") for k in SNOWFLAKE_ENV}
    return values if all(values.values()) else None


class SnowflakeHarness:
    supported_modes = SnowflakeWriter.supported_modes

    def __init__(self, writer: SnowflakeWriter) -> None:
        self.writer = writer
        self.created: set[str] = set()

    def rows(self, target: str) -> list[dict[str, Any]]:
        self.created.add(target)
        cursor = self.writer.conn.cursor(DictCursor)
        try:
            cursor.execute(f"SELECT * FROM {target}")  # noqa: S608 - nombre generado por la prueba
            return [_upper(r) for r in cursor.fetchall()]
        finally:
            cursor.close()

    @contextmanager
    def failure_inside_apply(self) -> Iterator[None]:
        original = snowflake_sql.insert_control

        def broken(database: str, schema: str) -> str:
            return "SELECT 1/0"  # error de Snowflake dentro de la transacción de carga

        snowflake_sql.insert_control = broken
        try:
            yield
        finally:
            snowflake_sql.insert_control = original


@pytest.fixture(scope="module")
def snowflake_writer() -> Iterator[SnowflakeWriter]:
    env = _snowflake_env()
    if env is None:
        pytest.skip("Defina SX_TEST_SNOWFLAKE_* para probar contra Snowflake real")
    config = SnowflakeConfig(
        account=env["ACCOUNT"],
        user=env["USER"],
        role=env["ROLE"],
        warehouse=env["WAREHOUSE"],
        database=env["DATABASE"],
        schema_name=env["SCHEMA"],
    )
    secret = SnowflakeSecret(
        private_key_pem=SecretStr(Path(env["PRIVATE_KEY_PATH"]).read_text(encoding="utf-8")),
        private_key_passphrase=(
            SecretStr(os.environ["SX_TEST_SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"])
            if os.environ.get("SX_TEST_SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
            else None
        ),
    )
    ctx = PluginContext(
        host_policy=HostPolicy(allowed=("snowflakecomputing.com",), label="SX_DEST_ALLOWED_HOSTS"),
        csv_roots=CsvRoots(()),
        work_dir=Path(tempfile.gettempdir()),
    )
    with SnowflakePlugin().open_writer(config, secret, ctx) as writer:
        yield writer


@pytest.mark.snowflake
class TestSnowflakeDestination(DestinationContract):
    @pytest.fixture
    def harness(self, snowflake_writer: SnowflakeWriter) -> Iterator[SnowflakeHarness]:
        harness = SnowflakeHarness(snowflake_writer)
        yield harness
        cursor = snowflake_writer.conn.cursor()
        try:
            for target in harness.created:
                cursor.execute(f"DROP TABLE IF EXISTS {target}")
        finally:
            cursor.close()


# ------------------------------------------------------------------ SQL Server en contenedor
class SqlServerHarness:
    supported_modes = SqlServerWriter.supported_modes

    def __init__(self, writer: SqlServerWriter) -> None:
        self.writer = writer

    def rows(self, target: str) -> list[dict[str, Any]]:
        cursor = self.writer.conn.cursor()
        try:
            exists = cursor.execute("SELECT OBJECT_ID(?, 'U')", target).fetchone()[0]
            if exists is None:
                return []
            cursor.execute(f"SELECT * FROM {target}")  # noqa: S608 - nombre generado por la prueba
            names = [d[0].upper() for d in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            cursor.close()
            self.writer.conn.commit()

    @contextmanager
    def failure_inside_apply(self) -> Iterator[None]:
        original = sqlserver_sql.insert_control

        def broken(schema: str) -> str:
            return "SELECT 1/0"  # error de SQL Server dentro de la transacción de carga

        sqlserver_sql.insert_control = broken
        try:
            yield
        finally:
            sqlserver_sql.insert_control = original


@pytest.fixture(scope="module")
def sqlserver_writer(sqlserver: SqlServerInstance) -> Iterator[SqlServerWriter]:
    ctx = sqlserver.context(Path(tempfile.gettempdir()))
    with SqlServerPlugin().open_writer(sqlserver.config, sqlserver.secret, ctx) as writer:
        yield writer


@pytest.mark.integration
class TestSqlServerDestination(DestinationContract):
    @pytest.fixture
    def harness(self, sqlserver_writer: SqlServerWriter) -> SqlServerHarness:
        return SqlServerHarness(sqlserver_writer)
