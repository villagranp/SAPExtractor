"""Alertas y avisos (plan 09 §3): reglas, parámetros, estados, mensajes y firma de webhooks.

Sin dependencias de frameworks. Las reglas se evalúan por estado: una alerta está abierta
mientras su condición se cumple y se resuelve sola cuando deja de cumplirse (aviso de
recuperación). Así un incidente es un aviso, no uno por reintento.
"""

from __future__ import annotations

import hashlib
import hmac
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sapextract.core.exceptions import BusinessRuleError, FieldIssue


class AlertType(StrEnum):
    RUN_FAILED = "run_failed"
    DELTA_BROKEN = "delta_broken"
    FRESHNESS = "freshness"
    DURATION_ANOMALY = "duration_anomaly"
    VOLUME_ANOMALY = "volume_anomaly"
    STALE_SUBSCRIPTION = "stale_subscription"
    UNCLOSED_SUBSCRIPTION = "unclosed_subscription"
    CONNECTION_DOWN = "connection_down"
    NO_WORKERS = "no_workers"


class AlertSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


SEVERITY_RANK = {AlertSeverity.INFO: 0, AlertSeverity.WARNING: 1, AlertSeverity.CRITICAL: 2}


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


LIVE_ALERT_STATUSES = (AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value)


class RuleScope(StrEnum):
    GLOBAL = "global"
    PIPELINE = "pipeline"
    TAG = "tag"


class ChannelType(StrEnum):
    EMAIL = "email"
    TEAMS = "teams"
    WEBHOOK = "webhook"


class NotificationEvent(StrEnum):
    OPENED = "opened"
    RECOVERED = "recovered"
    TEST = "test"


# Reglas que solo tienen sentido sobre pipelines (admiten ámbito por pipeline o etiqueta).
PIPELINE_RULES = frozenset(
    {
        AlertType.RUN_FAILED,
        AlertType.DELTA_BROKEN,
        AlertType.FRESHNESS,
        AlertType.DURATION_ANOMALY,
        AlertType.VOLUME_ANOMALY,
        AlertType.STALE_SUBSCRIPTION,
        AlertType.UNCLOSED_SUBSCRIPTION,
    }
)


@dataclass(frozen=True)
class ParamSpec:
    default: float
    minimum: float
    maximum: float
    integer: bool = True


# Parámetros de cada regla: (defecto, mínimo, máximo). H13 fijará el umbral real de la
# suscripción estancada (periodo de recuperación de la ODQ).
PARAMS: dict[AlertType, dict[str, ParamSpec]] = {
    AlertType.FRESHNESS: {"minutes": ParamSpec(240, 5, 10_080)},
    AlertType.DURATION_ANOMALY: {
        "factor": ParamSpec(3, 1.5, 20, integer=False),
        "samples": ParamSpec(5, 3, 50),
    },
    AlertType.VOLUME_ANOMALY: {"zero_runs": ParamSpec(3, 2, 50)},
    AlertType.STALE_SUBSCRIPTION: {"days": ParamSpec(7, 1, 90)},
    AlertType.UNCLOSED_SUBSCRIPTION: {"days": ParamSpec(3, 1, 90)},
}

DEFAULT_SEVERITY: dict[AlertType, AlertSeverity] = {
    AlertType.RUN_FAILED: AlertSeverity.CRITICAL,
    AlertType.DELTA_BROKEN: AlertSeverity.CRITICAL,
    AlertType.FRESHNESS: AlertSeverity.WARNING,
    AlertType.DURATION_ANOMALY: AlertSeverity.WARNING,
    AlertType.VOLUME_ANOMALY: AlertSeverity.WARNING,
    AlertType.STALE_SUBSCRIPTION: AlertSeverity.WARNING,
    AlertType.UNCLOSED_SUBSCRIPTION: AlertSeverity.WARNING,
    AlertType.CONNECTION_DOWN: AlertSeverity.CRITICAL,
    AlertType.NO_WORKERS: AlertSeverity.CRITICAL,
}


def issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def check_params(rule_type: AlertType, params: dict[str, Any] | None) -> dict[str, float | int]:
    """Parámetros normalizados de una regla (los que faltan toman su valor por defecto)."""
    given = dict(params or {})
    specs = PARAMS.get(rule_type, {})
    unknown = sorted(set(given) - set(specs))
    if unknown:
        msg = f"Parámetros que la regla no usa: {', '.join(unknown)}."
        raise issue("params", msg, "SX-ALERT-PARAMS-UNKNOWN")
    result: dict[str, float | int] = {}
    for name, spec in specs.items():
        raw = given.get(name, spec.default)
        try:
            value = float(raw)
        except TypeError, ValueError:
            msg = f"El parámetro {name} debe ser un número."
            raise issue(f"params.{name}", msg, "SX-ALERT-PARAMS-INVALID") from None
        if not spec.minimum <= value <= spec.maximum or (spec.integer and value != int(value)):
            kind = "entero " if spec.integer else ""
            msg = f"{name} debe ser un número {kind}entre {spec.minimum:g} y {spec.maximum:g}."
            raise issue(f"params.{name}", msg, "SX-ALERT-PARAMS-INVALID")
        result[name] = int(value) if spec.integer else value
    return result


def check_scope(rule_type: AlertType, scope: RuleScope, value: str | None) -> str | None:
    if scope is RuleScope.GLOBAL:
        return None
    if rule_type not in PIPELINE_RULES:
        msg = "Esta regla no se refiere a pipelines: su ámbito es global."
        raise issue("scope", msg, "SX-ALERT-SCOPE-INVALID")
    text = (value or "").strip()
    if not text:
        msg = "Indique el pipeline o la etiqueta a los que se aplica la regla."
        raise issue("scope_value", msg, "SX-ALERT-SCOPE-REQUIRED")
    return text


@dataclass(frozen=True, kw_only=True)
class Finding:
    """Una condición que se cumple ahora para un recurso (abre o mantiene una alerta)."""

    resource_type: str
    resource_id: str
    resource_name: str
    message: str
    pipeline_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------- reglas de cálculo
def duration_anomaly(
    latest_seconds: float, previous_seconds: Sequence[float], *, factor: float, samples: int
) -> float | None:
    """Mediana de referencia si el último run tardó más de ``factor`` veces lo habitual."""
    if len(previous_seconds) < samples:
        return None
    median = statistics.median(previous_seconds[:samples])
    if median <= 0:
        return None
    return median if latest_seconds > factor * median else None


def zero_streak(rows: Sequence[int | None]) -> int:
    """Runs seguidos sin filas, empezando por el más reciente."""
    streak = 0
    for value in rows:
        if value:
            break
        streak += 1
    return streak


# ---------------------------------------------------------------------- mensajes
@dataclass(frozen=True, kw_only=True)
class Message:
    title: str
    text: str
    link: str | None


EVENT_PREFIX = {
    NotificationEvent.OPENED: "Alerta",
    NotificationEvent.RECOVERED: "Recuperado",
    NotificationEvent.TEST: "Prueba",
}


def render(
    event: NotificationEvent,
    *,
    rule_name: str,
    severity: AlertSeverity,
    resource_name: str,
    message: str,
    link: str | None,
) -> Message:
    """Texto del aviso. Nunca lleva filas de datos de SAP: solo el error y el recurso."""
    title = f"[SAPExtract] {EVENT_PREFIX[event]}: {rule_name} en {resource_name}"
    if event is NotificationEvent.RECOVERED:
        body = f"{resource_name} vuelve a estar bien. Motivo de la alerta: {message}"
    elif event is NotificationEvent.TEST:
        body = "Mensaje de prueba del canal de avisos de SAPExtract."
    else:
        body = f"Gravedad: {severity.value}. {message}"
    if link:
        body += f"\n{link}"
    return Message(title=title, text=body, link=link)


def sign(secret: str, body: bytes) -> str:
    """Firma HMAC-SHA256 de un webhook genérico: ``sha256=<hex>``."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
