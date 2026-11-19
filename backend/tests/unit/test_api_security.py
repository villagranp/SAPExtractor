"""Contrato de seguridad de la API, verificable sin base de datos."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sapextract.audit.api.openapi import AUDIT_EXEMPT_EXTENSION, AUDIT_EXTENSION
from sapextract.audit.domain.model import ACTIONS

MUTATING = {"post", "put", "patch", "delete"}


def test_every_mutating_route_declares_its_audit_events(app: FastAPI) -> None:
    missing: list[str] = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            if method not in MUTATING:
                continue
            actions = operation.get(AUDIT_EXTENSION)
            exempt = operation.get(AUDIT_EXEMPT_EXTENSION)
            if actions is None or (not actions and not exempt):
                missing.append(f"{method.upper()} {path}")
            for action in actions or []:
                assert action in ACTIONS, f"{method.upper()} {path}: {action}"
    assert missing == [], f"Rutas sin declaración de auditoría: {missing}"


def test_protected_routes_require_authentication(client: TestClient) -> None:
    for method, path in [
        ("GET", "/api/v1/me"),
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/roles"),
        ("GET", "/api/v1/audit-events"),
        ("POST", "/api/v1/audit-events/verify"),
        ("GET", "/api/v1/api-tokens"),
    ]:
        response = client.request(method, path, json={})
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["code"] == "SX-AUTH-UNAUTHENTICATED"


def test_login_is_rate_limited(app: FastAPI, client: TestClient) -> None:
    app.state.auth_limiter.limit = 2
    body = {"username": "x", "password": "y"}
    statuses = [client.post("/api/v1/auth/login", json=body).status_code for _ in range(3)]
    # Los dos primeros llegan al servicio (que falla por no haber BD); el tercero se frena antes.
    assert statuses[2] == 429
    response = client.post("/api/v1/auth/login", json=body)
    assert response.json()["code"] == "SX-GEN-RATE-LIMIT"
    assert "Retry-After" in response.headers


def test_logout_without_session_clears_cookies(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert "sx_session" in response.headers.get("set-cookie", "")
