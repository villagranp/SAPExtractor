"""Reglas del dominio de conexiones SAP: normalización, seguridad, clasificación e informe."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sapextract.core.exceptions import BusinessRuleError
from sapextract.sap_connections.domain.catalog import (
    CatalogQuery,
    CatalogService,
    ClassificationSource,
    ServiceKind,
    classify,
    default_service_path,
)
from sapextract.sap_connections.domain.metadata import OdpRole, odp_name, odp_role
from sapextract.sap_connections.domain.model import (
    BasicCredentials,
    OAuth2Credentials,
    SapEnvironment,
    X509Credentials,
    check_limits,
    check_production_security,
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
from sapextract.sap_connections.domain.testing import (
    StepName,
    StepStatus,
    TestReport,
    TestStep,
)


def _code(exc: pytest.ExceptionInfo[BusinessRuleError]) -> str:
    return exc.value.error_code


def test_normalizes_identifiers() -> None:
    assert normalize_code(" s4p_300 ") == "S4P_300"
    assert normalize_client("300") == "300"
    assert normalize_language("es") == "ES"
    assert normalize_tags([" FI ", "fi", "Ventas"]) == ["fi", "ventas"]
    for bad in ("1AB", "AB", "S4P-300"):
        with pytest.raises(BusinessRuleError) as exc:
            normalize_code(bad)
        assert _code(exc) == "SX-SAP-CODE-INVALID"
    with pytest.raises(BusinessRuleError):
        normalize_client("30")
    with pytest.raises(BusinessRuleError):
        normalize_language("ESP")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gw.empresa.local:44300/", "https://gw.empresa.local:44300"),
        ("http://10.0.0.5:8000", "http://10.0.0.5:8000"),
    ],
)
def test_base_url_is_normalized(url: str, expected: str) -> None:
    assert normalize_base_url(url) == expected


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://gw", "SX-SAP-URL-INVALID"),
        ("gw.empresa.local", "SX-SAP-URL-INVALID"),
        ("https://user:clave@gw", "SX-SAP-URL-CREDENTIALS"),
        ("https://gw/?sap-client=100", "SX-SAP-URL-INVALID"),
        ("https://gw:99999", "SX-SAP-URL-INVALID"),
    ],
)
def test_base_url_rejects_unsafe_values(url: str, code: str) -> None:
    with pytest.raises(BusinessRuleError) as exc:
        normalize_base_url(url)
    assert _code(exc) == code
    assert exc.value.errors[0].field == "base_url"


def test_proxy_and_limits() -> None:
    assert normalize_proxy_url("  ") is None
    assert normalize_proxy_url("http://proxy:3128/") == "http://proxy:3128"
    with pytest.raises(BusinessRuleError):
        normalize_proxy_url("http://u:p@proxy:3128")
    check_limits(60, 4)
    with pytest.raises(BusinessRuleError):
        check_limits(1, 4)
    with pytest.raises(BusinessRuleError):
        check_limits(60, 100)


def test_production_requires_https_and_tls_verification() -> None:
    check_production_security(SapEnvironment.DEV, "http://gw", tls_verify=False)
    check_production_security(SapEnvironment.PRD, "https://gw", tls_verify=True)
    with pytest.raises(BusinessRuleError) as exc:
        check_production_security(SapEnvironment.PRD, "http://gw", tls_verify=True)
    assert _code(exc) == "SX-SAP-PRD-REQUIRES-HTTPS"
    with pytest.raises(BusinessRuleError) as exc:
        check_production_security(SapEnvironment.PRD, "https://gw", tls_verify=False)
    assert _code(exc) == "SX-SAP-PRD-REQUIRES-TLS-VERIFY"


def test_credentials_roundtrip_and_validation() -> None:
    for credentials in (
        BasicCredentials(username="SXTECH", password="x"),
        X509Credentials(certificate_pem="c", private_key_pem="k", private_key_passphrase=None),
        OAuth2Credentials(token_url="https://t", client_id="id", client_secret="s", scope=None),
    ):
        assert credentials_from_payload(credentials_to_payload(credentials)) == credentials
    assert validate_credentials(BasicCredentials(username=" sxtech ", password="p")) == (
        BasicCredentials(username="SXTECH", password="p")
    )
    with pytest.raises(BusinessRuleError):
        validate_credentials(BasicCredentials(username="SXTECH", password=""))
    oauth = validate_credentials(
        OAuth2Credentials(token_url="https://auth/token/", client_id=" a ", client_secret="s")
    )
    assert oauth == OAuth2Credentials(
        token_url="https://auth/token", client_id="a", client_secret="s", scope=None
    )
    # El secreto nunca aparece en la representación (p. ej. en un log por accidente).
    assert "p4ss" not in repr(BasicCredentials(username="U", password="p4ss"))


def test_odp_roles_from_entity_set_names() -> None:
    assert odp_role("EntityOf0FI_GL_14") is OdpRole.ENTITY
    assert odp_role("FactsOfZFI_CV_GL_ACCOUNT") is OdpRole.FACTS
    assert odp_role("DeltaLinksOfEntityOf0FI_GL_14") is OdpRole.DELTA_LINKS
    assert odp_role("EntityOf") is None
    assert odp_role("ZMM_CV_MATERIAL") is None
    assert odp_name("EntityOf2LIS_11_VAITM") == "2LIS_11_VAITM"
    assert odp_name("DeltaLinksOfEntityOfX") is None


def _service(name: str, sets: tuple[str, ...] | None) -> CatalogService:
    return CatalogService(technical_name=name, version=1, service_id=name, entity_sets=sets)


def test_classifies_services() -> None:
    odp = classify(
        _service("ZFI_WS_GL_SRV", ("EntityOf0FI_GL_14", "DeltaLinksOfEntityOf0FI_GL_14"))
    )
    assert (odp.kind, odp.supports_delta) == (ServiceKind.ODP, True)
    assert odp.classified_by is ClassificationSource.ENTITY_SETS
    assert odp.odp_entity_sets == ("EntityOf0FI_GL_14",)
    full_only = classify(_service("ZFI_WS_X_SRV", ("AttrOf0MATERIAL",)))
    assert (full_only.kind, full_only.supports_delta) == (ServiceKind.ODP, False)
    cds = classify(_service("ZMM_CV_MATERIAL_CDS", ("ZMM_CV_MATERIAL",)))
    assert cds.kind is ServiceKind.CDS
    by_name = classify(_service("ZMM_CV_OTHER_CDS", None))
    assert (by_name.kind, by_name.classified_by) == (ServiceKind.CDS, ClassificationSource.NAME)
    assert classify(_service("API_BUSINESS_PARTNER", ("A_BusinessPartner",))).kind is (
        ServiceKind.OTHER
    )
    assert classify(_service("ZUNKNOWN_SRV", None)).kind is ServiceKind.UNKNOWN


def test_catalog_query_filters_and_sorts() -> None:
    services = [
        classify(_service("ZB_CDS", ("B",))),
        classify(_service("ZA_SRV", ("EntityOfA",))),
        CatalogService(
            technical_name="ZC_SRV",
            version=1,
            service_id="c",
            description="Partidas de mayor",
            entity_sets=("EntityOfC",),
        ),
    ]
    services = [classify(s) for s in services]
    odp = CatalogQuery(kinds=frozenset({ServiceKind.ODP})).apply(services)
    assert [s.technical_name for s in odp] == ["ZA_SRV", "ZC_SRV"]
    assert [s.technical_name for s in CatalogQuery(search="mayor").apply(services)] == ["ZC_SRV"]
    assert len(CatalogQuery().apply(services)) == 3


def test_default_service_paths() -> None:
    assert default_service_path("ZX_SRV", 1) == "/sap/opu/odata/sap/ZX_SRV"
    assert default_service_path("ZX_SRV", 2) == "/sap/opu/odata/sap/ZX_SRV;v=0002"
    assert default_service_path("/IWBEP/GWSAMPLE", 1) == "/sap/opu/odata/IWBEP/GWSAMPLE"


def test_report_summary_and_outcome() -> None:
    now = datetime.now(UTC)
    ok = TestReport(
        started_at=now,
        finished_at=now,
        steps=(
            TestStep(name=StepName.NETWORK, status=StepStatus.WARNING, message="Caduca pronto."),
            TestStep(name=StepName.AUTHENTICATION, status=StepStatus.OK, message="ok"),
        ),
    )
    assert ok.ok
    assert ok.error_code is None
    assert ok.summary == "Conexión correcta. Caduca pronto."
    failed = TestReport(
        started_at=now,
        finished_at=now,
        steps=(
            TestStep(name=StepName.NETWORK, status=StepStatus.OK, message="ok"),
            TestStep(
                name=StepName.AUTHENTICATION,
                status=StepStatus.FAILED,
                message="SAP rechazó las credenciales",
                error_code="SX-SAP-AUTH",
            ),
        ),
    )
    assert not failed.ok
    assert failed.error_code == "SX-SAP-AUTH"
    assert failed.summary == "SAP rechazó las credenciales"
    assert failed.audit_details()["steps"] == {"network": "ok", "authentication": "failed"}
