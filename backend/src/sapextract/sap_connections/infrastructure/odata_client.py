"""``SapODataClient``: cliente HTTP de SAP Gateway reutilizado por conexiones, orígenes y motor.

Responsabilidades (plan 04 §3): sesión ``httpx`` con *keep-alive*, timeouts, TLS y proxy;
autenticación intercambiable; ``sap-client``/``sap-language`` en cada petición; reintentos
exponenciales con *jitter* solo ante 429/502/503/504 y errores de red; límite de peticiones
simultáneas por sistema; traducción de errores SAP a ``SX-SAP-*`` y traza de cada petición
(URL sin credenciales, estado, duración, bytes).
"""

from __future__ import annotations

import secrets
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qsl, urlsplit

import httpx
import structlog
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from sapextract.core.masking import mask_value
from sapextract.sap_connections.domain.catalog import (
    CatalogService,
    classify,
    default_service_path,
)
from sapextract.sap_connections.domain.errors import (
    SapAuthenticationError,
    SapAuthorizationError,
    SapBusyError,
    SapDnsError,
    SapError,
    SapNetworkError,
    SapNotFoundError,
    SapProxyError,
    SapRateLimitError,
    SapRequestError,
    SapResponseError,
    SapServerError,
    SapTimeoutError,
    SapTlsError,
)
from sapextract.sap_connections.domain.model import ConnectionSpec
from sapextract.sap_connections.infrastructure.auth import build_auth
from sapextract.sap_connections.infrastructure.certificates import build_ssl_context

log = structlog.get_logger(__name__)

CATALOG_PATH = "/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2"
RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_PAGES = 500
_rng = secrets.SystemRandom()

type Sleep = Callable[[float], None]


