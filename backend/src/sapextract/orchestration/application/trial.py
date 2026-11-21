"""Vigencia de la ejecución de prueba de un pipeline (plan 08 §5).

La prueba la ejecuta el motor (``extraction/application/trial.py``) y deja en el pipeline su
resultado y la huella de lo probado; aquí se decide si sigue vigente.
"""

from __future__ import annotations

from sapextract.orchestration.domain.model import trial_fingerprint
from sapextract.orchestration.infrastructure.models import Pipeline
from sapextract.sources.infrastructure.models import Source


def config_fingerprint(pipeline: Pipeline, source: Source) -> str:
    """Huella de lo que la prueba comprueba, con los filtros efectivos del pipeline."""
    filters = source.default_filters if pipeline.filters is None else pipeline.filters
    return trial_fingerprint(
        source_id=str(pipeline.source_id),
        destination_id=str(pipeline.destination_id),
        extraction_mode=pipeline.extraction_mode,
        load_options=pipeline.load_options,
        column_mapping=pipeline.column_mapping or {},
        filters=filters,
    )


def trial_outdated(pipeline: Pipeline, source: Source) -> bool:
    """Hay una prueba guardada, pero de otra configuración."""
    return (
        pipeline.last_test_at is not None
        and pipeline.last_test_fingerprint != config_fingerprint(pipeline, source)
    )
