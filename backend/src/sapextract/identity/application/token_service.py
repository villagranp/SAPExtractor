"""Tokens API para cuentas de servicio (``Authorization: Bearer sxp_...``)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, FieldIssue, NotFoundError
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain.model import AuthMethod, Principal, UserStatus
from sapextract.identity.domain.permissions import PERMISSION_CODES
from sapextract.identity.domain.rules import ensure_can_grant
from sapextract.identity.infrastructure.models import ApiToken
from sapextract.identity.infrastructure.queries import effective_permissions, get_user
from sapextract.identity.infrastructure.security import API_TOKEN_PREFIX, hash_token, new_token

LAST_USED_RESOLUTION = timedelta(minutes=5)
MAX_TOKEN_DAYS = 365


@dataclass(frozen=True, kw_only=True)
class NewApiToken:
    name: str
    user_id: uuid.UUID | None
    scopes: list[str]
    expires_in_days: int


@dataclass(frozen=True, kw_only=True)
class CreatedApiToken:
    token: ApiToken
    secret: str


class ApiTokenService:
    def __init__(self, session: Session, toolkit: IdentityToolkit) -> None:
        self.session = session
        self.tk = toolkit

    def all(self, user_id: uuid.UUID | None = None) -> list[ApiToken]:
        stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
        if user_id is not None:
            stmt = stmt.where(ApiToken.user_id == user_id)
        return list(self.session.scalars(stmt))

    def create(self, data: NewApiToken, actor: Principal, request: RequestInfo) -> CreatedApiToken:
        owner = get_user(self.session, data.user_id or actor.user_id)
        if owner.status != UserStatus.ACTIVE or owner.deleted_at is not None:
            msg = "El usuario del token debe estar activo."
            raise BusinessRuleError(msg, code="SX-IDN-TOKEN-OWNER-INACTIVE")
        if not 1 <= data.expires_in_days <= MAX_TOKEN_DAYS:
            msg = f"La caducidad debe estar entre 1 y {MAX_TOKEN_DAYS} días."
            raise BusinessRuleError(
                msg, code="SX-IDN-TOKEN-EXPIRY", errors=[FieldIssue("expires_in_days", msg)]
            )
        scopes = set(data.scopes)
        unknown = sorted(scopes - PERMISSION_CODES)
        if unknown or not scopes:
            msg = (
                "Permisos desconocidos: " + ", ".join(unknown)
                if unknown
                else "Indique al menos un permiso."
            )
            raise BusinessRuleError(
                msg, code="SX-IDN-TOKEN-SCOPES", errors=[FieldIssue("scopes", msg)]
            )
        ensure_can_grant(actor.permissions, scopes)
        missing = sorted(scopes - effective_permissions(self.session, owner.id))
        if missing:
            msg = "El usuario del token no tiene estos permisos: " + ", ".join(missing)
            raise BusinessRuleError(
                msg, code="SX-IDN-TOKEN-SCOPES", errors=[FieldIssue("scopes", msg)]
            )
        secret = new_token(API_TOKEN_PREFIX)
        token = ApiToken(
            user_id=owner.id,
            name=data.name.strip(),
            token_prefix=secret[:12],
            token_hash=hash_token(secret),
            scopes=sorted(scopes),
            created_by=actor.username,
            expires_at=utcnow() + timedelta(days=data.expires_in_days),
        )
        self.session.add(token)
        self.session.flush()
        self._audit("api_token.created", token, actor, request)
        self.session.commit()
        return CreatedApiToken(token=token, secret=secret)

    def revoke(self, token_id: uuid.UUID, actor: Principal, request: RequestInfo) -> ApiToken:
        token = self.session.get(ApiToken, token_id)
        if token is None:
            msg = "El token no existe."
            raise NotFoundError(msg, code="SX-IDN-TOKEN-NOT-FOUND")
        if token.revoked_at is None:
            token.revoked_at = utcnow()
            self._audit("api_token.revoked", token, actor, request)
            self.session.commit()
        return token

    def resolve(self, secret: str) -> Principal | None:
        """Principal de un token válido. Permisos = alcance del token ∩ permisos actuales."""
        if not secret.startswith(API_TOKEN_PREFIX):
            return None
        token = self.session.scalar(
            select(ApiToken).where(ApiToken.token_hash == hash_token(secret))
        )
        now = utcnow()
        if token is None or token.revoked_at is not None or token.expires_at <= now:
            return None
        user = token.user
        if user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            return None
        if token.last_used_at is None or now - token.last_used_at >= LAST_USED_RESOLUTION:
            token.last_used_at = now
            self.session.commit()
        return Principal(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            permissions=frozenset(token.scopes) & effective_permissions(self.session, user.id),
            auth_method=AuthMethod.API_TOKEN,
            token_id=token.id,
            token_name=token.name,
        )

    def _audit(self, action: str, token: ApiToken, actor: Principal, request: RequestInfo) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="api_token",
                resource_id=str(token.id),
                resource_name=token.name,
                details={
                    "owner": token.user.username,
                    "prefix": token.token_prefix,
                    "scopes": sorted(token.scopes),
                    "expires_at": token.expires_at.isoformat(),
                },
            ),
            actor=actor.actor,
            request=request,
        )
