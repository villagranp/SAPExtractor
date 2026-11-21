"""Consultas reutilizadas por los casos de uso de identidad."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sapextract.core.exceptions import NotFoundError
from sapextract.identity.domain.model import UserStatus
from sapextract.identity.domain.permissions import PLATFORM_ADMIN
from sapextract.identity.infrastructure.models import (
    ApiToken,
    IdentityProvider,
    IdpGroupMapping,
    Role,
    RolePermission,
    UserAccount,
    UserRole,
    UserSession,
)


def effective_permissions(session: Session, user_id: uuid.UUID) -> frozenset[str]:
    """Unión de los permisos de todos los roles del usuario."""
    stmt = (
        select(RolePermission.permission_code)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    return frozenset(session.scalars(stmt))


def get_user(session: Session, user_id: uuid.UUID) -> UserAccount:
    user = session.get(UserAccount, user_id)
    if user is None:
        msg = "El usuario no existe."
        raise NotFoundError(msg, code="SX-IDN-USER-NOT-FOUND")
    return user


def find_user(session: Session, username: str) -> UserAccount | None:
    return session.scalar(
        select(UserAccount).where(
            UserAccount.username == username, UserAccount.deleted_at.is_(None)
        )
    )


def get_role(session: Session, role_id: uuid.UUID) -> Role:
    role = session.get(Role, role_id)
    if role is None:
        msg = "El rol no existe."
        raise NotFoundError(msg, code="SX-IDN-ROLE-NOT-FOUND")
    return role


def role_by_code(session: Session, code: str) -> Role | None:
    return session.scalar(select(Role).where(Role.code == code))


def is_active_admin(user: UserAccount) -> bool:
    return (
        user.status == UserStatus.ACTIVE
        and user.deleted_at is None
        and any(link.role.code == PLATFORM_ADMIN for link in user.role_links)
    )


def count_other_active_admins(session: Session, exclude_user_id: uuid.UUID) -> int:
    stmt = (
        select(func.count(func.distinct(UserAccount.id)))
        .join(UserRole, UserRole.user_id == UserAccount.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.code == PLATFORM_ADMIN,
            UserAccount.status == UserStatus.ACTIVE,
            UserAccount.deleted_at.is_(None),
            UserAccount.id != exclude_user_id,
        )
    )
    return session.scalar(stmt) or 0


def revoke_user_sessions(
    session: Session,
    user_id: uuid.UUID,
    *,
    reason: str,
    now: datetime,
    keep_session_id: uuid.UUID | None = None,
) -> int:
    stmt = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if keep_session_id is not None:
        stmt = stmt.where(UserSession.id != keep_session_id)
    result = session.execute(
        stmt.values(revoked_at=now, revoked_reason=reason).execution_options(
            synchronize_session=False
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


def revoke_user_tokens(session: Session, user_id: uuid.UUID, *, now: datetime) -> int:
    result = session.execute(
        update(ApiToken)
        .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def roles_in_use_by_idp(session: Session, role_id: uuid.UUID) -> int:
    """Usos de un rol en Entra ID (rol por defecto o mapeo): impiden borrarlo."""
    mapped = session.scalar(
        select(func.count()).select_from(IdpGroupMapping).where(IdpGroupMapping.role_id == role_id)
    )
    default = session.scalar(
        select(func.count())
        .select_from(IdentityProvider)
        .where(IdentityProvider.default_role_id == role_id)
    )
    return int(mapped or 0) + int(default or 0)
