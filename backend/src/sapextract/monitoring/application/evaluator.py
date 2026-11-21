"""Evaluación de las reglas de alerta (plan 09 §3). La llama el scheduler cada pocos segundos.

Cada regla calcula qué recursos cumplen su condición *ahora*. Una alerta se abre la primera vez,
se mantiene mientras se cumple (un aviso por incidente, no por reintento) y se resuelve sola
cuando deja de cumplirse, con aviso de recuperación. Una que reaparece dentro del *cooldown*
de otra igual no vuelve a avisar.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from sapextract.core.db import Database
from sapextract.destinations.infrastructure.models import Destination
from sapextract.extraction.infrastructure.models import DeltaState
from sapextract.monitoring.application.notifier import Event, Notifier
from sapextract.monitoring.application.toolkit import MonitoringToolkit
from sapextract.monitoring.domain.model import (
    LIVE_ALERT_STATUSES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Finding,
    NotificationEvent,
    RuleScope,
    duration_anomaly,
    zero_streak,
)
from sapextract.monitoring.infrastructure.models import Alert, AlertRule
from sapextract.orchestration.domain.model import (
    ExtractionMode,
    PipelineStatus,
    RunStatus,
    WorkerStatus,
)
from sapextract.orchestration.infrastructure.models import Pipeline, Run, Worker
from sapextract.sap_connections.infrastructure.models import SapSystem

log = structlog.get_logger(__name__)

HISTORY = 50  # runs recientes por pipeline que miran las reglas de anomalías
DELTA_MODES = ("DELTA", "DELTA_INIT")


@dataclass(frozen=True, kw_only=True)
class EvaluationResult:
    opened: int = 0
    resolved: int = 0
    notified: int = 0


class AlertEngine:
    def __init__(self, db: Database, toolkit: MonitoringToolkit) -> None:
        self.db = db
        self.tk = toolkit
        self.notifier = Notifier(db, toolkit)

    def evaluate(self) -> EvaluationResult:
        now = self.tk.clock()
        events: list[Event] = []
        opened = resolved = 0
        with self.db.session() as session:
            rules = list(session.scalars(select(AlertRule).where(AlertRule.enabled)))
            for rule in rules:
                findings = {f.resource_id: f for f in self._findings(session, rule, now)}
                o, r, ev = self._reconcile(session, rule, findings, now)
                opened, resolved = opened + o, resolved + r
                events.extend(ev)
            # Reglas desactivadas o borradas: sus alertas vivas se cierran sin aviso.
            stale = session.scalars(
                select(Alert)
                .join(AlertRule, AlertRule.id == Alert.rule_id)
                .where(Alert.status.in_(LIVE_ALERT_STATUSES), AlertRule.enabled.is_(False))
            )
            for alert in stale:
                alert.status, alert.resolved_at = AlertStatus.RESOLVED.value, now
            session.commit()
        notified = self.notifier.dispatch(events)  # fuera de la transacción
        if opened or resolved:
            log.info("alerts_evaluated", opened=opened, resolved=resolved, notified=notified)
        return EvaluationResult(opened=opened, resolved=resolved, notified=notified)

    # ================================================================ conciliación
    def _reconcile(
        self, session: Session, rule: AlertRule, findings: dict[str, Finding], now: datetime
    ) -> tuple[int, int, list[Event]]:
        live = {
            a.resource_id: a
            for a in session.scalars(
                select(Alert).where(Alert.rule_id == rule.id, Alert.status.in_(LIVE_ALERT_STATUSES))
            )
        }
        events: list[Event] = []
        opened = resolved = 0
        cooldown = now - timedelta(minutes=self.tk.settings.alert_cooldown_minutes)
        for resource_id, finding in findings.items():
            alert = live.get(resource_id)
            if alert is not None:
                alert.last_seen_at = now
                alert.message = finding.message
                alert.details = finding.details
                alert.count = int(finding.details.get("count", alert.count))
                continue
            recent = session.scalar(
                select(func.count())
                .select_from(Alert)
                .where(
                    Alert.rule_id == rule.id,
                    Alert.resource_id == resource_id,
                    Alert.resolved_at >= cooldown,
                )
            )
            alert = Alert(
                rule_id=rule.id,
                type=rule.type,
                severity=rule.severity,
                resource_type=finding.resource_type,
                resource_id=resource_id,
                resource_name=finding.resource_name,
                pipeline_id=uuid.UUID(finding.pipeline_id) if finding.pipeline_id else None,
                status=AlertStatus.OPEN.value,
                message=finding.message,
                details=finding.details,
                first_seen_at=now,
                last_seen_at=now,
                count=int(finding.details.get("count", 1)),
                notified=not recent,
            )
            session.add(alert)
            session.flush()
            opened += 1
            if alert.notified:
                events.append(Event.of(alert, rule, NotificationEvent.OPENED))
        for resource_id, alert in live.items():
            if resource_id in findings:
                continue
            alert.status = AlertStatus.RESOLVED.value
            alert.resolved_at = now
            resolved += 1
            if alert.notified:
                events.append(Event.of(alert, rule, NotificationEvent.RECOVERED))
        return opened, resolved, events

    # ================================================================ reglas
    def _findings(  # noqa: PLR0911 - una rama por tipo de regla
        self, session: Session, rule: AlertRule, now: datetime
    ) -> Iterable[Finding]:
        match AlertType(rule.type):
            case AlertType.RUN_FAILED:
                return self._run_failed(session, rule)
            case AlertType.DELTA_BROKEN:
                return self._delta(session, rule, broken=True)
            case AlertType.UNCLOSED_SUBSCRIPTION:
                return self._delta(session, rule, broken=False, now=now)
            case AlertType.STALE_SUBSCRIPTION:
                return self._stale_subscription(session, rule, now)
            case AlertType.FRESHNESS:
                return self._freshness(session, rule, now)
            case AlertType.DURATION_ANOMALY:
                return self._duration(session, rule)
            case AlertType.VOLUME_ANOMALY:
                return self._volume(session, rule)
            case AlertType.CONNECTION_DOWN:
                return self._connections(session)
            case AlertType.NO_WORKERS:
                return self._workers(session, now)

    def _pipelines(self, session: Session, rule: AlertRule) -> list[Pipeline]:
        """Pipelines vivos dentro del ámbito de la regla."""
        stmt = select(Pipeline).where(Pipeline.deleted_at.is_(None))
        scope = RuleScope(rule.scope)
        if scope is RuleScope.PIPELINE and rule.scope_value:
            try:
                stmt = stmt.where(Pipeline.id == uuid.UUID(rule.scope_value))
            except ValueError:
                stmt = stmt.where(Pipeline.code == rule.scope_value.upper())
        elif scope is RuleScope.TAG and rule.scope_value:
            stmt = stmt.where(Pipeline.tags.contains([rule.scope_value]))
        return list(session.scalars(stmt))

    @staticmethod
    def _finding(pipeline: Pipeline, message: str, **details: Any) -> Finding:
        return Finding(
            resource_type="pipeline",
            resource_id=str(pipeline.id),
            resource_name=pipeline.code,
            pipeline_id=str(pipeline.id),
            message=message,
            details=details,
        )

    @staticmethod
    def _recent_runs(session: Session, pipeline_id: uuid.UUID) -> list[Run]:
        return list(
            session.scalars(
                select(Run)
                .where(
                    Run.pipeline_id == pipeline_id,
                    Run.status.in_(
                        [RunStatus.SUCCEEDED.value, RunStatus.FAILED.value]
                    ),  # una cancelación no es ni fallo ni recuperación
                )
                .order_by(Run.finished_at.desc().nulls_last(), Run.created_at.desc())
                .limit(HISTORY)
            )
        )

    def _run_failed(self, session: Session, rule: AlertRule) -> list[Finding]:
        findings = []
        for pipeline in self._pipelines(session, rule):
            runs = self._recent_runs(session, pipeline.id)
            if not runs or runs[0].status != RunStatus.FAILED.value:
                continue
            failed = 0
            for run in runs:
                if run.status != RunStatus.FAILED.value:
                    break
                failed += 1
            last = runs[0]
            findings.append(
                self._finding(
                    pipeline,
                    f"El último run falló: {last.error_code or 'sin código'}. "
                    f"{last.error_message or ''}".strip(),
                    run_id=str(last.id),
                    error_code=last.error_code,
                    count=failed,
                )
            )
        return findings

    def _delta(
        self, session: Session, rule: AlertRule, *, broken: bool, now: datetime | None = None
    ) -> list[Finding]:
        findings = []
        days = int(rule.params.get("days", 3))
        for pipeline in self._pipelines(session, rule):
            state = session.get(DeltaState, pipeline.id)
            if state is None:
                continue
            if broken and state.status == "BROKEN":
                findings.append(
                    self._finding(
                        pipeline,
                        "SAP rechazó el token de delta: reinicialice el delta del pipeline.",
                    )
                )
            elif (
                not broken
                and now is not None
                and state.sap_close_pending
                and state.updated_at <= now - timedelta(days=days)
            ):
                findings.append(
                    self._finding(
                        pipeline,
                        f"La suscripción anterior sigue abierta en SAP desde hace más de {days} "
                        "días: ciérrela en ODQMON y confírmelo en la pestaña Delta.",
                        days=days,
                    )
                )
        return findings

    def _stale_subscription(
        self, session: Session, rule: AlertRule, now: datetime
    ) -> list[Finding]:
        days = int(rule.params.get("days", 7))
        limit = now - timedelta(days=days)
        findings = []
        for pipeline in self._pipelines(session, rule):
            if pipeline.extraction_mode != ExtractionMode.DELTA.value:
                continue
            state = session.get(DeltaState, pipeline.id)
            if state is None or state.status not in ("INITIALIZED", "BROKEN"):
                continue
            last = state.last_delta_at or state.initialized_at
            if last is not None and last < limit:
                findings.append(
                    self._finding(
                        pipeline,
                        f"Ninguna lectura de delta confirmada en {days} días: la cola crece en "
                        "SAP y puede perderse la posibilidad de repetir.",
                        days=days,
                        last_delta_at=last.isoformat(),
                    )
                )
        return findings

    def _freshness(self, session: Session, rule: AlertRule, now: datetime) -> list[Finding]:
        minutes = int(rule.params.get("minutes", 240))
        limit = now - timedelta(minutes=minutes)
        findings = []
        for pipeline in self._pipelines(session, rule):
            if pipeline.status != PipelineStatus.ACTIVE.value:
                continue  # un pipeline en pausa o en borrador no promete frescura
            last_ok = session.scalar(
                select(func.max(Run.finished_at)).where(
                    Run.pipeline_id == pipeline.id, Run.status == RunStatus.SUCCEEDED.value
                )
            )
            since = last_ok or pipeline.activated_at
            if since is not None and since < limit:
                findings.append(
                    self._finding(
                        pipeline,
                        f"Sin carga correcta en los últimos {minutes} minutos.",
                        minutes=minutes,
                        last_success_at=last_ok.isoformat() if last_ok else None,
                    )
                )
        return findings

    def _duration(self, session: Session, rule: AlertRule) -> list[Finding]:
        factor = float(rule.params.get("factor", 3))
        samples = int(rule.params.get("samples", 5))
        findings = []
        for pipeline in self._pipelines(session, rule):
            done = [
                (r.finished_at - r.started_at).total_seconds()
                for r in self._recent_runs(session, pipeline.id)
                if r.status == RunStatus.SUCCEEDED.value and r.started_at and r.finished_at
            ]
            if not done:
                continue
            median = duration_anomaly(done[0], done[1:], factor=factor, samples=samples)
            if median is not None:
                findings.append(
                    self._finding(
                        pipeline,
                        f"El último run tardó {done[0]:.0f} s, más de {factor:g} veces lo "
                        f"habitual ({median:.0f} s).",
                        seconds=round(done[0]),
                        median_seconds=round(median),
                    )
                )
        return findings

    def _volume(self, session: Session, rule: AlertRule) -> list[Finding]:
        zero_runs = int(rule.params.get("zero_runs", 3))
        findings = []
        for pipeline in self._pipelines(session, rule):
            if pipeline.extraction_mode == ExtractionMode.FULL.value:
                continue
            rows = [
                r.rows_extracted
                for r in self._recent_runs(session, pipeline.id)
                if r.status == RunStatus.SUCCEEDED.value and r.mode in (*DELTA_MODES, "WATERMARK")
            ]
            streak = zero_streak(rows)
            if streak >= zero_runs:
                findings.append(
                    self._finding(
                        pipeline,
                        f"{streak} cargas seguidas sin filas: compruebe que SAP sigue "
                        "registrando cambios.",
                        count=streak,
                    )
                )
        return findings

    @staticmethod
    def _connections(session: Session) -> list[Finding]:
        findings = []
        systems = session.scalars(
            select(SapSystem).where(SapSystem.status == "active", SapSystem.last_test_ok.is_(False))
        )
        for system in systems:
            findings.append(
                Finding(
                    resource_type="sap_system",
                    resource_id=str(system.id),
                    resource_name=system.code,
                    message="La última prueba de conexión con el sistema SAP falló.",
                )
            )
        destinations = session.scalars(
            select(Destination).where(
                and_(
                    Destination.status == "active",
                    Destination.deleted_at.is_(None),
                    Destination.last_test_ok.is_(False),
                )
            )
        )
        for destination in destinations:
            findings.append(
                Finding(
                    resource_type="destination",
                    resource_id=str(destination.id),
                    resource_name=destination.code,
                    message="La última prueba del destino falló.",
                )
            )
        return findings

    def _workers(self, session: Session, now: datetime) -> list[Finding]:
        """Hay trabajo en cola y ningún worker vivo con motor que pueda tomarlo."""
        alive = session.scalar(
            select(func.count())
            .select_from(Worker)
            .where(
                Worker.status == WorkerStatus.ONLINE.value,
                Worker.has_executor.is_(True),
                Worker.heartbeat_at >= now - timedelta(seconds=self.tk.settings.run_stale_seconds),
            )
        )
        pending = session.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.status == RunStatus.QUEUED.value, Run.available_at <= now)
        )
        if alive or not pending:
            return []
        return [
            Finding(
                resource_type="platform",
                resource_id="workers",
                resource_name="Workers",
                message=f"Hay {pending} ejecuciones en cola y ningún worker activo para tomarlas.",
                details={"queued": pending},
            )
        ]


def severity_of(value: str) -> AlertSeverity:
    return AlertSeverity(value)
