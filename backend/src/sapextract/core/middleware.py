"""Middlewares ASGI puros (compatibles con respuestas en streaming / SSE)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sapextract.core.context import set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# La API solo devuelve JSON: CSP restrictiva salvo en la documentación interactiva (Swagger UI).
API_CSP = "default-src 'none'; frame-ancestors 'none'"
_DOCS_SUFFIXES = ("/docs", "/docs/oauth2-redirect", "/redoc")

access_log = structlog.get_logger("sapextract.access")


class RequestContextMiddleware:
    """Asigna ``request_id``, cabeceras de seguridad, límite de tamaño y log de acceso."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, hsts: bool = False) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid7().hex
        set_request_id(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        content_length = headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self.max_body_bytes
        ):
            response = JSONResponse(
                {"code": "SX-HTTP-413", "title": "Petición demasiado grande", "status": 413},
                status_code=413,
                headers={REQUEST_ID_HEADER: request_id},
            )
            await response(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                out = MutableHeaders(scope=message)
                out[REQUEST_ID_HEADER] = request_id
                for name, value in SECURITY_HEADERS.items():
                    out.setdefault(name, value)
                if not str(scope.get("path", "")).endswith(_DOCS_SUFFIXES):
                    out.setdefault("Content-Security-Policy", API_CSP)
                if self.hsts:
                    out.setdefault(
                        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_log.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
