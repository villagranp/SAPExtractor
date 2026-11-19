"""Reloj de la aplicación (punto único para poder fijar la hora en pruebas)."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
