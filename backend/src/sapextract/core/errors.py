"""Errores de dominio y respuestas Problem Details (RFC 9457)."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from sapextract.core.context import get_request_id

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://sapextract/errors/"

log = structlog.get_logger(__name__)


class FieldError(BaseModel):
    field: str
    message: str
    code: str | None = None


class ProblemDetails(BaseModel):
    """Formato único de error de la API."""

    type: str = Field(examples=[f"{PROBLEM_TYPE_BASE}sx-gen-not-found"])
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str = Field(description="Código estable de error de SAPExtract (SX-...)")
    request_id: str | None = None
    errors: list[FieldError] = Field(default_factory=list)


class DomainError(Exception):
    """Error de negocio esperado. Las subclases fijan código, estado HTTP y título."""

    code: ClassVar[str] = "SX-GEN-ERROR"
    status_code: ClassVar[int] = status.HTTP_400_BAD_REQUEST
    title: ClassVar[str] = "Solicitud no válida"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        errors: list[FieldError] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = code or self.code
        self.errors = errors or []


class NotFoundError(DomainError):
    code = "SX-GEN-NOT-FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    title = "Recurso no encontrado"


class ConflictError(DomainError):
    code = "SX-GEN-CONFLICT"
    status_code = status.HTTP_409_CONFLICT
    title = "Conflicto con el estado actual"


class AuthenticationError(DomainError):
    code = "SX-AUTH-UNAUTHENTICATED"
    status_code = status.HTTP_401_UNAUTHORIZED
    title = "Autenticación requerida"


class PermissionDeniedError(DomainError):
    code = "SX-AUTH-FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN
    title = "Permiso denegado"


class BusinessRuleError(DomainError):
    code = "SX-GEN-RULE"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    title = "Regla de negocio incumplida"


def _problem_response(
    problem: ProblemDetails, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def _type_for(code: str) -> str:
    return PROBLEM_TYPE_BASE + code.lower()


async def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):  # registrado solo para DomainError
        raise TypeError(type(exc).__name__)
    return _problem_response(
        ProblemDetails(
            type=_type_for(exc.error_code),
            title=exc.title,
            status=exc.status_code,
            detail=exc.detail,
            instance=request.url.path,
            code=exc.error_code,
            request_id=get_request_id(),
            errors=exc.errors,
        )
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registrado solo para RequestValidationError
        raise TypeError(type(exc).__name__)
    errors = [
        FieldError(
            field=".".join(str(p) for p in err.get("loc", ()) if p != "body"),
            message=str(err.get("msg", "")),
            code=str(err.get("type", "")),
        )
        for err in exc.errors()
    ]
    return _problem_response(
        ProblemDetails(
            type=_type_for("SX-GEN-VALIDATION"),
            title="Datos de entrada no válidos",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Revise los campos indicados.",
            instance=request.url.path,
            code="SX-GEN-VALIDATION",
            request_id=get_request_id(),
            errors=errors,
        )
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # registrado solo para StarletteHTTPException
        raise TypeError(type(exc).__name__)
    code = f"SX-HTTP-{exc.status_code}"
    detail: Any = exc.detail
    return _problem_response(
        ProblemDetails(
            type=_type_for(code),
            title=str(detail) if isinstance(detail, str) else "Error HTTP",
            status=exc.status_code,
            detail=str(detail) if detail is not None else None,
            instance=request.url.path,
            code=code,
            request_id=get_request_id(),
        ),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_exception", path=request.url.path, error_type=type(exc).__name__)
    return _problem_response(
        ProblemDetails(
            type=_type_for("SX-GEN-INTERNAL"),
            title="Error interno",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ocurrió un error inesperado. Indique el request_id a soporte.",
            instance=request.url.path,
            code="SX-GEN-INTERNAL",
            request_id=get_request_id(),
        )
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected)
