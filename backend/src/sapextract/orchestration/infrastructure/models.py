"""Tabla ``pipeline`` (plan 08 §1), reducida a lo que usa la ejecución manual de la It5."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


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
    # Condiciones propias del pipeline; se suman (and) a los filtros por defecto del origen.
    filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
