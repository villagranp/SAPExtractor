"""Modelo de dominio de la auditoría: actores, categorías, resultados y catálogo de acciones."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActorType(StrEnum):
    USER = "user"
    # Falso positivo de los analizadores: no es un secreto.
    API_TOKEN = "api_token"  # noqa: S105  # nosec B105
    SYSTEM = "system"


class Category(StrEnum):
    AUTH = "auth"
    IDENTITY = "identity"
    CONFIG = "config"
    EXECUTION = "execution"
    SECURITY = "security"
    SYSTEM = "system"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


# Catálogo de acciones (código estable → categoría). Ver docs/plan/03-auditoria-y-logs.md.
# Los módulos añaden aquí sus acciones al implementarse.
ACTIONS: dict[str, Category] = {
    # auth
    "auth.login_succeeded": Category.AUTH,
    "auth.login_failed": Category.AUTH,
    "auth.mfa_verified": Category.AUTH,
    "auth.mfa_failed": Category.AUTH,
    "auth.logout": Category.AUTH,
    "auth.mfa_enabled": Category.AUTH,
    "auth.password_changed": Category.AUTH,
    "auth.session_expired": Category.AUTH,
    "auth.access_denied": Category.AUTH,
    # identity
    "user.created": Category.IDENTITY,
    "user.updated": Category.IDENTITY,
    "user.roles_changed": Category.IDENTITY,
    "user.locked": Category.IDENTITY,
    "user.unlocked": Category.IDENTITY,
    "user.disabled": Category.IDENTITY,
    "user.enabled": Category.IDENTITY,
    "user.anonymized": Category.IDENTITY,
    "user.password_reset": Category.IDENTITY,
    "user.mfa_reset": Category.IDENTITY,
    "role.created": Category.IDENTITY,
    "role.updated": Category.IDENTITY,
    "role.deleted": Category.IDENTITY,
    "api_token.created": Category.IDENTITY,
    "api_token.revoked": Category.IDENTITY,
    "session.revoked": Category.IDENTITY,
    # Entra ID (plan 02 §4): quién puede entrar y con qué rol.
    "idp.updated": Category.IDENTITY,
    "idp.credentials_rotated": Category.SECURITY,
    "idp.tested": Category.CONFIG,
    "idp.enabled": Category.SECURITY,
    "idp.disabled": Category.SECURITY,
    "idp.group_mappings_changed": Category.SECURITY,
    # config: conexiones SAP (plan 04)
    "sap_connection.created": Category.CONFIG,
    "sap_connection.updated": Category.CONFIG,
    "sap_connection.deleted": Category.CONFIG,
    "sap_connection.tested": Category.CONFIG,
    "sap_connection.credentials_rotated": Category.SECURITY,
    "sap_connection.activated": Category.CONFIG,
    "sap_connection.deactivated": Category.CONFIG,
    # sources (plan 05)
    "source.created": Category.CONFIG,
    "source.updated": Category.CONFIG,
    "source.deleted": Category.CONFIG,
    "source.activated": Category.CONFIG,
    "source.deprecated": Category.CONFIG,
    "source.schema_refreshed": Category.CONFIG,
    # Lee datos de SAP con el usuario técnico: queda registrado quién vio qué origen.
    "source.previewed": Category.EXECUTION,
    # config: destinos (plan 07)
    "destination.created": Category.CONFIG,
    "destination.updated": Category.CONFIG,
    "destination.deleted": Category.CONFIG,
    "destination.tested": Category.CONFIG,
    "destination.credentials_rotated": Category.SECURITY,
    "destination.activated": Category.CONFIG,
    "destination.deactivated": Category.CONFIG,
    # security
    "audit.exported": Category.SECURITY,
    "audit.verified": Category.SECURITY,
    "audit.verification_failed": Category.SECURITY,
}


def category_of(action: str) -> Category:
    try:
        return ACTIONS[action]
    except KeyError:
        msg = f"Acción de auditoría no registrada en el catálogo: {action}"
        raise ValueError(msg) from None


@dataclass(frozen=True, kw_only=True)
class Actor:
    """Quién realiza la acción, con el nombre visible en ese momento."""

    type: ActorType
    id: str | None
    name: str

    @classmethod
    def system(cls, name: str = "system") -> Actor:
        return cls(type=ActorType.SYSTEM, id=None, name=name)


@dataclass(frozen=True, kw_only=True)
class RequestInfo:
    """Desde dónde se realiza la acción."""

    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    def as_context(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "ip": self.ip,
                "user_agent": self.user_agent,
                "request_id": self.request_id,
            }.items()
            if v is not None
        }


@dataclass(frozen=True, kw_only=True)
class AuditEntry:
    """Lo que un caso de uso quiere dejar registrado."""

    action: str
    outcome: Outcome = Outcome.SUCCESS
    resource_type: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    changes: dict[str, Any] | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
