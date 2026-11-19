"""Entrypoint de la API: ``uvicorn sapextract.main_api:create_app --factory``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sapextract import __version__
from sapextract.audit.api import routes as audit_routes
from sapextract.core import health
from sapextract.core.config import Settings, get_settings
from sapextract.core.db import Database
from sapextract.core.errors import ProblemDetails, register_exception_handlers
from sapextract.core.events import EventBus
from sapextract.core.logging import configure_logging
from sapextract.core.middleware import RequestContextMiddleware
from sapextract.core.ratelimit import SlidingWindowLimiter
from sapextract.identity.api import admin_routes, auth_routes
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.sap_connections.api import routes as sap_routes
from sapextract.sap_connections.application.toolkit import SapToolkit

log = structlog.get_logger(__name__)


def build_api_router() -> APIRouter:
    """Agrega los routers de todos los módulos bajo el prefijo de versión."""
    api = APIRouter(responses={"default": {"model": ProblemDetails, "description": "Error"}})
    api.include_router(health.router)
    # Los módulos se registran aquí a medida que se implementan (ver docs/plan/ROADMAP.md).
    api.include_router(auth_routes.router)
    api.include_router(admin_routes.router)
    api.include_router(audit_routes.router)
    api.include_router(sap_routes.router)
    return api


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api_starting", version=__version__, environment=settings.environment.value)
        yield
        app.state.db.dispose()
        log.info("api_stopped")

    prefix = settings.api_prefix
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="API de la plataforma de orquestación de extracciones SAP.",
        lifespan=lifespan,
        openapi_url=f"{prefix}/openapi.json" if settings.docs_enabled else None,
        docs_url=f"{prefix}/docs" if settings.docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.events = EventBus()
    app.state.identity = IdentityToolkit.build(settings)
    app.state.sap = SapToolkit.build(settings, audit=app.state.identity.audit)
    app.state.auth_limiter = SlidingWindowLimiter(settings.auth_rate_limit_per_minute)

    register_exception_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )
    app.add_middleware(
        RequestContextMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
        hsts=settings.is_production,
    )
    app.include_router(build_api_router(), prefix=prefix)
    return app
