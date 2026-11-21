"""Batería que todo ``DestinationWriter`` debe pasar (ADR 0006 §9).

Cada destino crea una subclase ``Test…(DestinationContract)`` con el fixture ``harness``.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest

from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.errors import DataflowError
from sapextract.dataflow.domain.ports import (
    DeleteHandling,
    DestinationWriter,
    LoadOptions,
    is_test_target,
    target_name_for_test,
)
from tests.support.dataflow import GL_SCHEMA, gl_row, make_spool


class Harness(Protocol):
    writer: DestinationWriter
    supported_modes: frozenset[LoadMode]

    def rows(self, target: str) -> list[dict[str, Any]]:
        """Filas actuales del destino, con los nombres de columna del destino."""
        ...

    def failure_inside_apply(self) -> AbstractContextManager[None]:
        """Hace fallar el próximo ``apply`` después de escribir datos y antes de confirmar."""
        ...


@dataclass
class Loader:
    harness: Harness
    spool_root: Path
    pipeline_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def load(
        self,
        rows: list[dict[str, Any]],
        options: LoadOptions,
        *,
        start_token: str | None = None,
    ) -> tuple[Any, Any]:
        sample = make_spool(
            self.spool_root, rows, pipeline_id=self.pipeline_id, start_token=start_token
        )
        writer = self.harness.writer
        target = writer.prepare(GL_SCHEMA, options)
        stage = writer.stage(target, sample.spool)
        try:
            result = writer.apply(stage, sample.info)
        finally:
            writer.cleanup(stage)
        return target, (sample, result)


def _options(mode: LoadMode, **kw: Any) -> LoadOptions:
    return LoadOptions(load_mode=mode, target_name=f"T_{uuid.uuid4().hex[:8]}", **kw)


class DestinationContract:
    @pytest.fixture
    def harness(self) -> Harness:  # pragma: no cover - lo define cada destino
        raise NotImplementedError

    @pytest.fixture
    def loader(self, harness: Harness, tmp_path: Path) -> Loader:
        return Loader(harness, tmp_path / "spool")

    # -------------------------------------------------------------- idempotencia
    def test_repeating_a_batch_does_not_duplicate(self, loader: Loader) -> None:
        options = _options(LoadMode.APPEND)
        rows = [gl_row(i) for i in range(1, 51)]
        target, (sample, first) = loader.load(rows, options, start_token="D000000000100")
        assert first.rows_inserted == 50
        writer = loader.harness.writer
        assert writer.applied_batch(target, sample.info.batch_id) is not None
        # El mismo lote (mismo token de partida) se repite: el destino no cambia.
        loader.load(rows, options, start_token="D000000000100")
        assert len(loader.harness.rows(target.target)) == 50
        # Otro lote se añade.
        loader.load([gl_row(i) for i in range(51, 61)], options, start_token="D000000000200")
        assert len(loader.harness.rows(target.target)) == 60

    def test_applied_batch_reports_the_pending_token(self, loader: Loader) -> None:
        options = _options(LoadMode.APPEND)
        target, (sample, _) = loader.load([gl_row(1)], options)
        applied = loader.harness.writer.applied_batch(target, sample.info.batch_id)
        assert applied is not None
        assert applied.next_token == sample.info.next_token
        assert applied.run_id == sample.info.run_id
        assert loader.harness.writer.applied_batch(target, "d-" + "0" * 24) is None

    def test_failure_inside_apply_leaves_no_trace(self, loader: Loader) -> None:
        options = _options(LoadMode.APPEND)
        target, _ = loader.load([gl_row(1)], options, start_token="D000000000001")
        with loader.harness.failure_inside_apply(), pytest.raises(Exception):  # noqa: B017, PT011
            loader.load([gl_row(2), gl_row(3)], options, start_token="D000000000002")
        assert len(loader.harness.rows(target.target)) == 1

    # -------------------------------------------------------------- modos
    def test_replace_swaps_the_whole_content(self, loader: Loader) -> None:
        if LoadMode.REPLACE not in loader.harness.supported_modes:
            pytest.skip("modo no soportado")
        options = _options(LoadMode.REPLACE)
        target, _ = loader.load([gl_row(i) for i in range(1, 11)], options)
        loader.load([gl_row(i, amount="99.00") for i in range(5, 8)], options)
        rows = loader.harness.rows(target.target)
        assert len(rows) == 3
        assert {str(r.get("DMBTR")) for r in rows} == {"99.00"}

    def test_upsert_keeps_latest_per_key_and_soft_deletes(self, loader: Loader) -> None:
        if LoadMode.UPSERT not in loader.harness.supported_modes:
            pytest.skip("modo no soportado")
        options = _options(LoadMode.UPSERT, delete_handling=DeleteHandling.SOFT)
        target, _ = loader.load([gl_row(i) for i in range(1, 6)], options)
        changes = [
            gl_row(1, amount="1.00", mode="U"),
            gl_row(1, amount="2.00", mode="U"),  # gana el último por _sx_record_seq
            gl_row(2, mode="D"),
            gl_row(9, amount="9.00", mode="C"),
        ]
        loader.load(changes, options)
        rows = {r["BELNR"]: r for r in loader.harness.rows(target.target)}
        assert len(rows) == 6
        assert str(rows[f"{1:010d}"]["DMBTR"]) == "2.00"
        assert rows[f"{2:010d}"]["_SX_DELETED"] in (True, "true", 1)
        assert str(rows[f"{9:010d}"]["DMBTR"]) == "9.00"

    def test_unsupported_mode_is_rejected(self, loader: Loader) -> None:
        missing = set(LoadMode) - set(loader.harness.supported_modes)
        if not missing:
            pytest.skip("admite todos los modos")
        with pytest.raises(Exception, match="modo"):
            loader.harness.writer.prepare(GL_SCHEMA, _options(missing.pop()))

    # -------------------------------------------------------------- destino de prueba
    def test_test_target_is_dropped_and_real_targets_are_refused(self, loader: Loader) -> None:
        writer = loader.harness.writer
        options = LoadOptions(
            load_mode=LoadMode.APPEND,
            target_name=target_name_for_test("T_REAL", uuid.uuid4().hex[:8]),
        )
        assert is_test_target(options.target_name)
        target, (sample, _) = loader.load([gl_row(1), gl_row(2)], options)
        assert len(loader.harness.rows(target.target)) == 2

        writer.drop_test_target(target)
        writer.drop_test_target(target)  # idempotente
        again = writer.prepare(target.schema, options)
        assert writer.applied_batch(again, sample.info.batch_id) is None
        assert loader.harness.rows(again.target) == []
        writer.drop_test_target(again)

        # Un destino real no se borra aunque se pida.
        real, _ = loader.load([gl_row(1)], _options(LoadMode.APPEND))
        with pytest.raises(DataflowError) as refused:
            writer.drop_test_target(real)
        assert refused.value.code == "SX-DST-NOT-TEST-TARGET"
        assert len(loader.harness.rows(real.target)) == 1
