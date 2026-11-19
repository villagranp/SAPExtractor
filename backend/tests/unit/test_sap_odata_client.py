"""``SapODataClient`` con un transporte simulado: parámetros, reintentos, errores y catálogo."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from sapextract.sap_connections.domain.catalog import ServiceKind
from sapextract.sap_connections.domain.errors import (
    SapAuthenticationError,
    SapAuthorizationError,
    SapBusyError,
    SapDnsError,
    SapError,
    SapNetworkError,
    SapNotFoundError,
    SapOAuthError,
    SapServerError,
    SapTimeoutError,
)
from sapextract.sap_connections.domain.model import (
    BasicCredentials,
    ConnectionSpec,
    Credentials,
    OAuth2Credentials,
)
from sapextract.sap_connections.infrastructure.odata_client import (
    CATALOG_PATH,
    NO_RETRY,
    ConcurrencyLimiter,
    RetryPolicy,
    SapODataClient,
)

BASE = "https://gw.test:44300"
Handler = Callable[[httpx.Request], httpx.Response]


def _spec(credentials: Credentials | None = None, **kw: Any) -> ConnectionSpec:
    return ConnectionSpec(
        base_url=BASE,
        sap_client="300",
        language="ES",
        credentials=credentials or BasicCredentials(username="SXTECH", password="secreta"),
        timeout_seconds=kw.pop("timeout_seconds", 5),
        label="S4P_300",
        **kw,
    )


def _client(handler: Handler, *, retry: RetryPolicy = NO_RETRY, **kw: Any) -> SapODataClient:
    sleeps: list[float] = []
    client = SapODataClient(
        _spec(**kw), retry=retry, transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


def _json(body: Any, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json", **headers}
    )


def test_sends_client_language_format_and_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json({"d": {"EntitySets": []}})

    with _client(handler) as client:
        client.catalog_root()
    request = seen[0]
    assert request.url.path == f"{CATALOG_PATH}/"
    params = dict(request.url.params)
    assert params == {"sap-client": "300", "sap-language": "ES", "$format": "json"}
    assert request.headers["authorization"].startswith("Basic ")
    assert request.headers["accept"] == "application/json"


def test_retries_transient_errors_with_backoff() -> None:
    statuses = iter([503, 429, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"retry-after": "2"} if status == 429 else {}
        return _json({"d": {}}, status, **headers)

    client = _client(handler, retry=RetryPolicy(max_attempts=3, base_delay=0.1))
    with client:
        assert client.catalog_root().status_code == 200
    sleeps = client.sleeps  # type: ignore[attr-defined]
    assert len(sleeps) == 2
    assert sleeps[1] == 2.0  # respeta Retry-After


def test_does_not_retry_business_errors() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with (
        _client(handler, retry=RetryPolicy(max_attempts=3)) as client,
        pytest.raises(SapAuthenticationError) as exc,
    ):
        client.catalog_root()
    assert calls == 1
    assert exc.value.error_code == "SX-SAP-AUTH"
    assert exc.value.status_code == 502  # nunca 401: no debe parecer una sesión caducada


@pytest.mark.parametrize(
    ("status", "body", "error", "sap_message"),
    [
        (
            403,
            {
                "error": {
                    "code": "/IWFND/CM_BEC/026",
                    "message": {"lang": "es", "value": "Sin S_SERVICE"},
                }
            },
            SapAuthorizationError,
            "Sin S_SERVICE",
        ),
        (
            404,
            {"error": {"code": "/IWFND/MED/170", "message": "No service"}},
            SapNotFoundError,
            "No service",
        ),
        (500, None, SapServerError, None),
    ],
)
def test_translates_sap_errors(
    status: int, body: dict[str, Any] | None, error: type[SapError], sap_message: str | None
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _json(body, status) if body else httpx.Response(status, text="boom")

    with _client(handler) as client, pytest.raises(error) as exc:
        client.catalog_root()
    assert exc.value.http_status == status
    assert exc.value.sap_message == sap_message


def test_parses_xml_error_bodies() -> None:
    xml = (
        '<?xml version="1.0"?><error xmlns="http://schemas.microsoft.com/ado/2007/08/'
        'dataservices/metadata"><code>SY/530</code><message xml:lang="es">Mandante no existe'
        "</message><innererror><transactionid>ABC123</transactionid></innererror></error>"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=xml, headers={"content-type": "application/xml"})

    with _client(handler) as client, pytest.raises(SapError) as exc:
        client.catalog_root()
    assert exc.value.error_code == "SX-SAP-BAD-REQUEST"
    assert exc.value.as_details() == {
        "http_status": 400,
        "sap_code": "SY/530",
        "sap_message": "Mandante no existe",
        "transaction_id": "ABC123",
    }


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.ReadTimeout("lento"), SapTimeoutError),
        (httpx.ConnectError("refused"), SapNetworkError),
    ],
)
def test_translates_transport_errors(raised: Exception, expected: type[SapError]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise raised

    with _client(handler) as client, pytest.raises(expected):
        client.catalog_root()


def test_dns_failure_is_reported_as_such() -> None:
    import socket  # noqa: PLC0415

    def handler(_: httpx.Request) -> httpx.Response:
        try:
            raise socket.gaierror(11001, "getaddrinfo failed")
        except socket.gaierror as exc:
            raise httpx.ConnectError("dns") from exc

    with _client(handler) as client, pytest.raises(SapDnsError) as exc:
        client.catalog_root()
    assert "gw.test" in exc.value.detail


def test_follows_next_links_on_the_configured_host() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if "$skiptoken" in request.url.params:
            return _json({"d": {"results": [{"n": 2}]}})
        # SAP informa su nombre interno de host en __next.
        return _json(
            {
                "d": {
                    "results": [{"n": 1}],
                    "__next": "http://sapinterno:8000/sap/opu/odata/sap/X/Set?$skiptoken=20",
                }
            }
        )

    with _client(handler) as client:
        pages = list(client.iter_pages("/sap/opu/odata/sap/X/Set"))
    assert pages == [[{"n": 1}], [{"n": 2}]]
    assert hosts == ["gw.test", "gw.test"]


def _catalog_entry(name: str, sets: list[str] | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ID": f"{name}_0001",
        "Title": name,
        "TechnicalServiceName": name,
        "TechnicalServiceVersion": 1,
        "ServiceUrl": f"http://sapinterno:8000/sap/opu/odata/sap/{name}/",
    }
    if sets is not None:
        entry["EntitySets"] = {"results": [{"ID": s} for s in sets]}
    return entry


def test_catalog_with_expanded_entity_sets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["$expand"] == "EntitySets"
        return _json(
            {
                "d": {
                    "results": [
                        _catalog_entry("ZFI_WS_GL_SRV", ["EntityOfX", "DeltaLinksOfEntityOfX"]),
                        _catalog_entry("ZMM_CV_MAT_CDS", ["ZMM_CV_MAT"]),
                    ]
                }
            }
        )

    with _client(handler) as client:
        services = {s.technical_name: s for s in client.catalog_services()}
    assert services["ZFI_WS_GL_SRV"].kind is ServiceKind.ODP
    assert services["ZFI_WS_GL_SRV"].supports_delta is True
    assert services["ZFI_WS_GL_SRV"].service_path == "/sap/opu/odata/sap/ZFI_WS_GL_SRV"
    assert services["ZMM_CV_MAT_CDS"].kind is ServiceKind.CDS


def test_catalog_falls_back_when_expand_is_not_supported() -> None:
    expands: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        expands.append(request.url.params.get("$expand"))
        if "$expand" in request.url.params:
            return httpx.Response(400)
        return _json({"d": {"results": [_catalog_entry("ZUNKNOWN_SRV", None)]}})

    with _client(handler) as client:
        (service,) = client.catalog_services()
    assert expands == ["EntitySets", None]
    assert service.kind is ServiceKind.UNKNOWN


def test_oauth2_fetches_and_reuses_token() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth/token":
            token_calls += 1
            assert b"grant_type=client_credentials" in request.content
            return _json({"access_token": f"tok{token_calls}", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer tok1"
        return _json({"d": {}})

    credentials = OAuth2Credentials(
        token_url="https://auth.test/oauth/token", client_id="cid", client_secret="cs"
    )
    with _client(handler, credentials=credentials) as client:
        client.catalog_root()
        client.catalog_root()
    assert token_calls == 1


def test_oauth2_token_failure_is_explicit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401) if request.url.path == "/oauth/token" else _json({"d": {}})

    credentials = OAuth2Credentials(
        token_url="https://auth.test/oauth/token", client_id="cid", client_secret="cs"
    )
    with (
        _client(handler, credentials=credentials) as client,
        pytest.raises(SapOAuthError) as exc,
    ):
        client.catalog_root()
    assert exc.value.http_status == 401


def test_concurrency_limiter_rejects_when_full() -> None:
    limiter = ConcurrencyLimiter()
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        with limiter.slot("S4P", 1, timeout=1):
            entered.set()
            release.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    entered.wait(5)
    try:
        with pytest.raises(SapBusyError), limiter.slot("S4P", 1, timeout=0.05):
            pass  # pragma: no cover
    finally:
        release.set()
        worker.join()
    with limiter.slot("S4P", 1, timeout=0.05):
        pass


def test_metadata_request_asks_for_xml() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<x/>", headers={"content-type": "application/xml"})

    with _client(handler) as client:
        assert client.service_metadata("/sap/opu/odata/sap/X/") == b"<x/>"
    assert seen[0].url.path == "/sap/opu/odata/sap/X/$metadata"
    assert "$format" not in seen[0].url.params
    assert seen[0].headers["accept"] == "application/xml"
