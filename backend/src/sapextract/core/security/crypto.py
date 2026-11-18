"""Cifrado *envelope* de secretos (credenciales SAP, destinos, IdP).

Cada secreto se cifra con una clave de datos (DEK) aleatoria de 256 bits mediante AES-GCM;
la DEK se cifra (*wrap*) con la clave maestra vigente. Formato del token::

    sx1.<key_id>.<b64(nonce_wrap + dek_cifrada)>.<b64(nonce + datos_cifrados)>

Al rotar la clave maestra basta con re-envolver la DEK (``rotate``), sin descifrar
masivamente con claves nuevas; las claves anteriores se mantienen solo para lectura.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sapextract.core.config import Settings

# Prefijo de formato del token cifrado (no es una contraseña).
TOKEN_VERSION = "sx1"  # noqa: S105  # nosec B105
KEY_BYTES = 32
NONCE_BYTES = 12


class CryptoError(Exception):
    """Error al cifrar/descifrar. El mensaje nunca incluye material sensible."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_master_key() -> str:
    """Genera una clave maestra nueva (base64 url-safe, 256 bits)."""
    return _b64e(os.urandom(KEY_BYTES))


def decode_master_key(encoded: str) -> bytes:
    try:
        raw = _b64d(encoded.strip())
    except (binascii.Error, ValueError) as exc:
        msg = "La clave maestra no es base64 válido"
        raise CryptoError(msg) from exc
    if len(raw) != KEY_BYTES:
        msg = f"La clave maestra debe tener {KEY_BYTES} bytes"
        raise CryptoError(msg)
    return raw


class SecretCipher:
    def __init__(self, keys: Mapping[str, bytes], current_key_id: str) -> None:
        if current_key_id not in keys:
            msg = f"No existe la clave maestra '{current_key_id}'"
            raise CryptoError(msg)
        for key_id, key in keys.items():
            if "." in key_id or not key_id:
                msg = "Identificador de clave no válido"
                raise CryptoError(msg)
            if len(key) != KEY_BYTES:
                msg = f"La clave '{key_id}' debe tener {KEY_BYTES} bytes"
                raise CryptoError(msg)
        self._keys = dict(keys)
        self.current_key_id = current_key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> SecretCipher:
        if settings.master_key is None:
            msg = "SX_MASTER_KEY no está configurada"
            raise CryptoError(msg)
        keys = {
            k: decode_master_key(v.get_secret_value())
            for k, v in settings.previous_master_keys.items()
        }
        keys[settings.master_key_id] = decode_master_key(settings.master_key.get_secret_value())
        return cls(keys, settings.master_key_id)

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> str:
        """Cifra ``plaintext``. ``aad`` liga el secreto a su contexto (p. ej. ``secret:<id>``)."""
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        wrapped = self._wrap(dek, self.current_key_id)
        return ".".join(
            (TOKEN_VERSION, self.current_key_id, _b64e(wrapped), _b64e(nonce + ciphertext))
        )

    def decrypt(self, token: str, *, aad: bytes = b"") -> bytes:
        key_id, wrapped, payload = self._parse(token)
        dek = self._unwrap(wrapped, key_id)
        try:
            return AESGCM(dek).decrypt(payload[:NONCE_BYTES], payload[NONCE_BYTES:], aad)
        except InvalidTag as exc:
            msg = "No se pudo descifrar el secreto (datos alterados o contexto incorrecto)"
            raise CryptoError(msg) from exc

    def needs_rotation(self, token: str) -> bool:
        key_id, _, _ = self._parse(token)
        return key_id != self.current_key_id

    def rotate(self, token: str) -> str:
        """Re-envuelve la DEK con la clave maestra vigente (el contenido cifrado no cambia)."""
        key_id, wrapped, payload = self._parse(token)
        if key_id == self.current_key_id:
            return token
        dek = self._unwrap(wrapped, key_id)
        rewrapped = self._wrap(dek, self.current_key_id)
        return ".".join((TOKEN_VERSION, self.current_key_id, _b64e(rewrapped), _b64e(payload)))

    # ------------------------------------------------------------------ internos
    def _wrap(self, dek: bytes, key_id: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(self._keys[key_id]).encrypt(nonce, dek, key_id.encode())

    def _unwrap(self, wrapped: bytes, key_id: str) -> bytes:
        try:
            return AESGCM(self._keys[key_id]).decrypt(
                wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], key_id.encode()
            )
        except InvalidTag as exc:
            msg = "No se pudo descifrar la clave de datos"
            raise CryptoError(msg) from exc

    def _parse(self, token: str) -> tuple[str, bytes, bytes]:
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != TOKEN_VERSION:
            msg = "Formato de secreto cifrado no reconocido"
            raise CryptoError(msg)
        _, key_id, wrapped_b64, payload_b64 = parts
        if key_id not in self._keys:
            msg = f"Clave maestra '{key_id}' no disponible"
            raise CryptoError(msg)
        try:
            wrapped, payload = _b64d(wrapped_b64), _b64d(payload_b64)
        except (binascii.Error, ValueError) as exc:
            msg = "Secreto cifrado corrupto"
            raise CryptoError(msg) from exc
        if len(payload) <= NONCE_BYTES or len(wrapped) <= NONCE_BYTES:
            msg = "Secreto cifrado corrupto"
            raise CryptoError(msg)
        return key_id, wrapped, payload
