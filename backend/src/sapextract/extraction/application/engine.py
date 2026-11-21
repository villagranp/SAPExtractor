"""Motor de extracción (plan 06 §2): ``prepare → extract → load → commit_delta → cleanup``.

Garantías que sostiene este módulo:

* El token confirmado solo avanza en ``commit_delta``, después de que el destino confirmó la
  carga y en la misma transacción que marca el run como exitoso (``RunStore.commit``).
* Si el lote ya está en la tabla de control del destino, ``prepare`` salta la extracción y la
  carga y confirma el token guardado allí (caída entre los dos commits).
* Un reintento en el mismo worker reutiliza el spool sin volver a llamar a SAP.
* Un worker que perdió el run (``lease_epoch``) no confirma nada: se detiene como ``fenced``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
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
from sapextract.extraction.application.store import Claim, Failure, FencedError, RunStore, Totals
from sapextract.extraction.domain.model import (
    AttemptOutcome,
    LogLevel,
    StepName,
    StepStatus,
    backoff,
    outcome_for,
)
from sapextract.extraction.infrastructure.odata_connector import local_moment
from sapextract.sources.domain.model import WatermarkKind

log = structlog.get_logger(__name__)

# Primera ventana: desde el principio. Hora local SAP, sin zona (H15).
WATERMARK_ORIGIN = datetime(1900, 1, 1)  # noqa: DTZ001
PROGRESS_EVERY_PAGES = 10


class StopReason(StrEnum):
    CANCELLED = "cancelled"
    FENCED = "fenced"
    SHUTDOWN = "shutdown"
    TIMEOUT = "timeout"


class Stopped(Exception):  # noqa: N818 - no es un error: es una parada pedida
    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class Guard:
    """Latido en segundo plano y motivos de parada (cancelación, *fencing*, apagado, timeout)."""

    def __init__(
        self,
        store: RunStore,
        claim: Claim,
        *,
        interval: float,
        shutdown: Callable[[], bool],
        deadline: float,
    ) -> None:
        self._store = store
        self._claim = claim
        self._interval = interval
        self._shutdown = shutdown
        self._deadline = deadline
        self._fenced = threading.Event()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="sx-heartbeat", daemon=True)

    def __enter__(self) -> Guard:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)

    def _beat(self) -> None:
        while not self._stop.wait(self._interval):
            self.beat()

    def beat(self) -> None:
        try:
            alive, cancel = self._store.heartbeat(self._claim)
        except Exception:  # un fallo puntual de la BD no para el run; el siguiente latido sí
            log.warning("run_heartbeat_failed", run_id=str(self._claim.run_id))
            return
        if not alive:
            self._fenced.set()
        if cancel:
            self._cancel.set()

    def reason(self) -> StopReason | None:
        if self._fenced.is_set():
            return StopReason.FENCED
        if self._cancel.is_set():
            return StopReason.CANCELLED
        if self._shutdown():
            return StopReason.SHUTDOWN
        if time.monotonic() > self._deadline:
            return StopReason.TIMEOUT
        return None

    def should_stop(self) -> bool:
        return self.reason() is not None

    def check(self) -> None:
        reason = self.reason()
        if reason is not None:
            raise Stopped(reason)


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

    claim: Claim
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
    def __init__(
        self,
        db: Database,
        toolkit: ExtractionToolkit,
        store: RunStore,
        *,
        worker_id: str,
        spool_root: Path,
        shutdown: Callable[[], bool] = lambda: False,
    ) -> None:
        self.db = db
        self.tk = toolkit
        self.store = store
        self.worker_id = worker_id
        self.spool_root = spool_root
        self.shutdown = shutdown

    # ================================================================ entrada
    def execute(self, claim: Claim) -> AttemptOutcome:  # noqa: PLR0911 - un retorno por clase de error
        self._log(claim, None, LogLevel.INFO, f"Intento {claim.attempt} en {self.worker_id}")
        deadline = time.monotonic() + claim.timeout_minutes * 60
        guard = Guard(
            self.store,
            claim,
            interval=self.tk.settings.worker_heartbeat_seconds,
            shutdown=self.shutdown,
            deadline=deadline,
        )
        state = _Attempt(claim=claim)
        try:
            with guard, ExitStack() as stack:
                self._run(state, guard, stack)
        except Stopped as stop:
            return self._stopped(claim, stop.reason)
        except FencedError:
            return self._fenced(claim)
        except DataflowError as exc:
            return self._failed(claim, exc.kind, exc.code, exc.message)
        except InvalidSpoolError as exc:
            return self._failed(claim, ErrorKind.TRANSIENT, "SX-RUN-SPOOL-INVALID", str(exc))
        except DomainError as exc:  # configuración: pipeline, origen o destino ya no válidos
            return self._failed(claim, ErrorKind.CONFIGURATION, exc.error_code, exc.detail)
        except Exception as exc:
            log.exception("run_attempt_crashed", run_id=str(claim.run_id))
            message = f"Error interno del motor ({type(exc).__name__}). Revise el log técnico."
            return self._failed(claim, ErrorKind.TRANSIENT, "SX-RUN-INTERNAL", message)
        return AttemptOutcome.SUCCEEDED

    # ================================================================ pasos
    def _run(self, state: _Attempt, guard: Guard, stack: ExitStack) -> None:
        claim = state.claim
        with self._step(claim, StepName.PREPARE) as metrics:
            with self.db.session() as session:
                resolved = resolve(session, claim.pipeline_id, self.tk)
            state.resolved = resolved
            state.batch_id, state.window_start, state.window_end = self._identity(claim, resolved)
            writer = stack.enter_context(resolved.open_writer())
            state.writer = writer
            target = writer.prepare(resolved.schema, resolved.options)
            applied = writer.applied_batch(target, state.batch_id)
            metrics.update(batch_id=state.batch_id, target=target.target)
            guard.check()
        if applied is not None:
            self._already_applied(state, applied)
        else:
            with self._step(claim, StepName.EXTRACT) as metrics:
                spool = self._reuse_spool(state) or self._extract(state, guard, metrics)
                state.spool = spool
                state.totals = Totals(rows_extracted=spool.manifest.rows)
                metrics.update(rows=spool.manifest.rows, files=len(spool.manifest.files))
            with self._step(claim, StepName.LOAD) as metrics:
                guard.check()
                stage = writer.stage(target, spool)
                state.stage = stage
                guard.check()  # tras el apply ya no se cancela: los datos están en el destino
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
                    claim,
                    StepName.LOAD,
                    LogLevel.INFO,
                    f"Cargado en {resolved.destination_label}: {result.rows_inserted} filas "
                    f"nuevas, {result.rows_updated} actualizadas y {result.rows_deleted} borradas.",
                )
        with self._step(claim, StepName.COMMIT_DELTA):
            self.store.commit(
                claim,
                next_token=state.next_token,
                window_end=state.window_end,
                totals=state.totals,
            )
            self._log(claim, StepName.COMMIT_DELTA, LogLevel.INFO, _committed(claim.mode))
        try:  # con el escritor aún abierto; el run ya está confirmado y nada de aquí lo cambia
            self._cleanup(state)
        except Exception:
            log.warning("run_cleanup_failed", run_id=str(claim.run_id))

    def _identity(
        self, claim: Claim, resolved: ResolvedPipeline
    ) -> tuple[str, datetime | None, datetime | None]:
        if claim.mode is not RunMode.WATERMARK:
            if claim.batch_id is None:  # pragma: no cover - lo fija el claim
                msg = "Run sin batch_id"
                raise RuntimeError(msg)
            return claim.batch_id, None, None
        if claim.batch_id and claim.window_start and claim.window_end:
            return claim.batch_id, claim.window_start, claim.window_end
        start, end = self._window(claim, resolved)
        identity = self.store.fix_window(claim, start, end)
        self._log(
            claim,
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
        state.next_token = applied.next_token
        if claim.mode in (RunMode.DELTA_INIT, RunMode.DELTA):
            if not applied.next_token:
                msg = "La tabla de control del destino tiene el lote sin token: no se confirma."
                raise DataflowError(
                    msg, kind=ErrorKind.CONFIGURATION, code="SX-RUN-APPLIED-NO-TOKEN"
                )
            self.store.set_pending_token(claim, applied.next_token)
        if claim.mode is RunMode.WATERMARK and applied.window_end is not None:
            state.window_end = state.window_end or applied.window_end
        for step in (StepName.EXTRACT, StepName.LOAD):
            step_id = self.store.start_step(claim, step)
            self.store.finish_step(step_id, StepStatus.SKIPPED, {"reason": "already_applied"})
        self._log(
            claim,
            StepName.PREPARE,
            LogLevel.WARNING,
            "El destino ya tiene este lote aplicado (caída antes de confirmar): se confirma sin "
            "volver a extraer ni cargar.",
        )

    def _reuse_spool(self, state: _Attempt) -> DirectorySpool | None:
        claim, resolved = state.claim, state.resolved
        if claim.previous_spool_worker != self.worker_id or resolved is None or not state.batch_id:
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
        if claim.mode in (RunMode.DELTA_INIT, RunMode.DELTA) and state.next_token:
            self.store.set_pending_token(claim, state.next_token)
        self.store.mark_attempt(claim, spool_worker_id=self.worker_id)
        self._log(
            claim,
            StepName.EXTRACT,
            LogLevel.INFO,
            f"Se reutiliza el spool del intento anterior ({spool.manifest.rows} filas): no se "
            "vuelve a pedir a SAP.",
        )
        return spool

    def _extract(self, state: _Attempt, guard: Guard, metrics: dict[str, Any]) -> DirectorySpool:
        claim, resolved = state.claim, state.resolved
        if resolved is None or state.batch_id is None:  # pragma: no cover - fijados en prepare
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)

        def progress(p: _Pages) -> None:
            self._log(
                claim,
                StepName.EXTRACT,
                LogLevel.INFO,
                f"Página {p.pages}: {p.rows} filas leídas de SAP.",
                {"pages": p.pages, "rows": p.rows, "bytes": p.bytes},
            )

        pages = _Pages(on_progress=progress)
        ctx = ReadContext(
            run_id=claim.run_id,
            batch_id=state.batch_id,
            page_size=resolved.page_size,
            page_timeout_seconds=self.tk.settings.page_timeout_seconds,
            is_cancelled=guard.should_stop,
            on_page=pages.add,
        )
        self.store.mark_attempt(claim, sap_requested=True)
        result = self._read(state, ctx)
        with SpoolWriter(
            self.spool_root,
            batch_id=state.batch_id,
            pipeline_id=claim.pipeline_id,
            run_id=claim.run_id,
            mode=claim.mode,
            schema=resolved.schema,
            worker_id=self.worker_id,
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
            guard.check()  # el conector se detiene sin token si se pidió parar
            state.next_token = result.next_token()
            writer.finish(next_token=state.next_token, created_at=utcnow())
        metrics.update(pages.as_metrics())
        self.store.mark_attempt(claim, spool_worker_id=self.worker_id)
        if claim.mode in (RunMode.DELTA_INIT, RunMode.DELTA) and state.next_token:
            self.store.set_pending_token(claim, state.next_token)
        self._log(
            claim,
            StepName.EXTRACT,
            LogLevel.INFO,
            f"Extracción terminada: {pages.rows} filas en {pages.pages} páginas.",
            pages.as_metrics(),
        )
        return DirectorySpool.open(
            self.spool_root, state.batch_id, schema_hash=resolved.schema.schema_hash()
        )

    def _read(self, state: _Attempt, ctx: ReadContext) -> ReadResult:
        claim, resolved = state.claim, state.resolved
        if resolved is None:  # pragma: no cover - fijado en prepare
            msg = "El intento no pasó por prepare"
            raise RuntimeError(msg)
        connector = resolved.connector
        filters = {"$filter": resolved.rendered_filter} if resolved.rendered_filter else {}
        match claim.mode:
            case RunMode.FULL:
                return connector.read_full(filters, ctx)
            case RunMode.DELTA_INIT:
                return connector.read_delta_init(filters, ctx)
            case RunMode.DELTA:
                if not claim.start_token:
                    msg = "El run delta no tiene token de partida."
                    raise DataflowError(msg, kind=ErrorKind.DELTA, code="SX-SAP-DELTA-INVALID")
                return connector.read_delta(claim.start_token, ctx)
            case RunMode.WATERMARK:
                spec = resolved.watermark
                if spec is None or state.window_start is None or state.window_end is None:
                    msg = "El run de marca de agua no tiene ventana."
                    raise DataflowError(
                        msg, kind=ErrorKind.CONFIGURATION, code="SX-SRC-WATERMARK-REQUIRED"
                    )
                window = Window(
                    field=spec.field,
                    start=_naive(state.window_start),
                    end=_naive(state.window_end),
                )
                return connector.read_window(window, ctx)

    def _cleanup(self, state: _Attempt) -> None:
        """Borra el staging y el spool. Un fallo aquí no falla el run (ya está confirmado)."""
        claim = state.claim
        step_id = self.store.start_step(claim, StepName.CLEANUP)
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

    # ================================================================ fallos
    def _failed(self, claim: Claim, kind: ErrorKind, code: str, message: str) -> AttemptOutcome:
        attempts_left = claim.max_attempts - (claim.attempts_used + 1)
        outcome = outcome_for(kind, mode=claim.mode, attempts_left=attempts_left)
        retry_in = (
            backoff(claim.attempt, tuple(self.tk.settings.run_retry_backoff_seconds))
            if outcome is AttemptOutcome.FAILED_TRANSIENT
            else None
        )
        self._log(claim, None, LogLevel.ERROR, f"{code}: {message}", {"kind": kind.value})
        if outcome is AttemptOutcome.FAILED_TRANSIENT and retry_in is not None:
            self._log(
                claim,
                None,
                LogLevel.INFO,
                f"Se reintentará en {int(retry_in.total_seconds())} s.",
            )
        elif kind is ErrorKind.TRANSIENT and claim.mode is RunMode.DELTA_INIT:
            self._log(
                claim,
                None,
                LogLevel.WARNING,
                "Una inicialización de delta no se reintenta sola: revise ODQMON antes de lanzarla "
                "de nuevo (pendiente de confirmar en el spike, H10).",
            )
        failure = Failure(outcome=outcome, code=code, kind=kind, message=message, retry_in=retry_in)
        self.store.fail(claim, failure)
        return outcome

    def _stopped(self, claim: Claim, reason: StopReason) -> AttemptOutcome:
        if reason is StopReason.FENCED:
            return self._fenced(claim)
        if reason is StopReason.TIMEOUT:
            message = f"Se superó el tiempo máximo del run ({claim.timeout_minutes} min)."
            return self._failed(claim, ErrorKind.TRANSIENT, "SX-RUN-TIMEOUT", message)
        if reason is StopReason.CANCELLED:
            self._log(claim, None, LogLevel.WARNING, "Run cancelado: el token no avanza.")
            self.store.fail(
                claim,
                Failure(
                    outcome=AttemptOutcome.CANCELLED, code="SX-RUN-CANCELLED", message="Cancelado"
                ),
            )
            return AttemptOutcome.CANCELLED
        self._log(claim, None, LogLevel.WARNING, "Apagado del worker: el run vuelve a la cola.")
        self.store.fail(
            claim, Failure(outcome=AttemptOutcome.INTERRUPTED, code="SX-RUN-INTERRUPTED")
        )
        return AttemptOutcome.INTERRUPTED

    def _fenced(self, claim: Claim) -> AttemptOutcome:
        self._log(
            claim,
            None,
            LogLevel.WARNING,
            "Este intento perdió el run (otro worker lo tomó): se detiene sin confirmar nada.",
        )
        self.store.fail(
            claim,
            Failure(outcome=AttemptOutcome.FENCED, code="SX-RUN-FENCED", kind=ErrorKind.TRANSIENT),
        )
        return AttemptOutcome.FENCED

    # ================================================================ utilidades
    @contextmanager
    def _step(self, claim: Claim, step: StepName) -> Iterator[dict[str, Any]]:
        step_id = self.store.start_step(claim, step)
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
        claim: Claim,
        step: StepName | None,
        level: LogLevel,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.store.log(
                claim.run_id,
                attempt=claim.attempt,
                step=step,
                level=level.value,
                message=message,
                data=data,
            )
        except Exception:  # el log de ejecución nunca tumba el run
            log.warning("run_log_write_failed", run_id=str(claim.run_id))


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def _committed(mode: RunMode) -> str:
    return {
        RunMode.DELTA_INIT: "Delta inicializado: el token queda confirmado.",
        RunMode.DELTA: "Token de delta confirmado.",
        RunMode.WATERMARK: "Marca de agua confirmada.",
        RunMode.FULL: "Carga completa confirmada.",
    }[mode]
