"""Informe de la prueba de conexión por pasos (plan 04 §2.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class StepName(StrEnum):
    NETWORK = "network"  # DNS, TCP y TLS (con caducidad del certificado)
    AUTHENTICATION = "authentication"  # catálogo de Gateway con credenciales
    SERVER_INFO = "server_info"  # cabeceras sap-server / versión
    LATENCY = "latency"  # medición de latencia


STEP_ORDER: tuple[StepName, ...] = tuple(StepName)


class StepStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True)
class TestStep:
    __test__ = False  # no es una prueba de pytest

    name: StepName
    status: StepStatus
    message: str
    duration_ms: int = 0
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "details": self.details,
        }


@dataclass(frozen=True, kw_only=True)
class TestReport:
    __test__ = False

    started_at: datetime
    finished_at: datetime
    steps: tuple[TestStep, ...]

    @property
    def ok(self) -> bool:
        return all(s.status in (StepStatus.OK, StepStatus.WARNING) for s in self.steps)

    @property
    def error_code(self) -> str | None:
        return next((s.error_code for s in self.steps if s.status is StepStatus.FAILED), None)

    @property
    def summary(self) -> str:
        failed = next((s for s in self.steps if s.status is StepStatus.FAILED), None)
        if failed is not None:
            return failed.message
        warnings = [s.message for s in self.steps if s.status is StepStatus.WARNING]
        return "Conexión correcta." + (" " + " ".join(warnings) if warnings else "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "error_code": self.error_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "steps": [s.as_dict() for s in self.steps],
        }

    def audit_details(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "steps": {s.name.value: s.status.value for s in self.steps},
        }
