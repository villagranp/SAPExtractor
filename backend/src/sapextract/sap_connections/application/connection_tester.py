"""Prueba de conexión por pasos (plan 04 §2.2): red/TLS → autenticación → servidor → latencia.

Cada paso se ejecuta solo si el anterior no falló; los errores se traducen a mensajes
claros que distinguen credenciales (401/403) de problemas de red, DNS, TLS o timeout.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sapextract.core.clock import utcnow
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sap_connections.domain.errors import SapError, SapResponseError
from sapextract.sap_connections.domain.model import ConnectionSpec, X509Credentials
from sapextract.sap_connections.domain.testing import (
    STEP_ORDER,
    StepName,
    StepStatus,
    TestReport,
    TestStep,
)
from sapextract.sap_connections.infrastructure.certificates import inspect_client_certificate
from sapextract.sap_connections.infrastructure.odata_client import NO_RETRY, SapResponse

CERT_WARNING_DAYS = 30
LATENCY_SAMPLES = 3
LATENCY_WARNING_MS = 2000
SERVER_HEADERS = (
    "server",
    "sap-server",
    "sap-system",
    "dataserviceversion",
    "odata-version",
    "sap-perf-fesrec",
)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _failed(name: StepName, error: SapError, started: float) -> TestStep:
    return TestStep(
        name=name,
        status=StepStatus.FAILED,
        message=error.detail,
        duration_ms=_ms(started),
        error_code=error.error_code,
        details=error.as_details(),
    )


def _last_failed(steps: list[TestStep]) -> bool:
    return steps[-1].status is StepStatus.FAILED


class ConnectionTester:
    def __init__(self, toolkit: SapToolkit) -> None:
        self.tk = toolkit

    def run(self, spec: ConnectionSpec) -> TestReport:
        started_at = utcnow()
        steps: list[TestStep] = [self._network(spec)]
        if not _last_failed(steps):
            with self.tk.client_factory(spec, NO_RETRY) as client:
                step, response = self._authentication(spec, client.catalog_root)
                steps.append(step)
                if response is not None:
                    steps.append(self._server_info(response))
                if not _last_failed(steps):
                    steps.append(self._latency(client.catalog_root))
        done = {s.name for s in steps}
        steps += [
            TestStep(
                name=name,
                status=StepStatus.SKIPPED,
                message="No se ejecutó porque falló un paso anterior.",
            )
            for name in STEP_ORDER
            if name not in done
        ]
        return TestReport(started_at=started_at, finished_at=utcnow(), steps=tuple(steps))

    # -------------------------------------------------------------- pasos
    def _network(self, spec: ConnectionSpec) -> TestStep:
        started = time.perf_counter()
        try:
            result = self.tk.probe(spec)
        except SapError as exc:
            return _failed(StepName.NETWORK, exc, started)
        details = {"host": result.host, "port": result.port, "addresses": result.addresses}
        details.update(result.details)
        if "via_proxy" in result.details:
            message = f"Proxy {result.details['via_proxy']} accesible; TLS se valida en el paso 2."
        elif result.details.get("tls"):
            message = (
                f"{result.host}:{result.port} accesible por {result.details.get('tls_version')}."
            )
        else:
            message = f"{result.host}:{result.port} accesible (HTTP sin cifrar)."
        status = StepStatus.OK
        if result.certificate_not_after is not None:
            days = (result.certificate_not_after - datetime.now(UTC)).days
            message += (
                f" El certificado del servidor caduca el {result.certificate_not_after:%d.%m.%Y}."
            )
            if days < CERT_WARNING_DAYS:
                status = StepStatus.WARNING
                message += f" ¡Quedan {max(days, 0)} días!"
        elif not result.details.get("tls") and "via_proxy" not in result.details:
            status = StepStatus.WARNING
            message += " Las credenciales viajan sin cifrar: use HTTPS fuera de desarrollo."
        return TestStep(
            name=StepName.NETWORK,
            status=status,
            message=message,
            duration_ms=_ms(started),
            details=details,
        )

    def _authentication(
        self, spec: ConnectionSpec, call: Callable[[], SapResponse]
    ) -> tuple[TestStep, SapResponse | None]:
        started = time.perf_counter()
        try:
            response = call()
        except SapError as exc:
            return _failed(StepName.AUTHENTICATION, exc, started), None
        status, message = StepStatus.OK, "Gateway aceptó las credenciales (catálogo accesible)."
        details: dict[str, object] = {"http_status": response.status_code}
        if isinstance(spec.credentials, X509Credentials):
            info = inspect_client_certificate(spec.credentials)
            not_after = datetime.fromisoformat(info["certificate_not_after"])
            days = (not_after - datetime.now(UTC)).days
            details["client_certificate_not_after"] = info["certificate_not_after"]
            if days < CERT_WARNING_DAYS:
                status = StepStatus.WARNING
                message += f" El certificado de cliente caduca en {max(days, 0)} días."
        return (
            TestStep(
                name=StepName.AUTHENTICATION,
                status=status,
                message=message,
                duration_ms=_ms(started),
                details=details,
            ),
            response,
        )

    def _server_info(self, response: SapResponse) -> TestStep:
        started = time.perf_counter()
        headers = {h: response.headers[h] for h in SERVER_HEADERS if h in response.headers}
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            error = SapResponseError(
                "La URL responde, pero no como SAP Gateway (respuesta "
                f"«{content_type or 'sin tipo'}»): revise la URL base y si hay un proxy de SSO."
            )
            return _failed(StepName.SERVER_INFO, error, started)
        try:
            body = response.json()
        except SapResponseError as exc:
            return _failed(StepName.SERVER_INFO, exc, started)
        if "d" not in body:
            error = SapResponseError("El catálogo de Gateway no devolvió un documento OData V2.")
            return _failed(StepName.SERVER_INFO, error, started)
        version = headers.get("dataserviceversion") or headers.get("odata-version")
        server = headers.get("sap-server") or headers.get("server")
        parts = [f"OData {version}" if version else "OData V2"]
        if server:
            parts.append(f"servidor «{server}»")
        return TestStep(
            name=StepName.SERVER_INFO,
            status=StepStatus.OK,
            message="Gateway responde: " + ", ".join(parts) + ".",
            duration_ms=_ms(started),
            details={"headers": headers},
        )

    def _latency(self, call: Callable[[], SapResponse]) -> TestStep:
        started = time.perf_counter()
        samples: list[int] = []
        try:
            for _ in range(LATENCY_SAMPLES):
                tick = time.perf_counter()
                call()
                samples.append(_ms(tick))
        except SapError as exc:
            return _failed(StepName.LATENCY, exc, started)
        avg = int(statistics.mean(samples))
        status = StepStatus.WARNING if avg > LATENCY_WARNING_MS else StepStatus.OK
        message = f"Latencia media {avg} ms (mín. {min(samples)}, máx. {max(samples)})."
        if status is StepStatus.WARNING:
            message += " Es alta: las extracciones serán lentas."
        return TestStep(
            name=StepName.LATENCY,
            status=status,
            message=message,
            duration_ms=_ms(started),
            details={"samples_ms": samples, "avg_ms": avg},
        )
