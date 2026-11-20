"""``$metadata`` de un entity set → ``EntitySchema`` del flujo de datos (ADR 0006 §2)."""

from __future__ import annotations

from sapextract.core.exceptions import NotFoundError
from sapextract.dataflow.domain.schema import Column, EntitySchema
from sapextract.sap_connections.domain.metadata import ServiceMetadata


def entity_schema(metadata: ServiceMetadata, entity_set: str) -> EntitySchema:
    found = next((s for s in metadata.entity_sets if s.name == entity_set), None)
    entity_type = metadata.entity_type_of(found) if found else None
    if found is None or entity_type is None:
        msg = f"El servicio no tiene el entity set «{entity_set}»."
        raise NotFoundError(msg, code="SX-SAP-ENTITY-SET-NOT-FOUND")
    return EntitySchema(
        columns=tuple(
            Column(
                name=p.name,
                edm_type=p.edm_type,
                nullable=p.nullable and not p.is_key,
                is_key=p.is_key,
                max_length=p.max_length,
                precision=p.precision,
                scale=p.scale,
                label=p.label,
                display_format=p.display_format,
            )
            for p in entity_type.properties
        )
    )
