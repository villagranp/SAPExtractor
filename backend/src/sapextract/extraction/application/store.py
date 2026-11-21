"""Escrituras del motor sobre el run y el estado de delta, con *fencing* (plan 06 §3.3 y §3.6).

La orquestación toma el run y cierra el intento; el motor fija la identidad del lote, guarda el
token pendiente y confirma (``commit_delta``). Cada escritura lleva ``lease_epoch``: si no afecta
a ninguna fila, otro worker tiene el run y este se detiene (``FencedError``). Cada método usa su
propia transacción corta: no hay transacciones abiertas mientras se habla con SAP o el destino.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.model import Actor, AuditEntry, Outcome
from sapextract.core.clock import utcnow
from sapextract.core.db import Database
from sapextract.dataflow.domain.batch import RunMode, batch_id
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.extraction.domain.model import (
    INIT_FAILURE_LEAVES_SUBSCRIPTION,
    DeltaEvent,
    DeltaSnapshot,
    DeltaStatus,
    StepName,
    StepStatus,
    ensure_runnable,
)
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    RunLog,
    RunStep,
)
from sapextract.orchestration.domain.model import RunStatus
from sapextract.orchestration.domain.ports import RunContext
from sapextract.orchestration.infrastructure.models import Pipeline, Run, RunAttempt

DELTA_RUN_MODES = frozenset({RunMode.DELTA_INIT, RunMode.DELTA})
WORKER = Actor.system("worker")


class FencedError(Exception):
    """El intento perdió el derecho a escribir: otro worker tiene el run."""


def _rowcount(result: Any) -> int:
    return cast("CursorResult[Any]", result).rowcount


@dataclass(frozen=True, kw_only=True)
class Claim:
    """Run en manos del motor, con la identidad del lote si ya está fijada."""

    run_id: uuid.UUID
    pipeline_id: uuid.UUID
    attempt: int
    lease_epoch: int
    worker_id: uuid.UUID
    mode: RunMode
    batch_id: str | None
    generation: int | None
    start_token: str | None
    window_start: datetime | None
    window_end: datetime | None
    watermark_value: dict[str, Any] | None
    previous_spool_worker: uuid.UUID | None


@dataclass(frozen=True, kw_only=True)
class Totals:
    rows_extracted: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    rows_deleted: int | None = None


def snapshot(state: DeltaState | None) -> DeltaSnapshot | None:
    if state is None:
        return None
    return DeltaSnapshot(
        status=DeltaStatus(state.status),
        sap_close_pending=state.sap_close_pending,
        init_filters_hash=state.init_filters_hash,
    )


def ensure_delta_state(session: Session, pipeline_id: uuid.UUID) -> DeltaState:
    """Fila de estado del pipeline, bloqueada (se crea sin inicializar si no existía)."""
    session.execute(
        insert(DeltaState)
        .values(
            pipeline_id=pipeline_id,
            status=DeltaStatus.NOT_INITIALIZED.value,
            generation=0,
            lease_epoch=0,
            sap_close_pending=False,
            updated_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["pipeline_id"])
    )
    return session.scalars(
        select(DeltaState)
        .where(DeltaState.pipeline_id == pipeline_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def history(
    session: Session,
    state: DeltaState,
    event: DeltaEvent,
    before: str | None,
    *,
    actor: str,
    run_id: uuid.UUID | None = None,
    reason: str | None = None,
    token_before: str | None = None,
    token_after: str | None = None,
) -> None:
    session.add(
        DeltaStateHistory(
            pipeline_id=state.pipeline_id,
            at=utcnow(),
            event=event.value,
            from_status=before,
            to_status=state.status,
            generation=state.generation,
            run_id=run_id,
            actor=actor,
            reason=reason,
            token_before=token_before,
            token_after=token_after,
        )
    )


class EngineStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def _tx(self) -> Iterator[Session]:
        with self.db.session() as session:
            yield session
            session.commit()

    # ================================================================ toma
    def begin(self, ctx: RunContext, *, filters_hash: str) -> Claim:
        """Fija la identidad del lote y toma el estado de delta (plan 06 §3.6).

        Un run ``DELTA`` de un pipeline sin inicializar se convierte aquí en ``DELTA_INIT``.
        """
        with self._tx() as session:
            run = session.scalars(
                select(Run)
                .where(
                    Run.id == ctx.run_id,
                    Run.lease_epoch == ctx.lease_epoch,
                    Run.status == RunStatus.RUNNING.value,
                )
                .with_for_update()
            ).first()
            if run is None:
                raise FencedError
            previous = session.scalar(
                select(RunAttempt.spool_worker_id)
                .where(
                    RunAttempt.run_id == run.id,
                    RunAttempt.number < ctx.attempt,
                    RunAttempt.spool_worker_id.is_not(None),
                )
                .order_by(RunAttempt.number.desc())
                .limit(1)
            )
            mode = RunMode(run.mode)
            watermark: dict[str, Any] | None = None
            if mode in DELTA_RUN_MODES or mode is RunMode.WATERMARK:
                state = ensure_delta_state(session, run.pipeline_id)
                if state.pending_run_id not in (None, run.id):
                    msg = "Otro run es dueño del estado de delta del pipeline."
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-RUN-DELTA-OWNED"
                    )
                if mode in DELTA_RUN_MODES:
                    mode = self._delta_identity(session, run, state, filters_hash)
                watermark = state.watermark_value
                state.pending_run_id = run.id
                state.lease_epoch = ctx.lease_epoch
                state.updated_at = utcnow()
            elif run.batch_id is None:
                run.batch_id = batch_id(mode, pipeline_id=run.pipeline_id, run_id=run.id)
            session.flush()
            return Claim(
                run_id=run.id,
                pipeline_id=run.pipeline_id,
                attempt=ctx.attempt,
                lease_epoch=ctx.lease_epoch,
                worker_id=ctx.worker_id,
                mode=mode,
                batch_id=run.batch_id,
                generation=run.delta_generation,
                start_token=run.start_token,
                window_start=run.window_start,
                window_end=run.window_end,
                watermark_value=watermark,
                previous_spool_worker=previous,
            )

    @staticmethod
    def _delta_identity(
        session: Session, run: Run, state: DeltaState, filters_hash: str
    ) -> RunMode:
        if state.status == DeltaStatus.INITIALIZING.value and state.pending_run_id == run.id:
            run.mode = RunMode.DELTA_INIT.value  # reintento de una init: mismo lote
            return RunMode.DELTA_INIT
        if state.status == DeltaStatus.INITIALIZING.value:  # init huérfana, sin dueño
            state.status = DeltaStatus.NOT_INITIALIZED.value
        ensure_runnable(snapshot(state), filters_hash)
        if state.status == DeltaStatus.NOT_INITIALIZED.value:
            before = state.status
            state.status = DeltaStatus.INITIALIZING.value
            state.generation += 1
            state.current_token = state.pending_token = None
            state.init_filters_hash = filters_hash
            history(
                session, state, DeltaEvent.INIT_STARTED, before, actor=WORKER.name, run_id=run.id
            )
            run.mode = RunMode.DELTA_INIT.value
            run.delta_generation = state.generation
            run.start_token = None
            run.batch_id = batch_id(
                RunMode.DELTA_INIT,
                pipeline_id=run.pipeline_id,
                run_id=run.id,
                generation=state.generation,
            )
            return RunMode.DELTA_INIT
        # INITIALIZED: el lote depende del token de partida; un reintento repite el mismo.
        run.mode = RunMode.DELTA.value
        if run.batch_id is None or run.start_token != state.current_token:
            run.delta_generation = state.generation
            run.start_token = state.current_token
            run.batch_id = batch_id(
                RunMode.DELTA,
                pipeline_id=run.pipeline_id,
                generation=state.generation,
                start_token=state.current_token,
            )
        return RunMode.DELTA

    def fix_window(self, claim: Claim, start: datetime, end: datetime) -> str:
        identity = batch_id(
            RunMode.WATERMARK, pipeline_id=claim.pipeline_id, window_start=start, window_end=end
        )
        with self._tx() as session:
            changed = session.execute(
                update(Run)
                .where(Run.id == claim.run_id, Run.lease_epoch == claim.lease_epoch)
                .values(window_start=start, window_end=end, batch_id=identity)
            )
            if not _rowcount(changed):
                raise FencedError
        return identity

    # ================================================================ durante el intento
    def set_pending_token(self, claim: Claim, token: str) -> None:
        with self._tx() as session:
            changed = session.execute(
                update(DeltaState)
                .where(
                    DeltaState.pipeline_id == claim.pipeline_id,
                    DeltaState.pending_run_id == claim.run_id,
                    DeltaState.lease_epoch == claim.lease_epoch,
                )
                .values(pending_token=token, updated_at=utcnow())
            )
            if not _rowcount(changed):
                raise FencedError

    def mark_attempt(self, claim: Claim, **values: Any) -> None:
        """``sap_requested`` o ``spool_worker_id`` del intento (informativo, sin fencing)."""
        with self._tx() as session:
            session.execute(
                update(RunAttempt)
                .where(RunAttempt.run_id == claim.run_id, RunAttempt.number == claim.attempt)
                .values(**values)
            )

    def start_step(self, run_id: uuid.UUID, attempt: int, step: StepName) -> uuid.UUID:
        with self._tx() as session:
            row = RunStep(
                run_id=run_id,
                attempt=attempt,
                step=step.value,
                status=StepStatus.RUNNING.value,
                started_at=utcnow(),
                metrics={},
            )
            session.add(row)
            session.flush()
            return row.id

    def finish_step(
        self, step_id: uuid.UUID, status: StepStatus, metrics: dict[str, Any] | None = None
    ) -> None:
        with self._tx() as session:
            session.execute(
                update(RunStep)
                .where(RunStep.id == step_id)
                .values(status=status.value, finished_at=utcnow(), metrics=metrics or {})
            )

    def log(
        self,
        run_id: uuid.UUID,
        *,
        attempt: int | None,
        step: StepName | None,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._tx() as session:
            session.add(
                RunLog(
                    run_id=run_id,
                    attempt=attempt,
                    step=step.value if step else None,
                    level=level,
                    message=message,
                    data=data or {},
                    ts=utcnow(),
                )
            )

    # ================================================================ confirmación
    def commit(
        self,
        claim: Claim,
        *,
        next_token: str | None,
        window_end: datetime | None,
        totals: Totals,
    ) -> None:
        """``commit_delta``: token, marca de agua y run ``succeeded`` en una sola transacción.

        La orquestación ve el run ya confirmado y solo cierra el intento.
        """
        now = utcnow()
        with self.db.session() as session:
            if claim.mode in DELTA_RUN_MODES:
                self._commit_delta(session, claim, now)
            elif claim.mode is RunMode.WATERMARK:
                self._commit_watermark(session, claim, window_end, now)
            changed = session.execute(
                update(Run)
                .where(
                    Run.id == claim.run_id,
                    Run.lease_epoch == claim.lease_epoch,
                    Run.status == RunStatus.RUNNING.value,
                )
                .values(
                    status=RunStatus.SUCCEEDED.value,
                    finished_at=now,
                    claimed_by=None,
                    next_token=next_token,
                    error_code=None,
                    error_kind=None,
                    error_message=None,
                    rows_extracted=totals.rows_extracted,
                    rows_inserted=totals.rows_inserted,
                    rows_updated=totals.rows_updated,
                    rows_deleted=totals.rows_deleted,
                )
            )
            if not _rowcount(changed):
                session.rollback()
                raise FencedError
            session.commit()

    @staticmethod
    def _commit_delta(session: Session, claim: Claim, now: datetime) -> None:
        state = session.scalars(
            select(DeltaState)
            .where(
                DeltaState.pipeline_id == claim.pipeline_id,
                DeltaState.pending_run_id == claim.run_id,
                DeltaState.lease_epoch == claim.lease_epoch,
                DeltaState.generation == claim.generation,
                DeltaState.pending_token.is_not(None),
            )
            .with_for_update()
        ).first()
        if state is None:
            session.rollback()
            raise FencedError
        before_status, before_token = state.status, state.current_token
        state.current_token = state.pending_token
        state.pending_token = None
        state.pending_run_id = None
        state.status = DeltaStatus.INITIALIZED.value
        state.last_delta_at = now
        state.initialized_at = state.initialized_at or now
        state.updated_at = now
        if before_status != state.status:
            history(
                session,
                state,
                DeltaEvent.INITIALIZED,
                before_status,
                actor=WORKER.name,
                run_id=claim.run_id,
                token_before=before_token,
                token_after=state.current_token,
            )
        session.flush()

    @staticmethod
    def _commit_watermark(
        session: Session, claim: Claim, window_end: datetime | None, now: datetime
    ) -> None:
        changed = session.execute(
            update(DeltaState)
            .where(
                DeltaState.pipeline_id == claim.pipeline_id,
                DeltaState.pending_run_id == claim.run_id,
                DeltaState.lease_epoch == claim.lease_epoch,
            )
            .values(
                watermark_value={"end": window_end.isoformat() if window_end else None},
                pending_run_id=None,
                status=DeltaStatus.INITIALIZED.value,
                last_delta_at=now,
                initialized_at=func.coalesce(DeltaState.initialized_at, now),
                updated_at=now,
            )
        )
        if not _rowcount(changed):
            session.rollback()
            raise FencedError


def release_delta(session: Session, run: Run, *, actor: Actor, audit: AuditService) -> None:
    """Run terminado sin éxito: suelta el estado de delta (plan 06 §3.6).

    * Una init cortada vuelve a ``NOT_INITIALIZED``; si llegó a pedir datos a SAP, la
      suscripción puede haber quedado abierta (H10): ``sap_close_pending``.
    * Un token rechazado por SAP (error de clase ``delta``) deja el pipeline en ``BROKEN``.
    """
    state = session.scalars(
        select(DeltaState)
        .where(DeltaState.pipeline_id == run.pipeline_id, DeltaState.pending_run_id == run.id)
        .with_for_update()
    ).first()
    if state is None:
        return
    before = state.status
    state.pending_token = None
    state.pending_run_id = None
    state.updated_at = utcnow()
    event: DeltaEvent | None = None
    if before == DeltaStatus.INITIALIZING.value:
        state.status = DeltaStatus.NOT_INITIALIZED.value
        asked_sap = bool(
            session.scalar(
                select(func.count())
                .select_from(RunAttempt)
                .where(RunAttempt.run_id == run.id, RunAttempt.sap_requested.is_(True))
            )
        )
        if asked_sap and INIT_FAILURE_LEAVES_SUBSCRIPTION:
            state.sap_close_pending = True
        event = DeltaEvent.INIT_ABORTED
    elif run.error_kind == ErrorKind.DELTA.value and before == DeltaStatus.INITIALIZED.value:
        state.status = DeltaStatus.BROKEN.value
        event = DeltaEvent.BROKEN
    if event is None:
        return
    history(
        session, state, event, before, actor=actor.name, run_id=run.id, reason=run.error_message
    )
    if event is DeltaEvent.BROKEN:
        pipeline = session.get(Pipeline, run.pipeline_id)
        audit.record(
            session,
            AuditEntry(
                action="pipeline.delta_broken",
                outcome=Outcome.FAILURE,
                resource_type="pipeline",
                resource_id=str(run.pipeline_id),
                resource_name=pipeline.code if pipeline else None,
                details={"run_id": str(run.id), "error_code": run.error_code},
            ),
            actor=actor,
        )
