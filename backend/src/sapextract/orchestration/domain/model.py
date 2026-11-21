"""Pipeline: un origen hacia un destino con sus opciones (plan 08 §1).

En la iteración 5 solo existe lo que necesita la ejecución manual; programación, cola con
límites, pausas y clonado llegan en la iteración 6.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions, SchemaEvolution
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics


class ExtractionMode(StrEnum):
    FULL = "FULL"
    DELTA = "DELTA"
    WATERMARK = "WATERMARK"


class PipelineStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
MIN_TIMEOUT_MINUTES, MAX_TIMEOUT_MINUTES, DEFAULT_TIMEOUT_MINUTES = 5, 24 * 60, 240
MIN_ATTEMPTS, MAX_ATTEMPTS, DEFAULT_ATTEMPTS = 1, 10, 3
# H6/H18 (pendientes del spike): no se sabe si dos cadenas de delta conviven sobre el mismo
# entity set y usuario técnico. Hasta confirmarlo, un segundo pipeline delta sobre el mismo
# entity set se bloquea al crearlo (plan 06 §3.3).
ALLOW_SHARED_DELTA_SOURCE = False


def issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not CODE_PATTERN.match(value):
        msg = (
            "El código debe tener de 3 a 40 caracteres: letras, números y guion bajo, y empezar "
            "por una letra (p. ej. FI_GL_SNOWFLAKE)."
        )
        raise issue("code", msg, "SX-PPL-CODE-INVALID")
    return value


def check_extraction_mode(mode: ExtractionMode, delta_method: DeltaMethod) -> None:
    """El modo del pipeline debe existir en el origen (plan 05: delta ODQ, marca de agua o nada)."""
    allowed = {
        DeltaMethod.ODQ: {ExtractionMode.DELTA, ExtractionMode.FULL},
        DeltaMethod.WATERMARK: {ExtractionMode.WATERMARK, ExtractionMode.FULL},
        DeltaMethod.NONE: {ExtractionMode.FULL},
    }[delta_method]
    if mode not in allowed:
        msg = {
            ExtractionMode.DELTA: "El origen no tiene delta ODQ: use carga completa.",
            ExtractionMode.WATERMARK: "El origen no tiene marca de agua configurada.",
            ExtractionMode.FULL: "",
        }[mode]
        raise issue("extraction_mode", msg, "SX-PPL-MODE-UNSUPPORTED")


def check_load_mode(
    mode: ExtractionMode, load_mode: LoadMode, semantics: DeltaSemantics | None
) -> None:
    """Combinaciones peligrosas bloqueadas (plan 06 §3.4).

    * ``upsert`` sobre un origen aditivo o sin clasificar sumaría o mezclaría imágenes.
    * ``replace`` solo tiene sentido en una carga completa.
    """
    if load_mode is LoadMode.REPLACE and mode is not ExtractionMode.FULL:
        msg = "«Reemplazar» solo se usa con carga completa: un delta borraría el resto de filas."
        raise issue("load_options.load_mode", msg, "SX-PPL-LOAD-MODE")
    if (
        mode is ExtractionMode.DELTA
        and load_mode is LoadMode.UPSERT
        and semantics is not DeltaSemantics.AFTER_IMAGE
    ):
        msg = (
            "«Upsert» solo es seguro si el origen entrega imágenes posteriores (after image). "
            "En orígenes aditivos o sin clasificar use «Añadir» y consolide aguas abajo."
        )
        raise issue("load_options.load_mode", msg, "SX-PPL-LOAD-MODE")


def check_timeout(value: int | None) -> int:
    minutes = DEFAULT_TIMEOUT_MINUTES if value is None else value
    if not MIN_TIMEOUT_MINUTES <= minutes <= MAX_TIMEOUT_MINUTES:
        msg = (
            f"El tiempo máximo debe estar entre {MIN_TIMEOUT_MINUTES} y {MAX_TIMEOUT_MINUTES} min."
        )
        raise issue("timeout_minutes", msg, "SX-PPL-TIMEOUT-INVALID")
    return minutes


def check_attempts(value: int | None) -> int:
    attempts = DEFAULT_ATTEMPTS if value is None else value
    if not MIN_ATTEMPTS <= attempts <= MAX_ATTEMPTS:
        msg = f"Los intentos deben estar entre {MIN_ATTEMPTS} y {MAX_ATTEMPTS}."
        raise issue("max_attempts", msg, "SX-PPL-ATTEMPTS-INVALID")
    return attempts


def load_options_from_json(data: dict[str, Any]) -> LoadOptions:
    """Opciones de carga guardadas en ``pipeline.load_options`` (plan 07 §2)."""
    return LoadOptions(
        load_mode=LoadMode(data["load_mode"]),
        target_name=str(data["target_name"]),
        create_if_missing=bool(data.get("create_if_missing", True)),
        schema_evolution=SchemaEvolution(data.get("schema_evolution", "add_columns")),
        delete_handling=DeleteHandling(data.get("delete_handling", "soft")),
        technical_columns=bool(data.get("technical_columns", True)),
        column_mapping=dict(data.get("column_mapping") or {}),
    )


def load_options_to_json(options: LoadOptions) -> dict[str, Any]:
    return {
        "load_mode": options.load_mode.value,
        "target_name": options.target_name,
        "create_if_missing": options.create_if_missing,
        "schema_evolution": options.schema_evolution.value,
        "delete_handling": options.delete_handling.value,
        "technical_columns": options.technical_columns,
        "column_mapping": dict(options.column_mapping),
    }
