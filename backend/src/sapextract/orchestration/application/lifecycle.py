"""Ciclo de vida de un run en la cola: encolar, cerrar un intento y recuperar los huérfanos.

Una sola máquina de estados para run, intento y cola (plan 06 §2). Toda escritura de un
worker lleva su ``lease_epoch``: si no afecta a ninguna fila, el intento acaba como ``fenced``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sapextract.dataflow.domain.batch import RunMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.orchestration.domain.model import (
    CONSUMING_OUTCOMES,
    ERROR_STALE,
    LIVE_STATUSES,
    AttemptOutcome,
    RetryPolicy,
    RunStatus,
    RunTrigger,
)
from sapextract.orchestration.infrastructure.models import Pipeline, Run, RunAttempt
from sapextract.orchestration.infrastructure.queue import ClaimedRun, notify_queued

MAX_ERROR_MESSAGE = 2000


class VerdictKind(StrEnum):
    SUCCEEDED = "succeeded"
    TRANSIENT = "transient"  # se reintenta si queda presupuesto
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # apagado ordenado: vuelve a la cola sin gastar intento
    FENCED = "fenced"


@dataclass(frozen=True, kw_only=True)
class Verdict:
    """Cómo terminó la ejecución de un intento, visto por el worker."""

    kind: VerdictKind
    error_code: str | None = None
    error_kind: ErrorKind | None = None
    error_message: str | None = None


def _rowcount(result: Any) -> int:
    return cast("CursorResult[Any]", result).rowcount


def live_run(session: Session, pipeline_id: uuid.UUID) -> Run | None:
    return session.scalar(
        select(Run).where(Run.pipeline_id == pipeline_id, Run.status.in_(LIVE_STATUSES))
    )


def enqueue_run(
    session: Session,
    pipeline: Pipeline,
    *,
    mode: RunMode,
    trigger: RunTrigger,
    triggered_by: str | None,
    priority: int,
    now: datetime,
    schedule_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> Run | None:
    """Deja un run en la cola. ``None`` si el pipeline ya tiene uno en cola o en curso."""
    if live_run(session, pipeline.id) is not None:
        return None
    run = Run(
        pipeline_id=pipeline.id,
        mode=mode.value,
        status=RunStatus.QUEUED.value,
        trigger=trigger.value,
        triggered_by=triggered_by,
        schedule_id=schedule_id,
        reason=reason,
        priority=priority,
        available_at=now,
        attempts_budget=RetryPolicy.from_dict(pipeline.retry_policy).max_attempts,
        lease_epoch=0,
        created_at=now,
    )
    try:
        # El índice único parcial decide si dos peticiones encolan a la vez.
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:
        return None
    notify_queued(session)
    return run


def consumed_attempts(session: Session, run_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(RunAttempt)
            .where(RunAttempt.run_id == run_id, RunAttempt.outcome.in_(CONSUMING_OUTCOMES))
        )
        or 0
    )


def _close_attempt(
    session: Session,
    run_id: uuid.UUID,
    number: int,
    *,
    outcome: AttemptOutcome,
    now: datetime,
    verdict: Verdict | None = None,
) -> None:
    session.execute(
        update(RunAttempt)
        .where(
            RunAttempt.run_id == run_id, RunAttempt.number == number, RunAttempt.outcome.is_(None)
        )
        .values(
            outcome=outcome.value,
            finished_at=now,
            error_code=verdict.error_code if verdict else None,
            error_kind=verdict.error_kind.value if verdict and verdict.error_kind else None,
            error_message=(verdict.error_message or "")[:MAX_ERROR_MESSAGE] or None
            if verdict
            else None,
        )
    )


def _release(claimed: ClaimedRun, **values: Any) -> Any:
    """``UPDATE`` del run con *fencing*: solo si sigue siendo de este arrendamiento."""
    return (
        update(Run)
        .where(
            Run.id == claimed.run_id,
            Run.lease_epoch == claimed.lease_epoch,
            Run.status == RunStatus.RUNNING.value,
        )
        .values(claimed_by=None, **values)
    )


def settle(
    session: Session, claimed: ClaimedRun, verdict: Verdict, *, now: datetime, random: float
) -> AttemptOutcome:
    """Cierra el intento y deja el run en su estado siguiente. El llamador confirma."""
    outcome = _settle_run(session, claimed, verdict, now=now, random=random)
    _close_attempt(
        session,
        claimed.run_id,
        claimed.attempt,
        outcome=outcome,
        now=now,
        verdict=None if outcome in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FENCED) else verdict,
    )
    if outcome in (AttemptOutcome.FAILED_TRANSIENT, AttemptOutcome.INTERRUPTED):
        notify_queued(session)
    return outcome


def _settle_run(
    session: Session, claimed: ClaimedRun, verdict: Verdict, *, now: datetime, random: float
) -> AttemptOutcome:
    final_error = {
        "error_code": verdict.error_code,
        "error_kind": verdict.error_kind.value if verdict.error_kind else None,
        "error_message": (verdict.error_message or "")[:MAX_ERROR_MESSAGE] or None,
    }
    match verdict.kind:
        case VerdictKind.FENCED:
            return AttemptOutcome.FENCED
        case VerdictKind.SUCCEEDED:
            statement = _release(
                claimed,
                status=RunStatus.SUCCEEDED.value,
                finished_at=now,
                error_code=None,
                error_kind=None,
                error_message=None,
            )
            if _rowcount(session.execute(statement)):
                return AttemptOutcome.SUCCEEDED
            # El motor confirma el run en la transacción de ``commit_delta`` (plan 06 §3.6).
            committed = session.scalar(
                select(Run.id).where(
                    Run.id == claimed.run_id,
                    Run.lease_epoch == claimed.lease_epoch,
                    Run.status == RunStatus.SUCCEEDED.value,
                )
            )
            return AttemptOutcome.SUCCEEDED if committed else AttemptOutcome.FENCED
        case VerdictKind.CANCELLED:
            statement = _release(claimed, status=RunStatus.CANCELLED.value, finished_at=now)
            outcome = AttemptOutcome.CANCELLED
        case VerdictKind.INTERRUPTED:
            statement = _release(
                claimed, status=RunStatus.QUEUED.value, available_at=now, heartbeat_at=None
            )
            outcome = AttemptOutcome.INTERRUPTED
        case VerdictKind.TRANSIENT:
            consumed = consumed_attempts(session, claimed.run_id) + 1
            budget = session.scalar(select(Run.attempts_budget).where(Run.id == claimed.run_id))
            if consumed < (budget or 0):
                statement = _release(
                    claimed,
                    status=RunStatus.QUEUED.value,
                    available_at=now + claimed.retry_policy.delay(consumed, random),
                    heartbeat_at=None,
                )
                outcome = AttemptOutcome.FAILED_TRANSIENT
            else:
                statement = _release(
                    claimed, status=RunStatus.FAILED.value, finished_at=now, **final_error
                )
                outcome = AttemptOutcome.FAILED
        case VerdictKind.FAILED:
            statement = _release(
                claimed, status=RunStatus.FAILED.value, finished_at=now, **final_error
            )
            outcome = AttemptOutcome.FAILED
    return outcome if _rowcount(session.execute(statement)) else AttemptOutcome.FENCED


def reap_stale(
    session: Session,
    *,
    now: datetime,
    cutoff: datetime,
    random: float,
    on_final: Callable[[Session, Run], None] | None = None,
) -> int:
    """Cierra como ``stale`` los intentos sin latido y devuelve sus runs a la cola.

    Sube ``lease_epoch`` al recuperarlos: el worker anterior, si sigue vivo, queda fuera
    desde ese momento y no puede confirmar nada (plan 06 §3.3).
    """
    stmt = (
        select(Run, Pipeline.retry_policy)
        .join(Pipeline, Pipeline.id == Run.pipeline_id)
        .where(Run.status == RunStatus.RUNNING.value, Run.heartbeat_at < cutoff)
        .with_for_update(of=Run, skip_locked=True)
    )
    reaped = 0
    for run, policy in session.execute(stmt).all():
        session.execute(
            update(RunAttempt)
            .where(RunAttempt.run_id == run.id, RunAttempt.outcome.is_(None))
            .values(
                outcome=AttemptOutcome.STALE.value,
                finished_at=now,
                error_code=ERROR_STALE,
                error_message="El worker dejó de dar latido.",
            )
        )
        session.flush()
        consumed = consumed_attempts(session, run.id)
        run.lease_epoch += 1
        run.claimed_by = None
        run.heartbeat_at = None
        if run.cancel_requested_at is not None:
            run.status, run.finished_at = RunStatus.CANCELLED.value, now
        elif consumed < run.attempts_budget:
            run.status = RunStatus.QUEUED.value
            run.available_at = now + RetryPolicy.from_dict(policy).delay(consumed, random)
        else:
            run.status, run.finished_at = RunStatus.FAILED.value, now
            run.error_code = ERROR_STALE
            run.error_kind = ErrorKind.TRANSIENT.value
            run.error_message = "El worker dejó de dar latido y no quedan intentos."
        if on_final is not None and run.status != RunStatus.QUEUED.value:
            on_final(session, run)
        reaped += 1
    if reaped:
        session.flush()
        notify_queued(session)
    return reaped
