"""Dominio de destinos (plan 07): tipos, estados, nombres seguros y rutas CSV."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DeleteHandling, LoadOptions
from sapextract.dataflow.domain.schema import EntitySchema


class DestinationType(StrEnum):
    CSV = "csv"
    SNOWFLAKE = "snowflake"
    SQLSERVER = "sqlserver"


class DestinationStatus(StrEnum):
    """Igual que los sistemas SAP: activar exige una prueba correcta y vigente."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class DestinationEnvironment(StrEnum):
    DEV = "DEV"
    QAS = "QAS"
    PRD = "PRD"


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,29}$")
# Identificador SQL que se acepta de un usuario (tabla, esquema, base): se valida y se cita.
SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_NON_IDENT = re.compile(r"[^A-Z0-9_]")


def _issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not CODE_PATTERN.match(value):
        msg = "El código debe tener de 3 a 30 caracteres: letras, números y guion bajo."
        raise _issue("code", msg, "SX-DST-CODE-INVALID")
    return value


def check_identifier(value: str, fieldname: str) -> str:
    """Identificador SQL escrito por un usuario: letras, números y ``_``; nunca se interpola."""
    if not SQL_IDENTIFIER.match(value):
        msg = (
            f"«{value}» no es un nombre válido: use letras, números y guion bajo, "
            "empezando por letra o guion bajo (máx. 128)."
        )
        raise _issue(fieldname, msg, "SX-DST-IDENTIFIER-INVALID")
    return value


def target_column_name(source: str) -> str:
    """Nombre de columna en destino a partir del nombre SAP (``/BIC/ZFIELD`` → ``BIC_ZFIELD``)."""
    name = _NON_IDENT.sub("_", source.strip().upper()).strip("_")
    if not name:
        name = "COL"
    if name[0].isdigit():
        name = f"C_{name}"
    return name[:128]


def resolve_columns(schema: EntitySchema, mapping: Mapping[str, str | None]) -> dict[str, str]:
    """Columna de origen → columna en destino, aplicando ``column_mapping`` (``None`` excluye).

    Rechaza nombres no válidos, colisiones y excluir columnas clave.
    """
    unknown = sorted(set(mapping) - {c.name for c in schema.columns})
    if unknown:
        msg = "El mapeo menciona columnas que no existen: " + ", ".join(unknown)
        raise _issue("column_mapping", msg, "SX-DST-MAPPING-UNKNOWN")
    result: dict[str, str] = {}
    for column in schema.columns:
        if column.name in mapping and mapping[column.name] is None:
            if column.is_key:
                msg = f"No se puede excluir la columna clave {column.name}."
                raise _issue("column_mapping", msg, "SX-DST-MAPPING-KEY")
            continue
        target = mapping.get(column.name) or target_column_name(column.name)
        result[column.name] = check_identifier(target, "column_mapping")
    seen: dict[str, str] = {}
    for source, target in result.items():
        clash = seen.get(target.upper())
        if clash:
            msg = f"Las columnas {clash} y {source} acabarían con el mismo nombre ({target})."
            raise _issue("column_mapping", msg, "SX-DST-MAPPING-CLASH")
        seen[target.upper()] = source
    return result


def check_load_options(options: LoadOptions, supported: frozenset[LoadMode]) -> None:
    check_identifier(options.target_name, "target_name")
    if options.load_mode not in supported:
        msg = f"Este destino no admite el modo de carga «{options.load_mode.value}»."
        raise _issue("load_mode", msg, "SX-DST-MODE-UNSUPPORTED")


# ------------------------------------------------------------------ rutas CSV
@dataclass(frozen=True)
class CsvRoots:
    """Raíces permitidas para destinos CSV (``SX_CSV_ROOTS``)."""

    roots: tuple[Path, ...]

    def resolve(self, root: str, relative: str) -> Path:
        """Ruta real dentro de una raíz permitida: rechaza ``..``, rutas absolutas y enlaces."""
        allowed = {str(r): r for r in self.roots}
        base = allowed.get(root)
        if base is None:
            msg = "La carpeta raíz no está entre las permitidas (SX_CSV_ROOTS)."
            raise _issue("root", msg, "SX-DST-CSV-ROOT")
        raw = relative.strip().replace("\\", "/")
        rel = raw.rstrip("/")
        if (
            PurePosixPath(raw).is_absolute()
            or PureWindowsPath(relative).drive
            or any(part in {"..", ""} for part in rel.split("/") if rel)
        ):
            msg = "La subcarpeta debe ser relativa y no puede contener «..»."
            raise _issue("path", msg, "SX-DST-CSV-PATH")
        resolved_base = base.resolve()
        target = (resolved_base / rel).resolve() if rel else resolved_base
        if not target.is_relative_to(resolved_base):
            msg = "La ruta sale de la carpeta raíz permitida."
            raise _issue("path", msg, "SX-DST-CSV-PATH")
        return target


def default_delete_handling(mode: LoadMode) -> DeleteHandling:
    return DeleteHandling.SOFT if mode is LoadMode.UPSERT else DeleteHandling.IGNORE
