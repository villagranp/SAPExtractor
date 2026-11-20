"""Orígenes SAP de punta a punta: API + PostgreSQL + Gateway simulado (``tools/sap-mock``).

Cubre los criterios de aceptación del plan 05: alta de cada tipo desde el catálogo sin escribir
URLs, vista previa sin suscripción en la ODQ y detección de cambios de esquema como diff.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from sapextract.core.db import Database
from sapextract.sources.infrastructure.models import SourceSchemaVersion
from tests.integration.conftest import (
    ApiClient,
    activate_user,
    audit_actions,
    create_user,
    unique,
)
from tests.support.sap_gateway import MOCK_PASSWORD, MOCK_USER, RunningGateway, start_gateway

pytestmark = pytest.mark.integration

GL_SERVICE, GL_SET = "ZFI_WS_GL_ITEMS_SRV", "EntityOf0FI_GL_14"
SALES_SERVICE, SALES_SET = "ZSD_WS_SALES_ITEMS_SRV", "EntityOf2LIS_11_VAITM"
ACCOUNT_SERVICE, ACCOUNT_SET = "ZFI_WS_CV_GL_ACCOUNT_SRV", "FactsOfZFI_CV_GL_ACCOUNT"
MATERIAL_SERVICE, MATERIAL_SET = "ZMM_CV_MATERIAL_CDS", "ZMM_CV_MATERIAL"


@pytest.fixture(scope="module")
def gateway() -> Iterator[RunningGateway]:
    running = start_gateway()
    yield running
    running.stop()


def _system(admin: ApiClient, gateway: RunningGateway, *, activate: bool = True) -> dict[str, Any]:
    response = admin.post(
        "/sap-systems",
        {
            "code": unique("SRC_").upper(),
            "name": "S/4HANA desarrollo",
            "environment": "DEV",
            "system_type": "S4HANA_ONPREM",
            "base_url": gateway.base_url,
            "sap_client": "100",
            "language": "ES",
            "timeout_seconds": 5,
            "credentials": {"auth_type": "basic", "username": MOCK_USER, "password": MOCK_PASSWORD},
            "activate": activate,
        },
    )
    assert response.status_code == 201, response.text
    system: dict[str, Any] = response.json()["system"]
    return system


def _create(admin: ApiClient, system_id: str, service: str, entity_set: str, **extra: Any) -> Any:
    body = {
        "sap_system_id": system_id,
        "code": unique("SRC_").upper(),
        "name": f"Origen {entity_set}",
        "service_name": service,
        "entity_set": entity_set,
        **extra,
    }
    return admin.post("/sources", body)


def _tokens(gateway: RunningGateway, service: str) -> list[str]:
    response = httpx.get(f"{gateway.base_url}/_mock/services", verify=gateway.verify)
    response.raise_for_status()
    by_name = {s["service"]: s for s in response.json()}
    tokens: list[str] = by_name[service]["tokens"]
    return tokens


def test_inspect_proposes_type_keys_and_delta(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _system(admin, gateway)
    response = admin.post(
        "/sources/inspect",
        {"sap_system_id": system["id"], "service_name": SALES_SERVICE, "entity_set": SALES_SET},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["suggested_type"] == "ODP_EXTRACTOR"
    assert data["odp_name"] == "2LIS_11_VAITM"
    assert data["supports_delta"] is True
    assert data["keys"] == ["VBELN", "POSNR"]
    assert data["semantics_hint"] == {"delta_process": "ABR", "semantics": "before_after"}
    assert data["delta_processes"]["AIE"] == "after_image"
    erdat = next(c for c in data["columns"] if c["name"] == "ERDAT")
    assert erdat["arrow_type"] == "date32[day]"
    assert {"ODQ_CHANGEMODE", "ODQ_ENTITYCNTR"} <= {c["name"] for c in data["columns"]}

    cds = admin.post(
        "/sources/inspect",
        {
            "sap_system_id": system["id"],
            "service_name": MATERIAL_SERVICE,
            "entity_set": MATERIAL_SET,
        },
    ).json()
    assert cds["suggested_type"] == "CDS_ODATA"
    assert cds["supports_delta"] is False
    assert "LastChangeDateTime" in cds["watermark_candidates"]

    links = admin.post(
        "/sources/inspect",
        {
            "sap_system_id": system["id"],
            "service_name": GL_SERVICE,
            "entity_set": f"DeltaLinksOf{GL_SET}",
        },
    )
    assert links.status_code == 422
    assert links.json()["code"] == "SX-SRC-ENTITY-SET-INVALID"


def test_create_odp_source_and_preview_without_subscription(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    system = _system(admin, gateway)
    created = _create(
        admin,
        system["id"],
        GL_SERVICE,
        GL_SET,
        default_filters=[{"field": "RBUKRS", "op": "eq", "value": "1000"}],
        tags=["FI"],
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["status"] == "draft"
    assert source["source_type"] == "ODP_EXTRACTOR"
    assert source["odp_context"] == "SAPI"
    assert source["odp_name"] == "0FI_GL_14"
    assert source["service_path"] == f"/sap/opu/odata/sap/{GL_SERVICE}"
    assert source["delta_method"] == "ODQ"
    assert source["delta_semantics"] == "after_image"  # sugerencia para 0FI_GL_14 (AIMD)
    assert source["key_fields"] == ["RBUKRS", "GJAHR", "BELNR", "BUZEI"]
    assert source["filter_expression"] == "RBUKRS eq '1000'"
    assert source["schema"]["version"] == 1
    assert len(source["schema"]["hash"]) == 64
    assert source["tags"] == ["fi"]

    preview = admin.post(f"/sources/{source['id']}/preview")
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["rows"], data
    assert {r["RBUKRS"] for r in data["rows"]} == {"1000"}
    assert data["columns"][:4] == ["RBUKRS", "GJAHR", "BELNR", "BUZEI"]
    assert len(data["rows"][0]["BUDAT"]) == 10  # fecha ISO, sin hora
    assert data["count"] is None  # sin $count en ODP (H16)
    # La vista previa no abre suscripción: el mock no ha emitido ningún token de delta.
    assert _tokens(gateway, GL_SERVICE) == []

    actions = [e["action"] for e in audit_actions(admin, resource_id=source["id"])]
    assert "source.created" in actions
    assert "source.previewed" in actions

    listed = admin.get("/sources", params={"q": source["code"]}).json()
    assert [s["code"] for s in listed["items"]] == [source["code"]]
    assert listed["items"][0]["sap_system"]["code"] == system["code"]


def test_cds_odata_source_with_watermark(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _system(admin, gateway)
    created = _create(
        admin,
        system["id"],
        MATERIAL_SERVICE,
        MATERIAL_SET,
        delta={
            "method": "WATERMARK",
            "watermark_field": "LastChangeDateTime",
            "watermark_kind": "datetime",
            "watermark_timezone": "America/Guatemala",
        },
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["source_type"] == "CDS_ODATA"
    assert source["odp_context"] is None
    assert source["watermark_overlap_seconds"] == 300
    preview = admin.post(f"/sources/{source['id']}/preview").json()
    assert preview["count"] == 50  # $count solo en CDS vía OData
    assert len(preview["rows"]) == 50  # sigue __next: el mock pagina de 20 en 20
    assert preview["truncated"] is False

    odq = _create(admin, system["id"], MATERIAL_SERVICE, MATERIAL_SET, delta={"method": "ODQ"})
    assert odq.status_code == 422
    assert odq.json()["code"] == "SX-SRC-DELTA-UNSUPPORTED"


def test_lifecycle_and_system_dependency(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _system(admin, gateway)
    source = _create(admin, system["id"], ACCOUNT_SERVICE, ACCOUNT_SET).json()
    assert source["source_type"] == "CDS_ODP"
    assert source["odp_context"] == "ABAP_CDS"
    sid = source["id"]

    blocked = admin.delete(f"/sap-systems/{system['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SX-SAP-SYSTEM-IN-USE"

    active = admin.post(f"/sources/{sid}/activate")
    assert active.status_code == 200, active.text
    assert active.json()["status"] == "active"
    again = admin.post(f"/sources/{sid}/activate")
    assert again.status_code == 422
    assert again.json()["code"] == "SX-SRC-STATUS-TRANSITION"

    edited = admin.patch(
        f"/sources/{sid}",
        {"version": active.json()["version"], "name": "Cuentas de mayor", "page_size": 1000},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["page_size"] == 1000
    stale = admin.patch(f"/sources/{sid}", {"version": 1, "name": "Otro"})
    assert stale.status_code == 409

    deprecated = admin.post(f"/sources/{sid}/deprecate", {"reason": "Reemplazado"})
    assert deprecated.json()["status"] == "deprecated"
    assert deprecated.json()["status_reason"] == "Reemplazado"
    # Un origen obsoleto ya no impide dar de baja el sistema.
    assert admin.delete(f"/sap-systems/{system['id']}").status_code == 204

    actions = {e["action"] for e in audit_actions(admin, resource_id=sid)}
    assert {"source.created", "source.activated", "source.updated", "source.deprecated"} <= (
        actions
    )


def test_schema_change_is_versioned_with_diff(
    admin: ApiClient, gateway: RunningGateway, platform_db: Database
) -> None:
    system = _system(admin, gateway)
    source = _create(admin, system["id"], GL_SERVICE, GL_SET).json()
    sid = source["id"]
    unchanged = admin.post(f"/sources/{sid}/schema/refresh").json()
    assert unchanged["changed"] is False
    assert unchanged["source"]["schema"]["version"] == 1

    # Simula que SAP cambió: la versión guardada no tenía HKONT y DMBTR tenía menos dígitos.
    with platform_db.session() as session:
        version = session.scalars(
            select(SourceSchemaVersion).where(SourceSchemaVersion.source_id == sid)
        ).one()
        columns = [dict(c) for c in version.columns if c["name"] != "HKONT"]
        for c in columns:
            if c["name"] == "DMBTR":
                c["precision"] = 13
        version.columns = columns
        version.hash = "0" * 64
        session.commit()

    refreshed = admin.post(f"/sources/{sid}/schema/refresh")
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()
    assert result["changed"] is True
    assert result["source"]["schema"]["version"] == 2
    diff = result["diff"]
    assert [c["name"] for c in diff["added"]] == ["HKONT"]
    assert diff["removed"] == []
    assert [(c["name"], c["breaking"]) for c in diff["changed"]] == [("DMBTR", False)]
    assert diff["breaking"] is False

    versions = admin.get(f"/sources/{sid}/schema/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    stored = admin.get(f"/sources/{sid}/schema/diff", params={"from": 1, "to": 2}).json()
    assert stored["added"] == diff["added"]
    missing = admin.get(f"/sources/{sid}/schema/diff", params={"from": 1, "to": 9})
    assert missing.status_code == 404


def test_validation_rules(admin: ApiClient, gateway: RunningGateway) -> None:
    system = _system(admin, gateway)
    draft_system = _system(admin, gateway, activate=False)
    inactive = _create(admin, draft_system["id"], GL_SERVICE, GL_SET)
    assert inactive.status_code == 422
    assert inactive.json()["code"] == "SX-SRC-SYSTEM-INACTIVE"

    bad_filter = _create(
        admin,
        system["id"],
        GL_SERVICE,
        GL_SET,
        default_filters=[{"field": "RBUKRS", "op": "eq", "value": "12345"}],
    )
    assert bad_filter.status_code == 422
    assert bad_filter.json()["errors"][0]["field"] == "default_filters[0]"

    unknown_key = _create(admin, system["id"], GL_SERVICE, GL_SET, key_fields=["NOPE"])
    assert unknown_key.json()["code"] == "SX-SRC-KEYS-UNKNOWN"

    mismatch = _create(admin, system["id"], GL_SERVICE, GL_SET, source_type="CDS_ODATA")
    assert mismatch.json()["code"] == "SX-SRC-TYPE-MISMATCH"

    first = _create(admin, system["id"], GL_SERVICE, GL_SET, code="FI_GL_DUP_TEST")
    assert first.status_code == 201, first.text
    taken = _create(admin, system["id"], GL_SERVICE, GL_SET, code="fi_gl_dup_test")
    assert taken.status_code == 409
    assert taken.json()["code"] == "SX-SRC-CODE-TAKEN"
    assert (
        admin.delete(f"/sources/{first.json()['id']}", params={"reason": "prueba"}).status_code
        == 204
    )
    assert admin.get(f"/sources/{first.json()['id']}").status_code == 404
    freed = _create(admin, system["id"], GL_SERVICE, GL_SET, code="FI_GL_DUP_TEST")
    assert freed.status_code == 201, freed.text

    disabled = admin.patch(f"/sources/{freed.json()['id']}", {"preview_enabled": False})
    assert disabled.json()["preview_enabled"] is False
    blocked = admin.post(f"/sources/{freed.json()['id']}/preview")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SX-SRC-PREVIEW-DISABLED"


def test_permissions(admin: ApiClient, gateway: RunningGateway, client_factory: Any) -> None:
    system = _system(admin, gateway)
    source = _create(admin, system["id"], GL_SERVICE, GL_SET).json()
    viewer = create_user(admin, "viewer")
    client: ApiClient = client_factory()
    activate_user(client, viewer["user"]["username"], viewer["temporary_password"])
    assert client.get(f"/sources/{source['id']}").status_code == 200
    assert client.post(f"/sources/{source['id']}/preview").status_code == 403
    denied = client.post(
        "/sources/inspect",
        {"sap_system_id": system["id"], "service_name": GL_SERVICE, "entity_set": GL_SET},
    )
    assert denied.status_code == 403


def test_inactive_system_blocks_preview_and_refresh(
    admin: ApiClient, gateway: RunningGateway
) -> None:
    system = _system(admin, gateway)
    source = _create(admin, system["id"], GL_SERVICE, GL_SET).json()
    deactivated = admin.post(f"/sap-systems/{system['id']}/deactivate", {"reason": "mantenimiento"})
    assert deactivated.status_code == 200, deactivated.text
    for path in ("preview", "schema/refresh", "activate"):
        response = admin.post(f"/sources/{source['id']}/{path}")
        assert response.status_code == 422, (path, response.text)
        assert response.json()["code"] == "SX-SRC-SYSTEM-INACTIVE"
