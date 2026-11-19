"""TOTP (RFC 6238) para el segundo factor local. Compatible con Microsoft/Google Authenticator."""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
from urllib.parse import quote, urlencode

PERIOD_SECONDS = 30
DIGITS = 6
# Pasos de tolerancia hacia atrás/adelante (desfase de reloj del móvil).
VALID_WINDOW = 1


def generate_secret() -> str:
    """Secreto de 160 bits en Base32 sin relleno."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def time_step(unix_time: float) -> int:
    return int(unix_time // PERIOD_SECONDS)


def code_at(secret: str, step: int, *, digits: int = DIGITS, digest: str = "sha1") -> str:
    """Código para un paso de tiempo (HOTP del RFC 4226 con contador = paso)."""
    mac = hmac.new(_decode(secret), struct.pack(">Q", step), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def match_step(
    secret: str, code: str, unix_time: float, *, last_used_step: int | None
) -> int | None:
    """Devuelve el paso que coincide con ``code`` o ``None``.

    Rechaza la reutilización de un código ya aceptado (``last_used_step``) para impedir
    ataques de repetición dentro de la ventana de validez.
    """
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = time_step(unix_time)
    for step in range(current - VALID_WINDOW, current + VALID_WINDOW + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    params = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{params}"
