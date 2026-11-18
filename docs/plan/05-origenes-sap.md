# 05 · Orígenes SAP (extractores y CDS)

**Iteración:** 3 · **Depende de:** 04 · **Bloquea a:** 06, 08

## Objetivo

Dar de alta, de forma guiada, los **orígenes de datos SAP** que se van a extraer:

| Tipo de origen | Ejemplos | Cómo llega a la plataforma | Delta |
|---|---|---|---|
| `ODP_EXTRACTOR` | DataSources estándar (`0FI_GL_14`, `2LIS_11_VAITM`, `0MATERIAL_ATTR`…) y DataSources **Z** | Servicio OData generado en SEGW con *Redefine → ODP Extraction*, contexto **SAPI** | Sí, nativo ODQ (según el método de delta del extractor) |
| `CDS_ODP` | Vistas CDS estándar liberadas para extracción o CDS **Z** con `@Analytics.dataExtraction.enabled: true` | Servicio OData SEGW ODP, contexto **ABAP_CDS** | Sí, si la CDS declara delta (CDC o por elemento) |
| `CDS_ODATA` | CDS publicada con `@OData.publish: true` o *service binding* (RAP, S/4HANA) | Servicio OData estándar | No nativo → **pseudo-delta** por campo de fecha/hora de modificación, o solo *full* |

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `source` | `id`, `sap_system_id`, `code`, `name`, `description`, `source_type`, `odp_context` (`SAPI` \| `ABAP_CDS`), `odp_name` (DataSource o CDS), `service_name`, `service_version`, `entity_set`, `odata_version`, `delta_method` (`ODQ` \| `WATERMARK` \| `NONE`), `watermark_field`, `watermark_overlap_seconds`, `key_fields[]`, `default_filters` (JSON), `page_size`, `tags`, `status` (`draft` \| `active` \| `deprecated`) |
| `source_schema_version` | `source_id`, `version`, `columns` (JSON: nombre, tipo EDM, longitud, precisión, escala, nullable, es_clave, etiqueta SAP), `detected_at`, `hash` |

> Las credenciales y URL vienen del `sap_system`; el origen solo referencia servicio + entidad.

## 2. Asistente "Nuevo origen"

1. **Sistema SAP**: seleccionar una conexión activa.
2. **Tipo**: Extractor ODP · CDS vía ODP · CDS vía OData.
3. **Servicio y entidad**: buscador sobre el catálogo (filtrado por tipo); muestra descripción y si soporta delta.
4. **Esquema**: tabla de columnas leída de `$metadata` (nombre técnico, etiqueta SAP, tipo, clave). El usuario puede:
   - Marcar/confirmar **campos clave** (necesarios para `MERGE` en destino).
   - Excluir columnas.
   - En `CDS_ODATA`: elegir el **campo marca de agua** para pseudo-delta y el solapamiento.
5. **Filtros por defecto** (opcional): constructor visual de `$filter` sobre campos filtrables (p. ej. sociedad, ejercicio). Aviso: en orígenes ODP, los filtros de una inicialización de delta quedan fijos para esa suscripción.
6. **Vista previa**: primeras 100 filas (`$top=100`) **sin** `Prefer: odata.track-changes`, para no crear suscripción en la ODQ. Muestra recuento estimado si el servicio admite `$count`.
7. **Resumen y guardar**.

## 3. Gestión del esquema

- **Instantánea versionada**: cada lectura de `$metadata` calcula un hash; si cambia, se crea `source_schema_version` nueva.
- Botón **"Revisar cambios de esquema"** y comprobación automática antes de cada ejecución (configurable):
  - Columna nueva → se añade al destino si el pipeline tiene `schema_evolution = add_columns` (por defecto) o se ignora.
  - Columna eliminada / cambio de tipo incompatible → la ejecución se detiene con error `SX-SRC-SCHEMA-BREAKING` y alerta.
- Diff visual entre versiones de esquema.

## 4. Componentes técnicos

| Componente | Responsabilidad |
|---|---|
| `MetadataParser` | EDMX V2/V4 → `EntitySchema` interno (tipos EDM, `sap:label`, `sap:filterable`, claves) |
| `SourceCatalogService` | Clasifica servicios ODP/CDS y detecta capacidad de delta (presencia de `DeltaLinksOf…`, anotaciones de *change tracking*) |
| `SchemaRegistry` | Versionado y comparación de esquemas |
| `PreviewService` | Lectura acotada sin suscripción, con timeout corto |
| `SourceConnector` (puerto) | Interfaz implementada en el módulo 06: `read_full()`, `read_delta_init()`, `read_delta(token)`, `read_watermark(desde, hasta)` |

## 5. Lado SAP: guía de publicación

