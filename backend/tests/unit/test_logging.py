from __future__ import annotations

import json
import logging
import sys
from typing import Any

from sapextract.core.config import Settings
from sapextract.core.logging import build_formatter, mask_secrets, safe_exc_info
from sapextract.core.masking import MASK


def test_masks_sensitive_keys_recursively() -> None:
    event = {
        "event": "sap_connection_saved",
        "password": "p@ss",
        "config": {"user": "RFC_USER", "client_secret": "abc", "nested": [{"api_key": "k"}]},
        "Authorization": "Basic xyz",
        "rows": 10,
    }
    masked = mask_secrets(None, "info", event)
    assert masked["password"] == MASK
    assert masked["Authorization"] == MASK
    assert masked["config"]["client_secret"] == MASK
    assert masked["config"]["nested"][0]["api_key"] == MASK
    assert masked["config"]["user"] == "RFC_USER"
    assert masked["rows"] == 10


def test_masks_credentials_inside_urls() -> None:
    masked = mask_secrets(
        None, "info", {"event": "x", "url": "postgresql+psycopg://app:supersecreta@db:5432/sx"}
    )
    assert "supersecreta" not in masked["url"]
    assert masked["url"] == f"postgresql+psycopg://app:{MASK}@db:5432/sx"


def _raise_chained() -> None:
    try:
        msg = "fila con IBAN ES7620770024003102575766"
        raise KeyError(msg)
    except KeyError as exc:
        msg = "clave=Secreta.2026 en postgresql://app:otra@db/sx"
        raise ValueError(msg) from exc


def _formatted(record: logging.LogRecord, settings: Settings) -> dict[str, Any]:
    line = build_formatter(settings.model_copy(update={"log_json": True})).format(record)
    assert "Secreta" not in line
    assert "IBAN" not in line
    assert "Traceback" not in line
    payload: dict[str, Any] = json.loads(line)
    return payload


def test_exception_text_never_reaches_the_log(settings: Settings) -> None:
    """``log.exception`` deja el tipo y los marcos de la traza, no el mensaje de la excepción."""
    try:
        _raise_chained()
    except ValueError:
        own = logging.makeLogRecord(
            {
                "msg": {"event": "scheduler_tick_failed", "exc_info": True},
                "levelname": "ERROR",
                "_logger": None,
                "_name": "exception",
                "exc_info": sys.exc_info(),
            }
        )
        payload = _formatted(own, settings)

    assert payload["event"] == "scheduler_tick_failed"
    assert payload["error_type"] == "ValueError"
    assert "exc_info" not in payload
    assert "exception" not in payload
    assert [line for line in payload["trace"] if " " not in line] == ["ValueError", "KeyError"]
    assert any(line.endswith(" _raise_chained") for line in payload["trace"])


def test_library_exception_text_never_reaches_the_log(settings: Settings) -> None:
    """Lo mismo para bibliotecas que registran con ``exc_info`` (uvicorn, sqlalchemy)."""
    try:
        _raise_chained()
    except ValueError:
        foreign = logging.LogRecord(
            "uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", (), None
        )
        foreign.exc_info = sys.exc_info()
        payload = _formatted(foreign, settings)

    assert payload["event"] == "Exception in ASGI application"
    assert payload["error_type"] == "ValueError"
    assert payload["trace"][0] == "ValueError"


def test_explicit_error_type_is_kept_and_plain_events_are_untouched() -> None:
    event = safe_exc_info(None, "error", {"event": "x", "error_type": "Propio", "exc_info": False})
    assert event == {"event": "x", "error_type": "Propio"}
    boom = RuntimeError("dato sensible")
    event = safe_exc_info(None, "error", {"event": "x", "error_type": "Propio", "exc_info": boom})
    assert event["error_type"] == "Propio"
    assert event["trace"] == ["RuntimeError"]
    event = safe_exc_info(None, "error", {"event": "x", "exc_info": (None, None, None)})
    assert event == {"event": "x"}
