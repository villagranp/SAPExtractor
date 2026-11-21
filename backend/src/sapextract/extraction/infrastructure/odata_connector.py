"""Conector de origen SAP por OData (ODP y CDS vía OData), con el contrato de ADR 0006 §7.

* Paginación de servidor: ``Prefer: odata.maxpagesize`` y ``__next`` / ``@odata.nextLink``.
  Nunca ``$skip`` en un delta. Sin tope de páginas (el ``MAX_PAGES`` del cliente es para el
  catálogo), con límite de bytes por página como protección.
* Las peticiones van **sin reintento HTTP** (``NO_RETRY``): un *timeout* no garantiza que SAP no
  procesara la petición de init o delta. El reintento se decide a nivel de run (plan 06 §3.2).
* La cancelación se consulta entre páginas: si se pide, la lectura se detiene sin token.

Pendiente del spike: el código real de un token rechazado (H7) y la paginación de servicios
CDS vía OData que no devuelvan ``__next`` (paginación por clave, plan 06 §6).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import pyarrow as pa

from sapextract.core.clock import utcnow
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import Filters, PageMetrics, ReadContext, Window
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.extraction.domain.convert import PageConverter
from sapextract.sap_connections.domain.errors import (
    SapAuthenticationError,
    SapAuthorizationError,
    SapBusyError,
    SapError,
    SapNetworkError,
    SapNotFoundError,
    SapRateLimitError,
    SapRequestError,
    SapServerError,
    SapTimeoutError,
)
from sapextract.sap_connections.infrastructure.odata_client import SapODataClient
from sapextract.sources.domain.filters import literal
from sapextract.sources.domain.model import WatermarkKind

DEFAULT_MAX_PAGE_BYTES = 256 * 1024 * 1024
FILTER_KEY = "$filter"
_TOKEN = re.compile(r"[!$]deltatoken=([^&]+)")

type ClientFactory = Callable[[], SapODataClient]


@dataclass(frozen=True, kw_only=True)
class WatermarkSpec:
    field: str
    kind: WatermarkKind
    timezone: str


@dataclass(frozen=True, kw_only=True)
class ODataSourceRef:
    """Lo que el conector necesita del origen (plan 05)."""

    service_path: str
    entity_set: str
    odata_version: str
    schema: EntitySchema
    supports_delta: bool
    base_filter: str = ""  # filtros del origen y del pipeline ya en $filter (para las ventanas)
    watermark: WatermarkSpec | None = None

    @property
    def path(self) -> str:
        return f"{self.service_path.rstrip('/')}/{self.entity_set}"


def classify(exc: SapError, *, delta: bool = False) -> DataflowError:
    """``SapError`` → ``DataflowError`` con su clase (plan 06 §8)."""
    message = str(exc.detail if hasattr(exc, "detail") else exc)
    if delta and isinstance(exc, SapNotFoundError | SapRequestError):
        # H7 (pendiente): SAP no documenta el error de un token borrado o caducado. El mock
        # responde 404 (ODQ) o 400; hasta el spike, cualquiera de los dos en un delta se
        # interpreta como token rechazado.
        detail = f" SAP: {exc.sap_message}" if exc.sap_message else ""
        return DataflowError(
            "SAP rechazó el token de delta (suscripción borrada en ODQMON o caducada)." + detail,
            kind=ErrorKind.DELTA,
            code="SX-SAP-DELTA-INVALID",
        )
    transient = (
        SapNetworkError | SapTimeoutError | SapServerError | SapRateLimitError | SapBusyError
    )
    if isinstance(exc, transient):
        return DataflowError(message, kind=ErrorKind.TRANSIENT, code=exc.error_code)
    if isinstance(exc, SapAuthenticationError | SapAuthorizationError | SapNotFoundError):
        return DataflowError(message, kind=ErrorKind.CONFIGURATION, code=exc.error_code)
    return DataflowError(message, kind=ErrorKind.CONFIGURATION, code=exc.error_code)


def token_of(link: str | None) -> str | None:
    """Token de un enlace ``__delta`` (``…?!deltatoken='D…'``) o ``@odata.deltaLink``."""
    if not link:
        return None
    query = urlsplit(link).query
    values = parse_qs(query).get("!deltatoken") or parse_qs(query).get("$deltatoken")
    raw = values[0] if values else None
    if raw is None:
        match = _TOKEN.search(unquote(link))
        raw = match[1] if match else None
    return raw.strip().strip("'") if raw else None


def _page(body: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Filas, enlace siguiente y enlace de delta (V2 ``d``, V4 ``value``)."""
    d = body.get("d")
    if isinstance(d, dict):
        rows, nxt, delta = d.get("results", []), d.get("__next"), d.get("__delta")
    elif isinstance(d, list):
        rows, nxt, delta = d, None, None
    else:
        rows = body.get("value", [])
        nxt, delta = body.get("@odata.nextLink"), body.get("@odata.deltaLink")
    clean = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return (
        clean,
        nxt if isinstance(nxt, str) else None,
        delta if isinstance(delta, str) else None,
    )


