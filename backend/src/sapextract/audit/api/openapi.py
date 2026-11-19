"""Declaración de los eventos de auditoría de cada endpoint (``x-sx-audit`` en el OpenAPI).

Una prueba recorre todas las rutas que modifican datos y exige esta declaración.
"""

from __future__ import annotations

from typing import Any

from sapextract.audit.domain.model import ACTIONS

AUDIT_EXTENSION = "x-sx-audit"
AUDIT_EXEMPT_EXTENSION = "x-sx-audit-exempt"


def audited(*actions: str) -> dict[str, Any]:
    unknown = [a for a in actions if a not in ACTIONS]
    if unknown or not actions:
        msg = f"Acciones de auditoría no registradas: {unknown or '(ninguna)'}"
        raise ValueError(msg)
    return {AUDIT_EXTENSION: list(actions)}


def not_audited(reason: str) -> dict[str, Any]:
    """Excepción documentada para operaciones que no cambian nada relevante."""
    return {AUDIT_EXTENSION: [], AUDIT_EXEMPT_EXTENSION: reason}
