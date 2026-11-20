"""Filtros por defecto de un origen: condiciones estructuradas → ``$filter`` OData (plan 05 §2.5).

El usuario nunca escribe ``$filter`` a mano: elige campo, operador y valor, y aquí se valida el
valor según el tipo EDM y se genera el literal correcto para V2 o V4. Las condiciones se unen
con ``and``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.sources.domain.schema import find_column, is_date_only

MAX_CONDITIONS = 20
MAX_VALUE_LENGTH = 200
MAX_DIGITS = 38
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_INTEGER = frozenset({"Edm.Byte", "Edm.SByte", "Edm.Int16", "Edm.Int32", "Edm.Int64"})
_FLOAT = frozenset({"Edm.Single", "Edm.Double"})
_NOT_FILTERABLE = frozenset({"Edm.Binary", "Edm.Time", "Edm.TimeOfDay", "Edm.Duration"})


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"


@dataclass(frozen=True, kw_only=True)
class FilterCondition:
    field: str
    op: FilterOperator
    value: str | int | float | bool

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op.value, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FilterCondition:
        return cls(field=str(data["field"]), op=FilterOperator(data["op"]), value=data["value"])


def _issue(index: int, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(
        message, code=code, errors=[FieldIssue(f"default_filters[{index}]", message, code)]
    )


def check_filters(
    conditions: list[FilterCondition],
    schema: EntitySchema,
    *,
    filterable: frozenset[str],
    odata_version: str,
) -> list[FilterCondition]:
    """Valida cada condición: campo filtrable y valor del tipo correcto."""
    if len(conditions) > MAX_CONDITIONS:
        msg = f"Como máximo {MAX_CONDITIONS} condiciones."
        raise _issue(MAX_CONDITIONS, msg, "SX-SRC-FILTER-TOO-MANY")
    checked = []
    for index, condition in enumerate(conditions):
        column = find_column(schema, condition.field)
        if column is None:
            msg = f"El campo {condition.field} no existe en el esquema."
            raise _issue(index, msg, "SX-SRC-FILTER-FIELD")
        if condition.field not in filterable or column.edm_type in _NOT_FILTERABLE:
            msg = f"SAP no permite filtrar por {condition.field}."
            raise _issue(index, msg, "SX-SRC-FILTER-FIELD")
        literal(column, condition.value, odata_version=odata_version, index=index)
        checked.append(condition)
    return checked


def render_filter(
    conditions: list[FilterCondition], schema: EntitySchema, odata_version: str
) -> str:
    """``$filter`` listo para enviar; vacío si no hay condiciones."""
    parts = []
    for index, condition in enumerate(conditions):
        column = find_column(schema, condition.field)
        if column is None:  # el esquema cambió desde que se guardó el filtro
            msg = f"El campo {condition.field} del filtro ya no existe en el esquema."
            raise _issue(index, msg, "SX-SRC-FILTER-FIELD")
        value = literal(column, condition.value, odata_version=odata_version, index=index)
        parts.append(f"{column.name} {condition.op.value} {value}")
    return " and ".join(parts)


def literal(column: Column, value: object, *, odata_version: str, index: int = 0) -> str:  # noqa: PLR0911, PLR0912
    """Literal OData del valor según el tipo EDM de la columna."""
    v2 = odata_version.startswith("2")
    edm = column.edm_type
    if isinstance(value, bool) != (edm == "Edm.Boolean"):
        raise _bad(index, column)
    if edm == "Edm.Boolean":
        return "true" if value else "false"
    text = str(value).strip()
    if not text or len(text) > MAX_VALUE_LENGTH:
        raise _bad(index, column)
    if edm == "Edm.String":
        if column.max_length and len(text) > column.max_length:
            msg = f"{column.name} admite como máximo {column.max_length} caracteres."
            raise _issue(index, msg, "SX-SRC-FILTER-VALUE")
        return "'" + text.replace("'", "''") + "'"
    if edm == "Edm.Guid":
        if not _GUID.match(text):
            raise _bad(index, column)
        return f"guid'{text}'" if v2 else text
    if edm in _INTEGER or edm in _FLOAT or edm == "Edm.Decimal":
        number = _number(text, index, column)
        if edm in _INTEGER and number != number.to_integral_value():
            raise _bad(index, column)
        rendered = format(number, "f")
        if v2 and edm == "Edm.Decimal":
            return f"{rendered}M"
        if v2 and edm == "Edm.Int64":
            return f"{rendered}L"
        return rendered
    if is_date_only(column):
        day = _parse(date.fromisoformat, text[:10], index, column)
        if v2 and edm == "Edm.DateTime":
            return f"datetime'{day.isoformat()}T00:00:00'"
        return day.isoformat()
    if edm == "Edm.DateTime":
        moment = _parse(datetime.fromisoformat, text, index, column)
        if moment.tzinfo is not None:  # hora local SAP sin zona: no se aceptan desfases
            raise _bad(index, column)
        return f"datetime'{moment.isoformat(timespec='seconds')}'"
    if edm == "Edm.DateTimeOffset":
        moment = _parse(datetime.fromisoformat, text.replace("Z", "+00:00"), index, column)
        if moment.tzinfo is None:
            raise _bad(index, column)
        iso = moment.isoformat(timespec="seconds").replace("+00:00", "Z")
        return f"datetimeoffset'{iso}'" if v2 else iso
    msg = f"SAP no permite filtrar por {column.name} ({edm})."
    raise _issue(index, msg, "SX-SRC-FILTER-FIELD")


def _bad(index: int, column: Column) -> BusinessRuleError:
    msg = f"Valor no válido para {column.name} ({column.edm_type})."
    return _issue(index, msg, "SX-SRC-FILTER-VALUE")


def _number(text: str, index: int, column: Column) -> Decimal:
    # Solo notación decimal simple: "1e1000000" cabe en MAX_VALUE_LENGTH pero se expandiría a
    # un millón de dígitos al formatearlo.
    if not _PLAIN_NUMBER.match(text):
        raise _bad(index, column)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise _bad(index, column) from None
    digits = number.as_tuple().digits
    if len(digits) > MAX_DIGITS:
        raise _bad(index, column)
    if column.edm_type == "Edm.Decimal" and column.precision is not None:
        exponent = number.as_tuple().exponent
        scale = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        if len(digits) - scale > column.precision - (column.scale or 0) or scale > (
            column.scale or 0
        ):
            raise _bad(index, column)
    return number


def _parse[T](parser: Callable[[str], T], text: str, index: int, column: Column) -> T:
    try:
        return parser(text)
    except ValueError:
        raise _bad(index, column) from None