class _PagedRead:
    """``ReadResult``: recorre las páginas de una lectura; el token sale al agotarlas."""

    def __init__(
        self,
        connector: ODataSourceConnector,
        ctx: ReadContext,
        *,
        params: dict[str, str],
        tracking: bool,
        change_mode: str | None,
        expects_token: bool,
        is_delta: bool = False,
        max_rows: int | None = None,
    ) -> None:
        self._connector = connector
        self._ctx = ctx
        self._params = params
        self._tracking = tracking
        self._converter = PageConverter(connector.ref.schema, change_mode=change_mode)
        self._expects_token = expects_token
        self._is_delta = is_delta
        self._max_rows = max_rows  # solo en lecturas de muestra
        self._token: str | None = None
        self._finished = False

    def batches(self) -> Iterator[pa.RecordBatch]:
        ref, ctx = self._connector.ref, self._ctx
        prefer = f"odata.maxpagesize={ctx.page_size}"
        first_prefer = f"odata.track-changes, {prefer}" if self._tracking else prefer
        url: str | None = ref.path
        params: dict[str, str] | None = self._params
        headers = {"Prefer": first_prefer}
        read = 0
        with self._connector.client_factory() as client:
            while url is not None:
                if ctx.is_cancelled():
                    return  # sin token: el motor no confirma nada
                started = time.perf_counter()
                try:
                    response = client.get(url, params=params, headers=headers)
                except SapError as exc:
                    raise classify(exc, delta=self._is_delta) from exc
                if len(response.content) > self._connector.max_page_bytes:
                    msg = (
                        f"Una página de SAP ocupa {len(response.content)} bytes, más que el límite "
                        f"de {self._connector.max_page_bytes}: reduzca el tamaño de página."
                    )
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-SAP-PAGE-TOO-LARGE"
                    )
                try:
                    rows, url, delta = _page(response.json())
                except SapError as exc:
                    raise classify(exc) from exc
                ctx.on_page(
                    PageMetrics(
                        rows=len(rows),
                        bytes=len(response.content),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                if self._max_rows is not None:
                    rows = rows[: self._max_rows - read]
                read += len(rows)
                if rows:
                    yield self._converter.convert(rows, extracted_at=utcnow())
                if self._max_rows is not None and read >= self._max_rows:
                    break
                if delta:
                    self._token = token_of(delta)
                params = None  # __next ya trae la consulta completa
                headers = {"Prefer": prefer}
        if self._expects_token and not self._token:
            msg = (
                "SAP terminó la lectura sin devolver token de delta (__delta). Compruebe que el "
                "servicio es ODP con change tracking."
            )
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-SAP-NO-DELTA-TOKEN")
        self._finished = True

    def next_token(self) -> str | None:
        if not self._finished:
            msg = "El token solo está disponible al agotar los lotes"
            raise RuntimeError(msg)
        return self._token


class ODataSourceConnector:
    """``SourceConnector`` para orígenes ODP (extractor y CDS) y CDS vía OData."""

    def __init__(
        self,
        ref: ODataSourceRef,
        client_factory: ClientFactory,
        *,
        max_page_bytes: int = DEFAULT_MAX_PAGE_BYTES,
    ) -> None:
        self.ref = ref
        self.client_factory = client_factory
        self.max_page_bytes = max_page_bytes

    def schema(self) -> EntitySchema:
        return self.ref.schema

    def read_full(self, filters: Filters, ctx: ReadContext) -> _PagedRead:
        return _PagedRead(
            self,
            ctx,
            params=_filter_params(filters),
            tracking=False,
            change_mode=None,
            expects_token=False,
        )

    def read_delta_init(self, filters: Filters, ctx: ReadContext) -> _PagedRead:
        if not self.ref.supports_delta:
            msg = "El servicio no declara delta (change tracking)."
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-SRC-DELTA-UNSUPPORTED")
        return _PagedRead(
            self,
            ctx,
            params=_filter_params(filters),
            tracking=True,
            change_mode="init",
            expects_token=True,
        )

    def read_delta(self, token: str, ctx: ReadContext) -> _PagedRead:
        return _PagedRead(
            self,
            ctx,
            params={"!deltatoken": f"'{token}'"},
            tracking=False,
            change_mode="delta",
            expects_token=True,
            is_delta=True,
        )

    def read_window(self, window: Window, ctx: ReadContext) -> _PagedRead:
        spec = self.ref.watermark
        if spec is None:
            msg = "El origen no tiene marca de agua configurada."
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-SRC-WATERMARK-REQUIRED")
        column = self.ref.schema.column(spec.field)
        version = self.ref.odata_version
        start = literal(column, _bound(spec.kind, window.start), odata_version=version)
        end = literal(column, _bound(spec.kind, window.end), odata_version=version)
        condition = f"{column.name} ge {start} and {column.name} lt {end}"
        base = self.ref.base_filter
        return _PagedRead(
            self,
            ctx,
            # Los filtros base solo se unen con «and»: no hacen falta paréntesis.
            params={FILTER_KEY: f"{base} and {condition}" if base else condition},
            tracking=False,
            change_mode=None,
            expects_token=False,
        )

    def read_sample(self, filters: Filters, ctx: ReadContext, *, rows: int) -> _PagedRead:
        """Primeras ``rows`` filas con ``$top``, sin ``Prefer: odata.track-changes``.

        No abre suscripción en la ODQ. Gateway puede repartirlas en varias páginas; la lectura
        se corta al llegar a ``rows`` aunque SAP ofrezca más.
        """
        return _PagedRead(
            self,
            ctx,
            params={**_filter_params(filters), "$top": str(rows)},
            tracking=False,
            change_mode=None,
            expects_token=False,
            max_rows=rows,
        )

    def server_now(self) -> datetime:
        """Hora de SAP según la cabecera ``Date`` (no la del worker, plan 06 §5)."""
        with self.client_factory() as client:
            try:
                response = client.get(f"{self.ref.service_path.rstrip('/')}/")
            except SapError as exc:
                raise classify(exc) from exc
        header = response.headers.get("date")
        if header:
            try:
                return parsedate_to_datetime(header).astimezone(UTC)
            except TypeError, ValueError:
                pass
        return utcnow()


def _filter_params(filters: Filters) -> dict[str, str]:
    value = filters.get(FILTER_KEY)
    return {FILTER_KEY: str(value)} if value else {}


def _bound(kind: WatermarkKind, moment: datetime) -> str:
    """Literal de un límite de ventana según el tipo de marca de agua (hora local del campo)."""
    if kind is WatermarkKind.DATE:
        return moment.date().isoformat()
    if kind is WatermarkKind.DECIMAL_TIMESTAMP:
        return moment.strftime("%Y%m%d%H%M%S")
    return moment.replace(microsecond=0).isoformat()


def local_moment(instant: datetime, timezone: str) -> datetime:
    """Instante UTC → hora local sin zona del campo SAP (las horas ABAP no llevan zona)."""
    if timezone == "UTC":
        return instant.astimezone(UTC).replace(tzinfo=None)
    return instant.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
