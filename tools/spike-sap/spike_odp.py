# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Spike SAP: valida el comportamiento real de ODP/CDS vía OData (iteración 0).

Ejecuta, contra UN servicio OData publicado en SAP Gateway, la secuencia que usará el motor:

  1. $metadata (entidades, campos, ¿change tracking?)
  2. Lectura full de una página SIN suscripción (latencia)
  3. Inicialización de delta (Prefer: odata.track-changes) paginando hasta el final → token T1
  4. Pausa para generar cambios en SAP (o --trigger-url con el mock)
  5. Delta con T1 → registros C/U/D y token T2
  6. REPETICIÓN: delta otra vez con T1 → ¿mismos registros? ¿mismo token?
  7. Listado DeltaLinksOf…

Guarda en --output: results.json, report.md (para pegar en docs/adr/0002) y muestras de
respuestas (pocas filas, para alimentar el mock). La contraseña se lee de SPIKE_SAP_PASSWORD
o se pide por teclado; nunca se escribe en disco ni en pantalla.

Ejemplos:
  uv run tools/spike-sap/spike_odp.py --base-url https://gateway:44300 --client 100 \\
      --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user EXTRACT_USER

  # Contra el mock local (docker compose):
  SPIKE_SAP_PASSWORD=sxmock uv run tools/spike-sap/spike_odp.py --base-url http://localhost:8081 \\
      --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user sxmock --non-interactive \\
      --trigger-url http://localhost:8081/_mock/services/ZFI_WS_GL_ITEMS_SRV/changes
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

import httpx

EDM_NS = {
    "edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
    "edm": "http://schemas.microsoft.com/ado/2008/09/edm",
    "sap": "http://www.sap.com/Protocols/SAPData",
}


@dataclass
class ReadResult:
    rows: int = 0
    pages: int = 0
    seconds: float = 0.0
    delta_link: str | None = None
    change_modes: dict[str, int] = field(default_factory=dict)
    first_page_ms: float | None = None

    @property
    def rows_per_second(self) -> float:
        return round(self.rows / self.seconds, 1) if self.seconds else 0.0


def log(msg: str) -> None:
    sys.stdout.write(f"[{datetime.now(UTC):%H:%M:%S}] {msg}\n")
    sys.stdout.flush()


def token_of(link: str | None) -> str | None:
    if not link:
        return None
    values = parse_qs(urlsplit(link).query).get("!deltatoken")
    return values[0].strip("'") if values else None


