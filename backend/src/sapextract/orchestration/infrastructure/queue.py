"""Cola de trabajo sobre ``run`` (plan 08 §3): toma con límites, latido y aviso a workers.

La cola son los runs en ``queued``. La toma aplica en una sola consulta los límites por
sistema SAP y por grupo; los runs sin hueco se quedan en la cola.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from sapextract.dataflow.domain.batch import RunMode
from sapextract.orchestration.domain.model import RetryPolicy

# Serializa las tomas: dos workers no pueden contar a la vez los runs en curso de un sistema
# o de un grupo y superar el límite entre los dos. La toma dura milisegundos, así que un
# bloqueo único basta; no se conoce el sistema SAP del candidato antes de elegirlo.
CLAIM_LOCK = 7_215_003
QUEUE_CHANNEL = "sx_run_queued"

_CLAIM = text(
    """
    WITH candidate AS (
        SELECT r.id
          FROM sx.run r
          JOIN sx.pipeline p ON p.id = r.pipeline_id
          JOIN sx.source s ON s.id = p.source_id
          JOIN sx.sap_system y ON y.id = s.sap_system_id
          LEFT JOIN sx.concurrency_group g ON g.code = p.concurrency_group
         WHERE r.status = 'queued'
           AND r.available_at <= :now
           AND p.status = 'active'
           AND p.deleted_at IS NULL
           AND p.worker_pool = ANY(CAST(:pools AS text[]))
           AND (SELECT count(*)
                  FROM sx.run x
                  JOIN sx.pipeline xp ON xp.id = x.pipeline_id
                  JOIN sx.source xs ON xs.id = xp.source_id
                 WHERE x.status = 'running'
                   AND xs.sap_system_id = s.sap_system_id) < y.max_concurrent_runs
           AND (g.code IS NULL
                OR (SELECT count(*)
                      FROM sx.run x
                      JOIN sx.pipeline xp ON xp.id = x.pipeline_id
                     WHERE x.status = 'running'
                       AND xp.concurrency_group = g.code) < g.max_parallel)
         ORDER BY r.priority, r.available_at, r.created_at
         LIMIT 1
           FOR UPDATE OF r SKIP LOCKED
    )
    UPDATE sx.run r
       SET status = 'running',
           lease_epoch = r.lease_epoch + 1,
           claimed_by = :worker,
           claimed_at = :now,
           heartbeat_at = :now,
           started_at = COALESCE(r.started_at, :now)
      FROM candidate c, sx.pipeline p
     WHERE r.id = c.id AND p.id = r.pipeline_id
    RETURNING r.id, r.pipeline_id, r.mode, r.lease_epoch, p.timeout_minutes, p.retry_policy
    """
)

_NEW_ATTEMPT = text(
    """
    INSERT INTO sx.run_attempt (run_id, number, worker_id, lease_epoch, started_at)
    SELECT :run, COALESCE(max(number), 0) + 1, :worker, :epoch, :now
      FROM sx.run_attempt WHERE run_id = :run
    RETURNING number
    """
)

_HEARTBEAT = text(
    """
    UPDATE sx.run SET heartbeat_at = :now
     WHERE id = :run AND status = 'running' AND lease_epoch = :epoch
    RETURNING cancel_requested_at
    """
)


@dataclass(frozen=True, kw_only=True)
class ClaimedRun:
    """Run tomado por un worker, con el arrendamiento que debe presentar en cada escritura."""

    run_id: uuid.UUID
    pipeline_id: uuid.UUID
    mode: RunMode
    lease_epoch: int
    attempt: int
    timeout_minutes: int
    retry_policy: RetryPolicy


@dataclass(frozen=True, kw_only=True)
class Lease:
    alive: bool  # False: el run ya no es de este worker (*fencing*)
    cancel_requested: bool = False


def claim(
    session: Session, *, worker_id: uuid.UUID, pools: list[str], now: datetime
) -> ClaimedRun | None:
    """Toma el siguiente run con hueco y abre su intento. El llamador confirma la transacción."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK})
    row = session.execute(_CLAIM, {"now": now, "pools": pools, "worker": worker_id}).first()
    if row is None:
        return None
    number = session.execute(
        _NEW_ATTEMPT, {"run": row.id, "worker": worker_id, "epoch": row.lease_epoch, "now": now}
    ).scalar_one()
    return ClaimedRun(
        run_id=row.id,
        pipeline_id=row.pipeline_id,
        mode=RunMode(row.mode),
        lease_epoch=row.lease_epoch,
        attempt=number,
        timeout_minutes=row.timeout_minutes,
        retry_policy=RetryPolicy.from_dict(row.retry_policy),
    )


def heartbeat(session: Session, claimed: ClaimedRun, *, now: datetime) -> Lease:
    """Latido con *fencing*: si no afecta a ninguna fila, el worker perdió el run."""
    row = session.execute(
        _HEARTBEAT, {"run": claimed.run_id, "epoch": claimed.lease_epoch, "now": now}
    ).first()
    if row is None:
        return Lease(alive=False)
    return Lease(alive=True, cancel_requested=row.cancel_requested_at is not None)


def notify_queued(session: Session) -> None:
    """Avisa a los workers ociosos; PostgreSQL entrega el aviso al confirmar la transacción."""
    session.execute(text("SELECT pg_notify(:channel, '')"), {"channel": QUEUE_CHANNEL})


class QueueListener:
    """Conexión dedicada con ``LISTEN``: despierta al worker sin esperar al sondeo."""

    def __init__(self, engine: Engine) -> None:
        self._proxy = engine.raw_connection()
        conn: Any = self._proxy.driver_connection
        if conn is None:  # el pool siempre entrega la conexión del driver
            msg = "Sin conexión del driver para LISTEN"
            raise RuntimeError(msg)
        self._conn: Any = conn
        self._conn.autocommit = True
        self._conn.execute(f"LISTEN {QUEUE_CHANNEL}")

    def wait(self, seconds: float) -> bool:
        """Espera un aviso como máximo ``seconds``. True si llegó alguno."""
        return any(True for _ in self._conn.notifies(timeout=seconds, stop_after=1))

    def close(self) -> None:
        # La conexión quedó en autocommit y escuchando: no vuelve al pool.
        self._proxy.invalidate()
        self._proxy.close()
