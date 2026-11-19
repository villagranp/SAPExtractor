"""Enmascarado de secretos compartido por logs técnicos y auditoría."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MASK = "***"

# Claves cuyo valor nunca debe aparecer en un log ni en la auditoría.
_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|"
    r"credential|master[_-]?key|connection[_-]?string|dsn|totp|otp_?code)",
    re.IGNORECASE,
)
# Credenciales embebidas en URLs: esquema://usuario:clave@host
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^:/@\s]+):[^@/\s]+@")


def is_sensitive_key(key: str) -> bool:
    return _SENSITIVE_KEY.search(key) is not None


def mask_value(value: Any) -> Any:
    """Enmascara recursivamente: claves sensibles en diccionarios y credenciales en URLs."""
    if isinstance(value, str):
        return _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:" + MASK + "@", value)
    if isinstance(value, Mapping):
        return mask_mapping(value)
    if isinstance(value, list | tuple):
        return type(value)(mask_value(v) for v in value)
    return value


def mask_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (MASK if is_sensitive_key(str(key)) else mask_value(value))
        for key, value in data.items()
    }
