"""Cliente HTTP de Entra ID: configuración OpenID del inquilino, claves de firma y tokens.

Solo habla con el servidor de ``SX_ENTRA_AUTHORITY_URL``: las URL que devuelve el
*discovery* tienen que estar en ese mismo servidor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "SAPExtract"


class EntraError(Exception):
    """Respuesta de error de Entra (o fallo de red), con sus códigos ``AADSTS``."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error: str | None = None,
        codes: list[int] | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        network: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.error = error
        self.codes = codes or []
        self.trace_id = trace_id
        self.correlation_id = correlation_id
        self.network = network

    @property
    def details(self) -> dict[str, Any]:
        """Datos para que IT localice el error en los registros de Entra (no son secretos)."""
        data: dict[str, Any] = {
            "http_status": self.status,
            "error": self.error,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
        }
        return {k: v for k, v in data.items() if v is not None}


@dataclass(frozen=True, kw_only=True)
class OpenIdConfiguration:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None


@dataclass(frozen=True, kw_only=True)
class EntraClient:
    authority_url: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def _check_host(self, url: str) -> str:
        expected, actual = urlsplit(self.authority_url), urlsplit(url)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            msg = f"Entra ID devolvió una dirección fuera de {expected.netloc}: {url}"
            raise EntraError(msg)
        return url

    def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            msg = f"Entra ID no respondió en {self.timeout:g} segundos."
            raise EntraError(msg, network=True) from None
        except httpx.HTTPError as exc:
            msg = f"No se pudo conectar con {urlsplit(url).netloc}: {type(exc).__name__}."
            raise EntraError(msg, network=True) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            msg = f"Respuesta inesperada de Entra ID (HTTP {response.status_code}, no es JSON)."
            raise EntraError(msg, status=response.status_code)
        if response.is_error:
            codes = [c for c in body.get("error_codes", []) if isinstance(c, int)]
            description = str(body.get("error_description") or body.get("error") or "")
            raise EntraError(
                description.splitlines()[0] if description else f"HTTP {response.status_code}",
                status=response.status_code,
                error=body.get("error"),
                codes=codes,
                trace_id=body.get("trace_id"),
                correlation_id=body.get("correlation_id"),
            )
        return body

    def discover(self, tenant_id: str) -> OpenIdConfiguration:
        url = f"{self.authority_url.rstrip('/')}/{tenant_id}/v2.0/.well-known/openid-configuration"
        body = self._send("GET", url)
        try:
            config = OpenIdConfiguration(
                issuer=str(body["issuer"]),
                authorization_endpoint=self._check_host(str(body["authorization_endpoint"])),
                token_endpoint=self._check_host(str(body["token_endpoint"])),
                jwks_uri=self._check_host(str(body["jwks_uri"])),
                end_session_endpoint=body.get("end_session_endpoint"),
            )
        except KeyError as exc:
            msg = f"La configuración OpenID del inquilino no trae {exc.args[0]}."
            raise EntraError(msg) from None
        if tenant_id not in config.issuer:
            msg = "El emisor de la configuración OpenID no corresponde al inquilino indicado."
            raise EntraError(msg)
        return config

    def signing_keys(self, jwks_uri: str) -> list[dict[str, Any]]:
        body = self._send("GET", self._check_host(jwks_uri))
        keys = body.get("keys")
        if not isinstance(keys, list):
            msg = "El conjunto de claves (JWKS) no trae claves."
            raise EntraError(msg)
        return [k for k in keys if isinstance(k, dict) and k.get("use", "sig") == "sig"]

    def request_token(self, token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
        return self._send("POST", self._check_host(token_endpoint), data=form)
