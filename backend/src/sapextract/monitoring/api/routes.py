"""Endpoints del panel, las alertas, las reglas, los canales y ``/metrics`` (plan 09 §6)."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from sapextract.audit.api.openapi import audited
from sapextract.core.db import Database
from sapextract.core.dependencies import SessionDep, get_database
from sapextract.core.exceptions import AuthenticationError, NotFoundError
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, require_permission
from sapextract.identity.domain.model import Principal
from sapextract.monitoring.api.schemas import (
    AlertCounts,
    AlertDetail,
    AlertResponse,
    AlertSubscriptionRequest,
    AlertSubscriptionResponse,
    ChannelCreateRequest,
    ChannelResponse,
    ChannelUpdateRequest,
    DashboardSummary,
    FreshnessRow,
    NotificationResponse,
    ResolveRequest,
    RuleRequest,
    RuleResponse,
    RuleTypeResponse,
    TimelineRun,
)
from sapextract.monitoring.application.alert_service import (
    AlertFilters,
    AlertService,
    ChannelInput,
    RuleInput,
)
from sapextract.monitoring.application.dashboard_service import DashboardFilters, DashboardService
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.monitoring.domain.model import AlertStatus

router = APIRouter(tags=["Monitoreo"])


def get_monitoring_toolkit(request: Request) -> MonitoringToolkit:
    toolkit: MonitoringToolkit = request.app.state.monitoring
    return toolkit


ToolkitDep = Annotated[MonitoringToolkit, Depends(get_monitoring_toolkit)]
DatabaseDep = Annotated[Database, Depends(get_database)]
Reader = Annotated[Principal, Depends(require_permission("dashboard:read"))]
Acker = Annotated[Principal, Depends(require_permission("alert:ack"))]
Manager = Annotated[Principal, Depends(require_permission("alert:manage"))]
Paging = Annotated[PageParams, Depends(page_params)]


def _filters(
    environment: Annotated[str | None, Query(max_length=10)] = None,
    sap_system_id: uuid.UUID | None = None,
    tag: Annotated[str | None, Query(max_length=60)] = None,
) -> DashboardFilters:
    return DashboardFilters(environment=environment, sap_system_id=sap_system_id, tag=tag)


Filters = Annotated[DashboardFilters, Depends(_filters)]


def _service(session: SessionDep, toolkit: ToolkitDep, db: DatabaseDep) -> AlertService:
    return AlertService(session, toolkit, db)


Service = Annotated[AlertService, Depends(_service)]


# ====================================================================== panel
@router.get("/dashboard/summary", response_model=DashboardSummary, summary="Panel: resumen")
def dashboard_summary(
    _: Reader, session: SessionDep, toolkit: ToolkitDep, filters: Filters
) -> DashboardSummary:
    service = DashboardService(session, toolkit.settings, toolkit.clock())
    return DashboardSummary.model_validate(service.summary(filters))


@router.get(
    "/dashboard/timeline", response_model=list[TimelineRun], summary="Panel: runs por pipeline"
)
def dashboard_timeline(
    _: Reader,
    session: SessionDep,
    toolkit: ToolkitDep,
    filters: Filters,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> list[TimelineRun]:
    rows = DashboardService(session, toolkit.settings, toolkit.clock()).timeline(filters, hours)
    return [TimelineRun.model_validate(r) for r in rows]


@router.get(
    "/dashboard/freshness",
    response_model=list[FreshnessRow],
    summary="Panel: último dato cargado por pipeline frente a su SLA",
)
def dashboard_freshness(
    _: Reader, session: SessionDep, toolkit: ToolkitDep, filters: Filters
) -> list[FreshnessRow]:
    rows = DashboardService(session, toolkit.settings, toolkit.clock()).freshness(filters)
    return [FreshnessRow.model_validate(r) for r in rows]


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Métricas en formato Prometheus (token Bearer SX_METRICS_TOKEN)",
    include_in_schema=False,
)
def metrics(
    session: SessionDep,
    toolkit: ToolkitDep,
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    token = toolkit.settings.metrics_token
    if token is None or not token.get_secret_value():
        msg = "Las métricas no están habilitadas."
        raise NotFoundError(msg, code="SX-METRICS-DISABLED")
    expected = f"Bearer {token.get_secret_value()}"
    if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
        msg = "Token de métricas no válido."
        raise AuthenticationError(msg, code="SX-METRICS-TOKEN")
    body = DashboardService(session, toolkit.settings, toolkit.clock()).metrics()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


# ====================================================================== alertas
@router.get("/alerts", response_model=Page[AlertResponse], summary="Alertas")
def list_alerts(
    _: Reader,
    service: Service,
    page: Paging,
    status_filter: Annotated[AlertStatus | None, Query(alias="status")] = None,
    live: bool = False,
    pipeline_id: uuid.UUID | None = None,
) -> Page[AlertResponse]:
    items, total = service.search(
        AlertFilters(status=status_filter, live=live, pipeline_id=pipeline_id), page
    )
    return Page[AlertResponse].of([AlertResponse.of(a) for a in items], total, page)


@router.get("/alerts/counts", response_model=AlertCounts, summary="Alertas abiertas (campana)")
def alert_counts(_: Reader, service: Service) -> AlertCounts:
    return AlertCounts.model_validate(service.counts())


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def get_alert(alert_id: uuid.UUID, _: Reader, service: Service) -> AlertDetail:
    alert = service.alert(alert_id)
    return AlertDetail(
        **AlertResponse.of(alert).model_dump(),
        notifications=[NotificationResponse.of(n) for n in service.notifications(alert_id)],
    )


@router.post(
    "/alerts/{alert_id}/ack",
    response_model=AlertResponse,
    summary="Reconocer: alguien se está ocupando",
    openapi_extra=audited("alert.acknowledged"),
)
def acknowledge_alert(
    alert_id: uuid.UUID, actor: Acker, service: Service, info: RequestInfoDep
) -> AlertResponse:
    return AlertResponse.of(service.acknowledge(alert_id, actor, info))


@router.post(
    "/alerts/{alert_id}/resolve",
    response_model=AlertResponse,
    summary="Cerrar a mano (si la condición sigue, la próxima evaluación la reabre)",
    openapi_extra=audited("alert.resolved"),
)
def resolve_alert(
    alert_id: uuid.UUID,
    body: ResolveRequest,
    actor: Acker,
    service: Service,
    info: RequestInfoDep,
) -> AlertResponse:
    return AlertResponse.of(service.resolve(alert_id, body.reason, actor, info))


# ====================================================================== reglas
@router.get("/alert-rule-types", response_model=list[RuleTypeResponse], summary="Tipos de regla")
def rule_types(_: Reader) -> list[RuleTypeResponse]:
    return RuleTypeResponse.all()


@router.get("/alert-rules", response_model=list[RuleResponse], summary="Reglas de alerta")
def list_rules(_: Reader, service: Service) -> list[RuleResponse]:
    return [RuleResponse.of(r) for r in service.rules()]


def _rule_input(body: RuleRequest) -> RuleInput:
    return RuleInput(
        name=body.name,
        type=body.type,
        scope=body.scope,
        scope_value=body.scope_value,
        params=body.params,
        severity=body.severity,
        enabled=body.enabled,
    )


@router.post(
    "/alert-rules",
    response_model=RuleResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=audited("alert_rule.created"),
)
def create_rule(
    body: RuleRequest, actor: Manager, service: Service, info: RequestInfoDep
) -> RuleResponse:
    return RuleResponse.of(service.create_rule(_rule_input(body), actor, info))


@router.put(
    "/alert-rules/{rule_id}",
    response_model=RuleResponse,
    openapi_extra=audited("alert_rule.updated"),
)
def update_rule(
    rule_id: uuid.UUID, body: RuleRequest, actor: Manager, service: Service, info: RequestInfoDep
) -> RuleResponse:
    return RuleResponse.of(service.update_rule(rule_id, _rule_input(body), actor, info))


@router.delete(
    "/alert-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=audited("alert_rule.deleted"),
)
def delete_rule(
    rule_id: uuid.UUID, actor: Manager, service: Service, info: RequestInfoDep
) -> Response:
    service.delete_rule(rule_id, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ====================================================================== canales
@router.get(
    "/notification-channels", response_model=list[ChannelResponse], summary="Canales de aviso"
)
def list_channels(_: Manager, service: Service) -> list[ChannelResponse]:
    return [ChannelResponse.of(c) for c in service.channels()]


@router.post(
    "/notification-channels",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=audited("notification_channel.created"),
)
def create_channel(
    body: ChannelCreateRequest, actor: Manager, service: Service, info: RequestInfoDep
) -> ChannelResponse:
    channel = service.create_channel(
        ChannelInput(
            name=body.name,
            type=body.type,
            config=body.config,
            secret=body.secret,
            enabled=body.enabled,
        ),
        actor,
        info,
    )
    return ChannelResponse.of(channel)


@router.patch(
    "/notification-channels/{channel_id}",
    response_model=ChannelResponse,
    openapi_extra=audited("notification_channel.updated"),
)
def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdateRequest,
    actor: Manager,
    service: Service,
    info: RequestInfoDep,
) -> ChannelResponse:
    channel = service.update_channel(
        channel_id,
        name=body.name,
        config=body.config,
        secret=body.secret,
        enabled=body.enabled,
        actor=actor,
        request=info,
    )
    return ChannelResponse.of(channel)


@router.delete(
    "/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=audited("notification_channel.deleted"),
)
def delete_channel(
    channel_id: uuid.UUID, actor: Manager, service: Service, info: RequestInfoDep
) -> Response:
    service.delete_channel(channel_id, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notification-channels/{channel_id}/test",
    response_model=ChannelResponse,
    summary="Envía un mensaje de prueba y guarda el resultado",
    openapi_extra=audited("notification_channel.tested"),
)
def test_channel(
    channel_id: uuid.UUID, actor: Manager, service: Service, info: RequestInfoDep
) -> ChannelResponse:
    return ChannelResponse.of(service.test_channel(channel_id, actor, info))


# ====================================================================== suscripciones
@router.get("/alert-subscriptions", response_model=list[AlertSubscriptionResponse])
def list_subscriptions(_: Manager, service: Service) -> list[AlertSubscriptionResponse]:
    return [AlertSubscriptionResponse.of(s) for s in service.subscriptions()]


@router.post(
    "/alert-subscriptions",
    response_model=AlertSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=audited("alert_subscription.created"),
)
def create_subscription(
    body: AlertSubscriptionRequest, actor: Manager, service: Service, info: RequestInfoDep
) -> AlertSubscriptionResponse:
    subscription = service.subscribe(body.channel_id, body.rule_id, body.min_severity, actor, info)
    return AlertSubscriptionResponse.of(subscription)


@router.delete(
    "/alert-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=audited("alert_subscription.deleted"),
)
def delete_subscription(
    subscription_id: uuid.UUID, actor: Manager, service: Service, info: RequestInfoDep
) -> Response:
    service.unsubscribe(subscription_id, actor, info)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
