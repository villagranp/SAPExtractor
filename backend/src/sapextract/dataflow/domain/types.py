"""Mapeo de tipos EDM (OData) → Arrow: fuente única de la tabla del plan 07 §6.

Las fechas y horas ABAP clásicas (``Edm.DateTime``) son hora local del sistema SAP sin zona:
se guardan tal cual (``timestamp[us]`` sin zona). Solo ``Edm.DateTimeOffset`` es un instante.
"""

from __future__ import annotations

import pyarrow as pa

DEFAULT_DECIMAL_PRECISION = 38
DEFAULT_DECIMAL_SCALE = 10
MAX_DECIMAL_PRECISION = 38


_SIMPLE: dict[str, pa.DataType] = {
    "Edm.String": pa.string(),
    "Edm.Guid": pa.string(),
    "Edm.Boolean": pa.bool_(),
    "Edm.Byte": pa.int16(),
    "Edm.SByte": pa.int16(),
    "Edm.Int16": pa.int16(),
    "Edm.Int32": pa.int32(),
    "Edm.Int64": pa.int64(),
    "Edm.Single": pa.float64(),
    "Edm.Double": pa.float64(),
    "Edm.Date": pa.date32(),  # OData V4
    "Edm.DateTimeOffset": pa.timestamp("us", tz="UTC"),
    "Edm.Time": pa.time64("us"),
    "Edm.TimeOfDay": pa.time64("us"),
    "Edm.Binary": pa.binary(),
}


def arrow_type(
    edm_type: str,
    *,
    precision: int | None = None,
    scale: int | None = None,
    display_format: str | None = None,
) -> pa.DataType:
    if edm_type == "Edm.Decimal":
        p = min(precision or DEFAULT_DECIMAL_PRECISION, MAX_DECIMAL_PRECISION)
        s = DEFAULT_DECIMAL_SCALE if scale is None else min(scale, p)
        return pa.decimal128(p, s)
    if edm_type == "Edm.DateTime":
        # sap:display-format="Date": solo fecha. Si no, fecha y hora local SAP sin zona.
        return pa.date32() if (display_format or "").lower() == "date" else pa.timestamp("us")
    return _SIMPLE.get(edm_type, pa.string())  # tipos no previstos: texto, sin perder el dato
