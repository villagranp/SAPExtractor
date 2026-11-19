"""Ciclo de vida de usuarios, sesiones, tokens y arranque (CLI) contra PostgreSQL real."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import func, select

from sapextract.core.config import Settings
from sapextract.core.db import Database
from sapextract.core.exceptions import BusinessRuleError, ConflictError
from sapextract.identity.application.bootstrap import (
    create_local_admin,
    ensure_bootstrap_admin,
    reset_password_cli,
    sync_catalog,
)
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.infrastructure.models import Permission, UserAccount
from tests.integration.conftest import ApiClient, activate_user, audit_actions, create_user, unique

pytestmark = pytest.mark.integration

ClientFactory = Callable[[], ApiClient]


def test_update_user_with_optimistic_lock_and_email_removal(admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    user = created["user"]
    updated = admin.patch(
        f"/users/{user['id']}",
        {"display_name": "Nombre nuevo", "email": "", "version": user["version"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_name"] == "Nombre nuevo"
    assert updated.json()["email"] is None
    assert updated.json()["version"] == user["version"] + 1

    stale = admin.patch(
        f"/users/{user['id']}", {"display_name": "Otro", "version": user["version"]}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "SX-GEN-STALE"
    unchanged = admin.patch(f"/users/{user['id']}", {"display_name": "Nombre nuevo"})
    assert unchanged.json()["version"] == updated.json()["version"]

    change = next(
        e for e in audit_actions(admin, resource_id=user["id"]) if e["action"] == "user.updated"
    )
    assert change["changes"]["display_name"] == {
        "before": "Usuario de prueba",
        "after": "Nombre nuevo",
    }


def test_duplicate_and_invalid_usernames(admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    duplicate = admin.post(
        "/users", {"username": created["user"]["username"].upper(), "display_name": "X"}
    )
    assert duplicate.status_code == 409
    invalid = admin.post("/users", {"username": "con espacios", "display_name": "X"})
    assert invalid.status_code == 422
    assert invalid.json()["errors"][0]["field"] == "username"


def test_manual_lock_unlock_and_enable(client_factory: ClientFactory, admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    user_id = created["user"]["id"]
    client = client_factory()
    activate_user(client, created["user"]["username"], created["temporary_password"])

    locked = admin.post(f"/users/{user_id}/lock", {"reason": "Revisión"})
    assert locked.json()["status"] == "locked"
    assert client.get("/me").status_code == 401
    assert admin.post(f"/users/{user_id}/lock", {}).json()["code"] == "SX-IDN-NOT-ACTIVE"
    assert admin.post(f"/users/{user_id}/enable").json()["code"] == "SX-IDN-NOT-DISABLED"

    unlocked = admin.post(f"/users/{user_id}/unlock")
    assert unlocked.json()["status"] == "active"
    actions = {e["action"] for e in audit_actions(admin, resource_id=user_id)}
    assert {"user.locked", "user.unlocked"} <= actions


def test_admin_password_reset_forces_change(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "viewer")
    user_id, username = created["user"]["id"], created["user"]["username"]
    client = client_factory()
    activate_user(client, username, created["temporary_password"])

    reset = admin.post(f"/users/{user_id}/reset-password")
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert client.get("/me").status_code == 401  # sesiones cerradas
    fresh = client_factory()
    assert fresh.login(username, temporary) == "password_change"
    assert admin.get(f"/users/{user_id}").json()["must_change_password"] is True


def test_sessions_listing_and_revocation(client_factory: ClientFactory, admin: ApiClient) -> None:
    created = create_user(admin, "viewer")
    user_id = created["user"]["id"]
    first = client_factory()
    password = activate_user(first, created["user"]["username"], created["temporary_password"])
    second = client_factory()
    assert second.login(created["user"]["username"], password) == "active"

    sessions = admin.get(f"/users/{user_id}/sessions").json()
    assert len(sessions) == 2
    own = first.get("/me/sessions").json()
    other_id = next(s["id"] for s in own if not s["current"])
    assert first.delete(f"/me/sessions/{other_id}").json() == {"revoked": 1}
    assert first.delete(f"/me/sessions/{other_id}").status_code == 404
    assert second.get("/me").status_code == 401

    assert admin.delete(f"/users/{user_id}/sessions").json() == {"revoked": 1}
    assert first.get("/me").status_code == 401


def test_api_token_rules(admin: ApiClient) -> None:
    viewer = create_user(admin, "viewer")
    owner_id = viewer["user"]["id"]
    beyond = admin.post(
        "/api-tokens",
        {"name": "Fuera de alcance", "user_id": owner_id, "scopes": ["user:read"]},
    )
    assert beyond.status_code == 422  # el dueño no tiene user:read
    unknown = admin.post("/api-tokens", {"name": "X", "scopes": ["no:existe"]})
    assert unknown.json()["code"] == "SX-IDN-TOKEN-SCOPES"

    ok = admin.post(
        "/api-tokens", {"name": "Lectura", "user_id": owner_id, "scopes": ["pipeline:read"]}
    )
    assert ok.status_code == 201, ok.text
    listed = admin.get("/api-tokens", params={"user_id": owner_id}).json()
    assert [t["name"] for t in listed] == ["Lectura"]

    admin.post(f"/users/{owner_id}/disable", {})
    assert admin.get("/api-tokens", params={"user_id": owner_id}).json()[0]["status"] == "revoked"
    inactive = admin.post(
        "/api-tokens", {"name": "Otra", "user_id": owner_id, "scopes": ["pipeline:read"]}
    )
    assert inactive.json()["code"] == "SX-IDN-TOKEN-OWNER-INACTIVE"


def test_role_validation_and_delete(admin: ApiClient) -> None:
    empty = admin.post("/roles", {"code": unique("r_"), "name": unique("R "), "permissions": []})
    assert empty.status_code == 422
    bad_code = admin.post("/roles", {"code": "X", "name": "Y", "permissions": ["user:read"]})
    assert bad_code.status_code == 422
    code = unique("borrar_")
    created = admin.post("/roles", {"code": code, "name": code, "permissions": ["user:read"]})
    assert created.status_code == 201
    duplicate = admin.post("/roles", {"code": code, "name": "Otro", "permissions": ["user:read"]})
    assert duplicate.status_code == 409
    unknown = admin.patch(f"/roles/{created.json()['id']}", {"permissions": ["no:existe"]})
    assert unknown.json()["code"] == "SX-IDN-PERMISSION-UNKNOWN"
    assert admin.delete(f"/roles/{created.json()['id']}").status_code == 204
    assert admin.get(f"/roles/{created.json()['id']}").status_code == 404
    assert len(admin.get("/permissions").json()) > 20


def test_cli_bootstrap_helpers(app_settings: Settings, platform_db: Database) -> None:
    toolkit = IdentityToolkit.build(app_settings)
    with platform_db.session() as session:
        sync_catalog(session)  # idempotente
        session.commit()
        assert (session.scalar(select(func.count()).select_from(Permission)) or 0) > 20
        # Con usuarios ya existentes no se crea otro administrador inicial.
        settings = app_settings.model_copy(
            update={"bootstrap_admin_user": "otro", "bootstrap_admin_password": None}
        )
        assert ensure_bootstrap_admin(session, IdentityToolkit.build(settings)) is None

        username = unique("bg")
        created = create_local_admin(
            session, toolkit, username=username, display_name="", password=None
        )
        session.commit()
        assert created.generated
        with pytest.raises(ConflictError):
            create_local_admin(session, toolkit, username=username, display_name="", password=None)
        session.rollback()
        with pytest.raises(BusinessRuleError):
            create_local_admin(
                session, toolkit, username=unique("bg"), display_name="", password="corta"
            )
        session.rollback()

        reset = reset_password_cli(session, toolkit, username=username)
        session.commit()
        assert reset.password != created.password
        user = session.scalar(select(UserAccount).where(UserAccount.username == username))
        assert user is not None
        assert user.is_break_glass
        assert user.must_change_password
        with pytest.raises(BusinessRuleError):
            reset_password_cli(session, toolkit, username="no-existe")
