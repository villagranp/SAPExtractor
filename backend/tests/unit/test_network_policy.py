"""Hosts permitidos para conexiones salientes (plan 13, C-07 a)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from sapextract.core.config import Environment, Settings
from sapextract.core.http_guard import guarded_transport
from sapextract.core.network import HostNotAllowedError, HostPolicy, parse_entry
from sapextract.core.security.crypto import generate_master_key
from sapextract.sap_connections.domain.model import BasicCredentials, ConnectionSpec
from sapextract.sap_connections.infrastructure.network_probe import probe
from sapextract.sap_connections.infrastructure.odata_client import NO_RETRY, SapODataClient
from tests.conftest import UNREACHABLE_DB

Resolver = Callable[..., list[tuple[Any, ...]]]


def _fake_dns(mapping: dict[str, list[str]]) -> Resolver:
    def getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]

    return getaddrinfo


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, list[str]]], None]:
    def install(mapping: dict[str, list[str]]) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _fake_dns(mapping))

    return install


def test_entries_are_names_or_networks() -> None:
    assert parse_entry("10.0.0.0/8") == ipaddress.ip_network("10.0.0.0/8")
    assert parse_entry("10.1.2.3") == ipaddress.ip_network("10.1.2.3/32")
    assert parse_entry("*.Empresa.Local") == "empresa.local"
    assert parse_entry(".empresa.local") == "empresa.local"
    for bad in ("http://gw", "gw:443", "gw/ruta", "a b"):
        with pytest.raises(ValueError, match="no válida"):
            parse_entry(bad)


def test_metadata_and_link_local_are_always_blocked() -> None:
    open_policy = HostPolicy(allow_loopback=True)
    for host in ("169.254.169.254", "fe80::1", "0.0.0.0", "::ffff:169.254.169.254"):  # noqa: S104
        with pytest.raises(HostNotAllowedError) as exc:
            open_policy.check_host(host)
        assert exc.value.error_code == "SX-NET-HOST-NOT-ALLOWED"


def test_loopback_only_in_local_development() -> None:
    HostPolicy(allow_loopback=True).check_host("127.0.0.1")
    with pytest.raises(HostNotAllowedError, match="loopback"):
        HostPolicy(allow_loopback=False).check_host("127.0.0.1")
    with pytest.raises(HostNotAllowedError):
        HostPolicy(allow_loopback=False).check_host("::1")


def test_allow_list_by_dns_suffix() -> None:
    policy = HostPolicy(allowed=("empresa.local",))
    policy.check_host("gateway.empresa.local")
    policy.check_host("EMPRESA.LOCAL")
    with pytest.raises(HostNotAllowedError, match="SX_SAP_ALLOWED_HOSTS"):
        policy.check_host("gateway.empresa.local.evil.com")
    with pytest.raises(HostNotAllowedError):
        policy.check_host("otroempresa.local")
    with pytest.raises(HostNotAllowedError):
        policy.check_host("10.0.0.5")  # una IP literal no coincide con un nombre


def test_allow_list_by_network_decides_on_resolved_ips(
    dns: Callable[[dict[str, list[str]]], None],
) -> None:
    policy = HostPolicy(allowed=("10.20.0.0/16",))
    policy.check_host("gw.cualquiera")  # sin resolver no se puede decidir: pasa al conectar
    dns(
        {
            "gw.ok": ["10.20.1.5"],
            "gw.fuera": ["192.168.1.5"],
            "gw.mixto": ["10.20.1.5", "169.254.169.254"],
        }
    )
    assert policy.resolve("gw.ok", 443) == ["10.20.1.5"]
    with pytest.raises(HostNotAllowedError, match="fuera de la lista"):
        policy.resolve("gw.fuera", 443)
    # Si alguna IP está bloqueada, se rechaza el nombre entero.
    with pytest.raises(HostNotAllowedError, match="enlace local"):
        policy.resolve("gw.mixto", 443)


def test_dns_that_points_to_metadata_is_rejected_even_without_allow_list(
    dns: Callable[[dict[str, list[str]]], None],
) -> None:
    dns({"rebind.example": ["169.254.169.254"]})
    with pytest.raises(HostNotAllowedError):
        HostPolicy(allow_loopback=True).resolve("rebind.example", 80)


def test_guarded_transport_validates_before_connecting(
    dns: Callable[[dict[str, list[str]]], None],
) -> None:
    import ssl  # noqa: PLC0415

    dns({"meta.example": ["169.254.169.254"]})
    transport = guarded_transport(
        HostPolicy(allow_loopback=True), verify=ssl.create_default_context()
    )
    with httpx.Client(transport=transport) as client, pytest.raises(HostNotAllowedError):
        client.get("http://meta.example/latest/meta-data/")


def _spec(url: str, **kw: Any) -> ConnectionSpec:
    return ConnectionSpec(
        base_url=url,
        sap_client="100",
        language="ES",
        credentials=BasicCredentials(username="U", password="p"),
        timeout_seconds=5,
        **kw,
    )


def test_sap_client_and_probe_apply_the_policy(dns: Callable[[dict[str, list[str]]], None]) -> None:
    dns({"gw.interno": ["127.0.0.1"]})
    strict = HostPolicy(allow_loopback=False)
    with (
        SapODataClient(
            _spec("http://gw.interno:8000"), retry=NO_RETRY, host_policy=strict
        ) as client,
        pytest.raises(HostNotAllowedError),
    ):
        client.catalog_root()
    with pytest.raises(HostNotAllowedError):
        probe(_spec("http://gw.interno:8000"), strict)
    # Con proxy, el destino se comprueba por nombre antes de enviar nada.
    named = HostPolicy(allowed=("empresa.local",))
    with pytest.raises(HostNotAllowedError):
        SapODataClient(
            _spec("http://gw.otro.com", proxy_url="http://proxy.empresa.local:3128"),
            host_policy=named,
        )


def test_settings_validate_network_options() -> None:
    base: dict[str, Any] = {"database_url": UNREACHABLE_DB}
    settings = Settings(
        environment=Environment.TEST,
        sap_allowed_hosts=["empresa.local", "10.0.0.0/8"],
        trusted_proxies=["10.0.0.0/24"],
        **base,
    )
    policy = settings.sap_host_policy
    assert policy.restricted
    assert policy.allow_loopback
    with pytest.raises(ValidationError, match="no válida"):
        Settings(environment=Environment.TEST, sap_allowed_hosts=["http://x"], **base)
    with pytest.raises(ValidationError, match="SX_TRUSTED_PROXIES"):
        Settings(environment=Environment.TEST, trusted_proxies=["proxy.local"], **base)

    production: dict[str, Any] = {
        **base,
        "environment": Environment.PRD,
        "master_key": generate_master_key(),
        "log_json": True,
    }
    with pytest.raises(ValidationError, match="SX_SAP_ALLOWED_HOSTS"):
        Settings(**production)
    with pytest.raises(ValidationError, match="SX_DEST_ALLOWED_HOSTS"):
        Settings(**production, sap_allowed_hosts=["empresa.local"])
    production["dest_allowed_hosts"] = ["snowflakecomputing.com"]
    with pytest.raises(ValidationError, match="SX_TRUSTED_PROXIES"):
        Settings(**production, sap_allowed_hosts=["empresa.local"], trusted_proxies=["*"])
    prd = Settings(**production, sap_allowed_hosts=["empresa.local"])
    assert prd.sap_host_policy.allow_loopback is False
