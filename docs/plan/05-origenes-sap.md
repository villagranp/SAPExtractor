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
| `source` | `id`, `sap_system_id`, `code`, `name`, `description`, `source_type`, `odp_context` (`SAPI` \| `ABAP_CDS`), `odp_name` (DataSource o CDS), `service_name`, `service_version`, `entity_set`, `odata_version`, `delta_method` (`ODQ` \| `WATERMARK` \| `NONE`), `delta_semantics` (`after_image` \| `additive` \| `before_after` \| `unknown`), `watermark_field`, `watermark_kind` (`datetime` \| `date` \| `decimal_timestamp`), `watermark_timezone`, `watermark_overlap_seconds`, `key_fields[]`, `default_filters` (JSON), `page_size`, `tags`, `status` (`draft` \| `active` \| `deprecated`) |
| `source_schema_version` | `source_id`, `version`, `columns` (JSON: nombre, tipo EDM, longitud, precisión, escala, nullable, es_clave, etiqueta SAP), `detected_at`, `hash` |

> Las credenciales y URL vienen del `sap_system`; el origen solo referencia servicio + entidad.

## 2. Asistente "Nuevo origen"

1. **Sistema SAP**: seleccionar una conexión activa.
2. **Tipo**: Extractor ODP · CDS vía ODP · CDS vía OData.
3. **Servicio y entidad**: buscador sobre el catálogo (filtrado por tipo); muestra descripción y si soporta delta.
4. **Esquema**: tabla de columnas leída de `$metadata` (nombre técnico, etiqueta SAP, tipo, clave). El usuario puede:
   - Marcar/confirmar **campos clave** (necesarios para `MERGE` en destino).
   - Excluir columnas.
   - En `CDS_ODATA`: elegir el **campo marca de agua** para pseudo-delta, su tipo (fecha-hora, solo fecha o *timestamp* decimal), su zona horaria y el solapamiento.
   - En orígenes ODP: declarar la **semántica del delta** (imagen posterior, aditivo, imagen anterior y posterior, o desconocida). El `$metadata` no la expone, así que no se puede detectar: el asistente ayuda con una tabla de extractores estándar frecuentes y con la guía (RSA2). Con "desconocida" el pipeline solo admite `append`.
5. **Filtros por defecto** (opcional): constructor visual de `$filter` sobre campos filtrables (p. ej. sociedad, ejercicio). Aviso: en orígenes ODP, los filtros de una inicialización de delta quedan fijos para esa suscripción.
6. **Vista previa**: primeras 100 filas (`$top=100`) **sin** `Prefer: odata.track-changes`, para no crear suscripción en la ODQ. Muestra recuento estimado si el servicio admite `$count`. Pendiente del spike (H16): en un extractor grande, `$top` podría lanzar la extracción completa en SAP antes de devolver las 100 filas; si es así, la vista previa llevará un filtro obligatorio o se desactivará para ese origen.

   La vista previa lee con el usuario técnico, que tiene autorización amplia: quien tenga el permiso `source:preview` ve cualquier dato que ese usuario pueda leer. No hay marca de origen confidencial (decisión del 19.11.2026); ese permiso se concede solo a personas capacitadas.
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
| `SourceConnector` (puerto) | Interfaz implementada en el módulo 06: `read_full()`, `read_delta_init()`, `read_delta(token)`, `read_window(field, start, end)` (firma en el plan 06 §7) |

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
7. **Proceso de delta**: consultar en `RSA2` el proceso de delta del DataSource (imagen posterior, aditivo, imagen anterior y posterior) e informarlo al dar de alta el origen. De él depende que el destino se cargue con `upsert` o con `append`.

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

- [x] Modelo `source` + `source_schema_version`, con `delta_semantics` y `watermark_kind` (migración `0005_sources`).
- [x] Tabla de semántica de delta de los extractores estándar frecuentes.
- [x] Formularios con React Hook Form y Zod (primera pantalla que los adopta, plan 10 §2).
- [x] `MetadataParser` V2 (prioridad) y V4 (ya en It2, `sap_connections`).
- [x] Clasificación ODP/CDS y detección de delta (It2; el origen la reutiliza).
- [x] Asistente backend (`inspect`) y vista previa sin suscripción.
- [x] Versionado y diff de esquema.
- [x] Pantallas 1–4.
- [ ] Guía SAP revisada con el equipo SAP de AG (y nomenclatura confirmada).
- [ ] Pruebas con fixtures del spike (extractor estándar, Z y CDS): hoy con el mock; se cambian por las respuestas reales cuando se grabe el spike.

