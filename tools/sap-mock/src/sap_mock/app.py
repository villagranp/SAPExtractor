"""Simulador de SAP Gateway (OData V2) con extracción ODP y deltas.

Imita lo que la plataforma necesita de SAP:

* Catálogo de servicios: ``/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection``
* ``$metadata`` (EDMX V2 con anotaciones ``sap:``)
* Lectura full paginada (``__next`` / ``$skiptoken``, ``Prefer: odata.maxpagesize``)
* Inicialización de delta (``Prefer: odata.track-changes``) → ``__delta`` con token
* Delta (``!deltatoken='…'``), repetición de un token anterior y tokens inválidos
* Escenarios de error (401, 5xx, latencia) y generación de cambios vía ``/_mock/*``

No es un SAP real: sirve para desarrollo, pruebas automáticas y demos.
"""

# Sin "from __future__ import annotations": FastAPI debe resolver anotaciones que usan
# variables locales de create_app (p. ej. el esquema HTTPBasic).
import asyncio
import itertools
import os
import random
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlencode
from xml.sax.saxutils import quoteattr

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from sap_mock.datasets import SERVICES, FieldDef, Row, ServiceDef, ServiceState

GATEWAY = "/sap/opu/odata"
CATALOG = f"{GATEWAY}/iwfnd/CATALOGSERVICE;v=2"
DELTA_PREFIX = "D"


# ------------------------------------------------------------------------------ estado


@dataclass
class Scenario:
    status: int | None = None
    remaining: int = 0
    delay_seconds: float = 0.0


@dataclass
class MockState:
    rows_per_service: int
    page_size: int
    seed: int
    services: dict[str, ServiceState] = field(default_factory=dict)
    snapshots: dict[int, tuple[int, list[Row]]] = field(default_factory=dict)
    scenario: Scenario = field(default_factory=Scenario)
    _snapshot_ids: itertools.count[int] = field(default_factory=itertools.count)

    def reset(self) -> None:
        self.services.clear()
        self.snapshots.clear()
        self.scenario = Scenario()
        for i, definition in enumerate(SERVICES):
            state = ServiceState(definition, random.Random(self.seed + i))
            state.seed(self.rows_per_service)
            self.services[definition.technical_name] = state

    def new_snapshot(self, seq: int, rows: list[Row]) -> int:
        snap_id = next(self._snapshot_ids)
        self.snapshots[snap_id] = (seq, rows)
        return snap_id


# ------------------------------------------------------------------------------ errores SAP


class SapError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _sap_error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": {"lang": "es", "value": message},
                "innererror": {"transactionid": secrets.token_hex(16).upper()},
            }
        },
    )


# ------------------------------------------------------------------------------ codificación


def _epoch_ms(value: datetime | date) -> int:
    if not isinstance(value, datetime):
        value = datetime.combine(value, time(), tzinfo=UTC)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def encode_value(value: Any, f: FieldDef) -> Any:
    if value is None:
        return None
    if f.edm_type == "Edm.DateTime":
        return f"/Date({_epoch_ms(value)})/"
    if f.edm_type == "Edm.DateTimeOffset":
        return f"/Date({_epoch_ms(value)}+0000)/"
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _key_predicate(d: ServiceDef, row: Row) -> str:
    return ",".join(f"{k}='{row[k]}'" for k in d.keys)


def encode_record(
    base: str, d: ServiceDef, row: Row, *, change_mode: str = "", counter: int = 1
) -> dict[str, Any]:
    uri = f"{base}/{d.entity_set}({_key_predicate(d, row)})"
    record: dict[str, Any] = {
        "__metadata": {"id": uri, "uri": uri, "type": f"{d.technical_name}.{d.entity_type}"}
    }
    for f in d.fields:
        record[f.name] = encode_value(row.get(f.name), f)
    if d.supports_delta:
        record["ODQ_CHANGEMODE"] = change_mode
        record["ODQ_ENTITYCNTR"] = str(counter)
    return record


# ------------------------------------------------------------------------------ $metadata