Esta guía se entrega al equipo SAP y se mostrará también como ayuda contextual en la plataforma.

### 5.1 Extractores (DataSources estándar y Z)

1. **Activar el DataSource**: estándar en `RSA5` → `RSA6`; Z ya creado en `RSO2` (o equivalente).
2. **Liberar para ODP**:
   - Estándar: comprobar que está liberado para ODP (nota SAP 2232584 y relacionadas).
   - Z: exponer con el programa `RODPS_OS_EXPOSE`.
3. **Extractores LO (2LIS_\*)**: llenar *setup tables* antes de la inicialización de delta y coordinar la ventana sin contabilizaciones con negocio.
4. **SEGW**: crear proyecto → *Data Model → Redefine → ODP Extraction* → contexto **SAPI** → seleccionar el DataSource → generar objetos de *runtime*.
5. **Registrar el servicio** en `/IWFND/MAINT_SERVICE` y asignar al rol del usuario técnico (`S_SERVICE`).
6. Monitorizar suscripciones en **ODQMON** (el suscriptor aparecerá como el servicio OData).

### 5.2 CDS con delta vía ODP

1. CDS con `@Analytics.dataExtraction.enabled: true`.
2. Delta, según versión de S/4HANA:
   - **CDC**: `@Analytics.dataExtraction.delta.changeDataCapture.automatic: true` (o `mapping` para vistas con *joins*).
   - **Por elemento**: `@Analytics.dataExtraction.delta.byElement.name: '<campo fecha/hora>'` (+ `maxDelayInSeconds`).
3. SEGW → *Redefine → ODP Extraction* → contexto **ABAP_CDS** → seleccionar la CDS → generar → registrar en `/IWFND/MAINT_SERVICE`.

### 5.3 CDS publicada directamente como OData

- `@OData.publish: true` (ECC/S/4 con Gateway) o *service definition + service binding* (RAP).
- Debe incluir un **campo de última modificación** confiable si se quiere pseudo-delta, y exponer las claves.
- Para volúmenes grandes es preferible la vía ODP (5.2).

### 5.4 Nomenclatura de objetos Z en SAP (estándar AG)

| Objeto | Propuesta | Estado en estándar AG |
|---|---|---|
| Proyecto SEGW / servicio OData | `ZMM_WS_<DESC>` (máx. 30; el servicio generado añade `_SRV`) | Se asimila a "Web Service"; **confirmar** |
| Vista CDS | `ZMM_CV_<DESC>` (máx. 30) | **No definido** en la matriz AG; propuesta a confirmar |
| DataSource Z | Convención propia de BW/BC (`Z…`) | Fuera de la matriz; seguir la práctica actual del equipo |
| Orden de transporte | Según `orden-transporte` del estándar AG | Definido |

`MM` = módulo SAP de los datos (`FI`, `SD`, `MM`…) o `BC` para objetos transversales de la plataforma.

## 6. API (resumen)

```
GET    /api/v1/sources?system=&type=&status=&search=
POST   /api/v1/sources
GET    /api/v1/sources/{id}          PATCH /api/v1/sources/{id}     DELETE /api/v1/sources/{id}
POST   /api/v1/sources/inspect       # servicio+entidad → esquema y capacidades (asistente)
POST   /api/v1/sources/{id}/preview
POST   /api/v1/sources/{id}/schema/refresh
GET    /api/v1/sources/{id}/schema/versions
GET    /api/v1/sources/{id}/schema/diff?from=&to=
```

## 7. Pantallas

1. **Orígenes**: tabla con sistema, tipo (icono extractor/CDS), delta (✔/pseudo/✖), nº de pipelines, estado.
2. **Asistente de alta** (pasos de la sección 2).
3. **Detalle**: pestañas *General · Esquema (con versiones y diff) · Vista previa · Pipelines · Actividad*.
4. **Ayuda "Cómo publicar en SAP"**: la guía de la sección 5 integrada.

## Tareas

- [ ] Modelo `source` + `source_schema_version`.
- [ ] `MetadataParser` V2 (prioridad) y V4.
- [ ] Clasificación ODP/CDS y detección de delta.
- [ ] Asistente backend (`inspect`) y vista previa sin suscripción.
- [ ] Versionado y diff de esquema.
- [ ] Pantallas 1–4.
- [ ] Guía SAP revisada con el equipo SAP de AG (y nomenclatura confirmada).
- [ ] Pruebas con fixtures del spike (extractor estándar, Z y CDS).

## Criterios de aceptación

- Un usuario crea un origen de cada tipo desde el catálogo sin escribir URLs a mano.
- La vista previa no deja suscripciones en ODQMON.
- Un cambio de esquema en SAP se detecta y se muestra como diff.
