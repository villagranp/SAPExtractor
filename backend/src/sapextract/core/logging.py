"""Logs estructurados (structlog) con enmascarado obligatorio de secretos."""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from sapextract.core.config import Settings
from sapextract.core.masking import mask_mapping


def mask_secrets(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Procesador structlog: enmascara claves sensibles y credenciales en URLs."""
    return mask_mapping(event_dict)


def safe_trace(exc: BaseException) -> list[str]:
    """Traza sin textos: tipo de cada excepción encadenada y sus marcos (archivo, línea, función).

    El mensaje de una excepción puede llevar datos o credenciales (parámetros de una consulta,
    una fila, una URL): nunca se escribe en el log.
    """
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        lines.append(type(current).__name__)
        lines.extend(
            f"{frame.filename}:{frame.lineno} {frame.name}"
            for frame in traceback.extract_tb(current.__traceback__)
        )
        current = current.__cause__ or current.__context__
    return lines


def _exception_of(exc_info: object) -> BaseException | None:
    if isinstance(exc_info, BaseException):
        return exc_info
    if isinstance(exc_info, tuple):
        found = exc_info[1] if len(exc_info) > 1 else None
        return found if isinstance(found, BaseException) else None
    return sys.exc_info()[1] if exc_info else None


def safe_exc_info(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Procesador structlog: sustituye ``exc_info`` por el tipo y la traza sin textos.

    Ocupa el lugar de ``format_exc_info``, que escribiría el mensaje de la excepción. Vale para
    ``log.exception`` y para las bibliotecas que registran con ``exc_info`` (uvicorn, sqlalchemy).
    """
    exc = _exception_of(event_dict.pop("exc_info", None))
    if exc is not None:
        event_dict.setdefault("error_type", type(exc).__name__)
        event_dict["trace"] = safe_trace(exc)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        mask_secrets,
    ]


def build_formatter(settings: Settings) -> structlog.stdlib.ProcessorFormatter:
    """Formateador único de la plataforma, para logs propios y de bibliotecas."""
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            safe_exc_info,
            renderer,
        ],
    )


def configure_logging(settings: Settings) -> None:
    """Configura structlog y redirige el logging estándar (uvicorn, sqlalchemy) al mismo formato."""
    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_formatter(settings))

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
