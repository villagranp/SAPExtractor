"""Pasos, log y estado de delta de un run (plan 06 §2 y §3.6).

El run, sus intentos, la cola y los reintentos son de la orquestación (plan 08). Las reglas que
dependen del spike (ADR 0002) toman aquí la opción conservadora; cada una indica la hipótesis
que la cerrará.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from sapextract.core.exceptions import ConflictError
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind


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


# H10 (pendiente): no se sabe si relanzar una init abre otra suscripción en la ODQ. Hasta
# confirmarlo, un error transitorio en una init no se reintenta solo.
RETRY_DELTA_INIT = False
# H10 (pendiente): una init cortada puede dejar la suscripción abierta en SAP.
INIT_FAILURE_LEAVES_SUBSCRIPTION = True

ERROR_DELTA_BROKEN = "SX-PIPE-DELTA-BROKEN"
ERROR_SAP_CLOSE_PENDING = "SX-PIPE-SAP-CLOSE-PENDING"
ERROR_FILTERS_CHANGED = "SX-PIPE-DELTA-FILTERS-CHANGED"


class NotRetryableError(DataflowError):
    """Error transitorio que, aun así, no debe reintentarse solo (una init, H10)."""

    @property
    def retryable(self) -> bool:
        return False


def filters_hash(rendered_filter: str) -> str:
    """Huella de los filtros de una init: si cambian, hay que reinicializar."""
    return hashlib.sha256(rendered_filter.encode()).hexdigest()


@dataclass(frozen=True, kw_only=True)
class DeltaSnapshot:
    status: DeltaStatus
    sap_close_pending: bool
    init_filters_hash: str | None


def delta_blocker(state: DeltaSnapshot | None, current_filters_hash: str) -> tuple[str, str] | None:
    """Por qué un pipeline delta no puede ejecutarse ahora: ``(código, mensaje)`` o ``None``."""
    if state is None:
        return None
    if state.status is DeltaStatus.BROKEN:
        return (
            ERROR_DELTA_BROKEN,
            "El delta del pipeline está roto (SAP rechazó el token). Reinicialícelo para volver "
            "a cargar.",
        )
    if state.status is DeltaStatus.NOT_INITIALIZED and state.sap_close_pending:
        return (
            ERROR_SAP_CLOSE_PENDING,
            "La suscripción anterior sigue abierta en SAP. Ciérrela en ODQMON y confírmelo, o "
            "declare que la deja abierta, antes de inicializar de nuevo.",
        )
    if state.status is DeltaStatus.INITIALIZED and state.init_filters_hash not in (
        None,
        current_filters_hash,
    ):
        return (
            ERROR_FILTERS_CHANGED,
            "Los filtros cambiaron desde la inicialización: reinicialice el delta.",
        )
    return None


def check_admission(state: DeltaSnapshot | None, current_filters_hash: str) -> None:
    """Antes de encolar: 409 si el estado de delta no permite ejecutar."""
    blocker = delta_blocker(state, current_filters_hash)
    if blocker is not None:
        raise ConflictError(blocker[1], code=blocker[0])


def ensure_runnable(state: DeltaSnapshot | None, current_filters_hash: str) -> None:
    """En el motor (runs programados): el mismo bloqueo como error de configuración."""
    blocker = delta_blocker(state, current_filters_hash)
    if blocker is not None:
        raise DataflowError(blocker[1], kind=ErrorKind.CONFIGURATION, code=blocker[0])
