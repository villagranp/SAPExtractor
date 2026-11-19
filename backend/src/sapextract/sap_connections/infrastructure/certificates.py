"""Certificados X.509: validación de PEM, resúmenes no sensibles y contextos TLS."""

from __future__ import annotations

import os
import ssl
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.sap_connections.domain.model import ConnectionSpec, X509Credentials


def _invalid(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def describe_certificate(cert: x509.Certificate) -> dict[str, Any]:
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "serial": format(cert.serial_number, "x"),
    }


def parse_ca_bundle(pem: str) -> list[dict[str, Any]]:
    """Valida un bundle de CA en PEM y devuelve un resumen de cada certificado."""
    try:
        certs = x509.load_pem_x509_certificates(pem.encode())
    except ValueError:
        msg = "El certificado de la CA no es un PEM válido (-----BEGIN CERTIFICATE-----)."
        raise _invalid("ca_bundle_pem", msg, "SX-SAP-CA-INVALID") from None
    return [describe_certificate(c) for c in certs]


def _load_key(credentials: X509Credentials) -> PrivateKeyTypes:
    password = (
        credentials.private_key_passphrase.encode() if credentials.private_key_passphrase else None
    )
    try:
        return serialization.load_pem_private_key(
            credentials.private_key_pem.encode(), password=password
        )
    except TypeError:
        msg = "La clave privada está cifrada: indique su contraseña."
        raise _invalid("credentials.private_key_passphrase", msg, "SX-SAP-KEY-PASSPHRASE") from None
    except ValueError:
        msg = "La clave privada no es válida o la contraseña no es correcta."
        raise _invalid("credentials.private_key_pem", msg, "SX-SAP-KEY-INVALID") from None


def inspect_client_certificate(credentials: X509Credentials) -> dict[str, Any]:
    """Comprueba que certificado y clave casan; devuelve los datos visibles del certificado."""
    try:
        cert = x509.load_pem_x509_certificate(credentials.certificate_pem.encode())
    except ValueError:
        msg = "El certificado de cliente no es un PEM válido."
        raise _invalid("credentials.certificate_pem", msg, "SX-SAP-CERT-INVALID") from None
    key = _load_key(credentials)
    public = key.public_key()
    if public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        msg = "La clave privada no corresponde al certificado."
        raise _invalid("credentials.private_key_pem", msg, "SX-SAP-KEY-MISMATCH")
    info = describe_certificate(cert)
    return {
        "certificate_subject": info["subject"],
        "certificate_issuer": info["issuer"],
        "certificate_not_after": info["not_after"],
    }


def certificate_expiry(info: dict[str, Any]) -> datetime | None:
    value = info.get("certificate_not_after") or info.get("not_after")
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def build_ssl_context(spec: ConnectionSpec) -> ssl.SSLContext:
    """Contexto TLS de la conexión: verificación, CA propia y certificado de cliente (X.509)."""
    if spec.tls_verify:
        context = ssl.create_default_context()
        if spec.ca_bundle_pem:
            context.load_verify_locations(cadata=spec.ca_bundle_pem)
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    credentials = spec.credentials
    if isinstance(credentials, X509Credentials):
        _load_client_chain(context, credentials)
    return context


def _load_client_chain(context: ssl.SSLContext, credentials: X509Credentials) -> None:
    # ``load_cert_chain`` solo acepta rutas: se escriben en un directorio privado (0700)
    # y se borran en cuanto OpenSSL ha cargado el material.
    with tempfile.TemporaryDirectory(prefix="sx-x509-") as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        cert_path, key_path = directory / "cert.pem", directory / "key.pem"
        for path, content in (
            (cert_path, credentials.certificate_pem),
            (key_path, credentials.private_key_pem),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(content)
        context.load_cert_chain(
            certfile=str(cert_path),
            keyfile=str(key_path),
            password=credentials.private_key_passphrase or None,
        )
