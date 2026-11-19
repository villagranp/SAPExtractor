"""Autenticación y autorización de peticiones: sesión por cookie o token API, CSRF y RBAC."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Response

from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.context import get_request_id
from sapextract.core.dependencies import DatabaseDep, SessionDep, SettingsDep
from sapextract.core.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    TooManyRequestsError,
)
from sapextract.core.ratelimit import SlidingWindowLimiter
from sapextract.identity.application.auth_service import (
    AuthService,
    IssuedSession,
    ResolvedSession,
)
from sapextract.identity.application.token_service import ApiTokenService
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain.model import Principal, SessionStage

SESSION_COOKIE = "sx_session"
CSRF_COOKIE = "sx_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_toolkit(request: Request) -> IdentityToolkit:
    toolkit: IdentityToolkit = request.app.state.identity
    return toolkit


ToolkitDep = Annotated[IdentityToolkit, Depends(get_toolkit)]


def get_request_info(request: Request) -> RequestInfo:
    user_agent = request.headers.get("user-agent", "")[:300]
    return RequestInfo(
        ip=request.client.host if request.client else None,
        user_agent=user_agent or None,
        request_id=get_request_id(),
    )


RequestInfoDep = Annotated[RequestInfo, Depends(get_request_info)]


@dataclass(frozen=True, kw_only=True)
class Authentication:
    principal: Principal
    session: ResolvedSession | None  # None si llega por token API (sin CSRF)


def get_authentication(
    request: Request, session: SessionDep, toolkit: ToolkitDep, info: RequestInfoDep
) -> Authentication | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        principal = ApiTokenService(session, toolkit).resolve(header[7:].strip())
        if principal is None:
            msg = "El token API no es válido, está revocado o caducó."
            raise AuthenticationError(msg, code="SX-AUTH-INVALID-TOKEN")
        return Authentication(principal=principal, session=None)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    resolved = AuthService(session, toolkit).resolve_session(token, info)
    if resolved is None:
        return None
    return Authentication(principal=resolved.principal, session=resolved)


OptionalAuthentication = Annotated[Authentication | None, Depends(get_authentication)]


def check_csrf(request: Request, auth: Authentication) -> None:
    """Doble envío ligado a la sesión: cabecera ``X-CSRF-Token`` = token emitido al iniciarla."""
    if request.method in SAFE_METHODS or auth.session is None:
        return
    if not auth.session.csrf_matches(request.headers.get(CSRF_HEADER)):
        msg = "Falta el token CSRF o no es válido; recargue la página."
        raise PermissionDeniedError(msg, code="SX-AUTH-CSRF")


def require_stage(*stages: SessionStage) -> Callable[..., Principal]:
    allowed = frozenset(stages)

    def dependency(request: Request, auth: OptionalAuthentication) -> Principal:
        if auth is None:
            msg = "Inicie sesión para continuar."
            raise AuthenticationError(msg)
        check_csrf(request, auth)
        if auth.principal.stage not in allowed:
            msg = "Complete primero los pasos pendientes del inicio de sesión."
            raise PermissionDeniedError(msg, code="SX-AUTH-STEP-REQUIRED")
        return auth.principal

    return dependency


CurrentPrincipal = Annotated[Principal, Depends(require_stage(SessionStage.ACTIVE))]
AnyStagePrincipal = Annotated[Principal, Depends(require_stage(*SessionStage))]


def require_permission(*permissions: str) -> Callable[..., Principal]:
    """Exige todos los permisos indicados; registra el intento denegado en la auditoría."""

    def dependency(
        request: Request,
        principal: CurrentPrincipal,
        db: DatabaseDep,
        toolkit: ToolkitDep,
        info: RequestInfoDep,
    ) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            toolkit.audit.record_detached(
                db,
                AuditEntry(
                    action="auth.access_denied",
                    outcome=Outcome.DENIED,
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "missing": missing,
                    },
                ),
                actor=principal.actor,
                request=info,
            )
            msg = "No tiene permiso para realizar esta acción."
            raise PermissionDeniedError(msg, code="SX-AUTH-FORBIDDEN")
        return principal

    return dependency


def auth_rate_limit(request: Request, info: RequestInfoDep) -> None:
    limiter: SlidingWindowLimiter = request.app.state.auth_limiter
    wait = limiter.hit(f"{info.ip}|{request.url.path}")
    if wait is not None:
        msg = f"Demasiados intentos seguidos. Espere {wait} segundos."
        raise TooManyRequestsError(msg, headers={"Retry-After": str(wait)})


def set_session_cookies(response: Response, issued: IssuedSession, settings: SettingsDep) -> None:
    """Cookies de sesión del navegador (sin ``max_age``): se borran al cerrarlo."""
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        httponly=False,  # el SPA lo lee para enviarlo en la cabecera X-CSRF-Token
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, settings: SettingsDep) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=name == SESSION_COOKIE
        )
