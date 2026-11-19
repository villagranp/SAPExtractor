"""Errores de dominio, sin dependencias de frameworks (los usa la capa ``domain``).

``core/errors.py`` los traduce a respuestas Problem Details (RFC 9457).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class FieldIssue:
    """Problema asociado a un campo concreto de la entrada."""

    field: str
    message: str
    code: str | None = None


class DomainError(Exception):
    """Error de negocio esperado. Las subclases fijan código, estado HTTP y título."""

    code: ClassVar[str] = "SX-GEN-ERROR"
    status_code: ClassVar[int] = 400
    title: ClassVar[str] = "Solicitud no válida"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        errors: list[FieldIssue] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = code or self.code
        self.errors = errors or []
        self.headers = headers


class NotFoundError(DomainError):
    code = "SX-GEN-NOT-FOUND"
    status_code = 404
    title = "Recurso no encontrado"


class ConflictError(DomainError):
    code = "SX-GEN-CONFLICT"
    status_code = 409
    title = "Conflicto con el estado actual"


class AuthenticationError(DomainError):
    code = "SX-AUTH-UNAUTHENTICATED"
    status_code = 401
    title = "Autenticación requerida"


class PermissionDeniedError(DomainError):
    code = "SX-AUTH-FORBIDDEN"
    status_code = 403
    title = "Permiso denegado"


class BusinessRuleError(DomainError):
    code = "SX-GEN-RULE"
    status_code = 422
    title = "Regla de negocio incumplida"


class TooManyRequestsError(DomainError):
    code = "SX-GEN-RATE-LIMIT"
    status_code = 429
    title = "Demasiadas solicitudes"
