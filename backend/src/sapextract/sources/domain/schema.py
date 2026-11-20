"""Esquema de un origen: construcción desde el ``$metadata`` y diff entre versiones (plan 05 §3).

El tipo compartido es ``EntitySchema`` de ``dataflow`` (ADR 0006 §2). Aquí van los ayudantes
propios de los orígenes: qué campos admiten ``$filter``, buscar una columna sin excepción,
cambiar las claves y comparar versiones.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.sap_connections.domain.metadata import EdmEntityType

DATE_FORMAT = "date"  # sap:display-format="Date": Edm.DateTime que solo lleva fecha


def is_date_only(column: Column) -> bool:
    return column.edm_type == "Edm.Date" or (
        column.edm_type == "Edm.DateTime" and (column.display_format or "").lower() == DATE_FORMAT
    )


def find_column(schema: EntitySchema, name: str) -> Column | None:
    return next((c for c in schema.columns if c.name == name), None)


def column_names(schema: EntitySchema) -> tuple[str, ...]:
    return tuple(c.name for c in schema.columns)


def with_keys(schema: EntitySchema, keys: tuple[str, ...]) -> EntitySchema:
    """Mismo esquema con otras claves (las que confirma el usuario en el origen)."""
    wanted = set(keys)
    return EntitySchema(
        columns=tuple(
            replace(c, is_key=c.name in wanted, nullable=c.nullable and c.name not in wanted)
            for c in schema.columns
        )
    )


def _invalid(message: str) -> BusinessRuleError:
    code = "SX-SRC-SCHEMA-INVALID"
    return BusinessRuleError(message, code=code, errors=[FieldIssue("entity_set", message, code)])


@dataclass(frozen=True, kw_only=True)
class DetectedSchema:
    """Esquema leído de SAP más lo que no forma parte del contrato: qué campos se pueden filtrar."""

    schema: EntitySchema
    filterable: frozenset[str]

    def to_json(self) -> list[dict[str, Any]]:
        return [
            c.as_dict() | {"filterable": c.name in self.filterable} for c in self.schema.columns
        ]

    @classmethod
    def from_json(cls, columns: list[dict[str, Any]]) -> DetectedSchema:
        return cls(
            schema=EntitySchema.from_list(columns),
            filterable=frozenset(c["name"] for c in columns if c.get("filterable", True)),
        )


def detect_schema(entity_type: EdmEntityType) -> DetectedSchema:
    """``EntitySchema`` con las claves que declara SAP (mismo criterio que el destino)."""
    keys = set(entity_type.keys)
    columns = tuple(
        Column(
            name=p.name,
            edm_type=p.edm_type,
            nullable=p.nullable and p.name not in keys,
            is_key=p.name in keys,
            max_length=p.max_length,
            precision=p.precision,
            scale=p.scale,
            label=p.label,
            display_format=p.display_format,
        )
        for p in entity_type.properties
    )
    try:
        schema = EntitySchema(columns=columns)
    except ValueError as exc:  # columnas repetidas o reservadas para uso técnico
        raise _invalid(str(exc)) from exc
    return DetectedSchema(
        schema=schema,
        filterable=frozenset(p.name for p in entity_type.properties if p.filterable),
    )


# ------------------------------------------------------------------------------ diff


@dataclass(frozen=True, kw_only=True)
class ColumnChange:
    name: str
    before: dict[str, Any]
    after: dict[str, Any]
    breaking: bool


@dataclass(frozen=True, kw_only=True)
class SchemaDiff:
    added: list[Column] = field(default_factory=list)
    removed: list[Column] = field(default_factory=list)
    changed: list[ColumnChange] = field(default_factory=list)
    reordered: bool = False

    @property
    def breaking(self) -> bool:
        """Columna eliminada o cambio incompatible: el run se detiene (SX-SRC-SCHEMA-BREAKING)."""
        return bool(self.removed) or any(c.breaking for c in self.changed)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.reordered)


_STRUCTURAL = ("edm_type", "max_length", "precision", "scale", "display_format", "is_key")


def diff_schemas(old: EntitySchema, new: EntitySchema) -> SchemaDiff:
    old_by_name = {c.name: c for c in old.columns}
    new_by_name = {c.name: c for c in new.columns}
    new_names = column_names(new)
    changed = []
    for name in old_by_name.keys() & new_by_name.keys():
        before, after = old_by_name[name], new_by_name[name]
        b = {k: getattr(before, k) for k in _STRUCTURAL}
        a = {k: getattr(after, k) for k in _STRUCTURAL}
        if a != b:
            changed.append(
                ColumnChange(name=name, before=b, after=a, breaking=not _compatible(before, after))
            )
    common_old = [n for n in column_names(old) if n in new_by_name]
    common_new = [n for n in new_names if n in old_by_name]
    return SchemaDiff(
        added=[c for c in new.columns if c.name not in old_by_name],
        removed=[c for c in old.columns if c.name not in new_by_name],
        changed=sorted(changed, key=lambda c: new_names.index(c.name)),
        reordered=common_old != common_new,
    )


def _compatible(before: Column, after: Column) -> bool:
    """Solo se admite ensanchar: texto más largo, o decimal con más dígitos y misma escala."""
    if before.is_key != after.is_key or before.display_format != after.display_format:
        return False
    if before.edm_type != after.edm_type:
        return False
    if after.edm_type == "Edm.String":
        return before.max_length is not None and (
            after.max_length is None or after.max_length >= before.max_length
        )
    if after.edm_type == "Edm.Decimal":
        return (
            before.scale == after.scale
            and before.precision is not None
            and after.precision is not None
            and after.precision >= before.precision
        )
    return False
