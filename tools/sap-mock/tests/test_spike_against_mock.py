"""El script del spike (tools/spike-sap) se ejecuta de punta a punta contra el mock.

Evita llegar a la sesión con el equipo SAP con un script roto. Los resultados reflejan el
comportamiento del mock, no el de SAP.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from sap_mock.app import GATEWAY, build_metadata, create_app

SPIKE_PATH = Path(__file__).resolve().parents[2] / "spike-sap" / "spike_odp.py"
SERVICE = "ZFI_WS_GL_ITEMS_SRV"
ENTITY_SET = "EntityOf0FI_GL_14"


def _load_spike() -> ModuleType:
    spec = importlib.util.spec_from_file_location("spike_odp", SPIKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["spike_odp"] = module  # dataclasses resuelve anotaciones vía sys.modules
    spec.loader.exec_module(module)
    return module


spike = _load_spike()


def _app() -> FastAPI:
    # 300 filas y páginas de 100: la init tiene 3 páginas y H10 puede cortarla.
    return create_app(user="u", password="p", rows_per_service=300, page_size=1000, seed=7)  # noqa: S106


def _client(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.auth = ("u", "p")
    return client


def _run(client: TestClient, out: Path, *extra: str) -> dict[str, Any]:
    args = spike.parse_args(
        [
            "--base-url",
            "http://testserver",
            "--service",
            SERVICE,
            "--entity-set",
            ENTITY_SET,
            "--user",
            "u",
            "--non-interactive",
            "--page-size",
            "100",
            "--mock-admin-url",
            f"http://testserver/_mock/services/{SERVICE}",
            "--output",
            str(out),
            *extra,
        ]
    )
    code = spike.Spike(args, "p", http=client, admin=client).run()
    run_dir = next(out.iterdir())
    results: dict[str, Any] = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    results["exit_code"] = code
    results["report"] = (run_dir / "report.md").read_text(encoding="utf-8")
    return results


def _outcomes(results: dict[str, Any]) -> dict[str, str]:
    return {h: f["outcome"] for h, f in results["hypotheses"].items()}


def _add_subscription_functions(app: FastAPI) -> None:
    """Simula H12 confirmada: SubscribedTo… y TerminateDeltasFor… en el servicio."""
    svc = app.state.mock.services[SERVICE]
    base = f"{GATEWAY}/sap/{SERVICE}"
    functions = (
        f'<FunctionImport Name="SubscribedTo{ENTITY_SET}" ReturnType="Edm.Boolean" '
        'm:HttpMethod="GET"/>'
        f'<FunctionImport Name="TerminateDeltasFor{ENTITY_SET}" m:HttpMethod="POST"/>'
    )

    def metadata() -> Response:
        xml = build_metadata(svc.definition)
        xml = xml.replace("</EntityContainer>", f"{functions}</EntityContainer>")
        return Response(xml, media_type="application/xml")

    def subscribed() -> dict[str, Any]:
        alive = any(int(t["DeltaToken"][1:]) > svc.invalidated_before for t in svc.issued_tokens)
        return {"d": {f"SubscribedTo{ENTITY_SET}": alive}}

    def terminate() -> Response:
        svc.invalidated_before = svc.seq
        return Response(status_code=204)

    routes: tuple[tuple[str, Callable[..., Any], str], ...] = (
        (f"{base}/$metadata", metadata, "GET"),
        (f"{base}/SubscribedTo{ENTITY_SET}", subscribed, "GET"),
        (f"{base}/TerminateDeltasFor{ENTITY_SET}", terminate, "POST"),
    )
    for path, endpoint, method in routes:
        app.add_api_route(path, endpoint, methods=[method])
        # Delante de la ruta genérica /{service}/{entity_set} del mock.
        app.router.routes.insert(0, app.router.routes.pop())


def test_default_run(tmp_path: Path) -> None:
    results = _run(_client(_app()), tmp_path)
    outcomes = _outcomes(results)
    assert results["exit_code"] == 0
    assert set(outcomes) == set(spike.DEFAULT_HYPOTHESES)
    for hyp in ("H1", "H2", "H3"):
        assert outcomes[hyp] == "se cumple"
    # El mock repite «desde el token hasta ahora» y sus borrados traen la fila completa.
    assert outcomes["H11"] == "se cumple"
    assert "desde el token" in results["hypotheses"]["H11"]["summary"]
    assert outcomes["H17"] == "no se cumple"
    assert outcomes["H14"] == "no ejecutada"  # el mock no tiene importes en CLP ni JPY
    assert outcomes["H15"] == "revisar a mano"
    next_tokens = results["core"]["repeat"]["next_after_repeat"]
    assert next_tokens["repeat"]["ok"] is True
    assert "## Suscripciones abiertas" in results["report"]
    assert "| H11 |" in results["report"]


def test_priority_hypotheses(tmp_path: Path) -> None:
    results = _run(
        _client(_app()),
        tmp_path,
        "--hypotheses",
        "H6,H12,H18",
        "--filter",
        "RBUKRS eq '1000'",
        "--filter-b",
        "RBUKRS eq '2000'",
    )
    outcomes = _outcomes(results)
    assert outcomes == {"H6": "se cumple", "H12": "no se cumple", "H18": "se cumple"}
    assert results["hypotheses"]["H18"]["evidence"]["totals_equal"] is True


def test_h6_needs_second_filter(tmp_path: Path) -> None:
    results = _run(_client(_app()), tmp_path, "--hypotheses", "H6")
    assert _outcomes(results) == {"H6": "no ejecutada"}


def test_terminate_closes_every_chain(tmp_path: Path) -> None:
    app = _app()
    _add_subscription_functions(app)
    client = _client(app)

    without_flag = _run(client, tmp_path / "a", "--hypotheses", "H12")
    assert _outcomes(without_flag) == {"H12": "revisar a mano"}

    results = _run(client, tmp_path / "b", "--hypotheses", "H12", "--allow-terminate")
    finding = results["hypotheses"]["H12"]
    assert finding["outcome"] == "se cumple"
    assert finding["evidence"]["subscribed_before"] is True
    assert finding["evidence"]["subscribed_after"] is False
    assert not any(v is True for v in finding["evidence"]["tokens_alive_after_terminate"].values())


def test_preview_with_subscription_functions(tmp_path: Path) -> None:
    app = _app()
    _add_subscription_functions(app)
    results = _run(_client(app), tmp_path, "--hypotheses", "H4")
    assert _outcomes(results)["H4"] == "se cumple"


def test_remaining_hypotheses(tmp_path: Path) -> None:
    results = _run(
        _client(_app()),
        tmp_path,
        "--hypotheses",
        "H7,H8,H9,H10",
        "--dialog-limit-seconds",
        "600",
    )
    outcomes = _outcomes(results)
    assert outcomes["H7"] == "se cumple"
    assert results["hypotheses"]["H7"]["evidence"]["code"] == "ODQ/032"
    assert outcomes["H8"] == "se cumple"
    assert outcomes["H9"] == "no se cumple"  # el mock ignora $top en la init
    assert outcomes["H10"] == "revisar a mano"
    assert results["hypotheses"]["H10"]["evidence"]["same_page"] is True


def test_retention_reuses_previous_run(tmp_path: Path) -> None:
    client = _client(_app())
    _run(client, tmp_path / "day1", "--hypotheses", "H11")
    previous = next((tmp_path / "day1").iterdir()) / "results.json"
    results = _run(
        client, tmp_path / "day2", "--hypotheses", "H13", "--previous-results", str(previous)
    )
    finding = results["hypotheses"]["H13"]
    assert finding["outcome"] == "revisar a mano"
    assert finding["evidence"]["último token"]["ok"] is True


def test_sap_client_keeps_sap_links_intact(tmp_path: Path) -> None:
    # httpx sustituye la query si recibe params: sap-client no debe borrar $skiptoken ni el token.
    results = _run(_client(_app()), tmp_path, "--hypotheses", "H1,H2", "--client", "100")
    assert _outcomes(results)["H2"] == "se cumple"
    assert results["core"]["init"]["pages"] == 3


def test_unknown_hypothesis_is_rejected() -> None:
    with pytest.raises(SystemExit):
        spike.parse_args(
            [
                *("--base-url", "x", "--service", "s", "--entity-set", "e", "--user", "u"),
                *("--hypotheses", "H99"),
            ]
        )
