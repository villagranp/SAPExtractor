"""Destinos de punta a punta: API + PostgreSQL + carpeta CSV real (plan 07, It4a).

Snowflake se prueba aquí con un plugin cuya *prueba de conexión* está simulada (no hay red
en CI): el resto del flujo (validación, cifrado del par de claves, rotación, auditoría) es el
real. La batería contra una cuenta Snowflake real está en ``tests/contract`` y solo corre
con ``SX_TEST_SNOWFLAKE_*`` definidas.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select

from sapextract.core.clock import utcnow
from sapextract.core.config import Settings
from sapextract.core.db import Database
from sapextract.core.secret_store import Secret
from sapextract.destinations.application.plugins import PluginContext, PluginRegistry
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.destinations.domain.testing import CheckReport, CheckStatus, CheckStep
from sapextract.destinations.infrastructure.csv_plugin import CsvPlugin
from sapextract.destinations.infrastructure.snowflake_plugin import SnowflakePlugin
from sapextract.main_api import create_app
from tests.integration.conftest import (
    ADMIN_USERNAME,
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    unique,
)
from tests.support.sap_gateway import MOCK_PASSWORD, MOCK_USER, RunningGateway, start_gateway

pytestmark = pytest.mark.integration


class OfflineSnowflakePlugin(SnowflakePlugin):
    """Snowflake real salvo la prueba de conexión, que devuelve un resultado fijo."""

    calls: ClassVar[list[str]] = []

    def test(self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext) -> CheckReport:
        self.calls.append(type(secret).__name__)
        now = utcnow()
        return CheckReport(
            started_at=now,
            finished_at=now,
            steps=tuple(
                CheckStep(name=n, status=CheckStatus.OK, message="Simulado.")
                for n in ("network", "authentication", "context", "privileges")
            ),
        )


def _pem(passphrase: bytes | None = None) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()


@pytest.fixture(scope="module")
def csv_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tmp_path_factory.mktemp("csv-root"))


@pytest.fixture(scope="module")
def dest_app(app_settings: Settings, platform_db: Database, csv_root: Path) -> FastAPI:
    settings = app_settings.model_copy(
        update={"csv_roots": [csv_root], "dest_allowed_hosts": ["snowflakecomputing.com"]}
    )
    app = create_app(settings)
    app.state.destinations = DestinationsToolkit.build(
        settings,
        audit=app.state.identity.audit,
        registry=PluginRegistry([CsvPlugin(), OfflineSnowflakePlugin()]),
    )
    return app


@pytest.fixture
def client(dest_app: FastAPI, admin_password: str) -> ApiClient:
    api = ApiClient(dest_app)
    assert api.login(ADMIN_USERNAME, admin_password) == "active"
    return api


@pytest.fixture(scope="module")
def gateway() -> Iterator[RunningGateway]:
    running = start_gateway()
    yield running
    running.stop()


def _csv_body(root: Path, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": unique("CSV_").upper(),
        "name": "Carpeta de intercambio",
        "type": "csv",
        "environment": "DEV",
        "tags": ["Finanzas"],
        "config": {"root": str(root), "path": "fi/gl"},
    }
    body.update(overrides)
    return body


def _snowflake_body(pem: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": unique("SNOW_").upper(),
        "name": "Snowflake finanzas",
        "type": "snowflake",
        "environment": "QAS",
        "config": {
            "account": "ab12345.east-us-2.azure",
            "user": "sx_loader",
            "role": "sx_role",
            "warehouse": "sx_wh",
            "database": "raw",
            "schema_name": "sap",
        },
        "secret": {"private_key_pem": pem},
    }
    body.update(overrides)
    return body


def _create(api: ApiClient, body: dict[str, Any]) -> dict[str, Any]:
    response = api.post("/destinations", body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def _steps(report: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["status"] for s in report["steps"]}


# ================================================================== tipos
def test_destination_types_publish_their_form_schema(client: ApiClient, csv_root: Path) -> None:
    types = {t["type"]: t for t in client.get("/destination-types").json()}
    assert set(types) == {"csv", "snowflake"}
    assert types["csv"]["requires_secret"] is False
    assert set(types["snowflake"]["supported_modes"]) == {"append", "upsert", "replace"}

    csv = client.get("/destination-types/csv/schema").json()
    assert csv["config_schema"]["properties"]["root"]["enum"] == [str(csv_root)]
    assert csv["secret_schema"] is None
    snow = client.get("/destination-types/snowflake/schema").json()
    assert snow["config_schema"]["properties"]["authentication"]["enum"] == ["key_pair", "pat"]
    # Clave privada y PAT son opcionales (uno u otro según el método): van dentro de anyOf.
    for field in ("private_key_pem", "token"):
        prop = snow["secret_schema"]["properties"][field]
        secret = next(p for p in prop["anyOf"] if p.get("type") == "string")
        assert secret["writeOnly"] is True
        assert secret["format"] == "password"


# ================================================================== CSV
def test_csv_destination_lifecycle(client: ApiClient, csv_root: Path) -> None:
    created = _create(client, _csv_body(csv_root, activate=True))
    destination, test = created["destination"], created["test"]
    assert test["ok"], test
    assert _steps(test) == {"path": "ok", "write": "ok"}
    assert destination["status"] == "active"
    assert destination["tags"] == ["finanzas"]
    assert destination["target"] == f"{csv_root}/fi/gl"
    assert (csv_root / "fi" / "gl").is_dir()
    did = destination["id"]

    listed = client.get("/destinations", params={"type": "csv", "q": destination["code"]}).json()
    assert [d["code"] for d in listed["items"]] == [destination["code"]]

    # Cambiar la configuración deja la prueba desactualizada; activar exige probar de nuevo.
    off = client.post(f"/destinations/{did}/deactivate", {"reason": "Mantenimiento"})
    assert off.json()["status"] == "inactive"
    edited = client.patch(
        f"/destinations/{did}",
        {"version": off.json()["version"], "config": {"root": str(csv_root), "path": "fi/gl2"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["test_outdated"] is True
    blocked = client.post(f"/destinations/{did}/activate")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SX-DST-TEST-REQUIRED"
    retest = client.post(f"/destinations/{did}/test").json()
    assert retest["test"]["ok"]
    assert retest["destination"]["test_outdated"] is False
    assert client.post(f"/destinations/{did}/activate").json()["status"] == "active"

    # Bloqueo optimista con una versión antigua.
    stale = client.patch(f"/destinations/{did}", {"version": 1, "name": "Otro"})
    assert stale.status_code == 409

    actions = {e["action"] for e in audit_actions(client, resource_id=did)}
    assert {
        "destination.created",
        "destination.tested",
        "destination.activated",
        "destination.deactivated",
        "destination.updated",
    } <= actions

    assert client.delete(f"/destinations/{did}", params={"reason": "Ya no se usa"}).status_code == (
        204
    )
    assert client.get(f"/destinations/{did}").status_code == 404
    # El código queda libre tras la baja lógica.
    _create(client, _csv_body(csv_root, code=destination["code"], run_test=False))


@pytest.mark.parametrize(
    ("config", "field", "code"),
    [
        ({"path": "../fuera"}, "config.path", "SX-DST-CSV-PATH"),
        ({"path": "/etc"}, "config.path", "SX-DST-CSV-PATH"),
        ({"root": "/no/permitida"}, "config.root", "SX-DST-CSV-ROOT"),
        ({"delimiter": ""}, "config.delimiter", "SX-DST-CONFIG-INVALID"),
    ],
)
def test_csv_rejects_paths_outside_the_allowed_roots(
    client: ApiClient, csv_root: Path, config: dict[str, str], field: str, code: str
) -> None:
    body = _csv_body(csv_root)
    body["config"] = {**body["config"], **config}
    response = client.post("/destinations", body)
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == code
    assert field in {e["field"] for e in problem["errors"]}


def test_unsaved_csv_test_is_audited(client: ApiClient, csv_root: Path) -> None:
    report = client.post("/destinations/test", {**_csv_body(csv_root), "code": "csv_borrador"})
    assert report.status_code == 200, report.text
    assert report.json()["ok"] is True
    events = [
        e
        for e in audit_actions(client, action="destination.tested")
        if e["resource_name"] == "CSV_BORRADOR"
    ]
    assert events


# ================================================================== Snowflake
def test_snowflake_key_pair_is_never_returned_logged_or_stored_in_clear(
    client: ApiClient, platform_db: Database
) -> None:
    pem = _pem()
    created = _create(client, _snowflake_body(pem))
    destination = created["destination"]
    assert created["test"]["ok"]
    assert destination["credential_info"]["public_key_fingerprint"].startswith("SHA256:")
    assert destination["config"]["user"] == "SX_LOADER"  # se normaliza a mayúsculas
    assert destination["target"] == "ab12345.east-us-2.azure · RAW.SAP"
    secret_line = pem.splitlines()[1]
    assert secret_line not in json.dumps(created)
    assert secret_line not in client.get(f"/destinations/{destination['id']}").text
    events = audit_actions(client, resource_id=destination["id"])
    assert secret_line not in json.dumps(events)
    with platform_db.session() as session:
        rows = session.scalars(select(Secret).where(Secret.kind == "destination_credentials"))
        for row in rows:
            assert secret_line not in row.ciphertext

    rotated = client.post(
        f"/destinations/{destination['id']}/credentials/rotate",
        {
            "secret": {
                "private_key_pem": _pem(b"frase-larga"),
                "private_key_passphrase": "frase-larga",
            }
        },
    )
    assert rotated.status_code == 200, rotated.text
    after = rotated.json()["destination"]["credential_info"]["public_key_fingerprint"]
    assert after != destination["credential_info"]["public_key_fingerprint"]
    rotation = audit_actions(
        client, resource_id=destination["id"], action="destination.credentials_rotated"
    )
    assert rotation[0]["changes"]["credentials"] == "***"

    # El asistente de edición puede probar reutilizando las credenciales guardadas.
    OfflineSnowflakePlugin.calls.clear()
    reuse = client.post(
        "/destinations/test",
        {
            "type": "snowflake",
            "config": destination["config"],
            "reuse_credentials_from": destination["id"],
        },
    )
    assert reuse.status_code == 200, reuse.text
    assert OfflineSnowflakePlugin.calls == ["SnowflakeSecret"]


def test_snowflake_requires_credentials_and_valid_identifiers(client: ApiClient) -> None:
    missing = client.post("/destinations", _snowflake_body(_pem(), secret=None))
    assert missing.status_code == 422
    assert missing.json()["code"] == "SX-DST-SECRET-MISSING"
    body = _snowflake_body(_pem())
    body["config"]["database"] = 'raw"; drop table x; --'
    bad = client.post("/destinations", body)
    assert bad.status_code == 422
    assert "config.database" in {e["field"] for e in bad.json()["errors"]}
    not_pem = client.post("/destinations", _snowflake_body("no es una clave"))
    assert not_pem.status_code == 422, not_pem.text


# ================================================================== permisos
def test_viewer_reads_but_cannot_manage_destinations(
    client: ApiClient, csv_root: Path, dest_app: FastAPI
) -> None:
    created = _create(client, _csv_body(csv_root, run_test=False))
    user = create_user(client, "viewer")
    viewer = ApiClient(dest_app)
    activate_user(viewer, user["user"]["username"], user["temporary_password"])
    assert viewer.get(f"/destinations/{created['destination']['id']}").status_code == 200
    assert viewer.post("/destinations", _csv_body(csv_root)).status_code == 403
    assert viewer.post(f"/destinations/{created['destination']['id']}/test").status_code == 403


# ================================================================== DDL
def test_ddl_preview_from_sap_metadata(
    client: ApiClient, gateway: RunningGateway, csv_root: Path
) -> None:
    system = client.post(
        "/sap-systems",
        {
            "code": unique("S4D_").upper(),
            "name": "S/4 simulado",
            "environment": "DEV",
            "system_type": "S4HANA_ONPREM",
            "base_url": gateway.base_url,
            "sap_client": "100",
            "language": "ES",
            "timeout_seconds": 5,
            "credentials": {
                "auth_type": "basic",
                "username": MOCK_USER,
                "password": MOCK_PASSWORD,
            },
            "run_test": False,
        },
    )
    assert system.status_code == 201, system.text
    sid = system.json()["system"]["id"]
    assert client.get(f"/sap-systems/{sid}/services").status_code == 200
    snow = _create(client, _snowflake_body(_pem(), run_test=False))["destination"]
    source = {
        "sap_system_id": sid,
        "service": "ZFI_WS_GL_ITEMS_SRV",
        "entity_set": "EntityOf0FI_GL_14",
    }

    preview = client.post(
        f"/destinations/{snow['id']}/ddl-preview",
        {"source": source, "target_name": "gl_items", "column_mapping": {"ODQ_CHANGEMODE": None}},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["keys"] == ["RBUKRS", "GJAHR", "BELNR", "BUZEI"]
    create_table = data["statements"][0]
    assert 'CREATE TABLE IF NOT EXISTS "RAW"."SAP"."GL_ITEMS"' in create_table
    assert '"_SX_DELETED"' in create_table
    excluded = next(c for c in data["columns"] if c["source"] == "ODQ_CHANGEMODE")
    assert excluded["target"] is None

    csv = _create(client, _csv_body(csv_root, run_test=False))["destination"]
    upsert = client.post(
        f"/destinations/{csv['id']}/ddl-preview", {"source": source, "target_name": "gl"}
    )
    assert upsert.status_code == 422
    assert upsert.json()["code"] == "SX-DST-MODE-UNSUPPORTED"
    missing = client.post(
        f"/destinations/{snow['id']}/ddl-preview",
        {"source": {**source, "entity_set": "NoExiste"}, "target_name": "gl"},
    )
    assert missing.status_code == 404
