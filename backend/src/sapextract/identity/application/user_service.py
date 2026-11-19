"""Ciclo de vida de usuarios: alta, edición, roles, bloqueo, baja y anonimización (plan 02 §5)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, selectinload

from sapextract.audit.domain.diff import diff
from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, FieldIssue
from sapextract.core.pagination import PageParams
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain.model import (
    USERNAME_PATTERN,
    AuthSource,
    Principal,
    RoleSource,
    UserStatus,
    normalize_username,
)
from sapextract.identity.domain.password_policy import generate_temporary_password
from sapextract.identity.domain.rules import (
    ensure_admins_remain,
    ensure_can_grant,
    ensure_not_break_glass,
    ensure_not_self,
)
from sapextract.identity.infrastructure.models import (
    LocalCredential,
    Role,
    UserAccount,
    UserRole,
    UserSession,
)
from sapextract.identity.infrastructure.queries import (
    count_other_active_admins,
    find_user,
    get_role,
    get_user,
    is_active_admin,
    revoke_user_sessions,
    revoke_user_tokens,
)

_SORTABLE: dict[str, Any] = {
    "username": UserAccount.username,
    "display_name": UserAccount.display_name,
    "status": UserAccount.status,
    "created_at": UserAccount.created_at,
    "last_login_at": UserAccount.last_login_at,
}


@dataclass(frozen=True, kw_only=True)
class UserFilters:
    q: str | None = None
    status: UserStatus | None = None
    auth_source: AuthSource | None = None
    role_id: uuid.UUID | None = None
    include_anonymized: bool = False


@dataclass(frozen=True, kw_only=True)
class NewUser:
    username: str
    display_name: str
    email: str | None
    auth_source: AuthSource
    role_ids: list[uuid.UUID]


@dataclass(frozen=True, kw_only=True)
class UserChanges:
    display_name: str | None = None
    email: str | None = None
    clear_email: bool = False
    version: int | None = None


@dataclass(frozen=True, kw_only=True)
class CreatedUser:
    user: UserAccount
    temporary_password: str | None


def user_snapshot(user: UserAccount) -> dict[str, Any]:
    return {
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "auth_source": user.auth_source,
        "status": user.status,
    }


class UserService:
    def __init__(self, session: Session, toolkit: IdentityToolkit) -> None:
        self.session = session
        self.tk = toolkit

    # ------------------------------------------------------------------ consultas
    def search(self, filters: UserFilters, page: PageParams) -> tuple[list[UserAccount], int]:
        conditions: list[ColumnElement[bool]] = []
        if not filters.include_anonymized:
            conditions.append(UserAccount.deleted_at.is_(None))
        if filters.q:
            like = f"%{filters.q.strip()}%"
            conditions.append(
                or_(
                    UserAccount.username.ilike(like),
                    UserAccount.display_name.ilike(like),
                    UserAccount.email.ilike(like),
                )
            )
        if filters.status:
            conditions.append(UserAccount.status == filters.status)
        if filters.auth_source:
            conditions.append(UserAccount.auth_source == filters.auth_source)
        if filters.role_id:
            conditions.append(
                UserAccount.id.in_(
                    select(UserRole.user_id).where(UserRole.role_id == filters.role_id)
                )
            )
        total = (
            self.session.scalar(select(func.count()).select_from(UserAccount).where(*conditions))
            or 0
        )
        sort_key = (page.sort or "username").lstrip("-")
        column = _SORTABLE.get(sort_key, UserAccount.username)
        order = column.desc() if (page.sort or "").startswith("-") else column.asc()
        rows = self.session.scalars(
            select(UserAccount)
            .where(*conditions)
            .options(selectinload(UserAccount.role_links), selectinload(UserAccount.credential))
            .order_by(order, UserAccount.id)
            .offset(page.offset)
            .limit(page.page_size)
        ).all()
        return list(rows), total

    def get(self, user_id: uuid.UUID) -> UserAccount:
        return get_user(self.session, user_id)

    def sessions(self, user_id: uuid.UUID) -> list[UserSession]:
        get_user(self.session, user_id)
        return list(
            self.session.scalars(
                select(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                )
                .order_by(UserSession.last_seen_at.desc())
            )
        )

    # ------------------------------------------------------------------ alta y edición
    def create(self, data: NewUser, actor: Principal, request: RequestInfo) -> CreatedUser:
        username = normalize_username(data.username)
        if not USERNAME_PATTERN.match(username):
            msg = "El nombre de usuario no es válido."
            raise BusinessRuleError(
                msg,
                code="SX-IDN-USERNAME-INVALID",
                errors=[
                    FieldIssue(
                        "username",
                        "Use de 3 a 100 caracteres: letras minúsculas, números y . _ @ -",
                    )
                ],
            )
        if find_user(self.session, username) is not None:
            msg = f"Ya existe un usuario con el nombre «{username}»."
            raise ConflictError(msg, code="SX-IDN-USERNAME-TAKEN")
        roles = self._roles(data.role_ids)
        ensure_can_grant(actor.permissions, _permissions_of(roles))

        user = UserAccount(
            username=username,
            display_name=data.display_name.strip(),
            email=_clean_email(data.email),
            auth_source=data.auth_source.value,
            status=(
                UserStatus.ACTIVE.value
                if data.auth_source is AuthSource.LOCAL
                else UserStatus.INVITED.value
            ),
            is_break_glass=False,
            failed_attempts=0,
            must_change_password=data.auth_source is AuthSource.LOCAL,
            created_by=actor.username,
            updated_by=actor.username,
        )
        temporary: str | None = None
        if data.auth_source is AuthSource.LOCAL:
            temporary = generate_temporary_password()
            user.credential = LocalCredential(
                password_hash=self.tk.hashing.hash(temporary), password_history=[]
            )
        user.role_links = [
            UserRole(role=role, source=RoleSource.MANUAL.value, assigned_by=actor.username)
            for role in roles
        ]
        self.session.add(user)
        self.session.flush()
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="user.created",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                changes=diff(None, {**user_snapshot(user), "roles": sorted(r.code for r in roles)}),
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return CreatedUser(user=user, temporary_password=temporary)

    def update(
        self, user_id: uuid.UUID, changes: UserChanges, actor: Principal, request: RequestInfo
    ) -> UserAccount:
        user = self._editable(user_id)
        _check_version(user.version, changes.version)
        before = user_snapshot(user)
        if changes.display_name is not None:
            user.display_name = changes.display_name.strip()
        if changes.clear_email:
            user.email = None
        elif changes.email is not None:
            user.email = _clean_email(changes.email)
        delta = diff(before, user_snapshot(user))
        if not delta:
            return user
        _touch(user, actor)
        self._audit("user.updated", user, actor, request, changes=delta)
        self.session.commit()
        return user

    def set_roles(
        self, user_id: uuid.UUID, role_ids: list[uuid.UUID], actor: Principal, request: RequestInfo
    ) -> UserAccount:
        user = self._editable(user_id)
        ensure_not_self(actor.user_id, user.id, "cambiar los roles de")
        ensure_not_break_glass(user.is_break_glass, "cambiar los roles")
        current = {link.role.id: link.role for link in user.role_links}
        wanted = {role.id: role for role in self._roles(role_ids)}
        added = [wanted[i] for i in wanted.keys() - current.keys()]
        removed = [current[i] for i in current.keys() - wanted.keys()]
        if not added and not removed:
            return user
        # Quitar o poner un rol equivale a gestionar sus permisos: ambos exigen tenerlos.
        ensure_can_grant(actor.permissions, _permissions_of([*added, *removed]))
        if is_active_admin(user) and not any(r.code == "platform_admin" for r in wanted.values()):
            ensure_admins_remain(
                target_is_active_admin=True,
                other_active_admins=count_other_active_admins(self.session, user.id),
            )
        before = sorted(r.code for r in current.values())
        user.role_links = [link for link in user.role_links if link.role.id in wanted]
        user.role_links.extend(
            UserRole(role=role, source=RoleSource.MANUAL.value, assigned_by=actor.username)
            for role in added
        )
        _touch(user, actor)
        self.session.flush()
        self._audit(
            "user.roles_changed",
            user,
            actor,
            request,
            changes={"roles": {"before": before, "after": sorted(r.code for r in wanted.values())}},
        )
        self.session.commit()
        return user

    # ------------------------------------------------------------------ estado
    def _guard_status_change(self, user: UserAccount, actor: Principal, verb: str) -> None:
        ensure_not_self(actor.user_id, user.id, verb)
        ensure_not_break_glass(user.is_break_glass, verb)
        ensure_admins_remain(
            target_is_active_admin=is_active_admin(user),
            other_active_admins=count_other_active_admins(self.session, user.id),
        )

    def disable(
        self, user_id: uuid.UUID, actor: Principal, request: RequestInfo, reason: str | None
    ) -> UserAccount:
        user = self._editable(user_id)
        if user.status == UserStatus.DISABLED:
            return user
        self._guard_status_change(user, actor, "desactivar")
        now = utcnow()
        before = user.status
        user.status = UserStatus.DISABLED.value
        _touch(user, actor)
        sessions = revoke_user_sessions(self.session, user.id, reason="user_disabled", now=now)
        tokens = revoke_user_tokens(self.session, user.id, now=now)
        self._audit(
            "user.disabled",
            user,
            actor,
            request,
            changes={"status": {"before": before, "after": user.status}},
            reason=reason,
            details={"sessions_revoked": sessions, "api_access_revoked": tokens},
        )
        self.session.commit()
        return user

    def enable(self, user_id: uuid.UUID, actor: Principal, request: RequestInfo) -> UserAccount:
        user = self._editable(user_id)
        if user.status != UserStatus.DISABLED:
            msg = "Solo se puede reactivar un usuario desactivado."
            raise BusinessRuleError(msg, code="SX-IDN-NOT-DISABLED")
        user.status = (
            UserStatus.ACTIVE.value
            if user.auth_source == AuthSource.LOCAL or user.last_login_at is not None
            else UserStatus.INVITED.value
        )
        user.failed_attempts = 0
        user.locked_until = None
        _touch(user, actor)
        self._audit(
            "user.enabled",
            user,
            actor,
            request,
            changes={"status": {"before": UserStatus.DISABLED.value, "after": user.status}},
        )
        self.session.commit()
        return user

    def lock(
        self, user_id: uuid.UUID, actor: Principal, request: RequestInfo, reason: str | None
    ) -> UserAccount:
        user = self._editable(user_id)
        if user.status != UserStatus.ACTIVE:
            msg = "Solo se puede bloquear un usuario activo."
            raise BusinessRuleError(msg, code="SX-IDN-NOT-ACTIVE")
        self._guard_status_change(user, actor, "bloquear")
        user.status = UserStatus.LOCKED.value
        _touch(user, actor)
        sessions = revoke_user_sessions(self.session, user.id, reason="user_locked", now=utcnow())
        self._audit(
            "user.locked",
            user,
            actor,
            request,
            changes={"status": {"before": UserStatus.ACTIVE.value, "after": user.status}},
            reason=reason,
            details={"sessions_revoked": sessions},
        )
        self.session.commit()
        return user

    def unlock(self, user_id: uuid.UUID, actor: Principal, request: RequestInfo) -> UserAccount:
        """Quita el bloqueo manual y el temporal por intentos fallidos."""
        user = self._editable(user_id)
        was_locked = user.status == UserStatus.LOCKED
        temp_locked = user.locked_until is not None and user.locked_until > utcnow()
        if not was_locked and not temp_locked and user.failed_attempts == 0:
            return user
        changes: dict[str, Any] = {}
        if was_locked:
            user.status = UserStatus.ACTIVE.value
            changes["status"] = {"before": UserStatus.LOCKED.value, "after": user.status}
        if temp_locked:
            changes["locked_until"] = {
                "before": user.locked_until.isoformat() if user.locked_until else None,
                "after": None,
            }
        user.failed_attempts = 0
        user.locked_until = None
        _touch(user, actor)
        self._audit("user.unlocked", user, actor, request, changes=changes)
        self.session.commit()
        return user

    # ------------------------------------------------------------------ credenciales
    def reset_password(
        self, user_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> tuple[UserAccount, str]:
        user = self._editable(user_id)
        ensure_not_self(actor.user_id, user.id, "restablecer la contraseña de")
        if user.auth_source != AuthSource.LOCAL or user.credential is None:
            msg = "Solo los usuarios locales tienen contraseña en la plataforma."
            raise BusinessRuleError(msg, code="SX-AUTH-NOT-LOCAL")
        temporary = generate_temporary_password()
        reset_local_password(user, temporary, self.tk)
        sessions = revoke_user_sessions(
            self.session, user.id, reason="password_reset", now=utcnow()
        )
        self._audit(
            "user.password_reset", user, actor, request, details={"sessions_revoked": sessions}
        )
        self.session.commit()
        return user, temporary

    def reset_mfa(self, user_id: uuid.UUID, actor: Principal, request: RequestInfo) -> UserAccount:
        user = self._editable(user_id)
        ensure_not_self(actor.user_id, user.id, "restablecer el segundo factor de")
        credential = user.credential
        if credential is None or not (credential.totp_enabled or credential.totp_pending_secret):
            msg = "El usuario no tiene segundo factor configurado."
            raise BusinessRuleError(msg, code="SX-AUTH-MFA-NOT-ENABLED")
        credential.totp_secret = None
        credential.totp_pending_secret = None
        credential.totp_enabled = False
        credential.totp_last_step = None
        _touch(user, actor)
        sessions = revoke_user_sessions(self.session, user.id, reason="mfa_reset", now=utcnow())
        self._audit("user.mfa_reset", user, actor, request, details={"sessions_revoked": sessions})
        self.session.commit()
        return user

    def revoke_sessions(
        self,
        user_id: uuid.UUID,
        actor: Principal,
        request: RequestInfo,
        *,
        session_id: uuid.UUID | None = None,
    ) -> int:
        user = get_user(self.session, user_id)
        now = utcnow()
        if session_id is None:
            count = revoke_user_sessions(self.session, user.id, reason="revoked", now=now)
        else:
            row = self.session.get(UserSession, session_id)
            if row is None or row.user_id != user.id or row.revoked_at is not None:
                return 0
            row.revoked_at = now
            row.revoked_reason = "revoked"
            count = 1
        if count:
            self.tk.audit.record(
                self.session,
                AuditEntry(
                    action="session.revoked",
                    resource_type="user",
                    resource_id=str(user.id),
                    resource_name=user.username,
                    details={
                        "sessions_revoked": count,
                        "session_id": str(session_id) if session_id else None,
                    },
                ),
                actor=actor.actor,
                request=request,
            )
        self.session.commit()
        return count

    # ------------------------------------------------------------------ anonimización
    def anonymize(
        self, user_id: uuid.UUID, actor: Principal, request: RequestInfo, reason: str
    ) -> UserAccount:
        """Sustituye los datos personales; el registro se conserva para no romper la auditoría."""
        user = self._editable(user_id)
        ensure_not_self(actor.user_id, user.id, "anonimizar")
        ensure_not_break_glass(user.is_break_glass, "anonimizar")
        if user.status != UserStatus.DISABLED:
            msg = "Desactive el usuario antes de anonimizarlo."
            raise BusinessRuleError(msg, code="SX-IDN-NOT-DISABLED")
        now = utcnow()
        original = user.username
        user.username = f"anon-{user.id.hex[:12]}"
        user.display_name = "Usuario anonimizado"
        user.email = None
        user.external_id = None
        user.deleted_at = now
        user.credential = None
        user.role_links = []
        _touch(user, actor)
        revoke_user_sessions(self.session, user.id, reason="user_anonymized", now=now)
        revoke_user_tokens(self.session, user.id, now=now)
        self.session.flush()
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="user.anonymized",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                reason=reason,
                details={"previous_username_length": len(original)},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return user

    # ------------------------------------------------------------------ utilidades
    def _editable(self, user_id: uuid.UUID) -> UserAccount:
        user = get_user(self.session, user_id)
        if user.deleted_at is not None:
            msg = "El usuario está anonimizado y no admite cambios."
            raise BusinessRuleError(msg, code="SX-IDN-ANONYMIZED")
        return user

    def _roles(self, role_ids: list[uuid.UUID]) -> list[Role]:
        return [get_role(self.session, role_id) for role_id in dict.fromkeys(role_ids)]

    def _audit(
        self,
        action: str,
        user: UserAccount,
        actor: Principal,
        request: RequestInfo,
        *,
        changes: dict[str, Any] | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                changes=changes,
                reason=reason,
                details=details or {},
            ),
            actor=actor.actor,
            request=request,
        )


def reset_local_password(user: UserAccount, temporary: str, toolkit: IdentityToolkit) -> None:
    """Asigna una contraseña temporal que el usuario deberá cambiar al entrar."""
    if user.credential is None:
        user.credential = LocalCredential(password_hash=toolkit.hashing.hash(temporary))
        user.credential.password_history = []
    else:
        credential = user.credential
        credential.password_history = [
            credential.password_hash,
            *credential.password_history,
        ][: toolkit.settings.password_history_size]
        credential.password_hash = toolkit.hashing.hash(temporary)
        credential.password_changed_at = utcnow()
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None


def _touch(user: UserAccount, actor: Principal) -> None:
    """Marca el cambio (y fuerza el UPDATE que incrementa ``version`` aunque solo cambien hijos)."""
    user.updated_by = actor.username
    user.updated_at = utcnow()


def _permissions_of(roles: list[Role]) -> set[str]:
    return {code for role in roles for code in role.permission_codes}


def _clean_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def _check_version(current: int, expected: int | None) -> None:
    if expected is not None and expected != current:
        msg = "El registro fue modificado por otra persona; recargue y vuelva a intentarlo."
        raise ConflictError(msg, code="SX-GEN-STALE")