def build_metadata(d: ServiceDef) -> str:
    def prop(f: FieldDef) -> str:
        attrs = [f"Name={quoteattr(f.name)}", f"Type={quoteattr(f.edm_type)}"]
        if f.key:
            attrs.append('Nullable="false"')
        if f.max_length:
            attrs.append(f'MaxLength="{f.max_length}"')
        if f.precision is not None:
            attrs.append(f'Precision="{f.precision}"')
        if f.scale is not None:
            attrs.append(f'Scale="{f.scale}"')
        attrs.append(f"sap:label={quoteattr(f.label)}")
        if f.display_format:
            attrs.append(f"sap:display-format={quoteattr(f.display_format)}")
        if not f.filterable:
            attrs.append('sap:filterable="false"')
        return f"<Property {' '.join(attrs)}/>"

    keys = "".join(f"<PropertyRef Name={quoteattr(k)}/>" for k in d.keys)
    props = "".join(prop(f) for f in d.all_fields)
    ns = d.technical_name
    tracking = ' sap:change-tracking="true"' if d.supports_delta else ""
    delta_type = delta_set = ""
    if d.supports_delta:
        delta_type = (
            f'<EntityType Name="DeltaLinksOf{d.entity_set}Type">'
            '<Key><PropertyRef Name="DeltaToken"/></Key>'
            '<Property Name="DeltaToken" Type="Edm.String" Nullable="false" MaxLength="32"/>'
            '<Property Name="CreatedAt" Type="Edm.DateTimeOffset"/>'
            '<Property Name="IsInitialLoad" Type="Edm.Boolean"/>'
            "</EntityType>"
        )
        delta_set = (
            f'<EntitySet Name="DeltaLinksOf{d.entity_set}" '
            f'EntityType="{ns}.DeltaLinksOf{d.entity_set}Type" sap:creatable="false" '
            'sap:updatable="false" sap:deletable="false"/>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" '
        'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
        'xmlns:sap="http://www.sap.com/Protocols/SAPData">'
        '<edmx:DataServices m:DataServiceVersion="2.0">'
        f'<Schema Namespace="{ns}" xml:lang="es" sap:schema-version="1" '
        'xmlns="http://schemas.microsoft.com/ado/2008/09/edm">'
        f'<EntityType Name="{d.entity_type}" sap:content-version="1">'
        f"<Key>{keys}</Key>{props}</EntityType>{delta_type}"
        f'<EntityContainer Name="{ns}_Entities" m:IsDefaultEntityContainer="true">'
        f'<EntitySet Name="{d.entity_set}" EntityType="{ns}.{d.entity_type}" '
        'sap:creatable="false" sap:updatable="false" sap:deletable="false" '
        f'sap:pageable="true" sap:content-version="1"{tracking}/>{delta_set}'
        "</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>"
    )


# ------------------------------------------------------------------------------ $filter


_CONDITION = re.compile(
    r"^\s*(?P<field>\w+)\s+(?P<op>eq|ne|ge|gt|le|lt)\s+"
    r"(?:datetime(?:offset)?'(?P<dt>[^']+)'|'(?P<str>[^']*)'|(?P<num>-?\d+(?:\.\d+)?))\s*$"
)


def _parse_filter(expr: str, d: ServiceDef) -> list[tuple[str, str, Any]]:
    fields = {f.name: f for f in d.fields}
    conditions: list[tuple[str, str, Any]] = []
    for part in re.split(r"\s+and\s+", expr.strip()):
        m = _CONDITION.match(part)
        if not m or m["field"] not in fields:
            raise SapError(400, "/IWBEP/CM_MGW_RT/022", f"Expresión $filter no soportada: {part}")
        f = fields[m["field"]]
        value: Any
        if m["dt"] is not None:
            parsed = datetime.fromisoformat(m["dt"])
            value = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        elif m["num"] is not None:
            value = Decimal(m["num"])
        else:
            value = m["str"]
        conditions.append((f.name, m["op"], value))
    return conditions


def _comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time(), tzinfo=UTC)
    return value


def _matches(row: Row, conditions: list[tuple[str, str, Any]]) -> bool:
    ops = {
        "eq": lambda a, b: a == b,
        "ne": lambda a, b: a != b,
        "ge": lambda a, b: a >= b,
        "gt": lambda a, b: a > b,
        "le": lambda a, b: a <= b,
        "lt": lambda a, b: a < b,
    }
    return all(
        ops[op](_comparable(row.get(name)), _comparable(value)) for name, op, value in conditions
    )


# ------------------------------------------------------------------------------ app


class ChangeRequest(BaseModel):
    inserts: int = Field(0, ge=0, le=1_000_000)
    updates: int = Field(0, ge=0, le=1_000_000)
    deletes: int = Field(0, ge=0, le=1_000_000)


