from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sapextract.audit.api.openapi import audited
from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.integrity import GENESIS_HASH, canonical_payload, compute_hash
from sapextract.audit.domain.model import category_of
from sapextract.core.masking import MASK
from sapextract.core.ratelimit import SlidingWindowLimiter


def _event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "seq": 1,
        "id": uuid.UUID("01900000-0000-7000-8000-000000000001"),
        "occurred_at": datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
        "actor_type": "user",
        "actor_id": "u1",
        "actor_name": "admin",
        "action": "user.created",
        "category": "identity",
        "resource_type": "user",
        "resource_id": "u2",
        "resource_name": "jperez",
        "outcome": "success",
        "changes": {"display_name": {"before": None, "after": "Juan"}},
        "context": {"ip": "10.0.0.1"},
        "reason": None,
    }
    event.update(overrides)
    return event


def test_hash_is_deterministic_and_key_order_independent() -> None:
    a = _event(changes={"x": 1, "y": 2})
    b = _event(changes={"y": 2, "x": 1})
    assert canonical_payload(a) == canonical_payload(b)
    assert compute_hash(GENESIS_HASH, a) == compute_hash(GENESIS_HASH, b)


TAMPERED: dict[str, object] = {
    "seq": 2,
    "actor_name": "otro",
    "action": "user.updated",
    "outcome": "failure",
    "changes": {},
    "context": {"ip": "10.0.0.2"},
    "reason": "x",
    "occurred_at": datetime(2026, 11, 18, 10, 0, 1, tzinfo=UTC),
}


@pytest.mark.parametrize("field", sorted(TAMPERED))
def test_any_change_breaks_the_hash(field: str) -> None:
    tampered = _event(**{field: TAMPERED[field]})
    assert compute_hash(GENESIS_HASH, _event()) != compute_hash(GENESIS_HASH, tampered)


def test_hash_depends_on_previous_link() -> None:
    assert compute_hash(GENESIS_HASH, _event()) != compute_hash("f" * 64, _event())


def test_timestamp_is_normalized_to_utc() -> None:
    local = datetime(2026, 11, 18, 4, 0, tzinfo=timezone(timedelta(hours=-6)))
    assert canonical_payload(_event(occurred_at=local)) == canonical_payload(_event())


def test_diff_only_changed_fields_and_masks_secrets() -> None:
    changes = diff(
        {"name": "A", "client_secret": "s1", "same": 1},
        {"name": "B", "client_secret": "s2", "same": 1},
    )
    assert changes == {
        "client_secret": {"before": MASK, "after": MASK},
        "name": {"before": "A", "after": "B"},
    }
    assert diff({"a": 1}, {"a": 1}) == {}
    assert snapshot({"password": "x", "host": "h"}) == {"password": MASK, "host": "h"}


def test_declared_sensitive_fields_are_masked() -> None:
    assert diff({"pin": "1"}, {"pin": "2"}, sensitive={"pin"}) == {
        "pin": {"before": MASK, "after": MASK}
    }


def test_catalog_rejects_unknown_actions() -> None:
    assert category_of("user.created").value == "identity"
    with pytest.raises(ValueError, match="no registrada"):
        category_of("user.teleported")
    with pytest.raises(ValueError, match="no registradas"):
        audited("user.teleported")


def test_sliding_window_limiter() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.hit("k", now=0) is None
    assert limiter.hit("k", now=1) is None
    assert limiter.hit("k", now=2) == 58
    assert limiter.hit("otra", now=2) is None
    assert limiter.hit("k", now=61) is None
