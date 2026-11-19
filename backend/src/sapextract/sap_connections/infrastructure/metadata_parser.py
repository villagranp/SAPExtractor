"""Parser de ``$metadata`` (EDMX OData V2 y V4) → :class:`ServiceMetadata`.

Usa ``defusedxml``: el XML viene de un sistema externo y no se resuelven entidades ni DTD.
Los espacios de nombres EDM cambian entre versiones, así que se compara por nombre local.
"""

from __future__ import annotations

# Solo se importa el tipo: el análisis del XML lo hace defusedxml.
from xml.etree.ElementTree import Element  # nosec B405

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from sapextract.sap_connections.domain.errors import SapResponseError
from sapextract.sap_connections.domain.metadata import (
    EdmEntitySet,
    EdmEntityType,
    EdmProperty,
    ServiceMetadata,
)

SAP_NS = "http://www.sap.com/Protocols/SAPData"
V4_EDMX_NS = "http://docs.oasis-open.org/odata/ns/edmx"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: Element, name: str) -> list[Element]:
    return [c for c in element if _local(c.tag) == name]


def _sap(element: Element, attr: str) -> str | None:
    return element.get(f"{{{SAP_NS}}}{attr}")


def _int(value: str | None) -> int | None:
    if value is None or not value.strip().isdigit():
        return None  # "max", "variable" o ausente
    return int(value)


def _false(value: str | None) -> bool:
    return value is not None and value.strip().lower() == "false"


def _true(value: str | None) -> bool:
    return value is not None and value.strip().lower() == "true"


def _annotation_label(element: Element) -> str | None:
    """Etiqueta V4: ``<Annotation Term="Common.Label" String="…"/>`` (con o sin alias SAP__)."""
    for ann in _children(element, "Annotation"):
        if (ann.get("Term") or "").endswith(".Label"):
            return ann.get("String")
    return None


def _change_tracking_supported(element: Element) -> bool:
    for ann in _children(element, "Annotation"):
        if not (ann.get("Term") or "").endswith(".ChangeTracking"):
            continue
        for record in _children(ann, "Record"):
            for pv in _children(record, "PropertyValue"):
                if pv.get("Property") == "Supported":
                    return _true(pv.get("Bool")) or _true(pv.findtext("*"))
        return True  # anotación presente sin registro explícito
    return False


def _parse_entity_type(element: Element, namespace: str) -> EdmEntityType:
    keys: tuple[str, ...] = ()
    for key in _children(element, "Key"):
        keys = tuple(ref.get("Name", "") for ref in _children(key, "PropertyRef"))
    properties = tuple(
        EdmProperty(
            name=prop.get("Name", ""),
            edm_type=prop.get("Type", ""),
            nullable=not _false(prop.get("Nullable")),
            is_key=prop.get("Name") in keys,
            max_length=_int(prop.get("MaxLength")),
            precision=_int(prop.get("Precision")),
            scale=_int(prop.get("Scale")),
            label=_sap(prop, "label") or _annotation_label(prop),
            filterable=not _false(_sap(prop, "filterable")),
            sortable=not _false(_sap(prop, "sortable")),
            display_format=_sap(prop, "display-format"),
        )
        for prop in _children(element, "Property")
    )
    return EdmEntityType(
        name=element.get("Name", ""), namespace=namespace, keys=keys, properties=properties
    )


def parse_metadata(content: bytes) -> ServiceMetadata:
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        msg = "El $metadata devuelto por SAP no es un XML EDMX válido."
        raise SapResponseError(msg) from exc
    if _local(root.tag) != "Edmx":
        msg = "La respuesta de SAP no es un documento $metadata (EDMX)."
        raise SapResponseError(msg)

    is_v4 = root.tag.startswith(f"{{{V4_EDMX_NS}}}")
    version = root.get("Version") or ("4.0" if is_v4 else "1.0")
    odata_version = "4.0" if is_v4 or version.startswith("4") else "2.0"

    schemas = [el for el in root.iter() if _local(el.tag) == "Schema"]
    entity_types: list[EdmEntityType] = []
    entity_sets: list[EdmEntitySet] = []
    # V4: anotaciones externas "Annotations Target=Contenedor/Set" con ChangeTracking.
    tracked_targets: set[str] = set()
    labels: dict[str, str] = {}
    for schema in schemas:
        namespace = schema.get("Namespace", "")
        for et in _children(schema, "EntityType"):
            entity_types.append(_parse_entity_type(et, namespace))
        for external in _children(schema, "Annotations"):
            target = external.get("Target", "")
            if _change_tracking_supported(external):
                tracked_targets.add(target.rsplit("/", 1)[-1])
            label = _annotation_label(external)
            if label:
                labels[target] = label
        for container in _children(schema, "EntityContainer"):
            for es in _children(container, "EntitySet"):
                name = es.get("Name", "")
                entity_sets.append(
                    EdmEntitySet(
                        name=name,
                        entity_type=es.get("EntityType", ""),
                        label=_sap(es, "label") or _annotation_label(es),
                        pageable=not _false(_sap(es, "pageable")),
                        countable=not _false(_sap(es, "countable")),
                        change_tracking=_true(_sap(es, "change-tracking"))
                        or _change_tracking_supported(es),
                    )
                )

    if tracked_targets or labels:
        entity_types = [_apply_labels(et, labels) for et in entity_types]
        entity_sets = [
            EdmEntitySet(
                name=s.name,
                entity_type=s.entity_type,
                label=s.label,
                pageable=s.pageable,
                countable=s.countable,
                change_tracking=s.change_tracking or s.name in tracked_targets,
            )
            for s in entity_sets
        ]
    if not entity_sets and not entity_types:
        msg = "El $metadata no declara entidades."
        raise SapResponseError(msg)
    return ServiceMetadata(
        odata_version=odata_version,
        namespaces=tuple(s.get("Namespace", "") for s in schemas),
        entity_types=tuple(entity_types),
        entity_sets=tuple(entity_sets),
    )


def _apply_labels(et: EdmEntityType, labels: dict[str, str]) -> EdmEntityType:
    if not labels:
        return et
    props = tuple(
        p
        if p.label
        else EdmProperty(
            **{**p.__dict__, "label": labels.get(f"{et.qualified_name}/{p.name}")},
        )
        for p in et.properties
    )
    return EdmEntityType(name=et.name, namespace=et.namespace, keys=et.keys, properties=props)
