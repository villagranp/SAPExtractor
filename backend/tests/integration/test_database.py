"""Pruebas contra PostgreSQL real (testcontainers). Requieren Docker."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from sapextract.core.config import Environment, Settings
from sapextract.core.db import Database
from sapextract.core.migrations import current_revisions, downgrade, head_revisions, upgrade
from sapextract.core.system_settings import SystemSetting
from sapextract.main_api import create_app
from sapextract.main_scheduler import SCHEDULER_LEADER_LOCK, try_acquire_leadership

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="module")
def migrated(database_url: str) -> str:
    upgrade(database_url)
    return database_url


def _settings(url: str) -> Settings:
    return Settings(environment=Environment.TEST, database_url=url, log_json=False)


def test_migrations_upgrade_and_downgrade(database_url: str) -> None:
    db = Database(_settings(database_url))
    try:
        upgrade(database_url)
        assert current_revisions(db.engine) == head_revisions()
        downgrade(database_url, "base")
        assert current_revisions(db.engine) == set()
        upgrade(database_url)
        assert current_revisions(db.engine) == head_revisions()
    finally:
        db.dispose()


def test_seeded_settings(migrated: str) -> None:
    db = Database(_settings(migrated))
    try:
        with db.session() as session:
            keys = set(session.scalars(select(SystemSetting.key)))
        assert {"platform.timezone", "platform.language"} <= keys
    finally:
        db.dispose()


def test_ready_ok_with_migrated_database(migrated: str) -> None:
    client = TestClient(create_app(_settings(migrated)))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["checks"]["migrations"]["status"] == "ok"


def test_scheduler_leadership_is_exclusive(migrated: str) -> None:
    db = Database(_settings(migrated))
    try:
        with db.engine.connect() as first, db.engine.connect() as second:
            assert try_acquire_leadership(first)
            assert not try_acquire_leadership(second)
            first.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEDULER_LEADER_LOCK})
            assert try_acquire_leadership(second)
    finally:
        db.dispose()
