"""Tabla de destinos (plan 07 §1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


class Destination(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "destination"

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    # Configuración NO sensible, validada por el modelo Pydantic del plugin.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret.id", ondelete="RESTRICT")
    )
    credential_info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    test_outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
