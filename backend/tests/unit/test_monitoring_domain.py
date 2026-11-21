"""Reglas puras del monitoreo: parámetros, ámbitos, anomalías, mensajes, firma y URL."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from sapextract.core.exceptions import BusinessRuleError, DomainError
from sapextract.core.network import HostPolicy
from sapextract.monitoring.application.dashboard_service import _sla_for
from sapextract.monitoring.domain.model import (
    AlertSeverity,
    AlertType,
    NotificationEvent,
    RuleScope,
    check_params,
    check_scope,
    duration_anomaly,
    render,
    sign,
    zero_streak,
)
from sapextract.monitoring.infrastructure.models import AlertRule
from sapextract.monitoring.infrastructure.senders import check_url
from sapextract.orchestration.infrastructure.models import Pipeline


def test_params_take_defaults_and_validate_ranges() -> None:
    assert check_params(AlertType.FRESHNESS, None) == {"minutes": 240}
    assert check_params(AlertType.DURATION_ANOMALY, {"factor": "2.5"}) == {
        "factor": 2.5,
        "samples": 5,
    }
    assert check_params(AlertType.RUN_FAILED, {}) == {}
    for rule, params, code in (
        (AlertType.FRESHNESS, {"minutes": 1}, "SX-ALERT-PARAMS-INVALID"),
        (AlertType.FRESHNESS, {"minutes": "x"}, "SX-ALERT-PARAMS-INVALID"),
        (AlertType.VOLUME_ANOMALY, {"zero_runs": 2.5}, "SX-ALERT-PARAMS-INVALID"),
        (AlertType.RUN_FAILED, {"minutes": 5}, "SX-ALERT-PARAMS-UNKNOWN"),
    ):
        with pytest.raises(BusinessRuleError) as info:
            check_params(rule, params)
        assert info.value.error_code == code


def test_scope_rules() -> None:
    assert check_scope(AlertType.RUN_FAILED, RuleScope.GLOBAL, "x") is None
    assert check_scope(AlertType.RUN_FAILED, RuleScope.TAG, " finanzas ") == "finanzas"
    with pytest.raises(BusinessRuleError):
        check_scope(AlertType.NO_WORKERS, RuleScope.PIPELINE, "P1")
    with pytest.raises(BusinessRuleError):
        check_scope(AlertType.FRESHNESS, RuleScope.PIPELINE, "  ")


def test_anomalies() -> None:
    assert duration_anomaly(100, [10, 12, 11, 9, 10], factor=3, samples=5) == 10
    assert duration_anomaly(25, [10, 12, 11, 9, 10], factor=3, samples=5) is None
    assert duration_anomaly(100, [10, 12], factor=3, samples=5) is None  # pocos datos
    assert zero_streak([0, None, 0, 5, 0]) == 3
    assert zero_streak([7, 0, 0]) == 0
    assert zero_streak([]) == 0


def test_messages_never_carry_data_and_link_back() -> None:
    opened = render(
        NotificationEvent.OPENED,
        rule_name="Run fallido",
        severity=AlertSeverity.CRITICAL,
        resource_name="FI_GL_CSV",
        message="El último run falló: SX-DST-DOWN.",
        link="https://sx.example/runs/1",
    )
    assert opened.title == "[SAPExtract] Alerta: Run fallido en FI_GL_CSV"
    assert "critical" in opened.text
    assert opened.text.endswith("https://sx.example/runs/1")
    recovered = render(
        NotificationEvent.RECOVERED,
        rule_name="Run fallido",
        severity=AlertSeverity.CRITICAL,
        resource_name="FI_GL_CSV",
        message="x",
        link=None,
    )
    assert recovered.title.startswith("[SAPExtract] Recuperado")
    assert "vuelve a estar bien" in recovered.text


def test_webhook_signature() -> None:
    body = b'{"event":"opened"}'
    expected = hmac.new(b"secreto-de-prueba-1234", body, hashlib.sha256).hexdigest()
    assert sign("secreto-de-prueba-1234", body) == f"sha256={expected}"


def test_webhook_urls_follow_the_host_policy() -> None:
    policy = HostPolicy(allowed=("hooks.example.com",), label="SX_ALERT_ALLOWED_HOSTS")
    assert check_url(" https://hooks.example.com/x ", policy) == "https://hooks.example.com/x"
    with pytest.raises(DomainError):
        check_url("https://otro.example.org/x", policy)
    with pytest.raises(ValueError, match="https"):
        check_url("http://hooks.example.com/x", policy)
    with pytest.raises(ValueError, match="servidor"):
        check_url("ftp://", policy)
    local = HostPolicy(allow_loopback=True)
    assert check_url("http://127.0.0.1:9000/hook", local)


def _rule(scope: RuleScope, value: str | None, minutes: int) -> AlertRule:
    return AlertRule(
        name="SLA",
        type="freshness",
        scope=scope.value,
        scope_value=value,
        params={"minutes": minutes},
        severity="warning",
        enabled=True,
        version=1,
    )


def test_freshness_sla_takes_the_most_specific_rule() -> None:
    pipeline = Pipeline(code="FI_GL", tags=["finanzas"])
    rules = [
        _rule(RuleScope.GLOBAL, None, 600),
        _rule(RuleScope.TAG, "finanzas", 120),
        _rule(RuleScope.PIPELINE, "OTRO", 5),
    ]
    assert _sla_for(pipeline, rules) == 120
    assert _sla_for(pipeline, [*rules, _rule(RuleScope.PIPELINE, "FI_GL", 30)]) == 30
    assert _sla_for(Pipeline(code="X", tags=[]), rules[1:]) is None
