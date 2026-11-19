"""Catálogo de servicios de Gateway y su clasificación ODP / CDS (plan 04 §2.3)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from sapextract.sap_connections.domain.metadata import OdpRole, odp_role


class ServiceKind(StrEnum):
    ODP = "odp"  # SEGW con Redefine → ODP Extraction (extractor o CDS vía ODP)
    CDS = "cds"  # CDS publicada (@OData.publish / service binding)
    OTHER = "other"  # cualquier otro servicio
    UNKNOWN = "unknown"  # sin entity sets conocidos todavía: se clasifica al abrirlo


class ClassificationSource(StrEnum):
    ENTITY_SETS = "entity_sets"  # por los entity sets del catálogo o de $metadata
    NAME = "name"  # solo por convención de nombre


# Sufijo que añade SAP al publicar una CDS con @OData.publish: true.
CDS_SERVICE_SUFFIX = "_CDS"


@dataclass(frozen=True, kw_only=True)
class CatalogService:
    technical_name: str
    version: int
    service_id: str
    title: str | None = None
    description: str | None = None
    service_path: str | None = None  # ruta relativa al Gateway, p. ej. /sap/opu/odata/sap/X_SRV
    entity_sets: tuple[str, ...] | None = None  # None: el catálogo no los informó
    kind: ServiceKind = ServiceKind.UNKNOWN
    classified_by: ClassificationSource | None = None
    supports_delta: bool | None = None

    @property
    def odp_entity_sets(self) -> tuple[str, ...]:
        return tuple(
            s for s in self.entity_sets or () if odp_role(s) not in (None, OdpRole.DELTA_LINKS)
        )

    def matches(self, search: str) -> bool:
        needle = search.strip().lower()
        haystack = " ".join(
            x for x in (self.technical_name, self.title, self.description) if x
        ).lower()
        return needle in haystack


def default_service_path(technical_name: str, version: int) -> str:
    """Ruta estándar de un servicio V2: ``/sap/opu/odata/sap/<NOMBRE>[;v=000N]``.

    Los servicios con espacio de nombres (``/NS/SERVICIO``) usan ``/sap/opu/odata/NS/SERVICIO``.
    """
    name = technical_name.strip("/")
    base = f"/sap/opu/odata/{name}" if "/" in name else f"/sap/opu/odata/sap/{name}"
    return base if version <= 1 else f"{base};v={version:04d}"


def classify(service: CatalogService) -> CatalogService:
    """Clasifica por entity sets (fiable) o, si no se conocen, por convención de nombre."""
    if service.entity_sets is not None:
        roles = {odp_role(s) for s in service.entity_sets}
        if roles - {None}:
            return replace(
                service,
                kind=ServiceKind.ODP,
                classified_by=ClassificationSource.ENTITY_SETS,
                supports_delta=OdpRole.DELTA_LINKS in roles,
            )
        kind = (
            ServiceKind.CDS
            if service.technical_name.upper().endswith(CDS_SERVICE_SUFFIX)
            else ServiceKind.OTHER
        )
        return replace(
            service,
            kind=kind,
            classified_by=ClassificationSource.ENTITY_SETS,
            supports_delta=service.supports_delta or False,
        )
    if service.technical_name.upper().endswith(CDS_SERVICE_SUFFIX):
        return replace(service, kind=ServiceKind.CDS, classified_by=ClassificationSource.NAME)
    return replace(service, kind=ServiceKind.UNKNOWN, classified_by=None)


@dataclass(frozen=True, kw_only=True)
class CatalogQuery:
    search: str | None = None
    kinds: frozenset[ServiceKind] = field(default_factory=frozenset)  # vacío = todos

    def apply(self, services: list[CatalogService]) -> list[CatalogService]:
        result = [
            s
            for s in services
            if (not self.kinds or s.kind in self.kinds)
            and (not self.search or s.matches(self.search))
        ]
        return sorted(result, key=lambda s: (s.technical_name, s.version))
