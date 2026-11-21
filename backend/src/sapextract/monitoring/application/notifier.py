"""Reparto de avisos a los canales suscritos y registro de cada envío (plan 09 §3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import or_, select

from sapextract.core.db import Database
from sapextract.core.secret_store import SecretStore
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.monitoring.domain.model import (
    SEVERITY_RANK,
    AlertSeverity,
    ChannelType,
    NotificationEvent,
    render,
)
from sapextract.monitoring.infrastructure.models import (
    Alert,
    AlertRule,
    AlertSubscription,
    Notification,
    NotificationChannel,
)
from sapextract.monitoring.infrastructure.senders import ChannelError, Delivery

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class Event:
    """Una alerta que se abrió o se resolvió y hay que avisar."""

    alert_id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str
    rule_type: str
    severity: AlertSeverity
    resource_type: str
    resource_id: str
    resource_name: str
    message: str
    event: NotificationEvent
    path: str  # pantalla de la plataforma a la que lleva el enlace

    @classmethod
    def of(cls, alert: Alert, rule: AlertRule, event: NotificationEvent) -> Event:
        run_id = alert.details.get("run_id")
        if run_id:
            path = f"/runs/{run_id}"
        elif alert.pipeline_id:
            path = f"/pipelines/{alert.pipeline_id}"
        else:
            path = "/alerts"
        return cls(
            alert_id=alert.id,
            rule_id=rule.id,
            rule_name=rule.name,
            rule_type=rule.type,
            severity=AlertSeverity(alert.severity),
            resource_type=alert.resource_type,
            resource_id=alert.resource_id,
            resource_name=alert.resource_name,
            message=alert.message,
            event=event,
            path=path,
        )


class Notifier:
    def __init__(self, db: Database, toolkit: MonitoringToolkit) -> None:
        self.db = db
        self.tk = toolkit

    def dispatch(self, events: list[Event]) -> int:
        """Envía cada evento a sus canales y guarda el resultado. Devuelve los envíos correctos."""
        sent = 0
        for event in events:
            for channel, config, secret in self._targets(event):
                ok, error = self.send(channel, config, secret, event)
                sent += int(ok)
                with self.db.session() as session:
                    session.add(
                        Notification(
                            alert_id=event.alert_id,
                            channel_id=channel,
                            event=event.event.value,
                            status="sent" if ok else "failed",
                            error=error,
                            sent_at=self.tk.clock(),
                        )
                    )
                    session.commit()
        return sent

    def send(
        self,
        channel_id: uuid.UUID,
        config: tuple[ChannelType, dict[str, Any]],
        secret: dict[str, Any] | None,
        event: Event,
    ) -> tuple[bool, str | None]:
        kind, values = config
        link = self.tk.link(event.path)
        message = render(
            event.event,
            rule_name=event.rule_name,
            severity=event.severity,
            resource_name=event.resource_name,
            message=event.message,
            link=link,
        )
        payload = {
            "event": event.event.value,
            "alert_id": str(event.alert_id),
            "rule": {"id": str(event.rule_id), "name": event.rule_name, "type": event.rule_type},
            "severity": event.severity.value,
            "resource": {
                "type": event.resource_type,
                "id": event.resource_id,
                "name": event.resource_name,
            },
            "message": event.message,
            "link": link,
        }
        try:
            self.tk.sender(kind, values, secret, Delivery(message=message, payload=payload))
        except ChannelError as exc:
            log.warning("notification_failed", channel_id=str(channel_id), error=str(exc))
            return False, str(exc)[:300]
        return True, None

    def _targets(
        self, event: Event
    ) -> list[tuple[uuid.UUID, tuple[ChannelType, dict[str, Any]], dict[str, Any] | None]]:
        rank = SEVERITY_RANK[event.severity]
        with self.db.session() as session:
            rows = session.execute(
                select(AlertSubscription, NotificationChannel)
                .join(NotificationChannel, NotificationChannel.id == AlertSubscription.channel_id)
                .where(
                    or_(
                        AlertSubscription.rule_id.is_(None),
                        AlertSubscription.rule_id == event.rule_id,
                    ),
                    NotificationChannel.enabled.is_(True),
                    NotificationChannel.deleted_at.is_(None),
                )
            ).all()
            targets: dict[uuid.UUID, Any] = {}
            store = SecretStore(session, self.tk.cipher)
            for subscription, channel in rows:
                if SEVERITY_RANK[AlertSeverity(subscription.min_severity)] > rank:
                    continue
                if channel.id in targets:  # suscrito por regla y global: un solo aviso
                    continue
                secret = store.read(channel.secret_id) if channel.secret_id else None
                targets[channel.id] = ((ChannelType(channel.type), dict(channel.config)), secret)
        return [(cid, cfg, secret) for cid, (cfg, secret) in targets.items()]
