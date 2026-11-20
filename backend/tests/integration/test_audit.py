"""Auditoría contra PostgreSQL real: inmutabilidad, cadena de hash, exportación y secretos."""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from sapextract.audit.application.service import AuditService
from sapextract.audit.domain.model import Actor, AuditEntry
from sapextract.core.db import Database
from sapextract.identity.domain import totp
from tests.integration.conftest import ApiClient, activate_user, audit_actions, create_user

pytestmark = pytest.mark.integration

ClientFactory = Callable[[], ApiClient]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sx.audit_event SET actor_name = 'x'",
        "DELETE FROM sx.audit_event",
        "TRUNCATE sx.audit_event",
    ],
)
def test_audit_table_is_append_only(
    platform_db: Database, admin: ApiClient, statement: str
) -> None:
    with pytest.raises(DBAPIError, match="solo inserción"), platform_db.engine.begin() as conn:
        conn.execute(text(statement))


def test_chain_detects_manual_tampering(platform_db: Database, admin: ApiClient) -> None:
    ok = admin.post("/audit-events/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["checked"] > 0

    target = audit_actions(admin, action="user.created")[0]
    with platform_db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE sx.audit_event DISABLE TRIGGER trg_audit_event_immutable"))
        conn.execute(
            text("UPDATE sx.audit_event SET actor_name = 'manipulado' WHERE id = :id"),
            {"id": target["id"]},
        )
    try:
        broken = admin.post("/audit-events/verify").json()
        assert broken["ok"] is False
        assert broken["broken_id"] == target["id"]
        assert "modificado" in broken["detail"]
        status = admin.get("/audit-events/integrity").json()["last_verification"]
        assert status["ok"] is False
        assert audit_actions(admin, action="audit.verification_failed")
    finally:
        with platform_db.engine.begin() as conn:
            conn.execute(
                text("UPDATE sx.audit_event SET actor_name = :n WHERE id = :id"),
                {"n": target["actor_name"], "id": target["id"]},
            )
            conn.execute(
                text("ALTER TABLE sx.audit_event ENABLE TRIGGER trg_audit_event_immutable")
            )
    assert admin.post("/audit-events/verify").json()["ok"] is True


def test_concurrent_writes_keep_the_chain_valid(platform_db: Database, admin: ApiClient) -> None:
    service = AuditService()

    def write(n: int) -> None:
        for i in range(10):
            service.record_detached(
                platform_db,
                AuditEntry(action="audit.verified", details={"thread": n, "i": i}),
                actor=Actor.system("prueba"),
            )

    threads = [threading.Thread(target=write, args=(n,)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert admin.post("/audit-events/verify").json()["ok"] is True


def test_export_csv_and_json(admin: ApiClient) -> None:
    csv_response = admin.post("/audit-events/export", {"format": "csv", "category": "identity"})
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(csv_response.text.lstrip("﻿"))))
    assert rows
    assert {r["category"] for r in rows} == {"identity"}

    json_response = admin.post("/audit-events/export", {"format": "json", "action": "user."})
    events = json.loads(json_response.text)
    assert events
    assert all(e["action"].startswith("user.") for e in events)
    assert audit_actions(admin, action="audit.exported")


def test_search_reports_the_real_total(admin: ApiClient) -> None:
    """El total de la paginación es el recuento real, no el número de filas de la página."""
    first = admin.get("/audit-events", params={"page_size": 2})
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 2
    assert body["total"] > 2
    everything = admin.get("/audit-events", params={"page_size": 200}).json()
    assert body["total"] == min(everything["total"], body["total"])
    filtered = admin.get(
        "/audit-events", params={"action": "auth.login_succeeded", "page_size": 200}
    )
    items = filtered.json()["items"]
    assert filtered.json()["total"] >= len(items) > 0
    assert all(e["action"] == "auth.login_succeeded" for e in items)


def test_event_detail_and_catalog(admin: ApiClient) -> None:
    event = audit_actions(admin)[0]
    detail = admin.get(f"/audit-events/{event['id']}")
    assert detail.status_code == 200
    assert detail.json()["hash"] == event["hash"]
    catalog = admin.get("/audit-events/catalog").json()
    assert {"action": "user.created", "category": "identity"} in catalog


def test_auditor_reads_but_cannot_manage_users(
    client_factory: ClientFactory, admin: ApiClient
) -> None:
    created = create_user(admin, "auditor")
    auditor = client_factory()
    activate_user(auditor, created["user"]["username"], created["temporary_password"])
    assert auditor.get("/audit-events").status_code == 200
    assert auditor.post("/audit-events/verify").status_code == 200
    assert auditor.post("/users", {"username": "x", "display_name": "x"}).status_code == 403


def test_secrets_never_reach_audit_or_logs(
    client_factory: ClientFactory, admin: ApiClient, platform_db: Database
) -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(f"{record.msg} {record.args}")

    root = logging.getLogger()
    handler = Capture(level=logging.DEBUG)
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        created = create_user(admin, "viewer")
        temporary = created["temporary_password"]
        client = client_factory()
        password = activate_user(client, created["user"]["username"], temporary)
        mfa_secret = client.post("/auth/mfa/setup").json()["secret"]
        code = totp.code_at(mfa_secret, totp.time_step(time.time()))
        client.post("/auth/mfa/enable", {"code": code})
        reset = admin.post(f"/users/{created['user']['id']}/reset-password").json()
        token = admin.post(
            "/api-tokens", {"name": "Secreto", "scopes": ["user:read"], "expires_in_days": 1}
        ).json()["secret"]
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    with platform_db.engine.connect() as conn:
        dump = "\n".join(
            str(row)
            for row in conn.execute(text("SELECT changes, context, reason FROM sx.audit_event"))
        )
    logs = "\n".join(records)
    for secret in (temporary, password, mfa_secret, reset["temporary_password"], token):
        assert secret not in dump
        assert secret not in logs
