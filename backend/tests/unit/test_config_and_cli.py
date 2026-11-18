from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sapextract.cli import main
from sapextract.core.config import Environment, Settings
from sapextract.core.events import DomainEvent, EventBus
from sapextract.core.pagination import Page, PageParams
from tests.conftest import UNREACHABLE_DB


def test_master_key_required_in_production() -> None:
    with pytest.raises(ValidationError, match="SX_MASTER_KEY"):
        Settings(environment=Environment.PRD, database_url=UNREACHABLE_DB)


def test_export_openapi(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["export-openapi", "--output", str(out)]) == 0
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert "/api/v1/health/ready" in spec["paths"]
    assert "ProblemDetails" in spec["components"]["schemas"]


def test_page_helpers() -> None:
    params = PageParams(page=3, page_size=20)
    assert params.offset == 40
    page = Page[int].of([1, 2], total=42, params=params)
    assert page.model_dump() == {"items": [1, 2], "total": 42, "page": 3, "page_size": 20}


def test_event_bus_isolates_failing_handlers() -> None:
    class UserDisabled(DomainEvent):
        pass

    received: list[DomainEvent] = []
    bus = EventBus()

    def failing(_: UserDisabled) -> None:
        msg = "fallo"
        raise RuntimeError(msg)

    bus.subscribe(UserDisabled, failing)
    bus.subscribe(UserDisabled, received.append)
    bus.publish(UserDisabled())
    assert len(received) == 1
