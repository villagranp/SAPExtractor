"""Contratos HTTP del panel, las alertas, las reglas y los canales (plan 09 §6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sapextract.monitoring.domain.model import (
    DEFAULT_SEVERITY,
    PARAMS,
    PIPELINE_RULES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    ChannelType,
    RuleScope,
)
from sapextract.monitoring.infrastructure.models import (
    Alert,
    AlertRule,
    AlertSubscription,
    Notification,
    NotificationChannel,
)


# ====================================================================== panel
class RunCounts(BaseModel):
    succeeded: int
    failed: int
    running: int
    queued: int


class LiveRun(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    pipeline_code: str
    mode: str
    status: str
    step: str | None
    rows: int | None = Field(description="Filas leídas de SAP hasta ahora")
    pages: int | None
    created_at: datetime
    started_at: datetime | None
    cancel_requested_at: datetime | None


class FailedRun(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    pipeline_code: str
    mode: str
    error_code: str | None
    error_message: str | None
    finished_at: datetime | None


class WorkerState(BaseModel):
    id: uuid.UUID
    hostname: str
    pools: list[str]
    status: str = Field(description="online, stopping o silent (sin latido reciente)")
    busy: bool
    current_run_id: uuid.UUID | None
    heartbeat_at: datetime


class SystemState(BaseModel):
    id: uuid.UUID
    code: str
    environment: str
    status: str
    last_test_ok: bool | None
    last_test_at: datetime | None
    running: int


class DashboardSummary(BaseModel):
    pipelines_active: int
    runs_24h: RunCounts
    rows_24h: int
    pipelines_broken: int
    success_rate_7d: float | None = Field(description="Entre 0 y 1; null sin runs terminados")
    alerts_open: dict[str, int] = Field(description="Alertas abiertas por gravedad")
    live_runs: list[LiveRun]
    recent_failures: list[FailedRun]
    workers: list[WorkerState]
    sap_systems: list[SystemState]


class TimelineRun(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    pipeline_code: str
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None


class FreshnessRow(BaseModel):
    pipeline_id: uuid.UUID
    pipeline_code: str
    status: str
    last_success_at: datetime | None
    minutes_since: int | None
    sla_minutes: int | None = Field(description="De la regla de frescura que aplica")
    state: str = Field(description="ok, warning, late, no_sla o unknown")


# ====================================================================== alertas
class AlertResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    type: AlertType
    severity: AlertSeverity
    resource_type: str
    resource_id: str
    resource_name: str
    pipeline_id: uuid.UUID | None
    status: AlertStatus
    message: str
    details: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime
    count: int
    notified: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None

    @classmethod
    def of(cls, a: Alert) -> AlertResponse:
        return cls(
            id=a.id,
            rule_id=a.rule_id,
            type=AlertType(a.type),
            severity=AlertSeverity(a.severity),
            resource_type=a.resource_type,
            resource_id=a.resource_id,
            resource_name=a.resource_name,
            pipeline_id=a.pipeline_id,
            status=AlertStatus(a.status),
            message=a.message,
            details=a.details,
            first_seen_at=a.first_seen_at,
            last_seen_at=a.last_seen_at,
            count=a.count,
            notified=a.notified,
            acknowledged_by=a.acknowledged_by,
            acknowledged_at=a.acknowledged_at,
            resolved_at=a.resolved_at,
            resolved_by=a.resolved_by,
        )


class NotificationResponse(BaseModel):
    channel_id: uuid.UUID
    event: str
    status: str
    error: str | None
    sent_at: datetime

    @classmethod
    def of(cls, n: Notification) -> NotificationResponse:
        return cls(
            channel_id=n.channel_id,
            event=n.event,
            status=n.status,
            error=n.error,
            sent_at=n.sent_at,
        )


class AlertDetail(AlertResponse):
    notifications: list[NotificationResponse]


class AlertCounts(BaseModel):
    open: int
    critical: int = 0
    warning: int = 0
    info: int = 0


class ResolveRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


# ====================================================================== reglas
class ParamResponse(BaseModel):
    name: str
    default: float
    minimum: float
    maximum: float
    integer: bool


class RuleTypeResponse(BaseModel):
    type: AlertType
    default_severity: AlertSeverity
    pipeline_scope: bool = Field(description="Admite ámbito por pipeline o etiqueta")
    params: list[ParamResponse]

    @classmethod
    def all(cls) -> list[RuleTypeResponse]:
        return [
            cls(
                type=t,
                default_severity=DEFAULT_SEVERITY[t],
                pipeline_scope=t in PIPELINE_RULES,
                params=[
                    ParamResponse(
                        name=name,
                        default=spec.default,
                        minimum=spec.minimum,
                        maximum=spec.maximum,
                        integer=spec.integer,
                    )
                    for name, spec in PARAMS.get(t, {}).items()
                ],
            )
            for t in AlertType
        ]


class RuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AlertType
    scope: RuleScope = RuleScope.GLOBAL
    scope_value: str | None = Field(None, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    severity: AlertSeverity | None = None
    enabled: bool = True


class RuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: AlertType
    scope: RuleScope
    scope_value: str | None
    params: dict[str, Any]
    severity: AlertSeverity
    enabled: bool
    updated_at: datetime
    updated_by: str | None

    @classmethod
    def of(cls, r: AlertRule) -> RuleResponse:
        return cls(
            id=r.id,
            name=r.name,
            type=AlertType(r.type),
            scope=RuleScope(r.scope),
            scope_value=r.scope_value,
            params=r.params,
            severity=AlertSeverity(r.severity),
            enabled=r.enabled,
            updated_at=r.updated_at,
            updated_by=r.updated_by,
        )


# ====================================================================== canales
SECRET_FIELD = Field(
    None,
    description="URL de Teams o secreto de firma del webhook (solo escritura; nunca se devuelven)",
    json_schema_extra={"writeOnly": True},
)


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: ChannelType
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Correo: {recipients: [...]}. Webhook genérico: {url}. Teams: vacío",
    )
    secret: dict[str, Any] | None = SECRET_FIELD
    enabled: bool = True


class ChannelUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    secret: dict[str, Any] | None = SECRET_FIELD
    enabled: bool | None = None


class ChannelResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: ChannelType
    config: dict[str, Any]
    has_secret: bool
    enabled: bool
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None
    updated_at: datetime

    @classmethod
    def of(cls, c: NotificationChannel) -> ChannelResponse:
        return cls(
            id=c.id,
            name=c.name,
            type=ChannelType(c.type),
            config=c.config,
            has_secret=c.secret_id is not None,
            enabled=c.enabled,
            last_test_at=c.last_test_at,
            last_test_ok=c.last_test_ok,
            last_test_error=c.last_test_error,
            updated_at=c.updated_at,
        )


class AlertSubscriptionRequest(BaseModel):
    channel_id: uuid.UUID
    rule_id: uuid.UUID | None = Field(None, description="null: todas las reglas")
    min_severity: AlertSeverity = AlertSeverity.WARNING


class AlertSubscriptionResponse(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    rule_id: uuid.UUID | None
    min_severity: AlertSeverity
    created_at: datetime
    created_by: str | None

    @classmethod
    def of(cls, s: AlertSubscription) -> AlertSubscriptionResponse:
        return cls(
            id=s.id,
            channel_id=s.channel_id,
            rule_id=s.rule_id,
            min_severity=AlertSeverity(s.min_severity),
            created_at=s.created_at,
            created_by=s.created_by,
        )
