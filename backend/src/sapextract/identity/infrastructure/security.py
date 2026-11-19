"""Hash de contraseñas (argon2id) y tokens opacos (sesiones, CSRF, tokens API)."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Falso positivo de los analizadores: no es un secreto.
API_TOKEN_PREFIX = "sxp_"  # noqa: S105  # nosec B105


class PasswordHashing:
    """argon2id con los parámetros por defecto de argon2-cffi (perfil RFC 9106 de baja memoria:
    t=3, m=64 MiB, p=4), por encima del mínimo recomendado por OWASP."""

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError, VerificationError, InvalidHashError:
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

    def dummy_verify(self, password: str) -> None:
        """Gasta el mismo tiempo que una verificación real (evita enumerar usuarios)."""
        self.verify(self._dummy_hash, password)


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Los tokens son aleatorios de 256 bits: SHA-256 basta (no hace falta un KDF lento)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
