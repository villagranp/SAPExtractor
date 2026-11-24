# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Spike SAP: valida el comportamiento real de ODP/CDS vía OData (hipótesis H1 a H18, ADR 0002).

Cada prueba cubre una o varias hipótesis y se elige con --hypotheses:

  previa            H4, H16   vista previa con $top: ¿crea suscripción?, ¿cuánto tarda?
  formatos          H14, H15  importes en monedas sin decimales y fechas (lista para SAP GUI)
  núcleo            H1, H2, H3, H5, H11, H17
                              init → cambios → delta → más cambios → repetición con el token
                              anterior, comparada por huella de registro (claves,
                              ODQ_CHANGEMODE, ODQ_ENTITYCNTR y contenido), no por recuento
  dos filtros       H6        dos inits con filtros distintos (--filter y --filter-b)
  mismo filtro      H18       dos inits iguales: ¿dos cadenas de delta independientes?
  cierre            H12       SubscribedTo… / TerminateDeltasFor… (cerrar exige --allow-terminate)
  borrado ODQMON    H7        el token deja de valer al borrar la suscripción en ODQMON
  corte de init     H10       repetir una página de la init y relanzarla
  init sin datos    H9        prueba de init con $top=0
  init grande       H8        tiempo hasta la primera página y Prefer: respond-async
  retención         H13       repetir deltas de una ejecución anterior (--previous-results)

Por defecto: H1 H2 H3 H4 H5 H11 H14 H15 H16 H17 (no borra nada en SAP). Primera sesión con el
equipo SAP: --hypotheses H6,H12,H18.

Guarda en --output: results.json, report.md (para pegar en docs/adr/0002), listas para comparar
con SAP GUI y muestras de respuestas (pocas filas, para alimentar el mock). La contraseña se lee de
SPIKE_SAP_PASSWORD o se pide por teclado; nunca se escribe en disco ni en pantalla.

Ejemplos:
  uv run tools/spike-sap/spike_odp.py --base-url https://gateway:44300 --client 100 \\
      --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user EXTRACT_USER \\
      --hypotheses H6,H12,H18 --filter "RBUKRS eq '1000'" --filter-b "RBUKRS eq '2000'"

  # Contra el mock local (docker compose):
  SPIKE_SAP_PASSWORD=sxmock uv run tools/spike-sap/spike_odp.py --base-url http://localhost:8081 \\
      --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user sxmock \\
      --non-interactive --hypotheses all \\
      --mock-admin-url http://localhost:8081/_mock/services/ZFI_WS_GL_ITEMS_SRV
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from xml.etree import ElementTree as ET

import httpx

EDM = "http://schemas.microsoft.com/ado/2008/09/edm"
SAP = "http://www.sap.com/Protocols/SAPData"
METADATA = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
ODQ_FIELDS = ("ODQ_CHANGEMODE", "ODQ_ENTITYCNTR")
CUT_PAGE_SIZE = 100  # H10: páginas pequeñas para poder cortar la init en la tercera
SLOW_PREVIEW_MS = 10_000

HYPOTHESES: dict[str, str] = {
    "H1": "La init con odata.track-changes devuelve __delta en la última página",
    "H2": "Re-solicitar con el token anterior repite el delta",
    "H3": "Los registros delta traen ODQ_CHANGEMODE y ODQ_ENTITYCNTR",
    "H4": "La lectura full con $top no crea suscripción",
    "H5": "Rendimiento aceptable (filas/s) con odata.maxpagesize",
    "H6": "Dos suscripciones con filtros distintos conviven en el mismo servicio y usuario",
    "H7": "Al borrar la suscripción en ODQMON el token deja de valer",
    "H8": "La primera página de una init grande llega dentro de los límites de Gateway",
    "H9": "Existe una init sin datos vía OData",
    "H10": "Repetir una página tras un corte devuelve lo mismo; relanzar la init no duplica",
    "H11": "La repetición devuelve el mismo conjunto o todo lo ocurrido desde ese token",
    "H12": "SubscribedTo… y TerminateDeltasFor… existen y cierran la suscripción",
    "H13": "La ODQ conserva repetible un delta ya confirmado",
    "H14": "Importes en monedas sin decimales: formato interno o externo",
    "H15": "Edm.DateTime: hora local del sistema o UTC",
    "H16": "La vista previa con $top no lanza la extracción completa",
    "H17": "Los registros con ODQ_CHANGEMODE = D solo traen las claves",
    "H18": "Dos inits iguales (entity set, filtros, usuario) dan cadenas de delta independientes",
}
DEFAULT_HYPOTHESES = ("H1", "H2", "H3", "H4", "H5", "H11", "H14", "H15", "H16", "H17")

MANUAL_CHECKS: dict[str, tuple[str, ...]] = {
    "H4": ("ODQMON: tras la vista previa no aparece una suscripción nueva del usuario técnico.",),
    "H5": ("Repetir con --page-size 1000, 5000 y 20000 y anotar filas/s de cada uno.",),
    "H6": (
        "ODQMON: hay una suscripción (o petición) por cada filtro y avanzan por separado.",
        "Los cambios generados cumplían los dos filtros.",
    ),
    "H7": ("Anotar el código y el mensaje de error de SAP: el motor los usará para clasificar.",),
    "H8": (
        "Pedir a Basis el límite de tiempo de los procesos de diálogo (rdisp/max_wprun_time o su "
        "equivalente en el kernel) y los timeouts de ICM y Gateway (PROCTIMEOUT, TIMEOUT).",
        "Repetir sin filtro sobre el extractor más grande disponible.",
    ),
    "H9": (
        "ODQMON: la petición creada figura como inicialización sin transferencia de datos.",
        "Preguntar al equipo SAP si hay otra vía OData para una init sin datos.",
    ),
    "H10": (
        "ODQMON: ¿la init cortada quedó abierta? ¿La init relanzada creó otra suscripción o "
        "reutilizó la primera?",
    ),
    "H12": (
        "ODQMON: tras TerminateDeltasFor… no queda ninguna suscripción del usuario técnico para el "
        "ODP (si queda alguna, anotar cuál).",
    ),
    "H13": (
        "Basis: periodo de retención para recuperación configurado en la reorganización de la ODQ "
        "y qué ocurre con una suscripción que deja de leer.",
    ),
    "H14": ("Comparar los importes de h14_importes.md con SAP GUI (FB03, VA03 o SE16N).",),
    "H15": (
        "Comparar las fechas y horas de h15_fechas.md con SAP GUI.",
        "Anotar la zona horaria del sistema (STZAC).",
    ),
    "H16": ("ODQMON y SM50/SM66 durante la vista previa: no se lanza una extracción completa.",),
    "H18": ("ODQMON: ¿dos suscripciones o una? Anotar el suscriptor y la petición de cada init.",),
}


class Outcome(StrEnum):
    CONFIRMED = "se cumple"
    REFUTED = "no se cumple"
    REVIEW = "revisar a mano"
    SKIPPED = "no ejecutada"
    ERROR = "error"


@dataclass
class Finding:
    outcome: Outcome
    summary: str
    evidence: dict[str, Any]
    manual: list[str]


class SapCallError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}".replace("  ", " "))
        self.status, self.code, self.message = status, code, message

    def as_evidence(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "message": self.message}


# ---------------------------------------------------------------------------------- metadatos


@dataclass
class Prop:
    name: str
    edm_type: str
    unit: str | None = None
    semantics: str | None = None
    display_format: str | None = None


@dataclass
class FunctionImport:
    name: str
    http_method: str
    return_type: str | None
    parameters: list[str]


