"""Sincronización del catálogo de permisos/roles de sistema y administradores locales.

Se ejecuta tras cada ``sapextract db upgrade`` (idempotente) y desde la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import Actor, AuditEntry
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, FieldIssue
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.application.user_service import reset_local_password, user_snapshot
from sapextract.identity.domain.model import (
    USERNAME_PATTERN,
    AuthSource,
    RoleSource,
    UserStatus,
    normalize_username,
)
from sapextract.identity.domain.password_policy import generate_temporary_password
from sapextract.identity.domain.permissions import PERMISSIONS, PLATFORM_ADMIN, SYSTEM_ROLES
from sapextract.identity.infrastructure.models import (
    LocalCredential,
    Permission,
    Role,
    RolePermission,
    UserAccount,
    UserRole,
)
from sapextract.identity.infrastructure.queries import find_user, revoke_user_sessions, role_by_code

log = structlog.get_logger(__name__)

CLI_ACTOR = Actor.system("cli")


@dataclass(frozen=True, kw_only=True)
class AdminCredentials:
    username: str
    password: str
    generated: bool


def sync_catalog(session: Session) -> None:
    """Deja permisos y roles de sistema exactamente como los define el código."""
    wanted = {p.code: p for p in PERMISSIONS}
    existing = {p.code: p for p in session.scalars(select(Permission))}
    for code, definition in wanted.items():
        row = existing.get(code)
        if row is None:
            session.add(
                Permission(code=code, module=definition.module, description=definition.description)
            )
        else:
            row.module = definition.module
            row.description = definition.description
    obsolete = set(existing) - set(wanted)
    if obsolete:
        session.execute(delete(Permission).where(Permission.code.in_(obsolete)))
    session.flush()

    for role_def in SYSTEM_ROLES:
        role = role_by_code(session, role_def.code)
        if role is None:
            role = Role(code=role_def.code, name=role_def.name, is_system=True)
            session.add(role)
        role.name = role_def.name
        role.description = role_def.description
        role.is_system = True
        current = set(role.permission_codes)
        if current != role_def.permissions:
            role.permission_links = [
                link
                for link in role.permission_links
                if link.permission_code in role_def.permissions
            ]
            role.permission_links.extend(
                RolePermission(permission_code=code)
                for code in sorted(role_def.permissions - current)
            )
    session.flush()


def create_local_admin(
    session: Session,
    toolkit: IdentityToolkit,
    *,
    username: str,
    display_name: str,
    password: str | None,
    actor: Actor = CLI_ACTOR,
) -> AdminCredentials:
    """Crea un administrador local de emergencia (break-glass) con el rol de plataforma."""
    uname = normalize_username(username)
    if not USERNAME_PATTERN.match(uname):
        msg = "El nombre de usuario no es válido (3-100: minúsculas, números y . _ @ -)."
        raise BusinessRuleError(msg, code="SX-IDN-USERNAME-INVALID")
    if find_user(session, uname) is not None:
        msg = f"Ya existe un usuario con el nombre «{uname}»."
        raise ConflictError(msg, code="SX-IDN-USERNAME-TAKEN")
    generated = password is None
    secret = password or generate_temporary_password()
    if not generated:
        problems = toolkit.policy.violations(secret, username=uname)
        if problems:
            msg = "La contraseña no cumple la política."
            raise BusinessRuleError(
                msg,
                code="SX-AUTH-PASSWORD-POLICY",
                errors=[FieldIssue("password", p) for p in problems],
            )
    admin_role = role_by_code(session, PLATFORM_ADMIN)
    if admin_role is None:
        sync_catalog(session)
        admin_role = role_by_code(session, PLATFORM_ADMIN)
    user = UserAccount(
        username=uname,
        display_name=display_name.strip() or uname,
        auth_source=AuthSource.LOCAL.value,
        status=UserStatus.ACTIVE.value,
        is_break_glass=True,
        failed_attempts=0,
        must_change_password=True,
        created_by=actor.name,
        updated_by=actor.name,
        credential=LocalCredential(password_hash=toolkit.hashing.hash(secret), password_history=[]),
        role_links=[
            UserRole(role=admin_role, source=RoleSource.MANUAL.value, assigned_by=actor.name)
        ],
    )
    session.add(user)
    session.flush()
    toolkit.audit.record(
        session,
        AuditEntry(
            action="user.created",
            resource_type="user",
            resource_id=str(user.id),
            resource_name=user.username,
            changes={
                key: {"before": None, "after": value}
                for key, value in {
                    **user_snapshot(user),
                    "roles": [PLATFORM_ADMIN],
                    "is_break_glass": True,
                }.items()
            },
        ),
        actor=actor,
    )
    return AdminCredentials(username=uname, password=secret, generated=generated)


def reset_password_cli(
    session: Session, toolkit: IdentityToolkit, *, username: str
) -> AdminCredentials:
    """Recuperación del administrador local desde el servidor (no hay recuperación por correo)."""
    user = find_user(session, normalize_username(username))
    if user is None or user.auth_source != AuthSource.LOCAL:
        msg = "No existe un usuario local con ese nombre."
        raise BusinessRuleError(msg, code="SX-IDN-USER-NOT-FOUND")
    temporary = generate_temporary_password()
    reset_local_password(user, temporary, toolkit)
    if user.status == UserStatus.LOCKED:
        user.status = UserStatus.ACTIVE.value
    revoke_user_sessions(session, user.id, reason="password_reset", now=utcnow())
    toolkit.audit.record(
        session,
        AuditEntry(
            action="user.password_reset",
            resource_type="user",
            resource_id=str(user.id),
            resource_name=user.username,
            reason="Restablecida desde la CLI del servidor",
        ),
        actor=CLI_ACTOR,
    )
    return AdminCredentials(username=user.username, password=temporary, generated=True)


def ensure_bootstrap_admin(session: Session, toolkit: IdentityToolkit) -> str | None:
    """Primer arranque: si no hay usuarios y hay SX_BOOTSTRAP_ADMIN_*, crea el administrador."""
    settings = toolkit.settings
    if not settings.bootstrap_admin_user or settings.bootstrap_admin_password is None:
        return None
    users = session.scalar(select(func.count()).select_from(UserAccount)) or 0
    if users:
        return None
    created = create_local_admin(
        session,
        toolkit,
        username=settings.bootstrap_admin_user,
        display_name="Administrador local",
        password=settings.bootstrap_admin_password.get_secret_value(),
        actor=Actor.system("bootstrap"),
    )
    log.warning("bootstrap_admin_created", username=created.username)
    return created.username
