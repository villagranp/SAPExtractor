"""Tablas del motor: pasos y log de cada run, y estado de delta (plan 06 §3.6, plan 03 §2).

``run`` y ``run_attempt`` son de la orquestación (plan 08); el motor añade aquí lo suyo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import Base, IdMixin


def _ts(*, nullable: bool = True, default_now: bool = False) -> Any:
    return mapped_column(
        DateTime(timezone=True),
        nullable=nullable,
        server_default=func.now() if default_now else None,
    )


class RunStep(IdMixin, Base):
    __tablename__ = "run_step"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = _ts(nullable=False, default_now=True)
    finished_at: Mapped[datetime | None] = _ts()
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class RunLog(Base):
    """Líneas del log de ejecución. Particionada por mes (``ts``); ``id`` es el cursor."""

    __tablename__ = "run_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt: Mapped[int | None] = mapped_column(Integer)
    step: Mapped[str | None] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class DeltaState(Base):
    """Una fila por pipeline DELTA o WATERMARK (borrador del plan 06 §3.6)."""

    __tablename__ = "delta_state"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_token: Mapped[str | None] = mapped_column(Text)
    pending_token: Mapped[str | None] = mapped_column(Text)
    pending_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run.id", ondelete="SET NULL")
    )
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    init_filters_hash: Mapped[str | None] = mapped_column(String(64))
    sap_close_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watermark_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    initialized_at: Mapped[datetime | None] = _ts()
    last_delta_at: Mapped[datetime | None] = _ts()
    updated_at: Mapped[datetime] = _ts(nullable=False, default_now=True)


class DeltaStateHistory(Base):
    __tablename__ = "delta_state_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = _ts(nullable=False, default_now=True)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    actor: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    token_before: Mapped[str | None] = mapped_column(Text)
    token_after: Mapped[str | None] = mapped_column(Text)
