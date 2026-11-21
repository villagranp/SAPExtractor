"""Microsoft Entra ID como proveedor de identidad: reglas de la configuración y su prueba.

Ver docs/plan/02-usuarios-autenticacion-roles.md §4. Sin dependencias de frameworks: la
llamada HTTP a Entra y la firma de la aserción viven en ``infrastructure``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from sapextract.core.exceptions import BusinessRuleError, FieldIssue

PROVIDER_TYPE = "entra_id"
CALLBACK_PATH = "/auth/entra/callback"
# Antelación con la que se avisa de que el secreto o el certificado caducan.
EXPIRY_WARNING_DAYS = 30
MAX_GROUP_MAPPINGS = 200
MAX_ALLOWED_DOMAINS = 50
MAX_GROUP_NAME = 256
# La credencial se prueba pidiendo un token de aplicación: no da acceso a nada por sí mismo.
CREDENTIAL_TEST_SCOPE = "https://graph.microsoft.com/.default"

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class CredentialType(StrEnum):
    # Falso positivo de los analizadores: es el nombre del tipo, no un secreto.
    CLIENT_SECRET = "client_secret"  # noqa: S105  # nosec B105
    CERTIFICATE = "certificate"


def issue(field_name: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(field_name, message, code)])


def normalize_guid(value: str, field_name: str, label: str) -> str:
    guid = value.strip().lower()
    if not _GUID.match(guid):
        msg = f"{label} debe ser un GUID, con el formato 00000000-0000-0000-0000-000000000000."
        raise issue(field_name, msg, "SX-IDP-GUID")
    return guid


def normalize_domains(values: Iterable[str]) -> list[str]:
    """Dominios de correo admitidos, en minúsculas y sin repetir (vacío = todo el inquilino)."""
    result: list[str] = []
    for raw in values:
        domain = raw.strip().lower().removeprefix("@")
        if not domain:
            continue
        if not _DOMAIN.match(domain):
            msg = f"«{raw.strip()}» no es un dominio válido (por ejemplo, empresa.com)."
            raise issue("allowed_domains", msg, "SX-IDP-DOMAIN")
        if domain not in result:
            result.append(domain)
    if len(result) > MAX_ALLOWED_DOMAINS:
        msg = f"Indique como máximo {MAX_ALLOWED_DOMAINS} dominios."
        raise issue("allowed_domains", msg, "SX-IDP-DOMAIN")
    return result


@dataclass(frozen=True, kw_only=True)
class GroupMapping:
    """Grupo de seguridad de Entra (por su Object ID) que concede un rol de la plataforma."""

    group_object_id: str
    group_name: str
    role_id: uuid.UUID


def check_group_mappings(mappings: Iterable[GroupMapping]) -> list[GroupMapping]:
    result: list[GroupMapping] = []
    seen: set[tuple[str, uuid.UUID]] = set()
    for index, mapping in enumerate(mappings):
        prefix = f"mappings.{index}"
        group_id = normalize_guid(mapping.group_object_id, f"{prefix}.group_object_id", "El grupo")
        name = mapping.group_name.strip()
        if not name or len(name) > MAX_GROUP_NAME:
            msg = f"Indique el nombre del grupo (máximo {MAX_GROUP_NAME} caracteres)."
            raise issue(f"{prefix}.group_name", msg, "SX-IDP-GROUP-NAME")
        key = (group_id, mapping.role_id)
        if key in seen:
            msg = "Este grupo ya tiene asignado ese rol."
            raise issue(f"{prefix}.role_id", msg, "SX-IDP-GROUP-DUPLICATE")
        seen.add(key)
        result.append(
            GroupMapping(group_object_id=group_id, group_name=name, role_id=mapping.role_id)
        )
    if len(result) > MAX_GROUP_MAPPINGS:
        msg = f"Se admiten como máximo {MAX_GROUP_MAPPINGS} filas de mapeo."
        raise issue("mappings", msg, "SX-IDP-GROUP-LIMIT")
    return result


def redirect_uri(base_url: str, api_prefix: str) -> str:
    """URI que se registra en Entra; el callback lo atiende la API (patrón BFF, ADR 0004)."""
    return f"{base_url.rstrip('/')}{api_prefix}{CALLBACK_PATH}"


def redirect_warnings(uri: str) -> list[str]:
    """Entra solo acepta https, salvo ``http://localhost`` para desarrollo."""
    parts = urlsplit(uri)
    if parts.scheme == "https" or parts.hostname in ("localhost", "127.0.0.1"):
        return []
    return [
        "Entra ID solo admite URIs de redirección https (salvo localhost). "
        "Defina SX_PUBLIC_URL con la dirección pública de la plataforma."
    ]


