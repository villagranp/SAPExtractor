"""Tablas de orquestación (plan 08 §1): pipeline, programación, cola de runs y workers.

``run`` y ``run_attempt`` siguen el ADR 0006 §5. La cola son los runs en ``queued``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


def _ts(*, nullable: bool = True) -> Mapped[Any]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class ConcurrencyGroup(Base):
    __tablename__ = "concurrency_group"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    max_parallel: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))


class Pipeline(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "pipeline"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source.id", ondelete="RESTRICT"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destination.id", ondelete="RESTRICT"), nullable=False
    )
    extraction_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    load_options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    column_mapping: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    concurrency_group: Mapped[str | None] = mapped_column(
        ForeignKey("concurrency_group.code", ondelete="RESTRICT")
    )
    worker_pool: Mapped[str] = mapped_column(String(30), nullable=False)
    log_level: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    cloned_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline.id", ondelete="SET NULL")
    )
    cross_environment_reason: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = _ts()
    deleted_at: Mapped[datetime | None] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class Schedule(IdMixin, TimestampMixin, Base):
    __tablename__ = "schedule"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String(120))
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(60), nullable=False)
    start_at: Mapped[datetime | None] = _ts()
    end_at: Mapped[datetime | None] = _ts()
    blackout_windows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    misfire_policy: Mapped[str] = mapped_column(String(10), nullable=False)
    next_fire_at: Mapped[datetime | None] = _ts()
    last_fire_at: Mapped[datetime | None] = _ts()
    last_skip_at: Mapped[datetime | None] = _ts()
    last_skip_reason: Mapped[str | None] = mapped_column(String(30))


class Worker(Base):
    __tablename__ = "worker"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    pools: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    # Sin motor registrado (It5) el worker está vivo pero no toma runs.
    has_executor: Mapped[bool] = mapped_column(Boolean, nullable=False)
    current_run_id: Mapped[uuid.UUID | None] = mapped_column()
    started_at: Mapped[datetime] = _ts(nullable=False)
    heartbeat_at: Mapped[datetime] = _ts(nullable=False)


class Run(IdMixin, Base):
    __tablename__ = "run"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(100))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = _ts(nullable=False)
    attempts_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column()
    claimed_at: Mapped[datetime | None] = _ts()
    heartbeat_at: Mapped[datetime | None] = _ts()
    started_at: Mapped[datetime | None] = _ts()
    finished_at: Mapped[datetime | None] = _ts()
    cancel_requested_at: Mapped[datetime | None] = _ts()
    cancel_requested_by: Mapped[str | None] = mapped_column(String(100))
    batch_id: Mapped[str | None] = mapped_column(String(30))
    delta_generation: Mapped[int | None] = mapped_column(Integer)
    start_token: Mapped[str | None] = mapped_column(Text)
    window_start: Mapped[datetime | None] = _ts()
    window_end: Mapped[datetime | None] = _ts()
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_kind: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunAttempt(Base):
    __tablename__ = "run_attempt"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run.id", ondelete="CASCADE"), primary_key=True
    )
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    worker_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = _ts(nullable=False)
    finished_at: Mapped[datetime | None] = _ts()
    outcome: Mapped[str | None] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_kind: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    spool_worker_id: Mapped[uuid.UUID | None] = mapped_column()
