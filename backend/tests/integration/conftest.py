"""Entorno compartido de integración: PostgreSQL real, API y un administrador listo para usar."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sapextract.audit.application.maintenance import ensure_partitions
from sapextract.core.config import Environment, Settings
from sapextract.core.db import Database
from sapextract.core.migrations import upgrade
from sapextract.core.security.crypto import generate_master_key
from sapextract.identity.application.bootstrap import create_local_admin, sync_catalog
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.main_api import create_app
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from tests.support.orchestration import FakeClock, OrchEnv
from tests.support.sap_gateway import start_gateway

ADMIN_USERNAME = "root"
ADMIN_PASSWORD = "Integracion.Admin-2026"
DEFAULT_NEW_PASSWORD = "Clave.Nueva-Segura-77"
API = "/api/v1"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def app_settings(pg_url: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=pg_url,
        master_key=generate_master_key(),
        log_json=False,
        log_level="WARNING",
        auth_rate_limit_per_minute=10_000,
    )


@pytest.fixture(scope="session")
def platform_db(app_settings: Settings) -> Iterator[Database]:
    """BD migrada con el catálogo sincronizado y el administrador de emergencia creado."""
    upgrade(app_settings.database_url)
    db = Database(app_settings)
    toolkit = IdentityToolkit.build(app_settings)
    with db.session() as session:
        ensure_partitions(session)
        sync_catalog(session)
        create_local_admin(
            session,
            toolkit,
            username=ADMIN_USERNAME,
            display_name="Administrador",
            password=ADMIN_PASSWORD,
        )
        session.commit()
    yield db
    db.dispose()


@pytest.fixture(scope="session")
def api_app(app_settings: Settings, platform_db: Database) -> FastAPI:
    return create_app(app_settings)


class ApiClient:
    """Cliente de pruebas que envía la cabecera CSRF como hace el SPA."""

    def __init__(self, app: FastAPI) -> None:
        self.http = TestClient(app, raise_server_exceptions=False)

    def _headers(self, method: str, extra: dict[str, str] | None) -> dict[str, str]:
        headers = dict(extra or {})
        csrf = self.http.cookies.get("sx_csrf")
        if method not in ("GET", "HEAD") and csrf and "X-CSRF-Token" not in headers:
            headers["X-CSRF-Token"] = csrf
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        response: httpx.Response = self.http.request(
            method,
            API + path,
            json=json,
            headers=self._headers(method, headers),
            params=params,
        )
        return response

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self.request("POST", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self.request("PUT", path, json=json, **kw)

    def patch(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self.request("PATCH", path, json=json, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.request("DELETE", path, **kw)

    def login(self, username: str, password: str) -> str:
        response = self.post("/auth/login", {"username": username, "password": password})
        assert response.status_code == 200, response.text
        return str(response.json()["stage"])


@pytest.fixture(scope="session")
def admin_password(api_app: FastAPI) -> str:
    """Completa una vez el primer acceso del administrador (cambio de contraseña obligatorio)."""
    client = ApiClient(api_app)
    assert client.login(ADMIN_USERNAME, ADMIN_PASSWORD) == "password_change"
    response = client.post("/auth/password/change", {"new_password": DEFAULT_NEW_PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "active"
    return DEFAULT_NEW_PASSWORD


@pytest.fixture
def admin(api_app: FastAPI, admin_password: str) -> ApiClient:
    client = ApiClient(api_app)
    assert client.login(ADMIN_USERNAME, admin_password) == "active"
    return client


@pytest.fixture
def client_factory(api_app: FastAPI) -> Callable[[], ApiClient]:
    def factory() -> ApiClient:
        return ApiClient(api_app)

    return factory


def role_id(admin: ApiClient, code: str) -> str:
    roles = admin.get("/roles").json()
    return str(next(r["id"] for r in roles if r["code"] == code))


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def create_user(admin: ApiClient, *role_codes: str, username: str | None = None) -> dict[str, Any]:
    response = admin.post(
        "/users",
        {
            "username": username or unique("u"),
            "display_name": "Usuario de prueba",
            "email": "prueba@example.com",
            "role_ids": [role_id(admin, code) for code in role_codes],
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def activate_user(client: ApiClient, username: str, temporary: str) -> str:
    """Primer acceso de un usuario: login con la temporal y cambio obligatorio."""
    assert client.login(username, temporary) == "password_change"
    password = "Otra.Clave-Valida-2026"
    response = client.post("/auth/password/change", {"new_password": password})
    assert response.status_code == 200, response.text
    return password


def audit_actions(admin: ApiClient, **params: Any) -> list[dict[str, Any]]:
    response = admin.get("/audit-events", params={"page_size": 200, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


# Entorno de las pruebas de orquestación: pipelines, cola, scheduler y workers.
@pytest.fixture(scope="session")
def orch(
    app_settings: Settings, platform_db: Database, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[OrchEnv]:
    """API con reloj falso para la orquestación, Gateway simulado y una carpeta CSV real."""
    csv_root = tmp_path_factory.mktemp("orch-csv")
    settings = app_settings.model_copy(
        update={"csv_roots": [csv_root], "worker_heartbeat_seconds": 0.05}
    )
    app = create_app(settings)
    clock = FakeClock()
    toolkit = OrchestrationToolkit.build(
        settings,
        audit=app.state.identity.audit,
        sap=app.state.sap,
        destinations=app.state.destinations,
        clock=clock,
        random_fn=lambda: 0.0,
    )
    app.state.orchestration = toolkit
    gateway = start_gateway()
    yield OrchEnv(
        app=app, db=platform_db, toolkit=toolkit, clock=clock, gateway=gateway, csv_root=csv_root
    )
    gateway.stop()
    app.state.db.dispose()


@pytest.fixture
def orch_admin(orch: OrchEnv, admin_password: str) -> ApiClient:
    client = ApiClient(orch.app)
    assert client.login(ADMIN_USERNAME, admin_password) == "active"
    return client
