"""Roles personalizados y catálogo de permisos (plan 02 §2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from sapextract.audit.domain.diff import diff
from sapextract.audit.domain.model import AuditEntry, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, FieldIssue
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain.model import ROLE_CODE_PATTERN, Principal
from sapextract.identity.domain.permissions import PERMISSION_CODES
from sapextract.identity.domain.rules import ensure_can_grant
from sapextract.identity.infrastructure.models import Permission, Role, RolePermission, UserRole
from sapextract.identity.infrastructure.queries import get_role, role_by_code


@dataclass(frozen=True, kw_only=True)
class NewRole:
    code: str
    name: str
    description: str | None
    permissions: list[str]


@dataclass(frozen=True, kw_only=True)
class RoleChanges:
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None
    version: int | None = None


class RoleService:
    def __init__(self, session: Session, toolkit: IdentityToolkit) -> None:
        self.session = session
        self.tk = toolkit

    def all(self) -> list[tuple[Role, int]]:
        """Roles con su número de usuarios asignados."""
        # Comprensión explícita: dict(Result) falla porque Result expone keys().
        counts: dict[uuid.UUID, int] = {  # noqa: C416
            role_id: count
            for role_id, count in self.session.execute(
                select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
            )
        }
        roles = self.session.scalars(
            select(Role)
            .options(selectinload(Role.permission_links))
            .order_by(Role.is_system.desc(), Role.name)
        ).all()
        return [(role, counts.get(role.id, 0)) for role in roles]

    def get(self, role_id: uuid.UUID) -> tuple[Role, int]:
        role = get_role(self.session, role_id)
        count = (
            self.session.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
            )
            or 0
        )
        return role, count

    def permissions(self) -> list[Permission]:
        return list(self.session.scalars(select(Permission).order_by(Permission.code)))

    def create(self, data: NewRole, actor: Principal, request: RequestInfo) -> Role:
        code = data.code.strip().lower()
        if not ROLE_CODE_PATTERN.match(code):
            msg = "El código del rol no es válido."
            raise BusinessRuleError(
                msg,
                code="SX-IDN-ROLE-CODE-INVALID",
                errors=[
                    FieldIssue(
                        "code", "Use de 3 a 50 caracteres: minúsculas, números y guion bajo."
                    )
                ],
            )
        if role_by_code(self.session, code) is not None:
            msg = f"Ya existe un rol con el código «{code}»."
            raise ConflictError(msg, code="SX-IDN-ROLE-CODE-TAKEN")
        self._ensure_name_free(data.name)
        permissions = self._validate_permissions(data.permissions)
        ensure_can_grant(actor.permissions, permissions)
        role = Role(
            code=code,
            name=data.name.strip(),
            description=(data.description or "").strip() or None,
            is_system=False,
            created_by=actor.username,
            updated_by=actor.username,
            permission_links=[RolePermission(permission_code=p) for p in sorted(permissions)],
        )
        self.session.add(role)
        self.session.flush()
        self._audit(
            "role.created",
            role,
            actor,
            request,
            diff(
                None,
                {
                    "code": role.code,
                    "name": role.name,
                    "description": role.description,
                    "permissions": sorted(permissions),
                },
            ),
        )
        self.session.commit()
        return role

    def update(
        self, role_id: uuid.UUID, changes: RoleChanges, actor: Principal, request: RequestInfo
    ) -> Role:
        role = self._custom(role_id, "modificar")
        if changes.version is not None and changes.version != role.version:
            msg = "El rol fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        before = _snapshot(role)
        if changes.name is not None and changes.name.strip() != role.name:
            self._ensure_name_free(changes.name)
            role.name = changes.name.strip()
        if changes.description is not None:
            role.description = changes.description.strip() or None
        if changes.permissions is not None:
            wanted = self._validate_permissions(changes.permissions)
            current = set(role.permission_codes)
            ensure_can_grant(actor.permissions, (wanted - current) | (current - wanted))
            role.permission_links = [
                link for link in role.permission_links if link.permission_code in wanted
            ]
            role.permission_links.extend(
                RolePermission(permission_code=p) for p in sorted(wanted - current)
            )
        delta = diff(before, _snapshot(role))
        if not delta:
            return role
        role.updated_by = actor.username
        role.updated_at = utcnow()  # fuerza el UPDATE que incrementa la versión
        self.session.flush()
        self._audit("role.updated", role, actor, request, delta)
        self.session.commit()
        return role

    def delete(self, role_id: uuid.UUID, actor: Principal, request: RequestInfo) -> None:
        role = self._custom(role_id, "eliminar")
        assigned = (
            self.session.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
            )
            or 0
        )
        if assigned:
            msg = f"El rol está asignado a {assigned} usuario(s); retírelo antes de eliminarlo."
            raise ConflictError(msg, code="SX-IDN-ROLE-IN-USE")
        ensure_can_grant(actor.permissions, role.permission_codes)
        self._audit("role.deleted", role, actor, request, diff(_snapshot(role), None))
        self.session.delete(role)
        self.session.commit()

    # ------------------------------------------------------------------ utilidades
    def _custom(self, role_id: uuid.UUID, verb: str) -> Role:
        role = get_role(self.session, role_id)
        if role.is_system:
            msg = f"No se puede {verb} un rol de sistema; cree un rol personalizado."
            raise BusinessRuleError(msg, code="SX-IDN-SYSTEM-ROLE")
        return role

    def _ensure_name_free(self, name: str) -> None:
        exists = self.session.scalar(select(Role.id).where(Role.name == name.strip()))
        if exists is not None:
            msg = f"Ya existe un rol llamado «{name.strip()}»."
            raise ConflictError(msg, code="SX-IDN-ROLE-NAME-TAKEN")

    @staticmethod
    def _validate_permissions(codes: list[str]) -> set[str]:
        unknown = sorted(set(codes) - PERMISSION_CODES)
        if unknown:
            msg = "Permisos desconocidos: " + ", ".join(unknown)
            raise BusinessRuleError(
                msg, code="SX-IDN-PERMISSION-UNKNOWN", errors=[FieldIssue("permissions", msg)]
            )
        if not codes:
            msg = "Un rol debe tener al menos un permiso."
            raise BusinessRuleError(
                msg, code="SX-IDN-ROLE-EMPTY", errors=[FieldIssue("permissions", msg)]
            )
        return set(codes)

    def _audit(
        self,
        action: str,
        role: Role,
        actor: Principal,
        request: RequestInfo,
        changes: dict[str, dict[str, object]],
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="role",
                resource_id=str(role.id),
                resource_name=role.name,
                changes=changes,
            ),
            actor=actor.actor,
            request=request,
        )


def _snapshot(role: Role) -> dict[str, object]:
    return {
        "name": role.name,
        "description": role.description,
        "permissions": role.permission_codes,
    }
