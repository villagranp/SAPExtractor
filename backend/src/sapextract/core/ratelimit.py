"""Limitador de frecuencia en memoria (ventana deslizante) para endpoints sensibles.

Es por proceso: con varias réplicas de la API el límite efectivo se multiplica. Suficiente
como freno a la fuerza bruta junto con el bloqueo de cuentas, que sí es global (en BD).
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, *, now: float | None = None) -> int | None:
        """Registra un intento. Devuelve los segundos de espera si se supera el límite."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            if len(self._hits) > 10_000:  # evita crecer sin límite con IPs efímeras
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
