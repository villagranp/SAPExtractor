"""Tareas de mantenimiento de la auditoría ejecutadas por el scheduler."""

from __future__ import annotations

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

PARTITION_MONTHS_AHEAD = 3


def ensure_partitions(
    conn: Connection | Session, months_ahead: int = PARTITION_MONTHS_AHEAD
) -> int:
    """Crea las particiones mensuales que falten (actual + ``months_ahead``)."""
    created = conn.execute(
        text("SELECT sx.audit_ensure_partitions(:m)"), {"m": months_ahead}
    ).scalar()
    return int(created or 0)
