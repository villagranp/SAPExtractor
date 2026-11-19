"""Modelo de dominio de las conexiones SAP (plan 04 §1): enumerados, reglas y valores."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from sapextract.core.exceptions import BusinessRuleError, FieldIssue


class SapEnvironment(StrEnum):
    DEV = "DEV"
    QAS = "QAS"
    PRD = "PRD"


class SystemType(StrEnum):
    ECC = "ECC"
    S4HANA_ONPREM = "S4HANA_ONPREM"
    S4HANA_CLOUD = "S4HANA_CLOUD"


class AuthType(StrEnum):
    BASIC = "basic"
    X509 = "x509"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"


class SystemStatus(StrEnum):
    """``draft``: sin prueba correcta aún · ``active``: usable · ``inactive``: desactivado."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,29}$")
CLIENT_PATTERN = re.compile(r"^\d{3}$")
LANGUAGE_PATTERN = re.compile(r"^[A-Z]{1,2}$")
TAG_PATTERN = re.compile(r"^[\w\-.]{1,40}$")
MAX_TAGS = 20
MIN_TIMEOUT, MAX_TIMEOUT = 5, 600
MIN_CONCURRENCY, MAX_CONCURRENCY = 1, 32


def _issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not CODE_PATTERN.match(value):
        msg = (
            "El código debe tener de 3 a 30 caracteres: letras, números y guion bajo "
            "(p. ej. S4P_300)."
        )
        raise _issue("code", msg, "SX-SAP-CODE-INVALID")
    return value


def normalize_base_url(url: str, *, fieldname: str = "base_url") -> str:
    """URL base del Gateway ``http(s)://host[:puerto]``, sin credenciales, consulta ni fragmento."""
    value = url.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        msg = "Indique una URL http(s) válida, p. ej. https://gateway.empresa.local:44300."
        raise _issue(fieldname, msg, "SX-SAP-URL-INVALID")
    if parts.username or parts.password:
        msg = "La URL no puede incluir usuario ni contraseña; use el paso de credenciales."
        raise _issue(fieldname, msg, "SX-SAP-URL-CREDENTIALS")
    if parts.query or parts.fragment:
        msg = "La URL no puede incluir parámetros (?…) ni fragmentos (#…)."
        raise _issue(fieldname, msg, "SX-SAP-URL-INVALID")
    try:
        _ = parts.port
    except ValueError:
        msg = "El puerto de la URL no es válido."
        raise _issue(fieldname, msg, "SX-SAP-URL-INVALID") from None
    return value


def normalize_proxy_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    value = url.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        msg = "Indique la URL del proxy, p. ej. http://proxy.empresa.local:3128."
        raise _issue("proxy_url", msg, "SX-SAP-PROXY-INVALID")
    if parts.username or parts.password:
        msg = "El proxy no puede llevar credenciales en la URL."
        raise _issue("proxy_url", msg, "SX-SAP-PROXY-INVALID")
    return value


def normalize_client(client: str) -> str:
    value = client.strip()
    if not CLIENT_PATTERN.match(value):
        msg = "El mandante debe tener 3 dígitos (p. ej. 300)."
        raise _issue("sap_client", msg, "SX-SAP-CLIENT-INVALID")
    return value


def normalize_language(language: str) -> str:
    value = language.strip().upper()
    if not LANGUAGE_PATTERN.match(value):
        msg = "El idioma debe ser un código SAP de 1 o 2 letras (p. ej. ES, EN)."
        raise _issue("language", msg, "SX-SAP-LANGUAGE-INVALID")
    return value


def normalize_tags(tags: list[str]) -> list[str]:
    cleaned = sorted({t.strip().lower() for t in tags if t.strip()})
    if len(cleaned) > MAX_TAGS or not all(TAG_PATTERN.match(t) for t in cleaned):
        msg = f"Hasta {MAX_TAGS} etiquetas de 1 a 40 caracteres (letras, números, - _ .)."
        raise _issue("tags", msg, "SX-SAP-TAGS-INVALID")
    return cleaned


def check_limits(timeout_seconds: int, max_concurrent_requests: int) -> None:
    if not MIN_TIMEOUT <= timeout_seconds <= MAX_TIMEOUT:
        msg = f"El timeout debe estar entre {MIN_TIMEOUT} y {MAX_TIMEOUT} segundos."
        raise _issue("timeout_seconds", msg, "SX-SAP-TIMEOUT-INVALID")
    if not MIN_CONCURRENCY <= max_concurrent_requests <= MAX_CONCURRENCY:
        msg = f"Las peticiones simultáneas deben estar entre {MIN_CONCURRENCY} y {MAX_CONCURRENCY}."
        raise _issue("max_concurrent_requests", msg, "SX-SAP-CONCURRENCY-INVALID")


