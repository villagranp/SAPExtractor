"""Retención del log de ejecución (plan 03 §4): borra las particiones mensuales vencidas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.model import Actor, AuditEntry

_PARTITION = re.compile(r"^run_log_(\d{4})(\d{2})$")
_PARTITIONS = text(
    """
    SELECT c.relname
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    JOIN pg_namespace n ON n.oid = p.relnamespace
    WHERE n.nspname = 'sx' AND p.relname = 'run_log'
    ORDER BY c.relname
    """
)


@dataclass(frozen=True, kw_only=True)
class RetentionResult:
    cutoff: datetime
    dropped: tuple[str, ...]
    default_rows: int

    @property
    def changed(self) -> bool:
        return bool(self.dropped) or self.default_rows > 0


def _month_end(name: str) -> datetime | None:
    """Fin (exclusivo) del mes de una partición ``run_log_YYYYMM``; ``None`` si no lo es."""
    match = _PARTITION.match(name)
    if match is None:
        return None
    year, month = int(match[1]), int(match[2])
    if not 1 <= month <= 12:
        return None
    return datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)


def apply_run_log_retention(
    session: Session, audit: AuditService, *, days: int, now: datetime
) -> RetentionResult:
    """Borra las particiones cuyo mes entero es anterior a ``now - days``.

    Una partición solo se borra cuando todas sus líneas han vencido: el log se conserva entre
    ``days`` y ``days`` más un mes. Queda auditado como ``system.retention_applied``.
    """
    cutoff = now - timedelta(days=days)
    dropped: list[str] = []
    for name in session.scalars(_PARTITIONS).all():
        end = _month_end(name)
        if end is None or end > cutoff:
            continue
        # ``name`` cumple ``run_log_YYYYMM`` (solo dígitos): se puede interpolar.
        session.execute(text(f'DROP TABLE IF EXISTS sx."{name}"'))  # nosec B608
        dropped.append(name)
    deleted = session.execute(
        text("DELETE FROM sx.run_log_default WHERE ts < :cutoff"), {"cutoff": cutoff}
    )
    default_rows = int(getattr(deleted, "rowcount", 0) or 0)
    result = RetentionResult(cutoff=cutoff, dropped=tuple(dropped), default_rows=default_rows)
    if result.changed:
        audit.record(
            session,
            AuditEntry(
                action="system.retention_applied",
                resource_type="run_log",
                details={
                    "retention_days": days,
                    "cutoff": cutoff.isoformat(),
                    "partitions_dropped": dropped,
                    "default_rows_deleted": default_rows,
                },
            ),
            actor=Actor.system("scheduler"),
        )
    session.commit()
    return result