class ScenarioRequest(BaseModel):
    status: int | None = Field(None, ge=400, le=599, description="Código HTTP a devolver")
    times: int = Field(1, ge=0, le=1000, description="Nº de peticiones afectadas")
    delay_seconds: float = Field(0.0, ge=0, le=120)


def create_app(
    *,
    user: str | None = None,
    password: str | None = None,
    rows_per_service: int | None = None,
    page_size: int | None = None,
    seed: int | None = None,
) -> FastAPI:
    expected_user: str = user or os.getenv("MOCK_USER") or "sxmock"
    expected_password: str = password or os.getenv("MOCK_PASSWORD") or "sxmock"
    state = MockState(
        rows_per_service=rows_per_service or int(os.getenv("MOCK_ROWS", "2500")),
        page_size=page_size or int(os.getenv("MOCK_PAGE_SIZE", "1000")),
        seed=seed if seed is not None else int(os.getenv("MOCK_SEED", "42")),
    )
    state.reset()

    app = FastAPI(
        title="SAP Gateway mock (SAPExtract)",
        docs_url="/_mock/docs",
        openapi_url="/_mock/openapi.json",
    )
    app.state.mock = state
    basic = HTTPBasic(auto_error=False)

    @app.exception_handler(SapError)
    async def _sap_error(_: Request, exc: SapError) -> JSONResponse:
        return _sap_error_response(exc.status, exc.code, exc.message)

    async def sap_guard(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    ) -> None:
        sc = state.scenario
        if sc.delay_seconds:
            await asyncio.sleep(sc.delay_seconds)
        if sc.remaining > 0 and sc.status:
            sc.remaining -= 1
            raise SapError(sc.status, "/IWFND/CM_BEC/026", f"Error simulado {sc.status}")
        ok = credentials is not None and (
            secrets.compare_digest(credentials.username, expected_user)
            & secrets.compare_digest(credentials.password, expected_password)
        )
        if not ok:
            raise HTTPException(
                401, "Logon failed", headers={"WWW-Authenticate": 'Basic realm="SAP"'}
            )

    Guard = Depends(sap_guard)

    def _service(name: str) -> ServiceState:
        svc = state.services.get(name)
        if svc is None:
            raise SapError(404, "/IWFND/MED/170", f"No service found for namespace , name {name}")
        return svc

    def _service_base(request: Request, name: str) -> str:
        return f"{str(request.base_url).rstrip('/')}{GATEWAY}/sap/{name}"

    # -------------------------------------------------------------- catálogo
    @app.get(f"{CATALOG}/", dependencies=[Guard])
    def catalog_root() -> dict[str, Any]:
        return {"d": {"EntitySets": ["ServiceCollection"]}}

    @app.get(f"{CATALOG}/ServiceCollection", dependencies=[Guard])
    def catalog(request: Request) -> dict[str, Any]:
        root = str(request.base_url).rstrip("/")
        results = []
        for name, svc in state.services.items():
            d = svc.definition
            url = f"{root}{GATEWAY}/sap/{name}"
            results.append(
                {
                    "__metadata": {"uri": f"{root}{CATALOG}/ServiceCollection('{name}_0001')"},
                    "ID": f"{name}_0001",
                    "Title": name,
                    "Description": d.description,
                    "TechnicalServiceName": name,
                    "TechnicalServiceVersion": 1,
                    "ServiceUrl": f"{url}/",
                    "MetadataUrl": f"{url}/$metadata",
                    "IsSapService": False,
                }
            )
        return {"d": {"results": results}}

    # -------------------------------------------------------------- servicio
    @app.get(f"{GATEWAY}/sap/{{service}}/", dependencies=[Guard])
    def service_document(service: str) -> dict[str, Any]:
        d = _service(service).definition
        sets = [d.entity_set] + ([f"DeltaLinksOf{d.entity_set}"] if d.supports_delta else [])
        return {"d": {"EntitySets": sets}}

    @app.get(f"{GATEWAY}/sap/{{service}}/$metadata", dependencies=[Guard])
    def metadata(service: str) -> Response:
        xml = build_metadata(_service(service).definition)
        return Response(xml, media_type="application/xml")

    @app.get(f"{GATEWAY}/sap/{{service}}/{{entity_set}}/$count", dependencies=[Guard])
    def count(service: str, entity_set: str, request: Request) -> PlainTextResponse:
        svc = _service(service)
        if entity_set != svc.definition.entity_set:
            raise SapError(
                404, "/IWBEP/CM_MGW_RT/021", f"Resource not found for segment '{entity_set}'"
            )
        rows = list(svc.rows.values())
        if expr := request.query_params.get("$filter"):
            conditions = _parse_filter(expr, svc.definition)
            rows = [r for r in rows if _matches(r, conditions)]
        return PlainTextResponse(str(len(rows)))

    @app.get(f"{GATEWAY}/sap/{{service}}/{{entity_set}}", dependencies=[Guard])
    def read(service: str, entity_set: str, request: Request) -> JSONResponse:
        svc = _service(service)
        d = svc.definition
        base = _service_base(request, service)
        if entity_set == f"DeltaLinksOf{d.entity_set}" and d.supports_delta:
            return JSONResponse({"d": {"results": svc.issued_tokens}})
        if entity_set != d.entity_set:
            raise SapError(
                404, "/IWBEP/CM_MGW_RT/021", f"Resource not found for segment '{entity_set}'"
            )

        q = request.query_params
        page_size = _page_size(request, state.page_size)
        delta_token = q.get("!deltatoken")
        skiptoken = q.get("$skiptoken")
        tracking = "odata.track-changes" in request.headers.get("prefer", "")

        if delta_token is not None or (skiptoken or "").startswith("C"):
            return _read_delta(svc, base, page_size, delta_token, skiptoken)
        if tracking or (skiptoken or "").startswith("I"):
            if not d.supports_delta:
                raise SapError(
                    400, "/IWBEP/CM_MGW_RT/030", "El servicio no soporta change tracking"
                )
            return _read_init(svc, base, page_size, skiptoken, q.get("$filter"))
        return _read_full(svc, base, page_size, request)

    # -------------------------------------------------------------- lecturas
    def _read_full(svc: ServiceState, base: str, page_size: int, request: Request) -> JSONResponse:
        d = svc.definition
        q = request.query_params
        rows = list(svc.rows.values())
        if expr := q.get("$filter"):
            conditions = _parse_filter(expr, d)
            rows = [r for r in rows if _matches(r, conditions)]
        if orderby := q.get("$orderby"):
            fld, _, direction = orderby.partition(" ")
            rows.sort(key=lambda r: _comparable(r.get(fld)), reverse=direction.strip() == "desc")
        top = int(q["$top"]) if "$top" in q else None
        offset = int(q.get("$skiptoken") or q.get("$skip") or 0)
        limit = len(rows) if top is None else min(len(rows), top)
        end = min(offset + page_size, limit)
        results = [encode_record(base, d, r) for r in rows[offset:end]]
        body: dict[str, Any] = {"results": results}
        if end < limit:
            params = {k: v for k, v in q.items() if k not in ("$skiptoken", "$skip")}
            params["$skiptoken"] = str(end)
            body["__next"] = f"{base}/{d.entity_set}?{urlencode(params)}"
        return JSONResponse({"d": body})

    def _read_init(
        svc: ServiceState, base: str, page_size: int, skiptoken: str | None, expr: str | None
    ) -> JSONResponse:
        d = svc.definition
        if skiptoken:
            snap_id, offset = (int(x) for x in skiptoken[1:].split("-"))
            if snap_id not in state.snapshots:
                raise SapError(400, "/IWBEP/CM_MGW_RT/031", "Skiptoken caducado")
            seq, rows = state.snapshots[snap_id]
        else:
            rows = list(svc.rows.values())
            if expr:
                conditions = _parse_filter(expr, d)
                rows = [r for r in rows if _matches(r, conditions)]
            seq = svc.seq
            snap_id, offset = state.new_snapshot(seq, rows), 0
        end = min(offset + page_size, len(rows))
        body: dict[str, Any] = {"results": [encode_record(base, d, r) for r in rows[offset:end]]}
        if end < len(rows):
            body["__next"] = f"{base}/{d.entity_set}?$skiptoken=I{snap_id}-{end}"
        else:
            state.snapshots.pop(snap_id, None)
            body["__delta"] = _issue_token(svc, base, seq, initial=True)
        return JSONResponse({"d": body}, headers={"Preference-Applied": "odata.track-changes"})

    def _read_delta(
        svc: ServiceState, base: str, page_size: int, delta_token: str | None, skiptoken: str | None
    ) -> JSONResponse:
        d = svc.definition
        if skiptoken:
            after, up_to, offset = (int(x) for x in skiptoken[1:].split("-"))
        else:
            after, up_to, offset = _parse_token(svc, delta_token or ""), svc.seq, 0
        changes = svc.changes_between(after, up_to)
        end = min(offset + page_size, len(changes))
        results = [
            encode_record(base, d, c.row, change_mode=c.mode, counter=c.counter)
            for c in changes[offset:end]
        ]
        body: dict[str, Any] = {"results": results}
        if end < len(changes):
            body["__next"] = f"{base}/{d.entity_set}?$skiptoken=C{after}-{up_to}-{end}"
        else:
            body["__delta"] = _issue_token(svc, base, up_to, initial=False)
        return JSONResponse({"d": body})

    def _issue_token(svc: ServiceState, base: str, seq: int, *, initial: bool) -> str:
        token = f"{DELTA_PREFIX}{seq:012d}"
        if not any(t["DeltaToken"] == token for t in svc.issued_tokens):
            svc.issued_tokens.append(
                {
                    "DeltaToken": token,
                    "CreatedAt": f"/Date({_epoch_ms(datetime.now(UTC))}+0000)/",
                    "IsInitialLoad": initial,
                }
            )
        return f"{base}/{svc.definition.entity_set}?!deltatoken='{token}'"

    def _parse_token(svc: ServiceState, raw: str) -> int:
        token = raw.strip("'")
        if not re.fullmatch(rf"{DELTA_PREFIX}\d{{12}}", token):
            raise SapError(400, "/IWBEP/CM_MGW_RT/032", f"Delta token '{token}' no válido")
        seq = int(token[1:])
        issued = {t["DeltaToken"] for t in svc.issued_tokens}
        if token not in issued or seq <= svc.invalidated_before or seq > svc.seq:
            raise SapError(
                404, "ODQ/032", f"Delta token '{token}' desconocido o suscripción eliminada"
            )
        return seq

    # -------------------------------------------------------------- administración del mock
    @app.get("/_mock/health")
    def mock_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/_mock/services")
    def mock_services() -> list[dict[str, Any]]:
        return [
            {
                "service": name,
                "kind": svc.definition.kind.value,
                "odp_name": svc.definition.odp_name,
                "entity_set": svc.definition.entity_set,
                "rows": len(svc.rows),
                "change_seq": svc.seq,
                "tokens": [t["DeltaToken"] for t in svc.issued_tokens],
            }
            for name, svc in state.services.items()
        ]

    @app.post("/_mock/services/{service}/changes")
    def mock_changes(service: str, body: ChangeRequest) -> dict[str, int]:
        svc = _service(service)
        seq = svc.apply_changes(body.inserts, body.updates, body.deletes)
        return {"change_seq": seq, "rows": len(svc.rows)}

    @app.post("/_mock/services/{service}/invalidate-tokens")
    def mock_invalidate(service: str) -> dict[str, int]:
        """Simula borrar la suscripción en ODQMON: los tokens emitidos dejan de valer."""
        svc = _service(service)
        svc.invalidated_before = svc.seq
        return {"invalidated_up_to": svc.seq}

    @app.post("/_mock/scenario")
    def mock_scenario(body: ScenarioRequest) -> dict[str, Any]:
        state.scenario = Scenario(body.status, body.times if body.status else 0, body.delay_seconds)
        return {"status": body.status, "remaining": state.scenario.remaining}

    @app.post("/_mock/reset")
    def mock_reset() -> dict[str, str]:
        state.reset()
        return {"status": "reset"}

    return app


def _page_size(request: Request, default: int) -> int:
    m = re.search(r"odata\.maxpagesize=(\d+)", request.headers.get("prefer", ""))
    return max(1, min(int(m.group(1)), 100_000)) if m else default


def main() -> None:
    import uvicorn

    uvicorn.run(
        "sap_mock.app:create_app",
        factory=True,
        host=os.getenv("MOCK_HOST", "0.0.0.0"),  # noqa: S104 - servicio de desarrollo en contenedor
        port=int(os.getenv("MOCK_PORT", "8081")),
    )


if __name__ == "__main__":
    main()
