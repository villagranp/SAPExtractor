"""Explorador de servicios de Gateway (plan 04 §2.3): catálogo en caché, entity sets, metadatos."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from sapextract.core.clock import utcnow
from sapextract.core.exceptions import NotFoundError
from sapextract.core.pagination import PageParams
from sapextract.sap_connections.application.system_service import SapSystemService
from sapextract.sap_connections.application.toolkit import SapToolkit
from sapextract.sap_connections.domain.catalog import (
    CatalogQuery,
    CatalogService,
    ClassificationSource,
    ServiceKind,
    classify,
    default_service_path,
)
from sapextract.sap_connections.domain.metadata import (
    EdmEntitySet,
    EdmEntityType,
    OdpRole,
    ServiceMetadata,
    odp_name,
)
from sapextract.sap_connections.domain.model import ConnectionSpec
from sapextract.sap_connections.infrastructure.metadata_parser import parse_metadata
from sapextract.sap_connections.infrastructure.models import SapCatalogCache


@dataclass(frozen=True, kw_only=True)
class CatalogPage:
    items: list[CatalogService]
    total: int
    fetched_at: datetime
    from_cache: bool
    counts: dict[str, int]


@dataclass(frozen=True, kw_only=True)
class EntitySetInfo:
    entity_set: EdmEntitySet
    entity_type: EdmEntityType | None
    supports_delta: bool
    odp_name: str | None

    @property
    def odp_role(self) -> OdpRole | None:
        return self.entity_set.odp_role


@dataclass(frozen=True, kw_only=True)
class ServiceDetail:
    service: CatalogService
    metadata: ServiceMetadata
    entity_sets: list[EntitySetInfo]


def _to_json(service: CatalogService) -> dict[str, Any]:
    data = asdict(service)
    data["entity_sets"] = list(service.entity_sets) if service.entity_sets is not None else None
    return data


def _from_json(data: dict[str, Any]) -> CatalogService:
    sets = data.get("entity_sets")
    return CatalogService(
        technical_name=data["technical_name"],
        version=int(data.get("version") or 1),
        service_id=data.get("service_id") or data["technical_name"],
        title=data.get("title"),
        description=data.get("description"),
        service_path=data.get("service_path"),
        entity_sets=tuple(sets) if sets is not None else None,
        kind=ServiceKind(data.get("kind") or ServiceKind.UNKNOWN),
        classified_by=ClassificationSource(data["classified_by"])
        if data.get("classified_by")
        else None,
        supports_delta=data.get("supports_delta"),
    )


class SapExplorerService:
    def __init__(self, session: Session, toolkit: SapToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.systems = SapSystemService(session, toolkit)

    # -------------------------------------------------------------- catálogo
    def services(
        self,
        system_id: uuid.UUID,
        query: CatalogQuery,
        page: PageParams,
        *,
        refresh: bool = False,
    ) -> CatalogPage:
        services, fetched_at, from_cache = self._catalog(system_id, refresh=refresh)
        counts = {k.value: 0 for k in ServiceKind}
        for s in services:
            counts[s.kind.value] += 1
        filtered = query.apply(services)
        return CatalogPage(
            items=filtered[page.offset : page.offset + page.page_size],
            total=len(filtered),
            fetched_at=fetched_at,
            from_cache=from_cache,
            counts=counts,
        )

    # -------------------------------------------------------------- un servicio
    def service_detail(
        self, system_id: uuid.UUID, technical_name: str, version: int = 1
    ) -> ServiceDetail:
        """Lee ``$metadata``: entity sets, propiedades y capacidad de delta.

        Con lo leído se afina la clasificación del servicio en la caché del catálogo.
        """
        service = self._find(system_id, technical_name, version)
        spec = self._spec(system_id)
        with self.tk.client_factory(spec, self.tk.explorer_retry) as client:
            raw = client.service_metadata(service.service_path or "")
        metadata = parse_metadata(raw)
        entity_sets = [
            EntitySetInfo(
                entity_set=es,
                entity_type=metadata.entity_type_of(es),
                supports_delta=metadata.supports_delta(es),
                odp_name=odp_name(es.name),
            )
            for es in metadata.entity_sets
        ]
        refined = classify(
            replace(
                service,
                entity_sets=tuple(es.name for es in metadata.entity_sets),
                supports_delta=any(e.supports_delta for e in entity_sets),
            )
        )
        self._update_cached(system_id, refined)
        return ServiceDetail(service=refined, metadata=metadata, entity_sets=entity_sets)

    # -------------------------------------------------------------- internos
    def _spec(self, system_id: uuid.UUID) -> ConnectionSpec:
        spec = self.systems.spec_for(self.systems.get(system_id))
        self.session.commit()  # no mantener la transacción abierta durante la llamada a SAP
        return spec

    def _catalog(
        self, system_id: uuid.UUID, *, refresh: bool
    ) -> tuple[list[CatalogService], datetime, bool]:
        self.systems.get(system_id)
        cache = self.session.get(SapCatalogCache, system_id)
        now = utcnow()
        if cache is not None and not refresh and cache.fetched_at + self.tk.catalog_ttl > now:
            return [_from_json(s) for s in cache.services], cache.fetched_at, True
        spec = self._spec(system_id)
        with self.tk.client_factory(spec, self.tk.explorer_retry) as client:
            services = client.catalog_services()
        # Conserva lo aprendido al abrir servicios si el catálogo no trae entity sets.
        known = {(s.technical_name, s.version): s for s in self._cached(system_id)}
        merged = [
            known[(s.technical_name, s.version)]
            if s.entity_sets is None
            and (s.technical_name, s.version) in known
            and known[(s.technical_name, s.version)].entity_sets is not None
            else s
            for s in services
        ]
        self._store(system_id, merged, now)
        return merged, now, False

    def _cached(self, system_id: uuid.UUID) -> list[CatalogService]:
        cache = self.session.get(SapCatalogCache, system_id)
        return [_from_json(s) for s in cache.services] if cache else []

    def _store(self, system_id: uuid.UUID, services: list[CatalogService], when: datetime) -> None:
        payload = [_to_json(s) for s in services]
        cache = self.session.get(SapCatalogCache, system_id)
        if cache is None:
            self.session.add(
                SapCatalogCache(sap_system_id=system_id, fetched_at=when, services=payload)
            )
        else:
            cache.fetched_at, cache.services = when, payload
        self.session.commit()

    def _find(self, system_id: uuid.UUID, technical_name: str, version: int) -> CatalogService:
        services, _, _ = self._catalog(system_id, refresh=False)
        for s in services:
            if s.technical_name.upper() == technical_name.upper() and s.version == version:
                return s
        if not technical_name.strip():
            msg = "Indique el servicio."
            raise NotFoundError(msg, code="SX-SAP-SERVICE-NOT-FOUND")
        # No está en el catálogo (p. ej. caché antigua): se intenta por la ruta estándar.
        return CatalogService(
            technical_name=technical_name.upper(),
            version=version,
            service_id=f"{technical_name.upper()}_{version:04d}",
            service_path=default_service_path(technical_name.upper(), version),
        )

    def _update_cached(self, system_id: uuid.UUID, service: CatalogService) -> None:
        cache = self.session.get(SapCatalogCache, system_id)
        if cache is None:
            return
        key = (service.technical_name, service.version)
        services = [_from_json(s) for s in cache.services]
        updated = [service if (s.technical_name, s.version) == key else s for s in services]
        if key not in {(s.technical_name, s.version) for s in services}:
            updated.append(service)
        cache.services = [_to_json(s) for s in updated]
        self.session.commit()
