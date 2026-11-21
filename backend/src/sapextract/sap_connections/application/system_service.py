"""Casos de uso de los sistemas SAP: alta, edición, credenciales, prueba, estado y baja.

Las llamadas a SAP (pruebas) se hacen fuera de cualquier transacción abierta; el
resultado se guarda después junto con su evento de auditoría.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, FieldIssue, NotFoundError
from sapextract.core.network import HostNotAllowedError
from sapextract.core.pagination import PageParams
from sapextract.core.secret_store import SecretStore
from sapextract.identity.domain.model import Principal
from sapextract.sap_connections.application.connection_tester import ConnectionTester
from sapextract.sap_connections.application.toolkit import Dependent, SapToolkit
from sapextract.sap_connections.domain.model import (
    DEFAULT_RUNS,
    AuthType,
    BasicCredentials,
    ConnectionSpec,
    Credentials,
    OAuth2Credentials,
    SapEnvironment,
    SystemStatus,
    SystemType,
    X509Credentials,
    auth_type_of,
    check_limits,
    check_production_security,
    check_run_limit,
    credentials_from_payload,
    credentials_to_payload,
    normalize_base_url,
    normalize_client,
    normalize_code,
    normalize_language,
    normalize_proxy_url,
    normalize_tags,
    validate_credentials,
)
from sapextract.sap_connections.domain.testing import TestReport
from sapextract.sap_connections.infrastructure.certificates import (
    inspect_client_certificate,
    parse_ca_bundle,
)
from sapextract.sap_connections.infrastructure.models import SapSystem

CREDENTIAL_KIND = "sap_credentials"
CA_BUNDLE_KIND = "sap_ca_bundle"
# Campos cuyo cambio invalida la última prueba de conexión.
CONNECTION_FIELDS = frozenset(
    {"base_url", "sap_client", "language", "tls_verify", "ca_bundle", "proxy_url"}
)
SORTABLE = {"code", "name", "environment", "status", "last_test_at", "created_at"}
# Campos editables con su normalización (PATCH).
_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "name": lambda v: str(v).strip(),
    "description": lambda v: str(v).strip() or None,
    "environment": lambda v: SapEnvironment(v).value,
    "system_type": lambda v: SystemType(v).value,
    "tags": normalize_tags,
    "base_url": normalize_base_url,
    "sap_client": normalize_client,
    "language": normalize_language,
    "tls_verify": bool,
    "proxy_url": normalize_proxy_url,
}


@dataclass(frozen=True, kw_only=True)
class ConnectionData:
    base_url: str
    sap_client: str
    language: str = "ES"
    tls_verify: bool = True
    ca_bundle_pem: str | None = None
    proxy_url: str | None = None
    timeout_seconds: int = 60
    max_concurrent_requests: int = 4
    max_concurrent_runs: int = DEFAULT_RUNS


@dataclass(frozen=True, kw_only=True)
class NewSapSystem:
    code: str
    name: str
    description: str | None
    environment: SapEnvironment
    system_type: SystemType
    tags: list[str]
    connection: ConnectionData
    credentials: Credentials
    run_test: bool = True
    activate: bool = False


@dataclass(frozen=True, kw_only=True)
class SystemChanges:
    """Campos a modificar; ``None`` = sin cambios (``ca_bundle_pem=""`` elimina la CA)."""

    version: int | None = None
    name: str | None = None
    description: str | None = None
    environment: SapEnvironment | None = None
    system_type: SystemType | None = None
    tags: list[str] | None = None
    base_url: str | None = None
    sap_client: str | None = None
    language: str | None = None
    tls_verify: bool | None = None
    ca_bundle_pem: str | None = None
    proxy_url: str | None = None
    timeout_seconds: int | None = None
    max_concurrent_requests: int | None = None
    max_concurrent_runs: int | None = None


@dataclass(frozen=True, kw_only=True)
class DuplicateData:
    code: str
    name: str
    environment: SapEnvironment
    base_url: str | None
    sap_client: str | None
    credentials: Credentials | None  # None = copiar las credenciales del original


@dataclass(frozen=True, kw_only=True)
class SystemFilters:
    q: str | None = None
    environment: SapEnvironment | None = None
    status: SystemStatus | None = None
    system_type: SystemType | None = None
    tag: str | None = None


@dataclass(frozen=True, kw_only=True)
class SystemWithTest:
    system: SapSystem
    report: TestReport | None = None
    dependents: list[Dependent] = field(default_factory=list)


def credential_info(credentials: Credentials) -> dict[str, Any]:
    """Datos visibles de la credencial: nunca contraseñas, claves ni secretos."""
    match credentials:
        case BasicCredentials(username=username):
            return {"username": username}
        case OAuth2Credentials(token_url=url, client_id=client_id, scope=scope):
            return {"client_id": client_id, "token_url": url, "scope": scope}
        case X509Credentials():
            return inspect_client_certificate(credentials)


class SapSystemService:
    def __init__(self, session: Session, toolkit: SapToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.secrets = SecretStore(session, toolkit.cipher)

    # ================================================================ consultas
    def search(self, filters: SystemFilters, page: PageParams) -> tuple[list[SapSystem], int]:
        stmt = select(SapSystem).where(SapSystem.deleted_at.is_(None))
        if filters.q:
            like = f"%{filters.q.strip()}%"
            stmt = stmt.where(
                or_(
                    SapSystem.code.ilike(like),
                    SapSystem.name.ilike(like),
                    SapSystem.description.ilike(like),
                    SapSystem.base_url.ilike(like),
                )
            )
        if filters.environment:
            stmt = stmt.where(SapSystem.environment == filters.environment.value)
        if filters.status:
            stmt = stmt.where(SapSystem.status == filters.status.value)
        if filters.system_type:
            stmt = stmt.where(SapSystem.system_type == filters.system_type.value)
        if filters.tag:
            stmt = stmt.where(SapSystem.tags.contains([filters.tag.strip().lower()]))
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "code"
        column = (
            getattr(SapSystem, sort.lstrip("-"), None) if sort.lstrip("-") in SORTABLE else None
        )
        order = SapSystem.code if column is None else column
        stmt = stmt.order_by(order.desc() if sort.startswith("-") else order.asc(), SapSystem.code)
        items = list(self.session.scalars(stmt.offset(page.offset).limit(page.page_size)))
        return items, total

    def get(self, system_id: uuid.UUID) -> SapSystem:
        system = self.session.get(SapSystem, system_id)
        if system is None or system.deleted_at is not None:
            msg = "El sistema SAP no existe."
            raise NotFoundError(msg, code="SX-SAP-SYSTEM-NOT-FOUND")
        return system

    def dependents(self, system_id: uuid.UUID) -> list[Dependent]:
        return self.tk.dependents_of(self.session, system_id)

    def spec_for(self, system: SapSystem) -> ConnectionSpec:
        """Especificación de conexión con las credenciales descifradas (solo en memoria)."""
        if system.credential_secret_id is None:
            msg = "El sistema no tiene credenciales."
            raise BusinessRuleError(msg, code="SX-SAP-NO-CREDENTIALS")
        credentials = credentials_from_payload(self.secrets.read(system.credential_secret_id))
        ca = (
            self.secrets.read(system.ca_bundle_secret_id)["pem"]
            if system.ca_bundle_secret_id
            else None
        )
        return ConnectionSpec(
            base_url=system.base_url,
            sap_client=system.sap_client,
            language=system.language,
            credentials=credentials,
            tls_verify=system.tls_verify,
            ca_bundle_pem=ca,
            proxy_url=system.proxy_url,
            timeout_seconds=system.timeout_seconds,
            max_concurrent_requests=system.max_concurrent_requests,
            label=system.code,
        )

    # ================================================================ alta
    def create(self, data: NewSapSystem, actor: Principal, request: RequestInfo) -> SystemWithTest:
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        spec = self._spec(data.connection, validate_credentials(data.credentials), code)
        check_production_security(data.environment, spec.base_url, tls_verify=spec.tls_verify)
        info = credential_info(spec.credentials)
        ca_info = parse_ca_bundle(spec.ca_bundle_pem) if spec.ca_bundle_pem else None

        credential = self.secrets.create(
            CREDENTIAL_KIND, credentials_to_payload(spec.credentials), actor=actor.username
        )
        ca_secret = (
            self.secrets.create(CA_BUNDLE_KIND, {"pem": spec.ca_bundle_pem}, actor=actor.username)
            if spec.ca_bundle_pem
            else None
        )
        system = SapSystem(
            code=code,
            name=data.name.strip(),
            description=(data.description or "").strip() or None,
            environment=data.environment.value,
            system_type=data.system_type.value,
            base_url=spec.base_url,
            sap_client=spec.sap_client,
            language=spec.language,
            auth_type=auth_type_of(spec.credentials).value,
            credential_secret_id=credential.id,
            credential_info=info,
            tls_verify=spec.tls_verify,
            ca_bundle_secret_id=ca_secret.id if ca_secret else None,
            ca_bundle_info=ca_info,
            proxy_url=spec.proxy_url,
            timeout_seconds=spec.timeout_seconds,
            max_concurrent_requests=spec.max_concurrent_requests,
            max_concurrent_runs=check_run_limit(data.connection.max_concurrent_runs),
            tags=normalize_tags(data.tags),
            status=SystemStatus.DRAFT.value,
            created_by=actor.username,
            updated_by=actor.username,
        )
        self.session.add(system)
        self.session.flush()
        self._audit("sap_connection.created", system, actor, request, diff(None, _snapshot(system)))
        self.session.commit()

        report = None
        if data.run_test or data.activate:
            report = self._test_and_store(system, spec, actor, request)
            if data.activate and report.ok:
                self.activate(system.id, actor, request)
        return SystemWithTest(system=self.get(system.id), report=report)

    def duplicate(
        self, source_id: uuid.UUID, data: DuplicateData, actor: Principal, request: RequestInfo
    ) -> SystemWithTest:
        source = self.get(source_id)
        connection = ConnectionData(
            base_url=data.base_url or source.base_url,
            sap_client=data.sap_client or source.sap_client,
            language=source.language,
            tls_verify=source.tls_verify,
            ca_bundle_pem=(
                self.secrets.read(source.ca_bundle_secret_id)["pem"]
                if source.ca_bundle_secret_id
                else None
            ),
            proxy_url=source.proxy_url,
            timeout_seconds=source.timeout_seconds,
            max_concurrent_requests=source.max_concurrent_requests,
            max_concurrent_runs=source.max_concurrent_runs,
        )
        credentials = data.credentials or self.spec_for(source).credentials
        return self.create(
            NewSapSystem(
                code=data.code,
                name=data.name,
                description=source.description,
                environment=data.environment,
                system_type=SystemType(source.system_type),
                tags=list(source.tags),
                connection=connection,
                credentials=credentials,
                run_test=False,
            ),
            actor,
            request,
        )

    # ================================================================ edición
    def update(
        self, system_id: uuid.UUID, changes: SystemChanges, actor: Principal, request: RequestInfo
    ) -> SapSystem:
        system = self.get(system_id)
        if changes.version is not None and changes.version != system.version:
            msg = "El sistema fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        before = _snapshot(system)

        for fieldname, normalize in _NORMALIZERS.items():
            value = getattr(changes, fieldname)
            if value is not None:
                setattr(system, fieldname, normalize(value))
        timeout = changes.timeout_seconds or system.timeout_seconds
        concurrency = changes.max_concurrent_requests or system.max_concurrent_requests
        check_limits(timeout, concurrency)
        system.timeout_seconds, system.max_concurrent_requests = timeout, concurrency
        if changes.max_concurrent_runs is not None:
            system.max_concurrent_runs = check_run_limit(changes.max_concurrent_runs)
        if changes.ca_bundle_pem is not None:
            self._set_ca_bundle(system, changes.ca_bundle_pem, actor)
        check_production_security(
            SapEnvironment(system.environment), system.base_url, tls_verify=system.tls_verify
        )
        self._check_hosts(system.base_url, system.proxy_url, None)

        after = _snapshot(system)
        delta = diff(before, after)
        if not delta:
            return system
        if CONNECTION_FIELDS & delta.keys() and system.last_test_at is not None:
            system.test_outdated = True
        system.updated_by = actor.username
        system.updated_at = utcnow()
        self.session.flush()
        self._audit("sap_connection.updated", system, actor, request, delta)
        self.session.commit()
        return system

    def rotate_credentials(
        self,
        system_id: uuid.UUID,
        credentials: Credentials,
        actor: Principal,
        request: RequestInfo,
        *,
        version: int | None = None,
    ) -> SystemWithTest:
        """Sustituye la credencial (o cambia el tipo de autenticación) y la prueba enseguida."""
        system = self.get(system_id)
        if version is not None and version != system.version:
            msg = "El sistema fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        credentials = validate_credentials(credentials)
        self._check_hosts(system.base_url, system.proxy_url, credentials)
        info = credential_info(credentials)
        before = {"auth_type": system.auth_type, "auth_identity": system.credential_info}
        payload = credentials_to_payload(credentials)
        if system.credential_secret_id is None:
            system.credential_secret_id = self.secrets.create(
                CREDENTIAL_KIND, payload, actor=actor.username
            ).id
        else:
            self.secrets.replace(system.credential_secret_id, payload)
        system.auth_type = auth_type_of(credentials).value
        system.credential_info = info
        system.test_outdated = system.last_test_at is not None
        system.updated_by = actor.username
        system.updated_at = utcnow()
        self.session.flush()
        changes = diff(before, {"auth_type": system.auth_type, "auth_identity": info})
        changes["credentials"] = {"before": "***", "after": "***"}
        self._audit("sap_connection.credentials_rotated", system, actor, request, changes)
        self.session.commit()
        report = self._test_and_store(system, self.spec_for(system), actor, request)
        return SystemWithTest(system=self.get(system.id), report=report)

    # ================================================================ pruebas
    def test(self, system_id: uuid.UUID, actor: Principal, request: RequestInfo) -> SystemWithTest:
        system = self.get(system_id)
        report = self._test_and_store(system, self.spec_for(system), actor, request)
        return SystemWithTest(system=self.get(system.id), report=report)

    def test_unsaved(
        self,
        code: str | None,
        connection: ConnectionData,
        credentials: Credentials | None,
        actor: Principal,
        request: RequestInfo,
        *,
        reuse_from: uuid.UUID | None = None,
    ) -> TestReport:
        """Prueba del asistente, antes de guardar. Puede reutilizar la credencial guardada
        de un sistema existente (edición o duplicado) sin que vuelva al navegador."""
        label = code.strip().upper() if code and code.strip() else "borrador"
        existing = self.get(reuse_from) if reuse_from else None
        if credentials is None:
            if existing is None:
                msg = "Indique las credenciales para probar la conexión."
                raise BusinessRuleError(msg, code="SX-SAP-CREDENTIALS-INCOMPLETE")
            credentials = self.spec_for(existing).credentials
        ca = connection.ca_bundle_pem
        if ca is None and existing is not None and existing.ca_bundle_secret_id:
            ca = self.secrets.read(existing.ca_bundle_secret_id)["pem"]
        spec = self._spec(
            ConnectionData(**{**connection.__dict__, "ca_bundle_pem": ca or None}),
            validate_credentials(credentials),
            label,
        )
        self.session.commit()  # cierra la transacción de lectura antes de llamar a SAP
        report = ConnectionTester(self.tk).run(spec)
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="sap_connection.tested",
                outcome=Outcome.SUCCESS if report.ok else Outcome.FAILURE,
                resource_type="sap_system",
                resource_id=str(existing.id) if existing else None,
                resource_name=existing.code if existing else label,
                details={"unsaved": True, "base_url": spec.base_url, **report.audit_details()},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return report

    def _test_and_store(
        self, system: SapSystem, spec: ConnectionSpec, actor: Principal, request: RequestInfo
    ) -> TestReport:
        self.session.commit()
        report = ConnectionTester(self.tk).run(spec)
        # UPDATE directo: guardar el resultado de una prueba no cambia la versión del sistema
        # (no debe invalidar un formulario de edición abierto).
        self.session.execute(
            update(SapSystem)
            .where(SapSystem.id == system.id)
            .values(
                last_test_at=report.finished_at,
                last_test_ok=report.ok,
                last_test_result=report.as_dict(),
                test_outdated=False,
            )
            .execution_options(synchronize_session=False)
        )
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="sap_connection.tested",
                outcome=Outcome.SUCCESS if report.ok else Outcome.FAILURE,
                resource_type="sap_system",
                resource_id=str(system.id),
                resource_name=system.code,
                details=report.audit_details(),
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        self.session.expire(system)
        return report

    # ================================================================ estado y baja
    def activate(self, system_id: uuid.UUID, actor: Principal, request: RequestInfo) -> SapSystem:
        system = self.get(system_id)
        if system.status == SystemStatus.ACTIVE:
            return system
        if not system.last_test_ok or system.test_outdated:
            msg = (
                "Pruebe la conexión antes de activarla: se exige una prueba correcta "
                "con la configuración y credenciales actuales."
            )
            raise BusinessRuleError(msg, code="SX-SAP-TEST-REQUIRED")
        return self._set_status(
            system,
            status=SystemStatus.ACTIVE,
            reason=None,
            action="sap_connection.activated",
            actor=actor,
            request=request,
        )

    def deactivate(
        self, system_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> SystemWithTest:
        system = self.get(system_id)
        dependents = self.dependents(system.id)
        if system.status != SystemStatus.INACTIVE:
            self._set_status(
                system,
                status=SystemStatus.INACTIVE,
                reason=reason,
                action="sap_connection.deactivated",
                actor=actor,
                request=request,
            )
        return SystemWithTest(system=system, dependents=dependents)

    def delete(
        self, system_id: uuid.UUID, reason: str | None, actor: Principal, request: RequestInfo
    ) -> None:
        system = self.get(system_id)
        blocking = [d for d in self.dependents(system.id) if d.active]
        if blocking:
            names = ", ".join(f"{d.kind} «{d.name}»" for d in blocking[:5])
            msg = f"No se puede eliminar: lo usan objetos activos ({names})."
            raise ConflictError(msg, code="SX-SAP-SYSTEM-IN-USE")
        self._audit(
            "sap_connection.deleted",
            system,
            actor,
            request,
            diff(_snapshot(system), None),
            reason=reason,
        )
        # Borrado lógico; las credenciales se destruyen (no se conservan secretos sin uso).
        secret_ids = [s for s in (system.credential_secret_id, system.ca_bundle_secret_id) if s]
        system.credential_secret_id = None
        system.ca_bundle_secret_id = None
        system.deleted_at = utcnow()
        system.status = SystemStatus.INACTIVE.value
        system.updated_by = actor.username
        self.session.flush()
        for secret_id in secret_ids:
            self.secrets.delete(secret_id)
        self.session.commit()

    # ================================================================ utilidades
    def _set_status(
        self,
        system: SapSystem,
        *,
        status: SystemStatus,
        reason: str | None,
        action: str,
        actor: Principal,
        request: RequestInfo,
    ) -> SapSystem:
        before = system.status
        system.status = status.value
        system.status_reason = (reason or "").strip() or None
        system.updated_by = actor.username
        system.updated_at = utcnow()
        self.session.flush()
        self._audit(
            action,
            system,
            actor,
            request,
            {"status": {"before": before, "after": status.value}},
            reason=system.status_reason,
        )
        self.session.commit()
        return system

    def _spec(self, data: ConnectionData, credentials: Credentials, label: str) -> ConnectionSpec:
        check_limits(data.timeout_seconds, data.max_concurrent_requests)
        ca = (data.ca_bundle_pem or "").strip() or None
        if ca:
            parse_ca_bundle(ca)
        spec = ConnectionSpec(
            base_url=normalize_base_url(data.base_url),
            sap_client=normalize_client(data.sap_client),
            language=normalize_language(data.language),
            credentials=credentials,
            tls_verify=data.tls_verify,
            ca_bundle_pem=ca,
            proxy_url=normalize_proxy_url(data.proxy_url),
            timeout_seconds=data.timeout_seconds,
            max_concurrent_requests=data.max_concurrent_requests,
            label=label,
        )
        self._check_hosts(spec.base_url, spec.proxy_url, credentials)
        return spec

    def _check_hosts(
        self, base_url: str, proxy_url: str | None, credentials: Credentials | None
    ) -> None:
        """Comprobación de hosts sin resolver DNS, para dar el error en su campo.

        La comprobación definitiva (con las IP resueltas) se hace al conectar.
        """
        targets = [("base_url", base_url), ("proxy_url", proxy_url)]
        if isinstance(credentials, OAuth2Credentials):
            targets.append(("credentials.token_url", credentials.token_url))
        for fieldname, url in targets:
            if not url:
                continue
            try:
                self.tk.host_policy.check_host(urlsplit(url).hostname or "")
            except HostNotAllowedError as exc:
                raise BusinessRuleError(
                    exc.detail,
                    code=exc.error_code,
                    errors=[FieldIssue(fieldname, exc.detail, exc.error_code)],
                ) from exc

    def _set_ca_bundle(self, system: SapSystem, pem: str, actor: Principal) -> None:
        pem = pem.strip()
        if not pem:
            if system.ca_bundle_secret_id:
                self.secrets.delete(system.ca_bundle_secret_id)
            system.ca_bundle_secret_id = None
            system.ca_bundle_info = None
            return
        info = parse_ca_bundle(pem)
        if system.ca_bundle_secret_id:
            self.secrets.replace(system.ca_bundle_secret_id, {"pem": pem})
        else:
            system.ca_bundle_secret_id = self.secrets.create(
                CA_BUNDLE_KIND, {"pem": pem}, actor=actor.username
            ).id
        system.ca_bundle_info = info

    def _ensure_code_free(self, code: str) -> None:
        exists = self.session.scalar(
            select(SapSystem.id).where(SapSystem.code == code, SapSystem.deleted_at.is_(None))
        )
        if exists is not None:
            msg = f"Ya existe un sistema SAP con el código «{code}»."
            raise ConflictError(msg, code="SX-SAP-CODE-TAKEN")

    def _audit(
        self,
        action: str,
        system: SapSystem,
        actor: Principal,
        request: RequestInfo,
        changes: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="sap_system",
                resource_id=str(system.id),
                resource_name=system.code,
                changes=changes or None,
                reason=reason,
            ),
            actor=actor.actor,
            request=request,
        )


def _snapshot(system: SapSystem) -> dict[str, Any]:
    return snapshot(
        {
            "code": system.code,
            "name": system.name,
            "description": system.description,
            "environment": system.environment,
            "system_type": system.system_type,
            "base_url": system.base_url,
            "sap_client": system.sap_client,
            "language": system.language,
            "auth_type": system.auth_type,
            # Solo datos visibles (usuario, client_id, certificado); nunca el secreto.
            "auth_identity": system.credential_info,
            "tls_verify": system.tls_verify,
            "ca_bundle": [c.get("subject") for c in system.ca_bundle_info or []] or None,
            "proxy_url": system.proxy_url,
            "timeout_seconds": system.timeout_seconds,
            "max_concurrent_requests": system.max_concurrent_requests,
            "max_concurrent_runs": system.max_concurrent_runs,
            "tags": list(system.tags),
            "status": system.status,
        }
    )


__all__ = [
    "AuthType",
    "ConnectionData",
    "DuplicateData",
    "NewSapSystem",
    "SapSystemService",
    "SystemChanges",
    "SystemFilters",
    "SystemWithTest",
    "credential_info",
]
