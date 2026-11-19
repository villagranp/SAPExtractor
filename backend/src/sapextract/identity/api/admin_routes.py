"""Administración de usuarios, roles, permisos y tokens API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from sapextract.audit.api.openapi import audited
from sapextract.core.dependencies import SessionDep
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, ToolkitDep, require_permission
from sapextract.identity.api.schemas import (
    AnonymizeRequest,
    ApiTokenCreatedResponse,
    ApiTokenCreateRequest,
    ApiTokenResponse,
    PermissionResponse,
    ReasonRequest,
    RevokedResponse,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
    SessionResponse,
    TemporaryPasswordResponse,
    UserCreatedResponse,
    UserCreateRequest,
    UserDetail,
    UserRolesRequest,
    UserSummary,
    UserUpdateRequest,
)
from sapextract.identity.application.role_service import NewRole, RoleChanges, RoleService
from sapextract.identity.application.token_service import ApiTokenService, NewApiToken
from sapextract.identity.application.user_service import (
    NewUser,
    UserChanges,
    UserFilters,
    UserService,
)
from sapextract.identity.domain.model import AuthSource, Principal, UserStatus

router = APIRouter()


UserReader = Annotated[Principal, Depends(require_permission("user:read"))]
UserCreator = Annotated[Principal, Depends(require_permission("user:create"))]
UserEditor = Annotated[Principal, Depends(require_permission("user:update"))]
UserDisabler = Annotated[Principal, Depends(require_permission("user:disable"))]
UserDeleter = Annotated[Principal, Depends(require_permission("user:delete"))]
SessionRevoker = Annotated[Principal, Depends(require_permission("session:revoke"))]
RoleReader = Annotated[Principal, Depends(require_permission("role:read"))]
RoleManager = Annotated[Principal, Depends(require_permission("role:manage"))]
TokenManager = Annotated[Principal, Depends(require_permission("api_token:manage"))]
Paging = Annotated[PageParams, Depends(page_params)]


# ================================================================== usuarios
@router.get("/users", response_model=Page[UserSummary], tags=["Usuarios"], summary="Usuarios")
def list_users(
    _: UserReader,
    session: SessionDep,
    toolkit: ToolkitDep,
    page: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
    auth_source: AuthSource | None = None,
    role_id: uuid.UUID | None = None,
    include_anonymized: bool = False,
) -> Page[UserSummary]:
    users, total = UserService(session, toolkit).search(
        UserFilters(
            q=q,
            status=status_filter,
            auth_source=auth_source,
            role_id=role_id,
            include_anonymized=include_anonymized,
        ),
        page,
    )
    return Page[UserSummary].of([UserSummary.of(u) for u in users], total, page)


@router.post(
    "/users",
    response_model=UserCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Usuarios"],
    summary="Alta de usuario (local con contraseña temporal, o Entra ID pre-registrado)",
    openapi_extra=audited("user.created"),
)
def create_user(
    body: UserCreateRequest,
    actor: UserCreator,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserCreatedResponse:
    created = UserService(session, toolkit).create(
        NewUser(
            username=body.username,
            display_name=body.display_name,
            email=body.email,
            auth_source=body.auth_source,
            role_ids=body.role_ids,
        ),
        actor,
        info,
    )
    return UserCreatedResponse(
        user=UserDetail.of(created.user), temporary_password=created.temporary_password
    )


@router.get("/users/{user_id}", response_model=UserDetail, tags=["Usuarios"])
def get_user_detail(
    user_id: uuid.UUID, _: UserReader, session: SessionDep, toolkit: ToolkitDep
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).get(user_id))


@router.patch(
    "/users/{user_id}",
    response_model=UserDetail,
    tags=["Usuarios"],
    openapi_extra=audited("user.updated"),
)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    email_sent = "email" in body.model_fields_set
    user = UserService(session, toolkit).update(
        user_id,
        UserChanges(
            display_name=body.display_name,
            email=body.email or None,
            clear_email=email_sent and not body.email,
            version=body.version,
        ),
        actor,
        info,
    )
    return UserDetail.of(user)


@router.put(
    "/users/{user_id}/roles",
    response_model=UserDetail,
    tags=["Usuarios"],
    summary="Sustituye los roles asignados manualmente",
    openapi_extra=audited("user.roles_changed"),
)
def set_user_roles(
    user_id: uuid.UUID,
    body: UserRolesRequest,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(
        UserService(session, toolkit).set_roles(user_id, body.role_ids, actor, info)
    )


@router.post(
    "/users/{user_id}/disable",
    response_model=UserDetail,
    tags=["Usuarios"],
    summary="Baja: desactiva y cierra sesiones y tokens",
    openapi_extra=audited("user.disabled"),
)
def disable_user(
    user_id: uuid.UUID,
    body: ReasonRequest,
    actor: UserDisabler,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).disable(user_id, actor, info, body.reason))


@router.post(
    "/users/{user_id}/enable",
    response_model=UserDetail,
    tags=["Usuarios"],
    openapi_extra=audited("user.enabled"),
)
def enable_user(
    user_id: uuid.UUID,
    actor: UserDisabler,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).enable(user_id, actor, info))


@router.post(
    "/users/{user_id}/lock",
    response_model=UserDetail,
    tags=["Usuarios"],
    openapi_extra=audited("user.locked"),
)
def lock_user(
    user_id: uuid.UUID,
    body: ReasonRequest,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).lock(user_id, actor, info, body.reason))


@router.post(
    "/users/{user_id}/unlock",
    response_model=UserDetail,
    tags=["Usuarios"],
    summary="Quita el bloqueo manual o por intentos fallidos",
    openapi_extra=audited("user.unlocked"),
)
def unlock_user(
    user_id: uuid.UUID,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).unlock(user_id, actor, info))


@router.post(
    "/users/{user_id}/reset-password",
    response_model=TemporaryPasswordResponse,
    tags=["Usuarios"],
    summary="Genera una contraseña temporal (se muestra una vez)",
    openapi_extra=audited("user.password_reset"),
)
def reset_password(
    user_id: uuid.UUID,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> TemporaryPasswordResponse:
    _, temporary = UserService(session, toolkit).reset_password(user_id, actor, info)
    return TemporaryPasswordResponse(temporary_password=temporary)


@router.post(
    "/users/{user_id}/reset-mfa",
    response_model=UserDetail,
    tags=["Usuarios"],
    summary="Elimina el segundo factor (deberá configurarlo de nuevo)",
    openapi_extra=audited("user.mfa_reset"),
)
def reset_mfa(
    user_id: uuid.UUID,
    actor: UserEditor,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).reset_mfa(user_id, actor, info))


@router.post(
    "/users/{user_id}/anonymize",
    response_model=UserDetail,
    tags=["Usuarios"],
    summary="Anonimiza un usuario desactivado (conserva la auditoría)",
    openapi_extra=audited("user.anonymized"),
)
def anonymize_user(
    user_id: uuid.UUID,
    body: AnonymizeRequest,
    actor: UserDeleter,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> UserDetail:
    return UserDetail.of(UserService(session, toolkit).anonymize(user_id, actor, info, body.reason))


@router.get(
    "/users/{user_id}/sessions",
    response_model=list[SessionResponse],
    tags=["Usuarios"],
    summary="Sesiones activas de un usuario",
)
def user_sessions(
    user_id: uuid.UUID, _: UserReader, session: SessionDep, toolkit: ToolkitDep
) -> list[SessionResponse]:
    rows = UserService(session, toolkit).sessions(user_id)
    return [SessionResponse.of(row, None) for row in rows]


@router.delete(
    "/users/{user_id}/sessions",
    response_model=RevokedResponse,
    tags=["Usuarios"],
    summary="Cierra todas las sesiones de un usuario",
    openapi_extra=audited("session.revoked"),
)
def revoke_user_sessions(
    user_id: uuid.UUID,
    actor: SessionRevoker,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RevokedResponse:
    return RevokedResponse(
        revoked=UserService(session, toolkit).revoke_sessions(user_id, actor, info)
    )


# ================================================================== roles y permisos
@router.get(
    "/permissions",
    response_model=list[PermissionResponse],
    tags=["Roles"],
    summary="Catálogo de permisos",
)
def list_permissions(
    _: RoleReader, session: SessionDep, toolkit: ToolkitDep
) -> list[PermissionResponse]:
    return [PermissionResponse.of(p) for p in RoleService(session, toolkit).permissions()]


@router.get("/roles", response_model=list[RoleResponse], tags=["Roles"], summary="Roles")
def list_roles(_: RoleReader, session: SessionDep, toolkit: ToolkitDep) -> list[RoleResponse]:
    return [RoleResponse.of(role, count) for role, count in RoleService(session, toolkit).all()]


@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Roles"],
    summary="Crea un rol personalizado",
    openapi_extra=audited("role.created"),
)
def create_role(
    body: RoleCreateRequest,
    actor: RoleManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RoleResponse:
    role = RoleService(session, toolkit).create(
        NewRole(
            code=body.code,
            name=body.name,
            description=body.description,
            permissions=body.permissions,
        ),
        actor,
        info,
    )
    return RoleResponse.of(role, 0)


@router.get("/roles/{role_id}", response_model=RoleResponse, tags=["Roles"])
def get_role_detail(
    role_id: uuid.UUID, _: RoleReader, session: SessionDep, toolkit: ToolkitDep
) -> RoleResponse:
    role, count = RoleService(session, toolkit).get(role_id)
    return RoleResponse.of(role, count)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleResponse,
    tags=["Roles"],
    openapi_extra=audited("role.updated"),
)
def update_role(
    role_id: uuid.UUID,
    body: RoleUpdateRequest,
    actor: RoleManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> RoleResponse:
    service = RoleService(session, toolkit)
    service.update(
        role_id,
        RoleChanges(
            name=body.name,
            description=body.description,
            permissions=body.permissions,
            version=body.version,
        ),
        actor,
        info,
    )
    role, count = service.get(role_id)
    return RoleResponse.of(role, count)


@router.delete(
    "/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Roles"],
    openapi_extra=audited("role.deleted"),
)
def delete_role(
    role_id: uuid.UUID,
    actor: RoleManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> None:
    RoleService(session, toolkit).delete(role_id, actor, info)


# ================================================================== tokens API
@router.get(
    "/api-tokens", response_model=list[ApiTokenResponse], tags=["Tokens API"], summary="Tokens API"
)
def list_tokens(
    _: TokenManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    user_id: uuid.UUID | None = None,
) -> list[ApiTokenResponse]:
    return [ApiTokenResponse.of(t) for t in ApiTokenService(session, toolkit).all(user_id)]


@router.post(
    "/api-tokens",
    response_model=ApiTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Tokens API"],
    summary="Crea un token API (el valor se muestra una sola vez)",
    openapi_extra=audited("api_token.created"),
)
def create_token(
    body: ApiTokenCreateRequest,
    actor: TokenManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> ApiTokenCreatedResponse:
    created = ApiTokenService(session, toolkit).create(
        NewApiToken(
            name=body.name,
            user_id=body.user_id,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
        ),
        actor,
        info,
    )
    return ApiTokenCreatedResponse(token=ApiTokenResponse.of(created.token), secret=created.secret)


@router.delete(
    "/api-tokens/{token_id}",
    response_model=ApiTokenResponse,
    tags=["Tokens API"],
    summary="Revoca un token API",
    openapi_extra=audited("api_token.revoked"),
)
def revoke_token(
    token_id: uuid.UUID,
    actor: TokenManager,
    session: SessionDep,
    toolkit: ToolkitDep,
    info: RequestInfoDep,
) -> ApiTokenResponse:
    return ApiTokenResponse.of(ApiTokenService(session, toolkit).revoke(token_id, actor, info))
