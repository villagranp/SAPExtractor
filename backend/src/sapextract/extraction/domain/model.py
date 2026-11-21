"""Ejecuciones, intentos, pasos y estado de delta (plan 06 §2 y §3.6, ADR 0006 §5).

Las reglas que dependen del spike (ADR 0002) toman aquí la opción conservadora; cada una
indica la hipótesis que la cerrará.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sapextract.core.exceptions import ConflictError
from sapextract.dataflow.domain.batch import RunMode
from sapextract.dataflow.domain.errors import ErrorKind
from sapextract.orchestration.domain.model import ExtractionMode


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})
FINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    API = "api"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED = "failed"
    STALE = "stale"
    FENCED = "fenced"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class StepName(StrEnum):
    PREPARE = "prepare"
    EXTRACT = "extract"
    LOAD = "load"
    COMMIT_DELTA = "commit_delta"
    CLEANUP = "cleanup"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeltaStatus(StrEnum):
    NOT_INITIALIZED = "NOT_INITIALIZED"
    INITIALIZING = "INITIALIZING"
    INITIALIZED = "INITIALIZED"
    BROKEN = "BROKEN"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DeltaEvent(StrEnum):
    """Eventos del historial de ``delta_state`` (plan 06 §3.6)."""

    INIT_STARTED = "init_started"
    INITIALIZED = "initialized"
    INIT_ABORTED = "init_aborted"
    BROKEN = "broken"
    RESET = "reset"
    SAP_SUBSCRIPTION_CLOSED = "sap_subscription_closed"
    SAP_SUBSCRIPTION_LEFT_OPEN = "sap_subscription_left_open"


# Esperas entre intentos (plan 08 §4: 1, 5 y 15 min). La It6 añade el jitter y la política
# por pipeline; aquí se pueden acortar por configuración para las pruebas.
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900)

# H10 (pendiente): no se sabe si relanzar una init abre otra suscripción en la ODQ. Hasta
# confirmarlo, una init no se reintenta sola.
RETRY_DELTA_INIT = False
# H10 (pendiente): una init cortada puede dejar la suscripción abierta en SAP.
INIT_FAILURE_LEAVES_SUBSCRIPTION = True


def backoff(attempt_number: int, schedule: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS) -> timedelta:
    """Espera antes del intento ``attempt_number + 1``."""
    if not schedule:
        return timedelta(0)
    index = min(max(attempt_number, 1), len(schedule)) - 1
    return timedelta(seconds=schedule[index])


def filters_hash(rendered_filter: str) -> str:
    """Huella de los filtros de una init: si cambian, hay que reinicializar."""
    return hashlib.sha256(rendered_filter.encode()).hexdigest()


@dataclass(frozen=True, kw_only=True)
class DeltaSnapshot:
    """Lo que hace falta del ``delta_state`` para decidir el modo del run."""

    status: DeltaStatus
    sap_close_pending: bool
    init_filters_hash: str | None


def run_mode_for(
    mode: ExtractionMode, delta: DeltaSnapshot | None, current_filters_hash: str
) -> RunMode:
    """Modo del próximo run de un pipeline, o 409 si el estado de delta no lo permite."""
    if mode is ExtractionMode.FULL:
        return RunMode.FULL
    if mode is ExtractionMode.WATERMARK:
        return RunMode.WATERMARK
    status = delta.status if delta else DeltaStatus.NOT_INITIALIZED
    if status is DeltaStatus.BROKEN:
        msg = (
            "El delta del pipeline está roto (SAP rechazó el token). Reinicialícelo para "
            "volver a cargar."
        )
        raise ConflictError(msg, code="SX-PPL-DELTA-BROKEN")
    if status is DeltaStatus.INITIALIZING:  # solo ocurre con un run en curso
        msg = "La inicialización del delta sigue en curso."
        raise ConflictError(msg, code="SX-PPL-RUN-ACTIVE")
    if status is DeltaStatus.NOT_INITIALIZED:
        if delta is not None and delta.sap_close_pending:
            msg = (
                "La suscripción anterior sigue abierta en SAP. Ciérrela en ODQMON y "
                "confírmelo, o declare que la deja abierta, antes de inicializar de nuevo."
            )
            raise ConflictError(msg, code="SX-PPL-SAP-CLOSE-PENDING")
        return RunMode.DELTA_INIT
    if delta is not None and delta.init_filters_hash not in (None, current_filters_hash):
        msg = "Los filtros cambiaron desde la inicialización: reinicialice el delta."
        raise ConflictError(msg, code="SX-PPL-DELTA-FILTERS-CHANGED")
    return RunMode.DELTA


def outcome_for(kind: ErrorKind, *, mode: RunMode, attempts_left: int) -> AttemptOutcome:
    """Un error transitorio se reintenta si quedan intentos (y no es una init, H10)."""
    if kind is not ErrorKind.TRANSIENT or attempts_left <= 0:
        return AttemptOutcome.FAILED
    if mode is RunMode.DELTA_INIT and not RETRY_DELTA_INIT:
        return AttemptOutcome.FAILED
    return AttemptOutcome.FAILED_TRANSIENT