@dataclass
class Metadata:
    entity_sets: list[dict[str, Any]]
    props: list[Prop]
    keys: list[str]
    functions: list[FunctionImport]

    def prop(self, name: str) -> Prop | None:
        return next((p for p in self.props if p.name == name), None)

    @property
    def data_props(self) -> list[Prop]:
        """Campos de negocio no clave (sin los campos técnicos de ODQ)."""
        return [p for p in self.props if p.name not in self.keys and p.name not in ODQ_FIELDS]


def parse_metadata(xml: bytes, entity_set: str) -> Metadata:
    root = ET.fromstring(xml)  # noqa: S314 - $metadata del Gateway propio, no de terceros
    sets = [
        {
            "name": es.get("Name"),
            "entity_type": es.get("EntityType"),
            "change_tracking": es.get(f"{{{SAP}}}change-tracking"),
        }
        for es in root.iter(f"{{{EDM}}}EntitySet")
    ]
    target = next((s for s in sets if s["name"] == entity_set), None)
    type_name = str((target or {}).get("entity_type") or "").rsplit(".", 1)[-1]
    entity_type = next(
        (t for t in root.iter(f"{{{EDM}}}EntityType") if t.get("Name") == type_name), None
    )
    props: list[Prop] = []
    keys: list[str] = []
    if entity_type is not None:
        keys = [str(k.get("Name")) for k in entity_type.iter(f"{{{EDM}}}PropertyRef")]
        props = [
            Prop(
                name=str(p.get("Name")),
                edm_type=str(p.get("Type")),
                unit=p.get(f"{{{SAP}}}unit"),
                semantics=p.get(f"{{{SAP}}}semantics"),
                display_format=p.get(f"{{{SAP}}}display-format"),
            )
            for p in entity_type.iter(f"{{{EDM}}}Property")
        ]
    functions = [
        FunctionImport(
            name=str(f.get("Name")),
            http_method=f.get(f"{{{METADATA}}}HttpMethod") or "GET",
            return_type=f.get("ReturnType"),
            parameters=[str(p.get("Name")) for p in f.iter(f"{{{EDM}}}Parameter")],
        )
        for f in root.iter(f"{{{EDM}}}FunctionImport")
    ]
    return Metadata(sets, props, keys, functions)


# ---------------------------------------------------------------------------------- lecturas


@dataclass
class ReadResult:
    rows: int = 0
    pages: int = 0
    seconds: float = 0.0
    first_page_ms: float | None = None
    delta_link: str | None = None
    next_link: str | None = None  # solo si la lectura se cortó con max_pages
    change_modes: Counter[str] = field(default_factory=Counter)
    entity_counters: Counter[str] = field(default_factory=Counter)
    missing_odq: int = 0
    deletes: int = 0
    deletes_with_values: Counter[str] = field(default_factory=Counter)
    fingerprints: Counter[str] = field(default_factory=Counter)
    sample_keys: dict[str, str] = field(default_factory=dict)

    @property
    def rows_per_second(self) -> float:
        return round(self.rows / self.seconds, 1) if self.seconds else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "pages": self.pages,
            "seconds": self.seconds,
            "rows_per_second": self.rows_per_second,
            "first_page_ms": self.first_page_ms,
            "change_modes": dict(self.change_modes),
            "entity_counters": dict(self.entity_counters),
            "deletes": self.deletes,
            "token": token_of(self.delta_link),
        }


def compare(first: Counter[str], second: Counter[str]) -> dict[str, int]:
    """Compara dos lecturas como multiconjuntos de huellas de registro."""
    return {
        "only_first": sum((first - second).values()),
        "only_second": sum((second - first).values()),
        "common": sum((first & second).values()),
    }


def fingerprint(row: dict[str, Any]) -> str:
    payload = {k: v for k, v in row.items() if k != "__metadata"}
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def is_blank(value: Any) -> bool:
    if value is None or value is False or value == "":
        return True
    if isinstance(value, int | float):
        return value == 0
    return isinstance(value, str) and re.fullmatch(r"-?0*\.?0*", value.strip()) is not None


def decode_edm_date(value: Any) -> str:
    """``/Date(ms)/`` → ISO sin zona, tal cual llega; el desfase, si viene, se indica aparte."""
    match = re.fullmatch(r"/Date\((-?\d+)([+-]\d{4})?\)/", str(value))
    if not match:
        return str(value)
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(match[1]))
    text = moment.replace(tzinfo=None).isoformat()
    return f"{text} (desfase {match[2]})" if match[2] else text


def token_of(link: str | None) -> str | None:
    if not link:
        return None
    values = parse_qs(urlsplit(link).query).get("!deltatoken")
    return values[0].strip("'") if values else None


