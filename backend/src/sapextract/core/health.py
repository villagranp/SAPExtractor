"""Endpoints de salud (para el orquestador de contenedores) e información del sistema."""

from __future__ import annotations

from enum import StrEnum

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from sapextract import __version__
from sapextract.core.dependencies import DatabaseDep, SettingsDep
from sapextract.core.migrations import current_revisions, head_revisions

log = structlog.get_logger(__name__)

router = APIRouter(tags=["Sistema"])


class CheckStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"


class CheckResult(BaseModel):
    status: CheckStatus
    detail: str | None = None


class HealthReport(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckResult]


class SystemInfo(BaseModel):
    name: str
    version: str
    environment: str
    build_commit: str


@router.get("/health/live", response_model=HealthReport, summary="El proceso responde")
def live() -> HealthReport:
    return HealthReport(status=CheckStatus.OK, checks={})


@router.get(
    "/health/ready",
    response_model=HealthReport,
    summary="Listo para recibir tráfico (BD accesible y migraciones al día)",
    responses={503: {"model": HealthReport}},
)
def ready(db: DatabaseDep, response: Response) -> HealthReport:
    checks: dict[str, CheckResult] = {}
    try:
        db.ping()
        checks["database"] = CheckResult(status=CheckStatus.OK)
    except Exception as exc:
        log.warning("health_database_failed", error_type=type(exc).__name__)
        checks["database"] = CheckResult(
            status=CheckStatus.FAIL, detail="Base de metadatos no accesible"
        )

    if checks["database"].status is CheckStatus.OK:
        try:
            current, heads = current_revisions(db.engine), head_revisions()
            checks["migrations"] = (
                CheckResult(status=CheckStatus.OK)
                if current == heads
                else CheckResult(
                    status=CheckStatus.FAIL,
                    detail=(
                        f"Migraciones pendientes (actual={sorted(current)}, "
                        f"esperado={sorted(heads)})"
                    ),
                )
            )
        except Exception as exc:
            log.warning("health_migrations_failed", error_type=type(exc).__name__)
            checks["migrations"] = CheckResult(
                status=CheckStatus.FAIL, detail="No se pudo leer la versión"
            )

    overall = (
        CheckStatus.OK
        if all(c.status is CheckStatus.OK for c in checks.values())
        else CheckStatus.FAIL
    )
    if overall is CheckStatus.FAIL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthReport(status=overall, checks=checks)


@router.get("/system/info", response_model=SystemInfo, summary="Versión y entorno")
def system_info(settings: SettingsDep) -> SystemInfo:
    return SystemInfo(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        build_commit=settings.build_commit,
    )
