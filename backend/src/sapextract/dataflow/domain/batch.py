"""Modos de run y de carga, e identidad determinista del lote (ADR 0006 §4)."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunMode(StrEnum):
    FULL = "FULL"
    DELTA_INIT = "DELTA_INIT"
    DELTA = "DELTA"
    WATERMARK = "WATERMARK"


class LoadMode(StrEnum):
    APPEND = "append"
    UPSERT = "upsert"
    REPLACE = "replace"


BATCH_ID_PATTERN = re.compile(r"^[dwif]-[0-9a-f]{24}$")
_PREFIX = {
    RunMode.DELTA: "d",
    RunMode.WATERMARK: "w",
    RunMode.DELTA_INIT: "i",
    RunMode.FULL: "f",
}


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def batch_id(
    mode: RunMode,
    *,
    pipeline_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    generation: int | None = None,
    start_token: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> str:
    """``<tipo>-<24 hex>``: mismo lote ⇔ mismo identificador (clave de idempotencia).

    * DELTA: pipeline + generación + token de partida (una repetición es el mismo lote).
    * WATERMARK: pipeline + ventana.
    * DELTA_INIT: pipeline + generación + run.
    * FULL: pipeline + run.
    """
    pid = str(pipeline_id)
    match mode:
        case RunMode.DELTA:
            if generation is None or not start_token:
                msg = "Un lote DELTA necesita generación y token de partida"
                raise ValueError(msg)
            digest = _digest(pid, str(generation), start_token)
        case RunMode.WATERMARK:
            if window_start is None or window_end is None:
                msg = "Un lote WATERMARK necesita la ventana"
                raise ValueError(msg)
            digest = _digest(pid, window_start.isoformat(), window_end.isoformat())
        case RunMode.DELTA_INIT:
            if generation is None or run_id is None:
                msg = "Un lote DELTA_INIT necesita generación y run"
                raise ValueError(msg)
            digest = _digest(pid, str(generation), str(run_id))
        case RunMode.FULL:
            if run_id is None:
                msg = "Un lote FULL necesita el run"
                raise ValueError(msg)
            digest = _digest(pid, str(run_id))
    return f"{_PREFIX[mode]}-{digest}"


def is_batch_id(value: str) -> bool:
    return BATCH_ID_PATTERN.match(value) is not None


@dataclass(frozen=True, kw_only=True)
class BatchInfo:
    """Lo que el destino necesita saber del lote al aplicarlo."""

    pipeline_id: uuid.UUID
    run_id: uuid.UUID
    batch_id: str
    mode: RunMode
    schema_hash: str
    next_token: str | None = None
    window_end: datetime | None = None

    def __post_init__(self) -> None:
        if not is_batch_id(self.batch_id):
            msg = f"batch_id no válido: {self.batch_id!r}"
            raise ValueError(msg)
