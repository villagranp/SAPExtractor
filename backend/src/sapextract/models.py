"""Registro de todos los modelos ORM (lo usa Alembic para ``--autogenerate``).

Cada módulo añade aquí la importación de sus modelos al implementarse.
"""

from sapextract.audit.infrastructure.models import AuditEventRow
from sapextract.core.db import Base
from sapextract.core.secret_store import Secret
from sapextract.core.system_settings import SystemSetting
from sapextract.destinations.infrastructure.models import Destination
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
from sapextract.sap_connections.infrastructure.models import SapCatalogCache, SapSystem

__all__ = [
    "ApiToken",
    "AuditEventRow",
    "Base",
    "Destination",
    "LocalCredential",
    "Permission",
    "Role",
    "RolePermission",
    "SapCatalogCache",
    "SapSystem",
    "Secret",
    "SystemSetting",
    "UserAccount",
    "UserRole",
    "UserSession",
]
