"""Esquema de un entity set SAP para la vista previa de DDL (lee ``$metadata``)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.sap_connections.application.explorer_service import SapExplorerService
from sapextract.sap_connections.application.schema import entity_schema
from sapextract.sap_connections.application.toolkit import SapToolkit


def source_entity_schema(
    session: Session,
    sap: SapToolkit,
    *,
    sap_system_id: uuid.UUID,
    service: str,
    version: int,
    entity_set: str,
) -> EntitySchema:
    detail = SapExplorerService(session, sap).service_detail(sap_system_id, service, version)
    return entity_schema(detail.metadata, entity_set)
