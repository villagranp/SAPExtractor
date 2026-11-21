"""Puerto entre la orquestación y el motor: quién ejecuta un run una vez tomado de la cola.

La orquestación (It6) reparte el trabajo y lleva el arrendamiento; el motor de extracción
(It5) implementa ``RunExecutor`` con los pasos ``prepare → extract → load → commit_delta``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sapextract.dataflow.domain.batch import RunMode


class StopReason(StrEnum):
    CANCELLED = "cancelled"  # lo pidió un usuario
    SHUTDOWN = "shutdown"  # apagado ordenado del worker (SIGTERM)
    TIMEOUT = "timeout"  # superó el tiempo máximo del pipeline
    FENCED = "fenced"  # perdió el arrendamiento: otro worker es el dueño


class RunStoppedError(Exception):
    """El ejecutor dejó el trabajo porque la orquestación se lo pidió."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, kw_only=True)
class RunContext:
    """Lo que el ejecutor necesita para trabajar con *fencing* (ADR 0006 §5)."""

    run_id: uuid.UUID
    pipeline_id: uuid.UUID
    mode: RunMode
    attempt: int
    lease_epoch: int
    worker_id: uuid.UUID
    stop_reason: Callable[[], StopReason | None]

    def is_cancelled(self) -> bool:
        """Para ``ReadContext.is_cancelled``: cualquier motivo de parada corta la lectura."""
        return self.stop_reason() is not None

    def checkpoint(self) -> None:
        """Se llama entre páginas y entre pasos; lanza si hay que parar."""
        reason = self.stop_reason()
        if reason is not None:
            raise RunStoppedError(reason)


class RunExecutor(Protocol):
    def execute(self, ctx: RunContext) -> None:
        """Ejecuta el run. Termina sin error si lo confirmó.

        Lanza ``DataflowError`` (su ``ErrorKind`` decide si se reintenta) o ``RunStoppedError``.
        Toda escritura sobre el run o el estado de delta lleva ``AND lease_epoch = :epoch``.
        """
        ...
