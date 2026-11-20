"""Identidad de punta a punta contra PostgreSQL real (plan 02, pruebas clave)."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from sapextract.core.db import Database
from sapextract.identity.domain import totp
from tests.integration.conftest import (
    ADMIN_USERNAME,
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    role_id,
    unique,
)

pytestmark = pytest.mark.integration

ClientFactory = Callable[[], ApiClient]


def test_first_login_forces_password_change(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "viewer")
    username, temporary = created["user"]["username"], created["temporary_password"]
    assert temporary
    client = client_factory()
    assert client.login(username, temporary) == "password_change"

    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["stage"] == "password_change"
    blocked = client.get("/roles")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "SX-AUTH-STEP-REQUIRED"

    weak = client.post("/auth/password/change", {"new_password": "password1234"})
    assert weak.status_code == 422
    assert weak.json()["code"] == "SX-AUTH-PASSWORD-POLICY"
    reuse = client.post("/auth/password/change", {"new_password": temporary})
    assert reuse.status_code == 422

    ok = client.post("/auth/password/change", {"new_password": "Primer.Acceso-Valido-1"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "active"
    me = client.get("/me").json()
    assert me["stage"] == "active"
    assert "pipeline:read" in me["permissions"]
    assert "user:read" not in me["permissions"]


def test_session_id_rotates_after_each_step(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "viewer")
    client = client_factory()
    client.login(created["user"]["username"], created["temporary_password"])
    first = client.http.cookies.get("sx_session")
    client.post("/auth/password/change", {"new_password": "Rotacion.De-Sesion-9"})
    assert client.http.cookies.get("sx_session") not in (None, first)


def test_concurrent_logins_of_the_same_user(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    """Varias pestañas o procesos entrando a la vez no dan 500 (bloqueo de la fila)."""
    created = create_user(admin, "viewer")
    username = created["user"]["username"]
    password = activate_user(client_factory(), username, created["temporary_password"])
    clients = [client_factory() for _ in range(6)]

    def login(client: ApiClient) -> int:
        response = client.post("/auth/login", {"username": username, "password": password})
        return response.status_code

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        statuses = list(pool.map(login, clients))
    assert statuses == [200] * len(clients)


def test_wrong_password_locks_account_temporarily(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "viewer")
    user_id, username = created["user"]["id"], created["user"]["username"]
    client = client_factory()
    for _ in range(5):
        response = client.post("/auth/login", {"username": username, "password": "Incorrecta-123"})
        assert response.status_code == 401
        assert response.json()["code"] == "SX-AUTH-INVALID-CREDENTIALS"
    # Bloqueada: ni la contraseña correcta entra (y el mensaje no revela el motivo).
    locked = client.post(
        "/auth/login", {"username": username, "password": created["temporary_password"]}
    )
    assert locked.status_code == 401
    detail = admin.get(f"/users/{user_id}").json()
    assert detail["temporarily_locked"] is True
    actions = [e["action"] for e in audit_actions(admin, resource_id=user_id)]
    assert "user.locked" in actions
    assert actions.count("auth.login_failed") == 6

    assert admin.post(f"/users/{user_id}/unlock").status_code == 200
    assert client.login(username, created["temporary_password"]) == "password_change"


def test_unknown_user_gets_same_answer(client_factory: ClientFactory) -> None:
    response = client_factory().post(
        "/auth/login", {"username": "no-existe", "password": "Cualquiera-123"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "SX-AUTH-INVALID-CREDENTIALS"


def test_csrf_header_is_required_for_cookie_sessions(admin: ApiClient) -> None:
    response = admin.post(
        "/users",
        {"username": unique("csrf"), "display_name": "X"},
        headers={"X-CSRF-Token": "falso"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "SX-AUTH-CSRF"


def test_permission_denied_is_audited(client_factory: ClientFactory, admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    client = client_factory()
    activate_user(client, created["user"]["username"], created["temporary_password"])
    response = client.get("/users")
    assert response.status_code == 403
    assert response.json()["code"] == "SX-AUTH-FORBIDDEN"
    denied = audit_actions(admin, action="auth.access_denied", actor_id=created["user"]["id"])
    assert denied
    assert denied[0]["outcome"] == "denied"
    assert denied[0]["context"]["missing"] == ["user:read"]


def test_disable_closes_sessions_and_blocks_login(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "operator")
    client = client_factory()
    password = activate_user(client, created["user"]["username"], created["temporary_password"])
    assert client.get("/me").status_code == 200

    disabled = admin.post(f"/users/{created['user']['id']}/disable", {"reason": "Baja"})
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert client.get("/me").status_code == 401
    retry = client_factory().post(
        "/auth/login", {"username": created["user"]["username"], "password": password}
    )
    assert retry.status_code == 401

    assert admin.post(f"/users/{created['user']['id']}/enable").json()["status"] == "active"


def test_break_glass_and_self_protection(admin: ApiClient) -> None:
    me = admin.get("/me").json()
    assert me["is_break_glass"] is True
    self_disable = admin.post(f"/users/{me['id']}/disable", {})
    assert self_disable.status_code == 422
    assert self_disable.json()["code"] == "SX-IDN-SELF-ACTION"
    self_roles = admin.put(f"/users/{me['id']}/roles", {"role_ids": []})
    assert self_roles.json()["code"] == "SX-IDN-SELF-ACTION"


def test_other_admin_cannot_disable_break_glass(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "platform_admin")
    other = client_factory()
    activate_user(other, created["user"]["username"], created["temporary_password"])
    root_id = admin.get("/me").json()["id"]
    response = other.post(f"/users/{root_id}/disable", {})
    assert response.status_code == 422
    assert response.json()["code"] == "SX-IDN-BREAK-GLASS"
    assert other.put(f"/users/{root_id}/roles", {"role_ids": []}).status_code == 422


def test_idle_session_expires(
    client_factory: ClientFactory, admin: ApiClient, platform_db: Database
) -> None:
    created = create_user(admin, "viewer")
    client = client_factory()
    activate_user(client, created["user"]["username"], created["temporary_password"])
    with platform_db.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE sx.user_session SET last_seen_at = now() - interval '2 hours' "
                "WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"u": created["user"]["id"]},
        )
    assert client.get("/me").status_code == 401
    events = audit_actions(admin, action="auth.session_expired", actor_id=created["user"]["id"])
    assert len(events) == 1


def test_mfa_enrollment_and_login(client_factory: ClientFactory, admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    username = created["user"]["username"]
    client = client_factory()
    password = activate_user(client, username, created["temporary_password"])

    setup = client.post("/auth/mfa/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["qr_svg_data_uri"].startswith("data:image/svg+xml")
    bad = client.post("/auth/mfa/enable", {"code": "000000"})
    assert bad.status_code == 422
    step = totp.time_step(time.time())
    enabled = client.post("/auth/mfa/enable", {"code": totp.code_at(secret, step)})
    assert enabled.status_code == 200, enabled.text
    assert client.get("/me").json()["mfa_enabled"] is True

    second = client_factory()
    assert second.login(username, password) == "mfa"
    assert second.get("/users").status_code in (401, 403)
    # El código del mismo paso ya se usó al activar: se rechaza (anti-repetición).
    replay = second.post("/auth/mfa/verify", {"code": totp.code_at(secret, step)})
    assert replay.status_code == 401
    assert replay.json()["code"] == "SX-AUTH-MFA-INVALID"
    verified = second.post("/auth/mfa/verify", {"code": totp.code_at(secret, step + 1)})
    assert verified.status_code == 200, verified.text
    assert verified.json()["stage"] == "active"

    reset = admin.post(f"/users/{created['user']['id']}/reset-mfa")
    assert reset.status_code == 200
    assert reset.json()["mfa_enabled"] is False
    assert second.get("/me").status_code == 401  # restablecer el MFA cierra sus sesiones


def test_roles_crud_and_anti_escalation(client_factory: ClientFactory, admin: ApiClient) -> None:
    code = unique("rol_")
    created = admin.post(
        "/roles",
        {
            "code": code,
            "name": f"Gestor de roles {code}",
            "permissions": ["role:read", "role:manage"],
        },
    )
    assert created.status_code == 201, created.text
    custom_id = created.json()["id"]

    user = create_user(admin, code)
    manager = client_factory()
    activate_user(manager, user["user"]["username"], user["temporary_password"])
    escalation = manager.post(
        "/roles",
        {"code": unique("esc_"), "name": unique("Escalada "), "permissions": ["user:create"]},
    )
    assert escalation.status_code == 403
    assert escalation.json()["code"] == "SX-IDN-ESCALATION"

    system_id = role_id(admin, "viewer")
    assert admin.patch(f"/roles/{system_id}", {"name": "X"}).json()["code"] == "SX-IDN-SYSTEM-ROLE"
    in_use = admin.delete(f"/roles/{custom_id}")
    assert in_use.status_code == 409

    updated = admin.patch(
        f"/roles/{custom_id}", {"permissions": ["role:read"], "version": created.json()["version"]}
    )
    assert updated.status_code == 200
    assert updated.json()["permissions"] == ["role:read"]
    stale = admin.patch(f"/roles/{custom_id}", {"name": "Otro", "version": 1})
    assert stale.status_code == 409

    events = audit_actions(admin, resource_id=custom_id)
    role_updated = next(e for e in events if e["action"] == "role.updated")
    assert role_updated["changes"]["permissions"] == {
        "before": ["role:manage", "role:read"],
        "after": ["role:read"],
    }


def test_api_token_scopes_and_revocation(client_factory: ClientFactory, admin: ApiClient) -> None:
    created = admin.post(
        "/api-tokens", {"name": "Integración", "scopes": ["user:read"], "expires_in_days": 1}
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    assert secret.startswith("sxp_")
    bearer = {"Authorization": f"Bearer {secret}"}
    anonymous = client_factory()

    assert anonymous.get("/users", headers=bearer).status_code == 200
    forbidden = anonymous.post("/users", {"username": "x", "display_name": "x"}, headers=bearer)
    assert forbidden.status_code == 403

    revoked = admin.delete(f"/api-tokens/{created.json()['token']['id']}")
    assert revoked.json()["status"] == "revoked"
    assert anonymous.get("/users", headers=bearer).status_code == 401


def test_anonymize_requires_disabled_and_keeps_audit(admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    user_id = created["user"]["id"]
    early = admin.post(f"/users/{user_id}/anonymize", {"reason": "Solicitud formal 123"})
    assert early.json()["code"] == "SX-IDN-NOT-DISABLED"
    admin.post(f"/users/{user_id}/disable", {})
    done = admin.post(f"/users/{user_id}/anonymize", {"reason": "Solicitud formal 123"})
    assert done.status_code == 200
    body = done.json()
    assert body["username"].startswith("anon-")
    assert body["email"] is None
    assert body["roles"] == []
    actions = [e["action"] for e in audit_actions(admin, resource_id=user_id)]
    assert {"user.created", "user.disabled", "user.anonymized"} <= set(actions)


def test_users_list_filters(admin: ApiClient) -> None:
    created = create_user(admin, "auditor", username=unique("filtro"))
    page = admin.get("/users", params={"q": created["user"]["username"]}).json()
    assert page["total"] == 1
    assert page["items"][0]["roles"][0]["code"] == "auditor"
    by_role = admin.get("/users", params={"role_id": role_id(admin, "platform_admin")}).json()
    assert ADMIN_USERNAME in [u["username"] for u in by_role["items"]]


def test_my_sessions_and_logout(client_factory: ClientFactory, admin: ApiClient) -> None:
    sessions = admin.get("/me/sessions").json()
    assert any(s["current"] for s in sessions)
    other = client_factory()
    created = create_user(admin, "viewer")
    activate_user(other, created["user"]["username"], created["temporary_password"])
    assert other.post("/auth/logout").status_code == 204
    assert other.get("/me").status_code == 401
