"""Tablas del módulo de conexiones SAP (plan 04 §1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


class SapSystem(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "sap_system"

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    system_type: Mapped[str] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    sap_client: Mapped[str] = mapped_column(String(3), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # NULL solo tras la baja lógica (las credenciales se destruyen al eliminar).
    credential_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret.id", ondelete="RESTRICT")
    )
    # Datos no sensibles de la credencial (usuario, client_id, sujeto/caducidad del certificado).
    credential_info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tls_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ca_bundle_secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret.id", ondelete="SET NULL")
    )
    ca_bundle_info: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    proxy_url: Mapped[str | None] = mapped_column(String(500))
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrent_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # True cuando cambió la conexión o la credencial después de la última prueba.
    test_outdated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class SapCatalogCache(Base):
    """Última lectura del catálogo de Gateway de un sistema (TTL configurable)."""

    __tablename__ = "sap_catalog_cache"

    sap_system_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sap_system.id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    services: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
