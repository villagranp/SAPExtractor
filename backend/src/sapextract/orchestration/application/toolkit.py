"""Dependencias compartidas por los casos de uso de orquestación (una instancia por proceso)."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sapextract.audit.application.service import AuditService
from sapextract.core.clock import utcnow
from sapextract.core.config import Settings
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.orchestration.domain.ports import EngineHooks
from sapextract.sap_connections.application.toolkit import SapToolkit


def _random() -> float:
    # Jitter de los reintentos: no es criptografía.
    return random.random()  # noqa: S311  # nosec B311


@dataclass(frozen=True, kw_only=True)
class OrchestrationToolkit:
    settings: Settings
    audit: AuditService
    sap: SapToolkit
    destinations: DestinationsToolkit
    # Reloj y azar inyectables: las pruebas fijan la hora y el jitter.
    clock: Callable[[], datetime] = utcnow
    random: Callable[[], float] = _random
    # El motor (It5) se engancha aquí; sin él la orquestación funciona igual.
    hooks: EngineHooks | None = None

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        audit: AuditService | None = None,
        sap: SapToolkit | None = None,
        destinations: DestinationsToolkit | None = None,
        clock: Callable[[], datetime] = utcnow,
        random_fn: Callable[[], float] = _random,
        hooks: EngineHooks | None = None,
    ) -> OrchestrationToolkit:
        audit = audit or AuditService()
        return cls(
            settings=settings,
            audit=audit,
            sap=sap or SapToolkit.build(settings, audit=audit),
            destinations=destinations or DestinationsToolkit.build(settings, audit=audit),
            clock=clock,
            random=random_fn,
            hooks=hooks,
        )

    @property
    def stale_after(self) -> timedelta:
        return timedelta(seconds=self.settings.run_stale_seconds)

    @property
    def misfire_grace(self) -> timedelta:
        return timedelta(seconds=self.settings.scheduler_misfire_grace_seconds)
