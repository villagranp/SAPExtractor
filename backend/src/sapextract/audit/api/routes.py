"""Consulta, exportación y verificación de la auditoría (plan 03 §1)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sapextract.audit.api.openapi import audited
from sapextract.audit.application.service import AuditFilters, AuditService
from sapextract.audit.domain.model import ACTIONS, AuditEntry, Category, Outcome
from sapextract.audit.infrastructure.models import AuditEventRow
from sapextract.core.clock import utcnow
from sapextract.core.dependencies import DatabaseDep, SessionDep
from sapextract.core.exceptions import BusinessRuleError
from sapextract.core.pagination import Page, PageParams, page_params
from sapextract.identity.api.deps import RequestInfoDep, ToolkitDep, require_permission
from sapextract.identity.domain.model import Principal

router = APIRouter(prefix="/audit-events", tags=["Auditoría"])

AuditReader = Annotated[Principal, Depends(require_permission("audit:read"))]
AuditExporter = Annotated[Principal, Depends(require_permission("audit:read", "audit:export"))]
Paging = Annotated[PageParams, Depends(page_params)]

MAX_EXPORT_ROWS = 100_000


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    actor_name: str
    action: str
    category: Category
    resource_type: str | None
    resource_id: str | None
    resource_name: str | None
    outcome: Outcome
    changes: dict[str, Any] | None
    context: dict[str, Any]
    reason: str | None
    hash: str
    prev_hash: str

    @classmethod
    def of(cls, row: AuditEventRow) -> AuditEventResponse:
        return cls(
            id=row.id,
            seq=row.seq,
            occurred_at=row.occurred_at,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            actor_name=row.actor_name,
            action=row.action,
            category=Category(row.category),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            resource_name=row.resource_name,
            outcome=Outcome(row.outcome),
            changes=row.changes,
            context=row.context,
            reason=row.reason,
            hash=row.hash,
            prev_hash=row.prev_hash,
        )


class AuditActionInfo(BaseModel):
    action: str
    category: Category


class AuditVerificationResponse(BaseModel):
    ok: bool
    checked: int
    verified_at: datetime
    broken_seq: int | None = None
    broken_id: str | None = None
    detail: str | None = None


class AuditIntegrityResponse(BaseModel):
    last_verification: AuditVerificationResponse | None


class AuditFilterModel(BaseModel):
    occurred_from: datetime | None = Field(None, alias="from")
    occurred_to: datetime | None = Field(None, alias="to")
    actor_id: str | None = Field(None, max_length=64)
    actor: str | None = Field(None, max_length=200, description="Texto en el nombre del actor")
    action: str | None = Field(
        None, max_length=100, description="Acción exacta o prefijo terminado en '.' o '*'"
    )
    category: Category | None = None
    resource_type: str | None = Field(None, max_length=50)
    resource_id: str | None = Field(None, max_length=100)
    outcome: Outcome | None = None

    model_config = {"populate_by_name": True}

    def to_filters(self) -> AuditFilters:
        return AuditFilters(
            occurred_from=self.occurred_from,
            occurred_to=self.occurred_to,
            actor_id=self.actor_id,
            actor=self.actor,
            action=self.action,
            category=self.category.value if self.category else None,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            outcome=self.outcome.value if self.outcome else None,
        )


class AuditExportRequest(AuditFilterModel):
    format: Literal["csv", "json"] = "csv"


def _service(toolkit: ToolkitDep) -> AuditService:
    return toolkit.audit


AuditServiceDep = Annotated[AuditService, Depends(_service)]
FilterQuery = Annotated[AuditFilterModel, Query()]


@router.get("", response_model=Page[AuditEventResponse], summary="Eventos de auditoría")
def search_events(
    _: AuditReader,
    session: SessionDep,
    audit: AuditServiceDep,
    page: Paging,
    filters: FilterQuery,
) -> Page[AuditEventResponse]:
    rows, total = audit.search(session, filters.to_filters(), page)
    return Page[AuditEventResponse].of([AuditEventResponse.of(r) for r in rows], total, page)


@router.get(
    "/catalog", response_model=list[AuditActionInfo], summary="Catálogo de acciones auditadas"
)
def action_catalog(_: AuditReader) -> list[AuditActionInfo]:
    return [AuditActionInfo(action=a, category=c) for a, c in sorted(ACTIONS.items())]


@router.get(
    "/integrity",
    response_model=AuditIntegrityResponse,
    summary="Resultado de la última verificación de integridad",
)
def integrity_status(
    _: AuditReader, session: SessionDep, audit: AuditServiceDep
) -> AuditIntegrityResponse:
    last = audit.last_verification(session)
    return AuditIntegrityResponse(
        last_verification=AuditVerificationResponse.model_validate(last) if last else None
    )


@router.post(
    "/verify",
    response_model=AuditVerificationResponse,
    summary="Recalcula la cadena de hash completa",
    openapi_extra=audited("audit.verified", "audit.verification_failed"),
)
def verify_chain(
    actor: AuditExporter, session: SessionDep, audit: AuditServiceDep, info: RequestInfoDep
) -> AuditVerificationResponse:
    result = audit.verify_and_record(session, actor=actor.actor, request=info)
    return AuditVerificationResponse.model_validate(result.to_setting())


_CSV_COLUMNS = (
    "occurred_at",
    "seq",
    "actor_type",
    "actor_name",
    "action",
    "category",
    "outcome",
    "resource_type",
    "resource_id",
    "resource_name",
    "reason",
    "changes",
    "context",
    "id",
    "hash",
)


def _csv_safe(value: object) -> str:
    """Evita la inyección de fórmulas al abrir el CSV en Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_rows(rows: Iterator[AuditEventRow]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    buffer.write("﻿")  # BOM: Excel abre el UTF-8 correctamente
    writer.writerow(_CSV_COLUMNS)
    for row in rows:
        data = AuditEventResponse.of(row).model_dump(mode="json")
        writer.writerow(
            [
                _csv_safe(
                    json.dumps(data[c], ensure_ascii=False)
                    if c in ("changes", "context")
                    else data[c]
                )
                for c in _CSV_COLUMNS
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
    yield buffer.getvalue()


def _json_rows(rows: Iterator[AuditEventRow]) -> Iterator[str]:
    yield "["
    first = True
    for row in rows:
        yield ("" if first else ",") + AuditEventResponse.of(row).model_dump_json()
        first = False
    yield "]"


@router.post(
    "/export",
    summary="Exporta los eventos filtrados (CSV o JSON)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}, "application/json": {}}}},
    openapi_extra=audited("audit.exported"),
)
def export_events(
    body: AuditExportRequest,
    actor: AuditExporter,
    db: DatabaseDep,
    session: SessionDep,
    audit: AuditServiceDep,
    info: RequestInfoDep,
) -> StreamingResponse:
    filters = body.to_filters()
    total = audit.count(session, filters)
    if total > MAX_EXPORT_ROWS:
        msg = (
            f"La exportación tiene {total} eventos; el máximo es {MAX_EXPORT_ROWS}. "
            "Acote el rango de fechas u otros filtros."
        )
        raise BusinessRuleError(msg, code="SX-AUD-EXPORT-TOO-LARGE")
    audit.record(
        session,
        AuditEntry(
            action="audit.exported",
            details={"format": body.format, "rows": total, "filters": _filters_for_log(filters)},
        ),
        actor=actor.actor,
        request=info,
    )
    session.commit()

    def stream() -> Iterator[str]:
        # Sesión propia: la de la petición se cierra al terminar el endpoint.
        with db.session() as export_session:
            rows = audit.iter_rows(export_session, filters)
            yield from (_csv_rows(rows) if body.format == "csv" else _json_rows(rows))

    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    media = "text/csv; charset=utf-8" if body.format == "csv" else "application/json"
    return StreamingResponse(
        stream(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="auditoria-{stamp}.{body.format}"'},
    )


@router.get("/{event_id}", response_model=AuditEventResponse, summary="Detalle de un evento")
def get_event(
    event_id: uuid.UUID, _: AuditReader, session: SessionDep, audit: AuditServiceDep
) -> AuditEventResponse:
    return AuditEventResponse.of(audit.get(session, event_id))


def _filters_for_log(filters: AuditFilters) -> dict[str, Any]:
    return {
        k: v.isoformat() if isinstance(v, datetime) else v for k, v in filters.as_dict().items()
    }
