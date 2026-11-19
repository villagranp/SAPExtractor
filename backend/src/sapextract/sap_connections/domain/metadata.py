"""Modelo interno de un servicio OData (resultado de leer su ``$metadata``, V2 o V4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OdpRole(StrEnum):
    """Papel de un *entity set* en un servicio generado con *Redefine → ODP Extraction*."""

    ENTITY = "entity"  # EntityOf<ODP>: extractor (contexto SAPI)
    FACTS = "facts"  # FactsOf<ODP>: CDS / datos de hechos (contexto ABAP_CDS)
    ATTRIBUTES = "attributes"  # AttrOf<ODP>
    TEXTS = "texts"  # TextOf<ODP>
    HIERARCHY = "hierarchy"  # HierOf<ODP>
    DELTA_LINKS = "delta_links"  # DeltaLinksOf<EntitySet>: tokens de delta emitidos


ODP_PREFIXES: tuple[tuple[str, OdpRole], ...] = (
    ("DeltaLinksOf", OdpRole.DELTA_LINKS),
    ("EntityOf", OdpRole.ENTITY),
    ("FactsOf", OdpRole.FACTS),
    ("AttrOf", OdpRole.ATTRIBUTES),
    ("TextOf", OdpRole.TEXTS),
    ("HierOf", OdpRole.HIERARCHY),
)


def odp_role(entity_set: str) -> OdpRole | None:
    for prefix, role in ODP_PREFIXES:
        if entity_set.startswith(prefix) and len(entity_set) > len(prefix):
            return role
    return None


def odp_name(entity_set: str) -> str | None:
    """Nombre del ODP (DataSource o CDS) a partir del entity set, p. ej. ``0FI_GL_14``."""
    for prefix, role in ODP_PREFIXES:
        if role is not OdpRole.DELTA_LINKS and entity_set.startswith(prefix):
            return entity_set[len(prefix) :] or None
    return None


@dataclass(frozen=True, kw_only=True)
class EdmProperty:
    name: str
    edm_type: str
    nullable: bool = True
    is_key: bool = False
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    label: str | None = None
    filterable: bool = True
    sortable: bool = True
    display_format: str | None = None


@dataclass(frozen=True, kw_only=True)
class EdmEntityType:
    name: str
    namespace: str
    keys: tuple[str, ...]
    properties: tuple[EdmProperty, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"


@dataclass(frozen=True, kw_only=True)
class EdmEntitySet:
    name: str
    entity_type: str  # nombre cualificado (Namespace.Tipo)
    label: str | None = None
    pageable: bool = True
    countable: bool = True
    change_tracking: bool = False  # sap:change-tracking (V2) o Capabilities.ChangeTracking (V4)

    @property
    def odp_role(self) -> OdpRole | None:
        return odp_role(self.name)


@dataclass(frozen=True, kw_only=True)
class ServiceMetadata:
    odata_version: str  # "2.0" | "4.0"
    namespaces: tuple[str, ...]
    entity_types: tuple[EdmEntityType, ...]
    entity_sets: tuple[EdmEntitySet, ...]
    _types: dict[str, EdmEntityType] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        for et in self.entity_types:
            self._types[et.qualified_name] = et
            self._types.setdefault(et.name, et)

    def entity_type_of(self, entity_set: EdmEntitySet) -> EdmEntityType | None:
        name = entity_set.entity_type
        return self._types.get(name) or self._types.get(name.rsplit(".", 1)[-1])

    def supports_delta(self, entity_set: EdmEntitySet) -> bool:
        """Delta nativo: el set declara *change tracking* o existe ``DeltaLinksOf<set>``."""
        names = {s.name for s in self.entity_sets}
        return entity_set.change_tracking or f"DeltaLinksOf{entity_set.name}" in names
