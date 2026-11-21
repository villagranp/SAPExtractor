"""Conversión de filas OData (V2 y V4) a lotes Arrow con el esquema del spool (plan 06 §6).

* ``Edm.DateTime`` (``/Date(ms)/``) es hora local del sistema SAP sin zona: se guarda tal cual,
  sin conversión (H15). Con ``sap:display-format="Date"`` solo la fecha.
* ``Edm.DateTimeOffset`` es un instante: UTC.
* ``Edm.Decimal`` llega como texto: decimal con la precisión del ``$metadata``. Si trae más
  decimales que la escala, es un error de datos (no se redondea en silencio).
* ``Edm.Time`` (``PT12H30M00S``) → hora del día.
* Los textos se conservan tal cual (ceros a la izquierda de los NUMC incluidos).
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from sapextract.dataflow.domain.errors import DataflowError, ErrorKind
from sapextract.dataflow.domain.schema import CHANGE_MODE, EXTRACTED_AT, Column, EntitySchema

ODQ_CHANGEMODE = "ODQ_CHANGEMODE"
CHANGE_MODES = frozenset({"C", "U", "D"})
_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - las horas ABAP no llevan zona (H15)
_V2_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")
_DURATION = re.compile(r"^-?P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")


class ConversionError(DataflowError):
    def __init__(self, column: Column, value: Any, row: int) -> None:
        shown = str(value)
        if len(shown) > 60:
            shown = shown[:57] + "…"
        super().__init__(
            f"No se pudo convertir el valor «{shown}» de la columna {column.name} "
            f"({column.edm_type}) en la fila {row} de la página.",
            kind=ErrorKind.DATA,
            code="SX-SRC-CONVERSION",
        )
        self.column = column.name


def _v2_moment(text: str) -> datetime | None:
    match = _V2_DATE.match(text)
    if not match:
        return None
    moment = _EPOCH + timedelta(milliseconds=int(match[1]))
    offset = match[2]
    if offset:  # /Date(ms+0000)/ es UTC; un desfase distinto se descuenta
        sign = 1 if offset[0] == "+" else -1
        minutes = int(offset[1:3]) * 60 + int(offset[3:5])
        moment -= sign * timedelta(minutes=minutes)
    return moment


def _naive_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value)
    moment = _v2_moment(text)
    if moment is not None:
        return moment
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():  # DATS interno (AAAAMMDD)
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return _naive_datetime(value).date()


def _to_instant(value: Any) -> datetime:
    text = str(value)
    moment = _v2_moment(text)
    if moment is not None:
        return moment.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_time(value: Any) -> time:
    text = str(value)
    match = _DURATION.match(text)
    if match and text.startswith("P"):
        days, hours, minutes, seconds = match.groups()
        total = timedelta(
            days=int(days or 0),
            hours=int(hours or 0),
            minutes=int(minutes or 0),
            seconds=float(seconds or 0),
        )
        total %= timedelta(days=1)
        return (datetime.min + total).time()  # noqa: DTZ901
    return time.fromisoformat(text)


def _decimal_converter(column: Column) -> Callable[[Any], Decimal]:
    arrow = column.arrow_type
    scale = arrow.scale if isinstance(arrow, pa.Decimal128Type) else 0

    def convert(value: Any) -> Decimal:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise InvalidOperation
        exponent = number.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > scale:
            trimmed = number.quantize(Decimal(1).scaleb(-scale))
            if trimmed != number:  # perdería decimales significativos
                raise InvalidOperation
            return trimmed
        return number

    return convert


def _int(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError
    return int(str(value).strip()) if isinstance(value, str) else int(value)


def _float(value: Any) -> float:
    return float(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "x", "1"):
        return True
    if text in ("false", "", "0"):
        return False
    raise ValueError(text)


def _binary(value: Any) -> bytes:
    return base64.b64decode(str(value), validate=True)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def converter_for(column: Column) -> Callable[[Any], Any]:  # noqa: PLR0911
    arrow = column.arrow_type
    if pa.types.is_decimal(arrow):
        return _decimal_converter(column)
    if pa.types.is_integer(arrow):
        return _int
    if pa.types.is_floating(arrow):
        return _float
    if pa.types.is_boolean(arrow):
        return _bool
    if pa.types.is_date32(arrow):
        return _to_date
    if pa.types.is_timestamp(arrow):
        return _to_instant if arrow.tz else _naive_datetime
    if pa.types.is_time(arrow):
        return _to_time
    if pa.types.is_binary(arrow):
        return _binary
    return _text


def _is_empty_date(column: Column, value: Any) -> bool:
    """Fechas SAP vacías (``00000000``, ``/Date(-62135596800000)/``…) → NULL."""
    if column.edm_type not in ("Edm.DateTime", "Edm.Date", "Edm.DateTimeOffset"):
        return False
    text = str(value).strip()
    return text in ("", "00000000", "0000-00-00") or text.startswith("/Date(-6213")


class PageConverter:
    """Convierte las páginas de un lote en ``RecordBatch`` con el esquema de origen + técnicas.

    ``_sx_batch_id``, ``_sx_run_id`` y ``_sx_record_seq`` los completa el escritor del spool.
    """

    def __init__(self, schema: EntitySchema, *, change_mode: str | None) -> None:
        """``change_mode``: ``"init"``, ``"delta"`` o ``None`` (FULL y WATERMARK)."""
        self.schema = schema
        self._columns = schema.columns
        self._converters = [converter_for(c) for c in self._columns]
        self._change_mode = change_mode
        arrow_schema = schema.arrow_schema()
        self._source_fields = [arrow_schema.field(c.name) for c in self._columns]
        self._extracted_field = arrow_schema.field(EXTRACTED_AT)
        self._mode_field = arrow_schema.field(CHANGE_MODE)

    def convert(self, rows: Sequence[dict[str, Any]], extracted_at: datetime) -> pa.RecordBatch:
        values: list[list[Any]] = [[] for _ in self._columns]
        modes: list[str | None] = []
        for index, row in enumerate(rows, start=1):
            for position, column in enumerate(self._columns):
                raw = row.get(column.name)
                if raw is None or _is_empty_date(column, raw):
                    if not column.nullable and column.is_key:
                        raise ConversionError(column, "(vacío)", index)
                    values[position].append(None)
                    continue
                try:
                    values[position].append(self._converters[position](raw))
                except ValueError, TypeError, InvalidOperation, ArithmeticError:
                    raise ConversionError(column, raw, index) from None
            modes.append(self._mode_of(row))
        n = len(rows)
        arrays: dict[str, pa.Array[Any]] = {}
        for position, column in enumerate(self._columns):
            field = self._source_fields[position]
            try:
                arrays[column.name] = pa.array(values[position], type=field.type)
            except pa.ArrowInvalid, pa.ArrowTypeError, OverflowError:
                bad = next(
                    (v for v in values[position] if not _fits(v, field.type)), values[position][0]
                )
                raise ConversionError(column, bad, 1) from None
        stamp = extracted_at.astimezone(UTC)
        arrays[EXTRACTED_AT] = pa.array([stamp] * n, type=self._extracted_field.type)
        arrays[CHANGE_MODE] = pa.array(modes, type=self._mode_field.type)
        names = [c.name for c in self._columns] + [EXTRACTED_AT, CHANGE_MODE]
        return pa.RecordBatch.from_arrays([arrays[name] for name in names], names=names)

    def _mode_of(self, row: dict[str, Any]) -> str | None:
        if self._change_mode is None:
            return None
        raw = str(row.get(ODQ_CHANGEMODE) or "").strip().upper()
        if raw in CHANGE_MODES:
            return raw
        # En una init SAP deja el modo vacío: son altas (ADR 0006 §3). En un delta un modo
        # vacío no se interpreta (queda nulo y el destino lo trata como alta o cambio).
        return "C" if self._change_mode == "init" else None


def _fits(value: Any, type_: pa.DataType) -> bool:
    try:
        pa.array([value], type=type_)
    except pa.ArrowInvalid, pa.ArrowTypeError, OverflowError:
        return False
    return True
