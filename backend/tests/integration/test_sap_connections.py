"""Conexiones SAP de punta a punta: API + PostgreSQL + Gateway simulado (``tools/sap-mock``).

Cubre los criterios de aceptación del plan 04: alta, prueba por pasos, catálogo ODP/CDS,
credenciales que nunca vuelven ni se registran y errores de credenciales distintos de red.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from sapextract.core.config import Settings
from sapextract.core.db import Database
from sapextract.core.secret_store import Secret
from sapextract.main_api import create_app
from tests.integration.conftest import (
    ADMIN_USERNAME,
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    unique,
)
from tests.support.certs import CertificatePair, make_certificate
from tests.support.sap_gateway import MOCK_PASSWORD, MOCK_USER, RunningGateway, start_gateway

pytestmark = pytest.mark.integration

ClientFactory = Callable[[], ApiClient]


@pytest.fixture(scope="module")
def gateway() -> Iterator[RunningGateway]:
    running = start_gateway()
    yield running
    running.stop()


@pytest.fixture(scope="module")
def tls_cert(tmp_path_factory: pytest.TempPathFactory) -> CertificatePair:
    return make_certificate(Path(tmp_path_factory.mktemp("gw-tls")))


@pytest.fixture(scope="module")
def tls_gateway(tls_cert: CertificatePair) -> Iterator[RunningGateway]:
    running = start_gateway(tls=tls_cert)
    yield running
    running.stop()


@pytest.fixture(autouse=True)
def _reset_scenario(gateway: RunningGateway) -> Iterator[None]:
    yield
    gateway.scenario(status=None, times=0, delay=0)


def _body(base_url: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": unique("S4D_").upper(),
        "name": "S/4HANA desarrollo",
        "environment": "DEV",
        "system_type": "S4HANA_ONPREM",
        "base_url": base_url,
        "sap_client": "100",
        "language": "ES",
        "timeout_seconds": 5,
        "tags": ["fi", "Pruebas"],
        "credentials": {"auth_type": "basic", "username": MOCK_USER, "password": MOCK_PASSWORD},
    }
    body.update(overrides)
    return body


def _create(admin: ApiClient, base_url: str, **overrides: Any) -> dict[str, Any]:
    response = admin.post("/sap-systems", _body(base_url, **overrides))
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def _steps(report: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["status"] for s in report["steps"]}


def test_register_test_and_activate_system(admin: ApiClient, gateway: RunningGateway) -> None:
    created = _create(admin, gateway.base_url, activate=True)
    system, test = created["system"], created["test"]
    assert test["ok"], test
    assert _steps(test) == {
        "network": "warning",  # HTTP sin cifrar: aviso, no error
        "authentication": "ok",
        "server_info": "ok",
        "latency": "ok",
    }
    assert system["status"] == "active"
    assert system["last_test_ok"] is True
    assert system["credential_info"]["username"] == MOCK_USER
    assert system["tags"] == ["fi", "pruebas"]
    # La contraseña nunca vuelve al navegador.
    assert MOCK_PASSWORD not in json.dumps(created)
    detail = admin.get(f"/sap-systems/{system['id']}")
    assert MOCK_PASSWORD not in detail.text
    assert detail.json()["last_test_result"]["steps"][1]["name"] == "authentication"

    listed = admin.get("/sap-systems", params={"q": system["code"]}).json()
    assert [s["code"] for s in listed["items"]] == [system["code"]]

    actions = {e["action"] for e in audit_actions(admin, resource_id=system["id"])}
    assert {"sap_connection.created", "sap_connection.tested", "sap_connection.activated"} <= (
        actions
    )


def test_secrets_never_reach_audit_or_database_in_clear(
    admin: ApiClient, gateway: RunningGateway, platform_db: Database
) -> None:
    system = _create(admin, gateway.base_url)["system"]
    events = audit_actions(admin, resource_id=system["id"])
    assert events
    assert MOCK_PASSWORD not in json.dumps(events)
    with platform_db.session() as session:
        ciphertexts = list(session.scalars(select(Secret.ciphertext)))
    assert ciphertexts
    assert all(MOCK_PASSWORD not in c for c in ciphertexts)


def test_wrong_password_is_distinct_from_network_errors(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    wrong = admin.post(
        "/sap-systems/test",
        _body(
            gateway.base_url,
            credentials={"auth_type": "basic", "username": MOCK_USER, "password": "Mala-1234"},
        ),
    )
    assert wrong.status_code == 200, wrong.text
    report = wrong.json()
    assert report["error_code"] == "SX-SAP-AUTH"
    assert _steps(report)["network"] == "warning"
    assert _steps(report)["authentication"] == "failed"
    assert "credenciales" in report["summary"]

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed = sock.getsockname()[1]
    unreachable = admin.post("/sap-systems/test", _body(f"http://127.0.0.1:{closed}")).json()
    assert unreachable["error_code"] == "SX-SAP-NETWORK"
    assert _steps(unreachable)["authentication"] == "skipped"


def test_forbidden_and_timeout_are_reported(admin: ApiClient, gateway: RunningGateway) -> None:
    gateway.scenario(status=403, times=1)
    forbidden = admin.post("/sap-systems/test", _body(gateway.base_url)).json()
    assert forbidden["error_code"] == "SX-SAP-FORBIDDEN"

    gateway.scenario(delay=6)
    slow = admin.post("/sap-systems/test", _body(gateway.base_url, timeout_seconds=5)).json()
    assert slow["error_code"] == "SX-SAP-TIMEOUT"
    assert _steps(slow)["authentication"] == "failed"


def test_tls_requires_trusted_certificate(
    admin: ApiClient, tls_gateway: RunningGateway, tls_cert: CertificatePair
) -> None:
    untrusted = admin.post("/sap-systems/test", _body(tls_gateway.base_url)).json()
    assert untrusted["error_code"] == "SX-SAP-TLS"
    network = untrusted["steps"][0]
    assert network["details"]["presented_certificate"]["subject"] == "CN=127.0.0.1"

    trusted = admin.post(
        "/sap-systems/test", _body(tls_gateway.base_url, ca_bundle_pem=tls_cert.cert_pem)
    ).json()
    assert trusted["ok"], trusted
    assert _steps(trusted)["network"] == "ok"
    assert trusted["steps"][0]["details"]["certificate"]["subject"] == "CN=127.0.0.1"

    created = _create(admin, tls_gateway.base_url, ca_bundle_pem=tls_cert.cert_pem)["system"]
    assert created["ca_bundle"][0]["subject"] == "CN=127.0.0.1"
    assert created["last_test_ok"] is True


def test_explorer_lists_odp_and_cds_services(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _create(admin, gateway.base_url)["system"]
    path = f"/sap-systems/{system['id']}/services"
    catalog = admin.get(path)
    assert catalog.status_code == 200, catalog.text
    data = catalog.json()
    assert data["from_cache"] is False
    kinds = {s["technical_name"]: s["kind"] for s in data["items"]}
    assert kinds == {
        "ZFI_WS_CV_GL_ACCOUNT_SRV": "odp",
        "ZFI_WS_GL_ITEMS_SRV": "odp",
        "ZMM_CV_MATERIAL_CDS": "cds",
        "ZSD_WS_SALES_ITEMS_SRV": "odp",
    }
    assert data["counts"]["odp"] == 3
    assert data["counts"]["cds"] == 1

    cds_only = admin.get(path, params={"type": "cds"}).json()
    assert [s["technical_name"] for s in cds_only["items"]] == ["ZMM_CV_MATERIAL_CDS"]
    assert cds_only["from_cache"] is True
    search = admin.get(path, params={"search": "ventas"}).json()
    assert [s["technical_name"] for s in search["items"]] == ["ZSD_WS_SALES_ITEMS_SRV"]
    assert admin.get(path, params={"refresh": True}).json()["from_cache"] is False

    sets = admin.get(f"{path}/ZFI_WS_GL_ITEMS_SRV/entity-sets")
    assert sets.status_code == 200, sets.text
    by_name = {s["name"]: s for s in sets.json()["entity_sets"]}
    main = by_name["EntityOf0FI_GL_14"]
    assert main["odp_role"] == "entity"
    assert main["odp_name"] == "0FI_GL_14"
    assert main["supports_delta"] is True
    assert main["keys"] == ["RBUKRS", "GJAHR", "BELNR", "BUZEI"]
    assert {p["name"] for p in main["properties"]} >= {"BELNR", "DMBTR", "ODQ_CHANGEMODE"}
    assert by_name["DeltaLinksOfEntityOf0FI_GL_14"]["odp_role"] == "delta_links"

    cds = admin.get(f"{path}/ZMM_CV_MATERIAL_CDS/metadata").json()
    assert cds["odata_version"] == "2.0"
    (material,) = cds["entity_sets"]
    assert material["supports_delta"] is False
    assert cds["entity_types"][0]["keys"] == ["Material"]

    missing = admin.get(f"{path}/ZNO_EXISTE_SRV/entity-sets")
    assert missing.status_code == 502
    assert missing.json()["code"] == "SX-SAP-NOT-FOUND"


def test_explorer_reports_sap_errors_without_logging_out(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    system = _create(admin, gateway.base_url)["system"]
    gateway.scenario(status=401, times=5)
    response = admin.get(f"/sap-systems/{system['id']}/services", params={"refresh": True})
    assert response.status_code == 502
    assert response.json()["code"] == "SX-SAP-AUTH"
    gateway.scenario(status=None, times=0)
    assert admin.get("/me").status_code == 200  # la sesión de SAPExtract sigue viva


def test_activation_requires_current_successful_test(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    system = _create(admin, gateway.base_url, run_test=False)["system"]
    assert system["status"] == "draft"
    assert system["last_test_at"] is None
    blocked = admin.post(f"/sap-systems/{system['id']}/activate")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SX-SAP-TEST-REQUIRED"

    tested = admin.post(f"/sap-systems/{system['id']}/test").json()
    assert tested["test"]["ok"]
    # Guardar el resultado de la prueba no cambia la versión (no invalida formularios abiertos).
    assert tested["system"]["version"] == system["version"]
    assert admin.post(f"/sap-systems/{system['id']}/activate").json()["status"] == "active"

    updated = admin.patch(
        f"/sap-systems/{system['id']}",
        {"version": system["version"] + 1, "sap_client": "200", "name": "Renombrado"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["test_outdated"] is True
    stale = admin.patch(f"/sap-systems/{system['id']}", {"version": 1, "name": "Otro"})
    assert stale.status_code == 409

    deactivated = admin.post(f"/sap-systems/{system['id']}/deactivate", {"reason": "Mantenimiento"})
    assert deactivated.json()["status"] == "inactive"
    again = admin.post(f"/sap-systems/{system['id']}/activate")
    assert again.json()["code"] == "SX-SAP-TEST-REQUIRED"

    events = audit_actions(admin, resource_id=system["id"], action="sap_connection.updated")
    assert events[0]["changes"]["sap_client"] == {"before": "100", "after": "200"}


def test_rotate_credentials_retests_automatically(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    system = _create(admin, gateway.base_url, activate=True)["system"]
    rotated = admin.post(
        f"/sap-systems/{system['id']}/credentials/rotate",
        {"credentials": {"auth_type": "basic", "username": "otro", "password": "Mala.Clave-1"}},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["system"]["credential_info"]["username"] == "OTRO"
    assert body["test"]["error_code"] == "SX-SAP-AUTH"
    assert body["system"]["last_test_ok"] is False
    assert "Mala.Clave-1" not in rotated.text

    events = audit_actions(admin, resource_id=system["id"])
    rotation = next(e for e in events if e["action"] == "sap_connection.credentials_rotated")
    assert rotation["changes"]["credentials"] == "***"  # enmascarado por completo
    assert "Mala.Clave-1" not in json.dumps(events)


def test_duplicate_reuses_credentials(admin: ApiClient, gateway: RunningGateway) -> None:
    source = _create(admin, gateway.base_url)["system"]
    code = unique("S4Q_").upper()
    copy = admin.post(
        f"/sap-systems/{source['id']}/duplicate",
        {"code": code, "name": "Copia QAS", "environment": "QAS", "sap_client": "300"},
    )
    assert copy.status_code == 201, copy.text
    duplicated = copy.json()["system"]
    assert (duplicated["code"], duplicated["sap_client"]) == (code, "300")
    assert duplicated["status"] == "draft"
    tested = admin.post(f"/sap-systems/{duplicated['id']}/test").json()
    assert tested["test"]["ok"], tested

    reuse = admin.post(
        "/sap-systems/test",
        {
            "base_url": gateway.base_url,
            "sap_client": "100",
            "timeout_seconds": 5,
            "reuse_credentials_from": source["id"],
        },
    ).json()
    assert reuse["ok"], reuse


def test_validation_and_conflicts(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _create(admin, gateway.base_url)["system"]
    duplicate_code = admin.post("/sap-systems", _body(gateway.base_url, code=system["code"]))
    assert duplicate_code.status_code == 409
    assert duplicate_code.json()["code"] == "SX-SAP-CODE-TAKEN"

    bad_url = admin.post("/sap-systems", _body("https://u:p@gw.local"))
    assert bad_url.status_code == 422
    assert bad_url.json()["errors"][0]["field"] == "base_url"

    prd_http = admin.post("/sap-systems", _body(gateway.base_url, environment="PRD"))
    assert prd_http.json()["code"] == "SX-SAP-PRD-REQUIRES-HTTPS"

    no_password = admin.post(
        "/sap-systems",
        _body(gateway.base_url, credentials={"auth_type": "basic", "username": "X"}),
    )
    assert no_password.status_code == 422


def test_delete_destroys_credentials_and_frees_code(
    admin: ApiClient, gateway: RunningGateway, platform_db: Database
) -> None:
    created = _create(admin, gateway.base_url)["system"]
    with platform_db.session() as session:
        before = session.scalar(select(func.count()).select_from(Secret)) or 0
    deleted = admin.delete(f"/sap-systems/{created['id']}", params={"reason": "Sistema retirado"})
    assert deleted.status_code == 204
    with platform_db.session() as session:
        after = session.scalar(select(func.count()).select_from(Secret)) or 0
    assert after == before - 1
    assert admin.get(f"/sap-systems/{created['id']}").status_code == 404
    again = admin.post("/sap-systems", _body(gateway.base_url, code=created["code"]))
    assert again.status_code == 201, again.text
    deletion = audit_actions(admin, resource_id=created["id"], action="sap_connection.deleted")
    assert deletion[0]["reason"] == "Sistema retirado"


def test_permissions(
    admin: ApiClient, gateway: RunningGateway, client_factory: ClientFactory
) -> None:
    system = _create(admin, gateway.base_url)["system"]
    created = create_user(admin, "viewer")
    viewer = client_factory()
    activate_user(viewer, created["user"]["username"], created["temporary_password"])

    assert viewer.get("/sap-systems").status_code == 200
    assert viewer.get(f"/sap-systems/{system['id']}/services").status_code == 200
    denied = viewer.post("/sap-systems", _body(gateway.base_url))
    assert denied.status_code == 403
    assert viewer.post(f"/sap-systems/{system['id']}/test").status_code == 403
    assert viewer.delete(f"/sap-systems/{system['id']}").status_code == 403


def test_allowed_hosts_restrict_where_the_platform_connects(
    app_settings: Settings, admin_password: str, gateway: RunningGateway
) -> None:
    restricted = create_app(
        app_settings.model_copy(update={"sap_allowed_hosts": ["empresa.local"]})
    )
    client = ApiClient(restricted)
    assert client.login(ADMIN_USERNAME, admin_password) == "active"

    blocked = client.post("/sap-systems/test", _body(gateway.base_url))
    assert blocked.status_code == 422, blocked.text
    problem = blocked.json()
    assert problem["code"] == "SX-NET-HOST-NOT-ALLOWED"
    assert problem["errors"][0]["field"] == "base_url"

    saved = client.post("/sap-systems", _body(gateway.base_url))
    assert saved.status_code == 422
    metadata = client.post("/sap-systems/test", _body("http://169.254.169.254"))
    assert metadata.json()["code"] == "SX-NET-HOST-NOT-ALLOWED"
