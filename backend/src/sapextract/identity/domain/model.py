"""Conceptos del dominio de identidad."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

from sapextract.audit.domain.model import Actor, ActorType


class UserStatus(StrEnum):
    INVITED = "invited"  # pre-registrado (Entra ID), aún sin primer acceso
    ACTIVE = "active"
    LOCKED = "locked"  # bloqueo manual
    DISABLED = "disabled"


class AuthSource(StrEnum):
    LOCAL = "local"
    ENTRA = "entra"


class SessionStage(StrEnum):
    """Paso en el que está una sesión. Solo ``active`` da acceso a la API."""

    MFA = "mfa"  # falta el código TOTP
    # Paso de cambio de contraseña temporal (falso positivo de los analizadores: no es un secreto).
    PASSWORD_CHANGE = "password_change"  # noqa: S105  # nosec B105
    MFA_SETUP = "mfa_setup"  # MFA obligatorio y aún no configurado
    ACTIVE = "active"


class AuthMethod(StrEnum):
    LOCAL = "local"
    ENTRA = "entra"
    # Falso positivo de los analizadores: no es un secreto.
    API_TOKEN = "api_token"  # noqa: S105  # nosec B105


class RoleSource(StrEnum):
    MANUAL = "manual"
    ENTRA_GROUP = "entra_group"


USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._@\-]{2,99}$")
ROLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


def normalize_username(value: str) -> str:
    return value.strip().lower()


@dataclass(frozen=True, kw_only=True)
class Principal:
    """Identidad autenticada de una petición, con sus permisos efectivos."""

    user_id: uuid.UUID
    username: str
    display_name: str
    permissions: frozenset[str]
    auth_method: AuthMethod
    stage: SessionStage = SessionStage.ACTIVE
    session_id: uuid.UUID | None = None
    token_id: uuid.UUID | None = None
    token_name: str | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def actor(self) -> Actor:
        if self.auth_method is AuthMethod.API_TOKEN:
            return Actor(
                type=ActorType.API_TOKEN,
                id=str(self.user_id),
                name=f"{self.username} (token {self.token_name})",
            )
        return Actor(type=ActorType.USER, id=str(self.user_id), name=self.username)
