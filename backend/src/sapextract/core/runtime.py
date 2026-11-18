"""Utilidades para procesos de larga duración (scheduler, worker)."""

from __future__ import annotations

import signal
import threading
from types import FrameType

import structlog

log = structlog.get_logger(__name__)


class GracefulShutdown:
    """Captura SIGTERM/SIGINT y expone un evento para terminar el bucle principal ordenadamente."""

    def __init__(self) -> None:
        self.event = threading.Event()

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown_requested", signal=signal.Signals(signum).name)
        self.event.set()

    @property
    def requested(self) -> bool:
        return self.event.is_set()

    def wait(self, seconds: float) -> bool:
        """Espera ``seconds`` o hasta que se pida el apagado. Devuelve True si hay que salir."""
        return self.event.wait(seconds)