class Spike:
    def __init__(self, args: argparse.Namespace, password: str) -> None:
        self.args = args
        self.service_url = f"{args.base_url.rstrip('/')}/sap/opu/odata/sap/{args.service}"
        verify: bool | str = args.ca_bundle or not args.insecure
        self.http = httpx.Client(
            auth=(args.user, password),
            params={"sap-client": args.client, "sap-language": args.language}
            if args.client
            else None,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(args.timeout, connect=15),
            verify=verify,
        )
        self.out = Path(args.output) / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.out.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, Any] = {
            "service": args.service,
            "entity_set": args.entity_set,
            "page_size": args.page_size,
            "started_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------ utilidades
    def save(self, name: str, content: str | dict[str, Any]) -> None:
        path = self.out / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")

    def sample(self, body: dict[str, Any]) -> dict[str, Any]:
        """Recorta la respuesta a N registros y enmascara campos sensibles indicados."""
        d = dict(body.get("d", {}))
        rows = d.get("results", [])[: self.args.sample_records]
        masked = set(self.args.mask_fields.split(",")) if self.args.mask_fields else set()
        d["results"] = [{k: ("***" if k in masked else v) for k, v in r.items()} for r in rows]
        return {"d": d}

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        response = self.http.get(url, headers=headers)
        if response.status_code >= 400:
            log(f"  ✖ HTTP {response.status_code}: {response.text[:300]}")
        return response

    # ------------------------------------------------------------------ pasos
    def step_metadata(self) -> None:
        log("1) $metadata")
        r = self.get(f"{self.service_url}/$metadata", {"Accept": "application/xml"})
        r.raise_for_status()
        self.save("metadata.xml", r.text)
        root = ET.fromstring(r.content)
        sets = []
        for es in root.iter(f"{{{EDM_NS['edm']}}}EntitySet"):
            sets.append(
                {
                    "name": es.get("Name"),
                    "change_tracking": es.get(f"{{{EDM_NS['sap']}}}change-tracking"),
                }
            )
        props = [p.get("Name") for p in root.iter(f"{{{EDM_NS['edm']}}}Property")]
        self.results["metadata"] = {
            "entity_sets": sets,
            "property_count": len(props),
            "has_odq_fields": "ODQ_CHANGEMODE" in props,
            "has_delta_links_set": any((s["name"] or "").startswith("DeltaLinksOf") for s in sets),
        }
        log(f"   entity sets: {[s['name'] for s in sets]} · propiedades: {len(props)}")

    def step_full_page(self) -> None:
        log("2) Página full sin suscripción ($top)")
        url = f"{self.service_url}/{self.args.entity_set}?$format=json&$top={self.args.page_size}"
        started = time.perf_counter()
        r = self.get(url)
        elapsed = (time.perf_counter() - started) * 1000
        r.raise_for_status()
        body = r.json()
        self.save("full_first_page.sample.json", self.sample(body))
        rows = len(body.get("d", {}).get("results", []))
        self.results["full_page"] = {"rows": rows, "ms": round(elapsed, 1)}
        log(f"   {rows} filas en {elapsed:.0f} ms")

    def read_all(self, url: str, *, tracking: bool, label: str) -> ReadResult:
        headers = {"Prefer": f"odata.maxpagesize={self.args.page_size}"}
        if tracking:
            headers["Prefer"] = f"odata.track-changes, {headers['Prefer']}"
        result, modes = ReadResult(), Counter[str]()
        started = time.perf_counter()
        while url:
            page_started = time.perf_counter()
            r = self.get(url, headers)
            r.raise_for_status()
            if result.first_page_ms is None:
                result.first_page_ms = round((time.perf_counter() - page_started) * 1000, 1)
            body = r.json()
            d = body.get("d", {})
            rows = d.get("results", [])
            if result.pages == 0:
                self.save(f"{label}_first_page.sample.json", self.sample(body))
            result.pages += 1
            result.rows += len(rows)
            modes.update(str(row.get("ODQ_CHANGEMODE", "?")) for row in rows)
            result.delta_link = d.get("__delta") or result.delta_link
            next_link = d.get("__next")
            url = self._relative(next_link) if next_link else ""
            if result.pages % 20 == 0:
                log(f"   … {result.pages} páginas, {result.rows} filas")
            if self.args.max_pages and result.pages >= self.args.max_pages and url:
                log(
                    f"   ⚠ límite --max-pages={self.args.max_pages}: se corta la lectura (sin token)"
                )
                break
        result.seconds = round(time.perf_counter() - started, 2)
        result.change_modes = dict(modes)
        return result

    def step_init(self) -> str | None:
        log("3) Inicialización de delta (Prefer: odata.track-changes)")
        url = f"{self.service_url}/{self.args.entity_set}?$format=json"
        if self.args.filter:
            url += f"&$filter={self.args.filter}"
        res = self.read_all(url, tracking=True, label="delta_init")
        self.results["delta_init"] = asdict(res) | {"rows_per_second": res.rows_per_second}
        log(
            f"   {res.rows} filas, {res.pages} páginas, {res.seconds}s ({res.rows_per_second} filas/s)"
        )
        log(f"   token T1: {token_of(res.delta_link)}")
        return res.delta_link

    def wait_for_changes(self) -> None:
        log("4) Generar cambios en SAP")
        if self.args.trigger_url:
            r = httpx.post(self.args.trigger_url, json={"inserts": 5, "updates": 3, "deletes": 2})
            log(f"   trigger → HTTP {r.status_code}")
        elif self.args.non_interactive:
            log(f"   esperando {self.args.wait_seconds}s")
            time.sleep(self.args.wait_seconds)
        else:
            input("   Cree/modifique/borre registros en SAP para este origen y pulse Enter… ")

    def step_delta(self, link: str, label: str) -> ReadResult:
        res = self.read_all(self._relative(link), tracking=False, label=label)
        self.results[label] = asdict(res) | {
            "token_used": token_of(link),
            "token_received": token_of(res.delta_link),
        }
        log(f"   {res.rows} filas {res.change_modes} → token {token_of(res.delta_link)}")
        return res

    def step_delta_links(self) -> None:
        log("7) DeltaLinksOf…")
        r = self.get(f"{self.service_url}/DeltaLinksOf{self.args.entity_set}?$format=json")
        if r.status_code < 400:
            links = r.json().get("d", {}).get("results", [])
            self.results["delta_links"] = links
            log(f"   {len(links)} tokens registrados")
        else:
            self.results["delta_links"] = f"HTTP {r.status_code}"

    def _relative(self, link: str) -> str:
        """SAP devuelve el delta link con su host interno: se reescribe al --base-url usado."""
        parts = urlsplit(link)
        return f"{self.args.base_url.rstrip('/')}{parts.path}?{parts.query}"

    # ------------------------------------------------------------------ orquestación
    def run(self) -> int:
        try:
            self.step_metadata()
            self.step_full_page()
            t1 = self.args.delta_link or self.step_init()
            if not t1:
                log("✖ SAP no devolvió __delta: el servicio no soporta change tracking")
                return 2
            self.wait_for_changes()
            log("5) Delta con T1")
            first = self.step_delta(t1, "delta")
            log("6) REPETICIÓN del delta con T1")
            repeated = self.step_delta(t1, "delta_repeat")
            self.results["repeat_analysis"] = {
                "same_row_count": first.rows == repeated.rows,
                "same_token": token_of(first.delta_link) == token_of(repeated.delta_link),
            }
            self.step_delta_links()
            return 0
        except httpx.HTTPError as exc:
            self.results["error"] = f"{type(exc).__name__}: {exc}"
            log(f"✖ {self.results['error']}")
            return 1
        finally:
            self.results["finished_at"] = datetime.now(UTC).isoformat()
            self.save("results.json", self.results)
            self.save("report.md", render_report(self.results))
            log(f"Resultados en {self.out}")


def render_report(r: dict[str, Any]) -> str:
    def g(*keys: str) -> Any:
        node: Any = r
        for k in keys:
            node = node.get(k) if isinstance(node, dict) else None
        return "—" if node is None else node

    return f"""# Resultado del spike SAP · {g("service")} / {g("entity_set")}

| Comprobación | Resultado |
|---|---|
| Entity sets | {", ".join(str(s["name"]) for s in r.get("metadata", {}).get("entity_sets", []))} |
| Campos ODQ (ODQ_CHANGEMODE…) | {g("metadata", "has_odq_fields")} |
| Conjunto DeltaLinksOf… | {g("metadata", "has_delta_links_set")} |
| Página full ({g("page_size")} filas) | {g("full_page", "ms")} ms |
| Init delta: filas / páginas / segundos | {g("delta_init", "rows")} / {g("delta_init", "pages")} / {g("delta_init", "seconds")} |
| Init delta: filas/seg | {g("delta_init", "rows_per_second")} |
| Delta: filas y modos | {g("delta", "rows")} · {g("delta", "change_modes")} |
| Repetición con token anterior: mismas filas | {g("repeat_analysis", "same_row_count")} |
| Repetición: mismo token resultante | {g("repeat_analysis", "same_token")} |
| Error | {g("error")} |

Inicio: {g("started_at")} · Fin: {g("finished_at")}
"""


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
    p.add_argument("--filter", help="$filter para la inicialización (p. ej. RBUKRS eq '1000')")
    p.add_argument("--page-size", type=int, default=5000)
    p.add_argument(
        "--max-pages", type=int, default=0, help="Corta la init tras N páginas (0 = sin límite)"
    )
    p.add_argument("--delta-link", help="Reanudar con un delta link ya obtenido (omite la init)")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--ca-bundle", help="Certificado CA del Gateway (PEM)")
    p.add_argument("--insecure", action="store_true", help="No verificar TLS (solo pruebas)")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--wait-seconds", type=int, default=60)
    p.add_argument("--trigger-url", help="URL POST que genera cambios (solo con el mock)")
    p.add_argument("--sample-records", type=int, default=5)
    p.add_argument("--mask-fields", help="Campos a enmascarar en las muestras, separados por coma")
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
