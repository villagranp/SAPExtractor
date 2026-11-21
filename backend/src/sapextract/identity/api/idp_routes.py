"""Configuración de Microsoft Entra ID (pantalla "Proveedor de identidad", plan 02 §4)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.identity.api.deps import RequestInfoDep, ToolkitDep, require_permission
from sapextract.identity.api.idp_schemas import (
    EntraProviderResponse,
    EntraProviderUpdateRequest,
    EntraStatusRequest,
    EntraTestReportResponse,
    EntraTestRequest,
    GroupMappingsRequest,
)
from sapextract.identity.application.idp_service import IdentityProviderService
from sapextract.identity.domain.model import Principal

router = APIRouter(prefix="/identity-providers/entra", tags=["Proveedor de identidad"])

IdpManager = Annotated[Principal, Depends(require_permission("idp:manage"))]


def _view(request: Request, service: IdentityProviderService) -> EntraProviderResponse:
    return EntraProviderResponse.of(service.view(str(request.base_url)))


@router.get("", response_model=EntraProviderResponse, summary="Configuración de Entra ID")
def get_entra(
    request: Request, _: IdpManager, session: SessionDep, toolkit: ToolkitDep
) -> EntraProviderResponse:
    return _view(request, IdentityProviderService(session, toolkit))


@router.put(
    "",
    response_model=EntraProviderResponse,
    summary="Guarda la configuración de Entra ID (la credencial nunca se devuelve)",
    openapi_extra=audited("idp.updated", "idp.credentials_rotated"),
)
def save_entra(
    body: EntraProviderUpdateRequest,
    request: Request,
    principal: IdpManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> EntraProviderResponse:
    service = IdentityProviderService(session, toolkit)
    service.save(body.to_domain(), principal, info)
    return _view(request, service)


@router.post(
    "/test",
    response_model=EntraTestReportResponse,
    summary="Prueba inquilino, claves de firma y credencial sin guardar",
    openapi_extra=audited("idp.tested"),
)
def test_entra(
    body: EntraTestRequest,
    principal: IdpManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> EntraTestReportResponse:
    report = IdentityProviderService(session, toolkit).test(body.to_domain(), principal, info)
    return EntraTestReportResponse.of(report)


@router.post(
    "/enable",
    response_model=EntraProviderResponse,
    summary="Activa el inicio de sesión con Entra ID (exige una prueba correcta)",
    openapi_extra=audited("idp.enabled"),
)
def enable_entra(
    body: EntraStatusRequest,
    request: Request,
    principal: IdpManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> EntraProviderResponse:
    service = IdentityProviderService(session, toolkit)
    service.enable(body.version, principal, info)
    return _view(request, service)


@router.post(
    "/disable",
    response_model=EntraProviderResponse,
    summary="Desactiva Entra ID (el administrador local sigue entrando)",
    openapi_extra=audited("idp.disabled"),
)
def disable_entra(
    body: EntraStatusRequest,
    request: Request,
    principal: IdpManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> EntraProviderResponse:
    service = IdentityProviderService(session, toolkit)
    service.disable(body.version, body.reason, principal, info)
    return _view(request, service)


@router.put(
    "/group-mappings",
    response_model=EntraProviderResponse,
    summary="Sustituye el mapeo de grupos de Entra a roles",
    openapi_extra=audited("idp.group_mappings_changed"),
)
def save_group_mappings(
    body: GroupMappingsRequest,
    request: Request,
    principal: IdpManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> EntraProviderResponse:
    service = IdentityProviderService(session, toolkit)
    service.replace_group_mappings(
        body.version, [m.to_domain() for m in body.mappings], principal, info
    )
    return _view(request, service)
