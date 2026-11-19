"""Registro de todos los modelos ORM (lo usa Alembic para ``--autogenerate``).

Cada módulo añade aquí la importación de sus modelos al implementarse.
"""

from sapextract.audit.infrastructure.models import AuditEventRow
from sapextract.core.db import Base
from sapextract.core.system_settings import SystemSetting
from sapextract.identity.infrastructure.models import (
    ApiToken,
    LocalCredential,
    Permission,
    Role,
    RolePermission,
    UserAccount,
    UserRole,
    UserSession,
)

__all__ = [
    "ApiToken",
    "AuditEventRow",
    "Base",
    "LocalCredential",
    "Permission",
    "Role",
    "RolePermission",
    "SystemSetting",
    "UserAccount",
    "UserRole",
    "UserSession",
]
