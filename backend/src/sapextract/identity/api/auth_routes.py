"""Endpoints de autenticación local y de la sesión actual (``/auth``, ``/me``)."""

from __future__ import annotations

import uuid
from typing import Annotated

import segno
from fastapi import APIRouter, Depends, Request, Response, status

from sapextract.audit.api.openapi import audited, not_audited
from sapextract.core.dependencies import SessionDep, SettingsDep
from sapextract.core.exceptions import NotFoundError
from sapextract.identity.api.deps import (
    SESSION_COOKIE,
    AnyStagePrincipal,
    CurrentPrincipal,
    OptionalAuthentication,
    RequestInfoDep,
    ToolkitDep,
    auth_rate_limit,
    check_csrf,
    clear_session_cookies,
    require_stage,
    set_session_cookies,
)
from sapextract.identity.api.schemas import (
    LoginRequest,
    MeResponse,
    MfaCodeRequest,
    MfaEnrollmentResponse,
    PasswordChangeRequest,
    RevokedResponse,
    RoleRef,
    SessionResponse,
    SessionStatusResponse,
)
from sapextract.identity.application.auth_service import AuthService
from sapextract.identity.application.user_service import UserService
from sapextract.identity.domain.model import AuthSource, Principal, SessionStage
from sapextract.identity.infrastructure.queries import get_user

router = APIRouter(tags=["Autenticación"])

RateLimited = [Depends(auth_rate_limit)]
MfaStagePrincipal = Annotated[Principal, Depends(require_stage(SessionStage.MFA))]
PasswordStagePrincipal = Annotated[
    Principal, Depends(require_stage(SessionStage.PASSWORD_CHANGE, SessionStage.ACTIVE))
]
MfaSetupPrincipal = Annotated[
    Principal, Depends(require_stage(SessionStage.MFA_SETUP, SessionStage.ACTIVE))
]


@router.post(
    "/auth/login",
    response_model=SessionStatusResponse,
    summary="Inicio de sesión local (usuario y contraseña)",
    dependencies=RateLimited,
    openapi_extra=audited("auth.login_succeeded", "auth.login_failed", "user.locked"),
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    toolkit: ToolkitDep,
    settings: SettingsDep,
    info: RequestInfoDep,
) -> SessionStatusResponse:
    issued = AuthService(session, toolkit).login(
        body.username,
        body.password,
        info,
        previous_token=request.cookies.get(SESSION_COOKIE),
    )
    set_session_cookies(response, issued, settings)
    return SessionStatusResponse(stage=issued.stage)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cierra la sesión actual",
    openapi_extra=audited("auth.logout"),
)
def logout(
    request: Request,
    response: Response,
    auth: OptionalAuthentication,
    session: SessionDep,
    toolkit: ToolkitDep,
    settings: SettingsDep,
    info: RequestInfoDep,
) -> None:
    if auth is not None:
        check_csrf(request, auth)
        AuthService(session, toolkit).logout(auth.principal, info)
    clear_session_cookies(response, settings)


@router.post(
    "/auth/mfa/verify",
    response_model=SessionStatusResponse,
    summary="Segundo factor (TOTP) durante el inicio de sesión",
    dependencies=RateLimited,
    openapi_extra=audited("auth.mfa_verified", "auth.mfa_failed", "user.locked"),
)
def verify_mfa(
    body: MfaCodeRequest,
    principal: MfaStagePrincipal,
    response: Response,
    session: SessionDep,
    toolkit: ToolkitDep,
    settings: SettingsDep,
    info: RequestInfoDep,
) -> SessionStatusResponse:
    issued = AuthService(session, toolkit).verify_mfa(principal, body.code, info)
    set_session_cookies(response, issued, settings)
    return SessionStatusResponse(stage=issued.stage)


