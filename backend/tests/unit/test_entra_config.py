"""Configuración de Entra ID (It7a): validación, credenciales, aserción firmada y prueba."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc import jwt
from joserfc.jwk import RSAKey

from sapextract.core.exceptions import BusinessRuleError
from sapextract.identity.application.idp_service import run_test
from sapextract.identity.domain.entra import (
    AccessSettings,
    GroupMapping,
    StepName,
    StepStatus,
    access_warnings,
    check_group_mappings,
    describe_aadsts,
    expiry_warning,
    fingerprint,
    normalize_domains,
    normalize_guid,
    redirect_uri,
    redirect_warnings,
)
from sapextract.identity.infrastructure.entra_client import EntraClient, EntraError
from sapextract.identity.infrastructure.entra_credentials import (
    ASSERTION_TYPE,
    app_token_form,
    certificate_payload,
    client_assertion,
    secret_payload,
)
from tests.support.fake_entra import (
    AUTHORITY,
    CLIENT_ID,
    CLIENT_SECRET,
    TENANT_ID,
    FakeEntra,
    make_rsa_certificate,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _code(exc: pytest.ExceptionInfo[BusinessRuleError]) -> str:
    return exc.value.error_code


# ------------------------------------------------------------------ validación


def test_guid_is_normalized_and_validated() -> None:
    assert normalize_guid(f"  {TENANT_ID.upper()} ", "tenant_id", "X") == TENANT_ID
    with pytest.raises(BusinessRuleError) as exc:
        normalize_guid("contoso.onmicrosoft.com", "tenant_id", "El ID de inquilino")
    assert _code(exc) == "SX-IDP-GUID"
    assert exc.value.errors[0].field == "tenant_id"


def test_domains_are_lowercased_deduplicated_and_validated() -> None:
    assert normalize_domains(["Empresa.COM", "@empresa.com", " ", "filial.es"]) == [
        "empresa.com",
        "filial.es",
    ]
    with pytest.raises(BusinessRuleError):
        normalize_domains(["no es un dominio"])


def test_group_mappings_reject_duplicates_and_bad_ids() -> None:
    role = uuid.uuid4()
    group = "0b0b0b0b-1111-2222-3333-444444444444"
    ok = check_group_mappings(
        [GroupMapping(group_object_id=group.upper(), group_name=" Admins ", role_id=role)]
    )
    assert ok == [GroupMapping(group_object_id=group, group_name="Admins", role_id=role)]
    with pytest.raises(BusinessRuleError) as exc:
        check_group_mappings(
            [
                GroupMapping(group_object_id=group, group_name="A", role_id=role),
                GroupMapping(group_object_id=group, group_name="B", role_id=role),
            ]
        )
    assert _code(exc) == "SX-IDP-GROUP-DUPLICATE"
    with pytest.raises(BusinessRuleError) as exc:
        check_group_mappings([GroupMapping(group_object_id="x", group_name="A", role_id=role)])
    assert exc.value.errors[0].field == "mappings.0.group_object_id"


def test_redirect_uri_points_to_the_api_callback() -> None:
    uri = redirect_uri("https://sx.empresa.local/", "/api/v1")
    assert uri == "https://sx.empresa.local/api/v1/auth/entra/callback"
    assert redirect_warnings(uri) == []
    assert redirect_warnings("http://localhost:8000/api/v1/auth/entra/callback") == []
    assert redirect_warnings("http://sx.empresa.local/api/v1/auth/entra/callback")


def test_expiry_warning_thresholds() -> None:
    assert expiry_warning(None, NOW) is None
    assert expiry_warning(NOW + timedelta(days=90), NOW) is None
    assert "20/12/2026" in (expiry_warning(NOW + timedelta(days=30), NOW) or "")
    assert "caducó" in (expiry_warning(NOW - timedelta(seconds=1), NOW) or "")


def test_access_warnings() -> None:
    nobody = AccessSettings(
        jit_provisioning=False,
        default_role_id=None,
        require_group_membership=True,
        group_mappings=0,
    )
    assert len(access_warnings(nobody)) == 2
    open_tenant = AccessSettings(
        jit_provisioning=True,
        default_role_id=uuid.uuid4(),
        require_group_membership=False,
        group_mappings=0,
    )
    assert any("cualquier usuario" in w for w in access_warnings(open_tenant))


def test_aadsts_codes_are_translated() -> None:
    message, code = describe_aadsts([7000215], "x")
    assert code == "AADSTS7000215"
    assert "valor del secreto" in message
    assert describe_aadsts([123], "Original") == ("Original", "AADSTS123")
    assert describe_aadsts([], "Original") == ("Original", None)


def test_fingerprint_changes_with_any_connection_field() -> None:
    base = fingerprint(TENANT_ID, CLIENT_ID, {"client_secret": "a"})
    assert base == fingerprint(TENANT_ID, CLIENT_ID, {"client_secret": "a"})
    assert base != fingerprint(TENANT_ID, CLIENT_ID, {"client_secret": "b"})
    assert base != fingerprint(TENANT_ID, CLIENT_ID.replace("a", "b"), {"client_secret": "a"})


# ------------------------------------------------------------------ credenciales


def test_secret_payload_requires_a_value() -> None:
    assert secret_payload("  s3cret ")["client_secret"] == "s3cret"
    with pytest.raises(BusinessRuleError):
        secret_payload("   ")


def test_certificate_payload_validates_and_drops_the_passphrase() -> None:
    pair = make_rsa_certificate(passphrase="clave-del-pem")
    payload, info = certificate_payload(pair.cert_pem, pair.key_pem, "clave-del-pem", now=NOW)
    assert payload["type"] == "certificate"
    assert "ENCRYPTED" not in payload["private_key_pem"]  # se guarda dentro del secreto cifrado
    cert = x509.load_pem_x509_certificate(pair.cert_pem.encode())
    assert info.thumbprint == cert.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303
    assert info.subject == "CN=SAPExtract pruebas"

    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload(pair.cert_pem, pair.key_pem, None, now=NOW)
    assert _code(exc) == "SX-IDP-KEY-PASSPHRASE"
    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload(pair.cert_pem, pair.key_pem, "otra", now=NOW)
    assert _code(exc) == "SX-IDP-KEY-INVALID"


def test_certificate_payload_rejects_mismatch_non_rsa_and_expired() -> None:
    a, b = make_rsa_certificate(), make_rsa_certificate()
    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload(a.cert_pem, b.key_pem, None, now=NOW)
    assert _code(exc) == "SX-IDP-KEY-MISMATCH"
    ec_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload(a.cert_pem, ec_key.decode(), None, now=NOW)
    assert _code(exc) == "SX-IDP-KEY-TYPE"
    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload(a.cert_pem, a.key_pem, None, now=NOW + timedelta(days=400))
    assert _code(exc) == "SX-IDP-CERT-EXPIRED"
    with pytest.raises(BusinessRuleError) as exc:
        certificate_payload("no es un pem", a.key_pem, None, now=NOW)
    assert _code(exc) == "SX-IDP-CERT-INVALID"


def test_client_assertion_follows_the_microsoft_format() -> None:
    pair = make_rsa_certificate()
    payload, _ = certificate_payload(pair.cert_pem, pair.key_pem, None, now=NOW)
    endpoint = f"{AUTHORITY}/{TENANT_ID}/oauth2/v2.0/token"
    assertion = client_assertion(payload, client_id=CLIENT_ID, token_endpoint=endpoint, now=NOW)

    header = json.loads(base64.urlsafe_b64decode(assertion.split(".")[0] + "=="))
    cert = x509.load_pem_x509_certificate(pair.cert_pem.encode())
    thumb = base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA256())).rstrip(b"=").decode()
    assert header == {"alg": "PS256", "typ": "JWT", "x5t#S256": thumb}
    public = cert.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    token = jwt.decode(assertion, RSAKey.import_key(public), algorithms=["PS256"])
    assert token.claims["aud"] == endpoint
    assert token.claims["iss"] == token.claims["sub"] == CLIENT_ID
    assert token.claims["exp"] - token.claims["iat"] == 300

    form = app_token_form(payload, client_id=CLIENT_ID, token_endpoint=endpoint, now=NOW)
    assert form["client_assertion_type"] == ASSERTION_TYPE
    assert "client_secret" not in form
    secret_form = app_token_form(
        secret_payload("x"), client_id=CLIENT_ID, token_endpoint=endpoint, now=NOW
    )
    assert secret_form["client_secret"] == "x"
    assert secret_form["scope"] == "https://graph.microsoft.com/.default"


# ------------------------------------------------------------------ cliente y prueba


def _client(fake: FakeEntra) -> EntraClient:
    return EntraClient(authority_url=AUTHORITY, transport=fake.transport())


def _statuses(report: object) -> dict[str, str]:
    return {s.name.value: s.status.value for s in report.steps}  # type: ignore[attr-defined]


def test_run_test_with_a_valid_secret() -> None:
    fake = FakeEntra()
    report = run_test(_client(fake), TENANT_ID, CLIENT_ID, secret_payload(CLIENT_SECRET), None)
    assert report.ok, report.as_dict()
    assert _statuses(report) == {"discovery": "ok", "keys": "ok", "credential": "ok"}
    # Lo que va a la auditoría no lleva la credencial.
    assert CLIENT_SECRET not in json.dumps(report.as_dict())


def test_run_test_with_a_registered_certificate() -> None:
    pair = make_rsa_certificate()
    fake = FakeEntra(certificates=[pair.cert_pem])
    payload, info = certificate_payload(pair.cert_pem, pair.key_pem, None, now=datetime.now(UTC))
    report = run_test(_client(fake), TENANT_ID, CLIENT_ID, payload, info.not_after)
    assert report.ok, report.as_dict()


def test_run_test_explains_entra_errors() -> None:
    fake = FakeEntra()
    unknown_tenant = run_test(
        _client(fake), "99999999-2222-3333-4444-555555555555", CLIENT_ID, None, None
    )
    assert _statuses(unknown_tenant) == {
        "discovery": "failed",
        "keys": "skipped",
        "credential": "skipped",
    }
    assert unknown_tenant.error_code == "AADSTS90002"

    bad_secret = run_test(_client(fake), TENANT_ID, CLIENT_ID, secret_payload("otro"), None)
    step = next(s for s in bad_secret.steps if s.name is StepName.CREDENTIAL)
    assert step.status is StepStatus.FAILED
    assert step.error_code == "AADSTS7000215"
    assert step.details["correlation_id"] == "00000000-correlation"

    wrong_client = run_test(
        _client(fake), TENANT_ID, CLIENT_ID.replace("a", "f"), secret_payload(CLIENT_SECRET), None
    )
    assert wrong_client.error_code == "AADSTS700016"

    pair = make_rsa_certificate()
    payload, _ = certificate_payload(pair.cert_pem, pair.key_pem, None, now=datetime.now(UTC))
    not_uploaded = run_test(_client(fake), TENANT_ID, CLIENT_ID, payload, None)
    assert not_uploaded.error_code == "AADSTS700027"

    missing = run_test(_client(fake), TENANT_ID, CLIENT_ID, None, None)
    assert missing.error_code == "SX-IDP-NO-CREDENTIAL"


def test_run_test_warns_about_expiring_credentials_and_network_errors() -> None:
    fake = FakeEntra()
    soon = datetime.now(UTC) + timedelta(days=10)
    report = run_test(_client(fake), TENANT_ID, CLIENT_ID, secret_payload(CLIENT_SECRET), soon)
    assert report.ok
    assert _statuses(report)["credential"] == "warning"

    fake.down = True
    offline = run_test(_client(fake), TENANT_ID, CLIENT_ID, secret_payload(CLIENT_SECRET), None)
    assert offline.error_code == "SX-IDP-NETWORK"


def test_client_rejects_endpoints_outside_the_authority() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": f"https://login.entra.test/{TENANT_ID}/v2.0",
                "authorization_endpoint": f"{AUTHORITY}/{TENANT_ID}/oauth2/v2.0/authorize",
                "token_endpoint": "https://attacker.example/token",
                "jwks_uri": f"{AUTHORITY}/{TENANT_ID}/discovery/v2.0/keys",
            },
        )

    client = EntraClient(authority_url=AUTHORITY, transport=httpx.MockTransport(handler))
    with pytest.raises(EntraError, match="fuera de"):
        client.discover(TENANT_ID)


def test_client_reports_non_json_answers() -> None:
    client = EntraClient(
        authority_url=AUTHORITY,
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>proxy</html>")),
    )
    with pytest.raises(EntraError, match="no es JSON"):
        client.discover(TENANT_ID)
