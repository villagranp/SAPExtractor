"""Ejecución de prueba de un pipeline (plan 08 §5 y §6): «Probar pipeline».

Recorre el camino de un run con una muestra, sin tocar nada del pipeline real:

* **Lectura**: las primeras filas con ``$top`` y los filtros del pipeline, sin
  ``Prefer: odata.track-changes``; no abre suscripción en la ODQ ni mueve el estado de delta.
* **Carga**: la muestra se lleva a un destino de prueba (``SXTEST_<8 hex>_<nombre>``) con las
  mismas opciones de carga y mapeo; la tabla o carpeta real no se toca.
* **Limpieza**: el destino de prueba se borra al terminar, también si la carga falló.

No crea ningún run. El resultado y la huella de la configuración probada quedan en el pipeline:
la comprobación previa a activar exige una prueba correcta y vigente.
"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import update
from sqlalchemy.orm import Session

from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import BusinessRuleError, DomainError, NotFoundError
from sapextract.core.logging import safe_trace
from sapextract.dataflow.domain.batch import BatchInfo, RunMode, batch_id
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    DestinationWriter,
    PageMetrics,
    PreparedTarget,
    ReadContext,
    target_name_for_test,
)
from sapextract.dataflow.infrastructure.parquet_spool import (
    DirectorySpool,
    InvalidSpoolError,
    SpoolWriter,
    spool_path,
)
from sapextract.extraction.application.resolve import ExtractionToolkit, ResolvedPipeline, resolve
from sapextract.extraction.domain.model import StepStatus
from sapextract.identity.domain.model import Principal
from sapextract.orchestration.application.trial import config_fingerprint
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sources.infrastructure.models import Source

log = structlog.get_logger(__name__)

ERROR_UNEXPECTED = "SX-PIPE-TEST-UNEXPECTED"
ERROR_TIMEOUT = "SX-PIPE-TEST-TIMEOUT"
ERROR_DISABLED = "SX-PIPE-TEST-DISABLED"
SPOOL_FOLDER = "trial-spool"


class TrialStep(StrEnum):
    CONFIGURATION = "configuration"
    READ = "read"
    LOAD = "load"
    CLEANUP = "cleanup"


# Un fallo al borrar el destino de prueba no invalida la prueba: la carga funcionó.
REQUIRED_STEPS = (TrialStep.CONFIGURATION, TrialStep.READ, TrialStep.LOAD)


@dataclass(frozen=True, kw_only=True)
class TrialStepResult:
    step: TrialStep
    status: StepStatus
    message: str
    duration_ms: int | None = None
    error_code: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step.value,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, kw_only=True)
class TrialResult:
    ok: bool
    tested_at: datetime
    rows_read: int
    rows_loaded: int
    target: str | None
    duration_ms: int
    steps: list[TrialStepResult]

    @property
    def error_code(self) -> str | None:
        return next((s.error_code for s in self.steps if s.error_code), None)

    @property
    def failed_step(self) -> TrialStep | None:
        return next((s.step for s in self.steps if s.status is StepStatus.FAILED), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tested_at": self.tested_at.isoformat(),
            "rows_read": self.rows_read,
            "rows_loaded": self.rows_loaded,
            "target": self.target,
            "duration_ms": self.duration_ms,
            "steps": [s.to_json() for s in self.steps],
        }


@dataclass
class _State:
    resolved: ResolvedPipeline | None = None
    spool: DirectorySpool | None = None
    prepared: PreparedTarget | None = None
    batch: str = ""
    rows_read: int = 0
    rows_loaded: int = 0
    target: str | None = None
    steps: list[TrialStepResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.status is StepStatus.FAILED and s.step in REQUIRED_STEPS for s in self.steps)

    def done(self, step: TrialStep) -> bool:
        return any(s.step is step for s in self.steps)

    def skip(self, step: TrialStep, message: str) -> None:
        if not self.done(step):
            self.steps.append(
                TrialStepResult(step=step, status=StepStatus.SKIPPED, message=message)
            )

    def fail(self, step: TrialStep, exc: Exception, started: float) -> None:
        code, message = _describe(exc)
        self.steps.append(
            TrialStepResult(
                step=step,
                status=StepStatus.FAILED,
                message=message,
                duration_ms=_elapsed(started),
                error_code=code,
            )
        )

    def run(self, step: TrialStep, action: Callable[[], str]) -> bool:
        """Ejecuta un paso; ``action`` devuelve el texto del resultado. ``False`` si falló."""
        started = time.perf_counter()
        try:
            message = action()
        except Exception as exc:
            self.fail(step, exc, started)
            return False
        self.steps.append(
            TrialStepResult(
                step=step,
                status=StepStatus.SUCCEEDED,
                message=message,
                duration_ms=_elapsed(started),
            )
        )
        return True


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _describe(exc: Exception) -> tuple[str, str]:
    """Código y texto para la pantalla. El texto de un error inesperado no se muestra."""
    if isinstance(exc, DataflowError):
        return exc.code, exc.message
    if isinstance(exc, DomainError):
        return exc.error_code, exc.detail
    if isinstance(exc, InvalidSpoolError):
        return "SX-RUN-SPOOL-INVALID", str(exc)
    log.error("pipeline_trial_failed", error_type=type(exc).__name__, trace=safe_trace(exc))
    return ERROR_UNEXPECTED, f"Error interno ({type(exc).__name__}). Revise el log técnico."


def _rows(count: int) -> str:
    return "1 fila" if count == 1 else f"{count} filas"


class PipelineTrialService:
    """Caso de uso «Probar pipeline». Una instancia por petición."""

    def __init__(
        self, session: Session, toolkit: ExtractionToolkit, *, spool_root: Path | None = None
    ) -> None:
        self.session = session
        self.tk = toolkit
        self.spool_root = spool_root or toolkit.destinations.context.work_dir / SPOOL_FOLDER

    def run(self, pipeline_id: uuid.UUID, actor: Principal, request: RequestInfo) -> TrialResult:
        pipeline = self.session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.deleted_at is not None:
            msg = "El pipeline no existe."
            raise NotFoundError(msg, code="SX-PIPE-NOT-FOUND")
        source = self.session.get(Source, pipeline.source_id)
        if source is None:
            msg = "El origen del pipeline no existe."
            raise NotFoundError(msg, code="SX-SRC-NOT-FOUND")
        if not source.preview_enabled:
            msg = (
                "El origen tiene desactivada la lectura de muestra, así que el pipeline no se "
                "puede probar. La comprobación previa a activar no exige la prueba en ese caso."
            )
            raise BusinessRuleError(msg, code=ERROR_DISABLED)
        code = pipeline.code
        fingerprint = config_fingerprint(pipeline, source)

        started = time.perf_counter()
        state = _State()
        self._execute(state, pipeline_id)
        result = TrialResult(
            ok=not state.failed,
            tested_at=utcnow(),
            rows_read=state.rows_read,
            rows_loaded=state.rows_loaded,
            target=state.target,
            duration_ms=_elapsed(started),
            steps=state.steps,
        )
        self._save(pipeline_id, code, fingerprint, result, actor=actor, request=request)
        return result

    # ------------------------------------------------------------------ pasos
    def _execute(self, state: _State, pipeline_id: uuid.UUID) -> None:
        state.run(TrialStep.CONFIGURATION, lambda: self._resolve(state, pipeline_id))
        self.session.commit()  # sin transacción abierta durante las llamadas a SAP y al destino
        if state.failed:
            self._skip_rest(state)
            return
        try:
            state.run(TrialStep.READ, lambda: self._read(state, pipeline_id))
            if state.failed:
                self._skip_rest(state)
                return
            self._load(state, pipeline_id)
        finally:
            if state.batch:
                shutil.rmtree(spool_path(self.spool_root, state.batch), ignore_errors=True)

    @staticmethod
    def _skip_rest(state: _State) -> None:
        skipped = "No se ejecutó: un paso anterior falló."
        for step in (TrialStep.READ, TrialStep.LOAD, TrialStep.CLEANUP):
            state.skip(step, skipped)

    def _resolve(self, state: _State, pipeline_id: uuid.UUID) -> str:
        state.resolved = resolve(self.session, pipeline_id, self.tk, sample=True)
        return "El pipeline, su origen y su destino se pueden usar."

    def _read(self, state: _State, pipeline_id: uuid.UUID) -> str:
        resolved = _resolved(state)
        settings = self.tk.settings
        limit = settings.test_run_rows
        run_id = uuid.uuid4()
        state.batch = batch_id(RunMode.FULL, pipeline_id=pipeline_id, run_id=run_id)
        deadline = time.monotonic() + settings.test_run_timeout_seconds
        pages: list[PageMetrics] = []
        ctx = ReadContext(
            run_id=run_id,
            batch_id=state.batch,
            page_size=min(resolved.page_size, limit),
            page_timeout_seconds=settings.test_run_timeout_seconds,
            is_cancelled=lambda: time.monotonic() > deadline,
            on_page=pages.append,
        )
        filters = {"$filter": resolved.rendered_filter} if resolved.rendered_filter else {}
        result = resolved.connector.read_sample(filters, ctx, rows=limit)
        self.spool_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with SpoolWriter(
            self.spool_root,
            batch_id=state.batch,
            pipeline_id=pipeline_id,
            run_id=run_id,
            mode=RunMode.FULL,
            schema=resolved.schema,
            worker_id="trial",
        ) as writer:
            for batch in result.batches():
                try:
                    writer.write(batch)
                except ValueError as exc:
                    raise DataflowError(
                        str(exc), kind=ErrorKind.DATA, code="SX-SRC-SCHEMA-MISMATCH"
                    ) from exc
            if time.monotonic() > deadline:
                msg = (
                    f"SAP tardó más de {settings.test_run_timeout_seconds} s en devolver la "
                    "muestra. Revise los filtros o el rendimiento del servicio."
                )
                raise DataflowError(msg, kind=ErrorKind.TRANSIENT, code=ERROR_TIMEOUT)
            writer.finish(next_token=None, created_at=utcnow())
        state.spool = DirectorySpool.open(
            self.spool_root, state.batch, schema_hash=resolved.schema.schema_hash()
        )
        state.rows_read = state.spool.manifest.rows
        if state.rows_read == 0:
            return (
                "SAP respondió sin filas con los filtros del pipeline: la carga se prueba sin "
                "datos. No se abrió ninguna suscripción de delta."
            )
        latency = sum(p.latency_ms for p in pages)
        return (
            f"SAP devolvió {_rows(state.rows_read)} en {latency} ms. No se abrió ninguna "
            "suscripción de delta."
        )

    def _load(self, state: _State, pipeline_id: uuid.UUID) -> None:
        resolved = _resolved(state)
        started = time.perf_counter()
        try:
            with resolved.open_writer() as writer:
                state.run(TrialStep.LOAD, lambda: self._apply(state, writer, pipeline_id))
                if state.prepared is None:
                    state.skip(TrialStep.CLEANUP, "No se creó ningún destino de prueba.")
                else:
                    state.run(TrialStep.CLEANUP, lambda: self._drop(state, writer))
        except Exception as exc:  # abrir o cerrar la conexión con el destino
            if not state.done(TrialStep.LOAD):
                state.fail(TrialStep.LOAD, exc, started)
            state.skip(TrialStep.CLEANUP, "No se creó ningún destino de prueba.")

    def _apply(self, state: _State, writer: DestinationWriter, pipeline_id: uuid.UUID) -> str:
        resolved = _resolved(state)
        spool = state.spool
        if spool is None:  # pragma: no cover - lo fija la lectura
            msg = "La prueba no pasó por la lectura"
            raise RuntimeError(msg)
        options = replace(
            resolved.options,
            target_name=target_name_for_test(resolved.options.target_name, uuid.uuid4().hex[:8]),
            create_if_missing=True,
        )
        prepared = writer.prepare(resolved.schema, options)
        state.prepared, state.target = prepared, options.target_name
        stage = writer.stage(prepared, spool)
        try:
            result = writer.apply(
                stage,
                BatchInfo(
                    pipeline_id=pipeline_id,
                    run_id=spool.manifest.run_id,
                    batch_id=state.batch,
                    mode=RunMode.FULL,
                    schema_hash=resolved.schema.schema_hash(),
                    next_token=None,
                    window_end=None,
                ),
            )
        finally:
            try:
                writer.cleanup(stage)
            except Exception:
                log.warning("pipeline_trial_stage_cleanup_failed", pipeline_id=str(pipeline_id))
        state.rows_loaded = result.rows_inserted + result.rows_updated
        return (
            f"{_rows(state.rows_loaded).capitalize()} en el destino de prueba "
            f"{options.target_name} de {resolved.destination_label}, con el modo de carga y el "
            "mapeo del pipeline."
        )

    @staticmethod
    def _drop(state: _State, writer: DestinationWriter) -> str:
        if state.prepared is None:  # pragma: no cover - lo comprueba el llamador
            return "No se creó ningún destino de prueba."
        try:
            writer.drop_test_target(state.prepared)
        except DataflowError as exc:
            msg = (
                f"El destino de prueba {state.target} no se pudo borrar: bórrelo a mano. "
                f"{exc.message}"
            )
            raise DataflowError(msg, kind=exc.kind, code=exc.code) from exc
        return f"El destino de prueba {state.target} se borró."

    # ------------------------------------------------------------------ resultado
    def _save(
        self,
        pipeline_id: uuid.UUID,
        code: str,
        fingerprint: str,
        result: TrialResult,
        *,
        actor: Principal,
        request: RequestInfo,
    ) -> None:
        # ``UPDATE`` directo: guardar la prueba no cambia la versión del pipeline, así que no
        # invalida una edición que alguien tenga abierta.
        self.session.execute(
            update(Pipeline)
            .where(Pipeline.id == pipeline_id)
            .values(
                last_test_at=result.tested_at,
                last_test_ok=result.ok,
                last_test_result=result.to_json(),
                last_test_fingerprint=fingerprint,
            )
        )
        failed = result.failed_step
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="pipeline.tested",
                outcome=Outcome.SUCCESS if result.ok else Outcome.FAILURE,
                resource_type="pipeline",
                resource_id=str(pipeline_id),
                resource_name=code,
                details={
                    "ok": result.ok,
                    "rows_read": result.rows_read,
                    "rows_loaded": result.rows_loaded,
                    "failed_step": failed.value if failed else None,
                    "error_code": result.error_code,
                    "duration_ms": result.duration_ms,
                },
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()


def _resolved(state: _State) -> ResolvedPipeline:
    if state.resolved is None:  # pragma: no cover - lo fija el primer paso
        msg = "La prueba no pasó por la configuración"
        raise RuntimeError(msg)
    return state.resolved
