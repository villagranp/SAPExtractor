"""Prueba de conexión por pasos con red y Gateway simulados."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sapextract.core.config import Settings
from sapextract.sap_connections.application.connection_tester import ConnectionTester
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sap_connections.domain.errors import SapDnsError
from sapextract.sap_connections.domain.model import BasicCredentials, ConnectionSpec
from sapextract.sap_connections.domain.testing import StepName, StepStatus
from sapextract.sap_connections.infrastructure.network_probe import ProbeResult
from sapextract.sap_connections.infrastructure.odata_client import RetryPolicy, SapODataClient

Handler = Callable[[httpx.Request], httpx.Response]
SPEC = ConnectionSpec(
    base_url="https://gw.test",
    sap_client="300",
    language="ES",
    credentials=BasicCredentials(username="SXTECH", password="x"),
    timeout_seconds=5,
)


def _ok_probe(days: int = 200) -> Callable[[ConnectionSpec], ProbeResult]:
    def fake(_: ConnectionSpec) -> ProbeResult:
        return ProbeResult(
            host="gw.test",
            port=443,
            addresses=["10.0.0.1"],
            details={"tls": True, "tls_version": "TLSv1.3"},
            certificate_not_after=datetime.now(UTC) + timedelta(days=days),
        )

    return fake


def _toolkit(
    settings: Settings, handler: Handler, probe: Callable[[ConnectionSpec], ProbeResult]
) -> SapToolkit:
    def factory(spec: ConnectionSpec, retry: RetryPolicy) -> SapODataClient:
        return SapODataClient(spec, retry=retry, transport=httpx.MockTransport(handler))

    return SapToolkit.build(settings, client_factory=factory, probe_fn=probe)


def _gateway(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps({"d": {"EntitySets": ["ServiceCollection"]}}),
        headers={
            "content-type": "application/json",
            "sap-server": "true",
            "dataserviceversion": "2.0",
        },
    )


def _statuses(report_steps: tuple[object, ...]) -> dict[str, str]:
    return {s.name.value: s.status.value for s in report_steps}  # type: ignore[attr-defined]


def test_successful_connection_runs_every_step(settings: Settings) -> None:
    report = ConnectionTester(_toolkit(settings, _gateway, _ok_probe())).run(SPEC)
    assert report.ok
    assert _statuses(report.steps) == {
        "network": "ok",
        "authentication": "ok",
        "server_info": "ok",
        "latency": "ok",
    }
    server = next(s for s in report.steps if s.name is StepName.SERVER_INFO)
    assert server.details["headers"]["sap-server"] == "true"
    latency = next(s for s in report.steps if s.name is StepName.LATENCY)
    assert len(latency.details["samples_ms"]) == 3


def test_expiring_server_certificate_is_a_warning(settings: Settings) -> None:
    report = ConnectionTester(_toolkit(settings, _gateway, _ok_probe(days=10))).run(SPEC)
    assert report.ok
    assert report.steps[0].status is StepStatus.WARNING
    assert "Quedan" in report.summary


@pytest.mark.parametrize(("status", "code"), [(401, "SX-SAP-AUTH"), (403, "SX-SAP-FORBIDDEN")])
def test_credential_errors_stop_at_authentication(
    settings: Settings, status: int, code: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    report = ConnectionTester(_toolkit(settings, handler, _ok_probe())).run(SPEC)
    assert not report.ok
    assert report.error_code == code
    assert _statuses(report.steps) == {
        "network": "ok",
        "authentication": "failed",
        "server_info": "skipped",
        "latency": "skipped",
    }


def test_network_errors_are_distinct_from_credential_errors(settings: Settings) -> None:
    def failing_probe(_: ConnectionSpec) -> ProbeResult:
        msg = "No se pudo resolver el nombre «gw.test» (DNS)."
        raise SapDnsError(msg)

    report = ConnectionTester(_toolkit(settings, _gateway, failing_probe)).run(SPEC)
    assert report.error_code == "SX-SAP-DNS"
    assert [s.status for s in report.steps] == [
        StepStatus.FAILED,
        StepStatus.SKIPPED,
        StepStatus.SKIPPED,
        StepStatus.SKIPPED,
    ]


def test_html_answer_is_not_a_gateway(settings: Settings) -> None:
    def portal(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>SSO</html>", headers={"content-type": "text/html"})

    report = ConnectionTester(_toolkit(settings, portal, _ok_probe())).run(SPEC)
    assert report.error_code == "SX-SAP-INVALID-RESPONSE"
    assert _statuses(report.steps)["server_info"] == "failed"
    assert _statuses(report.steps)["latency"] == "skipped"
