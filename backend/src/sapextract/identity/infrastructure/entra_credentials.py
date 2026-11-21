"""Credencial de la aplicación en Entra ID: secreto de cliente o certificado con su clave.

El contenido se guarda cifrado en el almacén de secretos (``kind = entra_credentials``) y solo
se descifra en memoria para pedir un token. Con certificado, Entra recibe una aserción JWT
firmada con la clave privada (PS256 y ``x5t#S256``, formato vigente de Microsoft).
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt
from joserfc.jwk import RSAKey

from sapextract.identity.domain.entra import CREDENTIAL_TEST_SCOPE, CredentialType, issue

SECRET_KIND = "entra_credentials"  # noqa: S105  # nosec B105 (tipo de secreto, no un secreto)
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ASSERTION_LIFETIME = timedelta(minutes=5)
MAX_SECRET_LENGTH = 512
MIN_RSA_BITS = 2048


@dataclass(frozen=True, kw_only=True)
class CertificateInfo:
    """Datos públicos del certificado (se muestran en la pantalla y en la auditoría)."""

    subject: str
    thumbprint: str  # SHA-1 en hexadecimal: es lo que muestra el portal de Entra
    not_before: datetime
    not_after: datetime


def secret_payload(client_secret: str) -> dict[str, Any]:
    value = client_secret.strip()
    if not value or len(value) > MAX_SECRET_LENGTH:
        msg = "Indique el valor del secreto de cliente."
        raise issue("client_secret", msg, "SX-IDP-SECRET-REQUIRED")
    return {"type": CredentialType.CLIENT_SECRET.value, "client_secret": value}


def certificate_payload(
    certificate_pem: str, private_key_pem: str, passphrase: str | None, *, now: datetime
) -> tuple[dict[str, Any], CertificateInfo]:
    """Valida certificado y clave; devuelve el contenido a cifrar (clave sin contraseña)."""
    try:
        cert = x509.load_pem_x509_certificate(certificate_pem.strip().encode())
    except ValueError:
        msg = "El certificado no es un PEM válido (-----BEGIN CERTIFICATE-----)."
        raise issue("certificate_pem", msg, "SX-IDP-CERT-INVALID") from None
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.strip().encode(),
            password=passphrase.encode() if passphrase else None,
        )
    except TypeError:
        msg = "La clave privada está cifrada: indique su contraseña."
        raise issue("private_key_passphrase", msg, "SX-IDP-KEY-PASSPHRASE") from None
    except ValueError:
        msg = "La clave privada no es válida o la contraseña no es correcta."
        raise issue("private_key_pem", msg, "SX-IDP-KEY-INVALID") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < MIN_RSA_BITS:
        msg = f"Entra ID necesita una clave RSA de al menos {MIN_RSA_BITS} bits."
        raise issue("private_key_pem", msg, "SX-IDP-KEY-TYPE")
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(
        serialization.Encoding.DER, spki
    ) != cert.public_key().public_bytes(serialization.Encoding.DER, spki):
        msg = "La clave privada no corresponde al certificado."
        raise issue("private_key_pem", msg, "SX-IDP-KEY-MISMATCH")
    info = _info(cert)
    if info.not_after <= now:
        msg = "El certificado ya caducó; genere uno nuevo."
        raise issue("certificate_pem", msg, "SX-IDP-CERT-EXPIRED")
    payload = {
        "type": CredentialType.CERTIFICATE.value,
        "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "private_key_pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }
    return payload, info


def certificate_info(payload: dict[str, Any]) -> CertificateInfo:
    return _info(x509.load_pem_x509_certificate(payload["certificate_pem"].encode()))


def _info(cert: x509.Certificate) -> CertificateInfo:
    return CertificateInfo(
        subject=cert.subject.rfc4514_string(),
        thumbprint=cert.fingerprint(hashes.SHA1()).hex().upper(),  # noqa: S303  # nosec B303
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def client_assertion(
    payload: dict[str, Any], *, client_id: str, token_endpoint: str, now: datetime
) -> str:
    """JWT firmado con la clave del certificado que sustituye al secreto (RFC 7523)."""
    cert = x509.load_pem_x509_certificate(payload["certificate_pem"].encode())
    key = RSAKey.import_key(payload["private_key_pem"])
    header = {
        "alg": "PS256",
        "typ": "JWT",
        "x5t#S256": _b64url(cert.fingerprint(hashes.SHA256())),
    }
    issued = int(now.timestamp())
    claims = {
        "aud": token_endpoint,
        "iss": client_id,
        "sub": client_id,
        "jti": str(uuid.uuid4()),
        "iat": issued,
        "nbf": issued,
        "exp": issued + int(ASSERTION_LIFETIME.total_seconds()),
    }
    return jwt.encode(header, claims, key, algorithms=["PS256"])


def app_token_form(
    payload: dict[str, Any], *, client_id: str, token_endpoint: str, now: datetime
) -> dict[str, str]:
    """Formulario ``client_credentials`` con el que se prueba la credencial."""
    form = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "scope": CREDENTIAL_TEST_SCOPE,
    }
    if payload["type"] == CredentialType.CLIENT_SECRET.value:
        form["client_secret"] = payload["client_secret"]
    else:
        form["client_assertion_type"] = ASSERTION_TYPE
        form["client_assertion"] = client_assertion(
            payload, client_id=client_id, token_endpoint=token_endpoint, now=now
        )
    return form
