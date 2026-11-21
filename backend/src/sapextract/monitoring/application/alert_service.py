"""Casos de uso de alertas: reglas, canales, suscripciones y gestión de alertas (plan 09 §3)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.db import Database
from sapextract.core.exceptions import ConflictError, DomainError, NotFoundError
from sapextract.core.pagination import PageParams
from sapextract.core.secret_store import SecretStore
from sapextract.identity.domain.model import Principal
from sapextract.monitoring.application.notifier import Event, Notifier
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.monitoring.domain.model import (
    DEFAULT_SEVERITY,
    LIVE_ALERT_STATUSES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    ChannelType,
    NotificationEvent,
    RuleScope,
    check_params,
    check_scope,
    issue,
)
from sapextract.monitoring.infrastructure.models import (
    Alert,
    AlertRule,
    AlertSubscription,
    Notification,
    NotificationChannel,
)
from sapextract.monitoring.infrastructure.senders import check_url

# Falso positivo de los analizadores: es el tipo de secreto, no un secreto.
SECRET_KIND = "notification_channel"  # noqa: S105  # nosec B105
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 20


@dataclass(frozen=True, kw_only=True)
class RuleInput:
    name: str
    type: AlertType
    scope: RuleScope = RuleScope.GLOBAL
    scope_value: str | None = None
    params: dict[str, Any] | None = None
    severity: AlertSeverity | None = None
    enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class ChannelInput:
    name: str
    type: ChannelType
    config: dict[str, Any]
    secret: dict[str, Any] | None = None
    enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class AlertFilters:
    status: AlertStatus | None = None
    live: bool = False
    pipeline_id: uuid.UUID | None = None


class AlertService:
    def __init__(self, session: Session, toolkit: MonitoringToolkit, db: Database) -> None:
        self.session = session
        self.tk = toolkit
        self.db = db

    # ================================================================ alertas
    def search(self, filters: AlertFilters, page: PageParams) -> tuple[list[Alert], int]:
        stmt = select(Alert)
        if filters.live:
            stmt = stmt.where(Alert.status.in_(LIVE_ALERT_STATUSES))
        elif filters.status:
            stmt = stmt.where(Alert.status == filters.status.value)
        if filters.pipeline_id:
            stmt = stmt.where(Alert.pipeline_id == filters.pipeline_id)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.session.scalars(
            stmt.order_by(Alert.last_seen_at.desc()).offset(page.offset).limit(page.page_size)
        )
        return list(rows), total

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(Alert.severity, func.count())
            .where(Alert.status == AlertStatus.OPEN.value)
            .group_by(Alert.severity)
        ).all()
        by_severity = {str(sev): int(n) for sev, n in rows}
        return {"open": sum(by_severity.values()), **by_severity}

    def alert(self, alert_id: uuid.UUID) -> Alert:
        alert = self.session.get(Alert, alert_id)
        if alert is None:
            msg = "La alerta no existe."
            raise NotFoundError(msg, code="SX-ALERT-NOT-FOUND")
        return alert

    def notifications(self, alert_id: uuid.UUID) -> list[Notification]:
        self.alert(alert_id)
        return list(
            self.session.scalars(
                select(Notification)
                .where(Notification.alert_id == alert_id)
                .order_by(Notification.id.desc())
            )
        )

    def acknowledge(self, alert_id: uuid.UUID, actor: Principal, request: RequestInfo) -> Alert:
        alert = self.alert(alert_id)
        if alert.status != AlertStatus.OPEN.value:
            msg = "Solo se reconoce una alerta abierta."
            raise ConflictError(msg, code="SX-ALERT-NOT-OPEN")
        alert.status = AlertStatus.ACKNOWLEDGED.value
        alert.acknowledged_by, alert.acknowledged_at = actor.username, self.tk.clock()
        self._audit(
            "alert.acknowledged", "alert", str(alert.id), alert.resource_name, actor, request
        )
        self.session.commit()
        return alert

    def resolve(
        self, alert_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> Alert:
        """Cierre manual: si la condición sigue, la próxima evaluación abre otra (sin avisar)."""
        alert = self.alert(alert_id)
        if alert.status == AlertStatus.RESOLVED.value:
            msg = "La alerta ya está resuelta."
            raise ConflictError(msg, code="SX-ALERT-RESOLVED")
        alert.status = AlertStatus.RESOLVED.value
        alert.resolved_at, alert.resolved_by = self.tk.clock(), actor.username
        self._audit(
            "alert.resolved",
            "alert",
            str(alert.id),
            alert.resource_name,
            actor,
            request,
            reason=(reason or "").strip() or None,
        )
        self.session.commit()
        return alert

    # ================================================================ reglas
    def rules(self) -> list[AlertRule]:
        return list(self.session.scalars(select(AlertRule).order_by(AlertRule.name)))

    def rule(self, rule_id: uuid.UUID) -> AlertRule:
        rule = self.session.get(AlertRule, rule_id)
        if rule is None:
            msg = "La regla no existe."
            raise NotFoundError(msg, code="SX-ALERT-RULE-NOT-FOUND")
        return rule

    def create_rule(self, data: RuleInput, actor: Principal, request: RequestInfo) -> AlertRule:
        rule = AlertRule(version=1, created_by=actor.username, updated_by=actor.username)
        self._apply_rule(rule, data)
        self.session.add(rule)
        self.session.flush()
        self._audit(
            "alert_rule.created",
            "alert_rule",
            str(rule.id),
            rule.name,
            actor,
            request,
            changes=diff(None, _rule_snapshot(rule)),
        )
        self.session.commit()
        return rule

    def update_rule(
        self, rule_id: uuid.UUID, data: RuleInput, actor: Principal, request: RequestInfo
    ) -> AlertRule:
        rule = self.rule(rule_id)
        before = _rule_snapshot(rule)
        self._apply_rule(rule, data)
        rule.updated_by = actor.username
        self.session.flush()
        changes = diff(before, _rule_snapshot(rule))
        if changes:
            self._audit(
                "alert_rule.updated",
                "alert_rule",
                str(rule.id),
                rule.name,
                actor,
                request,
                changes=changes,
            )
        self.session.commit()
        return rule

    def delete_rule(self, rule_id: uuid.UUID, actor: Principal, request: RequestInfo) -> None:
        rule = self.rule(rule_id)
        self._audit(
            "alert_rule.deleted",
            "alert_rule",
            str(rule.id),
            rule.name,
            actor,
            request,
            changes=diff(_rule_snapshot(rule), None),
        )
        self.session.delete(rule)
        self.session.commit()

    @staticmethod
    def _apply_rule(rule: AlertRule, data: RuleInput) -> None:
        name = data.name.strip()
        if not name:
            raise issue("name", "Campo obligatorio.", "SX-ALERT-REQUIRED")
        rule.name = name
        rule.type = data.type.value
        rule.scope = data.scope.value
        rule.scope_value = check_scope(data.type, data.scope, data.scope_value)
        rule.params = check_params(data.type, data.params)
        rule.severity = (data.severity or DEFAULT_SEVERITY[data.type]).value
        rule.enabled = data.enabled

    # ================================================================ canales
    def channels(self) -> list[NotificationChannel]:
        return list(
            self.session.scalars(
                select(NotificationChannel)
                .where(NotificationChannel.deleted_at.is_(None))
                .order_by(NotificationChannel.name)
            )
        )

    def channel(self, channel_id: uuid.UUID) -> NotificationChannel:
        channel = self.session.get(NotificationChannel, channel_id)
        if channel is None or channel.deleted_at is not None:
            msg = "El canal no existe."
            raise NotFoundError(msg, code="SX-ALERT-CHANNEL-NOT-FOUND")
        return channel

    def create_channel(
        self, data: ChannelInput, actor: Principal, request: RequestInfo
    ) -> NotificationChannel:
        config, secret = self._check_channel(data.type, data.config, data.secret, creating=True)
        channel = NotificationChannel(
            name=_name(data.name),
            type=data.type.value,
            config=config,
            enabled=data.enabled,
            version=1,
            created_by=actor.username,
            updated_by=actor.username,
        )
        if secret:
            stored = SecretStore(self.session, self.tk.cipher).create(
                SECRET_KIND, secret, actor=actor.username
            )
            channel.secret_id = stored.id
        self.session.add(channel)
        self.session.flush()
        self._audit(
            "notification_channel.created",
            "notification_channel",
            str(channel.id),
            channel.name,
            actor,
            request,
            changes=diff(None, _channel_snapshot(channel)),
        )
        self.session.commit()
        return channel

    def update_channel(
        self,
        channel_id: uuid.UUID,
        *,
        name: str | None,
        config: dict[str, Any] | None,
        secret: dict[str, Any] | None,
        enabled: bool | None,
        actor: Principal,
        request: RequestInfo,
    ) -> NotificationChannel:
        channel = self.channel(channel_id)
        before = _channel_snapshot(channel)
        kind = ChannelType(channel.type)
        if name is not None:
            channel.name = _name(name)
        if config is not None or secret is not None:
            checked, new_secret = self._check_channel(
                kind,
                config if config is not None else dict(channel.config),
                secret,
                creating=channel.secret_id is None and secret is None and kind is ChannelType.TEAMS,
            )
            channel.config = checked
            if new_secret:
                store = SecretStore(self.session, self.tk.cipher)
                if channel.secret_id:
                    store.replace(channel.secret_id, new_secret)
                else:
                    channel.secret_id = store.create(
                        SECRET_KIND, new_secret, actor=actor.username
                    ).id
        if enabled is not None:
            channel.enabled = enabled
        channel.updated_by = actor.username
        self.session.flush()
        changes = diff(before, _channel_snapshot(channel))
        if changes or secret:
            self._audit(
                "notification_channel.updated",
                "notification_channel",
                str(channel.id),
                channel.name,
                actor,
                request,
                changes=changes or {"secret": {"before": "***", "after": "***"}},
            )
        self.session.commit()
        return channel

    def delete_channel(self, channel_id: uuid.UUID, actor: Principal, request: RequestInfo) -> None:
        channel = self.channel(channel_id)
        self._audit(
            "notification_channel.deleted",
            "notification_channel",
            str(channel.id),
            channel.name,
            actor,
            request,
            changes=diff(_channel_snapshot(channel), None),
        )
        if channel.secret_id:
            SecretStore(self.session, self.tk.cipher).delete(channel.secret_id)
            channel.secret_id = None
        channel.deleted_at = self.tk.clock()
        channel.enabled = False
        self.session.execute(
            delete(AlertSubscription).where(AlertSubscription.channel_id == channel.id)
        )
        self.session.commit()

    def test_channel(
        self, channel_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> NotificationChannel:
        """Envía un mensaje de prueba y guarda el resultado (sin transacción abierta al enviar)."""
        channel = self.channel(channel_id)
        secret = (
            SecretStore(self.session, self.tk.cipher).read(channel.secret_id)
            if channel.secret_id
            else None
        )
        config = (ChannelType(channel.type), dict(channel.config))
        self.session.commit()
        event = Event(
            alert_id=uuid.UUID(int=0),
            rule_id=uuid.UUID(int=0),
            rule_name="Prueba",
            rule_type="test",
            severity=AlertSeverity.INFO,
            resource_type="notification_channel",
            resource_id=str(channel.id),
            resource_name=channel.name,
            message="Mensaje de prueba",
            event=NotificationEvent.TEST,
            path="/alerts",
        )
        ok, error = Notifier(self.db, self.tk).send(channel.id, config, secret, event)
        channel.last_test_at, channel.last_test_ok, channel.last_test_error = (
            self.tk.clock(),
            ok,
            error,
        )
        self.session.add(
            Notification(
                alert_id=None,
                channel_id=channel.id,
                event=NotificationEvent.TEST.value,
                status="sent" if ok else "failed",
                error=error,
                sent_at=self.tk.clock(),
            )
        )
        self._audit(
            "notification_channel.tested",
            "notification_channel",
            str(channel.id),
            channel.name,
            actor,
            request,
            outcome=Outcome.SUCCESS if ok else Outcome.FAILURE,
            details={"ok": ok, **({"error": error} if error else {})},
        )
        self.session.commit()
        return channel

    def _check_channel(
        self,
        kind: ChannelType,
        config: dict[str, Any],
        secret: dict[str, Any] | None,
        *,
        creating: bool,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        policy = self.tk.settings.alert_host_policy
        if kind is ChannelType.EMAIL:
            recipients = [str(r).strip() for r in config.get("recipients", []) if str(r).strip()]
            if not recipients or len(recipients) > MAX_RECIPIENTS:
                msg = f"Indique de 1 a {MAX_RECIPIENTS} destinatarios."
                raise issue("config.recipients", msg, "SX-ALERT-RECIPIENTS")
            wrong = [r for r in recipients if not EMAIL.match(r)]
            if wrong:
                msg = f"Direcciones no válidas: {', '.join(wrong)}."
                raise issue("config.recipients", msg, "SX-ALERT-RECIPIENTS")
            return {"recipients": sorted(set(recipients))}, None
        if kind is ChannelType.TEAMS:
            url = str((secret or {}).get("url", "")).strip()
            if not url:
                if creating:
                    msg = "Indique la URL del webhook de Teams."
                    raise issue("secret.url", msg, "SX-ALERT-URL-REQUIRED")
                return {}, None
            _url(url, policy, "secret.url")
            return {}, {"url": url}
        url = _url(str(config.get("url", "")), policy, "config.url")
        hmac_secret = str((secret or {}).get("hmac_secret", "")).strip()
        if hmac_secret and len(hmac_secret) < 16:
            msg = "El secreto de firma debe tener al menos 16 caracteres."
            raise issue("secret.hmac_secret", msg, "SX-ALERT-HMAC-SHORT")
        return {"url": url}, ({"hmac_secret": hmac_secret} if hmac_secret else None)

    # ================================================================ suscripciones
    def subscriptions(self) -> list[AlertSubscription]:
        return list(
            self.session.scalars(select(AlertSubscription).order_by(AlertSubscription.created_at))
        )

    def subscribe(
        self,
        channel_id: uuid.UUID,
        rule_id: uuid.UUID | None,
        min_severity: AlertSeverity,
        actor: Principal,
        request: RequestInfo,
    ) -> AlertSubscription:
        channel = self.channel(channel_id)
        rule = self.rule(rule_id) if rule_id else None
        exists = self.session.scalar(
            select(func.count())
            .select_from(AlertSubscription)
            .where(
                AlertSubscription.channel_id == channel_id,
                AlertSubscription.rule_id.is_(None)
                if rule_id is None
                else AlertSubscription.rule_id == rule_id,
            )
        )
        if exists:
            msg = "El canal ya recibe esas alertas."
            raise ConflictError(msg, code="SX-ALERT-SUBSCRIPTION-EXISTS")
        subscription = AlertSubscription(
            channel_id=channel_id,
            rule_id=rule_id,
            min_severity=min_severity.value,
            created_at=self.tk.clock(),
            created_by=actor.username,
        )
        self.session.add(subscription)
        self.session.flush()
        self._audit(
            "alert_subscription.created",
            "alert_subscription",
            str(subscription.id),
            channel.name,
            actor,
            request,
            details={"rule": rule.name if rule else "todas", "min_severity": min_severity.value},
        )
        self.session.commit()
        return subscription

    def unsubscribe(
        self, subscription_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> None:
        subscription = self.session.get(AlertSubscription, subscription_id)
        if subscription is None:
            msg = "La suscripción no existe."
            raise NotFoundError(msg, code="SX-ALERT-SUBSCRIPTION-NOT-FOUND")
        channel = self.session.get(NotificationChannel, subscription.channel_id)
        self._audit(
            "alert_subscription.deleted",
            "alert_subscription",
            str(subscription.id),
            channel.name if channel else None,
            actor,
            request,
        )
        self.session.delete(subscription)
        self.session.commit()

    # ================================================================ utilidades
    def _audit(  # noqa: PLR0917
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        resource_name: str | None,
        actor: Principal,
        request: RequestInfo,
        *,
        changes: dict[str, Any] | None = None,
        reason: str | None = None,
        outcome: Outcome = Outcome.SUCCESS,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                outcome=outcome,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_name=resource_name,
                changes=changes or None,
                reason=reason,
                details=details or {},
            ),
            actor=actor.actor,
            request=request,
        )


def _name(value: str) -> str:
    text = value.strip()
    if not text:
        raise issue("name", "Campo obligatorio.", "SX-ALERT-REQUIRED")
    return text


def _url(value: str, policy: Any, fieldname: str) -> str:
    try:
        return check_url(value, policy)
    except (ValueError, DomainError) as exc:
        detail = exc.detail if isinstance(exc, DomainError) else str(exc)
        raise issue(fieldname, detail, "SX-ALERT-URL-INVALID") from None


def _rule_snapshot(rule: AlertRule) -> dict[str, Any]:
    return snapshot(
        {
            "name": rule.name,
            "type": rule.type,
            "scope": rule.scope,
            "scope_value": rule.scope_value,
            "params": rule.params,
            "severity": rule.severity,
            "enabled": rule.enabled,
        }
    )


def _channel_snapshot(channel: NotificationChannel) -> dict[str, Any]:
    return snapshot(
        {
            "name": channel.name,
            "type": channel.type,
            "config": channel.config,
            "enabled": channel.enabled,
            "has_secret": channel.secret_id is not None,
        }
    )
