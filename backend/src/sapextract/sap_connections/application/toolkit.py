"""Dependencias compartidas por los casos de uso de conexiones SAP (una instancia por proceso)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings
from sapextract.core.network import HostPolicy
from sapextract.core.security.crypto import SecretCipher
from sapextract.sap_connections.domain.model import ConnectionSpec
from sapextract.sap_connections.infrastructure.network_probe import ProbeResult, probe
from sapextract.sap_connections.infrastructure.odata_client import (
    ConcurrencyLimiter,
    RetryPolicy,
    SapODataClient,
)


@dataclass(frozen=True, kw_only=True)
class Dependent:
    """Objeto que usa un sistema SAP (origen, pipeline…), aportado por otros módulos."""

    kind: str
    id: str
    name: str
    active: bool


type ClientFactory = Callable[[ConnectionSpec, RetryPolicy], SapODataClient]
type Probe = Callable[[ConnectionSpec], ProbeResult]
type DependentsProvider = Callable[[Session, uuid.UUID], list[Dependent]]


@dataclass(frozen=True, kw_only=True)
class SapToolkit:
    settings: Settings
    audit: AuditService
    cipher: SecretCipher | None
    limiter: ConcurrencyLimiter
    client_factory: ClientFactory
    probe: Probe
    catalog_ttl: timedelta
    host_policy: HostPolicy
    explorer_retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=2))
    # Los módulos de orígenes (It3) y pipelines (It6) registran aquí sus dependencias.
    dependents: list[DependentsProvider] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        audit: AuditService | None = None,
        client_factory: ClientFactory | None = None,
        probe_fn: Probe | None = None,
    ) -> SapToolkit:
        limiter = ConcurrencyLimiter()
        policy = settings.sap_host_policy

        def default_factory(spec: ConnectionSpec, retry: RetryPolicy) -> SapODataClient:
            return SapODataClient(spec, retry=retry, limiter=limiter, host_policy=policy)

        def default_probe(spec: ConnectionSpec) -> ProbeResult:
            return probe(spec, policy)

        return cls(
            settings=settings,
            audit=audit or AuditService(),
            cipher=SecretCipher.from_settings(settings) if settings.master_key else None,
            limiter=limiter,
            client_factory=client_factory or default_factory,
            probe=probe_fn or default_probe,
            catalog_ttl=timedelta(minutes=settings.sap_catalog_ttl_minutes),
            host_policy=policy,
        )

    def dependents_of(self, session: Session, system_id: uuid.UUID) -> list[Dependent]:
        return [d for provider in self.dependents for d in provider(session, system_id)]