def fingerprint(tenant_id: str, client_id: str, credential: dict[str, Any] | None) -> str:
    """Huella de lo que decide si la prueba sigue valiendo. Solo se guarda en BD, nunca sale."""
    raw = json.dumps(
        {"tenant": tenant_id, "client": client_id, "credential": credential},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def expiry_warning(expires_at: datetime | None, now: datetime) -> str | None:
    if expires_at is None:
        return None
    if expires_at <= now:
        return "La credencial caducó: cree una nueva en el registro de aplicación y cárguela aquí."
    if expires_at - now <= timedelta(days=EXPIRY_WARNING_DAYS):
        return (
            f"La credencial caduca el {expires_at:%d/%m/%Y}: renuévela antes en el registro "
            "de aplicación."
        )
    return None


# ------------------------------------------------------------------ mensajes de Entra

# Códigos AADSTS habituales al configurar el registro, con lo que tiene que hacer el admin.
AADSTS_MESSAGES: dict[int, str] = {
    90002: "El inquilino no existe. Copie el ID de inquilino de la página de información "
    "general de Entra ID.",
    900023: "El ID de inquilino no es válido.",
    700016: "El client ID no existe en este inquilino. Copie el ID de aplicación (cliente) "
    "del registro de aplicación.",
    7000215: "El secreto de cliente no es válido. Copie el valor del secreto, no su ID.",
    7000222: "El secreto de cliente caducó. Cree uno nuevo en el registro de aplicación.",
    700027: "Entra no reconoce el certificado. Súbalo al registro de aplicación, en "
    "Certificados y secretos.",
    700024: "La aserción firmada está fuera de su ventana de validez. Revise la hora del servidor.",
    7000218: "Falta la credencial: indique un secreto de cliente o un certificado.",
    7000112: "La aplicación está deshabilitada en el inquilino.",
    700025: "El registro de aplicación es de cliente público. Desactive «Permitir flujos de "
    "cliente público» en Autenticación.",
}


def describe_aadsts(codes: Iterable[int], fallback: str) -> tuple[str, str | None]:
    """Mensaje para el admin y código ``AADSTS…`` del primer error conocido."""
    codes = list(codes)
    for code in codes:
        if code in AADSTS_MESSAGES:
            return AADSTS_MESSAGES[code], f"AADSTS{code}"
    return fallback, f"AADSTS{codes[0]}" if codes else None


# ------------------------------------------------------------------ prueba por pasos


class StepName(StrEnum):
    DISCOVERY = "discovery"  # el inquilino existe y publica su configuración OpenID
    KEYS = "keys"  # claves de firma de los tokens (JWKS)
    CREDENTIAL = "credential"  # client ID y secreto o certificado aceptados por Entra


class StepStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True)
class TestStep:
    __test__ = False  # no es una prueba de pytest

    name: StepName
    status: StepStatus
    message: str
    duration_ms: int = 0
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "details": self.details,
        }


@dataclass(frozen=True, kw_only=True)
class TestReport:
    __test__ = False

    started_at: datetime
    finished_at: datetime
    steps: tuple[TestStep, ...]

    @property
    def ok(self) -> bool:
        return all(s.status in (StepStatus.OK, StepStatus.WARNING) for s in self.steps)

    @property
    def error_code(self) -> str | None:
        return next((s.error_code for s in self.steps if s.status is StepStatus.FAILED), None)

    @property
    def summary(self) -> str:
        failed = next((s for s in self.steps if s.status is StepStatus.FAILED), None)
        if failed is not None:
            return failed.message
        warnings = [s.message for s in self.steps if s.status is StepStatus.WARNING]
        return "Entra ID responde y acepta la credencial." + (
            " " + " ".join(warnings) if warnings else ""
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "error_code": self.error_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "steps": [s.as_dict() for s in self.steps],
        }

    def audit_details(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "steps": {s.name.value: s.status.value for s in self.steps},
        }


@dataclass(frozen=True, kw_only=True)
class AccessSettings:
    """Quién puede entrar y con qué rol (se aplica en el login, It7b)."""

    jit_provisioning: bool
    default_role_id: uuid.UUID | None
    require_group_membership: bool
    group_mappings: int


def access_warnings(access: AccessSettings) -> list[str]:
    """Configuraciones válidas pero que probablemente no son lo que se quiere."""
    warnings: list[str] = []
    if access.group_mappings == 0 and access.default_role_id is None:
        warnings.append("Ningún usuario recibirá roles: añada grupos o un rol por defecto.")
    if access.require_group_membership and access.group_mappings == 0:
        warnings.append(
            "Se exige pertenecer a un grupo mapeado y no hay ninguno: nadie podrá entrar."
        )
    if access.jit_provisioning and not access.require_group_membership:
        warnings.append(
            "Con el alta automática y sin exigir grupo, cualquier usuario del inquilino "
            "puede entrar."
        )
    return warnings
