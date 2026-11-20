"""Informe de la prueba de conexión de un destino (pasos con estado, como en los sistemas SAP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CheckStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True)
class CheckStep:
    name: str  # p. ej. network, authentication, context, privileges, write
    status: CheckStatus
    message: str
    duration_ms: int = 0
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "details": self.details,
        }


@dataclass(frozen=True, kw_only=True)
class CheckReport:
    started_at: datetime
    finished_at: datetime
    steps: tuple[CheckStep, ...]

    @property
    def ok(self) -> bool:
        return all(s.status in (CheckStatus.OK, CheckStatus.WARNING) for s in self.steps)

    @property
    def error_code(self) -> str | None:
        return next((s.error_code for s in self.steps if s.status is CheckStatus.FAILED), None)

    @property
    def summary(self) -> str:
        failed = next((s for s in self.steps if s.status is CheckStatus.FAILED), None)
        if failed is not None:
            return failed.message
        warnings = [s.message for s in self.steps if s.status is CheckStatus.WARNING]
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
            "steps": {s.name: s.status.value for s in self.steps},
        }


def skipped_after_failure(steps: list[CheckStep], names: tuple[str, ...]) -> list[CheckStep]:
    """Completa los pasos que no se ejecutaron por un fallo anterior."""
    done = {s.name for s in steps}
    return steps + [
        CheckStep(
            name=n, status=CheckStatus.SKIPPED, message="No se ejecutó por un fallo anterior."
        )
        for n in names
        if n not in done
    ]
