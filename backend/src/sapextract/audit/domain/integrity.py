"""Cadena de hash de la auditoría: ``hash = sha256(prev_hash + contenido canónico)``.

Cualquier modificación, borrado o reordenación de un evento rompe la cadena a partir de él.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

GENESIS_HASH = "0" * 64

# Campos que forman parte del contenido protegido, en este orden lógico.
HASHED_FIELDS = (
    "seq",
    "id",
    "occurred_at",
    "actor_type",
    "actor_id",
    "actor_name",
    "action",
    "category",
    "resource_type",
    "resource_id",
    "resource_name",
    "outcome",
    "changes",
    "context",
    "reason",
)


def to_json_compatible(value: Any) -> Any:
    """Normaliza a tipos JSON nativos (lo que devolverá PostgreSQL al leer el JSONB)."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _normalize(name: str, value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if name in ("id", "actor_id", "resource_id") and value is not None:
        return str(value)
    return value


def canonical_payload(event: Mapping[str, Any]) -> bytes:
    """Serialización determinista del contenido protegido de un evento."""
    content = {name: _normalize(name, event.get(name)) for name in HASHED_FIELDS}
    return json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def compute_hash(prev_hash: str, event: Mapping[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(canonical_payload(event))
    return digest.hexdigest()
