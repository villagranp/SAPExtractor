"""Reglas de negocio puras del ciclo de vida de usuarios y roles."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta

from sapextract.core.exceptions import BusinessRuleError, PermissionDeniedError


def ensure_not_self(actor_id: object, target_id: object, action: str) -> None:
    if actor_id == target_id:
        msg = f"No puede {action} su propio usuario."
        raise BusinessRuleError(msg, code="SX-IDN-SELF-ACTION")


def ensure_not_break_glass(is_break_glass: bool, action: str) -> None:
    if is_break_glass:
        msg = f"No se puede {action} al administrador local de emergencia (break-glass)."
        raise BusinessRuleError(msg, code="SX-IDN-BREAK-GLASS")


def ensure_admins_remain(*, target_is_active_admin: bool, other_active_admins: int) -> None:
    """Siempre debe quedar al menos un administrador de plataforma activo."""
    if target_is_active_admin and other_active_admins == 0:
        msg = "Es el último administrador de plataforma activo; asigne el rol a otro usuario antes."
        raise BusinessRuleError(msg, code="SX-IDN-LAST-ADMIN")


def ensure_can_grant(actor_permissions: Collection[str], granted: Collection[str]) -> None:
    """Anti-escalada: nadie concede permisos que no tiene."""
    missing = sorted(set(granted) - set(actor_permissions))
    if missing:
        msg = "No puede conceder permisos que usted no tiene: " + ", ".join(missing)
        raise PermissionDeniedError(msg, code="SX-IDN-ESCALATION")


def lockout_until(
    *,
    failed_attempts: int,
    max_attempts: int,
    base_minutes: int,
    max_minutes: int,
    now: datetime,
) -> datetime | None:
    """Bloqueo temporal con retardo progresivo (se duplica con cada fallo adicional)."""
    if failed_attempts < max_attempts:
        return None
    minutes = min(base_minutes * 2 ** (failed_attempts - max_attempts), max_minutes)
    return now + timedelta(minutes=minutes)