# ------------------------------------------------------------------ reintentos y concurrencia
@dataclass(frozen=True, kw_only=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Espera exponencial con *jitter* completo; respeta ``Retry-After`` (segundos)."""
        if retry_after and retry_after.strip().isdigit():
            return min(float(retry_after), self.max_delay)
        return _rng.uniform(0, min(self.max_delay, self.base_delay * 2 ** (attempt - 1)))


NO_RETRY = RetryPolicy(max_attempts=1)


class ConcurrencyLimiter:
    """Semáforo por sistema dentro del proceso (API o worker).

    La coordinación entre procesos (*advisory locks* en la BD) llega con los workers
    (iteraciones 5 y 6); hasta entonces el límite se aplica por proceso.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._semaphores: dict[str, tuple[int, threading.BoundedSemaphore]] = {}

    @contextmanager
    def slot(self, key: str, limit: int, timeout: float) -> Iterator[None]:
        with self._lock:
            current = self._semaphores.get(key)
            if current is None or current[0] != limit:
                current = (limit, threading.BoundedSemaphore(limit))
                self._semaphores[key] = current
            semaphore = current[1]
        if not semaphore.acquire(timeout=timeout):
            msg = f"Se alcanzó el máximo de {limit} peticiones simultáneas a SAP para «{key}»."
            raise SapBusyError(msg)
        try:
            yield
        finally:
            semaphore.release()


# ------------------------------------------------------------------ respuesta
@dataclass(frozen=True, kw_only=True)
class SapResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes
    elapsed_ms: int
    url: str

    def json(self) -> dict[str, Any]:
        try:
            data = httpx.Response(200, content=self.content).json()
        except ValueError as exc:
            msg = "SAP devolvió una respuesta que no es JSON."
            raise SapResponseError(msg, http_status=self.status_code) from exc
        if not isinstance(data, dict):
            msg = "SAP devolvió un JSON inesperado."
            raise SapResponseError(msg, http_status=self.status_code)
        return data


def odata_v2_results(body: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Filas y enlace ``__next`` de una respuesta OData V2 (``{"d": {"results": […]}}``)."""
    d = body.get("d")
    if isinstance(d, list):  # formato antiguo sin "results"
        return [r for r in d if isinstance(r, dict)], None
    if not isinstance(d, dict):
        msg = "La respuesta OData no tiene el formato V2 esperado ({'d': …})."
        raise SapResponseError(msg)
    results = d.get("results", [])
    if not isinstance(results, list):
        msg = "La respuesta OData no contiene una colección 'results'."
        raise SapResponseError(msg)
    nxt = d.get("__next")
    return [r for r in results if isinstance(r, dict)], nxt if isinstance(nxt, str) else None


# ------------------------------------------------------------------ errores
def _sap_error_body(content: bytes) -> tuple[str | None, str | None, str | None]:
    """(código, mensaje, transactionid) del cuerpo de error SAP (JSON V2/V4 o XML)."""
    text = content[:20_000]
    try:
        body = httpx.Response(200, content=text).json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        message = error.get("message")
        if isinstance(message, dict):
            message = message.get("value")
        inner = error.get("innererror") if isinstance(error.get("innererror"), dict) else {}
        return (
            str(error.get("code")) if error.get("code") else None,
            str(message)[:500] if message else None,
            str(inner.get("transactionid")) if inner.get("transactionid") else None,
        )
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError, DefusedXmlException, ValueError:
        return None, None, None
    values = {el.tag.rsplit("}", 1)[-1]: (el.text or "").strip() for el in root.iter()}
    return (
        values.get("code") or None,
        (values.get("message") or None),
        values.get("transactionid") or None,
    )


def error_for_status(status: int, content: bytes) -> SapError:
    sap_code, sap_message, transaction_id = _sap_error_body(content)
    suffix = f" SAP: {sap_message}" if sap_message else ""

    def make(kind: type[SapError], message: str) -> SapError:
        return kind(
            message,
            http_status=status,
            sap_code=sap_code,
            sap_message=sap_message,
            transaction_id=transaction_id,
        )

    if status == 401:
        return make(
            SapAuthenticationError,
            "SAP rechazó las credenciales (HTTP 401). Revise el usuario técnico y su "
            "contraseña o certificado.",
        )
    if status == 403:
        return make(
            SapAuthorizationError,
            "El usuario técnico no tiene autorización (HTTP 403). Revise sus roles en SAP "
            "(S_SERVICE para el servicio)." + suffix,
        )
    if status == 404:
        return make(
            SapNotFoundError,
            "El recurso no existe en SAP (HTTP 404): compruebe que el servicio está activo "
            "en /IWFND/MAINT_SERVICE." + suffix,
        )
    if status == 429:
        return make(SapRateLimitError, "SAP limitó las peticiones (HTTP 429)." + suffix)
    if status >= 500:
        return make(SapServerError, f"SAP respondió con un error interno (HTTP {status})." + suffix)
    return make(SapRequestError, f"SAP rechazó la petición (HTTP {status})." + suffix)


def _root_cause(exc: BaseException) -> BaseException:
    seen: set[int] = set()
    current: BaseException = exc
    while current.__cause__ is not None or current.__context__ is not None:
        nxt = current.__cause__ or current.__context__
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        current = nxt
    return current


def _contains(exc: BaseException, kind: type[BaseException]) -> BaseException | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, kind):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def error_for_transport(exc: httpx.HTTPError, *, host: str, timeout: float) -> SapError:
    if isinstance(exc, httpx.TimeoutException):
        msg = f"SAP ({host}) no respondió en {timeout:g} s."
        return SapTimeoutError(msg)
    if isinstance(exc, httpx.ProxyError):
        return SapProxyError(f"El proxy rechazó o no pudo abrir la conexión con {host}.")
    if tls := _contains(exc, ssl.SSLError):
        detail = getattr(tls, "verify_message", None) or getattr(tls, "reason", None) or str(tls)
        msg = f"Falló la conexión TLS con {host}: {detail}."
        return SapTlsError(msg)
    if _contains(exc, socket.gaierror):
        return SapDnsError(f"No se pudo resolver el nombre «{host}» (DNS).")
    cause = _root_cause(exc)
    msg = f"No se pudo conectar con {host} ({type(cause).__name__})."
    return SapNetworkError(msg)


# ------------------------------------------------------------------ cliente
class SapODataClient:
    """Cliente síncrono de un sistema SAP. Uso: ``with SapODataClient(spec) as client: …``."""

    def __init__(
        self,
        spec: ConnectionSpec,
        *,
        retry: RetryPolicy | None = None,
        limiter: ConcurrencyLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        self.spec = spec
        self.retry = retry or RetryPolicy()
        self._limiter = limiter or ConcurrencyLimiter()
        self._sleep = sleep
        self._host = urlsplit(spec.base_url).hostname or spec.base_url
        timeout = httpx.Timeout(spec.timeout_seconds, connect=min(15.0, spec.timeout_seconds))
        common: dict[str, Any] = {
            "timeout": timeout,
            "trust_env": False,  # el proxy se configura explícitamente por sistema
            "follow_redirects": False,
        }
        if transport is not None:
            common["transport"] = transport
        else:
            common["verify"] = build_ssl_context(spec)
            if spec.proxy_url:
                common["proxy"] = spec.proxy_url
        self._token_client = httpx.Client(**common)
        self._http = httpx.Client(
            auth=build_auth(spec.credentials, self._token_client),
            headers={"User-Agent": "SAPExtract", "Accept-Language": spec.language.lower()},
            **common,
        )

    # -------------------------------------------------------------- ciclo de vida
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._token_client.close()

    # -------------------------------------------------------------- peticiones
    def url_for(self, path_or_url: str) -> tuple[str, list[tuple[str, str]]]:
        """URL absoluta y parámetros de consulta que ya traía.

        Los enlaces de SAP (``__next``) se reanclan a la URL base configurada, porque Gateway
        informa a menudo su nombre interno de host.
        """
        parts = urlsplit(path_or_url)
        return (
            f"{self.spec.base_url}{parts.path or '/'}",
            parse_qsl(parts.query, keep_blank_values=True),
        )

    def get(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        json_format: bool = True,
    ) -> SapResponse:
        url, existing = self.url_for(path_or_url)
        query: dict[str, str] = {
            "sap-client": self.spec.sap_client,
            "sap-language": self.spec.language,
        }
        if json_format:
            query["$format"] = "json"
        query.update(existing)  # p. ej. $skiptoken o !deltatoken de un __next
        query.update(params or {})
        request_headers = {"Accept": "application/json" if json_format else "application/xml"}
        request_headers.update(headers or {})
        return self._send("GET", url, query, request_headers)

    def get_json(
        self, path_or_url: str, *, params: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        return self.get(path_or_url, params=params).json()

    def iter_pages(
        self, path: str, *, params: Mapping[str, str] | None = None
    ) -> Iterator[list[dict[str, Any]]]:
        """Recorre una colección V2 siguiendo ``__next`` (con un tope de seguridad)."""
        url: str | None = path
        current_params = params
        for _ in range(MAX_PAGES):
            if url is None:
                return
            rows, url = odata_v2_results(self.get_json(url, params=current_params))
            current_params = None  # __next ya trae la consulta completa
            yield rows
        msg = f"Se superó el máximo de {MAX_PAGES} páginas al leer {path}."
        raise SapResponseError(msg)

    def fetch_csrf_token(self, path: str) -> str:
        """Preparado para escrituras futuras (``x-csrf-token: fetch``); la lectura no lo usa."""
        response = self.get(path, headers={"x-csrf-token": "fetch"})
        token = response.headers.get("x-csrf-token")
        if not token:
            msg = "SAP no devolvió un token CSRF."
            raise SapResponseError(msg)
        return str(token)

    # -------------------------------------------------------------- Gateway
    def catalog_root(self) -> SapResponse:
        return self.get(f"{CATALOG_PATH}/")

    def catalog_services(self) -> list[CatalogService]:
        """Catálogo completo con entity sets (``$expand``) si Gateway lo admite."""
        try:
            entries = [
                row
                for page in self.iter_pages(
                    f"{CATALOG_PATH}/ServiceCollection", params={"$expand": "EntitySets"}
                )
                for row in page
            ]
        except (SapRequestError, SapServerError) as exc:
            log.info("sap_catalog_expand_unsupported", system=self.spec.label, error=exc.error_code)
            entries = [
                row for page in self.iter_pages(f"{CATALOG_PATH}/ServiceCollection") for row in page
            ]
        return [classify(parse_catalog_entry(e)) for e in entries if _technical_name(e)]

    def service_metadata(self, service_path: str) -> bytes:
        return self.get(f"{service_path.rstrip('/')}/$metadata", json_format=False).content

    # -------------------------------------------------------------- internos
    def _send(
        self, method: str, url: str, params: dict[str, str], headers: dict[str, str]
    ) -> SapResponse:
        attempts = max(1, self.retry.max_attempts)
        with self._limiter.slot(
            self.spec.label, self.spec.max_concurrent_requests, float(self.spec.timeout_seconds)
        ):
            for attempt in range(1, attempts + 1):
                started = time.perf_counter()
                try:
                    response = self._http.request(method, url, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    error = error_for_transport(
                        exc, host=self._host, timeout=float(self.spec.timeout_seconds)
                    )
                    self._trace(
                        method=method,
                        url=url,
                        params=params,
                        status=None,
                        started=started,
                        size=0,
                        attempt=attempt,
                        error=error.error_code,
                    )
                    retryable = isinstance(error, SapNetworkError | SapTimeoutError)
                    if retryable and attempt < attempts:
                        self._sleep(self.retry.delay(attempt))
                        continue
                    raise error from exc
                content = response.content
                self._trace(
                    method=method,
                    url=url,
                    params=params,
                    status=response.status_code,
                    started=started,
                    size=len(content),
                    attempt=attempt,
                )
                if response.status_code in RETRY_STATUSES and attempt < attempts:
                    self._sleep(self.retry.delay(attempt, response.headers.get("retry-after")))
                    continue
                if response.status_code >= 400:
                    raise error_for_status(response.status_code, content)
                return SapResponse(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=content,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    url=str(response.url),
                )
        msg = "No se pudo completar la petición a SAP."  # pragma: no cover - bucle exhaustivo
        raise SapNetworkError(msg)

    def _trace(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[str, str],
        status: int | None,
        started: float,
        size: int,
        attempt: int,
        error: str | None = None,
    ) -> None:
        log.info(
            "sap_request",
            system=self.spec.label,
            method=method,
            url=mask_value(url),
            params=mask_value(dict(params)),
            status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            bytes=size,
            attempt=attempt,
            error=error,
        )


# ------------------------------------------------------------------ catálogo
def _technical_name(entry: Mapping[str, Any]) -> str:
    return str(entry.get("TechnicalServiceName") or entry.get("Title") or "").strip()


def parse_catalog_entry(entry: Mapping[str, Any]) -> CatalogService:
    name = _technical_name(entry)
    try:
        version = int(entry.get("TechnicalServiceVersion") or 1)
    except TypeError, ValueError:
        version = 1
    service_url = entry.get("ServiceUrl")
    path = urlsplit(service_url).path.rstrip("/") if isinstance(service_url, str) else ""
    sets_raw = entry.get("EntitySets")
    entity_sets: tuple[str, ...] | None = None
    if isinstance(sets_raw, dict) and isinstance(sets_raw.get("results"), list):
        entity_sets = tuple(
            str(s.get("ID") or s.get("Name"))
            for s in sets_raw["results"]
            if isinstance(s, dict) and (s.get("ID") or s.get("Name"))
        )
    return CatalogService(
        technical_name=name,
        version=version,
        service_id=str(entry.get("ID") or f"{name}_{version:04d}"),
        title=str(entry["Title"]) if entry.get("Title") else None,
        description=str(entry["Description"]) if entry.get("Description") else None,
        service_path=path or default_service_path(name, version),
        entity_sets=entity_sets,
    )