def check_production_security(
    environment: SapEnvironment, base_url: str, *, tls_verify: bool
) -> None:
    """En PRD las credenciales técnicas solo viajan por HTTPS con el certificado verificado."""
    if environment is not SapEnvironment.PRD:
        return
    if urlsplit(base_url).scheme != "https":
        msg = "Un sistema de producción debe usar HTTPS."
        raise _issue("base_url", msg, "SX-SAP-PRD-REQUIRES-HTTPS")
    if not tls_verify:
        msg = "En producción no se puede desactivar la verificación del certificado TLS."
        raise _issue("tls_verify", msg, "SX-SAP-PRD-REQUIRES-TLS-VERIFY")


# ------------------------------------------------------------------ credenciales
@dataclass(frozen=True, kw_only=True)
class BasicCredentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class X509Credentials:
    certificate_pem: str = field(repr=False)
    private_key_pem: str = field(repr=False)
    private_key_passphrase: str | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class OAuth2Credentials:
    token_url: str
    client_id: str
    client_secret: str = field(repr=False)
    scope: str | None = None


type Credentials = BasicCredentials | X509Credentials | OAuth2Credentials


def auth_type_of(credentials: Credentials) -> AuthType:
    match credentials:
        case BasicCredentials():
            return AuthType.BASIC
        case X509Credentials():
            return AuthType.X509
        case OAuth2Credentials():
            return AuthType.OAUTH2_CLIENT_CREDENTIALS


def credentials_to_payload(credentials: Credentials) -> dict[str, Any]:
    """Contenido que se cifra en ``secret`` (incluye el tipo para poder reconstruirlo)."""
    payload: dict[str, Any] = {"auth_type": auth_type_of(credentials).value}
    payload.update(credentials.__dict__)
    return payload


def credentials_from_payload(payload: dict[str, Any]) -> Credentials:
    data = {k: v for k, v in payload.items() if k != "auth_type"}
    match AuthType(payload["auth_type"]):
        case AuthType.BASIC:
            return BasicCredentials(**data)
        case AuthType.X509:
            return X509Credentials(**data)
        case AuthType.OAUTH2_CLIENT_CREDENTIALS:
            return OAuth2Credentials(**data)


def validate_credentials(credentials: Credentials) -> Credentials:
    """Reglas de forma; el contenido de certificados se valida en infraestructura."""
    match credentials:
        case BasicCredentials(username=username, password=password):
            if not username.strip() or not password:
                msg = "Indique el usuario técnico y su contraseña."
                raise _issue("credentials", msg, "SX-SAP-CREDENTIALS-INCOMPLETE")
            return BasicCredentials(username=username.strip().upper(), password=password)
        case X509Credentials(certificate_pem=cert, private_key_pem=key):
            if not cert.strip() or not key.strip():
                msg = "Indique el certificado de cliente y su clave privada (PEM)."
                raise _issue("credentials", msg, "SX-SAP-CREDENTIALS-INCOMPLETE")
            return credentials
        case OAuth2Credentials(token_url=url, client_id=client_id, client_secret=secret):
            if not client_id.strip() or not secret:
                msg = "Indique el client_id y el client_secret."
                raise _issue("credentials", msg, "SX-SAP-CREDENTIALS-INCOMPLETE")
            return OAuth2Credentials(
                token_url=normalize_base_url(url, fieldname="credentials.token_url"),
                client_id=client_id.strip(),
                client_secret=secret,
                scope=(credentials.scope or "").strip() or None,
            )


# ------------------------------------------------------------------ conexión
@dataclass(frozen=True, kw_only=True)
class ConnectionSpec:
    """Todo lo necesario para hablar con un Gateway (sin persistencia ni identidad)."""

    base_url: str
    sap_client: str
    language: str
    credentials: Credentials
    tls_verify: bool = True
    ca_bundle_pem: str | None = field(default=None, repr=False)
    proxy_url: str | None = None
    timeout_seconds: int = 60
    max_concurrent_requests: int = 4
    label: str = "sap"  # código del sistema para logs y limitación de concurrencia
