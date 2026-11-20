"""Valores de la vista previa en forma legible (plan 05 §2.6).

Solo para mostrar: la conversión real a Arrow la hace el conector (It5). Las fechas
``Edm.DateTime`` se muestran tal cual llegan, sin zona (hora local del sistema SAP, H15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.sources.domain.schema import DATE_FORMAT

PREVIEW_ROWS = 100
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_V2_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")
_V2_TIME = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)(?:\.\d+)?S)?$")


@dataclass(frozen=True, kw_only=True)
class PreviewData:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool  # SAP tenía más filas que las mostradas
    count: int | None  # recuento total, si se pudo obtener
    elapsed_ms: int


def display_rows(schema: EntitySchema, raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filas con solo las columnas del esquema y fechas/horas legibles."""
    return [
        {
            c.name: display_value(c.edm_type, c.display_format, row.get(c.name))
            for c in schema.columns
        }
        for row in raw_rows
    ]


def display_value(edm_type: str, display_format: str | None, value: Any) -> Any:  # noqa: PLR0911
    if not isinstance(value, str):
        return value
    if edm_type in ("Edm.DateTime", "Edm.DateTimeOffset"):
        match = _V2_DATE.match(value)
        if not match:
            return value  # V4 ya llega en ISO 8601
        moment = _EPOCH + timedelta(milliseconds=int(match[1]))
        if edm_type == "Edm.DateTime":
            local = moment.replace(tzinfo=None)
            if (display_format or "").lower() == DATE_FORMAT:
                return local.date().isoformat()
            return local.isoformat()
        return moment.isoformat().replace("+00:00", "Z")
    if edm_type == "Edm.Time":
        match = _V2_TIME.match(value)
        if match:
            hours, minutes, seconds = (int(g or 0) for g in match.groups())
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return value
