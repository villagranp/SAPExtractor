"""Modelo de dominio de los orígenes SAP (plan 05 §1) y sus reglas de validación."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sapextract.core.exceptions import BusinessRuleError, FieldIssue
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.sap_connections.domain.metadata import OdpRole
from sapextract.sources.domain.schema import find_column, is_date_only


class SourceType(StrEnum):
    ODP_EXTRACTOR = "ODP_EXTRACTOR"  # DataSource vía SEGW ODP, contexto SAPI
    CDS_ODP = "CDS_ODP"  # CDS vía SEGW ODP, contexto ABAP_CDS
    CDS_ODATA = "CDS_ODATA"  # CDS publicada como OData (sin delta nativo)


class OdpContext(StrEnum):
    SAPI = "SAPI"
    ABAP_CDS = "ABAP_CDS"


class DeltaMethod(StrEnum):
    ODQ = "ODQ"  # delta nativo de la cola ODQ
    WATERMARK = "WATERMARK"  # pseudo-delta por campo de modificación
    NONE = "NONE"  # solo carga completa


class DeltaSemantics(StrEnum):
    """Proceso de delta del extractor (RSA2). No se detecta: lo declara el usuario."""

    AFTER_IMAGE = "after_image"
    ADDITIVE = "additive"
    BEFORE_AFTER = "before_after"
    UNKNOWN = "unknown"


class WatermarkKind(StrEnum):
    DATETIME = "datetime"
    DATE = "date"
    DECIMAL_TIMESTAMP = "decimal_timestamp"  # TIMESTAMP (DEC 15) o TIMESTAMPL (DEC 21,7)


class SourceStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
MIN_PAGE_SIZE, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE = 100, 50_000, 5_000
MAX_OVERLAP_SECONDS = 86_400
DEFAULT_OVERLAP_SECONDS = 300
ODP_TYPES = frozenset({SourceType.ODP_EXTRACTOR, SourceType.CDS_ODP})
CONTEXT_OF: dict[SourceType, OdpContext] = {
    SourceType.ODP_EXTRACTOR: OdpContext.SAPI,
    SourceType.CDS_ODP: OdpContext.ABAP_CDS,
}
# Transiciones de estado permitidas (plan 05 §1).
TRANSITIONS: dict[SourceStatus, frozenset[SourceStatus]] = {
    SourceStatus.DRAFT: frozenset({SourceStatus.ACTIVE}),
    SourceStatus.ACTIVE: frozenset({SourceStatus.DEPRECATED}),
    SourceStatus.DEPRECATED: frozenset({SourceStatus.ACTIVE}),
}
_WATERMARK_TYPES: dict[WatermarkKind, frozenset[str]] = {
    WatermarkKind.DATETIME: frozenset({"Edm.DateTime", "Edm.DateTimeOffset"}),
    WatermarkKind.DATE: frozenset({"Edm.DateTime", "Edm.Date"}),
    WatermarkKind.DECIMAL_TIMESTAMP: frozenset({"Edm.Decimal"}),
}


def issue(fieldname: str, message: str, code: str) -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[FieldIssue(fieldname, message, code)])


def normalize_code(code: str) -> str:
    value = code.strip().upper()
    if not CODE_PATTERN.match(value):
        msg = (
            "El código debe tener de 3 a 40 caracteres: letras, números y guion bajo, y empezar "
            "por una letra (p. ej. FI_GL_ITEMS)."
        )
        raise issue("code", msg, "SX-SRC-CODE-INVALID")
    return value


def normalize_page_size(value: int | None) -> int:
    size = DEFAULT_PAGE_SIZE if value is None else value
    if not MIN_PAGE_SIZE <= size <= MAX_PAGE_SIZE:
        msg = f"El tamaño de página debe estar entre {MIN_PAGE_SIZE} y {MAX_PAGE_SIZE} filas."
        raise issue("page_size", msg, "SX-SRC-PAGE-SIZE-INVALID")
    return size


def suggest_type(role: OdpRole | None) -> SourceType:
    """Tipo de origen según el entity set: EntityOf → extractor; FactsOf, AttrOf… → CDS."""
    if role is None:
        return SourceType.CDS_ODATA
    if role is OdpRole.ENTITY:
        return SourceType.ODP_EXTRACTOR
    return SourceType.CDS_ODP


def check_type(source_type: SourceType, role: OdpRole | None) -> None:
    """Un origen ODP necesita un entity set ODP; uno CDS_ODATA, uno que no lo sea."""
    if role is OdpRole.DELTA_LINKS:
        msg = "Ese entity set solo lista tokens de delta: elija el entity set de datos."
        raise issue("entity_set", msg, "SX-SRC-ENTITY-SET-INVALID")
    if role is OdpRole.HIERARCHY:
        msg = "Las jerarquías (HierOf…) no se admiten todavía como origen."
        raise issue("entity_set", msg, "SX-SRC-ENTITY-SET-INVALID")
    is_odp = role is not None
    if (source_type in ODP_TYPES) != is_odp:
        msg = (
            "El tipo no corresponde al entity set: los orígenes ODP usan EntityOf… o FactsOf…, "
            "y CDS vía OData usa un entity set normal."
        )
        raise issue("source_type", msg, "SX-SRC-TYPE-MISMATCH")


def check_keys(keys: tuple[str, ...], schema: EntitySchema) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(k.strip() for k in keys if k.strip()))
    if not cleaned:
        msg = "Indique al menos un campo clave: el destino los necesita para no duplicar filas."
        raise issue("key_fields", msg, "SX-SRC-KEYS-REQUIRED")
    unknown = [k for k in cleaned if find_column(schema, k) is None]
    if unknown:
        msg = f"Campos clave que no existen en el esquema: {', '.join(unknown)}."
        raise issue("key_fields", msg, "SX-SRC-KEYS-UNKNOWN")
    return cleaned


@dataclass(frozen=True, kw_only=True)
class DeltaConfig:
    method: DeltaMethod
    semantics: DeltaSemantics | None = None
    watermark_field: str | None = None
    watermark_kind: WatermarkKind | None = None
    watermark_timezone: str | None = None
    watermark_overlap_seconds: int | None = None


def check_delta(
    config: DeltaConfig, *, source_type: SourceType, supports_delta: bool, schema: EntitySchema
) -> DeltaConfig:
    """Valida y normaliza la configuración de delta según el tipo de origen."""
    if config.method is DeltaMethod.ODQ:
        if source_type not in ODP_TYPES:
            msg = "El delta ODQ solo existe en orígenes ODP; use marca de agua o carga completa."
            raise issue("delta_method", msg, "SX-SRC-DELTA-UNSUPPORTED")
        if not supports_delta:
            msg = "El servicio no declara delta (change tracking): solo admite carga completa."
            raise issue("delta_method", msg, "SX-SRC-DELTA-UNSUPPORTED")
        return DeltaConfig(
            method=DeltaMethod.ODQ, semantics=config.semantics or DeltaSemantics.UNKNOWN
        )
    if config.method is DeltaMethod.NONE:
        return DeltaConfig(method=DeltaMethod.NONE)
    if source_type in ODP_TYPES:
        msg = "En orígenes ODP el delta lo da la ODQ: elija delta ODQ o carga completa."
        raise issue("delta_method", msg, "SX-SRC-DELTA-UNSUPPORTED")
    return _check_watermark(config, schema)


def _check_watermark(config: DeltaConfig, schema: EntitySchema) -> DeltaConfig:
    if not config.watermark_field or config.watermark_kind is None:
        msg = "La marca de agua necesita un campo y su tipo."
        raise issue("watermark_field", msg, "SX-SRC-WATERMARK-REQUIRED")
    column = find_column(schema, config.watermark_field)
    if column is None:
        msg = f"El campo {config.watermark_field} no existe en el esquema."
        raise issue("watermark_field", msg, "SX-SRC-WATERMARK-UNKNOWN")
    kind = config.watermark_kind
    date_only = is_date_only(column)
    compatible = column.edm_type in _WATERMARK_TYPES[kind] and not (
        kind is WatermarkKind.DATETIME and date_only
    )
    if not compatible:
        msg = (
            f"El campo {column.name} ({column.edm_type}) no sirve como marca de agua "
            f"de tipo {kind}."
        )
        raise issue("watermark_kind", msg, "SX-SRC-WATERMARK-TYPE")
    # Un DateTimeOffset es un instante; el resto son horas locales del sistema sin zona (H15).
    timezone = (
        "UTC"
        if column.edm_type == "Edm.DateTimeOffset"
        else normalize_timezone(config.watermark_timezone)
    )
    overlap = (
        DEFAULT_OVERLAP_SECONDS
        if config.watermark_overlap_seconds is None
        else config.watermark_overlap_seconds
    )
    if not 0 <= overlap <= MAX_OVERLAP_SECONDS:
        msg = f"El solapamiento debe estar entre 0 y {MAX_OVERLAP_SECONDS} segundos."
        raise issue("watermark_overlap_seconds", msg, "SX-SRC-WATERMARK-OVERLAP")
    return DeltaConfig(
        method=DeltaMethod.WATERMARK,
        watermark_field=column.name,
        watermark_kind=kind,
        watermark_timezone=timezone,
        watermark_overlap_seconds=overlap,
    )


def normalize_timezone(value: str | None) -> str:
    """Zona IANA del valor de la marca de agua (las horas ABAP no llevan zona)."""
    name = (value or "").strip()
    if not name:
        msg = "Indique la zona horaria del campo (p. ej. America/Guatemala o UTC)."
        raise issue("watermark_timezone", msg, "SX-SRC-TIMEZONE-REQUIRED")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        msg = f"Zona horaria desconocida: {name}."
        raise issue("watermark_timezone", msg, "SX-SRC-TIMEZONE-INVALID") from None
    return name


def timezone_names() -> list[str]:
    return sorted(available_timezones())


def check_transition(current: SourceStatus, target: SourceStatus) -> None:
    if target not in TRANSITIONS[current]:
        msg = f"No se puede pasar un origen de «{current}» a «{target}»."
        raise issue("status", msg, "SX-SRC-STATUS-TRANSITION")
