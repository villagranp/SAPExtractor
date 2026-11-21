"""Pipelines de punta a punta por la API (plan 08): asistente, comprobación previa y acciones.

API + PostgreSQL + Gateway simulado + destino CSV real. La cola, el scheduler y los workers
se prueban en ``test_orchestration_queue.py``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from sapextract.sources.infrastructure.models import SourceSchemaVersion
from tests.integration.conftest import ApiClient, activate_user, audit_actions, create_user
from tests.support.certs import make_certificate
from tests.support.orchestration import (
    MATERIAL_SERVICE,
    MATERIAL_SET,
    SALES_SERVICE,
    SALES_SET,
    OrchEnv,
    unique,
)
from tests.support.sap_gateway import start_gateway

pytestmark = pytest.mark.integration


def _checks(report: dict[str, Any]) -> dict[str, bool]:
    return {c["code"]: c["ok"] for c in report["checks"]}


def _try_out(api: ApiClient, pipeline_id: str) -> dict[str, Any]:
    """«Probar pipeline»: la comprobación previa exige una prueba correcta y vigente."""
    response = api.post(f"/pipelines/{pipeline_id}/test-run")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _finding_codes(items: list[dict[str, Any]], severity: str) -> set[str]:
    return {f["code"] for f in items if f["severity"] == severity}


def _drop_column(orch: OrchEnv, source_id: str, column: str) -> None:
    """Simula que el esquema guardado de un origen no tiene una columna."""
    with orch.db.session() as session:
        version = session.scalars(
            select(SourceSchemaVersion)
            .where(SourceSchemaVersion.source_id == uuid.UUID(source_id))
            .order_by(SourceSchemaVersion.version.desc())
        ).first()
        assert version is not None
        version.columns = [dict(c) for c in version.columns if c["name"] != column]
        session.commit()


def _add_column(orch: OrchEnv, source_id: str, column: str) -> None:
    """Simula que el esquema guardado tiene una columna que SAP ya no publica."""
    with orch.db.session() as session:
        version = session.scalars(
            select(SourceSchemaVersion).where(SourceSchemaVersion.source_id == uuid.UUID(source_id))
        ).one()
        extra = dict(version.columns[-1]) | {"name": column, "is_key": False}
        version.columns = [*version.columns, extra]
        session.commit()


# ====================================================================== ciclo de vida
def test_pipeline_lifecycle(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    source = orch.source(api, system["id"])
    destination = orch.destination(api)
    code = unique("FI_GL_")
    created = api.post(
        "/pipelines",
        {
            "code": code.lower(),
            "name": "Partidas de mayor a CSV",
            "description": "Carga diaria",
            "source_id": source["id"],
            "destination_id": destination["id"],
            "extraction_mode": "DELTA",
            "load": {"load_mode": "append", "column_mapping": {"HKONT": "CUENTA", "WAERS": None}},
            "filters": [{"field": "RBUKRS", "op": "eq", "value": "1000"}],
            "priority": 2,
            "retry_policy": {"max_attempts": 4, "backoff_minutes": [2, 10]},
            "tags": ["FI"],
        },
    )
    assert created.status_code == 201, created.text
    pipeline = created.json()
    pid = pipeline["id"]
    assert pipeline["code"] == code
    assert pipeline["status"] == "draft"
    assert pipeline["load"]["target_name"] == f"{system['code']}_{source['code']}"
    assert pipeline["load"]["delete_handling"] == "ignore"
    assert pipeline["load"]["column_mapping"] == {"HKONT": "CUENTA", "WAERS": None}
    assert pipeline["worker_pool"] == "default"
    assert pipeline["retry_policy"] == {
        "max_attempts": 4,
        "backoff_minutes": [2, 10],
        "jitter": True,
    }
    assert pipeline["cross_environment"] is False
    assert pipeline["sap_system"]["environment"] == "DEV"
    assert pipeline["destination"]["type"] == "csv"
    # after_image con append: aviso, no bloqueo.
    assert _finding_codes(pipeline["findings"], "warning") == {"SX-PIPE-APPEND-AFTER-IMAGE"}
    assert _finding_codes(pipeline["findings"], "error") == set()

    listed = api.get("/pipelines", params={"q": code, "status": "draft", "tag": "FI"}).json()
    assert [p["id"] for p in listed["items"]] == [pid]
    by_system = api.get("/pipelines", params={"system": system["id"], "environment": "DEV"})
    assert [p["id"] for p in by_system.json()["items"]] == [pid]
    assert api.get("/pipelines", params={"q": code, "environment": "QAS"}).json()["total"] == 0

    # No se ejecuta ni se pausa un borrador.
    assert api.post(f"/pipelines/{pid}/run", {}).json()["code"] == "SX-PIPE-NOT-ACTIVE"
    assert api.post(f"/pipelines/{pid}/pause", {}).json()["code"] == "SX-PIPE-NOT-ACTIVE"

    updated = api.patch(
        f"/pipelines/{pid}",
        {"version": pipeline["version"], "name": "Partidas de mayor", "timeout_minutes": 30},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["timeout_minutes"] == 30
    stale = api.patch(f"/pipelines/{pid}", {"version": pipeline["version"], "name": "Otro"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "SX-GEN-STALE"

    assert _try_out(api, pid)["ok"] is True
    report = api.post(f"/pipelines/{pid}/validate", {}).json()
    assert report["ok"] is True
    assert _checks(report) == {
        "configuration": True,
        "source_active": True,
        "sap_system_test": True,
        "destination_test": True,
        "test_run": True,
        "service_published": True,
        "schema_current": True,
    }

    active = api.post(f"/pipelines/{pid}/activate", {})
    assert active.status_code == 200, active.text
    assert active.json()["status"] == "active"
    assert active.json()["activated_at"] is not None
    # Con el pipeline activo ya no se cambia el origen, el destino ni el modo.
    locked = api.patch(f"/pipelines/{pid}", {"extraction_mode": "FULL"})
    assert locked.status_code == 409
    assert locked.json()["code"] == "SX-PIPE-NOT-DRAFT"
    # Ni se deja una configuración bloqueada: «replace» en un pipeline delta.
    blocked = api.patch(f"/pipelines/{pid}", {"load": {"load_mode": "replace"}})
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SX-PIPE-REPLACE-INCREMENTAL"

    actions = {e["action"] for e in audit_actions(api, resource_id=pid)}
    assert actions == {
        "pipeline.created",
        "pipeline.updated",
        "pipeline.tested",
        "pipeline.activated",
    }


def test_pause_resume_run_and_delete(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    source = orch.source(api, orch.system(api)["id"])
    destination = orch.destination(api)
    pipeline = orch.pipeline(
        api, source=source, destination=destination, mode="DELTA", load_mode="append"
    )
    pid, code = pipeline["id"], pipeline["code"]

    paused = api.post(f"/pipelines/{pid}/pause", {"reason": "Cierre contable"})
    assert paused.status_code == 200, paused.text
    assert paused.json()["pipeline"]["status"] == "paused"
    assert paused.json()["pipeline"]["status_reason"] == "Cierre contable"
    assert "sigue creciendo en SAP" in paused.json()["warnings"][0]
    assert api.post(f"/pipelines/{pid}/activate", {}).json()["code"] == "SX-PIPE-PAUSED"
    resumed = api.post(f"/pipelines/{pid}/resume")
    assert resumed.json()["status"] == "active"
    assert api.post(f"/pipelines/{pid}/resume").json()["code"] == "SX-PIPE-NOT-PAUSED"

    run = orch.run_now(api, pid, reason="Prueba")
    assert api.delete(f"/pipelines/{pid}").json()["code"] == "SX-PIPE-RUN-LIVE"
    assert api.post(f"/runs/{run['id']}/cancel", {}).status_code == 200
    deleted = api.delete(f"/pipelines/{pid}", params={"reason": "Sustituido"})
    assert deleted.status_code == 204
    assert api.get(f"/pipelines/{pid}").status_code == 404
    # El código queda libre tras la baja lógica.
    again = orch.pipeline(api, source=source, destination=destination, activate=False, code=code)
    assert again["code"] == code

    actions = [e["action"] for e in audit_actions(api, resource_id=pid)]
    assert set(actions) == {
        "pipeline.created",
        "pipeline.tested",
        "pipeline.activated",
        "pipeline.paused",
        "pipeline.resumed",
        "pipeline.deleted",
    }
    paused_event = next(
        e for e in audit_actions(api, resource_id=pid) if e["action"] == "pipeline.paused"
    )
    assert paused_event["reason"] == "Cierre contable"


def test_input_validation(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    source = orch.source(api, orch.system(api)["id"])
    destination = orch.destination(api)
    base = {
        "code": unique("PL_"),
        "name": "Pipeline",
        "source_id": source["id"],
        "destination_id": destination["id"],
        "extraction_mode": "FULL",
        "load": {"load_mode": "replace"},
    }
    cases: tuple[tuple[dict[str, Any], str | None], ...] = (
        ({"code": "1X"}, "SX-GEN-VALIDATION"),
        ({"code": "CON ESPACIO"}, "SX-PIPE-CODE-INVALID"),
        ({"source_id": str(uuid.uuid4())}, "SX-SRC-NOT-FOUND"),
        ({"destination_id": str(uuid.uuid4())}, "SX-DST-NOT-FOUND"),
        (
            {"load": {"load_mode": "replace", "target_name": "con-guion"}},
            "SX-DST-IDENTIFIER-INVALID",
        ),
        (
            {"load": {"load_mode": "replace", "column_mapping": {"NOEXISTE": "X"}}},
            "SX-DST-MAPPING-UNKNOWN",
        ),
        ({"filters": [{"field": "NOEXISTE", "op": "eq", "value": "1"}]}, None),
        ({"concurrency_group": "NOEXISTE"}, "SX-PIPE-GROUP-NOT-FOUND"),
        ({"worker_pool": "con espacio"}, "SX-PIPE-POOL-INVALID"),
        ({"name": "   "}, "SX-PIPE-NAME-REQUIRED"),
    )
    for override, code in cases:
        response = api.post("/pipelines", base | override)
        assert response.status_code == 422, (override, response.text)
        if code:
            assert response.json()["code"] == code, override
    taken = orch.pipeline(api, source=source, destination=destination, activate=False)
    conflict = api.post("/pipelines", base | {"code": taken["code"]})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "SX-PIPE-CODE-TAKEN"
    assert api.get(f"/pipelines/{uuid.uuid4()}").json()["code"] == "SX-PIPE-NOT-FOUND"


# ====================================================================== avisos y bloqueos
def test_findings_block_activation(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    sales = orch.source(api, system["id"], SALES_SERVICE, SALES_SET)  # before_after (LO)
    destination = orch.destination(api)

    # CSV no admite upsert y un origen LO tampoco: se guarda en borrador, pero no se activa.
    live = api.post(
        "/pipelines/review",
        {
            "source_id": sales["id"],
            "destination_id": destination["id"],
            "extraction_mode": "DELTA",
            "load_mode": "upsert",
        },
    )
    assert live.status_code == 200, live.text
    assert _finding_codes(live.json(), "error") == {
        "SX-PIPE-LOAD-MODE-UNSUPPORTED",
        "SX-PIPE-UPSERT-NOT-AFTER-IMAGE",
    }
    draft = orch.pipeline(
        api, source=sales, destination=destination, mode="DELTA", load_mode="upsert", activate=False
    )
    assert _finding_codes(draft["findings"], "error") == _finding_codes(live.json(), "error")
    refused = api.post(f"/pipelines/{draft['id']}/activate", {})
    assert refused.status_code == 422
    assert refused.json()["code"] == "SX-PIPE-CHECK-FAILED"
    assert [e["field"] for e in refused.json()["errors"]] == ["configuration", "test_run"]

    fixed = api.patch(f"/pipelines/{draft['id']}", {"load": {"load_mode": "append"}})
    assert fixed.json()["findings"] == []
    assert _try_out(api, draft["id"])["ok"] is True
    assert api.post(f"/pipelines/{draft['id']}/activate", {}).status_code == 200

    # Segundo pipeline delta sobre el mismo origen: bloqueado hasta validarlo en SAP (H6, H18).
    second = orch.pipeline(
        api, source=sales, destination=destination, mode="DELTA", load_mode="append", activate=False
    )
    assert _finding_codes(second["findings"], "error") == {"SX-PIPE-DELTA-SHARED"}
    assert api.post(f"/pipelines/{second['id']}/activate", {}).status_code == 422
    # En carga completa sí conviven.
    full = api.patch(
        f"/pipelines/{second['id']}", {"extraction_mode": "FULL", "load": {"load_mode": "replace"}}
    )
    assert full.json()["findings"] == []

    # Un origen sin delta no admite modo DELTA ni WATERMARK.
    material = orch.source(api, system["id"], MATERIAL_SERVICE, MATERIAL_SET)
    for mode, code in (("DELTA", "SX-PIPE-NO-DELTA"), ("WATERMARK", "SX-PIPE-NO-WATERMARK")):
        found = api.post(
            "/pipelines/review",
            {
                "source_id": material["id"],
                "destination_id": destination["id"],
                "extraction_mode": mode,
                "load_mode": "append",
            },
        ).json()
        assert _finding_codes(found, "error") == {code}


def test_activation_needs_current_tests_and_schema(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    source = orch.source(api, system["id"], activate=False)
    destination = orch.destination(api, activate=False)
    pipeline = orch.pipeline(api, source=source, destination=destination, activate=False)
    pid = pipeline["id"]

    report = api.post(f"/pipelines/{pid}/validate", {}).json()
    assert report["ok"] is False
    assert _checks(report)["source_active"] is False
    assert _checks(report)["destination_test"] is False
    assert _checks(report)["sap_system_test"] is True
    refused = api.post(f"/pipelines/{pid}/activate", {}).json()
    assert {e["field"] for e in refused["errors"]} == {
        "source_active",
        "destination_test",
        "test_run",
    }
    # Con el destino sin activar, la prueba del pipeline falla en el primer paso.
    blocked = _try_out(api, pid)
    assert blocked["ok"] is False
    assert [(s["step"], s["status"]) for s in blocked["steps"]] == [
        ("configuration", "failed"),
        ("read", "skipped"),
        ("load", "skipped"),
        ("cleanup", "skipped"),
    ]

    assert api.post(f"/sources/{source['id']}/activate").status_code == 200
    assert api.post(f"/destinations/{destination['id']}/test").status_code == 200
    assert api.post(f"/destinations/{destination['id']}/activate").status_code == 200
    assert _try_out(api, pid)["ok"] is True

    # Cambiar la conexión deja la prueba del sistema SAP sin vigencia.
    api.patch(f"/sap-systems/{system['id']}", {"timeout_seconds": 6, "language": "EN"})
    outdated = api.post(f"/pipelines/{pid}/validate", {}).json()
    assert _checks(outdated)["sap_system_test"] is False
    assert api.post(f"/sap-systems/{system['id']}/test").status_code == 200
    assert api.post(f"/sap-systems/{system['id']}/activate").status_code == 200

    # El esquema guardado tiene una columna que SAP ya no publica: cambio incompatible.
    _add_column(orch, source["id"], "ZZ_RETIRADA")
    drift = api.post(f"/pipelines/{pid}/validate", {}).json()
    assert _checks(drift)["service_published"] is True
    assert _checks(drift)["schema_current"] is False
    message = next(c["message"] for c in drift["checks"] if c["code"] == "schema_current")
    assert "ZZ_RETIRADA" in message
    assert api.post(f"/sources/{source['id']}/schema/refresh").status_code == 200
    assert api.post(f"/pipelines/{pid}/validate", {}).json()["ok"] is True

    # SAP caído: el servicio no responde y el pipeline no se activa.
    orch.gateway.scenario(status=503, times=5)
    down = api.post(f"/pipelines/{pid}/validate", {}).json()
    orch.gateway.control("/_mock/reset")
    assert _checks(down)["service_published"] is False
    assert api.post(f"/pipelines/{pid}/activate", {}).status_code == 200


def test_cross_environment_needs_a_reason(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    source = orch.source(api, orch.system(api, "QAS")["id"])
    destination = orch.destination(api, "PRD")
    pipeline = orch.pipeline(api, source=source, destination=destination, activate=False)
    pid = pipeline["id"]
    assert pipeline["cross_environment"] is True
    assert _finding_codes(pipeline["findings"], "warning") == {"SX-PIPE-CROSS-ENVIRONMENT"}
    assert _try_out(api, pid)["ok"] is True

    refused = api.post(f"/pipelines/{pid}/activate", {})
    assert refused.status_code == 422
    assert [e["field"] for e in refused.json()["errors"]] == ["cross_environment"]
    assert (
        api.post(f"/pipelines/{pid}/activate", {"cross_environment_reason": "corto"}).status_code
        == 422
    )

    reason = "Carga puntual de QAS para validar el modelo en PRD"
    preview = api.post(f"/pipelines/{pid}/validate", {"cross_environment_reason": reason}).json()
    assert preview["ok"] is True
    active = api.post(f"/pipelines/{pid}/activate", {"cross_environment_reason": reason})
    assert active.status_code == 200, active.text
    assert active.json()["cross_environment_reason"] == reason
    event = next(
        e for e in audit_actions(api, resource_id=pid) if e["action"] == "pipeline.activated"
    )
    assert event["reason"] == reason
    assert event["context"]["cross_environment"] is True
    assert event["context"]["destination_environment"] == "PRD"


# ====================================================================== clonado entre entornos
def test_clone_from_dev_to_prd_is_tested_and_activated_there(
    orch: OrchEnv, orch_admin: ApiClient, tmp_path: Path
) -> None:
    """Criterio de aceptación: un pipeline validado en DEV pasa a PRD con la misma comprobación.

    PRD exige HTTPS verificado: el sistema PRD es otro Gateway simulado, con TLS.
    """
    api = orch_admin
    cert = make_certificate(tmp_path)
    prd_gateway = start_gateway(tls=cert)
    try:
        dev = orch.system(api, "DEV")
        original = orch.pipeline(
            api,
            source=orch.source(api, dev["id"]),
            destination=orch.destination(api, "DEV"),
            mode="DELTA",
            load_mode="append",
        )
        assert original["status"] == "active"

        prd = orch.system(api, "PRD", base_url=prd_gateway.base_url, ca_bundle_pem=cert.cert_pem)
        response = api.post(
            f"/pipelines/{original['id']}/clone",
            {
                "code": unique("PRD_"),
                "sap_system_id": prd["id"],
                "destination_id": orch.destination(api, "PRD")["id"],
            },
        )
        assert response.status_code == 201, response.text
        clone = response.json()["pipeline"]
        pid = clone["id"]
        assert clone["status"] == "draft"
        assert clone["sap_system"]["environment"] == "PRD"
        assert clone["destination"]["environment"] == "PRD"
        assert clone["cross_environment"] is False
        # El clon no hereda la prueba del pipeline de DEV: hay que probarlo en PRD.
        assert (clone["last_test_at"], clone["last_test_ok"]) == (None, None)

        assert api.post(f"/sources/{clone['source']['id']}/activate").status_code == 200
        pending = api.post(f"/pipelines/{pid}/validate", {}).json()
        assert [c["code"] for c in pending["checks"] if not c["ok"]] == ["test_run"]
        assert api.post(f"/pipelines/{pid}/activate", {}).status_code == 422

        assert _try_out(api, pid)["ok"] is True
        report = api.post(f"/pipelines/{pid}/validate", {}).json()
        assert report["ok"] is True
        assert _checks(report)["clone_schema"] is True
        active = api.post(f"/pipelines/{pid}/activate", {})
        assert active.status_code == 200, active.text
        assert active.json()["status"] == "active"
        # El de DEV sigue como estaba.
        assert api.get(f"/pipelines/{original['id']}").json()["status"] == "active"
    finally:
        prd_gateway.stop()


def test_clone_to_another_system_and_destination(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    dev = orch.system(api, "DEV")
    source = orch.source(api, dev["id"], tags=["FI"])
    original = orch.pipeline(
        api,
        source=source,
        destination=orch.destination(api, "DEV"),
        mode="DELTA",
        load_mode="append",
        filters=[{"field": "RBUKRS", "op": "eq", "value": "1000"}],
    )
    api.put(
        f"/pipelines/{original['id']}/schedules",
        {"schedules": [{"type": "interval", "interval_minutes": 30}]},
    )
    qas = orch.system(api, "QAS")
    qas_destination = orch.destination(api, "QAS")

    response = api.post(
        f"/pipelines/{original['id']}/clone",
        {
            "code": unique("QAS_"),
            "sap_system_id": qas["id"],
            "destination_id": qas_destination["id"],
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    clone = result["pipeline"]
    assert result["source_created"] is True
    assert result["schema_diff"] == {"breaking": False, "added": [], "removed": [], "changed": []}
    assert clone["status"] == "draft"
    assert clone["cloned_from_id"] == original["id"]
    assert clone["sap_system"]["id"] == qas["id"]
    assert clone["destination"]["id"] == qas_destination["id"]
    assert clone["extraction_mode"] == "DELTA"
    assert clone["filters"] == original["filters"]
    assert clone["next_fire_at"] is None  # la programación se copia, pero un borrador no dispara
    assert len(api.get(f"/pipelines/{clone['id']}/schedules").json()) == 1

    # El origen nuevo se leyó de SAP en el sistema elegido y queda en borrador.
    new_source = api.get(f"/sources/{clone['source']['id']}").json()
    assert new_source["sap_system"]["id"] == qas["id"]
    assert new_source["entity_set"] == source["entity_set"]
    assert new_source["status"] == "draft"
    assert new_source["tags"] == ["fi"]
    report = api.post(f"/pipelines/{clone['id']}/validate", {}).json()
    assert _checks(report)["source_active"] is False
    assert _checks(report)["clone_schema"] is True

    # Un segundo clon al mismo sistema reutiliza el origen que ya existe.
    again = api.post(
        f"/pipelines/{original['id']}/clone", {"code": unique("QAS_"), "sap_system_id": qas["id"]}
    ).json()
    assert again["source_created"] is False
    assert again["pipeline"]["source"]["id"] == new_source["id"]
    assert again["pipeline"]["destination"]["id"] == original["destination"]["id"]
    assert again["pipeline"]["cross_environment"] is True

    # Si el esquema del sistema de destino no tiene una columna del de partida, no se activa.
    api.post(f"/sources/{new_source['id']}/activate")
    _drop_column(orch, new_source["id"], "WAERS")
    incompatible = api.post(f"/pipelines/{clone['id']}/validate", {}).json()
    assert _checks(incompatible)["clone_schema"] is False
    refused = api.post(f"/pipelines/{clone['id']}/activate", {})
    assert refused.status_code == 422
    assert "clone_schema" in {e["field"] for e in refused.json()["errors"]}

    # Clon simple: mismo origen y destino, sin diff de esquema.
    same = api.post(f"/pipelines/{original['id']}/clone", {"code": unique("COPIA_")}).json()
    assert (same["schema_diff"], same["pipeline"]["source"]["id"]) == (None, source["id"])
    assert same["pipeline"]["name"].endswith("(copia)")

    events = audit_actions(api, resource_id=clone["id"])
    cloned = next(e for e in events if e["action"] == "pipeline.cloned")
    assert cloned["context"]["cloned_from"] == original["code"]
    assert cloned["context"]["source_created"] is True
    assert (
        api.post(f"/pipelines/{original['id']}/clone", {"code": clone["code"]}).status_code == 409
    )


def test_clone_needs_source_permission_to_create_the_source(
    orch: OrchEnv, orch_admin: ApiClient
) -> None:
    api = orch_admin
    original = orch.pipeline(api)
    qas = orch.system(api, "QAS")
    role = api.post(
        "/roles",
        {
            "code": unique("pl_only_").lower(),
            "name": "Solo pipelines",
            "permissions": ["pipeline:read", "pipeline:manage"],
        },
    )
    assert role.status_code == 201, role.text
    user = create_user(api, role.json()["code"])
    limited = ApiClient(orch.app)
    activate_user(limited, user["user"]["username"], user["temporary_password"])
    denied = limited.post(
        f"/pipelines/{original['id']}/clone", {"code": unique("QAS_"), "sap_system_id": qas["id"]}
    )
    assert denied.status_code == 403
    assert "source:manage" in denied.json()["detail"]


# ====================================================================== programación
def test_schedule_preview_and_replace(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    pipeline = orch.pipeline(api)
    pid = pipeline["id"]
    working_days = {
        "type": "cron",
        "cron_expression": "*/30 * * * 1-5",
        "timezone": "America/Guatemala",
    }

    preview = api.post("/schedules/preview", working_days)
    assert preview.status_code == 200, preview.text
    fires = preview.json()["fires"]
    assert preview.json()["timezone"] == "America/Guatemala"
    assert len(fires) == 10
    assert fires == sorted(fires)
    assert len(api.post("/schedules/preview", working_days | {"count": 3}).json()["fires"]) == 3
    default_tz = api.post("/schedules/preview", {"type": "interval", "interval_minutes": 15})
    assert default_tz.json()["timezone"] == "UTC"  # zona horaria de la plataforma
    invalid = api.post("/schedules/preview", {"type": "cron", "cron_expression": "cada hora"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "SX-SCHED-CRON-INVALID"
    assert invalid.json()["errors"][0]["field"] == "cron_expression"

    saved = api.put(
        f"/pipelines/{pid}/schedules",
        {
            "schedules": [
                working_days
                | {
                    "misfire_policy": "run_once",
                    "blackout_windows": [
                        {
                            "kind": "weekly",
                            "days": [5],
                            "start": "22:00",
                            "end": "02:00",
                            "label": "Backup",
                        }
                    ],
                },
                {"type": "manual_only"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    cron, manual = saved.json()
    assert cron["next_fire_at"] == fires[0]  # el pipeline está activo: ya tiene próximo disparo
    assert cron["blackout_windows"][0]["label"] == "Backup"
    assert cron["misfire_policy"] == "run_once"
    assert manual["next_fire_at"] is None
    assert api.get(f"/pipelines/{pid}").json()["next_fire_at"] == fires[0]
    assert len(api.get(f"/pipelines/{pid}/schedules").json()) == 2

    # Sustituir borra lo anterior; una lista vacía deja el pipeline solo manual.
    assert api.put(f"/pipelines/{pid}/schedules", {"schedules": []}).json() == []
    assert api.get(f"/pipelines/{pid}").json()["next_fire_at"] is None
    bad_window = api.put(
        f"/pipelines/{pid}/schedules",
        {
            "schedules": [
                {
                    "type": "interval",
                    "interval_minutes": 5,
                    "blackout_windows": [
                        {"kind": "weekly", "days": [], "start": "01:00", "end": "02:00"}
                    ],
                }
            ]
        },
    )
    assert bad_window.status_code == 422
    assert bad_window.json()["code"] == "SX-SCHED-BLACKOUT-INVALID"
    events = [e["action"] for e in audit_actions(api, resource_id=pid)]
    assert events.count("schedule.updated") == 2


# ====================================================================== ejecuciones
def test_run_now_cancel_and_backfill_rules(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    after_image = orch.pipeline(
        api, source=orch.source(api, system["id"]), mode="DELTA", load_mode="append"
    )
    pid = after_image["id"]

    run = orch.run_now(api, pid, reason="Carga pedida por finanzas")
    assert run["status"] == "queued"
    assert run["mode"] == "DELTA"
    assert run["trigger"] == "manual"
    assert run["priority"] == 1
    assert run["attempts"] == []
    assert run["attempts_budget"] == 3
    assert api.get(f"/pipelines/{pid}").json()["live_run"]["id"] == run["id"]
    again = api.post(f"/pipelines/{pid}/run", {})
    assert again.status_code == 409
    assert again.json()["code"] == "SX-RUN-ALREADY-LIVE"

    cancelled = api.post(f"/runs/{run['id']}/cancel", {"reason": "Lanzada por error"})
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["finished_at"] is not None
    assert api.post(f"/runs/{run['id']}/cancel", {}).json()["code"] == "SX-RUN-FINISHED"
    assert api.post(f"/runs/{run['id']}/retry").json()["code"] == "SX-RUN-NOT-FAILED"
    assert api.get(f"/pipelines/{pid}").json()["last_run"]["status"] == "cancelled"

    # Recarga completa de un pipeline delta: solo con imágenes posteriores.
    backfill = orch.run_now(api, pid, mode="FULL")
    assert backfill["mode"] == "FULL"
    api.post(f"/runs/{backfill['id']}/cancel", {})
    assert api.post(f"/pipelines/{pid}/run", {"mode": "DELTA_INIT"}).json()["code"] == (
        "SX-RUN-MODE-INVALID"
    )
    additive = orch.pipeline(
        api,
        source=orch.source(api, system["id"], SALES_SERVICE, SALES_SET),
        mode="DELTA",
        load_mode="append",
    )
    unsafe = api.post(f"/pipelines/{additive['id']}/run", {"mode": "FULL"})
    assert unsafe.status_code == 422
    assert unsafe.json()["code"] == "SX-RUN-BACKFILL-UNSAFE"

    listed = api.get("/runs", params={"pipeline": pid, "status": "cancelled"}).json()
    assert [r["id"] for r in listed["items"]] == [backfill["id"], run["id"]]
    assert listed["items"][0]["pipeline"]["code"] == after_image["code"]
    assert api.get("/runs", params={"pipeline": pid, "trigger": "schedule"}).json()["total"] == 0
    assert api.get(f"/runs/{uuid.uuid4()}").json()["code"] == "SX-RUN-NOT-FOUND"

    events = audit_actions(api, resource_id=run["id"])
    assert [e["action"] for e in events] == ["run.cancelled", "run.triggered_manual"]
    assert events[0]["reason"] == "Lanzada por error"
    assert events[1]["category"] == "execution"


def test_permissions(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    pipeline = orch.pipeline(api)
    pid = pipeline["id"]

    operator_user = create_user(api, "operator")
    operator = ApiClient(orch.app)
    activate_user(operator, operator_user["user"]["username"], operator_user["temporary_password"])
    assert operator.get(f"/pipelines/{pid}").status_code == 200
    assert operator.get("/workers").status_code == 200
    run = operator.post(f"/pipelines/{pid}/run", {})
    assert run.status_code == 201
    # Un operador ejecuta, pero la recarga completa exige pipeline:manage.
    assert operator.post(f"/runs/{run.json()['id']}/cancel", {}).status_code == 200
    assert operator.post(f"/pipelines/{pid}/pause", {}).status_code == 200
    assert operator.post(f"/pipelines/{pid}/resume").status_code == 200
    for method, path, body in (
        ("POST", "/pipelines", {}),
        ("PATCH", f"/pipelines/{pid}", {"name": "x"}),
        ("DELETE", f"/pipelines/{pid}", None),
        ("POST", f"/pipelines/{pid}/clone", {"code": "CLON_X"}),
        ("PUT", f"/pipelines/{pid}/schedules", {"schedules": []}),
        ("PUT", "/concurrency-groups/SAP_PRD", {"max_parallel": 2}),
    ):
        assert operator.request(method, path, json=body).status_code == 403, path

    viewer_user = create_user(api, "viewer")
    viewer = ApiClient(orch.app)
    activate_user(viewer, viewer_user["user"]["username"], viewer_user["temporary_password"])
    assert viewer.get("/pipelines").status_code == 200
    assert viewer.get("/runs").status_code == 200
    assert viewer.post(f"/pipelines/{pid}/run", {}).status_code == 403
    assert viewer.post(f"/pipelines/{pid}/pause", {}).status_code == 403
    denied = audit_actions(api, action="auth.access_denied")
    assert any(e["context"].get("path", "").endswith(f"/pipelines/{pid}/run") for e in denied)


# ====================================================================== grupos y dependencias
def test_concurrency_groups(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    code = unique("GRP_")
    created = api.put(
        f"/concurrency-groups/{code.lower()}", {"max_parallel": 2, "description": "SAP PRD"}
    )
    assert created.status_code == 200, created.text
    assert created.json() == created.json() | {"code": code, "max_parallel": 2, "pipelines": 0}
    assert api.put(f"/concurrency-groups/{code}", {"max_parallel": 0}).status_code == 422
    assert api.put("/concurrency-groups/1", {"max_parallel": 2}).json()["code"] == (
        "SX-PIPE-GROUP-INVALID"
    )

    pipeline = orch.pipeline(api, concurrency_group=code.lower())
    assert pipeline["concurrency_group"] == code
    listed = {g["code"]: g for g in api.get("/concurrency-groups").json()}
    assert listed[code]["pipelines"] == 1
    assert listed[code]["running"] == 0
    in_use = api.delete(f"/concurrency-groups/{code}")
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "SX-PIPE-GROUP-IN-USE"

    changed = api.put(f"/concurrency-groups/{code}", {"max_parallel": 5})
    assert changed.json()["max_parallel"] == 5
    assert (
        api.patch(f"/pipelines/{pipeline['id']}", {"concurrency_group": ""}).json()[
            "concurrency_group"
        ]
        is None
    )
    assert api.delete(f"/concurrency-groups/{code}").status_code == 204
    assert api.delete(f"/concurrency-groups/{code}").status_code == 404
    actions = [e["action"] for e in audit_actions(api, resource_id=code)]
    assert actions == [
        "concurrency_group.deleted",
        "concurrency_group.updated",
        "concurrency_group.updated",
    ]


def test_pipelines_protect_what_they_use(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    source = orch.source(api, system["id"])
    destination = orch.destination(api)
    pipeline = orch.pipeline(api, source=source, destination=destination)

    blocked_source = api.delete(f"/sources/{source['id']}")
    assert blocked_source.status_code == 409
    assert blocked_source.json()["code"] == "SX-SRC-IN-USE"
    assert pipeline["code"] in blocked_source.json()["detail"]
    blocked_destination = api.delete(f"/destinations/{destination['id']}")
    assert blocked_destination.status_code == 409
    assert blocked_destination.json()["code"] == "SX-DST-IN-USE"
    detail = api.get(f"/sap-systems/{system['id']}").json()
    assert {"pipeline", "origen"} <= {d["kind"] for d in detail["dependents"]}

    assert api.delete(f"/pipelines/{pipeline['id']}").status_code == 204
    assert api.delete(f"/destinations/{destination['id']}").status_code == 204
    assert api.delete(f"/sources/{source['id']}").status_code == 204


def test_sap_system_run_limit(orch: OrchEnv, orch_admin: ApiClient) -> None:
    api = orch_admin
    system = orch.system(api)
    detail = api.get(f"/sap-systems/{system['id']}").json()
    assert detail["max_concurrent_runs"] == 2  # valor por defecto
    changed = api.patch(f"/sap-systems/{system['id']}", {"max_concurrent_runs": 5})
    assert changed.status_code == 200, changed.text
    assert changed.json()["max_concurrent_runs"] == 5
    assert api.patch(f"/sap-systems/{system['id']}", {"max_concurrent_runs": 0}).status_code == 422
    explicit = orch.system(api, max_concurrent_runs=4)
    assert api.get(f"/sap-systems/{explicit['id']}").json()["max_concurrent_runs"] == 4
