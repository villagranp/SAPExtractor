"""SQL Server real en un contenedor para las pruebas del destino (plan 07 §3).

Necesita Docker y un driver ODBC de SQL Server (18 en CI y en la imagen; 17 también sirve en
local). Sin driver, las pruebas se saltan; con ``SX_TEST_REQUIRE_MSSQL=1`` (CI) fallan.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from sapextract.core.network import HostPolicy
from sapextract.destinations.application.plugins import PluginContext
from sapextract.destinations.domain.model import CsvRoots
from sapextract.destinations.infrastructure.sqlserver_plugin import (
    SqlServerConfig,
    SqlServerSecret,
)

IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
SA_PASSWORD = "Pruebas.Sx-2026!"  # contenedor efímero de pruebas
DATABASE = "sxtest"
PREFERRED_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")


@dataclass(frozen=True)
class SqlServerInstance:
    config: SqlServerConfig
    secret: SqlServerSecret
    driver: str

    def context(self, work_dir: Path) -> PluginContext:
        return PluginContext(
            host_policy=HostPolicy(allow_loopback=True, label="SX_DEST_ALLOWED_HOSTS"),
            csv_roots=CsvRoots(()),
            work_dir=work_dir,
            odbc_driver=self.driver,
        )

    def connect(self, database: str | None = None) -> Any:
        import pyodbc  # noqa: PLC0415

        conn = (
            f"DRIVER={{{self.driver}}};SERVER=tcp:{self.config.host},{self.config.port};"
            f"DATABASE={database or self.config.database};UID=sa;PWD={{{SA_PASSWORD}}};"
            "Encrypt=yes;TrustServerCertificate=yes"
        )
        return pyodbc.connect(conn, autocommit=True, timeout=10)


def odbc_driver() -> str | None:
    import pyodbc  # noqa: PLC0415

    wanted = os.environ.get("SX_TEST_MSSQL_ODBC_DRIVER")
    available = set(pyodbc.drivers())
    for name in (wanted, *PREFERRED_DRIVERS):
        if name and name in available:
            return name
    return None


def _skip_or_fail(reason: str) -> None:
    if os.environ.get("SX_TEST_REQUIRE_MSSQL"):
        pytest.fail(reason)
    pytest.skip(reason)


@contextmanager
def start_sqlserver() -> Iterator[SqlServerInstance]:
    driver = odbc_driver()
    if driver is None:
        _skip_or_fail("No hay driver ODBC de SQL Server instalado")
        raise AssertionError  # pragma: no cover - skip/fail lanzan
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: PLC0415

    container = (
        DockerContainer(IMAGE)
        .with_env("ACCEPT_EULA", "Y")
        .with_env("MSSQL_SA_PASSWORD", SA_PASSWORD)
        .with_exposed_ports(1433)
        .waiting_for(
            LogMessageWaitStrategy(
                "SQL Server is now ready for client connections"
            ).with_startup_timeout(180)
        )
    )
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(1433))
        config = SqlServerConfig(
            host="127.0.0.1" if host == "localhost" else host,
            port=port,
            database=DATABASE,
            user="sa",
            trust_server_certificate=True,
        )
        instance = SqlServerInstance(
            config=config, secret=SqlServerSecret(password=SecretStr(SA_PASSWORD)), driver=driver
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                conn = instance.connect("master")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
        conn.cursor().execute(f"CREATE DATABASE [{DATABASE}]")
        conn.close()
        yield instance