def first_flag(value: Any) -> bool | None:
    """Indicador booleano de la respuesta de una función (SubscribedFlag, ResultFlag…).

    SAP la devuelve plana (``{"d": {"SubscribedFlag": false}}``) o, cuando el tipo de retorno es
    un tipo complejo, anidada bajo el nombre de la función (``{"d": {"SubscribedTo…":
    {"SubscribedFlag": false}}}``, visto en el S/4HANA de AG).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false", "x", ""}:
        return value.lower() in {"true", "x"}
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "__metadata":
                continue
            flag = first_flag(item)
            if flag is not None:
                return flag
    return None


def sap_error_of(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json().get("error", {})
        message = error.get("message", {})
        text = message.get("value", "") if isinstance(message, dict) else str(message)
        return str(error.get("code", "")), str(text)[:300]
    except ValueError, AttributeError:
        pass
    match = re.search(r"<code>(.*?)</code>.*?<message[^>]*>(.*?)</message>", response.text, re.S)
    if match:
        return match[1], match[2][:300]
    return "", response.text[:300].strip()


def ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def log(msg: str) -> None:
    sys.stdout.write(f"[{datetime.now(UTC):%H:%M:%S}] {msg}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------------- spike


class Spike:
    def __init__(
        self,
        args: argparse.Namespace,
        password: str,
        *,
        http: httpx.Client | None = None,
        admin: httpx.Client | None = None,
    ) -> None:
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.service_url = f"{self.base_url}/sap/opu/odata/sap/{args.service}"
        self.entity_url = f"{self.service_url}/{args.entity_set}"
        verify: bool | str = args.ca_bundle or not args.insecure
        # Sin params a nivel de cliente: httpx sustituye la query de la URL en vez de mezclarla
        # y borraría $skiptoken y !deltatoken de los enlaces de SAP (ver with_query).
        self.sap_params: dict[str, str] = (
            {"sap-client": args.client, "sap-language": args.language} if args.client else {}
        )
        self.http = http or httpx.Client(
            auth=(args.user, password),
            timeout=httpx.Timeout(args.timeout, connect=15),
            verify=verify,
        )
        self.admin = admin or (httpx.Client(timeout=30) if args.mock_admin_url else None)
        self.masked = set(args.mask_fields.split(",")) if args.mask_fields else set()
        self.out = Path(args.output) / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.out.mkdir(parents=True, exist_ok=True)
        self.md = Metadata([], [], [], [])
        self.findings: dict[str, Finding] = {}
        self.opened: list[dict[str, Any]] = []
        self.links: dict[str, dict[str, str]] = {}
        self.chains: dict[str, str] = {}
        self.preview: list[dict[str, Any]] = []
        self.core_first_page_ms: float | None = None
        self.csrf_token: str | None = None
        self.results: dict[str, Any] = {
            "service": args.service,
            "entity_set": args.entity_set,
            "page_size": args.page_size,
            "filter": args.filter,
            "filter_b": args.filter_b,
            "requested": list(args.hypotheses),
            "started_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------ utilidades
    def save(self, name: str, content: str | dict[str, Any]) -> None:
        path = self.out / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(content, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )

    def mask(self, name: str, value: Any) -> Any:
        return "***" if name in self.masked else value

    def keys_of(self, row: dict[str, Any]) -> str:
        return "/".join(str(self.mask(k, row.get(k))) for k in self.md.keys)

    def sample(self, body: dict[str, Any]) -> dict[str, Any]:
        """Recorta la respuesta a N registros y enmascara los campos indicados."""
        d = dict(body.get("d", {}))
        rows = d.get("results", [])[: self.args.sample_records]
        d["results"] = [{k: self.mask(k, v) for k, v in r.items()} for r in rows]
        return {"d": d}

    def found(
        self, hyp: str, outcome: Outcome, summary: str, evidence: dict[str, Any] | None = None
    ) -> None:
        self.findings[hyp] = Finding(
            outcome, summary, evidence or {}, list(MANUAL_CHECKS.get(hyp, ()))
        )
        log(f"   {hyp}: {outcome} · {summary}")

    def call(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        expect_ok: bool = True,
    ) -> httpx.Response:
        merged = {"Accept": "application/json", **(headers or {})}
        response = self.http.request(method, self.with_query(url, params), headers=merged)
        if expect_ok and response.status_code >= 400:
            code, message = sap_error_of(response)
            log(f"   ✖ HTTP {response.status_code} {code}: {message}")
            raise SapCallError(response.status_code, code, message)
        return response

    def with_query(self, url: str, params: dict[str, str] | None) -> str:
        """Añade parámetros sin tocar la query existente (la de SAP se reenvía tal cual)."""
        query = urlsplit(url).query
        present = set(parse_qs(query, keep_blank_values=True))
        extra = {k: v for k, v in {**self.sap_params, **(params or {})}.items() if k not in present}
        if not extra:
            return url
        encoded = urlencode(extra, quote_via=quote, safe="$'(),:")
        return f"{url}{'&' if query else '?'}{encoded}"

    def rebase(self, link: str) -> str:
        """SAP devuelve los enlaces con su host interno: se reescriben al --base-url usado."""
        parts = urlsplit(link)
        return f"{self.base_url}{parts.path}" + (f"?{parts.query}" if parts.query else "")

    def remember(self, label: str, link: str) -> None:
        self.links[label] = {
            "link": link,
            "token": token_of(link) or "",
            "obtained_at": datetime.now(UTC).isoformat(),
        }

    def note_open(self, label: str, flt: str | None, link: str | None, *, complete: bool) -> None:
        self.opened.append(
            {"test": label, "filter": flt, "token": token_of(link), "complete": complete}
        )

    # ------------------------------------------------------------------ lecturas OData
    def read_all(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        tracking: bool = False,
        label: str | None = None,
        max_pages: int = 0,
        page_size: int | None = None,
        analyze: bool = False,
    ) -> ReadResult:
        prefer = f"odata.maxpagesize={page_size or self.args.page_size}"
        headers = {"Prefer": f"odata.track-changes, {prefer}" if tracking else prefer}
        res = ReadResult()
        started = time.perf_counter()
        request_url: str = url
        # with_query no repite $format si el enlace de SAP ya lo trae.
        request_params: dict[str, str] | None = {"$format": "json", **(params or {})}
        while True:
            page_started = time.perf_counter()
            response = self.call("GET", request_url, params=request_params, headers=headers)
            if res.first_page_ms is None:
                res.first_page_ms = ms_since(page_started)
            body = response.json()
            d = body.get("d", {})
            rows = d.get("results", [])
            if res.pages == 0 and label:
                self.save(f"{label}.sample.json", self.sample(body))
            res.pages += 1
            res.rows += len(rows)
            res.change_modes.update(str(r.get("ODQ_CHANGEMODE", "?")) for r in rows)
            if analyze:
                self.analyze(res, rows)
            res.delta_link = d.get("__delta") or res.delta_link
            next_link = d.get("__next")
            if not next_link:
                break
            request_url, request_params = self.rebase(next_link), None
            if max_pages and res.pages >= max_pages:
                res.next_link = request_url
                break
            if res.pages % 20 == 0:
                log(f"   … {res.pages} páginas, {res.rows} filas")
        res.seconds = round(time.perf_counter() - started, 2)
        return res

    def analyze(self, res: ReadResult, rows: list[dict[str, Any]]) -> None:
        data_fields = [p.name for p in self.md.data_props]
        for row in rows:
            fp = fingerprint(row)
            res.fingerprints[fp] += 1
            res.sample_keys.setdefault(fp, self.keys_of(row))
            res.entity_counters[str(row.get("ODQ_ENTITYCNTR", "?"))] += 1
            if any(f not in row for f in ODQ_FIELDS):
                res.missing_odq += 1
            if row.get("ODQ_CHANGEMODE") == "D":
                res.deletes += 1
                for name in data_fields or [k for k in row if k not in self.md.keys]:
                    if (
                        name not in ODQ_FIELDS
                        and name != "__metadata"
                        and not is_blank(row.get(name))
                    ):
                        res.deletes_with_values[name] += 1

    def init(self, label: str, flt: str | None) -> ReadResult:
        log(f"   init de delta «{label}»" + (f" con $filter={flt}" if flt else ""))
        res = self.read_all(
            self.entity_url, params={"$filter": flt} if flt else None, tracking=True, label=label
        )
        log(
            f"   {res.rows} filas, {res.pages} páginas, {res.seconds}s → {token_of(res.delta_link)}"
        )
        if res.delta_link:
            self.remember(label, res.delta_link)
            self.chains[label] = res.delta_link
        self.note_open(label, flt, res.delta_link, complete=res.delta_link is not None)
        return res

    def delta(self, link: str, label: str) -> ReadResult:
        res = self.read_all(self.rebase(link), label=label, analyze=True)
        log(f"   delta «{label}»: {res.rows} registros {dict(res.change_modes)}")
        if res.delta_link:
            self.remember(label, res.delta_link)
            for chain, latest in self.chains.items():
                if latest == link:
                    self.chains[chain] = res.delta_link
        return res

    def delta_links_count(self) -> int | None:
        r = self.call(
            "GET",
            f"{self.service_url}/DeltaLinksOf{self.args.entity_set}",
            params={"$format": "json"},
            expect_ok=False,
        )
        if r.status_code >= 400:
            return None
        return len(r.json().get("d", {}).get("results", []))

    # ------------------------------------------------------------------ funciones de suscripción
    def function(self, prefix: str) -> FunctionImport | None:
        name = f"{prefix}{self.args.entity_set}"
        return next((f for f in self.md.functions if f.name == name), None)

    def call_function(self, fn: FunctionImport) -> httpx.Response:
        url = f"{self.service_url}/{fn.name}"
        method = fn.http_method.upper()
        headers: dict[str, str] = {}
        if method != "GET":
            if self.csrf_token is None:
                r = self.call(
                    "GET",
                    f"{self.service_url}/",
                    headers={"X-CSRF-Token": "Fetch"},
                    expect_ok=False,
                )
                self.csrf_token = r.headers.get("x-csrf-token", "")
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token
        return self.call(method, url, params={"$format": "json"}, headers=headers, expect_ok=False)

    def subscribed_to(self) -> bool | None:
        fn = self.function("SubscribedTo")
        if fn is None or fn.parameters:
            return None
        r = self.call_function(fn)
        if r.status_code >= 400:
            return None
        return first_flag(r.json().get("d", {}))

    # ------------------------------------------------------------------ interacción
    def make_changes(self, prompt: str, mock_body: dict[str, int]) -> bool | None:
        """Genera cambios en el origen. True: hubo cambios; None: no se sabe."""
        if self.admin is not None and self.args.mock_admin_url:
            r = self.admin.post(f"{self.args.mock_admin_url.rstrip('/')}/changes", json=mock_body)
            r.raise_for_status()
            log(f"   cambios en el mock {mock_body}")
            return True
        if self.args.non_interactive:
            log(f"   esperando {self.args.wait_seconds}s. {prompt}")
            time.sleep(self.args.wait_seconds)
            return None
        answer = input(f"   ▶ {prompt}\n     Pulse Enter al terminar (n si no generó cambios): ")
        return answer.strip().lower() not in {"n", "no"}

    def manual_action(self, prompt: str, mock_endpoint: str) -> bool:
        if self.admin is not None and self.args.mock_admin_url:
            url = f"{self.args.mock_admin_url.rstrip('/')}/{mock_endpoint}"
            self.admin.post(url).raise_for_status()
            log(f"   mock: {mock_endpoint}")
            return True
        if self.args.non_interactive:
            return False
        answer = input(f"   ▶ {prompt}\n     Pulse Enter al terminar (n para saltar): ")
        return answer.strip().lower() not in {"n", "no"}

    def confirm_terminate(self) -> bool:
        if self.args.non_interactive:
            return True  # --allow-terminate ya es la confirmación explícita
        answer = input(
            f"   ▶ Se llamará a TerminateDeltasFor{self.args.entity_set}: cierra suscripciones en "
            "SAP. Escriba CERRAR para continuar: "
        )
        return answer.strip() == "CERRAR"

    # ------------------------------------------------------------------ pruebas
    def load_metadata(self) -> None:
        log("$metadata")
        r = self.call("GET", f"{self.service_url}/$metadata", headers={"Accept": "application/xml"})
        self.save("metadata.xml", r.text)
        self.md = parse_metadata(r.content, self.args.entity_set)
        names = [f.name for f in self.md.functions]
        self.results["metadata"] = {
            "entity_sets": self.md.entity_sets,
            "keys": self.md.keys,
            "property_count": len(self.md.props),
            "has_odq_fields": self.md.prop("ODQ_CHANGEMODE") is not None,
            "function_imports": names,
        }
        log(f"   entity sets: {[s['name'] for s in self.md.entity_sets]}")
        log(f"   claves: {self.md.keys} · propiedades: {len(self.md.props)} · funciones: {names}")
        if not self.md.keys:
            log(f"   ⚠ no se encontró el entity type de {self.args.entity_set}")

    def test_preview(self) -> None:
        before, links_before = self.subscribed_to(), self.delta_links_count()
        timings: dict[str, float] = {}
        for top in dict.fromkeys((1, self.args.preview_rows)):
            started = time.perf_counter()
            r = self.call("GET", self.entity_url, params={"$format": "json", "$top": str(top)})
            timings[f"top_{top}_ms"] = ms_since(started)
            if top == self.args.preview_rows:
                body = r.json()
                self.preview = body.get("d", {}).get("results", [])
                self.save("preview.sample.json", self.sample(body))
        after, links_after = self.subscribed_to(), self.delta_links_count()
        evidence = {
            "subscribed_before": before,
            "subscribed_after": after,
            "delta_links_before": links_before,
            "delta_links_after": links_after,
        }
        grew = links_before is not None and links_after is not None and links_after > links_before
        if (before is False and after is True) or grew:
            self.found("H4", Outcome.REFUTED, "La vista previa crea una suscripción", evidence)
        elif before is False and after is False:
            self.found(
                "H4",
                Outcome.CONFIRMED,
                "SubscribedTo… sigue en false tras la vista previa",
                evidence,
            )
        else:
            reason = (
                "ya había una suscripción antes de la prueba"
                if before
                else "sin SubscribedTo… no se comprueba solo"
            )
            self.found("H4", Outcome.REVIEW, f"DeltaLinksOf… no cambia; {reason}", evidence)
        top_n = self.args.preview_rows
        summary = f"$top=1: {timings['top_1_ms']} ms; $top={top_n}: {timings[f'top_{top_n}_ms']} ms"
        if timings["top_1_ms"] > SLOW_PREVIEW_MS:
            summary += "; lenta: posible extracción completa en SAP"
        self.found("H16", Outcome.REVIEW, summary, timings)

    def test_formats(self) -> None:
        if not self.preview:
            r = self.call("GET", self.entity_url, params={"$format": "json", "$top": "5"})
            self.preview = r.json().get("d", {}).get("results", [])
        self.check_amounts()
        self.check_dates()

    def currency_field(self) -> str | None:
        if self.args.currency_field:
            return str(self.args.currency_field)
        by_semantics = [p.name for p in self.md.props if p.semantics == "currency-code"]
        if by_semantics:
            return by_semantics[0]
        units = {p.unit for p in self.md.props if p.edm_type == "Edm.Decimal" and p.unit}
        for name in [*units, *(p.name for p in self.md.props)]:
            if name and (name.startswith("WAER") or name.lower().endswith("currency")):
                return name
        return None

    def check_amounts(self) -> None:
        cur_field = self.currency_field()
        if cur_field is None:
            self.found(
                "H14", Outcome.SKIPPED, "No se encontró campo de moneda: use --currency-field"
            )
            return
        amounts = [
            p
            for p in self.md.props
            if p.edm_type == "Edm.Decimal"
            and p.name not in ODQ_FIELDS
            and p.unit in (None, cur_field)
        ]
        rows: list[dict[str, Any]] = []
        errors: dict[str, Any] = {}
        for cur in self.args.currencies.split(","):
            cur = cur.strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{1,5}", cur):
                continue
            params = {"$format": "json", "$top": "5", "$filter": f"{cur_field} eq '{cur}'"}
            try:
                rows += self.call("GET", self.entity_url, params=params).json()["d"]["results"]
            except SapCallError as exc:
                errors[cur] = exc.as_evidence()
        evidence: dict[str, Any] = {"currency_field": cur_field, "errors": errors}
        if not rows:
            self.found(
                "H14",
                Outcome.SKIPPED,
                f"No hay registros en {self.args.currencies} ({cur_field}): use --currencies",
                evidence,
            )
            return
        lines = ["| Claves | Moneda | Campo | Valor recibido |", "|---|---|---|---|"]
        total = fractional = 0
        for row in rows:
            for p in amounts:
                value = row.get(p.name)
                if value is None:
                    continue
                total += 1
                if "." in str(value) and str(value).split(".")[1].strip("0"):
                    fractional += 1
                lines.append(
                    f"| {self.keys_of(row)} | {row.get(cur_field)} | {p.name} "
                    f"| {self.mask(p.name, value)} |"
                )
        self.save(
            "h14_importes.md", "# H14 · Importes para comparar con SAP GUI\n\n" + "\n".join(lines)
        )
        hint = (
            "formato interno probable (factor 100)"
            if fractional
            else "sin decimales: formato externo probable, o importes redondos"
        )
        evidence |= {"rows": len(rows), "amounts": total, "with_fraction": fractional}
        self.found(
            "H14",
            Outcome.REVIEW,
            f"{fractional} de {total} importes con decimales no nulos: {hint}",
            evidence,
        )

    def check_dates(self) -> None:
        props = [
            p
            for p in self.md.props
            if p.edm_type in ("Edm.DateTime", "Edm.DateTimeOffset", "Edm.Time")
        ]
        if not props:
            self.found("H15", Outcome.SKIPPED, "El origen no tiene campos de fecha u hora")
            return
        lines = [
            "| Claves | Campo | Tipo | Formato | Valor bruto | Leído sin zona |",
            "|---|---|---|---|---|---|",
        ]
        for row in self.preview[:3]:
            for p in props:
                raw = self.mask(p.name, row.get(p.name))
                lines.append(
                    f"| {self.keys_of(row)} | {p.name} | {p.edm_type} | {p.display_format or ''} "
                    f"| {raw} | {decode_edm_date(raw) if raw != '***' else raw} |"
                )
        self.save(
            "h15_fechas.md", "# H15 · Fechas para comparar con SAP GUI\n\n" + "\n".join(lines)
        )
        with_time = [p.name for p in props if p.display_format != "Date"]
        summary = (
            f"Campos con hora: {', '.join(with_time)}; compare h15_fechas.md con SAP GUI"
            if with_time
            else "Solo fechas (sap:display-format=Date): sin hora no hay ambigüedad de zona"
        )
        evidence = {"fields": [asdict(p) for p in props], "with_time": with_time}
        self.found("H15", Outcome.REVIEW, summary, evidence)

    def test_core(self) -> None:
        core: dict[str, Any] = {}
        self.results["core"] = core
        if self.args.delta_link:
            t1: str = self.args.delta_link
            self.chains["core_init"] = t1
            for hyp in ("H1", "H5"):
                self.found(hyp, Outcome.SKIPPED, "Se usó --delta-link: no hubo init")
        else:
            init = self.init("core_init", self.args.filter)
            core["init"] = init.summary()
            self.core_first_page_ms = init.first_page_ms
            if not init.delta_link:
                self.found(
                    "H1", Outcome.REFUTED, "La última página no trae __delta: sin change tracking"
                )
                return
            t1 = init.delta_link
            self.found("H1", Outcome.CONFIRMED, f"Token {token_of(t1)} en la página {init.pages}")
            self.found(
                "H5",
                Outcome.REVIEW,
                f"{init.rows} filas en {init.seconds}s: {init.rows_per_second} filas/s con páginas "
                f"de {self.args.page_size}; primera página en {init.first_page_ms} ms",
                core["init"],
            )

        self.make_changes(
            "Cree 2 registros, modifique 2 y borre 1 en SAP para este origen (H3, H17)",
            {"inserts": 5, "updates": 3, "deletes": 2},
        )
        first = self.delta(t1, "core_delta")
        core["delta"] = first.summary()
        if first.rows == 0:
            for hyp in ("H2", "H3", "H11", "H17"):
                self.found(hyp, Outcome.SKIPPED, "El delta llegó vacío: no hubo cambios")
            return
        if first.missing_odq:
            self.found(
                "H3",
                Outcome.REFUTED,
                f"{first.missing_odq} de {first.rows} registros sin campos ODQ",
                core["delta"],
            )
        else:
            self.found(
                "H3",
                Outcome.CONFIRMED,
                f"Modos {dict(first.change_modes)}; ODQ_ENTITYCNTR {dict(first.entity_counters)}",
                core["delta"],
            )
        if not first.deletes:
            self.found("H17", Outcome.SKIPPED, "El delta no trajo borrados (D)")
        elif first.deletes_with_values:
            self.found(
                "H17",
                Outcome.REFUTED,
                f"Los registros D traen informados: {', '.join(sorted(first.deletes_with_values))}",
                {"deletes": first.deletes, "fields": dict(first.deletes_with_values)},
            )
        else:
            self.found(
                "H17",
                Outcome.CONFIRMED,
                f"{first.deletes} registros D solo con las claves informadas",
            )

        changed_between = self.make_changes(
            "Genere 1 o 2 cambios más ANTES de la repetición (H11)", {"inserts": 2, "updates": 1}
        )
        repeat = self.delta(t1, "core_repeat")
        cmp = compare(first.fingerprints, repeat.fingerprints)
        evidence: dict[str, Any] = {
            "first": first.summary(),
            "repeat": repeat.summary(),
            "comparison": cmp,
            "changes_between_reads": changed_between,
            "same_token": token_of(first.delta_link) == token_of(repeat.delta_link),
            "missing_in_repeat": [
                first.sample_keys[fp] for fp in (first.fingerprints - repeat.fingerprints)
            ][:5],
        }
        core["repeat"] = evidence
        if cmp["only_first"]:
            self.found(
                "H2", Outcome.REFUTED, f"Faltan {cmp['only_first']} registros en la repetición"
            )
            self.found(
                "H11",
                Outcome.REFUTED,
                f"La repetición no contiene {cmp['only_first']} registros del primer delta",
                evidence,
            )
        else:
            self.found(
                "H2",
                Outcome.CONFIRMED,
                "La repetición contiene todos los registros del primer delta",
            )
            if cmp["only_second"]:
                self.found(
                    "H11",
                    Outcome.CONFIRMED,
                    f"Variante «todo lo ocurrido desde el token hasta ahora»: "
                    f"+{cmp['only_second']} registros nuevos",
                    evidence,
                )
            elif changed_between:
                self.found(
                    "H11",
                    Outcome.CONFIRMED,
                    "Variante «mismo conjunto»: repite exactamente",
                    evidence,
                )
            else:
                self.found(
                    "H11",
                    Outcome.REVIEW,
                    "Mismo conjunto, pero no consta que hubiera cambios entre las dos lecturas",
                    evidence,
                )

        # ¿Qué token sigue valiendo después de una repetición? Lo necesita el motor (plan 06 §3).
        next_tokens: dict[str, Any] = {}
        candidates = [repeat.delta_link, first.delta_link]
        for name, link in zip(("repeat", "first"), candidates, strict=True):
            if not link or token_of(link) in {v.get("token") for v in next_tokens.values()}:
                continue
            try:
                res = self.delta(link, f"core_next_from_{name}")
                next_tokens[name] = {"token": token_of(link), "ok": True, "rows": res.rows}
            except SapCallError as exc:
                next_tokens[name] = {"token": token_of(link), "ok": False, **exc.as_evidence()}
        evidence["next_after_repeat"] = next_tokens

    def test_two_chains(self, hyp: str) -> None:
        same_filter = hyp == "H18"
        filter_a = self.args.filter
        filter_b = self.args.filter if same_filter else self.args.filter_b
        if not same_filter and (not filter_b or filter_b == filter_a):
            self.found(hyp, Outcome.SKIPPED, "Requiere --filter-b distinto de --filter")
            return
        tag = hyp.lower()
        steps: dict[str, Any] = {}
        evidence: dict[str, Any] = {"filter_a": filter_a, "filter_b": filter_b, "steps": steps}

        def run(step: str, action: Callable[[], ReadResult]) -> ReadResult | None:
            try:
                res = action()
                steps[step] = res.summary()
                return res
            except SapCallError as exc:
                steps[step] = exc.as_evidence()
                self.found(hyp, Outcome.REFUTED, f"Falla «{step}»: {exc}", evidence)
                return None

        a = run("init A", lambda: self.init(f"{tag}_init_a", filter_a))
        if a is None:
            return
        if not a.delta_link:
            self.found(hyp, Outcome.ERROR, "La init A no devolvió token (ver H1)", evidence)
            return
        b = run("init B", lambda: self.init(f"{tag}_init_b", filter_b))
        if b is None:
            return
        if not b.delta_link:
            self.found(hyp, Outcome.REFUTED, "La segunda init no devolvió token", evidence)
            return
        ta1, tb1 = a.delta_link, b.delta_link
        evidence["same_initial_token"] = token_of(ta1) == token_of(tb1)
        scope = "que cumplan los dos filtros" if not same_filter else "que cumplan el filtro"
        self.make_changes(
            f"Cree, modifique o borre registros {scope}", {"inserts": 4, "updates": 2, "deletes": 1}
        )
        sa1 = run("delta A", lambda: self.delta(ta1, f"{tag}_a_delta1"))
        sb1 = run("delta B", lambda: self.delta(tb1, f"{tag}_b_delta1"))
        if sa1 is None or sb1 is None:
            return
        self.make_changes(f"Genere otro cambio {scope}", {"inserts": 2, "updates": 1})
        ta2 = sa1.delta_link or ta1
        sa2 = run("delta A siguiente (confirma A)", lambda: self.delta(ta2, f"{tag}_a_delta2"))
        sb1r = run(
            "repetición de B después de que A avanzara", lambda: self.delta(tb1, f"{tag}_b_repeat")
        )
        if sa2 is None or sb1r is None:
            return
        tb2 = sb1r.delta_link or sb1.delta_link or tb1
        sb2 = run("delta B siguiente", lambda: self.delta(tb2, f"{tag}_b_delta2"))
        if sb2 is None:
            return

        repeat_ok = compare(sb1.fingerprints, sb1r.fingerprints)["only_first"] == 0
        evidence["b_repeat_vs_b_first"] = compare(sb1.fingerprints, sb1r.fingerprints)
        if same_filter:
            evidence["b_first_vs_a_first"] = compare(sa1.fingerprints, sb1.fingerprints)
            totals_equal = (sa1.fingerprints + sa2.fingerprints) == (
                sb1r.fingerprints + sb2.fingerprints
            )
            evidence["totals_equal"] = totals_equal
            if sa1.rows == 0 and sb1.rows == 0:
                self.found(hyp, Outcome.SKIPPED, "Los dos deltas llegaron vacíos", evidence)
            elif sb1.rows == 0 and sa1.rows > 0:
                self.found(
                    hyp,
                    Outcome.REFUTED,
                    "B no recibe los cambios que leyó A: comparten la cola",
                    evidence,
                )
            elif sa1.fingerprints == sb1.fingerprints and repeat_ok and totals_equal:
                self.found(
                    hyp,
                    Outcome.CONFIRMED,
                    "B recibe los mismos cambios que A y su repetición sigue disponible después "
                    "de que A avanzara",
                    evidence,
                )
            else:
                self.found(
                    hyp, Outcome.REVIEW, "Resultados distintos entre A y B: ver evidencia", evidence
                )
            return
        if not repeat_ok:
            self.found(
                hyp,
                Outcome.REFUTED,
                "La repetición de B pierde registros cuando A avanza",
                evidence,
            )
        elif sa1.rows == 0 or sb1.rows == 0:
            self.found(
                hyp,
                Outcome.REVIEW,
                f"Delta A: {sa1.rows}, delta B: {sb1.rows}; genere cambios en ambos filtros",
                evidence,
            )
        else:
            self.found(
                hyp,
                Outcome.CONFIRMED,
                f"Las dos suscripciones avanzan por separado (A: {sa1.rows}, B: {sb1.rows})",
                evidence,
            )

    def test_two_filters(self) -> None:
        self.test_two_chains("H6")

    def test_same_filter(self) -> None:
        self.test_two_chains("H18")

    def test_terminate(self) -> None:
        sub_fn, term_fn = self.function("SubscribedTo"), self.function("TerminateDeltasFor")
        related = [
            asdict(f)
            for f in self.md.functions
            if any(word in f.name for word in ("Subscri", "Terminate", "Delta"))
        ]
        evidence: dict[str, Any] = {"functions": related}
        if sub_fn is None and term_fn is None:
            self.found("H12", Outcome.REFUTED, "No existen en el $metadata", evidence)
            return
        if term_fn is None:
            self.found("H12", Outcome.REVIEW, "Solo existe SubscribedTo…", evidence)
            return
        methods = ", ".join(f"{f['name']} ({f['http_method']})" for f in related)
        evidence["subscribed_initially"] = self.subscribed_to()
        if not self.args.allow_terminate:
            self.found(
                "H12",
                Outcome.REVIEW,
                f"Existen: {methods}. Cierre sin probar: repita con --allow-terminate",
                evidence,
            )
            return
        a = self.init("h12_init_a", self.args.filter)
        b = self.init("h12_init_b", self.args.filter_b or self.args.filter)
        evidence["subscribed_before"] = self.subscribed_to()
        if not (a.delta_link and b.delta_link):
            self.found("H12", Outcome.ERROR, "Alguna init no devolvió token", evidence)
            return
        if not self.confirm_terminate():
            self.found("H12", Outcome.SKIPPED, "Cierre cancelado por el usuario", evidence)
            return
        r = self.call_function(term_fn)
        evidence["terminate"] = {"status": r.status_code}
        if r.status_code < 400 and r.content:
            try:
                evidence["terminate"]["result_flag"] = first_flag(r.json().get("d", {}))
            except ValueError:
                evidence["terminate"]["result_flag"] = None
        if r.status_code >= 400:
            code, message = sap_error_of(r)
            evidence["terminate"] |= {"code": code, "message": message}
            self.found(
                "H12", Outcome.REFUTED, f"{term_fn.name} responde HTTP {r.status_code}", evidence
            )
            return
        evidence["subscribed_after"] = self.subscribed_to()
        alive: dict[str, Any] = {}
        for chain, link in self.chains.items():
            try:
                self.read_all(self.rebase(link), max_pages=1)
                alive[chain] = True
            except SapCallError as exc:
                alive[chain] = f"HTTP {exc.status} {exc.code}".strip()
        evidence["tokens_alive_after_terminate"] = alive
        still = [c for c, ok in alive.items() if ok is True]
        if not still and evidence["subscribed_after"] is not True:
            self.found(
                "H12",
                Outcome.CONFIRMED,
                f"Cierra las {len(alive)} cadenas abiertas en esta ejecución (alcance: todas las "
                "del entity set para el usuario técnico)",
                evidence,
            )
        elif len(still) < len(alive):
            self.found(
                "H12",
                Outcome.REVIEW,
                f"Cierra solo algunas cadenas; siguen vivas: {', '.join(still)}",
                evidence,
            )
        else:
            self.found(
                "H12",
                Outcome.REFUTED,
                f"{term_fn.name} responde bien pero los tokens siguen valiendo",
                evidence,
            )

    def test_odqmon_delete(self) -> None:
        init = self.init("h7_init", self.args.filter)
        if not init.delta_link:
            self.found("H7", Outcome.ERROR, "La init no devolvió token (ver H1)")
            return
        done = self.manual_action(
            f"En ODQMON, borre la suscripción del usuario {self.args.user} para el ODP de "
            f"{self.args.entity_set}",
            "invalidate-tokens",
        )
        if not done:
            self.found("H7", Outcome.SKIPPED, "Requiere modo interactivo o --mock-admin-url")
            return
        try:
            res = self.delta(init.delta_link, "h7_delta")
            self.found(
                "H7",
                Outcome.REFUTED,
                f"El token sigue valiendo ({res.rows} registros) tras borrar la suscripción",
            )
        except SapCallError as exc:
            self.found("H7", Outcome.CONFIRMED, f"El token deja de valer: {exc}", exc.as_evidence())

    def test_interrupted_init(self) -> None:
        params = {"$filter": self.args.filter} if self.args.filter else None
        # Corte tras la primera página; se repite la segunda (una página intermedia: la init
        # necesita al menos 3 páginas).
        first = self.read_all(
            self.entity_url, params=params, tracking=True, max_pages=1, page_size=CUT_PAGE_SIZE
        )
        if not first.next_link:
            self.note_open("h10_cut", self.args.filter, first.delta_link, complete=True)
            self.found(
                "H10", Outcome.SKIPPED, f"La init cabe en una página de {CUT_PAGE_SIZE} filas"
            )
            return
        self.note_open("h10_cut", self.args.filter, None, complete=False)
        page = first.next_link

        def read_page() -> ReadResult:
            return self.read_all(
                page, tracking=True, max_pages=1, page_size=CUT_PAGE_SIZE, analyze=True
            )

        page_a = read_page()
        if not page_a.next_link:
            self.found(
                "H10",
                Outcome.SKIPPED,
                f"La init cabe en menos de 3 páginas de {CUT_PAGE_SIZE} filas",
            )
            return
        evidence: dict[str, Any] = {"page_rows": page_a.rows}
        try:
            page_b = read_page()
        except SapCallError as exc:
            evidence["repeat"] = exc.as_evidence()
            self.found(
                "H10", Outcome.REFUTED, f"Volver a pedir la misma página falla: {exc}", evidence
            )
            return
        evidence["same_page"] = page_a.fingerprints == page_b.fingerprints
        evidence["comparison"] = compare(page_a.fingerprints, page_b.fingerprints)
        try:
            relaunch = self.init("h10_relaunch", self.args.filter)
            evidence["relaunch"] = relaunch.summary()
        except SapCallError as exc:
            evidence["relaunch"] = exc.as_evidence()
        try:
            evidence["old_page_after_relaunch"] = {"ok": True, "rows": read_page().rows}
        except SapCallError as exc:
            evidence["old_page_after_relaunch"] = {"ok": False, **exc.as_evidence()}
        evidence["subscribed"] = self.subscribed_to()
        evidence["delta_links"] = self.delta_links_count()
        if not evidence["same_page"]:
            self.found(
                "H10", Outcome.REFUTED, "La misma página devuelve registros distintos", evidence
            )
            return
        relaunch_ok = "rows" in evidence["relaunch"]
        self.found(
            "H10",
            Outcome.REVIEW,
            f"La página repetida coincide ({page_a.rows} registros); la init relanzada "
            + ("funciona" if relaunch_ok else f"falla: HTTP {evidence['relaunch']['status']}")
            + ". Revise en ODQMON cuántas suscripciones quedaron",
            evidence,
        )

    def test_init_without_data(self) -> None:
        params = {"$format": "json", "$top": "0"}
        if self.args.filter:
            params["$filter"] = self.args.filter
        headers = {"Prefer": f"odata.track-changes, odata.maxpagesize={self.args.page_size}"}
        d = self.call("GET", self.entity_url, params=params, headers=headers).json().get("d", {})
        rows, link = len(d.get("results", [])), d.get("__delta")
        evidence: dict[str, Any] = {
            "method": "$top=0 con Prefer: odata.track-changes",
            "rows": rows,
            "has_next": bool(d.get("__next")),
            "token": token_of(link),
        }
        self.note_open("h9_top0", self.args.filter, link, complete=link is not None)
        if rows or d.get("__next"):
            self.found(
                "H9",
                Outcome.REFUTED,
                f"Con $top=0 la init devuelve datos ({rows} filas): esta vía no sirve",
                evidence,
            )
            return
        if not link:
            self.found("H9", Outcome.REFUTED, "Con $top=0 no devuelve __delta", evidence)
            return
        self.make_changes("Modifique 1 registro de este origen (H9)", {"updates": 1})
        res = self.delta(link, "h9_delta")
        evidence["delta"] = res.summary()
        self.found(
            "H9",
            Outcome.REVIEW,
            f"$top=0 devuelve token sin filas y el delta siguiente trae {res.rows} registros: "
            "confirme en ODQMON que es una init sin datos",
            evidence,
        )

    def test_large_init(self) -> None:
        params = {"$format": "json"}
        if self.args.filter:
            params["$filter"] = self.args.filter
        prefer = f"respond-async, odata.track-changes, odata.maxpagesize={self.args.page_size}"
        evidence: dict[str, Any] = {"core_first_page_ms": self.core_first_page_ms}
        started = time.perf_counter()
        try:
            r = self.call(
                "GET", self.entity_url, params=params, headers={"Prefer": prefer}, expect_ok=False
            )
        except httpx.TimeoutException:
            evidence["probe"] = {"timeout_s": self.args.timeout}
            self.note_open("h8_probe", self.args.filter, None, complete=False)
            self.found(
                "H8",
                Outcome.REFUTED,
                f"Sin primera página en {self.args.timeout}s (--timeout)",
                evidence,
            )
            return
        probe: dict[str, Any] = {
            "status": r.status_code,
            "ms": ms_since(started),
            "preference_applied": r.headers.get("preference-applied"),
            "location": r.headers.get("location"),
        }
        evidence["probe"] = probe
        if r.status_code == 202:
            async_text = "respond-async admitido (202)"
        elif r.status_code < 400:
            async_text = "respond-async ignorado (respuesta síncrona)"
        else:
            probe["code"], probe["message"] = sap_error_of(r)
            async_text = f"respond-async rechazado (HTTP {r.status_code})"
        self.note_open("h8_probe", self.args.filter, None, complete=False)
        first_ms = self.core_first_page_ms
        if first_ms is None and r.status_code == 200:
            first_ms = probe["ms"]
        limit = self.args.dialog_limit_seconds
        summary = f"Primera página en {first_ms} ms; {async_text}"
        if first_ms is None:
            self.found(
                "H8", Outcome.REVIEW, f"{async_text}; sin medida de la primera página", evidence
            )
        elif limit:
            outcome = Outcome.CONFIRMED if first_ms / 1000 < limit else Outcome.REFUTED
            self.found("H8", outcome, f"{summary}; límite {limit}s", evidence)
        else:
            self.found(
                "H8", Outcome.REVIEW, f"{summary}; compare con los límites de Basis", evidence
            )

    def test_retention(self) -> None:
        if not self.args.previous_results:
            self.found(
                "H13",
                Outcome.SKIPPED,
                "Requiere --previous-results con el results.json de una ejecución de hace ≥ 24 h",
            )
            return
        previous = json.loads(Path(self.args.previous_results).read_text(encoding="utf-8"))
        confirmed = previous.get("links", {}).get("core_init")
        last = previous.get("chains", {}).get("core_init")
        if not confirmed or not last:
            self.found(
                "H13", Outcome.SKIPPED, "La ejecución anterior no tiene la prueba núcleo (H11)"
            )
            return
        obtained = datetime.fromisoformat(confirmed["obtained_at"])
        hours = round((datetime.now(UTC) - obtained).total_seconds() / 3600, 1)
        evidence: dict[str, Any] = {"hours_since_previous": hours}
        texts = []
        checks = (
            ("delta ya confirmado", "h13_confirmed", confirmed["link"]),
            ("último token", "h13_last", last),
        )
        for name, label, link in checks:
            try:
                res = self.delta(link, label)
                evidence[name] = {"ok": True, "rows": res.rows}
                texts.append(f"{name}: vale ({res.rows} registros)")
            except SapCallError as exc:
                evidence[name] = {"ok": False, **exc.as_evidence()}
                texts.append(f"{name}: no vale (HTTP {exc.status})")
        self.found("H13", Outcome.REVIEW, f"Tras {hours} h: " + "; ".join(texts), evidence)

    # ------------------------------------------------------------------ orquestación
    def run(self) -> int:
        selected = [t for t in TESTS if set(t.covers) & set(self.args.hypotheses)]
        try:
            self.load_metadata()
            for test in selected:
                self.run_test(test)
        except (SapCallError, httpx.HTTPError) as exc:
            self.results["error"] = f"{type(exc).__name__}: {exc}"
            log(f"✖ {self.results['error']}")
        finally:
            self.results["finished_at"] = datetime.now(UTC).isoformat()
            self.results["hypotheses"] = {
                h: {"title": HYPOTHESES[h], **asdict(f)} for h, f in sorted_findings(self.findings)
            }
            self.results["opened_subscriptions"] = self.opened
            self.results["links"] = self.links
            self.results["chains"] = self.chains
            self.save("results.json", self.results)
            self.save("report.md", render_report(self.results))
            log(f"Resultados en {self.out}")
        failed = "error" in self.results or any(
            f.outcome is Outcome.ERROR for f in self.findings.values()
        )
        return 1 if failed else 0

    def run_test(self, test: SpikeTest) -> None:
        log(f"== {test.title} ({', '.join(test.covers)})")
        try:
            test.run(self)
        except SapCallError as exc:
            self.fail_pending(test, str(exc))
        except httpx.TimeoutException as exc:
            self.fail_pending(test, f"Sin respuesta en {self.args.timeout}s ({type(exc).__name__})")
        except httpx.HTTPError as exc:
            self.fail_pending(test, f"{type(exc).__name__}: {exc}")
        for hyp in test.covers:
            if hyp not in self.findings:
                self.found(hyp, Outcome.SKIPPED, "La prueba terminó antes de evaluarla")

    def fail_pending(self, test: SpikeTest, message: str) -> None:
        for hyp in test.covers:
            if hyp not in self.findings:
                self.found(hyp, Outcome.ERROR, message)


@dataclass(frozen=True)
class SpikeTest:
    title: str
    covers: tuple[str, ...]
    run: Callable[[Spike], None]


# Orden de ejecución: primero lo que no abre suscripciones; el cierre (H12) después de las
# pruebas que necesitan sus cadenas vivas.
TESTS: tuple[SpikeTest, ...] = (
    SpikeTest("Vista previa", ("H4", "H16"), Spike.test_preview),
    SpikeTest("Formatos de importes y fechas", ("H14", "H15"), Spike.test_formats),
    SpikeTest(
        "Núcleo: init, delta y repetición", ("H1", "H2", "H3", "H5", "H11", "H17"), Spike.test_core
    ),
    SpikeTest("Dos suscripciones con filtros distintos", ("H6",), Spike.test_two_filters),
    SpikeTest("Dos inits con el mismo filtro", ("H18",), Spike.test_same_filter),
    SpikeTest("Cierre de suscripciones", ("H12",), Spike.test_terminate),
    SpikeTest("Suscripción borrada en ODQMON", ("H7",), Spike.test_odqmon_delete),
    SpikeTest("Init cortada y relanzada", ("H10",), Spike.test_interrupted_init),
    SpikeTest("Init sin datos", ("H9",), Spike.test_init_without_data),
    SpikeTest("Init grande y respond-async", ("H8",), Spike.test_large_init),
    SpikeTest("Retención de la ODQ", ("H13",), Spike.test_retention),
)


def sorted_findings(findings: dict[str, Finding]) -> list[tuple[str, Finding]]:
    return sorted(findings.items(), key=lambda item: int(item[0][1:]))


# ---------------------------------------------------------------------------------- informe


def render_report(r: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    hyps: dict[str, dict[str, Any]] = r.get("hypotheses", {})
    lines = [
        f"# Resultado del spike SAP · {r.get('service')} / {r.get('entity_set')}",
        "",
        f"Inicio: {r.get('started_at')} · Fin: {r.get('finished_at')}",
        "",
        f"Página: {r.get('page_size')} filas. Filtro: `{r.get('filter') or '—'}`. "
        f"Filtro B: `{r.get('filter_b') or '—'}`. Pedidas: {', '.join(r.get('requested', []))}.",
        "",
        "| # | Hipótesis | Resultado | Detalle |",
        "|---|---|---|---|",
    ]
    for hyp, f in hyps.items():
        lines.append(f"| {hyp} | {cell(f['title'])} | **{f['outcome']}** | {cell(f['summary'])} |")
    manual = [(h, f) for h, f in hyps.items() if f["manual"] and f["outcome"] != Outcome.SKIPPED]
    if manual:
        lines += ["", "## Comprobaciones manuales", ""]
        for hyp, f in manual:
            lines += [f"**{hyp}**", ""] + [f"- [ ] {item}" for item in f["manual"]] + [""]
    opened = r.get("opened_subscriptions", [])
    if opened:
        lines += [
            "",
            "## Suscripciones abiertas en SAP por esta ejecución",
            "",
            "Bórrelas en ODQMON al terminar, salvo las que H12 haya cerrado.",
            "",
            "| Prueba | Filtro | Token | Init completa |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {o['test']} | {cell(o['filter'] or '—')} | {o['token'] or '—'} "
            f"| {'sí' if o['complete'] else 'no'} |"
            for o in opened
        ]
    if r.get("error"):
        lines += ["", f"**Error general:** {cell(r['error'])}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------- CLI


def parse_hypotheses(value: str) -> list[str]:
    if value.strip().lower() in {"all", "todas"}:
        return list(HYPOTHESES)
    items = [v.strip().upper() for v in value.split(",") if v.strip()]
    unknown = [v for v in items if v not in HYPOTHESES]
    if unknown:
        raise argparse.ArgumentTypeError(f"hipótesis desconocidas: {', '.join(unknown)}")
    return items


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base-url", required=True, help="https://host:puerto del SAP Gateway")
    p.add_argument("--service", required=True, help="Nombre técnico del servicio (…_SRV)")
    p.add_argument(
        "--entity-set", required=True, help="EntityOf<DataSource> / FactsOf<CDS> / entidad"
    )
    p.add_argument("--user", required=True, help="Usuario técnico SAP")
    p.add_argument("--client", help="Mandante (sap-client)")
    p.add_argument("--language", default="ES")
    p.add_argument(
        "--hypotheses",
        type=parse_hypotheses,
        default=list(DEFAULT_HYPOTHESES),
        help="Lista separada por comas (H6,H12,H18) o all. Por defecto: "
        + ",".join(DEFAULT_HYPOTHESES),
    )
    p.add_argument("--filter", help="$filter de las inits (p. ej. RBUKRS eq '1000')")
    p.add_argument("--filter-b", help="Segundo $filter, distinto del primero (H6 y H12)")
    p.add_argument("--page-size", type=int, default=5000)
    p.add_argument("--preview-rows", type=int, default=100, help="Filas de la vista previa (H16)")
    p.add_argument("--delta-link", help="Prueba núcleo: partir de un delta link ya obtenido")
    p.add_argument(
        "--previous-results", help="H13: results.json de una ejecución anterior (≥ 24 h)"
    )
    p.add_argument(
        "--allow-terminate",
        action="store_true",
        help="H12: permite llamar a TerminateDeltasFor… (cierra suscripciones en SAP)",
    )
    p.add_argument(
        "--dialog-limit-seconds", type=float, help="H8: límite de diálogo/ICM que indique Basis"
    )
    p.add_argument("--currency-field", help="H14: campo de moneda si no se detecta en $metadata")
    p.add_argument("--currencies", default="CLP,JPY", help="H14: monedas sin decimales a buscar")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--ca-bundle", help="Certificado CA del Gateway (PEM)")
    p.add_argument("--insecure", action="store_true", help="No verificar TLS (solo pruebas)")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--wait-seconds", type=int, default=60)
    p.add_argument(
        "--mock-admin-url",
        help="Solo con el mock: base /_mock/services/<SERVICIO> para generar cambios y borrar "
        "suscripciones",
    )
    p.add_argument("--sample-records", type=int, default=5)
    p.add_argument(
        "--mask-fields", help="Campos a enmascarar en muestras y listas, separados por coma"
    )
    p.add_argument("--output", default="tools/spike-sap/output")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Consolas Windows (cp1252): evita fallos al imprimir caracteres como → o ✖.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    password = os.getenv("SPIKE_SAP_PASSWORD") or getpass.getpass(f"Contraseña de {args.user}: ")
    if args.insecure:
        log("⚠ Verificación TLS desactivada (--insecure)")
    return Spike(args, password).run()


if __name__ == "__main__":
    raise SystemExit(main())
