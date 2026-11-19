"""Estrategias de autenticación contra SAP Gateway (plan 04 §1, tipos de autenticación).

* ``basic``: usuario técnico de comunicación + contraseña.
* ``x509``: certificado de cliente; se aplica en el contexto TLS (ver ``certificates.py``).
* ``oauth2_client_credentials``: token *bearer* obtenido del servidor de autorización
  (S/4HANA Cloud / BTP) y renovado antes de caducar.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator

import httpx

from sapextract.sap_connections.domain.errors import SapOAuthError
from sapextract.sap_connections.domain.model import (
    BasicCredentials,
    Credentials,
    OAuth2Credentials,
    X509Credentials,
)

TOKEN_REFRESH_MARGIN_SECONDS = 60


class OAuth2ClientCredentialsAuth(httpx.Auth):
    """Obtiene y cachea el token; ante un 401 lo renueva una vez y repite la petición."""

    def __init__(self, credentials: OAuth2Credentials, token_client: httpx.Client) -> None:
        self._credentials = credentials
        self._client = token_client
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        response = yield request
        if response.status_code == httpx.codes.UNAUTHORIZED:
            request.headers["Authorization"] = f"Bearer {self._get_token(force=True)}"
            yield request

    def _get_token(self, *, force: bool = False) -> str:
        with self._lock:
            if force or self._token is None or time.monotonic() >= self._expires_at:
                self._token, lifetime = self._fetch()
                self._expires_at = time.monotonic() + max(
                    lifetime - TOKEN_REFRESH_MARGIN_SECONDS, 0
                )
            return self._token

    def _fetch(self) -> tuple[str, float]:
        data = {"grant_type": "client_credentials"}
        if self._credentials.scope:
            data["scope"] = self._credentials.scope
        try:
            response = self._client.post(
                self._credentials.token_url,
                data=data,
                auth=(self._credentials.client_id, self._credentials.client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            msg = f"No se pudo contactar con el servidor de tokens OAuth ({type(exc).__name__})."
            raise SapOAuthError(msg) from exc
        if response.status_code != httpx.codes.OK:
            msg = (
                f"El servidor de tokens OAuth respondió HTTP {response.status_code}: "
                "revise client_id, client_secret y la URL del token."
            )
            raise SapOAuthError(msg, http_status=response.status_code)
        try:
            body = response.json()
            token = str(body["access_token"])
            lifetime = float(body.get("expires_in", 300))
        except (ValueError, KeyError, TypeError) as exc:
            msg = "La respuesta del servidor de tokens OAuth no contiene access_token."
            raise SapOAuthError(msg) from exc
        return token, lifetime


def build_auth(credentials: Credentials, token_client: httpx.Client) -> httpx.Auth | None:
    match credentials:
        case BasicCredentials(username=username, password=password):
            return httpx.BasicAuth(username, password)
        case OAuth2Credentials():
            return OAuth2ClientCredentialsAuth(credentials, token_client)
        case X509Credentials():
            return None  # el certificado va en el contexto TLS
