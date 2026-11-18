"""Bus de eventos de dominio en proceso.

Permite desacoplar módulos: p. ej. ``UserDeactivated`` → auditoría y revocación de sesiones,
sin que el módulo de identidad conozca a los demás.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


type Handler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: defaultdict[type[DomainEvent], list[Handler]] = defaultdict(list)

    def subscribe[E: DomainEvent](self, event_type: type[E], handler: Callable[[E], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent) -> None:
        """Ejecuta los handlers de forma síncrona; un handler que falla no impide a los demás."""
        for event_type, handlers in self._handlers.items():
            if isinstance(event, event_type):
                for handler in handlers:
                    try:
                        handler(event)
                    except Exception:
                        log.exception("event_handler_failed", event_type=type(event).__name__)
