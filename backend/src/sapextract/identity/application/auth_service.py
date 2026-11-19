"""Autenticación local: login, segundo factor, cambio de contraseña y sesiones de servidor."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import Actor, ActorType, AuditEntry, Outcome, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.config import LocalLoginPolicy
from sapextract.core.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    FieldIssue,
)
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain import totp
from sapextract.identity.domain.model import (
    AuthMethod,
    AuthSource,
    Principal,
    SessionStage,
    UserStatus,
    normalize_username,
)
from sapextract.identity.domain.permissions import PLATFORM_ADMIN
from sapextract.identity.domain.rules import lockout_until
from sapextract.identity.infrastructure.models import (
    LocalCredential,
    UserAccount,
    UserSession,
)
from sapextract.identity.infrastructure.queries import effective_permissions, revoke_user_sessions
from sapextract.identity.infrastructure.security import hash_token, new_token

INVALID_CREDENTIALS = (
    "Usuario o contraseña incorrectos, o la cuenta está bloqueada. "
    "Tras varios intentos fallidos la cuenta se bloquea temporalmente."
)
# Frecuencia máxima con la que se actualiza ``last_seen_at`` (evita una escritura por petición).
LAST_SEEN_RESOLUTION = timedelta(seconds=60)


@dataclass(frozen=True, kw_only=True)
class IssuedSession:
    """Tokens recién emitidos. Solo existen en memoria: en BD se guarda su hash."""

    token: str
    csrf_token: str
    stage: SessionStage
    session_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class ResolvedSession:
    principal: Principal
    csrf_hash: str

    def csrf_matches(self, csrf_token: str | None) -> bool:
        return bool(csrf_token) and hmac.compare_digest(
            hash_token(csrf_token or ""), self.csrf_hash
        )


@dataclass(frozen=True, kw_only=True)
class MfaEnrollment:
    secret: str
    otpauth_uri: str


class AuthService:
    def __init__(self, session: Session, toolkit: IdentityToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.settings = toolkit.settings

    # ================================================================== login
    def login(
        self,
        username: str,
        password: str,
        request: RequestInfo,
        *,
        previous_token: str | None = None,
    ) -> IssuedSession:
        uname = normalize_username(username)[:100]
        now = utcnow()
        user = self.session.scalar(
            select(UserAccount).where(
                UserAccount.username == uname, UserAccount.deleted_at.is_(None)
            )
        )
        credential = (
            user.credential if user is not None and user.auth_source == AuthSource.LOCAL else None
        )
        if user is None or credential is None:
            self.tk.hashing.dummy_verify(password)
            self._fail_login(user, uname, "unknown_user", request, count_attempt=False)

        blocked = self._block_reason(user, now)
        if blocked is not None:
            self.tk.hashing.dummy_verify(password)
            self._fail_login(user, uname, blocked, request, count_attempt=False)

        if not self.tk.hashing.verify(credential.password_hash, password):
            self._fail_login(user, uname, "bad_password", request, count_attempt=True)

        if self.tk.hashing.needs_rehash(credential.password_hash):
            credential.password_hash = self.tk.hashing.hash(password)
        user.failed_attempts = 0
        user.locked_until = None
        if previous_token:
            self._revoke_by_token(previous_token, reason="replaced", now=now)

        stage = SessionStage.MFA if credential.totp_enabled else self._next_stage(user, credential)
        if stage is SessionStage.ACTIVE:
            user.last_login_at = now
        issued = self._create_session(user, stage, request, now=now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="auth.login_succeeded",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                details={"method": AuthMethod.LOCAL.value, "stage": stage.value},
            ),
            actor=_user_actor(user),
            request=request,
        )
        self.session.commit()
        return issued

    def _block_reason(self, user: UserAccount, now: datetime) -> str | None:
        if user.status != UserStatus.ACTIVE:
            return f"status_{user.status}"
        if user.locked_until is not None and user.locked_until > now:
            return "temporarily_locked"
        if (
            self.settings.local_login_enabled_for is LocalLoginPolicy.BREAK_GLASS_ONLY
            and not user.is_break_glass
        ):
            return "local_login_disabled"
        return None

    def _register_failure(self, user: UserAccount, request: RequestInfo, now: datetime) -> bool:
        """Suma un intento fallido; devuelve True si con él la cuenta queda bloqueada."""
        user.failed_attempts += 1
        until = lockout_until(
            failed_attempts=user.failed_attempts,
            max_attempts=self.settings.login_max_attempts,
            base_minutes=self.settings.login_lockout_base_minutes,
            max_minutes=self.settings.login_lockout_max_minutes,
            now=now,
        )
        if until is None:
            return False
        user.locked_until = until
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="user.locked",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                reason="Bloqueo temporal por intentos fallidos",
                details={"until": until.isoformat(), "failed_attempts": user.failed_attempts},
            ),
            actor=Actor.system(),
            request=request,
        )
        return True

    def _fail_login(
        self,
        user: UserAccount | None,
        username: str,
        reason: str,
        request: RequestInfo,
        *,
        count_attempt: bool,
    ) -> NoReturn:
        """Registra el fallo (en su propia transacción) y responde siempre lo mismo."""
        now = utcnow()
        if user is not None and count_attempt:
            self._register_failure(user, request, now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="auth.login_failed",
                outcome=Outcome.FAILURE,
                resource_type="user",
                resource_id=str(user.id) if user else None,
                resource_name=username,
                details={"reason": reason, "method": AuthMethod.LOCAL.value},
            ),
            actor=Actor(
                type=ActorType.USER, id=str(user.id) if user else None, name=username or "-"
            ),
            request=request,
        )
        self.session.commit()
        raise AuthenticationError(INVALID_CREDENTIALS, code="SX-AUTH-INVALID-CREDENTIALS")

    # ================================================================== sesiones
    def _mfa_required(self, user: UserAccount) -> bool:
        return self.settings.mfa_required and (
            user.is_break_glass or any(link.role.code == PLATFORM_ADMIN for link in user.role_links)
        )

    def _next_stage(self, user: UserAccount, credential: LocalCredential) -> SessionStage:
        """Paso siguiente tras validar contraseña (y TOTP si lo tiene)."""
        if user.must_change_password:
            return SessionStage.PASSWORD_CHANGE
        if self._mfa_required(user) and not credential.totp_enabled:
            return SessionStage.MFA_SETUP
        return SessionStage.ACTIVE

    def _lifetime(self, stage: SessionStage) -> timedelta:
        if stage is SessionStage.ACTIVE:
            return timedelta(hours=self.settings.session_absolute_hours)
        return timedelta(minutes=self.settings.session_pending_minutes)

    def _create_session(
        self, user: UserAccount, stage: SessionStage, request: RequestInfo, *, now: datetime
    ) -> IssuedSession:
        token, csrf = new_token(), new_token()
        row = UserSession(
            token_hash=hash_token(token),
            csrf_hash=hash_token(csrf),
            user_id=user.id,
            stage=stage.value,
            auth_method=AuthMethod.LOCAL.value,
            created_at=now,
            last_seen_at=now,
            expires_at=now + self._lifetime(stage),
            ip=request.ip,
            user_agent=(request.user_agent or "")[:300] or None,
        )
        self.session.add(row)
        self.session.flush()
        return IssuedSession(
            token=token, csrf_token=csrf, stage=stage, session_id=row.id, expires_at=row.expires_at
        )

    def _advance(self, row: UserSession, stage: SessionStage, *, now: datetime) -> IssuedSession:
        """Cambia de paso rotando el identificador de sesión (evita la fijación de sesión)."""
        token, csrf = new_token(), new_token()
        row.token_hash = hash_token(token)
        row.csrf_hash = hash_token(csrf)
        row.stage = stage.value
        row.last_seen_at = now
        if stage is SessionStage.ACTIVE:
            row.expires_at = now + self._lifetime(stage)
            row.user.last_login_at = now
        return IssuedSession(
            token=token, csrf_token=csrf, stage=stage, session_id=row.id, expires_at=row.expires_at
        )

    def _revoke_by_token(self, token: str, *, reason: str, now: datetime) -> None:
        row = self.session.scalar(
            select(UserSession).where(
                UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None)
            )
        )
        if row is not None:
            row.revoked_at = now
            row.revoked_reason = reason

    def resolve_session(self, token: str, request: RequestInfo) -> ResolvedSession | None:
        """Valida la cookie de sesión. Devuelve ``None`` si no es válida o expiró."""
        now = utcnow()
        row = self.session.scalar(
            select(UserSession).where(
                UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None)
            )
        )
        if row is None:
            return None
        user = row.user
        idle_deadline = row.last_seen_at + timedelta(minutes=self.settings.session_idle_minutes)
        expired = now >= row.expires_at or (
            row.stage == SessionStage.ACTIVE and now >= idle_deadline
        )
        if expired or user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            row.revoked_at = now
            row.revoked_reason = "expired" if expired else "user_inactive"
            if expired:
                self.tk.audit.record(
                    self.session,
                    AuditEntry(
                        action="auth.session_expired",
                        resource_type="session",
                        resource_id=str(row.id),
                        details={"stage": row.stage},
                    ),
                    actor=_user_actor(user),
                    request=request,
                )
            self.session.commit()
            return None
        if now - row.last_seen_at >= LAST_SEEN_RESOLUTION:
            row.last_seen_at = now
            self.session.commit()
        return ResolvedSession(
            principal=Principal(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                permissions=effective_permissions(self.session, user.id),
                auth_method=AuthMethod(row.auth_method),
                stage=SessionStage(row.stage),
                session_id=row.id,
            ),
            csrf_hash=row.csrf_hash,
        )

    def logout(self, principal: Principal, request: RequestInfo) -> None:
        if principal.session_id is None:
            return
        row = self.session.get(UserSession, principal.session_id)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = utcnow()
        row.revoked_reason = "logout"
        self.tk.audit.record(
            self.session,
            AuditEntry(action="auth.logout", resource_type="session", resource_id=str(row.id)),
            actor=principal.actor,
            request=request,
        )
        self.session.commit()

    # ================================================================== segundo factor
    def _session_row(self, principal: Principal) -> UserSession:
        row = self.session.get(UserSession, principal.session_id) if principal.session_id else None
        if row is None:
            msg = "Esta operación requiere una sesión interactiva."
            raise AuthenticationError(msg, code="SX-AUTH-SESSION-REQUIRED")
        return row

    def _local_credential(self, user: UserAccount) -> LocalCredential:
        if user.credential is None:
            msg = "El usuario no tiene credenciales locales (se autentica con Entra ID)."
            raise BusinessRuleError(msg, code="SX-AUTH-NOT-LOCAL")
        return user.credential

    def verify_mfa(self, principal: Principal, code: str, request: RequestInfo) -> IssuedSession:
        row = self._session_row(principal)
        user = row.user
        credential = self._local_credential(user)
        now = utcnow()
        cipher = self.tk.require_cipher()
        secret = cipher.decrypt(credential.totp_secret or "", aad=_totp_aad(user.id)).decode()
        step = totp.match_step(
            secret, code, now.timestamp(), last_used_step=credential.totp_last_step
        )
        if step is None:
            locked = self._register_failure(user, request, now)
            if locked:
                row.revoked_at = now
                row.revoked_reason = "locked"
            self.tk.audit.record(
                self.session,
                AuditEntry(
                    action="auth.mfa_failed",
                    outcome=Outcome.FAILURE,
                    resource_type="user",
                    resource_id=str(user.id),
                    resource_name=user.username,
                ),
                actor=_user_actor(user),
                request=request,
            )
            self.session.commit()
            msg = "El código no es válido o ya fue utilizado."
            raise AuthenticationError(
                msg,
                code="SX-AUTH-MFA-INVALID",
                errors=[FieldIssue("code", msg)],
            )
        credential.totp_last_step = step
        user.failed_attempts = 0
        issued = self._advance(row, self._next_stage(user, credential), now=now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="auth.mfa_verified",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
            ),
            actor=_user_actor(user),
            request=request,
        )
        self.session.commit()
        return issued

    def start_mfa_enrollment(self, principal: Principal) -> MfaEnrollment:
        row = self._session_row(principal)
        user = row.user
        credential = self._local_credential(user)
        if credential.totp_enabled:
            msg = "El segundo factor ya está configurado; un administrador puede restablecerlo."
            raise ConflictError(msg, code="SX-AUTH-MFA-ALREADY-ENABLED")
        cipher = self.tk.require_cipher()
        secret = totp.generate_secret()
        credential.totp_pending_secret = cipher.encrypt(secret.encode(), aad=_totp_aad(user.id))
        self.session.commit()
        return MfaEnrollment(
            secret=secret,
            otpauth_uri=totp.provisioning_uri(
                secret, account=user.username, issuer=self.settings.app_name
            ),
        )

    def confirm_mfa_enrollment(
        self, principal: Principal, code: str, request: RequestInfo
    ) -> IssuedSession | None:
        row = self._session_row(principal)
        user = row.user
        credential = self._local_credential(user)
        if credential.totp_pending_secret is None:
            msg = "Primero inicie la configuración del segundo factor."
            raise BusinessRuleError(msg, code="SX-AUTH-MFA-NOT-STARTED")
        cipher = self.tk.require_cipher()
        secret = cipher.decrypt(credential.totp_pending_secret, aad=_totp_aad(user.id)).decode()
        now = utcnow()
        step = totp.match_step(secret, code, now.timestamp(), last_used_step=None)
        if step is None:
            msg = "El código no es válido. Compruebe la hora del teléfono e inténtelo de nuevo."
            raise BusinessRuleError(
                msg, code="SX-AUTH-MFA-INVALID", errors=[FieldIssue("code", msg)]
            )
        credential.totp_secret = credential.totp_pending_secret
        credential.totp_pending_secret = None
        credential.totp_enabled = True
        credential.totp_last_step = step
        issued = None
        if principal.stage is SessionStage.MFA_SETUP:
            issued = self._advance(row, self._next_stage(user, credential), now=now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="auth.mfa_enabled",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
            ),
            actor=_user_actor(user),
            request=request,
        )
        self.session.commit()
        return issued

    # ================================================================== contraseña
    def change_password(
        self,
        principal: Principal,
        *,
        current_password: str | None,
        new_password: str,
        request: RequestInfo,
    ) -> IssuedSession:
        row = self._session_row(principal)
        user = row.user
        credential = self._local_credential(user)
        forced = principal.stage is SessionStage.PASSWORD_CHANGE
        if not forced and (
            current_password is None
            or not self.tk.hashing.verify(credential.password_hash, current_password)
        ):
            msg = "La contraseña actual no es correcta."
            raise BusinessRuleError(
                msg,
                code="SX-AUTH-WRONG-PASSWORD",
                errors=[FieldIssue("current_password", msg)],
            )
        self.validate_new_password(user, credential, new_password)

        now = utcnow()
        history_size = self.settings.password_history_size
        credential.password_history = [credential.password_hash, *credential.password_history][
            :history_size
        ]
        credential.password_hash = self.tk.hashing.hash(new_password)
        credential.password_changed_at = now
        user.must_change_password = False
        revoked = revoke_user_sessions(
            self.session, user.id, reason="password_changed", now=now, keep_session_id=row.id
        )
        next_stage = self._next_stage(user, credential) if forced else SessionStage.ACTIVE
        issued = self._advance(row, next_stage, now=now)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="auth.password_changed",
                resource_type="user",
                resource_id=str(user.id),
                resource_name=user.username,
                details={"forced": forced, "other_sessions_revoked": revoked},
            ),
            actor=_user_actor(user),
            request=request,
        )
        self.session.commit()
        return issued

    def validate_new_password(
        self, user: UserAccount, credential: LocalCredential, new_password: str
    ) -> None:
        problems = self.tk.policy.violations(new_password, username=user.username)
        previous = [credential.password_hash, *credential.password_history][
            : max(self.settings.password_history_size, 1)
        ]
        if any(self.tk.hashing.verify(h, new_password) for h in previous):
            problems.append(
                f"No puede reutilizar ninguna de sus últimas "
                f"{max(self.settings.password_history_size, 1)} contraseñas."
            )
        if problems:
            msg = "La nueva contraseña no cumple la política."
            raise BusinessRuleError(
                msg,
                code="SX-AUTH-PASSWORD-POLICY",
                errors=[FieldIssue("new_password", p) for p in problems],
            )


def _user_actor(user: UserAccount) -> Actor:
    return Actor(type=ActorType.USER, id=str(user.id), name=user.username)


def _totp_aad(user_id: uuid.UUID) -> bytes:
    return f"totp:{user_id}".encode()