@router.post(
    "/auth/password/change",
    response_model=SessionStatusResponse,
    summary="Cambia la contraseña local (cierra las demás sesiones)",
    dependencies=RateLimited,
    openapi_extra=audited("auth.password_changed"),
)
def change_password(
    body: PasswordChangeRequest,
    principal: PasswordStagePrincipal,
    response: Response,
    session: SessionDep,
    toolkit: ToolkitDep,
    settings: SettingsDep,
    info: RequestInfoDep,
) -> SessionStatusResponse:
    issued = AuthService(session, toolkit).change_password(
        principal,
        current_password=body.current_password,
        new_password=body.new_password,
        request=info,
    )
    set_session_cookies(response, issued, settings)
    return SessionStatusResponse(stage=issued.stage)


@router.post(
    "/auth/mfa/setup",
    response_model=MfaEnrollmentResponse,
    summary="Inicia la configuración del segundo factor (devuelve el QR)",
    openapi_extra=not_audited("Solo prepara un secreto pendiente; se audita al confirmarlo"),
)
def start_mfa_setup(
    principal: MfaSetupPrincipal, session: SessionDep, toolkit: ToolkitDep
) -> MfaEnrollmentResponse:
    enrollment = AuthService(session, toolkit).start_mfa_enrollment(principal)
    qr = segno.make(enrollment.otpauth_uri, error="m")
    return MfaEnrollmentResponse(
        secret=enrollment.secret,
        otpauth_uri=enrollment.otpauth_uri,
        qr_svg_data_uri=qr.svg_data_uri(scale=5, border=2),
    )


@router.post(
    "/auth/mfa/enable",
    response_model=SessionStatusResponse,
    summary="Confirma el segundo factor con un primer código",
    dependencies=RateLimited,
    openapi_extra=audited("auth.mfa_enabled"),
)
def enable_mfa(
    body: MfaCodeRequest,
    principal: MfaSetupPrincipal,
    response: Response,
    session: SessionDep,
    toolkit: ToolkitDep,
    settings: SettingsDep,
    info: RequestInfoDep,
) -> SessionStatusResponse:
    issued = AuthService(session, toolkit).confirm_mfa_enrollment(principal, body.code, info)
    if issued is None:
        return SessionStatusResponse(stage=principal.stage)
    set_session_cookies(response, issued, settings)
    return SessionStatusResponse(stage=issued.stage)


# ------------------------------------------------------------------ /me
@router.get("/me", response_model=MeResponse, summary="Perfil, paso de la sesión y permisos")
def me(principal: AnyStagePrincipal, session: SessionDep) -> MeResponse:
    user = get_user(session, principal.user_id)
    return MeResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        auth_source=AuthSource(user.auth_source),
        auth_method=principal.auth_method,
        stage=principal.stage,
        is_break_glass=user.is_break_glass,
        mfa_enabled=bool(user.credential and user.credential.totp_enabled),
        roles=[RoleRef.of(role) for role in user.roles],
        permissions=sorted(principal.permissions),
    )


@router.get("/me/sessions", response_model=list[SessionResponse], summary="Mis sesiones activas")
def my_sessions(
    principal: CurrentPrincipal, session: SessionDep, toolkit: ToolkitDep
) -> list[SessionResponse]:
    rows = UserService(session, toolkit).sessions(principal.user_id)
    return [SessionResponse.of(row, principal.session_id) for row in rows]


@router.delete(
    "/me/sessions/{session_id}",
    response_model=RevokedResponse,
    summary="Cierra una de mis sesiones",
    openapi_extra=audited("session.revoked"),
)
def revoke_my_session(
    session_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RevokedResponse:
    revoked = UserService(session, toolkit).revoke_sessions(
        principal.user_id, principal, info, session_id=session_id
    )
    if not revoked:
        msg = "La sesión no existe o ya estaba cerrada."
        raise NotFoundError(msg, code="SX-IDN-SESSION-NOT-FOUND")
    return RevokedResponse(revoked=revoked)
