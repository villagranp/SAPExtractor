"""Escrituras del worker sobre el run y el estado de delta, con *fencing* (plan 06 §3.3 y §3.6).

Cada escritura lleva ``lease_epoch``: si no afecta a ninguna fila, otro worker tomó el run y
este debe parar (``FencedError``). Cada método usa su propia transacción corta: el worker no
mantiene transacciones abiertas mientras habla con SAP o con el destino.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.model import Actor, AuditEntry, Outcome
from sapextract.core.clock import utcnow
from sapextract.core.db import Database
from sapextract.dataflow.domain.batch import RunMode, batch_id
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.extraction.domain.model import (
    INIT_FAILURE_LEAVES_SUBSCRIPTION,
    AttemptOutcome,
    DeltaEvent,
    DeltaStatus,
    RunStatus,
    StepName,
    StepStatus,
)
from sapextract.extraction.infrastructure.models import (
    DeltaState,
    DeltaStateHistory,
    Run,
    RunAttempt,
    RunLog,
    RunStep,
)
from sapextract.orchestration.infrastructure.models import Pipeline

DELTA_MODES = frozenset({RunMode.DELTA_INIT, RunMode.DELTA, RunMode.WATERMARK})
WORKER = Actor.system("worker")


class FencedError(Exception):
    """El intento perdió el derecho a escribir: otro worker tiene el run."""


@dataclass(frozen=True, kw_only=True)
class Claim:
    """Run tomado por un worker, con la identidad del lote si ya está fijada."""

    run_id: uuid.UUID
    pipeline_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt: int
    lease_epoch: int
    worker_id: str
    mode: RunMode
    batch_id: str | None
    generation: int | None
    start_token: str | None
    window_start: datetime | None
    window_end: datetime | None
    watermark_value: dict[str, Any] | None
    timeout_minutes: int
    attempts_used: int
    max_attempts: int
    previous_spool_worker: str | None


@dataclass(frozen=True, kw_only=True)
class Failure:
    outcome: AttemptOutcome
    code: str | None = None
    kind: ErrorKind | None = None
    message: str | None = None
    retry_in: timedelta | None = None


@dataclass(frozen=True, kw_only=True)
class Totals:
    rows_extracted: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    rows_deleted: int | None = None


class RunStore:
    def __init__(self, db: Database, audit: AuditService) -> None:
        self.db = db
        self.audit = audit

    @contextmanager
    def _tx(self) -> Iterator[Session]:
        with self.db.session() as session:
            yield session
            session.commit()

    # ================================================================ toma
    def claim(self, worker_id: str, *, now: datetime | None = None) -> Claim | None:
        """Toma el siguiente run en cola (``FOR UPDATE SKIP LOCKED``) y abre un intento.

        Los límites por sistema SAP y por grupo de concurrencia llegan en la It6 (plan 08 §3).
        """
        moment = now or utcnow()
        with self._tx() as session:
            run = session.scalars(
                select(Run)
                .where(Run.status == RunStatus.QUEUED.value, Run.available_at <= moment)
                .order_by(Run.priority, Run.available_at, Run.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if run is None:
                return None
            mode = RunMode(run.mode)
            run.lease_epoch += 1
            epoch = run.lease_epoch
            run.status = RunStatus.RUNNING.value
            run.claimed_by = worker_id
            run.claimed_at = run.heartbeat_at = moment
            run.started_at = run.started_at or moment
            number = (
                session.scalar(
                    select(func.coalesce(func.max(RunAttempt.number), 0)).where(
                        RunAttempt.run_id == run.id
                    )
                )
                or 0
            ) + 1
            previous = session.scalar(
                select(RunAttempt.spool_worker_id)
                .where(RunAttempt.run_id == run.id, RunAttempt.spool_worker_id.is_not(None))
                .order_by(RunAttempt.number.desc())
                .limit(1)
            )
            attempt = RunAttempt(
                run_id=run.id,
                number=number,
                worker_id=worker_id,
                lease_epoch=epoch,
                started_at=moment,
                sap_requested=False,
            )
            session.add(attempt)
            watermark: dict[str, Any] | None = None
            if mode in DELTA_MODES:
                state = self._claim_delta(session, run, epoch)
                if state is None:
                    run.status = RunStatus.FAILED.value
                    run.finished_at = moment
                    run.error_code = "SX-RUN-DELTA-OWNED"
                    run.error_kind = ErrorKind.CONFIGURATION.value
                    run.error_message = "Otro run es dueño del estado de delta del pipeline."
                    attempt.outcome = AttemptOutcome.FAILED.value
                    attempt.finished_at = moment
                    return None
                watermark = state.watermark_value
            self._fix_identity(run, mode)
            session.flush()
            return Claim(
                run_id=run.id,
                pipeline_id=run.pipeline_id,
                attempt_id=attempt.id,
                attempt=number,
                lease_epoch=epoch,
                worker_id=worker_id,
                mode=mode,
                batch_id=run.batch_id,
                generation=run.delta_generation,
                start_token=run.start_token,
                window_start=run.window_start,
                window_end=run.window_end,
                watermark_value=watermark,
                timeout_minutes=run.timeout_minutes,
                attempts_used=run.attempts_used,
                max_attempts=run.max_attempts,
                previous_spool_worker=previous,
            )

    def _claim_delta(self, session: Session, run: Run, epoch: int) -> DeltaState | None:
        state = session.scalars(
            select(DeltaState)
            .where(
                DeltaState.pipeline_id == run.pipeline_id,
                (DeltaState.pending_run_id.is_(None)) | (DeltaState.pending_run_id == run.id),
            )
            .with_for_update()
        ).first()
        if state is None:
            return None
        first = run.batch_id is None and run.delta_generation is None
        state.pending_run_id = run.id
        state.lease_epoch = epoch
        state.updated_at = utcnow()
        if RunMode(run.mode) is RunMode.DELTA_INIT and first:
            before = state.status
            state.status = DeltaStatus.INITIALIZING.value
            state.generation += 1
            state.current_token = state.pending_token = None
            state.init_filters_hash = run.filters_hash
            session.add(
                DeltaStateHistory(
                    pipeline_id=run.pipeline_id,
                    event=DeltaEvent.INIT_STARTED.value,
                    from_status=before,
                    to_status=state.status,
                    generation=state.generation,
                    run_id=run.id,
                    actor=WORKER.name,
                )
            )
        if first:
            run.delta_generation = state.generation
            if RunMode(run.mode) is RunMode.DELTA:
                run.start_token = state.current_token
        session.flush()
        return state

    @staticmethod
    def _fix_identity(run: Run, mode: RunMode) -> None:
        """Fija el ``batch_id`` en el primer intento (WATERMARK lo fija ``prepare``)."""
        if run.batch_id is not None or mode is RunMode.WATERMARK:
            return
        run.batch_id = batch_id(
            mode,
            pipeline_id=run.pipeline_id,
            run_id=run.id,
            generation=run.delta_generation,
            start_token=run.start_token,
        )

    def fix_window(self, claim: Claim, start: datetime, end: datetime) -> str:
        identity = batch_id(
            RunMode.WATERMARK, pipeline_id=claim.pipeline_id, window_start=start, window_end=end
        )
        with self._tx() as session:
            changed = session.execute(
                update(Run)
                .where(Run.id == claim.run_id, Run.lease_epoch == claim.lease_epoch)
                .values(window_start=start, window_end=end, batch_id=identity)
            ).rowcount  # type: ignore[attr-defined]
            if not changed:
                raise FencedError
        return identity

    # ================================================================ durante el intento
    def heartbeat(self, claim: Claim) -> tuple[bool, bool]:
        """(sigue siendo dueño, se pidió cancelar)."""
        with self._tx() as session:
            row = session.execute(
                update(Run)
                .where(
                    Run.id == claim.run_id,
                    Run.lease_epoch == claim.lease_epoch,
                    Run.status == RunStatus.RUNNING.value,
                )
                .values(heartbeat_at=utcnow())
                .returning(Run.cancel_requested)
            ).first()
        return (row is not None, bool(row[0]) if row else False)

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
            ).rowcount  # type: ignore[attr-defined]
            if not changed:
                raise FencedError

    def mark_attempt(self, claim: Claim, **values: Any) -> None:
        """``sap_requested`` o ``spool_worker_id`` del intento (informativo, sin fencing)."""
        with self._tx() as session:
            session.execute(
                update(RunAttempt).where(RunAttempt.id == claim.attempt_id).values(**values)
            )

    def start_step(self, claim: Claim, step: StepName) -> uuid.UUID:
        with self._tx() as session:
            row = RunStep(
                run_id=claim.run_id,
                attempt=claim.attempt,
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

    # ================================================================ cierre
    def commit(
        self,
        claim: Claim,
        *,
        next_token: str | None,
        window_end: datetime | None,
        totals: Totals,
    ) -> None:
        """``commit_delta``: token, marca de agua y run ``succeeded`` en una sola transacción."""
        now = utcnow()
        with self.db.session() as session:
            if claim.mode in (RunMode.DELTA_INIT, RunMode.DELTA):
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
                    attempts_used=Run.attempts_used + 1,  # el intento que confirma también cuenta
                    finished_at=now,
                    next_token=next_token,
                    error_code=None,
                    error_kind=None,
                    error_message=None,
                    rows_extracted=totals.rows_extracted,
                    rows_inserted=totals.rows_inserted,
                    rows_updated=totals.rows_updated,
                    rows_deleted=totals.rows_deleted,
                )
            ).rowcount  # type: ignore[attr-defined]
            if not changed:
                session.rollback()
                raise FencedError
            session.execute(
                update(RunAttempt)
                .where(RunAttempt.id == claim.attempt_id)
                .values(outcome=AttemptOutcome.SUCCEEDED.value, finished_at=now)
            )
            self._audit_outcome(session, claim, "run.succeeded", Outcome.SUCCESS, totals=totals)
            session.commit()

    def _commit_delta(self, session: Session, claim: Claim, now: datetime) -> None:
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
            session.add(
                DeltaStateHistory(
                    pipeline_id=claim.pipeline_id,
                    event=DeltaEvent.INITIALIZED.value,
                    from_status=before_status,
                    to_status=state.status,
                    generation=state.generation,
                    run_id=claim.run_id,
                    actor=WORKER.name,
                    token_before=before_token,
                    token_after=state.current_token,
                )
            )
        session.flush()

    def _commit_watermark(
        self, session: Session, claim: Claim, window_end: datetime | None, now: datetime
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
        ).rowcount  # type: ignore[attr-defined]
        if not changed:
            session.rollback()
            raise FencedError

    def fail(self, claim: Claim, failure: Failure) -> RunStatus | None:
        """Cierra el intento y decide el run: vuelve a la cola o termina (y suelta el delta).

        Devuelve el estado final del run, o ``None`` si este intento ya no era el dueño.
        """
        now = utcnow()
        with self.db.session() as session:
            self._close_attempt(session, claim.attempt_id, failure, now)
            if failure.outcome is AttemptOutcome.FENCED:
                session.commit()
                return None
            run = session.scalars(
                select(Run)
                .where(Run.id == claim.run_id, Run.lease_epoch == claim.lease_epoch)
                .with_for_update()
            ).first()
            if run is None or run.status != RunStatus.RUNNING.value:
                session.commit()
                return None
            status = self._settle(session, run, failure, now, actor=WORKER)
            session.commit()
            return status

    def _close_attempt(
        self, session: Session, attempt_id: uuid.UUID, failure: Failure, now: datetime
    ) -> None:
        session.execute(
            update(RunAttempt)
            .where(RunAttempt.id == attempt_id, RunAttempt.outcome.is_(None))  # ya cerrado: no
            .values(
                outcome=failure.outcome.value,
                finished_at=now,
                error_code=failure.code,
                error_kind=failure.kind.value if failure.kind else None,
                error_message=failure.message,
            )
        )

    def _settle(
        self, session: Session, run: Run, failure: Failure, now: datetime, *, actor: Actor
    ) -> RunStatus:
        run.error_code = failure.code
        run.error_kind = failure.kind.value if failure.kind else None
        run.error_message = failure.message
        run.claimed_by = None
        if failure.outcome is AttemptOutcome.INTERRUPTED:
            run.status = RunStatus.QUEUED.value  # apagado ordenado: no gasta intento
            run.available_at = now
            return RunStatus.QUEUED
        run.attempts_used += 1
        retry = failure.outcome in (AttemptOutcome.FAILED_TRANSIENT, AttemptOutcome.STALE)
        if retry and run.attempts_used < run.max_attempts and not run.cancel_requested:
            run.status = RunStatus.QUEUED.value
            run.available_at = now + (failure.retry_in or timedelta(0))
            return RunStatus.QUEUED
        final = (
            RunStatus.CANCELLED
            if failure.outcome is AttemptOutcome.CANCELLED or run.cancel_requested
            else RunStatus.FAILED
        )
        run.status = final.value
        run.finished_at = now
        self.release_delta(session, run, broken=failure.kind is ErrorKind.DELTA, actor=actor)
        action = "run.cancelled" if final is RunStatus.CANCELLED else "run.failed"
        self._audit_run(session, run, action, Outcome.FAILURE, actor=actor)
        return final

    def release_delta(self, session: Session, run: Run, *, broken: bool, actor: Actor) -> None:
        release_delta(session, run, broken=broken, actor=actor, audit=self.audit)

    def reap_stale(self, stale_after: timedelta, *, now: datetime | None = None) -> int:
        """Intentos sin latido: se cierran como ``stale`` y el run vuelve a la cola (plan 08 §2)."""
        moment = now or utcnow()
        reaped = 0
        with self.db.session() as session:
            runs = session.scalars(
                select(Run)
                .where(
                    Run.status == RunStatus.RUNNING.value,
                    Run.heartbeat_at < moment - stale_after,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for run in runs:
                attempt = session.scalars(
                    select(RunAttempt)
                    .where(RunAttempt.run_id == run.id, RunAttempt.lease_epoch == run.lease_epoch)
                    .order_by(RunAttempt.number.desc())
                    .limit(1)
                ).first()
                failure = Failure(
                    outcome=AttemptOutcome.STALE,
                    code="SX-RUN-STALE",
                    kind=ErrorKind.TRANSIENT,
                    message="El worker dejó de dar latido; el intento se dio por caído.",
                )
                # `moment` solo decide qué está caído; las fechas que se escriben son las reales.
                current = utcnow()
                if attempt is not None:
                    self._close_attempt(session, attempt.id, failure, current)
                self._settle(session, run, failure, current, actor=Actor.system("scheduler"))
                reaped += 1
            session.commit()
        return reaped

    # ================================================================ auditoría
    def _audit_outcome(
        self, session: Session, claim: Claim, action: str, outcome: Outcome, *, totals: Totals
    ) -> None:
        run = session.get(Run, claim.run_id)
        if run is not None:
            details = {k: v for k, v in totals.__dict__.items() if v is not None}
            self._audit_run(session, run, action, outcome, actor=WORKER, extra=details)

    def _audit_run(
        self,
        session: Session,
        run: Run,
        action: str,
        outcome: Outcome,
        *,
        actor: Actor,
        extra: dict[str, Any] | None = None,
    ) -> None:
        pipeline = session.get(Pipeline, run.pipeline_id)
        self.audit.record(
            session,
            AuditEntry(
                action=action,
                outcome=outcome,
                resource_type="run",
                resource_id=str(run.id),
                resource_name=pipeline.code if pipeline else None,
                details={
                    "pipeline_id": str(run.pipeline_id),
                    "mode": run.mode,
                    "attempts": run.attempts_used,
                    **({"error_code": run.error_code} if run.error_code else {}),
                    **(extra or {}),
                },
            ),
            actor=actor,
        )


def release_delta(
    session: Session, run: Run, *, broken: bool, actor: Actor, audit: AuditService
) -> None:
    """Run terminado sin éxito: suelta el estado de delta (plan 06 §3.6)."""
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
    elif broken and before == DeltaStatus.INITIALIZED.value:
        state.status = DeltaStatus.BROKEN.value
        event = DeltaEvent.BROKEN
    if event is None:
        return
    session.add(
        DeltaStateHistory(
            pipeline_id=run.pipeline_id,
            event=event.value,
            from_status=before,
            to_status=state.status,
            generation=state.generation,
            run_id=run.id,
            actor=actor.name,
            reason=run.error_message,
        )
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
