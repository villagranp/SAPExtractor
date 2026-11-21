"""Motor de extracción (plan 06 §2): ``prepare → extract → load → commit_delta → cleanup``.

Implementa el ``RunExecutor`` de la orquestación (plan 08): el worker toma el run, mantiene el
latido y cierra el intento; el motor hace el trabajo y confirma. Garantías:

* El token confirmado solo avanza en ``commit_delta``, después de que el destino confirmó la
  carga y en la misma transacción que marca el run como exitoso (``EngineStore.commit``).
* Si el lote ya está en la tabla de control del destino, ``prepare`` salta la extracción y la
  carga y confirma el token guardado allí (caída entre los dos commits).
* Un reintento en el mismo worker reutiliza el spool sin volver a llamar a SAP.
* Un worker que perdió el run (``lease_epoch``) no confirma nada: se detiene como ``fenced``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from sapextract.core.clock import utcnow
from sapextract.core.db import Database
from sapextract.core.exceptions import DomainError
from sapextract.dataflow.domain.batch import BatchInfo, RunMode
from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.ports import (
    AppliedBatch,
    DestinationWriter,
    PageMetrics,
    ReadContext,
    ReadResult,
    StageInfo,
    Window,
)
from sapextract.dataflow.infrastructure.parquet_spool import (
    DirectorySpool,
    InvalidSpoolError,
    SpoolWriter,
)
from sapextract.extraction.application.resolve import ExtractionToolkit, ResolvedPipeline, resolve
from sapextract.extraction.application.store import (
    DELTA_RUN_MODES,
    Claim,
    EngineStore,
    FencedError,
    Totals,
)
from sapextract.extraction.domain.model import (
    RETRY_DELTA_INIT,
    LogLevel,
    NotRetryableError,
    StepName,
    StepStatus,
)
from sapextract.extraction.infrastructure.odata_connector import local_moment
from sapextract.orchestration.domain.ports import RunContext, RunStoppedError, StopReason
from sapextract.sources.domain.model import WatermarkKind

log = structlog.get_logger(__name__)

# Primera ventana: desde el principio. Hora local SAP, sin zona (H15).
WATERMARK_ORIGIN = datetime(1900, 1, 1)  # noqa: DTZ001
PROGRESS_EVERY_PAGES = 10
STOP_MESSAGES = {
    StopReason.CANCELLED: "Run cancelado: el token no avanza.",
    StopReason.SHUTDOWN: "Apagado del worker: el run vuelve a la cola.",
    StopReason.TIMEOUT: "Se superó el tiempo máximo del run.",
    StopReason.FENCED: "Este intento perdió el run (otro worker lo tomó): no confirma nada.",
}


@dataclass
class _Pages:
    pages: int = 0
    rows: int = 0
    bytes: int = 0
    latency_ms: int = 0
    on_progress: Callable[[_Pages], None] | None = None

    def add(self, page: PageMetrics) -> None:
        self.pages += 1
        self.rows += page.rows
        self.bytes += page.bytes
        self.latency_ms += page.latency_ms
        if self.on_progress and (self.pages == 1 or self.pages % PROGRESS_EVERY_PAGES == 0):
            self.on_progress(self)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "rows": self.rows,
            "bytes": self.bytes,
            "sap_latency_ms": self.latency_ms,
        }


@dataclass
class _Attempt:
    """Estado de un intento mientras se ejecuta."""

    ctx: RunContext
    claim: Claim | None = None
    resolved: ResolvedPipeline | None = None
    batch_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    next_token: str | None = None
    totals: Totals = field(default_factory=Totals)
    spool: DirectorySpool | None = None
    stage: StageInfo | None = None
    writer: DestinationWriter | None = None


class ExtractionEngine:
    """``RunExecutor`` de la orquestación."""

    def __init__(
        self,
        db: Database,
        toolkit: ExtractionToolkit,
        *,
        spool_root: Path | None = None,
        store: EngineStore | None = None,
    ) -> None:
        self.db = db
        self.tk = toolkit
        self.store = store or EngineStore(db)
        self.spool_root = spool_root or toolkit.settings.spool_dir
        self.spool_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # ================================================================ entrada
    def execute(self, ctx: RunContext) -> None:
        self._log(ctx, None, LogLevel.INFO, f"Intento {ctx.attempt} en el worker {ctx.worker_id}")
        state = _Attempt(ctx=ctx)
        try:
            with ExitStack() as stack:
                self._run(state, stack)
        except FencedError:
            self._log(ctx, None, LogLevel.WARNING, STOP_MESSAGES[StopReason.FENCED])
            raise RunStoppedError(StopReason.FENCED) from None
        except RunStoppedError as stop:
            self._log(ctx, None, LogLevel.WARNING, STOP_MESSAGES[stop.reason])
            raise
        except DataflowError as exc:
            self._log(ctx, None, LogLevel.ERROR, f"{exc.code}: {exc.message}", {"kind": exc.kind})
            mode = state.claim.mode if state.claim else None
            if mode is RunMode.DELTA_INIT and exc.retryable and not RETRY_DELTA_INIT:
                self._log(
                    ctx,
                    None,
                    LogLevel.WARNING,
                    "Una inicialización de delta no se reintenta sola: revise ODQMON antes de "
                    "lanzarla de nuevo (pendiente de confirmar en el spike, H10).",
                )
                raise NotRetryableError(exc.message, kind=exc.kind, code=exc.code) from exc
            raise
        except InvalidSpoolError as exc:
            self._log(ctx, None, LogLevel.ERROR, str(exc))
            raise DataflowError(
                str(exc), kind=ErrorKind.TRANSIENT, code="SX-RUN-SPOOL-INVALID"
            ) from exc
        except DomainError as exc:  # pipeline, origen o destino ya no válidos
            self._log(ctx, None, LogLevel.ERROR, f"{exc.error_code}: {exc.detail}")
            raise DataflowError(
                exc.detail, kind=ErrorKind.CONFIGURATION, code=exc.error_code
            ) from exc
        except Exception as exc:
            self._log(
                ctx,
                None,
                LogLevel.ERROR,
                f"Error interno del motor ({type(exc).__name__}). Revise el log técnico.",
            )
            raise

    # ================================================================ pasos
    def _run(self, state: _Attempt, stack: ExitStack) -> None:
        ctx = state.ctx
        with self._step(ctx, StepName.PREPARE) as metrics:
            with self.db.session() as session:
                resolved = resolve(session, ctx.pipeline_id, self.tk)
            state.resolved = resolved
            claim = self.store.begin(ctx, filters_hash=resolved.filters_hash)
            state.claim = claim
            state.batch_id, state.window_start, state.window_end = self._identity(state)
            writer = stack.enter_context(resolved.open_writer())
            state.writer = writer
            target = writer.prepare(resolved.schema, resolved.options)
            applied = writer.applied_batch(target, state.batch_id)
            metrics.update(batch_id=state.batch_id, target=target.target, mode=claim.mode.value)
            ctx.checkpoint()
        if applied is not None:
            self._already_applied(state, applied)
        else:
            with self._step(ctx, StepName.EXTRACT) as metrics:
                spool = self._reuse_spool(state) or self._extract(state, metrics)
                state.spool = spool
                state.totals = Totals(rows_extracted=spool.manifest.rows)
                metrics.update(rows=spool.manifest.rows, files=len(spool.manifest.files))
            with self._step(ctx, StepName.LOAD) as metrics:
                ctx.checkpoint()
                stage = writer.stage(target, spool)
                state.stage = stage
                ctx.checkpoint()  # tras el apply ya no se para: los datos están en el destino
                result = writer.apply(
                    stage,
                    BatchInfo(
                        pipeline_id=claim.pipeline_id,
                        run_id=claim.run_id,
                        batch_id=state.batch_id,
                        mode=claim.mode,
                        schema_hash=resolved.schema.schema_hash(),
                        next_token=state.next_token,
                        window_end=state.window_end,
                    ),
                )
                state.totals = Totals(
                    rows_extracted=spool.manifest.rows,
                    rows_inserted=result.rows_inserted,
                    rows_updated=result.rows_updated,
                    rows_deleted=result.rows_deleted,
                )
                metrics.update(
                    rows_inserted=result.rows_inserted,
                    rows_updated=result.rows_updated,
                    rows_deleted=result.rows_deleted,
                )
                self._log(
                    ctx,
                    StepName.LOAD,
                    LogLevel.INFO,
                    f"Cargado en {resolved.destination_label}: {result.rows_inserted} filas "
                    f"nuevas, {result.rows_updated} actualizadas y {result.rows_deleted} borradas.",
                )
        with self._step(ctx, StepName.COMMIT_DELTA):
            self.store.commit(
                claim,
                next_token=state.next_token,
                window_end=state.window_end,
                totals=state.totals,
            )
            self._log(ctx, StepName.COMMIT_DELTA, LogLevel.INFO, _committed(claim.mode))
        try:  # con el escritor aún abierto; el run ya está confirmado y nada de aquí lo cambia
            self._cleanup(state)
        except Exception:
            log.warning("run_cleanup_failed", run_id=str(ctx.run_id))

    def _identity(self, state: _Attempt) -> tuple[str, datetime | None, datetime | None]:
        claim, resolved = state.claim, state.resolved
        if claim is None or resolved is None:  # pragma: no cover - fijados en prepare
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)
        if claim.mode is not RunMode.WATERMARK:
            if claim.batch_id is None:  # pragma: no cover - lo fija begin
                msg = "Run sin batch_id"
                raise RuntimeError(msg)
            return claim.batch_id, None, None
        if claim.batch_id and claim.window_start and claim.window_end:
            return claim.batch_id, claim.window_start, claim.window_end
        start, end = self._window(claim, resolved)
        identity = self.store.fix_window(claim, start, end)
        self._log(
            state.ctx,
            StepName.PREPARE,
            LogLevel.INFO,
            f"Ventana de la marca de agua: desde {_naive(start)} hasta {_naive(end)} (hora SAP).",
        )
        return identity, start, end

    def _window(self, claim: Claim, resolved: ResolvedPipeline) -> tuple[datetime, datetime]:
        spec = resolved.watermark
        if spec is None:
            msg = "El origen no tiene marca de agua configurada."
            raise DataflowError(msg, kind=ErrorKind.CONFIGURATION, code="SX-SRC-WATERMARK-REQUIRED")
        local_now = local_moment(resolved.connector.server_now(), spec.timezone)
        previous = (claim.watermark_value or {}).get("end")
        overlap = timedelta(seconds=resolved.watermark_overlap_seconds)
        if spec.kind is WatermarkKind.DATE:
            end = datetime.combine(local_now.date() + timedelta(days=1), datetime.min.time())
            start = (
                datetime.combine(
                    (_naive(datetime.fromisoformat(previous)) - timedelta(days=1)).date(),
                    datetime.min.time(),
                )
                if previous
                else WATERMARK_ORIGIN
            )
        else:
            lag = timedelta(seconds=self.tk.settings.watermark_safety_lag_seconds)
            end = (local_now - lag).replace(microsecond=0)
            start = (
                _naive(datetime.fromisoformat(previous)) - overlap if previous else WATERMARK_ORIGIN
            )
        # Las horas SAP no llevan zona: se guardan marcadas como UTC para la columna timestamptz.
        return start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)

    def _already_applied(self, state: _Attempt, applied: AppliedBatch) -> None:
        claim = state.claim
        if claim is None:  # pragma: no cover - fijado en prepare
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)
        state.next_token = applied.next_token
        if claim.mode in DELTA_RUN_MODES:
            if not applied.next_token:
                msg = "La tabla de control del destino tiene el lote sin token: no se confirma."
                raise DataflowError(
                    msg, kind=ErrorKind.CONFIGURATION, code="SX-RUN-APPLIED-NO-TOKEN"
                )
            self.store.set_pending_token(claim, applied.next_token)
        if claim.mode is RunMode.WATERMARK and applied.window_end is not None:
            state.window_end = state.window_end or applied.window_end
        for step in (StepName.EXTRACT, StepName.LOAD):
            step_id = self.store.start_step(claim.run_id, claim.attempt, step)
            self.store.finish_step(step_id, StepStatus.SKIPPED, {"reason": "already_applied"})
        self._log(
            state.ctx,
            StepName.PREPARE,
            LogLevel.WARNING,
            "El destino ya tiene este lote aplicado (caída antes de confirmar): se confirma sin "
            "volver a extraer ni cargar.",
        )

    def _reuse_spool(self, state: _Attempt) -> DirectorySpool | None:
        claim, resolved = state.claim, state.resolved
        if (
            claim is None
            or resolved is None
            or not state.batch_id
            or claim.previous_spool_worker != claim.worker_id
        ):
            return None
        try:
            spool = DirectorySpool.open(
                self.spool_root,
                state.batch_id,
                schema_hash=resolved.schema.schema_hash(),
                verify_hashes=True,
            )
        except InvalidSpoolError:
            return None
        state.next_token = spool.manifest.next_token
        if claim.mode in DELTA_RUN_MODES and state.next_token:
            self.store.set_pending_token(claim, state.next_token)
        self.store.mark_attempt(claim, spool_worker_id=claim.worker_id)
        self._log(
            state.ctx,
            StepName.EXTRACT,
            LogLevel.INFO,
            f"Se reutiliza el spool del intento anterior ({spool.manifest.rows} filas): no se "
            "vuelve a pedir a SAP.",
        )
        return spool

    def _extract(self, state: _Attempt, metrics: dict[str, Any]) -> DirectorySpool:
        ctx, claim, resolved = state.ctx, state.claim, state.resolved
        if claim is None or resolved is None or state.batch_id is None:  # pragma: no cover
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)

        def progress(p: _Pages) -> None:
            self._log(
                ctx,
                StepName.EXTRACT,
                LogLevel.INFO,
                f"Página {p.pages}: {p.rows} filas leídas de SAP.",
                {"pages": p.pages, "rows": p.rows, "bytes": p.bytes},
            )

        pages = _Pages(on_progress=progress)
        read_ctx = ReadContext(
            run_id=claim.run_id,
            batch_id=state.batch_id,
            page_size=resolved.page_size,
            page_timeout_seconds=self.tk.settings.page_timeout_seconds,
            is_cancelled=ctx.is_cancelled,
            on_page=pages.add,
        )
        self.store.mark_attempt(claim, sap_requested=True)
        result = self._read(state, read_ctx)
        with SpoolWriter(
            self.spool_root,
            batch_id=state.batch_id,
            pipeline_id=claim.pipeline_id,
            run_id=claim.run_id,
            mode=claim.mode,
            schema=resolved.schema,
            worker_id=str(claim.worker_id),
            start_token=claim.start_token,
            window_start=state.window_start,
            window_end=state.window_end,
        ) as writer:
            for batch in result.batches():
                try:
                    writer.write(batch)
                except ValueError as exc:
                    raise DataflowError(
                        str(exc), kind=ErrorKind.DATA, code="SX-SRC-SCHEMA-MISMATCH"
                    ) from exc
            ctx.checkpoint()  # el conector se detiene sin token si se pidió parar
            state.next_token = result.next_token()
            writer.finish(next_token=state.next_token, created_at=utcnow())
        metrics.update(pages.as_metrics())
        self.store.mark_attempt(claim, spool_worker_id=claim.worker_id)
        if claim.mode in DELTA_RUN_MODES and state.next_token:
            self.store.set_pending_token(claim, state.next_token)
        self._log(
            ctx,
            StepName.EXTRACT,
            LogLevel.INFO,
            f"Extracción terminada: {pages.rows} filas en {pages.pages} páginas.",
            pages.as_metrics(),
        )
        return DirectorySpool.open(
            self.spool_root, state.batch_id, schema_hash=resolved.schema.schema_hash()
        )

    def _read(self, state: _Attempt, read_ctx: ReadContext) -> ReadResult:
        claim, resolved = state.claim, state.resolved
        if claim is None or resolved is None:  # pragma: no cover
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)
        connector = resolved.connector
        filters = {"$filter": resolved.rendered_filter} if resolved.rendered_filter else {}
        match claim.mode:
            case RunMode.FULL:
                return connector.read_full(filters, read_ctx)
            case RunMode.DELTA_INIT:
                return connector.read_delta_init(filters, read_ctx)
            case RunMode.DELTA:
                if not claim.start_token:
                    msg = "El run delta no tiene token de partida."
                    raise DataflowError(msg, kind=ErrorKind.DELTA, code="SX-SAP-DELTA-INVALID")
                return connector.read_delta(claim.start_token, read_ctx)
            case RunMode.WATERMARK:
                spec = resolved.watermark
                if spec is None or state.window_start is None or state.window_end is None:
                    msg = "El run de marca de agua no tiene ventana."
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-SRC-WATERMARK-REQUIRED"
                    )
                window = Window(
                    field=spec.field, start=_naive(state.window_start), end=_naive(state.window_end)
                )
                return connector.read_window(window, read_ctx)

    def _cleanup(self, state: _Attempt) -> None:
        """Borra el staging y el spool. Un fallo aquí no falla el run (ya está confirmado)."""
        claim = state.claim
        if claim is None:  # pragma: no cover
            return
        step_id = self.store.start_step(claim.run_id, claim.attempt, StepName.CLEANUP)
        status = StepStatus.SUCCEEDED
        try:
            if state.writer is not None and state.stage is not None:
                try:
                    state.writer.cleanup(state.stage)
                except Exception:
                    status = StepStatus.FAILED
                    log.warning("destination_cleanup_failed", run_id=str(claim.run_id))
            if state.spool is not None and not self.tk.settings.spool_keep_on_success:
                state.spool.remove()
        finally:
            self.store.finish_step(step_id, status)

    # ================================================================ utilidades
    @contextmanager
    def _step(self, ctx: RunContext, step: StepName) -> Iterator[dict[str, Any]]:
        step_id = self.store.start_step(ctx.run_id, ctx.attempt, step)
        metrics: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            yield metrics
        except BaseException:
            metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
            self.store.finish_step(step_id, StepStatus.FAILED, metrics)
            raise
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        self.store.finish_step(step_id, StepStatus.SUCCEEDED, metrics)

    def _log(
        self,
        ctx: RunContext,
        step: StepName | None,
        level: LogLevel,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.store.log(
                ctx.run_id,
                attempt=ctx.attempt,
                step=step,
                level=level.value,
                message=message,
                data=data,
            )
        except Exception:  # el log de ejecución nunca tumba el run
            log.warning("run_log_write_failed", run_id=str(ctx.run_id))


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def _committed(mode: RunMode) -> str:
    return {
        RunMode.DELTA_INIT: "Delta inicializado: el token queda confirmado.",
        RunMode.DELTA: "Token de delta confirmado.",
        RunMode.WATERMARK: "Marca de agua confirmada.",
        RunMode.FULL: "Carga completa confirmada.",
    }[mode]
