"""Entra ID simulado para las pruebas (transporte httpx, sin red).

Atiende lo que usa la prueba de configuración: *discovery* del inquilino, claves de firma y
el endpoint de token con ``client_credentials``. Verifica de verdad la aserción firmada con
el certificado registrado (firma PS256, ``x5t#S256``, audiencia y caducidad) y responde con
los mismos códigos ``AADSTS`` que Entra.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

AUTHORITY = "https://login.entra.test"
TENANT_ID = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_SECRET = "Fake~Entra.Secret_0123456789abcdefghijklmn"  # nosec B105


@dataclass(frozen=True)
class RsaCertificate:
    cert_pem: str
    key_pem: str


def make_rsa_certificate(
    *, days: int = 365, key_size: int = 2048, passphrase: str | None = None
) -> RsaCertificate:
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SAPExtract pruebas")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    return RsaCertificate(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
        ).decode(),
    )


def _error(status: int, error: str, code: int, description: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": error,
            "error_description": f"AADSTS{code}: {description}\r\nTrace ID: fake",
            "error_codes": [code],
            "trace_id": "00000000-trace",
            "correlation_id": "00000000-correlation",
        },
    )


@dataclass
class FakeEntra:
    """Un inquilino con una aplicación: secretos y certificados que acepta."""

    tenant_id: str = TENANT_ID
    client_id: str = CLIENT_ID
    secrets: set[str] = field(default_factory=lambda: {CLIENT_SECRET})
    certificates: list[str] = field(default_factory=list)  # PEM subidos al registro
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False

    @property
    def token_endpoint(self) -> str:
        return f"{AUTHORITY}/{self.tenant_id}/oauth2/v2.0/token"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("fake Entra caído", request=request)
        parts = request.url.path.strip("/").split("/")
        tenant = parts[0] if parts else ""
        if tenant != self.tenant_id:
            return _error(400, "invalid_tenant", 90002, f"Tenant '{tenant}' not found.")
        path = "/" + "/".join(parts[1:])
        if path == "/v2.0/.well-known/openid-configuration":
            base = f"{AUTHORITY}/{tenant}"
            return httpx.Response(
                200,
                json={
                    "issuer": f"https://login.entra.test/{tenant}/v2.0",
                    "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
                    "token_endpoint": f"{base}/oauth2/v2.0/token",
                    "jwks_uri": f"{base}/discovery/v2.0/keys",
                    "end_session_endpoint": f"{base}/oauth2/v2.0/logout",
                },
            )
        if path == "/discovery/v2.0/keys":
            key = RSAKey.generate_key(2048)
            return httpx.Response(
                200, json={"keys": [{**key.as_dict(private=False), "use": "sig"}]}
            )
        if path == "/oauth2/v2.0/token" and request.method == "POST":
            return self._token(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") != "client_credentials":
            return _error(400, "unsupported_grant_type", 70003, "Unsupported grant type.")
        if form.get("client_id") != self.client_id:
            return _error(400, "unauthorized_client", 700016, "Application not found.")
        if "client_secret" in form:
            if form["client_secret"] not in self.secrets:
                return _error(401, "invalid_client", 7000215, "Invalid client secret provided.")
            return self._granted()
        if "client_assertion" in form:
            return self._assertion(form["client_assertion"])
        return _error(401, "invalid_client", 7000218, "Missing client_assertion or secret.")

    def _assertion(self, assertion: str) -> httpx.Response:
        header = json.loads(base64.urlsafe_b64decode(assertion.split(".", maxsplit=1)[0] + "=="))
        for pem in self.certificates:
            cert = x509.load_pem_x509_certificate(pem.encode())
            thumb = base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA256())).rstrip(b"=")
            if header.get("x5t#S256") != thumb.decode():
                continue
            public = cert.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            try:
                token = jwt.decode(assertion, RSAKey.import_key(public), algorithms=["PS256"])
            except JoseError:
                break
            claims: dict[str, Any] = token.claims
            now = time.time()
            if claims.get("aud") != self.token_endpoint or claims.get("sub") != self.client_id:
                return _error(401, "invalid_client", 700027, "Invalid audience or subject.")
            if not claims.get("nbf", now) - 300 <= now < claims.get("exp", 0):
                return _error(401, "invalid_client", 700024, "Assertion is not within range.")
            return self._granted()
        return _error(
            401, "invalid_client", 700027, "Client assertion failed signature validation."
        )

    @staticmethod
    def _granted() -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 3599,
                "access_token": f"fake.{uuid.uuid4().hex}",
            },
        )
