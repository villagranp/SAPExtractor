"""Destino SQL Server de punta a punta: API + PostgreSQL + SQL Server en contenedor (It4b)."""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from sapextract.core.config import Settings
from sapextract.core.db import Database
from sapextract.destinations.application.plugins import PluginRegistry
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.destinations.infrastructure.sqlserver_plugin import SqlServerPlugin
from sapextract.main_api import create_app
from tests.integration.conftest import ADMIN_USERNAME, ApiClient, audit_actions, unique
from tests.support.mssql import SA_PASSWORD, SqlServerInstance

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mssql_app(
    app_settings: Settings, platform_db: Database, sqlserver: SqlServerInstance
) -> FastAPI:
    app = create_app(app_settings)
    toolkit = DestinationsToolkit.build(
        app_settings,
        audit=app.state.identity.audit,
        registry=PluginRegistry([SqlServerPlugin()]),
    )
    # El contenedor escucha en loopback: se permite solo en esta prueba.
    app.state.destinations = dataclasses.replace(
        toolkit, context=sqlserver.context(Path(tempfile.gettempdir()))
    )
    return app


@pytest.fixture
def client(mssql_app: FastAPI, admin_password: str) -> ApiClient:
    api = ApiClient(mssql_app)
    assert api.login(ADMIN_USERNAME, admin_password) == "active"
    return api


def _body(instance: SqlServerInstance, password: str, **overrides: Any) -> dict[str, Any]:
    config = instance.config
    body: dict[str, Any] = {
        "code": unique("MSSQL_").upper(),
        "name": "Almacén SQL Server",
        "type": "sqlserver",
        "environment": "DEV",
        "config": {
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "schema_name": "dbo",
            "user": "sa",
            "trust_server_certificate": True,
        },
        "secret": {"password": password},
    }
    body.update(overrides)
    return body


def _steps(report: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["status"] for s in report["steps"]}


def test_sqlserver_type_publishes_its_form(client: ApiClient) -> None:
    types = {t["type"]: t for t in client.get("/destination-types").json()}
    assert types["sqlserver"]["requires_secret"] is True
    assert set(types["sqlserver"]["supported_modes"]) == {"append", "upsert", "replace"}
    schema = client.get("/destination-types/sqlserver/schema").json()
    assert schema["config_schema"]["properties"]["authentication"]
    assert schema["secret_schema"]["properties"]["password"]["writeOnly"] is True


def test_sqlserver_destination_is_tested_and_activated(
    client: ApiClient, sqlserver: SqlServerInstance
) -> None:
    response = client.post("/destinations", _body(sqlserver, SA_PASSWORD, activate=True))
    assert response.status_code == 201, response.text
    assert SA_PASSWORD not in response.text
    created = response.json()
    test, destination = created["test"], created["destination"]
    assert test["ok"], test
    assert _steps(test) == {
        "network": "ok",
        "authentication": "ok",
        "context": "ok",
        "privileges": "ok",
    }
    assert destination["status"] == "active"
    assert destination["target"].endswith(f":{sqlserver.config.port} · sxtest.dbo")
    # La prueba de privilegios no deja tablas: se deshace en su transacción.
    conn = sqlserver.connect()
    leftovers = conn.cursor().execute(
        "SELECT COUNT(*) FROM sys.tables WHERE name LIKE '[_][_]sx[_]test[_]%'"
    )
    assert leftovers.fetchone()[0] == 0
    conn.close()
    actions = {e["action"] for e in audit_actions(client, resource_id=destination["id"])}
    assert {"destination.created", "destination.tested", "destination.activated"} <= actions


def test_wrong_password_fails_authentication(
    client: ApiClient, sqlserver: SqlServerInstance
) -> None:
    response = client.post("/destinations/test", _body(sqlserver, "Incorrecta.2026"))
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["ok"] is False
    steps = {s["name"]: s for s in report["steps"]}
    assert steps["network"]["status"] == "ok"
    assert steps["authentication"]["error_code"] == "SX-DST-AUTH"
    assert steps["context"]["status"] == "skipped"
    assert "Incorrecta.2026" not in response.text
