"""Contratos HTTP de las conexiones SAP.

Las credenciales solo se reciben (``SecretStr``, ``writeOnly``) y nunca se devuelven:
las respuestas muestran únicamente ``credential_info`` (usuario, client_id, certificado).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, SecretStr

from sapextract.sap_connections.application.explorer_service import (
    CatalogPage,
    EntitySetInfo,
    ServiceDetail,
)
from sapextract.sap_connections.application.system_service import ConnectionData
from sapextract.sap_connections.application.toolkit import Dependent
from sapextract.sap_connections.domain.catalog import (
    CatalogService,
    ClassificationSource,
    ServiceKind,
)
from sapextract.sap_connections.domain.metadata import EdmEntityType, EdmProperty, OdpRole
from sapextract.sap_connections.domain.model import (
    AuthType,
    BasicCredentials,
    Credentials,
    OAuth2Credentials,
    SapEnvironment,
    SystemStatus,
    SystemType,
    X509Credentials,
)
from sapextract.sap_connections.domain.testing import StepName, StepStatus, TestReport
from sapextract.sap_connections.infrastructure.models import SapSystem

PEM_MAX = 100_000


# ------------------------------------------------------------------ credenciales de entrada
class BasicCredentialsIn(BaseModel):
    auth_type: Literal["basic"]
    username: str = Field(min_length=1, max_length=40, description="Usuario técnico de SAP")
    password: SecretStr = Field(min_length=1, max_length=256)

    def to_domain(self) -> Credentials:
        return BasicCredentials(username=self.username, password=self.password.get_secret_value())


class X509CredentialsIn(BaseModel):
    auth_type: Literal["x509"]
    certificate_pem: str = Field(min_length=1, max_length=PEM_MAX)
    private_key_pem: SecretStr = Field(min_length=1, max_length=PEM_MAX)
    private_key_passphrase: SecretStr | None = Field(None, max_length=256)

    def to_domain(self) -> Credentials:
        return X509Credentials(
            certificate_pem=self.certificate_pem,
            private_key_pem=self.private_key_pem.get_secret_value(),
            private_key_passphrase=(
                self.private_key_passphrase.get_secret_value()
                if self.private_key_passphrase
                else None
            ),
        )


class OAuth2CredentialsIn(BaseModel):
    auth_type: Literal["oauth2_client_credentials"]
    token_url: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=200)
    client_secret: SecretStr = Field(min_length=1, max_length=500)
    scope: str | None = Field(None, max_length=500)

    def to_domain(self) -> Credentials:
        return OAuth2Credentials(
            token_url=self.token_url,
            client_id=self.client_id,
            client_secret=self.client_secret.get_secret_value(),
            scope=self.scope,
        )


CredentialsIn = Annotated[
    BasicCredentialsIn | X509CredentialsIn | OAuth2CredentialsIn,
    Field(discriminator="auth_type"),
]


# ------------------------------------------------------------------ peticiones
class ConnectionFields(BaseModel):
    base_url: str = Field(
        min_length=1, max_length=500, examples=["https://gateway.empresa.local:44300"]
    )
    sap_client: str = Field(min_length=1, max_length=3, examples=["300"])
    language: str = Field("ES", min_length=1, max_length=2)
    tls_verify: bool = True
    ca_bundle_pem: str | None = Field(
        None, max_length=PEM_MAX, description="CA propia (PEM) para validar el certificado"
    )
    proxy_url: str | None = Field(None, max_length=500)
    timeout_seconds: int = Field(60, ge=5, le=600)
    max_concurrent_requests: int = Field(4, ge=1, le=32)

    def connection(self) -> ConnectionData:
        return ConnectionData(
            base_url=self.base_url,
            sap_client=self.sap_client,
            language=self.language,
            tls_verify=self.tls_verify,
            ca_bundle_pem=self.ca_bundle_pem,
            proxy_url=self.proxy_url,
            timeout_seconds=self.timeout_seconds,
            max_concurrent_requests=self.max_concurrent_requests,
        )


class SapSystemCreateRequest(ConnectionFields):
    code: str = Field(min_length=1, max_length=30, examples=["S4P_300"])
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    environment: SapEnvironment
    system_type: SystemType
    tags: list[str] = Field(default_factory=list, max_length=20)
    credentials: CredentialsIn
    run_test: bool = Field(True, description="Probar la conexión al guardar")
    activate: bool = Field(False, description="Activar si la prueba es correcta")


class SapSystemTestRequest(ConnectionFields):
    """Prueba del asistente sin guardar."""

    code: str | None = Field(None, max_length=30)
    credentials: CredentialsIn | None = None
    reuse_credentials_from: uuid.UUID | None = Field(
        None,
        description="Usar la credencial guardada de este sistema (edición/duplicado) "
        "si no se envían credenciales",
    )


class SapSystemUpdateRequest(BaseModel):
    version: int | None = Field(None, description="Versión leída (bloqueo optimista)")
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    environment: SapEnvironment | None = None
    system_type: SystemType | None = None
    tags: list[str] | None = Field(None, max_length=20)
    base_url: str | None = Field(None, min_length=1, max_length=500)
    sap_client: str | None = Field(None, min_length=1, max_length=3)
    language: str | None = Field(None, min_length=1, max_length=2)
    tls_verify: bool | None = None
    ca_bundle_pem: str | None = Field(None, max_length=PEM_MAX, description="'' elimina la CA")
    proxy_url: str | None = Field(None, max_length=500, description="'' elimina el proxy")
    timeout_seconds: int | None = Field(None, ge=5, le=600)
    max_concurrent_requests: int | None = Field(None, ge=1, le=32)


class CredentialRotateRequest(BaseModel):
    credentials: CredentialsIn
    version: int | None = None


class SapSystemDuplicateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    environment: SapEnvironment
    base_url: str | None = Field(None, max_length=500)
    sap_client: str | None = Field(None, max_length=3)
    credentials: CredentialsIn | None = Field(
        None, description="Credenciales nuevas; si se omiten se copian las del original"
    )


class StatusReasonRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


# ------------------------------------------------------------------ respuestas
class CredentialInfo(BaseModel):
    """Datos visibles de la credencial (nunca el secreto)."""

    username: str | None = None
    client_id: str | None = None
    token_url: str | None = None
    scope: str | None = None
    certificate_subject: str | None = None
    certificate_issuer: str | None = None
    certificate_not_after: datetime | None = None


class CertificateInfo(BaseModel):
    subject: str
    issuer: str
    not_after: datetime
    serial: str | None = None


class TestStepResponse(BaseModel):
    __test__ = False
    name: StepName
    status: StepStatus
    message: str
    duration_ms: int
    error_code: str | None = None
    details: dict[str, Any]


class TestReportResponse(BaseModel):
    __test__ = False
    ok: bool
    summary: str
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime
    steps: list[TestStepResponse]

    @classmethod
    def of(cls, report: TestReport) -> TestReportResponse:
        return cls.model_validate(report.as_dict())

    @classmethod
    def from_stored(cls, data: dict[str, Any] | None) -> TestReportResponse | None:
        return cls.model_validate(data) if data else None


class DependentResponse(BaseModel):
    kind: str
    id: str
    name: str
    active: bool

    @classmethod
    def of(cls, d: Dependent) -> DependentResponse:
        return cls(kind=d.kind, id=d.id, name=d.name, active=d.active)


class SapSystemSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    environment: SapEnvironment
    system_type: SystemType
    base_url: str
    sap_client: str
    auth_type: AuthType
    status: SystemStatus
    tags: list[str]
    last_test_at: datetime | None
    last_test_ok: bool | None
    test_outdated: bool
    updated_at: datetime

    @classmethod
    def of(cls, s: SapSystem) -> SapSystemSummary:
        return cls(
            id=s.id,
            code=s.code,
            name=s.name,
            environment=SapEnvironment(s.environment),
            system_type=SystemType(s.system_type),
            base_url=s.base_url,
            sap_client=s.sap_client,
            auth_type=AuthType(s.auth_type),
            status=SystemStatus(s.status),
            tags=list(s.tags),
            last_test_at=s.last_test_at,
            last_test_ok=s.last_test_ok,
            test_outdated=s.test_outdated,
            updated_at=s.updated_at,
        )


class SapSystemDetail(SapSystemSummary):
    description: str | None
    language: str
    credential_info: CredentialInfo
    tls_verify: bool
    ca_bundle: list[CertificateInfo]
    proxy_url: str | None
    timeout_seconds: int
    max_concurrent_requests: int
    status_reason: str | None
    last_test_result: TestReportResponse | None
    created_at: datetime
    created_by: str | None
    updated_by: str | None
    version: int
    dependents: list[DependentResponse]

    @classmethod
    def of_detail(cls, s: SapSystem, dependents: list[Dependent] | None = None) -> SapSystemDetail:
        return cls(
            **SapSystemSummary.of(s).model_dump(),
            description=s.description,
            language=s.language,
            credential_info=CredentialInfo.model_validate(s.credential_info or {}),
            tls_verify=s.tls_verify,
            ca_bundle=[CertificateInfo.model_validate(c) for c in s.ca_bundle_info or []],
            proxy_url=s.proxy_url,
            timeout_seconds=s.timeout_seconds,
            max_concurrent_requests=s.max_concurrent_requests,
            status_reason=s.status_reason,
            last_test_result=TestReportResponse.from_stored(s.last_test_result),
            created_at=s.created_at,
            created_by=s.created_by,
            updated_by=s.updated_by,
            version=s.version,
            dependents=[DependentResponse.of(d) for d in dependents or []],
        )


class SapSystemWithTestResponse(BaseModel):
    system: SapSystemDetail
    test: TestReportResponse | None = None


# ------------------------------------------------------------------ explorador
class CatalogServiceResponse(BaseModel):
    technical_name: str
    version: int
    service_id: str
    title: str | None
    description: str | None
    service_path: str | None
    kind: ServiceKind
    classified_by: ClassificationSource | None
    supports_delta: bool | None
    entity_sets: list[str] | None
    odp_entity_sets: list[str]

    @classmethod
    def of(cls, s: CatalogService) -> CatalogServiceResponse:
        return cls(
            technical_name=s.technical_name,
            version=s.version,
            service_id=s.service_id,
            title=s.title,
            description=s.description,
            service_path=s.service_path,
            kind=s.kind,
            classified_by=s.classified_by,
            supports_delta=s.supports_delta,
            entity_sets=list(s.entity_sets) if s.entity_sets is not None else None,
            odp_entity_sets=list(s.odp_entity_sets),
        )


class CatalogPageResponse(BaseModel):
    items: list[CatalogServiceResponse]
    total: int
    page: int
    page_size: int
    fetched_at: datetime
    from_cache: bool
    counts: dict[str, int] = Field(description="Servicios del catálogo por tipo (sin filtros)")

    @classmethod
    def of(cls, data: CatalogPage, page: int, page_size: int) -> CatalogPageResponse:
        return cls(
            items=[CatalogServiceResponse.of(s) for s in data.items],
            total=data.total,
            page=page,
            page_size=page_size,
            fetched_at=data.fetched_at,
            from_cache=data.from_cache,
            counts=data.counts,
        )


class PropertyResponse(BaseModel):
    name: str
    edm_type: str
    nullable: bool
    is_key: bool
    max_length: int | None
    precision: int | None
    scale: int | None
    label: str | None
    filterable: bool
    sortable: bool
    display_format: str | None

    @classmethod
    def of(cls, p: EdmProperty) -> PropertyResponse:
        return cls(**p.__dict__)


class EntityTypeResponse(BaseModel):
    name: str
    namespace: str
    keys: list[str]
    properties: list[PropertyResponse]

    @classmethod
    def of(cls, et: EdmEntityType) -> EntityTypeResponse:
        return cls(
            name=et.name,
            namespace=et.namespace,
            keys=list(et.keys),
            properties=[PropertyResponse.of(p) for p in et.properties],
        )


class EntitySetResponse(BaseModel):
    name: str
    entity_type: str
    label: str | None
    odp_role: OdpRole | None
    odp_name: str | None
    supports_delta: bool
    change_tracking: bool
    pageable: bool
    countable: bool
    keys: list[str]
    properties: list[PropertyResponse]

    @classmethod
    def of(cls, info: EntitySetInfo) -> EntitySetResponse:
        es, et = info.entity_set, info.entity_type
        return cls(
            name=es.name,
            entity_type=es.entity_type,
            label=es.label,
            odp_role=info.odp_role,
            odp_name=info.odp_name,
            supports_delta=info.supports_delta,
            change_tracking=es.change_tracking,
            pageable=es.pageable,
            countable=es.countable,
            keys=list(et.keys) if et else [],
            properties=[PropertyResponse.of(p) for p in et.properties] if et else [],
        )


class ServiceEntitySetsResponse(BaseModel):
    service: CatalogServiceResponse
    odata_version: str
    entity_sets: list[EntitySetResponse]

    @classmethod
    def of(cls, detail: ServiceDetail) -> ServiceEntitySetsResponse:
        return cls(
            service=CatalogServiceResponse.of(detail.service),
            odata_version=detail.metadata.odata_version,
            entity_sets=[EntitySetResponse.of(e) for e in detail.entity_sets],
        )


class ServiceMetadataResponse(BaseModel):
    service: CatalogServiceResponse
    odata_version: str
    namespaces: list[str]
    entity_types: list[EntityTypeResponse]
    entity_sets: list[EntitySetResponse]

    @classmethod
    def of(cls, detail: ServiceDetail) -> ServiceMetadataResponse:
        return cls(
            service=CatalogServiceResponse.of(detail.service),
            odata_version=detail.metadata.odata_version,
            namespaces=list(detail.metadata.namespaces),
            entity_types=[EntityTypeResponse.of(et) for et in detail.metadata.entity_types],
            entity_sets=[EntitySetResponse.of(e) for e in detail.entity_sets],
        )
