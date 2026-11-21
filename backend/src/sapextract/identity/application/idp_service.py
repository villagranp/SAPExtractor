"""Configuración de Microsoft Entra ID desde la plataforma (plan 02 §4, It7a).

Una sola fila por tipo de proveedor. La credencial vive cifrada en el almacén de secretos y
nunca vuelve al navegador. La prueba llama a Entra fuera de cualquier transacción abierta y
guarda su resultado con una huella de lo probado: Entra solo se activa si esa huella es la de
la configuración guardada.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff
from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from sapextract.core.secret_store import SecretStore
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.identity.domain.entra import (
    PROVIDER_TYPE,
    AccessSettings,
    CredentialType,
    GroupMapping,
    StepName,
    StepStatus,
    TestReport,
    TestStep,
    access_warnings,
    check_group_mappings,
    describe_aadsts,
    expiry_warning,
    fingerprint,
    issue,
    normalize_domains,
    normalize_guid,
    redirect_uri,
    redirect_warnings,
)
from sapextract.identity.domain.model import Principal
from sapextract.identity.domain.rules import ensure_can_grant
from sapextract.identity.infrastructure.entra_client import EntraClient, EntraError
from sapextract.identity.infrastructure.entra_credentials import (
    SECRET_KIND,
    app_token_form,
    certificate_payload,
    secret_payload,
)
from sapextract.identity.infrastructure.models import IdentityProvider, IdpGroupMapping, Role
from sapextract.identity.infrastructure.queries import get_role

RESOURCE_TYPE = "identity_provider"
RESOURCE_NAME = "Microsoft Entra ID"


@dataclass(frozen=True, kw_only=True)
class CredentialInput:
    """Credencial del formulario. Sin material (secreto o certificado) se conserva la guardada."""

    credential_type: CredentialType
    client_secret: str | None = None
    secret_expires_at: datetime | None = None
    certificate_pem: str | None = None
    private_key_pem: str | None = None
    private_key_passphrase: str | None = None

    @property
    def has_material(self) -> bool:
        if self.credential_type is CredentialType.CLIENT_SECRET:
            return bool(self.client_secret and self.client_secret.strip())
        return bool(
            (self.certificate_pem and self.certificate_pem.strip())
            or (self.private_key_pem and self.private_key_pem.strip())
        )


@dataclass(frozen=True, kw_only=True)
class EntraSettings:
    version: int | None  # None = primera configuración
    tenant_id: str
    client_id: str
    credential: CredentialInput | None
    jit_provisioning: bool
    default_role_id: uuid.UUID | None
    allowed_domains: list[str]
    require_group_membership: bool


@dataclass(frozen=True, kw_only=True)
class EntraDraft:
    """Lo que se prueba antes de guardar; lo que falte se toma de la configuración guardada."""

    tenant_id: str | None = None
    client_id: str | None = None
    credential: CredentialInput | None = None


@dataclass(frozen=True, kw_only=True)
class EntraView:
    provider: IdentityProvider | None
    redirect_uri: str
    test_current: bool  # la última prueba corresponde a la configuración guardada
    warnings: list[str]


@dataclass(frozen=True, kw_only=True)
class _Credential:
    payload: dict[str, Any]
    expires_at: datetime | None
    subject: str | None = None
    thumbprint: str | None = None


class IdentityProviderService:
    def __init__(self, session: Session, toolkit: IdentityToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.secrets = SecretStore(session, toolkit.cipher)

    # ================================================================ lectura
    def _row(self) -> IdentityProvider | None:
        return self.session.scalar(
            select(IdentityProvider).where(IdentityProvider.type == PROVIDER_TYPE)
        )

    def _require_row(self) -> IdentityProvider:
        row = self._row()
        if row is None:
            msg = "Entra ID aún no está configurado."
            raise NotFoundError(msg, code="SX-IDP-NOT-CONFIGURED")
        return row

    def view(self, base_url: str) -> EntraView:
        settings = self.tk.settings
        uri = redirect_uri(settings.public_url or base_url, settings.api_prefix)
        warnings = redirect_warnings(uri)
        if settings.public_url is None and not settings.is_local_dev:
            warnings.append(
                "SX_PUBLIC_URL no está definida: la URI de redirección se calcula a partir de "
                "la dirección con la que se abrió la plataforma."
            )
        row = self._row()
        if row is None:
            return EntraView(provider=None, redirect_uri=uri, test_current=False, warnings=warnings)
        warnings += access_warnings(
            AccessSettings(
                jit_provisioning=row.jit_provisioning,
                default_role_id=row.default_role_id,
                require_group_membership=row.require_group_membership,
                group_mappings=len(row.group_mappings),
            )
        )
        expiry = expiry_warning(row.credential_expires_at, utcnow())
        if expiry:
            warnings.append(expiry)
        return EntraView(
            provider=row,
            redirect_uri=uri,
            test_current=self._test_current(row),
            warnings=warnings,
        )

    def _stored_payload(self, row: IdentityProvider | None) -> dict[str, Any] | None:
        if row is None or row.secret_id is None:
            return None
        return self.secrets.read(row.secret_id)

    def _test_current(self, row: IdentityProvider) -> bool:
        if not row.last_test_ok or row.last_test_fingerprint is None:
            return False
        current = fingerprint(row.tenant_id, row.client_id, self._stored_payload(row))
        return current == row.last_test_fingerprint

    # ================================================================ guardar
    def save(self, data: EntraSettings, actor: Principal, request: RequestInfo) -> IdentityProvider:
        now = utcnow()
        row = self._row()
        self._check_version(row, data.version)
        tenant_id = normalize_guid(data.tenant_id, "tenant_id", "El ID de inquilino")
        client_id = normalize_guid(data.client_id, "client_id", "El client ID")
        domains = normalize_domains(data.allowed_domains)
        default_role = self._granted_role(
            data.default_role_id,
            previous=row.default_role_id if row else None,
            actor=actor,
        )
        credential = self._credential(data.credential, row, now=now)
        old_payload = self._stored_payload(row)
        if row is not None and row.enabled:
            # Con Entra activo, un inquilino, client ID o credencial erróneos dejarían a los
            # usuarios sin acceso: hay que probarlo antes (la prueba de borrador deja su huella).
            new_print = fingerprint(
                tenant_id, client_id, credential.payload if credential else old_payload
            )
            changed = new_print != fingerprint(row.tenant_id, row.client_id, old_payload)
            if changed and (not row.last_test_ok or row.last_test_fingerprint != new_print):
                msg = (
                    "Entra ID está activo: pruebe la nueva configuración (inquilino, client ID "
                    "o credencial) antes de guardarla."
                )
                raise BusinessRuleError(msg, code="SX-IDP-TEST-REQUIRED")

        before = _snapshot(row) if row else None
        if row is None:
            row = IdentityProvider(
                type=PROVIDER_TYPE,
                enabled=False,
                tenant_id=tenant_id,
                client_id=client_id,
                allowed_domains=domains,
                jit_provisioning=data.jit_provisioning,
                require_group_membership=data.require_group_membership,
                created_by=actor.username,
            )
            self.session.add(row)
        row.tenant_id = tenant_id
        row.client_id = client_id
        row.allowed_domains = domains
        row.jit_provisioning = data.jit_provisioning
        row.require_group_membership = data.require_group_membership
        row.default_role_id = default_role.id if default_role else None
        row.default_role = default_role
        row.updated_by = actor.username

        if credential is not None:
            self._store_credential(row, credential, actor, request, now=now)
        elif (
            data.credential is not None
            and data.credential.credential_type is CredentialType.CLIENT_SECRET
            and row.credential_type == CredentialType.CLIENT_SECRET
        ):
            row.credential_expires_at = data.credential.secret_expires_at
        self.session.flush()

        changes = diff(before, _snapshot(row))
        if changes or before is None:
            self._audit("idp.updated", actor, request, changes=changes)
        self.session.commit()
        return row

    def _check_version(self, row: IdentityProvider | None, version: int | None) -> None:
        if row is None and version is None:
            return
        if row is None or version != row.version:
            msg = "La configuración cambió mientras la editaba; recargue la página."
            raise ConflictError(msg, code="SX-IDP-VERSION")

    def _granted_role(
        self, role_id: uuid.UUID | None, *, previous: uuid.UUID | None, actor: Principal
    ) -> Role | None:
        if role_id is None:
            if previous is not None:
                ensure_can_grant(
                    actor.permissions, get_role(self.session, previous).permission_codes
                )
            return None
        role = get_role(self.session, role_id)
        if role_id != previous:
            ensure_can_grant(actor.permissions, role.permission_codes)
            if previous is not None:
                ensure_can_grant(
                    actor.permissions, get_role(self.session, previous).permission_codes
                )
        return role

    def _credential(
        self, data: CredentialInput | None, row: IdentityProvider | None, *, now: datetime
    ) -> _Credential | None:
        """Credencial nueva validada; ``None`` si se conserva la guardada."""
        if data is None or not data.has_material:
            if data is not None and (row is None or row.credential_type != data.credential_type):
                if data.credential_type is CredentialType.CLIENT_SECRET:
                    msg = "Indique el valor del secreto de cliente."
                    raise issue("client_secret", msg, "SX-IDP-SECRET-REQUIRED")
                msg = "Indique el certificado y su clave privada."
                raise issue("certificate_pem", msg, "SX-IDP-CERT-REQUIRED")
            return None
        if data.credential_type is CredentialType.CLIENT_SECRET:
            return _Credential(
                payload=secret_payload(data.client_secret or ""),
                expires_at=data.secret_expires_at,
            )
        if not (data.certificate_pem and data.private_key_pem):
            missing = "certificate_pem" if not data.certificate_pem else "private_key_pem"
            msg = "Indique el certificado y su clave privada."
            raise issue(missing, msg, "SX-IDP-CERT-REQUIRED")
        payload, info = certificate_payload(
            data.certificate_pem, data.private_key_pem, data.private_key_passphrase, now=now
        )
        return _Credential(
            payload=payload,
            expires_at=info.not_after,
            subject=info.subject,
            thumbprint=info.thumbprint,
        )

    def _store_credential(
        self,
        row: IdentityProvider,
        credential: _Credential,
        actor: Principal,
        request: RequestInfo,
        *,
        now: datetime,
    ) -> None:
        if row.secret_id is None:
            row.secret_id = self.secrets.create(
                SECRET_KIND, credential.payload, actor=actor.username
            ).id
        else:
            self.secrets.replace(row.secret_id, credential.payload)
        row.credential_type = credential.payload["type"]
        row.credential_expires_at = credential.expires_at
        row.credential_updated_at = now
        row.certificate_subject = credential.subject
        row.certificate_thumbprint = credential.thumbprint
        self._audit(
            "idp.credentials_rotated",
            actor,
            request,
            details={
                "client_auth": row.credential_type,
                "certificate_thumbprint": credential.thumbprint,
                "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
            },
        )

    # ================================================================ activar
    def enable(self, version: int, actor: Principal, request: RequestInfo) -> IdentityProvider:
        row = self._require_row()
        self._check_version(row, version)
        if row.secret_id is None or not self._test_current(row):
            msg = (
                "Antes de activar Entra ID, pruebe la configuración guardada y compruebe que "
                "la prueba sale correcta."
            )
            raise BusinessRuleError(msg, code="SX-IDP-TEST-REQUIRED")
        if not row.enabled:
            row.enabled = True
            row.updated_by = actor.username
            self._audit("idp.enabled", actor, request)
        self.session.commit()
        return row

    def disable(
        self, version: int, reason: str | None, actor: Principal, request: RequestInfo
    ) -> IdentityProvider:
        row = self._require_row()
        self._check_version(row, version)
        if row.enabled:
            row.enabled = False
            row.updated_by = actor.username
            self._audit("idp.disabled", actor, request, reason=(reason or "").strip() or None)
        self.session.commit()
        return row

    # ================================================================ grupos
    def replace_group_mappings(
        self,
        version: int,
        mappings: list[GroupMapping],
        actor: Principal,
        request: RequestInfo,
    ) -> IdentityProvider:
        row = self._require_row()
        self._check_version(row, version)
        wanted = check_group_mappings(mappings)
        current = {(m.group_object_id, m.role_id): m for m in row.group_mappings}
        target = {(m.group_object_id, m.role_id): m for m in wanted}
        roles = {m.role_id: get_role(self.session, m.role_id) for m in wanted}
        added = [target[k] for k in target.keys() - current.keys()]
        removed = [current[k] for k in current.keys() - target.keys()]
        for mapping in added:
            ensure_can_grant(actor.permissions, roles[mapping.role_id].permission_codes)
        for old in removed:
            ensure_can_grant(actor.permissions, old.role.permission_codes)

        renamed = []
        for key, mapping in target.items():
            existing = current.get(key)
            if existing is not None and existing.group_name != mapping.group_name:
                renamed.append({"group_object_id": key[0], "group_name": mapping.group_name})
                existing.group_name = mapping.group_name
        for old in removed:
            row.group_mappings.remove(old)
        for mapping in added:
            row.group_mappings.append(
                IdpGroupMapping(
                    group_object_id=mapping.group_object_id,
                    group_name=mapping.group_name,
                    role_id=mapping.role_id,
                    role=roles[mapping.role_id],
                    created_by=actor.username,
                )
            )
        if added or removed or renamed:
            row.updated_at = utcnow()  # sube la versión: el formulario abierto queda obsoleto
            row.updated_by = actor.username
            self._audit(
                "idp.group_mappings_changed",
                actor,
                request,
                details={
                    "added": [_mapping_ref(m, roles[m.role_id]) for m in added],
                    "removed": [_mapping_ref(m, m.role) for m in removed],
                    "renamed": renamed,
                },
            )
        self.session.commit()
        self.session.refresh(row)
        return row

    # ================================================================ prueba
    def test(self, draft: EntraDraft, actor: Principal, request: RequestInfo) -> TestReport:
        row = self._row()
        tenant_id = normalize_guid(
            draft.tenant_id or (row.tenant_id if row else ""), "tenant_id", "El ID de inquilino"
        )
        client_id = normalize_guid(
            draft.client_id or (row.client_id if row else ""), "client_id", "El client ID"
        )
        now = utcnow()
        credential = self._credential(draft.credential, row, now=now)
        if credential is not None:
            payload: dict[str, Any] | None = credential.payload
            expires_at = credential.expires_at
        else:
            payload = self._stored_payload(row)
            expires_at = row.credential_expires_at if row else None
            if (
                draft.credential
                and draft.credential.credential_type is CredentialType.CLIENT_SECRET
            ):
                expires_at = draft.credential.secret_expires_at or expires_at
        row_id = row.id if row else None
        self.session.commit()  # cierra la transacción de lectura antes de llamar a Entra

        report = run_test(self.tk.entra, tenant_id, client_id, payload, expires_at)
        if row_id is not None:
            # UPDATE directo: guardar el resultado no cambia la versión de la configuración.
            self.session.execute(
                update(IdentityProvider)
                .where(IdentityProvider.id == row_id)
                .values(
                    last_test_at=report.finished_at,
                    last_test_ok=report.ok,
                    last_test_result=report.as_dict(),
                    last_test_fingerprint=fingerprint(tenant_id, client_id, payload),
                )
                .execution_options(synchronize_session=False)
            )
        self._audit(
            "idp.tested",
            actor,
            request,
            outcome=Outcome.SUCCESS if report.ok else Outcome.FAILURE,
            details={"tenant_id": tenant_id, "client_id": client_id, **report.audit_details()},
        )
        self.session.commit()
        return report

    # ================================================================ utilidades
    def _audit(
        self,
        action: str,
        actor: Principal,
        request: RequestInfo,
        *,
        outcome: Outcome = Outcome.SUCCESS,
        changes: dict[str, Any] | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        row = self._row()
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                outcome=outcome,
                resource_type=RESOURCE_TYPE,
                resource_id=str(row.id) if row else None,
                resource_name=RESOURCE_NAME,
                changes=changes,
                reason=reason,
                details=details or {},
            ),
            actor=actor.actor,
            request=request,
        )


def _snapshot(row: IdentityProvider) -> dict[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "client_id": row.client_id,
        # Nombres que no confunden al enmascarado de la auditoría (no son secretos).
        "client_auth": row.credential_type,
        "certificate_thumbprint": row.certificate_thumbprint,
        "client_auth_expires_at": (
            row.credential_expires_at.isoformat() if row.credential_expires_at else None
        ),
        "jit_provisioning": row.jit_provisioning,
        "default_role": row.default_role.code if row.default_role else None,
        "allowed_domains": list(row.allowed_domains),
        "require_group_membership": row.require_group_membership,
    }


def _mapping_ref(mapping: GroupMapping | IdpGroupMapping, role: Role) -> dict[str, str]:
    return {
        "group_object_id": mapping.group_object_id,
        "group_name": mapping.group_name,
        "role": role.code,
    }


# ------------------------------------------------------------------ prueba por pasos


def _skipped(name: StepName) -> TestStep:
    return TestStep(
        name=name, status=StepStatus.SKIPPED, message="No se probó: falló un paso anterior."
    )


def _failed(name: StepName, exc: EntraError, fallback: str, started: float) -> TestStep:
    message, code = describe_aadsts(exc.codes, exc.message or fallback)
    if exc.network:
        code = "SX-IDP-NETWORK"
    return TestStep(
        name=name,
        status=StepStatus.FAILED,
        message=message,
        duration_ms=_elapsed(started),
        error_code=code or "SX-IDP-ERROR",
        details={"entra_message": exc.message, **exc.details},
    )


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def run_test(
    client: EntraClient,
    tenant_id: str,
    client_id: str,
    payload: dict[str, Any] | None,
    expires_at: datetime | None,
) -> TestReport:
    """Discovery del inquilino → claves de firma → token de aplicación con la credencial."""
    started_at = utcnow()
    steps: list[TestStep] = []

    t0 = time.perf_counter()
    try:
        config = client.discover(tenant_id)
    except EntraError as exc:
        steps.append(_failed(StepName.DISCOVERY, exc, "El inquilino no responde.", t0))
        steps += [_skipped(StepName.KEYS), _skipped(StepName.CREDENTIAL)]
        return TestReport(started_at=started_at, finished_at=utcnow(), steps=tuple(steps))
    steps.append(
        TestStep(
            name=StepName.DISCOVERY,
            status=StepStatus.OK,
            message="El inquilino publica su configuración OpenID.",
            duration_ms=_elapsed(t0),
            details={"issuer": config.issuer},
        )
    )

    t0 = time.perf_counter()
    try:
        keys = client.signing_keys(config.jwks_uri)
    except EntraError as exc:
        steps.append(_failed(StepName.KEYS, exc, "No se pudieron leer las claves.", t0))
        keys = []
    else:
        steps.append(
            TestStep(
                name=StepName.KEYS,
                status=StepStatus.OK if keys else StepStatus.FAILED,
                message=(
                    f"{len(keys)} claves de firma disponibles para validar los tokens."
                    if keys
                    else "Entra no publica claves de firma para este inquilino."
                ),
                duration_ms=_elapsed(t0),
                error_code=None if keys else "SX-IDP-NO-KEYS",
            )
        )

    t0 = time.perf_counter()
    if payload is None:
        steps.append(
            TestStep(
                name=StepName.CREDENTIAL,
                status=StepStatus.FAILED,
                message="Falta la credencial: indique un secreto de cliente o un certificado.",
                error_code="SX-IDP-NO-CREDENTIAL",
            )
        )
    else:
        now = utcnow()
        try:
            client.request_token(
                config.token_endpoint,
                app_token_form(
                    payload, client_id=client_id, token_endpoint=config.token_endpoint, now=now
                ),
            )  # el token se descarta: solo interesa que Entra lo emita
        except EntraError as exc:
            steps.append(_failed(StepName.CREDENTIAL, exc, "Entra rechazó la credencial.", t0))
        else:
            warning = expiry_warning(expires_at, now)
            steps.append(
                TestStep(
                    name=StepName.CREDENTIAL,
                    status=StepStatus.WARNING if warning else StepStatus.OK,
                    message=warning or "Entra acepta el client ID y la credencial.",
                    duration_ms=_elapsed(t0),
                    details={"client_auth": payload["type"]},
                )
            )
    return TestReport(started_at=started_at, finished_at=utcnow(), steps=tuple(steps))
