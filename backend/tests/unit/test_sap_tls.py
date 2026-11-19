"""TLS y certificados con un servidor HTTPS local autofirmado (sin servicios externos)."""

from __future__ import annotations

import http.server
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from sapextract.core.exceptions import BusinessRuleError
from sapextract.sap_connections.domain.errors import (
    SapNetworkError,
    SapTlsError,
)
from sapextract.sap_connections.domain.model import (
    BasicCredentials,
    ConnectionSpec,
    X509Credentials,
)
from sapextract.sap_connections.infrastructure.certificates import (
    inspect_client_certificate,
    parse_ca_bundle,
)
from sapextract.sap_connections.infrastructure.network_probe import probe
from tests.support.certs import CertificatePair, make_certificate


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture(scope="module")
def server_cert(tmp_path_factory: pytest.TempPathFactory) -> CertificatePair:
    return make_certificate(tmp_path_factory.mktemp("tls"))


class _QuietServer(http.server.HTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        """Los saludos TLS rechazados a propósito no ensucian la salida."""


@pytest.fixture(scope="module")
def https_url(server_cert: CertificatePair) -> Iterator[str]:
    httpd = _QuietServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server_cert.cert_path, server_cert.key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _spec(url: str, **kw: object) -> ConnectionSpec:
    return ConnectionSpec(
        base_url=url,
        sap_client="100",
        language="ES",
        credentials=BasicCredentials(username="U", password="p"),
        timeout_seconds=5,
        **kw,  # type: ignore[arg-type]
    )


def test_untrusted_certificate_fails_with_presented_details(https_url: str) -> None:
    with pytest.raises(SapTlsError) as exc:
        probe(_spec(https_url))
    assert exc.value.error_code == "SX-SAP-TLS"
    presented = exc.value.as_details()["presented_certificate"]
    assert presented["subject"] == "CN=127.0.0.1"


def test_own_ca_bundle_makes_the_certificate_trusted(
    https_url: str, server_cert: CertificatePair
) -> None:
    result = probe(_spec(https_url, ca_bundle_pem=server_cert.cert_pem))
    assert result.details["tls"] is True
    assert result.details["verified"] is True
    assert result.details["certificate"]["subject"] == "CN=127.0.0.1"
    assert result.certificate_not_after is not None
    assert result.details["certificate_days_left"] >= 360


def test_verification_can_be_disabled(https_url: str) -> None:
    result = probe(_spec(https_url, tls_verify=False))
    assert result.details["verified"] is False


def test_closed_port_is_a_network_error() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # puerto libre: nadie escucha tras cerrar el socket
    with pytest.raises(SapNetworkError):
        probe(_spec(f"http://127.0.0.1:{port}"))


def test_ca_bundle_and_client_certificate_validation(tmp_path: Path) -> None:
    pair = make_certificate(tmp_path, common_name="SXTECH", name="client")
    (summary,) = parse_ca_bundle(pair.cert_pem)
    assert summary["subject"] == "CN=SXTECH"
    with pytest.raises(BusinessRuleError) as exc:
        parse_ca_bundle("no es un PEM")
    assert exc.value.error_code == "SX-SAP-CA-INVALID"

    info = inspect_client_certificate(
        X509Credentials(certificate_pem=pair.cert_pem, private_key_pem=pair.key_pem)
    )
    assert info["certificate_subject"] == "CN=SXTECH"

    other = make_certificate(tmp_path, name="other")
    with pytest.raises(BusinessRuleError) as exc:
        inspect_client_certificate(
            X509Credentials(certificate_pem=pair.cert_pem, private_key_pem=other.key_pem)
        )
    assert exc.value.error_code == "SX-SAP-KEY-MISMATCH"

    locked = make_certificate(tmp_path, name="locked", passphrase="Frase-Segura-1")
    with pytest.raises(BusinessRuleError) as exc:
        inspect_client_certificate(
            X509Credentials(certificate_pem=locked.cert_pem, private_key_pem=locked.key_pem)
        )
    assert exc.value.error_code == "SX-SAP-KEY-PASSPHRASE"
    assert (
        inspect_client_certificate(
            X509Credentials(
                certificate_pem=locked.cert_pem,
                private_key_pem=locked.key_pem,
                private_key_passphrase="Frase-Segura-1",
            )
        )["certificate_subject"]
        == "CN=127.0.0.1"
    )
