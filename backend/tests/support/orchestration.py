"""Dobles y ayudas para probar la orquestación sin el motor de extracción (It5).

``ScriptedExecutor`` ocupa el lugar del motor: cada run ejecuta el siguiente paso del guion.
``FakeClock`` es el reloj de la orquestación: las pruebas adelantan la hora en vez de esperar.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select

from sapextract.core.clock import utcnow
from sapextract.core.db import Database
from sapextract.orchestration.application.scheduler import SchedulerService
from sapextract.orchestration.application.toolkit import OrchestrationToolkit
from sapextract.orchestration.application.worker import WorkerRuntime
from sapextract.orchestration.domain.ports import RunContext, RunExecutor
from sapextract.orchestration.infrastructure.models import Pipeline, Run, RunAttempt, Schedule
from tests.support.sap_gateway import MOCK_PASSWORD, MOCK_USER, RunningGateway

GL_SERVICE, GL_SET = "ZFI_WS_GL_ITEMS_SRV", "EntityOf0FI_GL_14"  # delta ODQ, after_image
SALES_SERVICE, SALES_SET = "ZSD_WS_SALES_ITEMS_SRV", "EntityOf2LIS_11_VAITM"  # before_after
MATERIAL_SERVICE, MATERIAL_SET = "ZMM_CV_MATERIAL_CDS", "ZMM_CV_MATERIAL"  # CDS sin delta


class FakeClock:
    """Reloj que solo avanza cuando la prueba lo pide."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


type Step = Callable[[RunContext], None]


class ScriptedExecutor:
    """Ejecutor de pruebas: sin guion, el run termina bien."""

    def __init__(self, *steps: Step) -> None:
        self.steps: list[Step] = list(steps)
        self.calls: list[RunContext] = []

    def execute(self, ctx: RunContext) -> None:
        self.calls.append(ctx)
        if self.steps:
            self.steps.pop(0)(ctx)


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}".upper()


@dataclass
class OrchEnv:
    """Entorno compartido: API con reloj falso, Gateway simulado y carpeta CSV."""

    app: FastAPI
    db: Database
    toolkit: OrchestrationToolkit
    clock: FakeClock
    gateway: RunningGateway
    csv_root: Path

    # ------------------------------------------------------------------ objetos vía API
    def system(self, api: Any, environment: str = "DEV", **extra: Any) -> dict[str, Any]:
        response = api.post(
            "/sap-systems",
            {
                "code": unique("SYS_"),
                "name": f"S/4HANA {environment}",
                "environment": environment,
                "system_type": "S4HANA_ONPREM",
                "base_url": self.gateway.base_url,
                "sap_client": "100",
                "language": "ES",
                "timeout_seconds": 5,
                # PRD exige TLS verificado; el Gateway simulado habla HTTP en local.
                "credentials": {
                    "auth_type": "basic",
                    "username": MOCK_USER,
                    "password": MOCK_PASSWORD,
                },
                "activate": True,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        system: dict[str, Any] = response.json()["system"]
        return system

    def source(
        self,
        api: Any,
        system_id: str,
        service: str = GL_SERVICE,
        entity_set: str = GL_SET,
        *,
        activate: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        response = api.post(
            "/sources",
            {
                "sap_system_id": system_id,
                "code": unique("SRC_"),
                "name": f"Origen {entity_set}",
                "service_name": service,
                "entity_set": entity_set,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        source: dict[str, Any] = response.json()
        if activate:
            activated = api.post(f"/sources/{source['id']}/activate")
            assert activated.status_code == 200, activated.text
            source = activated.json()
        return source

    def destination(
        self, api: Any, environment: str = "DEV", *, activate: bool = True
    ) -> dict[str, Any]:
        response = api.post(
            "/destinations",
            {
                "code": unique("CSV_"),
                "name": "Carpeta de intercambio",
                "type": "csv",
                "environment": environment,
                "config": {"root": str(self.csv_root), "path": "orquestacion"},
                "activate": activate,
            },
        )
        assert response.status_code == 201, response.text
        destination: dict[str, Any] = response.json()["destination"]
        return destination

    def pipeline(
        self,
        api: Any,
        *,
        source: dict[str, Any] | None = None,
        destination: dict[str, Any] | None = None,
        mode: str = "FULL",
        load_mode: str = "replace",
        activate: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        """Pipeline listo para ejecutar; por defecto carga completa a CSV en DEV."""
        if source is None:
            source = self.source(api, self.system(api)["id"])
        destination = destination or self.destination(api)
        response = api.post(
            "/pipelines",
            {
                "code": unique("PL_"),
                "name": "Pipeline de prueba",
                "source_id": source["id"],
                "destination_id": destination["id"],
                "extraction_mode": mode,
                "load": {"load_mode": load_mode},
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        pipeline: dict[str, Any] = response.json()
        if activate:
            activated = api.post(f"/pipelines/{pipeline['id']}/activate", {})
            assert activated.status_code == 200, activated.text
            pipeline = activated.json()
        return pipeline

    def run_now(self, api: Any, pipeline_id: str, **body: Any) -> dict[str, Any]:
        response = api.post(f"/pipelines/{pipeline_id}/run", body)
        assert response.status_code == 201, response.text
        run: dict[str, Any] = response.json()
        return run

    # ------------------------------------------------------------------ procesos
    def worker(
        self, executor: RunExecutor | None, pool: str, *, register: bool = True
    ) -> WorkerRuntime:
        runtime = WorkerRuntime(self.db, self.toolkit, executor=executor, pools=[pool])
        if register:
            runtime.register()
        return runtime

    def scheduler(self) -> SchedulerService:
        return SchedulerService(self.db, self.toolkit)

    # ------------------------------------------------------------------ lectura directa
    def run(self, run_id: str | uuid.UUID) -> Run:
        with self.db.session() as session:
            run = session.get(Run, uuid.UUID(str(run_id)))
            assert run is not None
            return run

    def runs_of(self, pipeline_id: str | uuid.UUID) -> list[Run]:
        with self.db.session() as session:
            stmt = (
                select(Run)
                .where(Run.pipeline_id == uuid.UUID(str(pipeline_id)))
                .order_by(Run.created_at, Run.id)
            )
            return list(session.scalars(stmt))

    def attempts(self, run_id: str | uuid.UUID) -> list[RunAttempt]:
        with self.db.session() as session:
            stmt = (
                select(RunAttempt)
                .where(RunAttempt.run_id == uuid.UUID(str(run_id)))
                .order_by(RunAttempt.number)
            )
            return list(session.scalars(stmt))

    def schedules_of(self, pipeline_id: str | uuid.UUID) -> list[Schedule]:
        with self.db.session() as session:
            stmt = select(Schedule).where(Schedule.pipeline_id == uuid.UUID(str(pipeline_id)))
            return list(session.scalars(stmt))

    def copies(self, pipeline_id: str, count: int, **overrides: Any) -> list[uuid.UUID]:
        """Copias activas de un pipeline, insertadas sin pasar por la API (pruebas de carga)."""
        ids: list[uuid.UUID] = []
        with self.db.session() as session:
            base = session.get(Pipeline, uuid.UUID(pipeline_id))
            assert base is not None
            for _ in range(count):
                values = {
                    c.name: getattr(base, c.name)
                    for c in Pipeline.__table__.columns
                    if c.name not in {"id", "code", "version"}
                }
                values.update(overrides)
                copy = Pipeline(code=unique("PL_"), **values)
                session.add(copy)
                session.flush()
                ids.append(copy.id)
            session.commit()
        return ids
