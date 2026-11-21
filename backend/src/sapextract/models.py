"""Registro de todos los modelos ORM (lo usa Alembic para ``--autogenerate``).

Cada módulo añade aquí la importación de sus modelos al implementarse.
"""

from sapextract.audit.infrastructure.models import AuditEventRow
from sapextract.core.db import Base
from sapextract.core.secret_store import Secret
from sapextract.core.system_settings import SystemSetting
from sapextract.destinations.infrastructure.models import Destination
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    Run,
    RunAttempt,
    RunLog,
    RunStep,
)
from sapextract.identity.infrastructure.models import (
    ApiToken,
    IdentityProvider,
    IdpGroupMapping,
    LocalCredential,
    Permission,
    Role,
    RolePermission,
    UserAccount,
    UserRole,
    UserSession,
)
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sap_connections.infrastructure.models import SapCatalogCache, SapSystem
from sapextract.sources.infrastructure.models import Source, SourceSchemaVersion

__all__ = [
    "ApiToken",
    "AuditEventRow",
    "Base",
    "DeltaState",
    "DeltaStateHistory",
    "Destination",
    "IdentityProvider",
    "IdpGroupMapping",
    "LocalCredential",
    "Permission",
    "Pipeline",
    "Role",
    "RolePermission",
    "Run",
    "RunAttempt",
    "RunLog",
    "RunStep",
    "SapCatalogCache",
    "SapSystem",
    "Secret",
    "Source",
    "SourceSchemaVersion",
    "SystemSetting",
    "UserAccount",
    "UserRole",
    "UserSession",
]
