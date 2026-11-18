from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from sapextract.core.errors import NotFoundError


class _Body(BaseModel):
    name: str
    size: int


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/api/v1/_test/missing")
    def missing() -> None:
        raise NotFoundError("El pipeline X no existe")

    @app.post("/api/v1/_test/validate")
    def validate(body: _Body) -> _Body:
        return body

    @app.get("/api/v1/_test/boom")
    def boom() -> None:
        msg = "detalle interno que no debe salir"
        raise RuntimeError(msg)


def test_live_and_system_info(client: TestClient) -> None:
    assert client.get("/api/v1/health/live").json()["status"] == "ok"
    info = client.get("/api/v1/system/info").json()
    assert info["environment"] == "test"
    assert info["name"] == "SAPExtract"


def test_ready_reports_database_down(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["database"]["status"] == "fail"


def test_request_id_and_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "abc12345-test"})
    assert response.headers["X-Request-ID"] == "abc12345-test"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "<script>"})
    assert response.headers["X-Request-ID"] != "<script>"
    assert len(response.headers["X-Request-ID"]) == 32


def test_domain_error_is_problem_details(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    response = client.get("/api/v1/_test/missing")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "SX-GEN-NOT-FOUND"
    assert body["detail"] == "El pipeline X no existe"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_validation_error_lists_fields(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    response = client.post("/api/v1/_test/validate", json={"name": "a", "size": "grande"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "SX-GEN-VALIDATION"
    assert [e["field"] for e in body["errors"]] == ["size"]


def test_unexpected_error_hides_internals(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    response = client.get("/api/v1/_test/boom")
    assert response.status_code == 500
    assert "detalle interno" not in response.text
    assert response.json()["code"] == "SX-GEN-INTERNAL"


def test_unknown_route_is_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/no-existe")
    assert response.status_code == 404
    assert response.json()["code"] == "SX-HTTP-404"


def test_oversized_body_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/health/live", content=b"x", headers={"content-length": str(50 * 1024 * 1024)}
    )
    assert response.status_code == 413
