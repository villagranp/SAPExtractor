"""Dependencias compartidas por los casos de uso de identidad (una instancia por proceso)."""

from __future__ import annotations

from dataclasses import dataclass

from sapextract.audit.application.service import AuditService
from sapextract.core.config import Settings
from sapextract.core.exceptions import BusinessRuleError
from sapextract.core.security.crypto import SecretCipher
from sapextract.identity.domain.password_policy import PasswordPolicy
from sapextract.identity.infrastructure.security import PasswordHashing


@dataclass(frozen=True, kw_only=True)
class IdentityToolkit:
    settings: Settings
    audit: AuditService
    hashing: PasswordHashing
    policy: PasswordPolicy
    cipher: SecretCipher | None

    @classmethod
    def build(cls, settings: Settings, *, audit: AuditService | None = None) -> IdentityToolkit:
        return cls(
            settings=settings,
            audit=audit or AuditService(),
            hashing=PasswordHashing(),
            policy=PasswordPolicy.build(
                min_length=settings.password_min_length,
                denylist_file=settings.password_denylist_file,
            ),
            cipher=SecretCipher.from_settings(settings) if settings.master_key else None,
        )

    def require_cipher(self) -> SecretCipher:
        if self.cipher is None:
            msg = (
                "La clave maestra de cifrado (SX_MASTER_KEY) no está configurada; "
                "es necesaria para guardar el segundo factor."
            )
            raise BusinessRuleError(msg, code="SX-SEC-NO-MASTER-KEY")
        return self.cipher
