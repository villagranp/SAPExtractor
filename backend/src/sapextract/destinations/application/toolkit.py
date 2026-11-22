"""Dependencias compartidas por los casos de uso de destinos (una instancia por proceso)."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings
from sapextract.core.security.crypto import SecretCipher
from sapextract.destinations.application.plugins import PluginContext, PluginRegistry
from sapextract.destinations.domain.model import CsvRoots
from sapextract.destinations.infrastructure.csv_plugin import CsvPlugin
from sapextract.destinations.infrastructure.snowflake_plugin import SnowflakePlugin
from sapextract.destinations.infrastructure.sqlserver_plugin import SqlServerPlugin


@dataclass(frozen=True, kw_only=True)
class DestinationDependent:
    """Objeto que usa un destino (pipelines, It6)."""

    kind: str
    id: str
    name: str
    active: bool


type DependentsProvider = Callable[[Session, uuid.UUID], list[DestinationDependent]]


@dataclass(frozen=True, kw_only=True)
class DestinationsToolkit:
    settings: Settings
    audit: AuditService
    cipher: SecretCipher | None
    registry: PluginRegistry
    context: PluginContext
    dependents: list[DependentsProvider] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        audit: AuditService | None = None,
        registry: PluginRegistry | None = None,
    ) -> DestinationsToolkit:
        return cls(
            settings=settings,
            audit=audit or AuditService(),
            cipher=SecretCipher.from_settings(settings) if settings.master_key else None,
            registry=registry
            or PluginRegistry([CsvPlugin(), SnowflakePlugin(), SqlServerPlugin()]),
            context=PluginContext(
                host_policy=settings.dest_host_policy,
                csv_roots=CsvRoots(tuple(Path(r) for r in settings.csv_roots)),
                work_dir=Path(tempfile.gettempdir()),
                odbc_driver=settings.mssql_odbc_driver,
            ),
        )

    def dependents_of(
        self, session: Session, destination_id: uuid.UUID
    ) -> list[DestinationDependent]:
        return [d for provider in self.dependents for d in provider(session, destination_id)]
