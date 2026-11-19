"""Tabla ``audit_event``: solo inserción, particionada por mes y encadenada por hash.

La tabla, sus particiones y el *trigger* que impide modificarla se crean en la migración
``0002_audit``; este modelo solo describe las columnas para el ORM.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Sequence, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import SCHEMA, Base

AUDIT_SEQUENCE = Sequence("audit_event_seq", schema=SCHEMA)


class AuditEventRow(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_occurred_at", "occurred_at"),
        Index("ix_audit_event_id", "id"),
        Index("ix_audit_event_actor_id", "actor_id"),
        Index("ix_audit_event_resource", "resource_type", "resource_id"),
        Index("ix_audit_event_action", "action"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )

    seq: Mapped[int] = mapped_column(BigInteger, AUDIT_SEQUENCE, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    resource_name: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    def hashed_content(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "id": self.id,
            "occurred_at": self.occurred_at,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "action": self.action,
            "category": self.category,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "outcome": self.outcome,
            "changes": self.changes,
            "context": self.context,
            "reason": self.reason,
        }
