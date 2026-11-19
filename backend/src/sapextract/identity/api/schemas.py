"""Contratos HTTP del módulo de identidad."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from sapextract.core.clock import utcnow
from sapextract.identity.domain.model import AuthMethod, AuthSource, SessionStage, UserStatus
from sapextract.identity.domain.permissions import MODULES
from sapextract.identity.infrastructure.models import (
    ApiToken,
    Permission,
    Role,
    UserAccount,
    UserSession,
)

EMAIL_PATTERN = r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ------------------------------------------------------------------ autenticación
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class SessionStatusResponse(BaseModel):
    """Paso en el que queda la sesión tras la operación."""

    stage: SessionStage


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10, examples=["123456"])


class PasswordChangeRequest(BaseModel):
    current_password: str | None = Field(
        None, max_length=256, description="Obligatoria salvo en el cambio forzado tras el login"
    )
    new_password: str = Field(min_length=1, max_length=256)


class MfaEnrollmentResponse(BaseModel):
    secret: str = Field(description="Clave para introducir a mano en la app de autenticación")
    otpauth_uri: str
    qr_svg_data_uri: str = Field(description="Código QR (data URI SVG) para escanear")


# ------------------------------------------------------------------ roles y permisos
class RoleRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str

    @classmethod
    def of(cls, role: Role) -> RoleRef:
        return cls(id=role.id, code=role.code, name=role.name)


class PermissionResponse(BaseModel):
    code: str
    module: str
    module_name: str
    description: str

    @classmethod
    def of(cls, permission: Permission) -> PermissionResponse:
        return cls(
            code=permission.code,
            module=permission.module,
            module_name=MODULES.get(permission.module, permission.module),
            description=permission.description,
        )


class RoleResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    user_count: int
    version: int
    updated_at: datetime

    @classmethod
    def of(cls, role: Role, user_count: int) -> RoleResponse:
        return cls(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=role.permission_codes,
            user_count=user_count,
            version=role.version,
            updated_at=role.updated_at,
        )


class RoleCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=50, examples=["operador_fi"])
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: list[str] = Field(min_length=1)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: list[str] | None = None
    version: int | None = Field(None, description="Versión leída; si no coincide responde 409")


# ------------------------------------------------------------------ usuarios
class UserRef(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str


class UserSummary(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    auth_source: AuthSource
    status: UserStatus
    is_break_glass: bool
    mfa_enabled: bool
    temporarily_locked: bool
    roles: list[RoleRef]
    last_login_at: datetime | None
    created_at: datetime

    @classmethod
    def fields_of(cls, user: UserAccount) -> dict[str, object]:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "auth_source": AuthSource(user.auth_source),
            "status": UserStatus(user.status),
            "is_break_glass": user.is_break_glass,
            "mfa_enabled": bool(user.credential and user.credential.totp_enabled),
            "temporarily_locked": bool(user.locked_until and user.locked_until > utcnow()),
            "roles": [RoleRef.of(role) for role in user.roles],
            "last_login_at": user.last_login_at,
            "created_at": user.created_at,
        }

    @classmethod
    def of(cls, user: UserAccount) -> UserSummary:
        return cls.model_validate(cls.fields_of(user))


class UserDetail(UserSummary):
    must_change_password: bool
    failed_attempts: int
    locked_until: datetime | None
    anonymized_at: datetime | None
    created_by: str | None
    updated_at: datetime
    updated_by: str | None
    version: int

    @classmethod
    def of(cls, user: UserAccount) -> UserDetail:
        return cls.model_validate(
            {
                **UserSummary.fields_of(user),
                "must_change_password": user.must_change_password,
                "failed_attempts": user.failed_attempts,
                "locked_until": user.locked_until,
                "anonymized_at": user.deleted_at,
                "created_by": user.created_by,
                "updated_at": user.updated_at,
                "updated_by": user.updated_by,
                "version": user.version,
            }
        )


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100, examples=["jperez"])
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(None, max_length=320, pattern=EMAIL_PATTERN)
    auth_source: AuthSource = AuthSource.LOCAL
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class UserCreatedResponse(BaseModel):
    user: UserDetail
    temporary_password: str | None = Field(
        description="Contraseña temporal (solo usuarios locales). Se muestra UNA vez."
    )


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    email: str | None = Field(
        None, max_length=320, pattern=EMAIL_PATTERN, description="Vacío o null para quitarlo"
    )
    version: int | None = Field(None, description="Versión leída; si no coincide responde 409")


class UserRolesRequest(BaseModel):
    role_ids: list[uuid.UUID]


class ReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class AnonymizeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500, description="Motivo o petición formal")


class TemporaryPasswordResponse(BaseModel):
    temporary_password: str = Field(description="Se muestra UNA vez")


class RevokedResponse(BaseModel):
    revoked: int


class SessionResponse(BaseModel):
    id: uuid.UUID
    auth_method: AuthMethod
    stage: SessionStage
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool

    @classmethod
    def of(cls, row: UserSession, current_id: uuid.UUID | None) -> SessionResponse:
        return cls(
            id=row.id,
            auth_method=AuthMethod(row.auth_method),
            stage=SessionStage(row.stage),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            ip=row.ip,
            user_agent=row.user_agent,
            current=row.id == current_id,
        )


class MeResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    auth_source: AuthSource
    auth_method: AuthMethod
    stage: SessionStage
    is_break_glass: bool
    mfa_enabled: bool
    roles: list[RoleRef]
    permissions: list[str] = Field(
        description="Solo para mostrar u ocultar opciones; la autorización la hace el backend"
    )


# ------------------------------------------------------------------ tokens API
class ApiTokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    user: UserRef
    status: Literal["active", "expired", "revoked"]
    created_at: datetime
    created_by: str | None
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def of(cls, token: ApiToken) -> ApiTokenResponse:
        status: Literal["active", "expired", "revoked"] = (
            "revoked"
            if token.revoked_at is not None
            else "expired"
            if token.expires_at <= utcnow()
            else "active"
        )
        return cls(
            id=token.id,
            name=token.name,
            prefix=token.token_prefix,
            scopes=sorted(token.scopes),
            user=UserRef(
                id=token.user.id,
                username=token.user.username,
                display_name=token.user.display_name,
            ),
            status=status,
            created_at=token.created_at,
            created_by=token.created_by,
            expires_at=token.expires_at,
            last_used_at=token.last_used_at,
            revoked_at=token.revoked_at,
        )


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Disparo desde orquestador externo"])
    user_id: uuid.UUID | None = Field(None, description="Dueño del token (por defecto, usted)")
    scopes: list[str] = Field(min_length=1, examples=[["pipeline:read", "pipeline:run"]])
    expires_in_days: int = Field(90, ge=1, le=365)


class ApiTokenCreatedResponse(BaseModel):
    token: ApiTokenResponse
    secret: str = Field(
        description="Valor del token. Se muestra UNA vez; guárdelo en un lugar seguro"
    )
