"""Contratos HTTP de la configuración de Entra ID (pantalla "Proveedor de identidad").

El secreto y la clave privada solo se reciben (``SecretStr``, ``writeOnly``); las respuestas
muestran el tipo de credencial, su caducidad y los datos públicos del certificado.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from sapextract.identity.api.schemas import RoleRef
from sapextract.identity.application.idp_service import (
    CredentialInput,
    EntraDraft,
    EntraSettings,
    EntraView,
)
from sapextract.identity.domain.entra import CredentialType, GroupMapping, StepName, StepStatus
from sapextract.identity.domain.entra import TestReport as EntraReport
from sapextract.identity.infrastructure.models import IdpGroupMapping

PEM_MAX = 100_000
GUID_MAX = 64


class EntraCredentialIn(BaseModel):
    """Credencial nueva. Sin secreto (o sin certificado y clave) se conserva la guardada."""

    credential_type: CredentialType
    client_secret: SecretStr | None = Field(None, max_length=512)
    secret_expires_at: datetime | None = Field(
        None, description="Caducidad del secreto que muestra Entra (para avisar antes)"
    )
    certificate_pem: str | None = Field(None, max_length=PEM_MAX)
    private_key_pem: SecretStr | None = Field(None, max_length=PEM_MAX)
    private_key_passphrase: SecretStr | None = Field(None, max_length=256)

    def to_domain(self) -> CredentialInput:
        return CredentialInput(
            credential_type=self.credential_type,
            client_secret=self.client_secret.get_secret_value() if self.client_secret else None,
            secret_expires_at=self.secret_expires_at,
            certificate_pem=self.certificate_pem,
            private_key_pem=(
                self.private_key_pem.get_secret_value() if self.private_key_pem else None
            ),
            private_key_passphrase=(
                self.private_key_passphrase.get_secret_value()
                if self.private_key_passphrase
                else None
            ),
        )


class EntraProviderUpdateRequest(BaseModel):
    version: int | None = Field(
        None, description="Versión leída; vacía en la primera configuración"
    )
    tenant_id: str = Field(min_length=1, max_length=GUID_MAX)
    client_id: str = Field(min_length=1, max_length=GUID_MAX)
    credential: EntraCredentialIn | None = None
    jit_provisioning: bool = False
    default_role_id: uuid.UUID | None = None
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    require_group_membership: bool = True

    def to_domain(self) -> EntraSettings:
        return EntraSettings(
            version=self.version,
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            credential=self.credential.to_domain() if self.credential else None,
            jit_provisioning=self.jit_provisioning,
            default_role_id=self.default_role_id,
            allowed_domains=self.allowed_domains,
            require_group_membership=self.require_group_membership,
        )


class EntraTestRequest(BaseModel):
    """Valores del formulario sin guardar; lo que falte se toma de la configuración guardada."""

    tenant_id: str | None = Field(None, max_length=GUID_MAX)
    client_id: str | None = Field(None, max_length=GUID_MAX)
    credential: EntraCredentialIn | None = None

    def to_domain(self) -> EntraDraft:
        return EntraDraft(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            credential=self.credential.to_domain() if self.credential else None,
        )


class EntraStatusRequest(BaseModel):
    version: int
    reason: str | None = Field(None, max_length=500)


class GroupMappingIn(BaseModel):
    group_object_id: str = Field(min_length=1, max_length=GUID_MAX)
    group_name: str = Field(min_length=1, max_length=256)
    role_id: uuid.UUID

    def to_domain(self) -> GroupMapping:
        return GroupMapping(
            group_object_id=self.group_object_id, group_name=self.group_name, role_id=self.role_id
        )


class GroupMappingsRequest(BaseModel):
    version: int
    mappings: list[GroupMappingIn] = Field(max_length=500)


class GroupMappingResponse(BaseModel):
    id: uuid.UUID
    group_object_id: str
    group_name: str
    role: RoleRef

    @classmethod
    def of(cls, row: IdpGroupMapping) -> GroupMappingResponse:
        return cls(
            id=row.id,
            group_object_id=row.group_object_id,
            group_name=row.group_name,
            role=RoleRef.of(row.role),
        )


class EntraTestStepResponse(BaseModel):
    __test__ = False
    name: StepName
    status: StepStatus
    message: str
    duration_ms: int
    error_code: str | None = None
    details: dict[str, Any]


class EntraTestReportResponse(BaseModel):
    __test__ = False
    ok: bool
    summary: str
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime
    steps: list[EntraTestStepResponse]

    @classmethod
    def of(cls, report: EntraReport) -> EntraTestReportResponse:
        return cls.model_validate(report.as_dict())


class EntraCredentialInfo(BaseModel):
    credential_type: CredentialType
    expires_at: datetime | None
    updated_at: datetime | None
    certificate_subject: str | None
    certificate_thumbprint: str | None = Field(
        description="Huella SHA-1, la que muestra el portal de Entra"
    )


class EntraProviderResponse(BaseModel):
    configured: bool
    redirect_uri: str = Field(description="URI que hay que registrar en Entra (tipo Web)")
    warnings: list[str]
    version: int | None = None
    enabled: bool = False
    tenant_id: str | None = None
    client_id: str | None = None
    credential: EntraCredentialInfo | None = None
    jit_provisioning: bool = False
    default_role: RoleRef | None = None
    allowed_domains: list[str] = Field(default_factory=list)
    require_group_membership: bool = True
    group_mappings: list[GroupMappingResponse] = Field(default_factory=list)
    last_test: EntraTestReportResponse | None = None
    test_current: bool = Field(
        False, description="La última prueba correcta corresponde a la configuración guardada"
    )
    updated_at: datetime | None = None
    updated_by: str | None = None

    @classmethod
    def of(cls, view: EntraView) -> EntraProviderResponse:
        row = view.provider
        if row is None:
            return cls(configured=False, redirect_uri=view.redirect_uri, warnings=view.warnings)
        credential = None
        if row.credential_type is not None:
            credential = EntraCredentialInfo(
                credential_type=CredentialType(row.credential_type),
                expires_at=row.credential_expires_at,
                updated_at=row.credential_updated_at,
                certificate_subject=row.certificate_subject,
                certificate_thumbprint=row.certificate_thumbprint,
            )
        return cls(
            configured=True,
            redirect_uri=view.redirect_uri,
            warnings=view.warnings,
            version=row.version,
            enabled=row.enabled,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            credential=credential,
            jit_provisioning=row.jit_provisioning,
            default_role=RoleRef.of(row.default_role) if row.default_role else None,
            allowed_domains=list(row.allowed_domains),
            require_group_membership=row.require_group_membership,
            group_mappings=[GroupMappingResponse.of(m) for m in row.group_mappings],
            last_test=(
                EntraTestReportResponse.model_validate(row.last_test_result)
                if row.last_test_result
                else None
            ),
            test_current=view.test_current,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )
