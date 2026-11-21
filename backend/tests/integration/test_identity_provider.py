"""Configuración de Entra ID de punta a punta (It7a): API + PostgreSQL + Entra simulado.

Cubre: credencial cifrada que nunca vuelve ni llega a la auditoría, prueba por pasos sin
guardar, activación solo con una prueba vigente, mapeo de grupos con anti-escalada y roles
protegidos frente al borrado.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from sapextract.core.db import Database
from sapextract.core.secret_store import Secret
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.infrastructure.entra_client import EntraClient
from sapextract.identity.infrastructure.entra_credentials import SECRET_KIND
from sapextract.identity.infrastructure.models import IdentityProvider, IdpGroupMapping
from tests.integration.conftest import (
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    role_id,
    unique,
)
from tests.support.fake_entra import (
    AUTHORITY,
    CLIENT_ID,
    CLIENT_SECRET,
    TENANT_ID,
    FakeEntra,
    make_rsa_certificate,
)

pytestmark = pytest.mark.integration

ENTRA = "/identity-providers/entra"
GROUP_A = "0a0a0a0a-1111-2222-3333-444444444444"
GROUP_B = "0b0b0b0b-1111-2222-3333-444444444444"


@pytest.fixture
def fake_entra(api_app: FastAPI, platform_db: Database) -> Iterator[FakeEntra]:
    """Entra simulado en el toolkit de la API y configuración de Entra vacía en cada prueba."""
    fake = FakeEntra()
    original: IdentityToolkit = api_app.state.identity
    api_app.state.identity = replace(
        original, entra=EntraClient(authority_url=AUTHORITY, transport=fake.transport())
    )
    _clear(platform_db)
    yield fake
    api_app.state.identity = original
    _clear(platform_db)


def _clear(db: Database) -> None:
    with db.session() as session:
        secret_ids = session.scalars(select(IdentityProvider.secret_id)).all()
        session.execute(delete(IdpGroupMapping))
        session.execute(delete(IdentityProvider))
        session.execute(delete(Secret).where(Secret.id.in_([s for s in secret_ids if s])))
        session.commit()


def _settings(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": None,
        "tenant_id": TENANT_ID,
        "client_id": CLIENT_ID,
        "credential": {"credential_type": "client_secret", "client_secret": CLIENT_SECRET},
        "jit_provisioning": True,
        "allowed_domains": ["Empresa.com"],
        "require_group_membership": True,
    }
    body.update(overrides)
    return body


def _configure(admin: ApiClient, **overrides: Any) -> dict[str, Any]:
    response = admin.put(ENTRA, _settings(**overrides))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _enable(admin: ApiClient) -> dict[str, Any]:
    tested = admin.post(f"{ENTRA}/test", {})
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"], tested.text
    version = admin.get(ENTRA).json()["version"]
    response = admin.post(f"{ENTRA}/enable", {"version": version})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_unconfigured_provider_shows_the_redirect_uri(
    admin: ApiClient, fake_entra: FakeEntra
) -> None:
    body = admin.get(ENTRA).json()
    assert body["configured"] is False
    assert body["redirect_uri"].endswith("/api/v1/auth/entra/callback")
    assert admin.post(f"{ENTRA}/enable", {"version": 1}).status_code == 404


def test_secret_is_stored_encrypted_and_never_returned_or_audited(
    admin: ApiClient, fake_entra: FakeEntra, platform_db: Database
) -> None:
    body = _configure(admin)
    assert body["configured"]
    assert not body["enabled"]
    assert body["allowed_domains"] == ["empresa.com"]
    assert body["credential"]["credential_type"] == "client_secret"
    assert CLIENT_SECRET not in json.dumps(body)

    with platform_db.session() as session:
        secret = session.scalars(select(Secret).where(Secret.kind == SECRET_KIND)).one()
        assert CLIENT_SECRET not in secret.ciphertext
    events = audit_actions(admin, resource_type="identity_provider")
    actions = {e["action"] for e in events}
    assert {"idp.updated", "idp.credentials_rotated"} <= actions
    assert CLIENT_SECRET not in json.dumps(events)

    # Guardar sin secreto conserva el guardado.
    kept = _configure(admin, version=body["version"], credential=None, jit_provisioning=False)
    assert kept["credential"]["credential_type"] == "client_secret"
    assert admin.post(f"{ENTRA}/test", {}).json()["ok"]


def test_draft_test_reports_entra_errors_without_saving(
    admin: ApiClient, fake_entra: FakeEntra
) -> None:
    bad = admin.post(
        f"{ENTRA}/test",
        {
            "tenant_id": TENANT_ID,
            "client_id": CLIENT_ID,
            "credential": {"credential_type": "client_secret", "client_secret": "mal"},
        },
    ).json()
    assert bad["ok"] is False
    assert bad["error_code"] == "AADSTS7000215"
    assert admin.get(ENTRA).json()["configured"] is False

    tenant = admin.post(
        f"{ENTRA}/test",
        {"tenant_id": "99999999-2222-3333-4444-555555555555", "client_id": CLIENT_ID},
    ).json()
    assert [s["status"] for s in tenant["steps"]] == ["failed", "skipped", "skipped"]

    invalid = admin.post(f"{ENTRA}/test", {"tenant_id": "contoso", "client_id": CLIENT_ID})
    assert invalid.status_code == 422
    assert invalid.json()["errors"][0]["field"] == "tenant_id"


def test_enable_requires_a_current_successful_test(admin: ApiClient, fake_entra: FakeEntra) -> None:
    body = _configure(admin)
    refused = admin.post(f"{ENTRA}/enable", {"version": body["version"]})
    assert refused.status_code == 422
    assert refused.json()["code"] == "SX-IDP-TEST-REQUIRED"

    enabled = _enable(admin)
    assert enabled["enabled"]
    assert enabled["test_current"]

    # Con Entra activo, un secreto nuevo sin probar no se guarda…
    new_secret = "Nuevo~Secreto.0123456789abcdefghijklmnopq"
    fake_entra.secrets.add(new_secret)
    change = _settings(
        version=enabled["version"],
        credential={"credential_type": "client_secret", "client_secret": new_secret},
    )
    blocked = admin.put(ENTRA, change)
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SX-IDP-TEST-REQUIRED"
    # …pero sí tras probarlo como borrador.
    assert admin.post(f"{ENTRA}/test", {"credential": change["credential"]}).json()["ok"]
    saved = admin.put(ENTRA, change)
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"]
    assert saved.json()["test_current"]

    # Un cambio que no toca la conexión no exige prueba.
    domains = _configure(admin, version=saved.json()["version"], credential=None)
    assert domains["enabled"]

    disabled = admin.post(
        f"{ENTRA}/disable", {"version": domains["version"], "reason": "Cambio de tenant"}
    )
    assert disabled.status_code == 200
    assert not disabled.json()["enabled"]
    actions = [e["action"] for e in audit_actions(admin, resource_type="identity_provider")]
    assert {"idp.enabled", "idp.disabled", "idp.tested"} <= set(actions)


def test_certificate_credential(admin: ApiClient, fake_entra: FakeEntra) -> None:
    pair = make_rsa_certificate(passphrase="pem-pass")
    body = _configure(
        admin,
        credential={
            "credential_type": "certificate",
            "certificate_pem": pair.cert_pem,
            "private_key_pem": pair.key_pem,
            "private_key_passphrase": "pem-pass",
        },
    )
    info = body["credential"]
    assert info["credential_type"] == "certificate"
    assert info["certificate_subject"] == "CN=SAPExtract pruebas"
    assert len(info["certificate_thumbprint"]) == 40
    assert "PRIVATE KEY" not in json.dumps(body)

    not_uploaded = admin.post(f"{ENTRA}/test", {}).json()
    assert not_uploaded["error_code"] == "AADSTS700027"
    fake_entra.certificates.append(pair.cert_pem)  # IT sube el certificado al registro
    assert admin.post(f"{ENTRA}/test", {}).json()["ok"]

    switch = admin.put(
        ENTRA,
        _settings(version=body["version"], credential={"credential_type": "client_secret"}),
    )
    assert switch.status_code == 422
    assert switch.json()["errors"][0]["field"] == "client_secret"


def test_version_conflict(admin: ApiClient, fake_entra: FakeEntra) -> None:
    body = _configure(admin)
    _configure(admin, version=body["version"], jit_provisioning=False)
    stale = admin.put(ENTRA, _settings(version=body["version"], credential=None))
    assert stale.status_code == 409
    first_again = admin.put(ENTRA, _settings())
    assert first_again.status_code == 409


def test_group_mappings_and_role_protection(admin: ApiClient, fake_entra: FakeEntra) -> None:
    body = _configure(admin, default_role_id=role_id(admin, "viewer"))
    assert body["default_role"]["code"] == "viewer"
    mappings = [
        {
            "group_object_id": GROUP_A,
            "group_name": "SG-Admins",
            "role_id": role_id(admin, "platform_admin"),
        },
        {
            "group_object_id": GROUP_B,
            "group_name": "SG-Lectura",
            "role_id": role_id(admin, "viewer"),
        },
    ]
    saved = admin.put(f"{ENTRA}/group-mappings", {"version": body["version"], "mappings": mappings})
    assert saved.status_code == 200, saved.text
    rows = saved.json()["group_mappings"]
    assert [(r["group_name"], r["role"]["code"]) for r in rows] == [
        ("SG-Admins", "platform_admin"),
        ("SG-Lectura", "viewer"),
    ]
    assert saved.json()["version"] > body["version"]
    event = next(e for e in audit_actions(admin, action="idp.group_mappings_changed"))
    assert len(event["context"]["added"]) == 2

    duplicated = admin.put(
        f"{ENTRA}/group-mappings",
        {"version": saved.json()["version"], "mappings": [mappings[1], mappings[1]]},
    )
    assert duplicated.status_code == 422

    # Un rol usado por el mapeo no se puede borrar.
    custom = admin.post(
        "/roles",
        {"code": unique("lectura_"), "name": unique("Lectura "), "permissions": ["dashboard:read"]},
    ).json()
    with_custom = admin.put(
        f"{ENTRA}/group-mappings",
        {
            "version": saved.json()["version"],
            "mappings": [*mappings, {**mappings[1], "role_id": custom["id"]}],
        },
    )
    assert with_custom.status_code == 200, with_custom.text
    assert admin.delete(f"/roles/{custom['id']}").status_code == 409


def test_anti_escalation_and_permissions(
    admin: ApiClient, fake_entra: FakeEntra, client_factory: Callable[[], ApiClient]
) -> None:
    body = _configure(admin)
    code = unique("idp_")
    created = admin.post(
        "/roles",
        {"code": code, "name": unique("Entra "), "permissions": ["idp:manage", "role:read"]},
    )
    assert created.status_code == 201, created.text
    user = create_user(admin, code)
    idp_admin = client_factory()
    activate_user(idp_admin, user["user"]["username"], user["temporary_password"])

    assert idp_admin.get(ENTRA).status_code == 200
    escalate = idp_admin.put(
        f"{ENTRA}/group-mappings",
        {
            "version": body["version"],
            "mappings": [
                {
                    "group_object_id": GROUP_A,
                    "group_name": "SG-Admins",
                    "role_id": role_id(admin, "platform_admin"),
                }
            ],
        },
    )
    assert escalate.status_code == 403
    assert escalate.json()["code"] == "SX-IDN-ESCALATION"
    default_admin = idp_admin.put(
        ENTRA,
        _settings(
            version=body["version"],
            credential=None,
            default_role_id=role_id(admin, "platform_admin"),
        ),
    )
    assert default_admin.status_code == 403

    # Con un grupo ya mapeado a administrador (por quien sí puede), quien no tiene esos
    # permisos no puede abrir el acceso ni llevarlo a otro inquilino, ni activar Entra ID.
    mapped = admin.put(
        f"{ENTRA}/group-mappings",
        {
            "version": body["version"],
            "mappings": [
                {
                    "group_object_id": GROUP_A,
                    "group_name": "SG-Admins",
                    "role_id": role_id(admin, "platform_admin"),
                }
            ],
        },
    ).json()
    version = mapped["version"]
    other_tenant = idp_admin.put(
        ENTRA,
        _settings(
            version=version, credential=None, tenant_id="99999999-2222-3333-4444-555555555555"
        ),
    )
    assert other_tenant.status_code == 403
    assert other_tenant.json()["code"] == "SX-IDN-ESCALATION"
    open_access = idp_admin.put(
        ENTRA, _settings(version=version, credential=None, require_group_membership=False)
    )
    assert open_access.status_code == 403
    any_domain = idp_admin.put(
        ENTRA, _settings(version=version, credential=None, allowed_domains=[])
    )
    assert any_domain.status_code == 403
    assert idp_admin.post(f"{ENTRA}/enable", {"version": version}).status_code == 403
    # Restringir sí puede: menos dominios, sin alta automática.
    narrower = idp_admin.put(
        ENTRA, _settings(version=version, credential=None, jit_provisioning=False)
    )
    assert narrower.status_code == 200, narrower.text

    viewer_user = create_user(admin, "viewer")
    viewer = client_factory()
    activate_user(viewer, viewer_user["user"]["username"], viewer_user["temporary_password"])
    assert viewer.get(ENTRA).status_code == 403
