"""Dominio de la orquestación (plan 08): reintentos, avisos de configuración y programación."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from sapextract.core.exceptions import BusinessRuleError
from sapextract.dataflow.domain.batch import LoadMode, RunMode
from sapextract.orchestration.domain.model import (
    ExtractionMode,
    PipelineFacts,
    RetryPolicy,
    Severity,
    check_group_limit,
    check_manual_mode,
    check_priority,
    check_timeout,
    has_errors,
    normalize_code,
    normalize_group_code,
    normalize_pool,
    review,
    run_mode_for,
    trial_fingerprint,
)
from sapextract.orchestration.domain.ports import RunContext, RunStoppedError, StopReason
from sapextract.orchestration.domain.schedule import (
    BlackoutKind,
    BlackoutWindow,
    MisfirePolicy,
    ScheduleSpec,
    ScheduleType,
    check_schedule,
    next_fire,
    upcoming,
)
from sapextract.sources.domain.model import DeltaMethod, DeltaSemantics

ALL_MODES = frozenset(LoadMode)
ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)


def _code(exc: pytest.ExceptionInfo[BusinessRuleError]) -> str:
    return exc.value.error_code


# ====================================================================== normalización
def test_normalizers_accept_and_reject() -> None:
    assert normalize_code(" fi_gl_snow ") == "FI_GL_SNOW"
    assert normalize_pool(None) == "default"
    assert normalize_pool(" PRD ") == "prd"
    assert normalize_group_code("sap_prd") == "SAP_PRD"
    assert check_priority(1) == 1
    assert check_timeout(2880) == 2880
    assert check_group_limit(2) == 2
    for call, code in (
        (lambda: normalize_code("x"), "SX-PIPE-CODE-INVALID"),
        (lambda: normalize_pool("con espacios"), "SX-PIPE-POOL-INVALID"),
        (lambda: normalize_group_code("1"), "SX-PIPE-GROUP-INVALID"),
        (lambda: check_priority(6), "SX-PIPE-PRIORITY-INVALID"),
        (lambda: check_timeout(0), "SX-PIPE-TIMEOUT-INVALID"),
        (lambda: check_group_limit(0), "SX-PIPE-GROUP-LIMIT"),
    ):
        with pytest.raises(BusinessRuleError) as exc:
            call()
        assert _code(exc) == code


# ====================================================================== reintentos
def test_retry_backoff_grows_and_stays_on_the_last_step() -> None:
    policy = RetryPolicy(jitter=False)
    assert [policy.delay(n).total_seconds() for n in (1, 2, 3, 4, 9)] == [60, 300, 900, 900, 900]


def test_retry_jitter_adds_up_to_twenty_percent() -> None:
    policy = RetryPolicy(max_attempts=2, backoff_minutes=(10,))
    assert policy.delay(1, 0.0) == timedelta(minutes=10)
    assert policy.delay(1, 0.5) == timedelta(minutes=11)
    assert policy.delay(1, 0.999) < timedelta(minutes=12)


def test_retry_policy_round_trip_and_validation() -> None:
    policy = RetryPolicy(max_attempts=5, backoff_minutes=(2, 4), jitter=False)
    assert RetryPolicy.from_dict(policy.to_dict()) == policy
    assert RetryPolicy.from_dict(None) == RetryPolicy()
    for kwargs in (
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"backoff_minutes": ()},
        {"backoff_minutes": (5000,)},
    ):
        with pytest.raises(BusinessRuleError) as exc:
            RetryPolicy(**kwargs)
        assert _code(exc) == "SX-PIPE-RETRY-INVALID"


# ====================================================================== modos
def test_pipeline_mode_maps_to_run_mode() -> None:
    assert run_mode_for(ExtractionMode.FULL) is RunMode.FULL
    assert run_mode_for(ExtractionMode.DELTA) is RunMode.DELTA
    assert run_mode_for(ExtractionMode.WATERMARK) is RunMode.WATERMARK


def test_manual_backfill_needs_permission_and_after_image() -> None:
    after = DeltaSemantics.AFTER_IMAGE
    assert (
        check_manual_mode(None, ExtractionMode.DELTA, delta_semantics=after, can_manage=False)
        is RunMode.DELTA
    )
    assert (
        check_manual_mode(
            RunMode.FULL, ExtractionMode.DELTA, delta_semantics=after, can_manage=True
        )
        is RunMode.FULL
    )
    assert (
        check_manual_mode(
            RunMode.FULL, ExtractionMode.WATERMARK, delta_semantics=None, can_manage=True
        )
        is RunMode.FULL
    )
    cases = (
        (RunMode.FULL, after, False, "SX-RUN-BACKFILL-FORBIDDEN"),
        (RunMode.FULL, DeltaSemantics.ADDITIVE, True, "SX-RUN-BACKFILL-UNSAFE"),
        (RunMode.DELTA_INIT, after, True, "SX-RUN-MODE-INVALID"),
    )
    for mode, semantics, can_manage, code in cases:
        with pytest.raises(BusinessRuleError) as exc:
            check_manual_mode(
                mode, ExtractionMode.DELTA, delta_semantics=semantics, can_manage=can_manage
            )
        assert _code(exc) == code


# ====================================================================== avisos
def _facts(**overrides: object) -> PipelineFacts:
    values: dict[str, object] = {
        "mode": ExtractionMode.DELTA,
        "load_mode": LoadMode.UPSERT,
        "supported_modes": ALL_MODES,
        "delta_method": DeltaMethod.ODQ,
        "delta_semantics": DeltaSemantics.AFTER_IMAGE,
        "source_environment": "DEV",
        "destination_environment": "DEV",
    }
    values.update(overrides)
    return PipelineFacts(**values)  # type: ignore[arg-type]


def _codes(facts: PipelineFacts, severity: Severity) -> set[str]:
    return {f.code for f in review(facts) if f.severity is severity}


def test_clean_delta_upsert_has_no_findings() -> None:
    assert review(_facts()) == []


def test_review_blocks_dangerous_combinations() -> None:
    assert _codes(_facts(supported_modes=frozenset({LoadMode.APPEND})), Severity.ERROR) == {
        "SX-PIPE-LOAD-MODE-UNSUPPORTED"
    }
    assert _codes(_facts(delta_method=DeltaMethod.NONE), Severity.ERROR) == {"SX-PIPE-NO-DELTA"}
    assert _codes(
        _facts(mode=ExtractionMode.WATERMARK, delta_method=DeltaMethod.NONE), Severity.ERROR
    ) == {"SX-PIPE-NO-WATERMARK"}
    assert _codes(_facts(load_mode=LoadMode.REPLACE), Severity.ERROR) == {
        "SX-PIPE-REPLACE-INCREMENTAL"
    }
    for semantics in (DeltaSemantics.ADDITIVE, DeltaSemantics.BEFORE_AFTER, DeltaSemantics.UNKNOWN):
        assert _codes(_facts(delta_semantics=semantics), Severity.ERROR) == {
            "SX-PIPE-UPSERT-NOT-AFTER-IMAGE"
        }
    shared = _facts(other_delta_pipelines=("FI_GL_CSV",))
    assert _codes(shared, Severity.ERROR) == {"SX-PIPE-DELTA-SHARED"}
    assert has_errors(review(shared))


def test_review_warns_without_blocking() -> None:
    full_append = _facts(
        mode=ExtractionMode.FULL, load_mode=LoadMode.APPEND, delta_method=DeltaMethod.NONE
    )
    assert _codes(full_append, Severity.WARNING) == {"SX-PIPE-FULL-APPEND"}
    assert _codes(_facts(load_mode=LoadMode.APPEND), Severity.WARNING) == {
        "SX-PIPE-APPEND-AFTER-IMAGE"
    }
    cross = _facts(source_environment="QAS", destination_environment="PRD")
    assert _codes(cross, Severity.WARNING) == {"SX-PIPE-CROSS-ENVIRONMENT"}
    assert not has_errors(review(cross))
    # Un segundo pipeline sobre el mismo origen sí se admite si no es delta.
    second = _facts(
        mode=ExtractionMode.FULL, load_mode=LoadMode.REPLACE, other_delta_pipelines=("OTRO",)
    )
    assert review(second) == []


# ====================================================================== puerto del ejecutor
def test_run_context_checkpoint_raises_with_the_reason() -> None:
    reason: list[StopReason | None] = [None]
    context = RunContext(
        run_id=uuid.uuid4(),
        pipeline_id=uuid.uuid4(),
        mode=RunMode.FULL,
        attempt=1,
        lease_epoch=1,
        worker_id=uuid.uuid4(),
        stop_reason=lambda: reason[0],
    )
    context.checkpoint()
    assert context.is_cancelled() is False
    reason[0] = StopReason.CANCELLED
    assert context.is_cancelled() is True
    with pytest.raises(RunStoppedError) as exc:
        context.checkpoint()
    assert exc.value.reason is StopReason.CANCELLED


# ====================================================================== programación
def _cron(expression: str, timezone: str = "UTC", **extra: object) -> ScheduleSpec:
    return check_schedule(
        ScheduleSpec(
            type=ScheduleType.CRON,
            cron_expression=expression,
            timezone=timezone,
            **extra,  # type: ignore[arg-type]
        )
    )


def test_every_thirty_minutes_on_working_days() -> None:
    spec = _cron("*/30 * * * 1-5", "America/Guatemala")
    friday_evening = datetime(2026, 11, 20, 23, 45, tzinfo=UTC)  # viernes 17:45 hora local
    fires = upcoming(spec, friday_evening, anchor=ANCHOR, count=13)
    assert fires[0] == datetime(2026, 11, 21, 0, 0, tzinfo=UTC)  # viernes 18:00 local
    assert fires[11] == datetime(2026, 11, 21, 5, 30, tzinfo=UTC)  # viernes 23:30 local
    assert fires[12] == datetime(2026, 11, 23, 6, 0, tzinfo=UTC)  # lunes 00:00 local
    assert all(f.astimezone(spec.tz).weekday() < 5 for f in fires)


def test_cron_validation() -> None:
    assert _cron("  0   6 * * *  ").cron_expression == "0 6 * * *"
    for expression in ("", "61 * * * *", "* * * * * *", "@daily", "0 0 31 2 *"):
        with pytest.raises(BusinessRuleError) as exc:
            _cron(expression)
        assert _code(exc) == "SX-SCHED-CRON-INVALID"
    with pytest.raises(BusinessRuleError) as exc:
        _cron("0 6 * * *", "Marte/Olympus")
    assert _code(exc) == "SX-SCHED-TIMEZONE-INVALID"


def test_spring_forward_runs_once_at_the_next_valid_time() -> None:
    spec = _cron("30 2 * * *", "Europe/Madrid")  # el 29.03.2026 las 02:30 no existen
    fires = upcoming(spec, datetime(2026, 3, 28, 12, tzinfo=UTC), anchor=ANCHOR, count=2)
    assert fires == [
        datetime(2026, 3, 29, 1, 0, tzinfo=UTC),  # 03:00 hora local, ya en horario de verano
        datetime(2026, 3, 30, 0, 30, tzinfo=UTC),
    ]


def test_fall_back_does_not_repeat_a_fixed_hour_job() -> None:
    spec = _cron("30 2 * * *", "Europe/Madrid")  # el 25.10.2026 las 02:30 ocurren dos veces
    fires = upcoming(spec, datetime(2026, 10, 24, 12, tzinfo=UTC), anchor=ANCHOR, count=2)
    assert fires == [
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 26, 1, 30, tzinfo=UTC),
    ]


def test_fall_back_keeps_every_real_hour_of_an_hourly_job() -> None:
    spec = _cron("0 * * * *", "Europe/Madrid")
    fires = upcoming(spec, datetime(2026, 10, 24, 22, 30, tzinfo=UTC), anchor=ANCHOR, count=4)
    first = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)  # 01:00 hora local del día 25
    # 01:00, 02:00 (verano), 02:00 (invierno) y 03:00: cuatro horas reales seguidas.
    assert fires == [first + timedelta(hours=h) for h in range(4)]


def test_interval_is_anchored_and_respects_start_and_end() -> None:
    spec = check_schedule(ScheduleSpec(type=ScheduleType.INTERVAL, interval_minutes=45))
    anchor = datetime(2026, 5, 1, 10, 10, tzinfo=UTC)
    assert next_fire(spec, datetime(2026, 5, 1, 9, tzinfo=UTC), anchor=anchor) == anchor
    assert next_fire(spec, anchor, anchor=anchor) == anchor + timedelta(minutes=45)
    assert next_fire(spec, anchor + timedelta(minutes=100), anchor=anchor) == anchor + timedelta(
        minutes=135
    )

    bounded = check_schedule(
        ScheduleSpec(
            type=ScheduleType.INTERVAL,
            interval_minutes=60,
            start_at=datetime(2026, 6, 1, 8, tzinfo=UTC),
            end_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
        )
    )
    fires = upcoming(bounded, datetime(2026, 5, 30, tzinfo=UTC), anchor=anchor)
    assert fires == [datetime(2026, 6, 1, h, tzinfo=UTC) for h in (8, 9, 10)]
    assert next_fire(bounded, datetime(2026, 6, 1, 10, tzinfo=UTC), anchor=anchor) is None


def test_manual_only_never_fires() -> None:
    spec = check_schedule(ScheduleSpec(type=ScheduleType.MANUAL_ONLY))
    assert next_fire(spec, ANCHOR, anchor=ANCHOR) is None
    assert upcoming(spec, ANCHOR, anchor=ANCHOR) == []


def test_schedule_validation_errors() -> None:
    cases: tuple[tuple[ScheduleSpec, str], ...] = (
        (ScheduleSpec(type=ScheduleType.INTERVAL), "SX-SCHED-INTERVAL-INVALID"),
        (
            ScheduleSpec(type=ScheduleType.INTERVAL, interval_minutes=10**6),
            "SX-SCHED-INTERVAL-INVALID",
        ),
        (
            ScheduleSpec(
                type=ScheduleType.MANUAL_ONLY,
                start_at=datetime(2026, 1, 1),  # noqa: DTZ001
            ),
            "SX-SCHED-DATE-NAIVE",
        ),
        (
            ScheduleSpec(
                type=ScheduleType.MANUAL_ONLY,
                start_at=datetime(2026, 2, 1, tzinfo=UTC),
                end_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "SX-SCHED-RANGE-INVALID",
        ),
    )
    for spec, code in cases:
        with pytest.raises(BusinessRuleError) as exc:
            check_schedule(spec)
        assert _code(exc) == code


def test_weekly_blackout_crossing_midnight() -> None:
    backup = BlackoutWindow(
        kind=BlackoutKind.WEEKLY, days=(5,), start=time(22, 0), end=time(2, 0), label="Backup"
    )
    spec = _cron("0 * * * *", "America/Guatemala", blackout_windows=(backup,))
    saturday_21 = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)  # sábado 21:00 local
    assert spec.blackout_at(saturday_21) is None
    assert spec.blackout_at(saturday_21 + timedelta(hours=1)) is backup  # sábado 22:00
    assert spec.blackout_at(saturday_21 + timedelta(hours=4)) is backup  # domingo 01:00
    assert spec.blackout_at(saturday_21 + timedelta(hours=5)) is None  # domingo 02:00
    # El domingo por la noche no hay ventana: empieza solo los sábados.
    assert spec.blackout_at(saturday_21 + timedelta(hours=25)) is None
    fires = upcoming(spec, saturday_21 - timedelta(minutes=1), anchor=ANCHOR, count=3)
    assert fires == [
        saturday_21,
        saturday_21 + timedelta(hours=5),
        saturday_21 + timedelta(hours=6),
    ]


def test_range_blackout_and_round_trip() -> None:
    closing = BlackoutWindow(
        kind=BlackoutKind.RANGE,
        start_at=datetime(2026, 12, 31, 18, tzinfo=UTC),
        end_at=datetime(2027, 1, 2, 6, tzinfo=UTC),
        label="Cierre contable",
    )
    spec = _cron("0 12 * * *", blackout_windows=(closing,), misfire_policy=MisfirePolicy.RUN_ONCE)
    fires = upcoming(spec, datetime(2026, 12, 30, 13, tzinfo=UTC), anchor=ANCHOR, count=2)
    assert fires == [datetime(2026, 12, 31, 12, tzinfo=UTC), datetime(2027, 1, 2, 12, tzinfo=UTC)]
    assert BlackoutWindow.from_dict(closing.to_dict()) == closing
    weekly = BlackoutWindow(kind=BlackoutKind.WEEKLY, days=(0, 4), start=time(8), end=time(9))
    assert BlackoutWindow.from_dict(weekly.to_dict()) == weekly


def test_blackout_validation() -> None:
    bad: tuple[dict[str, object], ...] = (
        {"kind": "weekly", "days": [1], "start": "08:00", "end": "08:00"},
        {"kind": "weekly", "days": [], "start": "08:00", "end": "09:00"},
        {"kind": "weekly", "days": [9], "start": "08:00", "end": "09:00"},
        {
            "kind": "range",
            "start_at": "2026-01-02T00:00:00+00:00",
            "end_at": "2026-01-01T00:00+00:00",
        },
        {"kind": "range", "start_at": "ayer", "end_at": "hoy"},
        {"kind": "otra"},
    )
    for data in bad:
        with pytest.raises(BusinessRuleError) as exc:
            BlackoutWindow.from_dict(dict(data))
        assert _code(exc) in {"SX-SCHED-BLACKOUT-INVALID", "SX-SCHED-DATE-NAIVE"}
    with pytest.raises(BusinessRuleError) as exc:
        _cron(
            "0 * * * *",
            blackout_windows=tuple(
                BlackoutWindow(kind=BlackoutKind.WEEKLY, days=(0,), start=time(1), end=time(2))
                for _ in range(21)
            ),
        )
    assert _code(exc) == "SX-SCHED-BLACKOUT-INVALID"


# ---------------------------------------------------------------------- ejecución de prueba
def test_fingerprint_follows_what_a_trial_checks() -> None:
    base: dict[str, Any] = {
        "source_id": "s1",
        "destination_id": "d1",
        "extraction_mode": "DELTA",
        "load_options": {"load_mode": "append", "target_name": "GL_ITEMS"},
        "column_mapping": {"BUKRS": "SOCIEDAD", "WAERS": None},
        "filters": [{"field": "BUKRS", "operator": "eq", "value": "1000"}],
    }
    fingerprint = trial_fingerprint(**base)
    assert len(fingerprint) == 64
    # El orden de las claves no cambia la huella.
    reordered = {
        **base,
        "load_options": {"target_name": "GL_ITEMS", "load_mode": "append"},
        "column_mapping": {"WAERS": None, "BUKRS": "SOCIEDAD"},
    }
    assert trial_fingerprint(**reordered) == fingerprint
    changes: dict[str, Any] = {
        "source_id": "s2",
        "destination_id": "d2",
        "extraction_mode": "FULL",
        "load_options": {"load_mode": "append", "target_name": "OTRA"},
        "column_mapping": {"BUKRS": "SOCIEDAD"},
        "filters": [],
    }
    for name, value in changes.items():
        assert trial_fingerprint(**{**base, name: value}) != fingerprint, name
