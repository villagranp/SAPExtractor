"""Dominio de la orquestación (plan 08): pipeline, run, reintentos y avisos de configuración."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.batch import LoadMode, RunMode
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics


class PipelineStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class ExtractionMode(StrEnum):
    FULL = "FULL"
    DELTA = "DELTA"
    WATERMARK = "WATERMARK"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


LIVE_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    API = "api"
    DEPENDENCY = "dependency"  # v1.1


class AttemptOutcome(StrEnum):
    """Cómo terminó un intento (ADR 0006 §5)."""

    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED = "failed"
    STALE = "stale"
    FENCED = "fenced"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


# Intentos que gastan el presupuesto de reintentos. Un apagado ordenado no lo gasta; un
# intento sin latido sí, para que un run que tumba al worker no se repita sin fin.
CONSUMING_OUTCOMES = (AttemptOutcome.FAILED_TRANSIENT.value, AttemptOutcome.STALE.value)


class WorkerStatus(StrEnum):
    ONLINE = "online"
    STOPPING = "stopping"
    OFFLINE = "offline"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
POOL_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,29}$")
GROUP_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,29}$")
DEFAULT_POOL = "default"
DEFAULT_PRIORITY = 3
MANUAL_PRIORITY = 1
MIN_PRIORITY, MAX_PRIORITY = 1, 5
DEFAULT_TIMEOUT_MINUTES = 120
MIN_TIMEOUT_MINUTES, MAX_TIMEOUT_MINUTES = 1, 2880
MAX_ATTEMPTS = 10
MAX_BACKOFF_MINUTES = 1440
MAX_GROUP_PARALLEL = 64
JITTER_FRACTION = 0.2

# Códigos de error que pone la orquestación (los del motor son SX-SAP-*, SX-DST-*, SX-SRC-*).
ERROR_STALE = "SX-RUN-STALE"
ERROR_TIMEOUT = "SX-RUN-TIMEOUT"
ERROR_UNEXPECTED = "SX-RUN-UNEXPECTED"


def issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not CODE_PATTERN.match(value):
        msg = "El código debe tener de 3 a 40 caracteres: letras, números y guion bajo."
        raise issue("code", msg, "SX-PIPE-CODE-INVALID")
    return value


def normalize_pool(pool: str | None) -> str:
    value = (pool or DEFAULT_POOL).strip().lower()
    if not POOL_PATTERN.match(value):
        msg = "El pool de workers admite minúsculas, números, guion y guion bajo (máx. 30)."
        raise issue("worker_pool", msg, "SX-PIPE-POOL-INVALID")
    return value


def normalize_group_code(code: str) -> str:
    value = code.strip().upper()
    if not GROUP_PATTERN.match(value):
        msg = "El código del grupo debe tener de 2 a 30 caracteres: letras, números y guion bajo."
        raise issue("code", msg, "SX-PIPE-GROUP-INVALID")
    return value


def check_priority(priority: int) -> int:
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        msg = f"La prioridad va de {MIN_PRIORITY} (más urgente) a {MAX_PRIORITY}."
        raise issue("priority", msg, "SX-PIPE-PRIORITY-INVALID")
    return priority


def check_timeout(minutes: int) -> int:
    if not MIN_TIMEOUT_MINUTES <= minutes <= MAX_TIMEOUT_MINUTES:
        msg = (
            f"El tiempo máximo de un run debe estar entre {MIN_TIMEOUT_MINUTES} y "
            f"{MAX_TIMEOUT_MINUTES} minutos."
        )
        raise issue("timeout_minutes", msg, "SX-PIPE-TIMEOUT-INVALID")
    return minutes


def check_group_limit(max_parallel: int) -> int:
    if not 1 <= max_parallel <= MAX_GROUP_PARALLEL:
        msg = f"El límite del grupo debe estar entre 1 y {MAX_GROUP_PARALLEL} runs simultáneos."
        raise issue("max_parallel", msg, "SX-PIPE-GROUP-LIMIT")
    return max_parallel


# ---------------------------------------------------------------------- reintentos
@dataclass(frozen=True, kw_only=True)
class RetryPolicy:
    """Reintentos automáticos de un run: solo ante errores transitorios (plan 08 §4)."""

    max_attempts: int = 3
    backoff_minutes: tuple[int, ...] = (1, 5, 15)
    jitter: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            msg = f"Los intentos deben estar entre 1 y {MAX_ATTEMPTS}."
            raise issue("retry_policy.max_attempts", msg, "SX-PIPE-RETRY-INVALID")
        if not self.backoff_minutes or len(self.backoff_minutes) > MAX_ATTEMPTS:
            msg = f"Indique de 1 a {MAX_ATTEMPTS} esperas entre intentos."
            raise issue("retry_policy.backoff_minutes", msg, "SX-PIPE-RETRY-INVALID")
        if any(not 0 <= m <= MAX_BACKOFF_MINUTES for m in self.backoff_minutes):
            msg = f"Cada espera debe estar entre 0 y {MAX_BACKOFF_MINUTES} minutos."
            raise issue("retry_policy.backoff_minutes", msg, "SX-PIPE-RETRY-INVALID")

    def delay(self, consumed: int, random: float = 0.0) -> timedelta:
        """Espera tras el fallo número ``consumed`` (desde 1); ``random`` en [0, 1) da el jitter."""
        index = min(max(consumed, 1), len(self.backoff_minutes)) - 1
        seconds = self.backoff_minutes[index] * 60.0
        if self.jitter:
            seconds *= 1 + JITTER_FRACTION * random
        return timedelta(seconds=seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_minutes": list(self.backoff_minutes),
            "jitter": self.jitter,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        if not data:
            return cls()
        return cls(
            max_attempts=int(data.get("max_attempts", 3)),
            backoff_minutes=tuple(int(m) for m in data.get("backoff_minutes", (1, 5, 15))),
            jitter=bool(data.get("jitter", True)),
        )


# ---------------------------------------------------------------------- modos
def run_mode_for(mode: ExtractionMode) -> RunMode:
    """Modo con el que se encola un run del pipeline.

    Un pipeline ``DELTA`` encola ``DELTA``; el motor lo convierte en ``DELTA_INIT`` en su
    primer intento si el estado de delta aún no está inicializado (plan 06 §3.6).
    """
    return {
        ExtractionMode.FULL: RunMode.FULL,
        ExtractionMode.DELTA: RunMode.DELTA,
        ExtractionMode.WATERMARK: RunMode.WATERMARK,
    }[mode]


def check_manual_mode(
    requested: RunMode | None,
    pipeline_mode: ExtractionMode,
    *,
    delta_semantics: DeltaSemantics | None,
    can_manage: bool,
) -> RunMode:
    """Modo de un run manual. ``FULL`` sobre un pipeline incremental es una recarga completa.

    La recarga exige ``pipeline:manage`` y un origen cuyo ``FULL`` sea seguro: en orígenes
    aditivos duplicaría importes y en los extractores LO leería las *setup tables* (plan 08 §5).
    """
    default = run_mode_for(pipeline_mode)
    if requested is None or requested is default:
        return default
    if requested is not RunMode.FULL:
        msg = "Solo se puede pedir una recarga completa (FULL) distinta del modo del pipeline."
        raise issue("mode", msg, "SX-RUN-MODE-INVALID")
    if not can_manage:
        msg = "La recarga completa de un pipeline incremental exige el permiso pipeline:manage."
        raise issue("mode", msg, "SX-RUN-BACKFILL-FORBIDDEN")
    if pipeline_mode is ExtractionMode.DELTA and delta_semantics is not DeltaSemantics.AFTER_IMAGE:
        msg = (
            "Este origen no admite recarga completa: sus registros de delta no son imágenes "
            "posteriores. Reinicialice el delta."
        )
        raise issue("mode", msg, "SX-RUN-BACKFILL-UNSAFE")
    return RunMode.FULL


# ---------------------------------------------------------------------- ejecución de prueba
def trial_fingerprint(
    *,
    source_id: str,
    destination_id: str,
    extraction_mode: str,
    load_options: Mapping[str, Any],
    column_mapping: Mapping[str, str | None],
    filters: Sequence[Mapping[str, Any]],
) -> str:
    """Huella de lo que una ejecución de prueba comprueba (plan 08 §5).

    Cambiar el origen, el destino, el modo, la carga, el mapeo o los filtros efectivos deja la
    prueba sin vigencia; el nombre, la prioridad o la programación no.
    """
    payload = {
        "source_id": source_id,
        "destination_id": destination_id,
        "extraction_mode": extraction_mode,
        "load_options": dict(load_options),
        "column_mapping": dict(column_mapping),
        "filters": [dict(f) for f in filters],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------- avisos de configuración
class Severity(StrEnum):
    ERROR = "error"  # impide activar
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    field: str
    message: str


@dataclass(frozen=True, kw_only=True)
class PipelineFacts:
    """Lo que hace falta saber de origen, destino y carga para revisar un pipeline."""

    mode: ExtractionMode
    load_mode: LoadMode
    supported_modes: frozenset[LoadMode]
    delta_method: DeltaMethod
    delta_semantics: DeltaSemantics | None
    source_environment: str
    destination_environment: str
    other_delta_pipelines: tuple[str, ...] = ()


def review(facts: PipelineFacts) -> list[Finding]:
    """Avisos y bloqueos de un pipeline (plan 08 §6, plan 06 §3.3 y §3.4). No toca SAP."""
    findings: list[Finding] = []

    def add(severity: Severity, code: str, fieldname: str, message: str) -> None:
        findings.append(Finding(severity, code, fieldname, message))

    if facts.load_mode not in facts.supported_modes:
        add(
            Severity.ERROR,
            "SX-PIPE-LOAD-MODE-UNSUPPORTED",
            "load.load_mode",
            f"El destino no admite el modo de carga «{facts.load_mode.value}».",
        )
    if facts.mode is ExtractionMode.DELTA and facts.delta_method is not DeltaMethod.ODQ:
        add(
            Severity.ERROR,
            "SX-PIPE-NO-DELTA",
            "extraction_mode",
            "El origen no tiene delta ODQ: use carga completa o marca de agua.",
        )
    if facts.mode is ExtractionMode.WATERMARK and facts.delta_method is not DeltaMethod.WATERMARK:
        add(
            Severity.ERROR,
            "SX-PIPE-NO-WATERMARK",
            "extraction_mode",
            "El origen no tiene un campo de marca de agua configurado.",
        )
    incremental = facts.mode is not ExtractionMode.FULL
    if incremental and facts.load_mode is LoadMode.REPLACE:
        add(
            Severity.ERROR,
            "SX-PIPE-REPLACE-INCREMENTAL",
            "load.load_mode",
            "«replace» sustituye toda la tabla en cada carga: en un pipeline incremental "
            "dejaría solo el último lote.",
        )
    if (
        facts.mode is ExtractionMode.DELTA
        and facts.load_mode is LoadMode.UPSERT
        and facts.delta_semantics is not DeltaSemantics.AFTER_IMAGE
    ):
        add(
            Severity.ERROR,
            "SX-PIPE-UPSERT-NOT-AFTER-IMAGE",
            "load.load_mode",
            "El delta de este origen no trae imágenes posteriores: «upsert» sobrescribiría "
            "importes. Use «append» y consolide aguas abajo.",
        )
    if facts.mode is ExtractionMode.DELTA and facts.other_delta_pipelines:
        others = ", ".join(facts.other_delta_pipelines[:3])
        add(
            Severity.ERROR,
            "SX-PIPE-DELTA-SHARED",
            "source_id",
            f"El origen ya tiene un pipeline delta ({others}). Dos suscripciones sobre el "
            "mismo origen siguen bloqueadas hasta validarlas en SAP; use carga completa o "
            "marca de agua para el segundo destino.",
        )
    if facts.mode is ExtractionMode.FULL and facts.load_mode is LoadMode.APPEND:
        add(
            Severity.WARNING,
            "SX-PIPE-FULL-APPEND",
            "load.load_mode",
            "Cada carga completa añade de nuevo todas las filas; con «replace» la tabla "
            "queda con el contenido actual.",
        )
    if (
        facts.mode is ExtractionMode.DELTA
        and facts.load_mode is LoadMode.APPEND
        and facts.delta_semantics is DeltaSemantics.AFTER_IMAGE
    ):
        add(
            Severity.WARNING,
            "SX-PIPE-APPEND-AFTER-IMAGE",
            "load.load_mode",
            "Con «append» el destino guarda el historial de cambios, no el estado actual; "
            "para el estado actual use «upsert».",
        )
    if facts.source_environment != facts.destination_environment:
        add(
            Severity.WARNING,
            "SX-PIPE-CROSS-ENVIRONMENT",
            "destination_id",
            f"El sistema SAP es {facts.source_environment} y el destino es "
            f"{facts.destination_environment}: activar exigirá confirmarlo con un motivo.",
        )
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.ERROR for f in findings)
