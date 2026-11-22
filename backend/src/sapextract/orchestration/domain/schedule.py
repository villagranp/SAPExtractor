"""Programación de un pipeline (plan 08 §2 y §6): cron, intervalo, ventanas de exclusión.

Todo el cálculo es puro: recibe el instante de referencia y devuelve instantes en UTC.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from croniter.croniter import CroniterError

from sapextract.core.exceptions import BusinessRuleError, FieldIssue


class ScheduleType(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    MANUAL_ONLY = "manual_only"
    # Sin reloj: se lanza cuando otro pipeline termina con éxito (plan 08 §6).
    DEPENDENCY = "dependency"


class MisfirePolicy(StrEnum):
    """Qué hacer con un disparo que llega tarde (scheduler caído o pipeline sin hueco)."""

    SKIP = "skip"
    RUN_ONCE = "run_once"


class SkipReason(StrEnum):
    BLACKOUT = "blackout"
    MISFIRE = "misfire"
    ALREADY_LIVE = "already_live"


class BlackoutKind(StrEnum):
    WEEKLY = "weekly"  # se repite cada semana, en la zona horaria de la programación
    RANGE = "range"  # entre dos instantes (cierre contable, ventana de backup)


MAX_INTERVAL_MINUTES = 31 * 24 * 60
MAX_BLACKOUT_WINDOWS = 20
MAX_SCHEDULES = 5
PREVIEW_COUNT = 10
_MAX_SEARCH = 2000  # disparos examinados como máximo al saltar ventanas de exclusión
_CRON_FIELDS = 5


def _issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name.strip())
    except ZoneInfoNotFoundError, ValueError:
        msg = f"Zona horaria desconocida: {name}."
        raise _issue("timezone", msg, "SX-SCHED-TIMEZONE-INVALID") from None


def _aware(value: datetime, fieldname: str) -> datetime:
    if value.tzinfo is None:
        msg = "Indique la fecha con su zona horaria (ISO 8601)."
        raise _issue(fieldname, msg, "SX-SCHED-DATE-NAIVE")
    return value.astimezone(UTC)


@dataclass(frozen=True, kw_only=True)
class BlackoutWindow:
    """Ventana en la que no se lanzan ejecuciones programadas."""

    kind: BlackoutKind
    label: str | None = None
    # weekly: días (0 = lunes) en los que empieza la ventana y horas locales; puede cruzar
    # la medianoche (de 22:00 a 02:00).
    days: tuple[int, ...] = ()
    start: time | None = None
    end: time | None = None
    # range
    start_at: datetime | None = None
    end_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind is BlackoutKind.WEEKLY:
            if self.start is None or self.end is None or self.start == self.end:
                msg = "Una ventana semanal necesita hora de inicio y de fin distintas."
                raise _issue("blackout_windows", msg, "SX-SCHED-BLACKOUT-INVALID")
            if not self.days or any(not 0 <= d <= 6 for d in self.days):
                msg = "Indique los días de la ventana (0 = lunes … 6 = domingo)."
                raise _issue("blackout_windows", msg, "SX-SCHED-BLACKOUT-INVALID")
        elif self.start_at is None or self.end_at is None or self.end_at <= self.start_at:
            msg = "Una ventana por fechas necesita un inicio anterior a su fin."
            raise _issue("blackout_windows", msg, "SX-SCHED-BLACKOUT-INVALID")

    def contains(self, moment: datetime, tz: ZoneInfo) -> bool:
        if self.kind is BlackoutKind.RANGE:
            if self.start_at is None or self.end_at is None:  # lo impide __post_init__
                return False
            return self.start_at <= moment < self.end_at
        if self.start is None or self.end is None:  # lo impide __post_init__
            return False
        local = moment.astimezone(tz)
        day, clock = local.weekday(), local.time().replace(tzinfo=None)
        if self.start < self.end:
            return day in self.days and self.start <= clock < self.end
        # Cruza la medianoche: la parte de madrugada pertenece al día anterior.
        return (day in self.days and clock >= self.start) or (
            (day - 1) % 7 in self.days and clock < self.end
        )

    def to_dict(self) -> dict[str, Any]:
        if self.kind is BlackoutKind.WEEKLY:
            return {
                "kind": self.kind.value,
                "label": self.label,
                "days": list(self.days),
                "start": self.start.strftime("%H:%M") if self.start else None,
                "end": self.end.strftime("%H:%M") if self.end else None,
            }
        return {
            "kind": self.kind.value,
            "label": self.label,
            "start_at": self.start_at.isoformat() if self.start_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlackoutWindow:
        try:
            kind = BlackoutKind(data.get("kind", ""))
            if kind is BlackoutKind.WEEKLY:
                return cls(
                    kind=kind,
                    label=data.get("label"),
                    days=tuple(sorted({int(d) for d in data.get("days", ())})),
                    start=time.fromisoformat(str(data.get("start"))),
                    end=time.fromisoformat(str(data.get("end"))),
                )
            return cls(
                kind=kind,
                label=data.get("label"),
                start_at=_aware(datetime.fromisoformat(str(data.get("start_at"))), "blackout"),
                end_at=_aware(datetime.fromisoformat(str(data.get("end_at"))), "blackout"),
            )
        except ValueError, TypeError:
            msg = "Ventana de exclusión no válida."
            raise _issue("blackout_windows", msg, "SX-SCHED-BLACKOUT-INVALID") from None


@dataclass(frozen=True, kw_only=True)
class ScheduleSpec:
    type: ScheduleType
    timezone: str = "UTC"
    cron_expression: str | None = None
    interval_minutes: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    blackout_windows: tuple[BlackoutWindow, ...] = field(default_factory=tuple)
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP
    # Tipo ``dependency``: el pipeline cuyas ejecuciones correctas lanzan las de este.
    upstream_pipeline_id: uuid.UUID | None = None

    @property
    def tz(self) -> ZoneInfo:
        return zone(self.timezone)

    def blackout_at(self, moment: datetime) -> BlackoutWindow | None:
        tz = self.tz
        return next((w for w in self.blackout_windows if w.contains(moment, tz)), None)


def check_schedule(spec: ScheduleSpec) -> ScheduleSpec:
    """Valida y normaliza una programación. Lanza ``BusinessRuleError`` con el campo afectado."""
    tz = zone(spec.timezone)
    start_at = _aware(spec.start_at, "start_at") if spec.start_at else None
    end_at = _aware(spec.end_at, "end_at") if spec.end_at else None
    if start_at and end_at and end_at <= start_at:
        msg = "La fecha de fin debe ser posterior a la de inicio."
        raise _issue("end_at", msg, "SX-SCHED-RANGE-INVALID")
    if len(spec.blackout_windows) > MAX_BLACKOUT_WINDOWS:
        msg = f"Como máximo {MAX_BLACKOUT_WINDOWS} ventanas de exclusión."
        raise _issue("blackout_windows", msg, "SX-SCHED-BLACKOUT-INVALID")

    if spec.type is ScheduleType.DEPENDENCY:
        if spec.upstream_pipeline_id is None:
            msg = "Elija el pipeline tras el que se ejecuta este."
            raise _issue("upstream_pipeline_id", msg, "SX-SCHED-UPSTREAM-REQUIRED")
        # Sin reloj: ni fechas de vigencia ni política de atraso. Las exclusiones sí valen.
        return ScheduleSpec(
            type=spec.type,
            timezone=tz.key,
            blackout_windows=spec.blackout_windows,
            upstream_pipeline_id=spec.upstream_pipeline_id,
        )

    cron: str | None = None
    interval: int | None = None
    if spec.type is ScheduleType.CRON:
        cron = " ".join((spec.cron_expression or "").split())
        if len(cron.split(" ")) != _CRON_FIELDS or not croniter.is_valid(cron):
            msg = (
                "Expresión cron no válida: use cinco campos "
                "(minuto, hora, día del mes, mes, día de la semana)."
            )
            raise _issue("cron_expression", msg, "SX-SCHED-CRON-INVALID")
        try:
            croniter(cron, datetime.now(tz)).get_next(datetime)
        except CroniterError:
            msg = "La expresión cron no llega a cumplirse nunca (p. ej. 31 de febrero)."
            raise _issue("cron_expression", msg, "SX-SCHED-CRON-INVALID") from None
    elif spec.type is ScheduleType.INTERVAL:
        interval = spec.interval_minutes
        if interval is None or not 1 <= interval <= MAX_INTERVAL_MINUTES:
            msg = f"El intervalo debe estar entre 1 y {MAX_INTERVAL_MINUTES} minutos."
            raise _issue("interval_minutes", msg, "SX-SCHED-INTERVAL-INVALID")
    return ScheduleSpec(
        type=spec.type,
        timezone=tz.key,
        cron_expression=cron,
        interval_minutes=interval,
        start_at=start_at,
        end_at=end_at,
        blackout_windows=spec.blackout_windows,
        misfire_policy=spec.misfire_policy,
    )


def check_schedule_set(specs: Sequence[ScheduleSpec]) -> None:
    """Reglas del conjunto de programaciones de un pipeline.

    Depender de otro pipeline excluye cualquier otra programación: quien decide cuándo corre
    es el pipeline anterior, y solo uno (plan 08 §6).
    """
    if len(specs) > MAX_SCHEDULES:
        msg = f"Un pipeline admite como máximo {MAX_SCHEDULES} programaciones."
        raise _issue("schedules", msg, "SX-SCHED-TOO-MANY")
    dependent = any(s.type is ScheduleType.DEPENDENCY for s in specs)
    if dependent and len(specs) > 1:
        msg = (
            "Un pipeline que se ejecuta después de otro no admite más programaciones: "
            "deje solo esa o quítela."
        )
        raise _issue("schedules", msg, "SX-SCHED-DEPENDENCY-EXCLUSIVE")


def check_dependency[K: Hashable](
    pipeline: K, upstream: K, upstream_of: Callable[[K], K | None]
) -> None:
    """Un pipeline no depende de sí mismo ni cierra un ciclo (A tras B, B tras A).

    ``upstream_of`` devuelve el pipeline anterior de otro, o ``None`` si no depende de nadie.
    """
    seen = {pipeline}
    current: K | None = upstream
    while current is not None:
        if current in seen:
            msg = (
                "No puede ejecutarse después de sí mismo."
                if upstream == pipeline
                else "Esa dependencia cierra un ciclo: el pipeline elegido ya se ejecuta, "
                "directa o indirectamente, después de este."
            )
            raise _issue("upstream_pipeline_id", msg, "SX-SCHED-DEPENDENCY-CYCLE")
        seen.add(current)
        current = upstream_of(current)


def _fixed_hour(cron: str) -> bool:
    """El campo de hora no es un comodín: el trabajo corre a horas concretas del día."""
    return not cron.split(" ")[1].startswith("*")


def _next_cron(spec: ScheduleSpec, after: datetime) -> datetime | None:
    cron = spec.cron_expression or ""
    iterator = croniter(cron, after.astimezone(spec.tz))
    for _ in range(4):
        try:
            candidate: datetime = iterator.get_next(datetime)
        except CroniterError:
            return None
        # Cambio de hora de otoño: la hora repetida no vuelve a lanzar un trabajo de hora fija.
        if candidate.fold and _fixed_hour(cron):
            continue
        return candidate.astimezone(UTC)
    return None


def _next_interval(spec: ScheduleSpec, after: datetime, anchor: datetime) -> datetime:
    step = timedelta(minutes=spec.interval_minutes or 1)
    if after < anchor:
        return anchor
    periods = (after - anchor) // step + 1
    return anchor + periods * step


def next_fire(spec: ScheduleSpec, after: datetime, *, anchor: datetime) -> datetime | None:
    """Primer disparo estrictamente posterior a ``after`` (UTC), sin mirar las exclusiones.

    ``anchor`` es el origen de los intervalos cuando no hay ``start_at`` (alta de la programación).
    """
    if spec.type in (ScheduleType.MANUAL_ONLY, ScheduleType.DEPENDENCY):
        return None
    after = after.astimezone(UTC)
    if spec.start_at and after < spec.start_at:
        after = spec.start_at - timedelta(microseconds=1)
    if spec.type is ScheduleType.CRON:
        candidate = _next_cron(spec, after)
    else:
        candidate = _next_interval(spec, after, (spec.start_at or anchor).astimezone(UTC))
    if candidate is None or (spec.end_at and candidate > spec.end_at):
        return None
    return candidate


def upcoming(
    spec: ScheduleSpec, after: datetime, *, anchor: datetime, count: int = PREVIEW_COUNT
) -> list[datetime]:
    """Próximas ejecuciones reales: las que caen en una ventana de exclusión no cuentan."""
    result: list[datetime] = []
    cursor = after
    for _ in range(_MAX_SEARCH):
        if len(result) >= count:
            break
        candidate = next_fire(spec, cursor, anchor=anchor)
        if candidate is None:
            break
        cursor = candidate
        if spec.blackout_at(candidate) is None:
            result.append(candidate)
    return result
