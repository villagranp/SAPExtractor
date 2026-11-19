"""Primer paso de la prueba de conexión: DNS, TCP y TLS (con datos del certificado del servidor)."""

from __future__ import annotations

import socket
import ssl
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509

from sapextract.sap_connections.domain.errors import (
    SapDnsError,
    SapNetworkError,
    SapTimeoutError,
    SapTlsError,
)
from sapextract.sap_connections.domain.model import ConnectionSpec
from sapextract.sap_connections.infrastructure.certificates import (
    build_ssl_context,
    describe_certificate,
)

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_PROBE_TIMEOUT = 10.0


@dataclass(frozen=True, kw_only=True)
class ProbeResult:
    host: str
    port: int
    addresses: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    certificate_not_after: datetime | None = None


def _endpoint(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return host, parts.port or DEFAULT_PORTS.get(parts.scheme, 443)


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        msg = f"No se pudo resolver el nombre «{host}» (DNS)."
        raise SapDnsError(msg) from exc
    return sorted({str(info[4][0]) for info in infos})


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except TimeoutError as exc:
        msg = f"Sin respuesta de {host}:{port} en {timeout:g} s (¿cortafuegos?)."
        raise SapTimeoutError(msg) from exc
    except OSError as exc:
        msg = f"No se pudo abrir la conexión TCP con {host}:{port} ({exc.strerror or exc})."
        raise SapNetworkError(msg) from exc


def _peer_certificate(sock: ssl.SSLSocket) -> x509.Certificate | None:
    der = sock.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der) if der else None


def _unverified_certificate(host: str, port: int, timeout: float) -> dict[str, Any] | None:
    """Certificado presentado por el servidor aunque no sea de confianza (para diagnóstico)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with (
            closing(_connect(host, port, timeout)) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            cert = _peer_certificate(tls)
    except OSError, SapNetworkError, SapTimeoutError:
        return None
    return describe_certificate(cert) if cert else None


def probe(spec: ConnectionSpec) -> ProbeResult:
    timeout = min(float(spec.timeout_seconds), MAX_PROBE_TIMEOUT)
    if spec.proxy_url:
        # Con proxy no hay ruta directa: se comprueba el proxy; TLS se valida en el paso HTTP.
        proxy_host, proxy_port = _endpoint(spec.proxy_url)
        addresses = _resolve(proxy_host, proxy_port)
        _connect(proxy_host, proxy_port, timeout).close()
        return ProbeResult(
            host=proxy_host,
            port=proxy_port,
            addresses=addresses,
            details={"via_proxy": f"{proxy_host}:{proxy_port}"},
        )

    host, port = _endpoint(spec.base_url)
    addresses = _resolve(host, port)
    raw = _connect(host, port, timeout)
    if urlsplit(spec.base_url).scheme != "https":
        raw.close()
        return ProbeResult(host=host, port=port, addresses=addresses, details={"tls": False})

    context = build_ssl_context(spec)
    try:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            cert = _peer_certificate(tls)
            details: dict[str, Any] = {
                "tls": True,
                "tls_version": tls.version(),
                "verified": spec.tls_verify,
            }
    except ssl.SSLCertVerificationError as exc:
        raw.close()
        presented = _unverified_certificate(host, port, timeout)
        msg = f"El certificado del servidor no es de confianza: {exc.verify_message}."
        raise SapTlsError(msg, extra={"presented_certificate": presented}) from exc
    except (ssl.SSLError, OSError) as exc:
        raw.close()
        msg = f"Falló el saludo TLS con {host}:{port} ({type(exc).__name__}: {exc})."
        raise SapTlsError(msg) from exc

    not_after = cert.not_valid_after_utc if cert else None
    if cert is not None:
        details["certificate"] = describe_certificate(cert)
        details["certificate_days_left"] = (cert.not_valid_after_utc - datetime.now(UTC)).days
    return ProbeResult(
        host=host,
        port=port,
        addresses=addresses,
        details=details,
        certificate_not_after=not_after,
    )
