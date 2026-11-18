from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sap_mock.app import CATALOG, GATEWAY, create_app

AUTH = ("u", "p")
GL = f"{GATEWAY}/sap/ZFI_WS_GL_ITEMS_SRV"
MAT = f"{GATEWAY}/sap/ZMM_CV_MATERIAL_CDS"
TRACK = {"Prefer": "odata.track-changes, odata.maxpagesize=40"}


@pytest.fixture
def client() -> TestClient:
    app = create_app(user="u", password="p", rows_per_service=100, page_size=40, seed=1)  # noqa: S106
    test_client = TestClient(app)
    test_client.auth = AUTH
    return test_client


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def _read_all(
    client: TestClient, url: str, headers: dict[str, str] | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    rows: list[dict[str, Any]] = []
    while True:
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        d = response.json()["d"]
        rows += d["results"]
        if "__next" not in d:
            return rows, d.get("__delta")
        url = _path(d["__next"])


def test_requires_basic_auth(client: TestClient) -> None:
    assert client.get(f"{CATALOG}/ServiceCollection", auth=("u", "mal")).status_code == 401


def test_catalog_lists_services(client: TestClient) -> None:
    results = client.get(f"{CATALOG}/ServiceCollection").json()["d"]["results"]
    names = {r["TechnicalServiceName"] for r in results}
    assert {"ZFI_WS_GL_ITEMS_SRV", "ZMM_CV_MATERIAL_CDS"} <= names


def test_metadata_is_valid_edmx(client: TestClient) -> None:
    root = ET.fromstring(client.get(f"{GL}/$metadata").content)  # noqa: S314 - XML propio
    ns = {"edm": "http://schemas.microsoft.com/ado/2008/09/edm"}
    sets = [e.get("Name") for e in root.iter(f"{{{ns['edm']}}}EntitySet")]
    assert sets == ["EntityOf0FI_GL_14", "DeltaLinksOfEntityOf0FI_GL_14"]


def test_full_read_paginates_without_delta(client: TestClient) -> None:
    rows, delta = _read_all(client, f"{GL}/EntityOf0FI_GL_14?$format=json")
    assert len(rows) == 100
    assert delta is None
    assert rows[0]["BUDAT"].startswith("/Date(")


def test_delta_init_delta_and_repeat(client: TestClient) -> None:
    rows, delta_link = _read_all(client, f"{GL}/EntityOf0FI_GL_14", TRACK)
    assert len(rows) == 100 and delta_link

    # Sin cambios: delta vacío
    empty, token_2 = _read_all(client, _path(delta_link))
    assert empty == [] and token_2

    client.post(
        "/_mock/services/ZFI_WS_GL_ITEMS_SRV/changes",
        json={"inserts": 5, "updates": 3, "deletes": 2},
    )
    delta_rows, token_3 = _read_all(client, _path(token_2))
    assert [r["ODQ_CHANGEMODE"] for r in delta_rows].count("C") == 5
    assert len(delta_rows) == 10

    # Repetir con el token anterior devuelve el mismo delta (repeat)
    repeated, token_3b = _read_all(client, _path(token_2))
    assert repeated == delta_rows and token_3b == token_3


def test_invalidated_token_is_rejected(client: TestClient) -> None:
    _, delta_link = _read_all(client, f"{GL}/EntityOf0FI_GL_14", TRACK)
    assert delta_link
    client.post("/_mock/services/ZFI_WS_GL_ITEMS_SRV/invalidate-tokens")
    response = client.get(_path(delta_link))
    assert response.status_code == 404
    assert "suscripción" in response.json()["error"]["message"]["value"]


def test_additive_delta_emits_before_and_after_images(client: TestClient) -> None:
    svc = f"{GATEWAY}/sap/ZSD_WS_SALES_ITEMS_SRV"
    _, link = _read_all(client, f"{svc}/EntityOf2LIS_11_VAITM", TRACK)
    assert link
    client.post("/_mock/services/ZSD_WS_SALES_ITEMS_SRV/changes", json={"updates": 1})
    rows, _ = _read_all(client, _path(link))
    assert [r["ODQ_ENTITYCNTR"] for r in rows] == ["-1", "1"]


def test_cds_without_delta_supports_watermark_filter(client: TestClient) -> None:
    flt = (
        "LastChangeDateTime ge datetime'2026-01-01T00:50:00' "
        "and LastChangeDateTime lt datetime'2026-01-01T01:00:00'"
    )
    rows, delta = _read_all(client, f"{MAT}/ZMM_CV_MATERIAL?$filter={flt}")
    assert len(rows) == 10 and delta is None
    assert client.get(f"{MAT}/ZMM_CV_MATERIAL", headers=TRACK).status_code == 400


def test_error_scenario(client: TestClient) -> None:
    client.post("/_mock/scenario", json={"status": 503, "times": 1})
    assert client.get(f"{GL}/$metadata").status_code == 503
    assert client.get(f"{GL}/$metadata").status_code == 200
