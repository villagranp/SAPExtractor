"""SAP Gateway simulado en proceso: el mismo ``tools/sap-mock`` servido por uvicorn en un hilo.

Así las pruebas recorren red real (TCP, TLS, timeouts) sin contenedores adicionales.
"""

from __future__ import annotations

import importlib
import socket
import ssl
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from tests.support.certs import CertificatePair

MOCK_SRC = Path(__file__).resolve().parents[3] / "tools" / "sap-mock" / "src"
MOCK_USER = "SXTECH"
MOCK_PASSWORD = "Mock.Secreta-2026"


def _create_mock_app(**kwargs: Any) -> Any:
    if str(MOCK_SRC) not in sys.path:
        sys.path.insert(0, str(MOCK_SRC))
    module = importlib.import_module("sap_mock.app")
    factory: Callable[..., Any] = module.create_app
    return factory(**kwargs)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class RunningGateway:
    base_url: str
    server: uvicorn.Server
    thread: threading.Thread
    verify: ssl.SSLContext | bool

    def control(self, path: str, body: dict[str, Any] | None = None) -> None:
        """Administración del mock (``/_mock/*``): escenarios de error, cambios, reinicio."""
        response = httpx.post(f"{self.base_url}{path}", json=body or {}, verify=self.verify)
        response.raise_for_status()

    def scenario(self, *, status: int | None = None, times: int = 1, delay: float = 0) -> None:
        self.control("/_mock/scenario", {"status": status, "times": times, "delay_seconds": delay})

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def start_gateway(tls: CertificatePair | None = None, rows: int = 50) -> RunningGateway:
    app = _create_mock_app(
        user=MOCK_USER, password=MOCK_PASSWORD, rows_per_service=rows, page_size=20, seed=7
    )
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        ssl_certfile=str(tls.cert_path) if tls else None,
        ssl_keyfile=str(tls.key_path) if tls else None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            msg = "El Gateway simulado no arrancó"
            raise RuntimeError(msg)
        time.sleep(0.05)
    scheme = "https" if tls else "http"
    return RunningGateway(
        base_url=f"{scheme}://127.0.0.1:{port}",
        server=server,
        thread=thread,
        verify=ssl.create_default_context(cafile=str(tls.cert_path)) if tls else False,
    )
