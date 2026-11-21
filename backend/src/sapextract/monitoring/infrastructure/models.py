"""Tablas de alertas y avisos (plan 09 §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


def _ts(*, nullable: bool = True) -> Any:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class AlertRule(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "alert_rule"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(100))  # pipeline (id) o etiqueta
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class NotificationChannel(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "notification_channel"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Sin secretos: destinatarios del correo o la URL de un webhook genérico. La URL de Teams
    # lleva su propia credencial y va cifrada con el secreto HMAC del webhook (secret_id).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret.id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_test_at: Mapped[datetime | None] = _ts()
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_error: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class AlertSubscription(IdMixin, Base):
    """Qué alertas van a qué canal: una regla concreta o todas (``rule_id`` nulo)."""

    __tablename__ = "alert_subscription"

    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert_rule.id", ondelete="CASCADE")
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channel.id", ondelete="CASCADE"), nullable=False
    )
    min_severity: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _ts(nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))


class Alert(IdMixin, Base):
    """Un incidente: una regla que se cumple para un recurso, hasta que deja de cumplirse."""

    __tablename__ = "alert"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_rule.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_name: Mapped[str] = mapped_column(String(200), nullable=False)
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = _ts(nullable=False)
    last_seen_at: Mapped[datetime] = _ts(nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    # False: se abrió dentro del cooldown de otra igual y no avisó; tampoco avisa al resolverse.
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))
    acknowledged_at: Mapped[datetime | None] = _ts()
    resolved_at: Mapped[datetime | None] = _ts()
    resolved_by: Mapped[str | None] = mapped_column(String(100))  # nulo: se resolvió sola


class Notification(Base):
    """Cada envío a un canal, con su resultado."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alert.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channel.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # sent | failed
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = _ts(nullable=False)
