"""Entrypoint de la API: ``uvicorn sapextract.main_api:create_app --factory``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

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
from sapextract.destinations.api import routes as destination_routes
from sapextract.destinations.application.toolkit import DestinationsToolkit
from sapextract.identity.api import admin_routes, auth_routes, idp_routes
from sapextract.identity.application.toolkit import IdentityToolkit
from sapextract.orchestration.api import routes as orchestration_routes
from sapextract.orchestration.application import pipeline_service as pipelines
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.sap_connections.api import routes as sap_routes
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sources.api import routes as source_routes
from sapextract.sources.application.source_service import sap_system_dependents

log = structlog.get_logger(__name__)


def build_api_router() -> APIRouter:
    """Agrega los routers de todos los módulos bajo el prefijo de versión."""
    api = APIRouter(responses={"default": {"model": ProblemDetails, "description": "Error"}})
    api.include_router(health.router)
    # Los módulos se registran aquí a medida que se implementan (ver docs/plan/ROADMAP.md).
    api.include_router(auth_routes.router)
    api.include_router(admin_routes.router)
    api.include_router(idp_routes.router)
    api.include_router(audit_routes.router)
    api.include_router(sap_routes.router)
    api.include_router(source_routes.router)
    api.include_router(destination_routes.router)
    for router in orchestration_routes.routers:
        api.include_router(router)
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
    # Un sistema SAP con orígenes vivos no se puede dar de baja (plan 04).
    app.state.sap.dependents.append(sap_system_dependents)
    app.state.destinations = DestinationsToolkit.build(settings, audit=app.state.identity.audit)
    app.state.orchestration = OrchestrationToolkit.build(
        settings,
        audit=app.state.identity.audit,
        sap=app.state.sap,
        destinations=app.state.destinations,
    )
    # Un pipeline impide dar de baja su sistema SAP, su origen y su destino (plan 08).
    app.state.sap.dependents.append(pipelines.sap_system_dependents)
    app.state.sap.source_dependents.append(pipelines.source_dependents)
    app.state.destinations.dependents.append(pipelines.destination_dependents)
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
    # El más externo: la IP del cliente (auditoría, límite de intentos) solo se toma de
    # X-Forwarded-For si la petición llega de un proxy de confianza (plan 13, C-07 d).
    # uvicorn se arranca con --no-proxy-headers para no aplicarlo dos veces.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)
    app.include_router(build_api_router(), prefix=prefix)
    return app
