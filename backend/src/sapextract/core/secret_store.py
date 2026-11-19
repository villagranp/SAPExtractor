"""Almacén genérico de secretos cifrados (credenciales SAP, destinos, IdP).

Cada fila guarda un JSON cifrado con :class:`SecretCipher` y ligado a su identificador
(``aad = secret:<id>``): copiar el texto cifrado a otra fila no permite descifrarlo.
El contenido descifrado solo vive en memoria del proceso que lo usa; nunca se devuelve
por la API ni se escribe en logs o auditoría.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from sapextract.core.clock import utcnow
from sapextract.core.db import Base, IdMixin, new_id
from sapextract.core.exceptions import BusinessRuleError, NotFoundError
from sapextract.core.security.crypto import SecretCipher


class Secret(IdMixin, Base):
    __tablename__ = "secret"

    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _aad(secret_id: uuid.UUID) -> bytes:
    return f"secret:{secret_id}".encode()


class SecretStore:
    """Escritura y lectura de secretos dentro de la transacción de ``session``."""

    def __init__(self, session: Session, cipher: SecretCipher | None) -> None:
        self.session = session
        self._cipher = cipher

    @property
    def cipher(self) -> SecretCipher:
        if self._cipher is None:
            msg = (
                "La clave maestra de cifrado (SX_MASTER_KEY) no está configurada; "
                "es necesaria para guardar credenciales."
            )
            raise BusinessRuleError(msg, code="SX-SEC-NO-MASTER-KEY")
        return self._cipher

    def create(self, kind: str, payload: dict[str, Any], *, actor: str | None) -> Secret:
        secret_id = new_id()
        secret = Secret(
            id=secret_id,
            kind=kind,
            ciphertext=self._encrypt(secret_id, payload),
            key_version=self.cipher.current_key_id,
            created_by=actor,
        )
        self.session.add(secret)
        self.session.flush()
        return secret

    def replace(self, secret_id: uuid.UUID, payload: dict[str, Any]) -> Secret:
        secret = self._get(secret_id)
        secret.ciphertext = self._encrypt(secret.id, payload)
        secret.key_version = self.cipher.current_key_id
        secret.rotated_at = utcnow()
        return secret

    def read(self, secret_id: uuid.UUID) -> dict[str, Any]:
        secret = self._get(secret_id)
        raw = self.cipher.decrypt(secret.ciphertext, aad=_aad(secret.id))
        data: dict[str, Any] = json.loads(raw)
        return data

    def delete(self, secret_id: uuid.UUID) -> None:
        secret = self.session.get(Secret, secret_id)
        if secret is not None:
            self.session.delete(secret)

    def _get(self, secret_id: uuid.UUID) -> Secret:
        secret = self.session.get(Secret, secret_id)
        if secret is None:
            msg = "El secreto no existe."
            raise NotFoundError(msg, code="SX-SEC-SECRET-NOT-FOUND")
        return secret

    def _encrypt(self, secret_id: uuid.UUID, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return self.cipher.encrypt(raw, aad=_aad(secret_id))