## Lo construido en la iteración 3

Código: `backend/src/sapextract/sources/`, `backend/src/sapextract/dataflow/domain/` (esquema compartido, ADR 0006) y `frontend/src/features/sources/`.

- **Esquema.** Se guarda el que declara SAP (`EntitySchema` de `dataflow`, con su `schema_hash()`) junto con qué campos admiten `$filter`. Las claves confirmadas van en `source.key_fields`: el esquema efectivo para el motor es el de SAP con esas claves (`effective_schema`). Un tipo EDM no previsto se lleva como texto (contrato común de `dataflow`); columnas repetidas o con nombre de columna técnica (`_sx_*`) impiden crear el origen. Como `schema_hash()` no incluye `display_format`, la relectura del esquema también crea versión si el diff detecta un cambio (ADR 0006 §2, pendiente de acordar).
- **Diferencias con el plan.**
  - La vista previa es posterior al alta: el asistente guarda el origen como **borrador** y la vista previa y la activación se hacen en el detalle. Así la lectura queda auditada contra un origen concreto (`source.previewed`).
  - **Excluir columnas** no está en el origen: el spool lleva todas las columnas de SAP y la exclusión es parte del mapeo del destino (ADR 0006 §2).
  - El tipo de origen se propone por el entity set (`EntityOf` → extractor, `FactsOf`/`AttrOf`/`TextOf` → CDS vía ODP, el resto → CDS vía OData). `HierOf` y `DeltaLinksOf` se rechazan.
  - Se añadió `preview_enabled` por origen (H16) y `service_path`, resuelto del catálogo al crear.
- **Delta.** Delta ODQ solo en orígenes ODP con *change tracking*; marca de agua solo en CDS vía OData. La semántica se declara con ayuda de los códigos de RSA2 (`ABR`, `ADD`, `AIE`, `AIMD`…) y de una sugerencia para extractores conocidos, que hay que confirmar en SAP. Las marcas de agua `Edm.DateTimeOffset` usan UTC; el resto exige la zona horaria del sistema (H15).
- **Filtros.** Condiciones campo-operador-valor guardadas como datos; el `$filter` se genera con los literales de OData V2 o V4 según el tipo EDM y se valida al guardar (texto con longitud máxima, enteros, decimales dentro de la precisión y la escala, fechas sin zona para `Edm.DateTime`).
- **Vista previa.** `$top=100` sin `Prefer: odata.track-changes`, siguiendo `__next` si Gateway pagina en el servidor, con un tiempo máximo de 30 s. `$count` solo en CDS vía OData: en ODP podría lanzar la extracción completa (H16). Exige un sistema SAP activo, igual que releer el esquema y activar.
- **Esquema versionado.** «Revisar cambios en SAP» relee el `$metadata`: si el hash cambia se crea una versión y se muestra el diff (columnas nuevas, eliminadas y modificadas; ensanchar texto o decimal es compatible, el resto rompe).
- **Ciclo de vida.** `draft → active → deprecated → active`, baja lógica que libera el código. Un origen no obsoleto impide dar de baja su sistema SAP. Cada cambio se audita.
- **Desde el explorador de It2.** «Crear origen» abre el asistente con el sistema, el servicio y el entity set ya elegidos.
- **Pruebas.** Dominio (`tests/unit/test_sources_domain.py`), integración contra el mock con PostgreSQL (`tests/integration/test_sources.py`, incluido que la vista previa no emite tokens de delta), Vitest de las pantallas y E2E del flujo explorador → asistente → vista previa → activación, con axe en las pantallas nuevas.

## Criterios de aceptación

- Un usuario crea un origen de cada tipo desde el catálogo sin escribir URLs a mano.
- La vista previa no deja suscripciones en ODQMON.
- Un cambio de esquema en SAP se detecta y se muestra como diff.
