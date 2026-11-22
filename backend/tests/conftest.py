from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sapextract.core.config import Environment, Settings
from sapextract.core.security.crypto import generate_master_key
from sapextract.main_api import create_app
from tests.support.mssql import SqlServerInstance, start_sqlserver

# Puerto cerrado a propósito: las pruebas unitarias nunca alcanzan una BD real.
UNREACHABLE_DB = "postgresql+psycopg://test:test@127.0.0.1:1/sapextract?connect_timeout=1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=UNREACHABLE_DB,
        master_key=generate_master_key(),
        log_json=False,
        log_level="WARNING",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session")
def sqlserver() -> Iterator[SqlServerInstance]:
    """SQL Server en contenedor, uno por sesión (contrato e integración del destino)."""
    with start_sqlserver() as instance:
        yield instance
