"""Monitoreo y alertas de punta a punta: API + PostgreSQL + reglas evaluadas sobre runs reales.

Criterios del plan 09 que se comprueban: un fallo definitivo genera **un** aviso con enlace al
run y otro de recuperación cuando vuelve a ir bien; los avisos no repiten por reintento ni
dentro del *cooldown*; el panel resume el estado y ``/metrics`` exige su token.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from sapextract.extraction.infrastructure.models import DeltaState
from sapextract.monitoring.application.evaluator import AlertEngine
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.monitoring.domain.model import ChannelType
from sapextract.monitoring.infrastructure.models import Alert, NotificationChannel
from sapextract.monitoring.infrastructure.senders import ChannelError, Delivery
from sapextract.orchestration.infrastructure.models import Run, Worker
from tests.integration.conftest import (
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
)
from tests.support.orchestration import FakeClock, OrchEnv

pytestmark = pytest.mark.integration


@dataclass
class Recorder:
    """Canal de pruebas: guarda cada aviso; ``fail`` hace que el siguiente falle."""

    sent: list[tuple[ChannelType, dict[str, Any], dict[str, Any] | None, Delivery]] = field(
        default_factory=list
    )
    fail: bool = False

    def __call__(
        self,
        kind: ChannelType,
        config: dict[str, Any],
        secret: dict[str, Any] | None,
        delivery: Delivery,
    ) -> None:
        if self.fail:
            self.fail = False
            msg = "El webhook respondió HTTP 500."
            raise ChannelError(msg)
        self.sent.append((kind, config, secret, delivery))

    def about(self, resource_id: str) -> list[Delivery]:
        return [d for _, _, _, d in self.sent if d.payload["resource"]["id"] == resource_id]


@dataclass
class Mon:
    orch: OrchEnv
    api: ApiClient
    recorder: Recorder
    toolkit: MonitoringToolkit

    @property
    def clock(self) -> FakeClock:
        return self.orch.clock

    def evaluate(self) -> None:
        AlertEngine(self.orch.db, self.toolkit).evaluate()

    def run(self, pipeline_id: str, status: str, **values: Any) -> uuid.UUID:
        """Run terminado insertado directamente (el motor ya tiene sus propias pruebas)."""
        now = self.clock.now
        with self.orch.db.session() as session:
            run = Run(
                pipeline_id=uuid.UUID(pipeline_id),
                mode=values.pop("mode", "FULL"),
                status=status,
                trigger="manual",
                triggered_by="prueba",
                priority=3,
                available_at=now,
                attempts_budget=3,
                lease_epoch=1,
                created_at=values.pop("created_at", now),
                started_at=values.pop("started_at", now - timedelta(seconds=10)),
                finished_at=values.pop("finished_at", now),
                **values,
            )
            session.add(run)
            session.commit()
            return run.id

    def alerts(self, resource_id: str, rule_type: str) -> list[Alert]:
        with self.orch.db.session() as session:
            return list(
                session.scalars(
                    select(Alert)
                    .where(Alert.resource_id == resource_id, Alert.type == rule_type)
                    .order_by(Alert.first_seen_at)
                )
            )

    def channel(self, **extra: Any) -> dict[str, Any]:
        body = {
            "name": f"Equipo de datos {uuid.uuid4().hex[:6]}",
            "type": "webhook",
            "config": {"url": "http://127.0.0.1:9/hook"},
            "secret": {"hmac_secret": "secreto-de-firma-0001"},
            **extra,
        }
        response = self.api.post("/notification-channels", body)
        assert response.status_code == 201, response.text
        channel: dict[str, Any] = response.json()
        return channel

    def subscribe(self, channel_id: str, **extra: Any) -> dict[str, Any]:
        response = self.api.post(
            "/alert-subscriptions", {"channel_id": channel_id, "min_severity": "info", **extra}
        )
        assert response.status_code == 201, response.text
        subscription: dict[str, Any] = response.json()
        return subscription


@pytest.fixture
def mon(orch: OrchEnv, orch_admin: ApiClient) -> Iterator[Mon]:
    recorder = Recorder()
    previous = orch.app.state.monitoring
    with orch.db.session() as session:  # los canales de otras pruebas no reciben estos avisos
        session.execute(update(NotificationChannel).values(enabled=False))
        session.commit()
    toolkit = MonitoringToolkit.build(
        orch.toolkit.settings,
        audit=orch.app.state.identity.audit,
        sender=recorder,
        clock=orch.clock,
    )
    orch.app.state.monitoring = toolkit
    yield Mon(orch=orch, api=orch_admin, recorder=recorder, toolkit=toolkit)
    orch.app.state.monitoring = previous


# ====================================================================== avisos
def test_failed_run_notifies_once_and_then_recovers(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api)
    channel = mon.channel()
    mon.subscribe(channel["id"])

    first = mon.run(pipeline["id"], "failed", error_code="SX-DST-DOWN", error_message="Caído")
    mon.evaluate()
    opened = mon.recorder.about(pipeline["id"])
    assert len(opened) == 1
    assert opened[0].payload["event"] == "opened"
    assert opened[0].payload["severity"] == "critical"
    assert opened[0].payload["link"] is None or str(first) in opened[0].payload["link"]
    assert "SX-DST-DOWN" in opened[0].message.text
    [alert] = mon.alerts(pipeline["id"], "run_failed")
    assert (alert.status, alert.count, alert.details["run_id"]) == ("open", 1, str(first))

    # Otro fallo y otra evaluación: el mismo incidente, sin nuevo aviso.
    mon.clock.advance(minutes=5)
    mon.run(pipeline["id"], "failed", error_code="SX-DST-DOWN")
    mon.evaluate()
    mon.evaluate()
    assert len(mon.recorder.about(pipeline["id"])) == 1
    [alert] = mon.alerts(pipeline["id"], "run_failed")
    assert alert.count == 2

    # Un run correcto lo resuelve y avisa de la recuperación.
    mon.clock.advance(minutes=5)
    mon.run(pipeline["id"], "succeeded", rows_extracted=10)
    mon.evaluate()
    events = [d.payload["event"] for d in mon.recorder.about(pipeline["id"])]
    assert events == ["opened", "recovered"]
    [alert] = mon.alerts(pipeline["id"], "run_failed")
    assert alert.status == "resolved"
    assert alert.resolved_by is None  # se resolvió sola

    # Vuelve a fallar dentro del cooldown: se abre otra alerta, pero no avisa (ni al resolverse).
    mon.clock.advance(minutes=1)
    mon.run(pipeline["id"], "failed", error_code="SX-DST-DOWN")
    mon.evaluate()
    alerts = mon.alerts(pipeline["id"], "run_failed")
    assert [a.status for a in alerts] == ["resolved", "open"]
    assert alerts[1].notified is False
    assert len(mon.recorder.about(pipeline["id"])) == 2

    response = mon.api.get(f"/alerts/{alert.id}")
    assert response.status_code == 200
    assert [n["event"] for n in response.json()["notifications"]] == ["recovered", "opened"]


def test_ack_resolve_and_counts(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api)
    mon.run(pipeline["id"], "failed", error_code="SX-X")
    mon.evaluate()
    [alert] = mon.alerts(pipeline["id"], "run_failed")
    counts = mon.api.get("/alerts/counts").json()
    assert counts["open"] >= 1
    assert counts["critical"] >= 1

    live = mon.api.get("/alerts", params={"live": True, "pipeline_id": pipeline["id"]}).json()
    assert [a["id"] for a in live["items"]] == [str(alert.id)]

    ack = mon.api.post(f"/alerts/{alert.id}/ack")
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "acknowledged"
    assert mon.api.post(f"/alerts/{alert.id}/ack").status_code == 409
    resolved = mon.api.post(f"/alerts/{alert.id}/resolve", {"reason": "Revisado"})
    assert resolved.status_code == 200
    assert resolved.json()["resolved_by"] is not None
    actions = {e["action"] for e in audit_actions(mon.api, resource_id=str(alert.id))}
    assert {"alert.acknowledged", "alert.resolved"} <= actions

    # La condición sigue: la siguiente evaluación abre otra alerta (sin avisar, cooldown).
    mon.evaluate()
    assert [a.status for a in mon.alerts(pipeline["id"], "run_failed")] == ["resolved", "open"]


def test_failed_channel_is_recorded_and_severity_filters(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api, mode="DELTA", load_mode="append")
    loud = mon.channel()
    mon.subscribe(loud["id"])
    quiet = mon.channel()
    mon.subscribe(quiet["id"], min_severity="critical")
    rule = mon.api.post(
        "/alert-rules",
        {
            "name": "Volumen",
            "type": "volume_anomaly",
            "scope": "pipeline",
            "scope_value": pipeline["id"],
            "params": {"zero_runs": 2},
        },
    ).json()
    assert rule["severity"] == "warning"
    mon.run(pipeline["id"], "succeeded", mode="DELTA", rows_extracted=0)
    mon.run(pipeline["id"], "succeeded", mode="DELTA", rows_extracted=0)
    mon.recorder.fail = True
    mon.evaluate()
    [alert] = mon.alerts(pipeline["id"], "volume_anomaly")
    assert alert.count == 2
    notes = mon.api.get(f"/alerts/{alert.id}").json()["notifications"]
    # Solo el canal sin umbral recibe un aviso de gravedad «warning», y su envío falló.
    assert [(n["channel_id"], n["status"]) for n in notes] == [(loud["id"], "failed")]
    assert "HTTP 500" in notes[0]["error"]


# ====================================================================== reglas de estado
def test_pipeline_state_rules(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api, mode="DELTA", load_mode="append")
    pid = uuid.UUID(pipeline["id"])
    now = mon.clock.now
    with mon.orch.db.session() as session:
        session.add(
            DeltaState(
                pipeline_id=pid,
                status="INITIALIZED",
                generation=1,
                current_token="D1",
                lease_epoch=1,
                sap_close_pending=True,
                initialized_at=now - timedelta(days=10),
                last_delta_at=now - timedelta(days=8),
                updated_at=now - timedelta(days=4),
            )
        )
        session.commit()
    mon.evaluate()
    assert len(mon.alerts(pipeline["id"], "stale_subscription")) == 1
    assert len(mon.alerts(pipeline["id"], "unclosed_subscription")) == 1
    assert mon.alerts(pipeline["id"], "delta_broken") == []

    with mon.orch.db.session() as session:
        state = session.get(DeltaState, pid)
        assert state is not None
        state.status, state.sap_close_pending, state.last_delta_at = "BROKEN", False, now
        session.commit()
    mon.evaluate()
    assert [a.status for a in mon.alerts(pipeline["id"], "delta_broken")] == ["open"]
    assert [a.status for a in mon.alerts(pipeline["id"], "unclosed_subscription")] == ["resolved"]
    assert [a.status for a in mon.alerts(pipeline["id"], "stale_subscription")] == ["resolved"]


def test_freshness_and_duration_rules(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api, tags=["finanzas"])
    for rule in (
        {
            "name": "SLA finanzas",
            "type": "freshness",
            "scope": "tag",
            "scope_value": "finanzas",
            "params": {"minutes": 60},
        },
        {
            "name": "Lento",
            "type": "duration_anomaly",
            "scope": "pipeline",
            "scope_value": pipeline["id"],
            "params": {"factor": 2, "samples": 3},
        },
    ):
        assert mon.api.post("/alert-rules", rule).status_code == 201
    start = mon.clock.now
    for i in range(3):
        mon.run(
            pipeline["id"],
            "succeeded",
            started_at=start - timedelta(seconds=10),
            finished_at=start,
            created_at=start - timedelta(minutes=10 - i),
        )
    mon.clock.advance(minutes=1)
    mon.run(
        pipeline["id"],
        "succeeded",
        started_at=mon.clock.now - timedelta(seconds=60),
        finished_at=mon.clock.now,
    )
    mon.evaluate()
    [slow] = mon.alerts(pipeline["id"], "duration_anomaly")
    assert slow.details == {"seconds": 60, "median_seconds": 10}
    assert mon.alerts(pipeline["id"], "freshness") == []

    mon.clock.advance(minutes=90)
    mon.evaluate()
    [late] = mon.alerts(pipeline["id"], "freshness")
    assert late.status == "open"
    fresh = mon.api.get("/dashboard/freshness", params={"tag": "finanzas"}).json()
    row = next(r for r in fresh if r["pipeline_id"] == pipeline["id"])
    assert (row["state"], row["sla_minutes"]) == ("late", 60)


def test_no_workers_rule(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api)
    with mon.orch.db.session() as session:
        session.add(
            Run(
                pipeline_id=uuid.UUID(pipeline["id"]),
                mode="FULL",
                status="queued",
                trigger="manual",
                priority=3,
                available_at=mon.clock.now - timedelta(minutes=1),
                attempts_budget=1,
                lease_epoch=0,
                created_at=mon.clock.now,
            )
        )
        session.commit()
    mon.evaluate()
    assert [a.status for a in mon.alerts("workers", "no_workers")][-1] == "open"
    with mon.orch.db.session() as session:
        session.add(
            Worker(
                id=uuid.uuid4(),
                hostname="prueba",
                pid=1,
                pools=["default"],
                status="online",
                has_executor=True,
                started_at=mon.clock.now,
                heartbeat_at=mon.clock.now,
            )
        )
        session.commit()
    mon.evaluate()
    assert mon.alerts("workers", "no_workers")[-1].status == "resolved"


# ====================================================================== reglas y canales
def test_rules_channels_and_subscriptions_api(mon: Mon) -> None:
    types = {t["type"]: t for t in mon.api.get("/alert-rule-types").json()}
    assert types["freshness"]["params"][0]["name"] == "minutes"
    assert types["no_workers"]["pipeline_scope"] is False
    defaults = {r["type"] for r in mon.api.get("/alert-rules").json()}
    assert {"run_failed", "delta_broken", "stale_subscription", "no_workers"} <= defaults

    bad = mon.api.post("/alert-rules", {"name": "X", "type": "freshness", "params": {"minutes": 1}})
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["field"] == "params.minutes"
    rule = mon.api.post("/alert-rules", {"name": "SLA", "type": "freshness"}).json()
    updated = mon.api.put(
        f"/alert-rules/{rule['id']}",
        {"name": "SLA", "type": "freshness", "params": {"minutes": 30}, "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["params"] == {"minutes": 30}

    email = mon.channel(type="email", config={"recipients": ["datos@example.com"]}, secret=None)
    assert email["has_secret"] is False
    wrong = mon.api.post(
        "/notification-channels",
        {"name": "Mal", "type": "email", "config": {"recipients": ["no-es-correo"]}},
    )
    assert wrong.status_code == 422
    teams_without_url = mon.api.post("/notification-channels", {"name": "T", "type": "teams"})
    assert teams_without_url.json()["errors"][0]["field"] == "secret.url"
    teams = mon.channel(type="teams", config={}, secret={"url": "http://127.0.0.1:9/teams"})
    assert teams["has_secret"] is True
    assert "127.0.0.1" not in str(teams)  # la URL de Teams es un secreto: no vuelve

    tested = mon.api.post(f"/notification-channels/{teams['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["last_test_ok"] is True
    kind, _, secret, delivery = mon.recorder.sent[-1]
    assert kind is ChannelType.TEAMS
    assert secret == {"url": "http://127.0.0.1:9/teams"}
    assert delivery.payload["event"] == "test"

    subscription = mon.subscribe(email["id"], rule_id=rule["id"])
    dup = mon.api.post("/alert-subscriptions", {"channel_id": email["id"], "rule_id": rule["id"]})
    assert dup.status_code == 409
    assert mon.api.delete(f"/alert-subscriptions/{subscription['id']}").status_code == 204
    assert mon.api.delete(f"/notification-channels/{teams['id']}").status_code == 204
    assert mon.api.delete(f"/alert-rules/{rule['id']}").status_code == 204
    actions = {e["action"] for e in audit_actions(mon.api, resource_id=teams["id"])}
    assert {"notification_channel.created", "notification_channel.tested"} <= actions


def test_webhook_hosts_are_restricted(mon: Mon) -> None:
    settings = mon.orch.app.state.settings.model_copy(
        update={"alert_allowed_hosts": ["hooks.example.com"], "environment": "qa"}
    )
    toolkit = replace(mon.toolkit, settings=settings)
    mon.orch.app.state.monitoring = toolkit
    blocked = mon.api.post(
        "/notification-channels",
        {"name": "Fuera", "type": "webhook", "config": {"url": "https://evil.example.org/x"}},
    )
    assert blocked.status_code == 422
    assert blocked.json()["errors"][0]["field"] == "config.url"


# ====================================================================== panel y métricas
def test_dashboard_and_metrics(mon: Mon) -> None:
    pipeline = mon.orch.pipeline(mon.api)
    mon.run(pipeline["id"], "succeeded", rows_extracted=1234)
    mon.run(pipeline["id"], "failed", error_code="SX-SAP-TIMEOUT", error_message="SAP no respondió")
    summary = mon.api.get("/dashboard/summary")
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["rows_24h"] >= 1234
    assert data["runs_24h"]["failed"] >= 1
    assert any(f["pipeline_id"] == pipeline["id"] for f in data["recent_failures"])
    assert 0 <= data["success_rate_7d"] <= 1
    timeline = mon.api.get("/dashboard/timeline", params={"hours": 2}).json()
    assert sum(1 for r in timeline if r["pipeline_id"] == pipeline["id"]) == 2

    disabled = mon.api.get("/metrics")
    assert disabled.status_code == 404
    settings = mon.orch.app.state.settings.model_copy(
        update={"metrics_token": SecretStr("token-de-metricas")}
    )
    mon.orch.app.state.monitoring = replace(mon.toolkit, settings=settings)
    assert mon.api.get("/metrics").status_code == 401
    metrics = mon.api.get("/metrics", headers={"Authorization": "Bearer token-de-metricas"})
    assert metrics.status_code == 200
    assert "sx_queue_pending" in metrics.text
    assert 'sx_runs{status="failed"}' in metrics.text


def test_viewer_sees_dashboard_but_cannot_manage(mon: Mon) -> None:
    user = create_user(mon.api, "viewer")
    viewer = ApiClient(mon.orch.app)
    activate_user(viewer, user["user"]["username"], user["temporary_password"])
    assert viewer.get("/dashboard/summary").status_code == 200
    assert viewer.get("/alerts").status_code == 200
    assert viewer.get("/notification-channels").status_code == 403
    assert viewer.post("/alert-rules", {"name": "X", "type": "run_failed"}).status_code == 403
