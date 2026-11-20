"""Tablas del módulo de orígenes SAP (plan 05 §1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


class Source(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "source"

    sap_system_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sap_system.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    odp_context: Mapped[str | None] = mapped_column(String(10))
    odp_name: Mapped[str | None] = mapped_column(String(100))
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_version: Mapped[int] = mapped_column(Integer, nullable=False)
    service_path: Mapped[str] = mapped_column(String(300), nullable=False)
    entity_set: Mapped[str] = mapped_column(String(120), nullable=False)
    odata_version: Mapped[str] = mapped_column(String(5), nullable=False)
    supports_delta: Mapped[bool] = mapped_column(Boolean, nullable=False)
    delta_method: Mapped[str] = mapped_column(String(20), nullable=False)
    delta_semantics: Mapped[str | None] = mapped_column(String(20))
    watermark_field: Mapped[str | None] = mapped_column(String(120))
    watermark_kind: Mapped[str | None] = mapped_column(String(20))
    watermark_timezone: Mapped[str | None] = mapped_column(String(60))
    watermark_overlap_seconds: Mapped[int | None] = mapped_column(Integer)
    key_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    default_filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    page_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Vista previa desactivable por origen (H16: $top podría lanzar la extracción completa).
    preview_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    schema_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class SourceSchemaVersion(IdMixin, Base):
    """Instantánea del esquema leído de SAP; una nueva cada vez que cambia su hash."""

    __tablename__ = "source_schema_version"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_source_schema_version_source_version"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_by: Mapped[str | None] = mapped_column(String(100))
