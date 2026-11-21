"""Dependencias compartidas del monitoreo (una instancia por proceso)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sapextract.audit.application.service import AuditService
from sapextract.core.clock import utcnow
from sapextract.core.config import Settings
from sapextract.core.security.crypto import SecretCipher
from sapextract.monitoring.infrastructure.senders import ChannelSenders, Sender


@dataclass(frozen=True, kw_only=True)
class MonitoringToolkit:
    settings: Settings
    audit: AuditService
    cipher: SecretCipher | None
    sender: Sender  # las pruebas lo sustituyen por un doble que guarda los avisos
    clock: Callable[[], datetime] = utcnow

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        audit: AuditService | None = None,
        sender: Sender | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> MonitoringToolkit:
        return cls(
            settings=settings,
            audit=audit or AuditService(),
            cipher=SecretCipher.from_settings(settings) if settings.master_key else None,
            sender=sender or ChannelSenders(settings),
            clock=clock,
        )

    def link(self, path: str) -> str | None:
        """Enlace absoluto a una pantalla de la plataforma (``SX_PUBLIC_URL``)."""
        base = (self.settings.public_url or "").rstrip("/")
        return f"{base}{path}" if base else None
