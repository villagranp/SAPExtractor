"""Casos de uso de la auditoría: registrar, consultar, exportar y verificar la integridad."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import ColumnElement, Select, func, select, text
from sqlalchemy.orm import Session

from sapextract.audit.domain.integrity import GENESIS_HASH, compute_hash, to_json_compatible
from sapextract.audit.domain.model import Actor, AuditEntry, Outcome, RequestInfo, category_of
from sapextract.audit.infrastructure.models import AUDIT_SEQUENCE, AuditEventRow
from sapextract.core.clock import utcnow
from sapextract.core.db import Database, new_id
from sapextract.core.exceptions import NotFoundError
from sapextract.core.masking import mask_mapping
from sapextract.core.pagination import PageParams
from sapextract.core.system_settings import SystemSetting

log = structlog.get_logger(__name__)

# Bloqueo consultivo que serializa la escritura de la cadena (dura hasta el commit).
AUDIT_CHAIN_LOCK = 7_215_002
LAST_VERIFICATION_KEY = "audit.last_verification"
CHAIN_ANCHOR_KEY = "audit.chain_anchor"


@dataclass(frozen=True, kw_only=True)
class AuditFilters:
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    actor_id: str | None = None
    actor: str | None = None
    action: str | None = None
    category: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True, kw_only=True)
class VerificationResult:
    ok: bool
    checked: int
    verified_at: datetime
    broken_seq: int | None = None
    broken_id: str | None = None
    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_setting(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "verified_at": self.verified_at.isoformat(),
            "broken_seq": self.broken_seq,
            "broken_id": self.broken_id,
            "detail": self.detail,
        }


class AuditService:
    """Punto único de escritura y lectura de la auditoría."""

    # ------------------------------------------------------------------ escritura
    def record(
        self,
        session: Session,
        entry: AuditEntry,
        *,
        actor: Actor,
        request: RequestInfo | None = None,
    ) -> AuditEventRow:
        """Añade un evento en la transacción de ``session`` (se confirma con el cambio auditado)."""
        category = category_of(entry.action)
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": AUDIT_CHAIN_LOCK})
        prev_hash = session.scalar(
            select(AuditEventRow.hash).order_by(AuditEventRow.seq.desc()).limit(1)
        ) or self._anchor(session)
        seq = session.scalar(select(AUDIT_SEQUENCE.next_value()))
        if seq is None:  # pragma: no cover - nextval siempre devuelve valor
            msg = "No se pudo obtener la secuencia de auditoría"
            raise RuntimeError(msg)

        context = {**(request.as_context() if request else {}), **entry.details}
        row = AuditEventRow(
            seq=seq,
            id=new_id(),
            occurred_at=utcnow(),
            actor_type=actor.type.value,
            actor_id=actor.id,
            actor_name=actor.name[:200],
            action=entry.action,
            category=category.value,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            resource_name=entry.resource_name[:200] if entry.resource_name else None,
            outcome=entry.outcome.value,
            changes=to_json_compatible(mask_mapping(entry.changes)) if entry.changes else None,
            context=to_json_compatible(mask_mapping(context)),
            reason=entry.reason,
            prev_hash=prev_hash,
            hash="",
        )
        row.hash = compute_hash(prev_hash, row.hashed_content())
        session.add(row)
        session.flush()
        return row

    def record_detached(
        self,
        db: Database,
        entry: AuditEntry,
        *,
        actor: Actor,
        request: RequestInfo | None = None,
    ) -> None:
        """Registra en una transacción propia (intentos fallidos o denegados).

        Nunca propaga errores: la respuesta al usuario no debe depender de esta escritura.
        """
        try:
            with db.session() as session:
                self.record(session, entry, actor=actor, request=request)
                session.commit()
        except Exception:
            log.exception("audit_record_failed", action=entry.action)

    # ------------------------------------------------------------------ lectura
    def _filtered(self, filters: AuditFilters) -> Select[AuditEventRow]:
        conditions: list[ColumnElement[bool]] = []
        if filters.occurred_from:
            conditions.append(AuditEventRow.occurred_at >= filters.occurred_from)
        if filters.occurred_to:
            conditions.append(AuditEventRow.occurred_at < filters.occurred_to)
        if filters.actor_id:
            conditions.append(AuditEventRow.actor_id == filters.actor_id)
        if filters.actor:
            conditions.append(AuditEventRow.actor_name.ilike(f"%{_escape_like(filters.actor)}%"))
        if filters.action:
            if filters.action.endswith((".", "*")):
                prefix = _escape_like(filters.action.rstrip("*"))
                conditions.append(AuditEventRow.action.like(f"{prefix}%"))
            else:
                conditions.append(AuditEventRow.action == filters.action)
        if filters.category:
            conditions.append(AuditEventRow.category == filters.category)
        if filters.resource_type:
            conditions.append(AuditEventRow.resource_type == filters.resource_type)
        if filters.resource_id:
            conditions.append(AuditEventRow.resource_id == filters.resource_id)
        if filters.outcome:
            conditions.append(AuditEventRow.outcome == filters.outcome)
        return select(AuditEventRow).where(*conditions)

    def count(self, session: Session, filters: AuditFilters) -> int:
        stmt = self._filtered(filters).with_only_columns(func.count()).order_by(None)
        return session.scalar(stmt) or 0

    def search(
        self, session: Session, filters: AuditFilters, page: PageParams
    ) -> tuple[list[AuditEventRow], int]:
        total = self.count(session, filters)
        order = AuditEventRow.seq.asc() if page.sort == "occurred_at" else AuditEventRow.seq.desc()
        rows = session.scalars(
            self._filtered(filters).order_by(order).offset(page.offset).limit(page.page_size)
        ).all()
        return list(rows), total

    def get(self, session: Session, event_id: uuid.UUID) -> AuditEventRow:
        row = session.scalar(select(AuditEventRow).where(AuditEventRow.id == event_id))
        if row is None:
            msg = "El evento de auditoría no existe."
            raise NotFoundError(msg)
        return row

    def iter_rows(self, session: Session, filters: AuditFilters) -> Iterator[AuditEventRow]:
        stmt = self._filtered(filters).order_by(AuditEventRow.seq.asc())
        yield from session.scalars(stmt.execution_options(yield_per=1000))

    # ------------------------------------------------------------------ integridad
    def _anchor(self, session: Session) -> str:
        """Hash previo del primer evento conservado (cambia solo al depurar por retención)."""
        anchor = session.get(SystemSetting, CHAIN_ANCHOR_KEY)
        return str(anchor.value) if anchor is not None else GENESIS_HASH

    def verify(self, session: Session) -> VerificationResult:
        """Recalcula toda la cadena. Se detiene en el primer evento manipulado."""
        prev_hash = self._anchor(session)
        checked = 0
        stmt = select(AuditEventRow).order_by(AuditEventRow.seq.asc())
        for row in session.scalars(stmt.execution_options(yield_per=1000)):
            expected = compute_hash(prev_hash, row.hashed_content())
            if row.prev_hash != prev_hash or row.hash != expected:
                detail = (
                    "El enlace con el evento anterior no coincide (evento borrado o reordenado)."
                    if row.prev_hash != prev_hash
                    else "El contenido del evento no coincide con su hash (evento modificado)."
                )
                return VerificationResult(
                    ok=False,
                    checked=checked,
                    verified_at=utcnow(),
                    broken_seq=row.seq,
                    broken_id=str(row.id),
                    detail=detail,
                )
            prev_hash = row.hash
            checked += 1
        return VerificationResult(ok=True, checked=checked, verified_at=utcnow())

    def save_verification(self, session: Session, result: VerificationResult) -> None:
        setting = session.get(SystemSetting, LAST_VERIFICATION_KEY)
        if setting is None:
            session.add(
                SystemSetting(
                    key=LAST_VERIFICATION_KEY,
                    value=result.to_setting(),
                    description="Resultado de la última verificación de integridad de auditoría",
                )
            )
        else:
            setting.value = result.to_setting()

    def last_verification(self, session: Session) -> dict[str, Any] | None:
        setting = session.get(SystemSetting, LAST_VERIFICATION_KEY)
        return dict(setting.value) if setting is not None else None

    def verify_and_record(
        self, session: Session, *, actor: Actor, request: RequestInfo | None = None
    ) -> VerificationResult:
        result = self.verify(session)
        self.save_verification(session, result)
        self.record(
            session,
            AuditEntry(
                action="audit.verified" if result.ok else "audit.verification_failed",
                outcome=Outcome.SUCCESS if result.ok else Outcome.FAILURE,
                details={
                    "checked": result.checked,
                    "broken_seq": result.broken_seq,
                    "broken_id": result.broken_id,
                },
            ),
            actor=actor,
            request=request,
        )
        session.commit()
        if not result.ok:
            log.error("audit_chain_broken", broken_seq=result.broken_seq)
        return result


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
