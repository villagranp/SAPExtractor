"""Cálculo de diferencias antes/después para la auditoría, con enmascarado de secretos."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from sapextract.core.masking import MASK, is_sensitive_key, mask_value


def diff(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    sensitive: Collection[str] = (),
) -> dict[str, dict[str, Any]]:
    """Devuelve ``{campo: {"before": x, "after": y}}`` solo con los campos que cambian.

    Los campos sensibles (declarados o detectados por nombre) se registran como ``"***"``:
    queda constancia de que cambiaron, nunca de su valor.
    """
    before = before or {}
    after = after or {}
    changes: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key in sensitive or is_sensitive_key(key):
            changes[key] = {
                "before": MASK if old is not None else None,
                "after": MASK if new is not None else None,
            }
        else:
            changes[key] = {"before": mask_value(old), "after": mask_value(new)}
    return changes


def snapshot(data: Mapping[str, Any], *, sensitive: Collection[str] = ()) -> dict[str, Any]:
    """Estado completo de una entidad nueva o eliminada (con secretos enmascarados)."""
    return {
        key: (MASK if key in sensitive or is_sensitive_key(key) else mask_value(value))
        for key, value in data.items()
    }
