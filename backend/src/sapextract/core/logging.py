"""Logs estructurados (structlog) con enmascarado obligatorio de secretos."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from sapextract.core.config import Settings

MASK = "***"

# Claves cuyo valor nunca debe aparecer en un log.
_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|"
    r"credential|master[_-]?key|connection[_-]?string|dsn)",
    re.IGNORECASE,
)
# Credenciales embebidas en URLs: esquema://usuario:clave@host
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^:/@\s]+):[^@/\s]+@")


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return _URL_CREDENTIALS.sub(r"\g<scheme>\g<user>:" + MASK + "@", value)
    if isinstance(value, Mapping):
        return _mask_mapping(value)
    if isinstance(value, list | tuple):
        return type(value)(_mask_value(v) for v in value)
    return value


def _mask_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (MASK if _SENSITIVE_KEY.search(str(key)) else _mask_value(value))
        for key, value in data.items()
    }


def mask_secrets(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Procesador structlog: enmascara claves sensibles y credenciales en URLs."""
    return _mask_mapping(event_dict)


def configure_logging(settings: Settings) -> None:
    """Configura structlog y redirige el logging estándar (uvicorn, sqlalchemy) al mismo formato."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        mask_secrets,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # uvicorn registra sus propios handlers: se delegan al root para un formato único.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # El log de acceso lo emite nuestro middleware (con request_id y duración).
    logging.getLogger("uvicorn.access").disabled = True
