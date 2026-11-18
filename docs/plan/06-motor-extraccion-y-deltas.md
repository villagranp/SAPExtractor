# 06 · Motor de extracción y gestión de deltas

**Iteración:** 5 · **Depende de:** 01 (spike), 04, 05, 07 · **Bloquea a:** 08, 09

## Objetivo

Ejecutar de forma **fiable, reanudable e idempotente** la lectura de un origen SAP y su carga en un destino, gestionando correctamente los **deltas** de la ODQ y el pseudo-delta por marca de agua.

> Este es el núcleo de la plataforma. Antes de implementarlo deben estar documentados los resultados del **Spike SAP** (ADR 0002).

## 1. Conceptos

| Concepto | Definición |
|---|---|
| **Pipeline** | Origen + destino + opciones (definido en el módulo 08) |
| **Run** (ejecución) | Una ejecución concreta de un pipeline |
| **Modo de extracción** | `FULL`, `DELTA_INIT` (con datos), `DELTA_INIT_NO_DATA`, `DELTA`, `DELTA_REPEAT`, `WATERMARK` |
| **Delta token** | Identificador que devuelve SAP (`__delta` / `!deltatoken`) para pedir los cambios desde la última petición |
| **Spool** | Lotes extraídos guardados temporalmente en disco (Parquet) entre la extracción y la carga |

## 2. Ciclo de vida de una ejecución

```
queued ──► running ──► succeeded
              │  ├──► failed ──(reintento)──► queued
              │  └──► cancelled
              └──► (worker caído, sin heartbeat) ──► failed (stale)
```

Pasos internos (`run_step`):

| Paso | Qué hace |
|---|---|
| `prepare` | Bloquea el pipeline (un solo run activo), carga estado de delta, valida esquema, prueba destino |
| `extract` | Lee página a página de SAP → convierte a Arrow → escribe spool Parquet. Guarda el **token pendiente** al final |
| `load` | Lee el spool → carga en *staging* del destino → aplica (`append` / `upsert` / `replace`) en una transacción |
| `commit_delta` | En la base de metadatos: `current_token ← pending_token`, marca de agua, run `succeeded` (misma transacción) |
| `cleanup` | Borra spool (o lo conserva N días si se configura), libera bloqueo, emite métricas y eventos |

Si falla `load`, el reintento **reutiliza el spool** sin volver a pedir a SAP (el token no ha avanzado).

## 3. Protocolo de delta (ODQ vía OData)

### 3.1 Estado (`delta_state`, uno por pipeline)

| Campo | Descripción |
|---|---|
| `status` | `NOT_INITIALIZED` \| `INITIALIZING` \| `INITIALIZED` \| `BROKEN` |
| `current_token` | Último token **confirmado** (carga exitosa) |
| `pending_token` | Token obtenido en un run que aún no se ha confirmado |
| `pending_run_id` | Run dueño del token pendiente |
| `init_filters_hash` | Filtros usados en la inicialización (si cambian, hay que reinicializar) |
| `initialized_at`, `last_delta_at` | Fechas |
| `watermark_value` | Para `WATERMARK` |
| `history` | Tabla `delta_state_history` con cada transición (auditada) |

### 3.2 Algoritmo

```
DELTA_INIT:
  GET <EntitySet>?$filter=...   + header Prefer: odata.track-changes
  → paginar con __next hasta el final
  → la última página trae __delta (token T1) → pending_token = T1
  → load OK → commit: current_token = T1, status = INITIALIZED

DELTA (token Tn):
  GET <EntitySet>?!deltatoken='Tn'
  → paginar → nuevo token Tn+1 → pending_token = Tn+1
  → load OK → commit: current_token = Tn+1
  → load KO → current_token sigue = Tn (spool conservado para reintento)

Reintento tras fallo en extract (sin spool válido):
  → se vuelve a pedir con Tn  ⇒ la ODQ debe devolver de nuevo el mismo delta (REPEAT)
     [comportamiento a confirmar en el Spike SAP]
```

Regla de oro: **el token confirmado solo avanza en la misma transacción que marca el run como exitoso, y solo después de que el destino confirmó la carga.**

### 3.3 Garantías y casos límite

| Caso | Tratamiento |
|---|---|
| Caída entre commit en destino y commit en metadatos | El siguiente run repite el delta Tn → la aplicación en destino es **idempotente** (ver 4) |
| Dos pipelines delta sobre el mismo origen y filtros | Bloqueado por validación (la suscripción ODQ se compartiría). Usar un pipeline con varios destinos (v1.1) o filtros distintos |
| Filtros cambiados tras la inicialización | El pipeline pasa a requerir **reinicialización** explícita |
| Token rechazado por SAP (suscripción borrada en ODQMON, caducada) | Error `SX-SAP-DELTA-INVALID`, `status = BROKEN`, alerta; el usuario decide reinicializar |
| Reinicializar | Acción con permiso `pipeline:reset_delta`, motivo obligatorio, confirmación doble; opción de vaciar el destino; se documenta cerrar la suscripción antigua en ODQMON |
| Run cancelado | Cancelación cooperativa (se comprueba entre páginas); el token no avanza |

### 3.4 Semántica de los registros delta ODP

Los servicios ODP añaden campos técnicos, entre ellos `ODQ_CHANGEMODE` (C/U/D) y `ODQ_ENTITYCNTR`. Según el **proceso de delta** del extractor:

| Tipo de delta del extractor | Ejemplo | Modo de aplicación recomendado |
|---|---|---|
| Imagen posterior (*after image*) | Muchos extractores maestros y CDS con CDC | `upsert` por clave; `D` → borrado lógico (`_sx_deleted = 1`) o físico (configurable) |
| Aditivo / imagen anterior + posterior (*before/after*, p. ej. 2LIS) | Extractores LO | `append` (histórico) conservando `ODQ_CHANGEMODE` y `ODQ_ENTITYCNTR`; la consolidación se hace aguas abajo |

El asistente de pipeline sugiere el modo según el origen y avisa si la combinación es peligrosa (p. ej. `upsert` sobre un extractor aditivo).

## 4. Idempotencia en destino

- Columnas técnicas añadidas a cada fila: `_sx_run_id`, `_sx_extracted_at`, `_sx_change_mode`, `_sx_record_seq`, `_sx_deleted`.
- `upsert`: `MERGE` por clave sobre una tabla de staging deduplicada (gana el último registro por clave según orden de llegada / `ODQ_ENTITYCNTR`).
- `append`: antes de insertar se eliminan las filas del mismo `_sx_run_id` lógico de delta (clave = token origen), de modo que repetir el delta no duplica.
- `replace` (full): carga en tabla/objeto temporal y **intercambio atómico** (rename/swap) para no dejar el destino vacío durante la carga.
- CSV: escritura a archivo temporal + renombrado atómico; nombre determinista por token → repetir sobrescribe el mismo archivo.

## 5. Pseudo-delta por marca de agua (`WATERMARK`)

Para `CDS_ODATA` sin delta nativo:

```
desde = watermark_value - overlap        (p. ej. 10 min)
hasta = ahora_SAP - safety_lag           (p. ej. 2 min, evita transacciones en curso)
GET <EntitySet>?$filter=<campo> ge datetime'desde' and <campo> lt datetime'hasta'&$orderby=<campo>
→ upsert por clave → watermark_value = hasta
```

Limitación documentada: no detecta borrados (se recomienda una reconciliación full periódica configurable, p. ej. semanal).

## 6. Lectura y conversión de datos

| Tema | Decisión |
|---|---|
| Paginación | Paginación de servidor (`__next` / `$skiptoken`) con `Prefer: odata.maxpagesize=<page_size>`; nunca `$skip` en delta |
| Memoria | *Parsing* incremental de JSON por página (p. ej. `ijson`), lotes Arrow de tamaño fijo, spool a Parquet: memoria acotada independiente del volumen |
| Tipos OData V2 | `/Date(ms)/` → timestamp UTC; `Edm.Decimal` (string) → decimal con precisión del `$metadata`; `Edm.Time` (`PT12H30M00S`) → time; fechas SAP vacías (`00000000`) → NULL |
| Textos | Se respetan ceros a la izquierda (NUMC como texto); recorte de espacios configurable |
| Paralelismo | Un run lee secuencialmente (el delta es secuencial); el paralelismo es **entre pipelines**, limitado por sistema SAP |
| Rendimiento | Métricas por página (latencia SAP, filas/seg); objetivo de referencia a fijar tras el spike |
| Timeouts | Timeout de lectura por página y timeout global por run (configurable por pipeline) |
| Heartbeat | El worker actualiza `run.heartbeat_at` cada 15 s; el scheduler marca como `failed (stale)` los runs sin latido y libera el bloqueo |

## 7. Interfaces (puertos) y plugins

```python
class SourceConnector(Protocol):
    def schema(self) -> EntitySchema: ...
    def read_full(self, filters: Filters) -> Iterator[pa.RecordBatch]: ...
    def read_delta_init(self, filters: Filters, with_data: bool) -> DeltaReadResult: ...
    def read_delta(self, token: DeltaToken) -> DeltaReadResult: ...
    def read_window(self, field: str, start: datetime, end: datetime) -> Iterator[pa.RecordBatch]: ...

class DeltaReadResult(Protocol):
    batches: Iterator[pa.RecordBatch]
    def next_token(self) -> DeltaToken: ...   # disponible al agotar batches

class DestinationWriter(Protocol):          # módulo 07
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> None: ...
    def stage(self, batches: Iterable[pa.RecordBatch]) -> StageInfo: ...
    def apply(self, stage: StageInfo, mode: LoadMode) -> LoadResult: ...   # transaccional
    def cleanup(self, stage: StageInfo) -> None: ...
```

Registro de plugins por tipo (`ODP_EXTRACTOR`, `CDS_ODP`, `CDS_ODATA` / `sqlserver`, `csv`, `snowflake`). El motor (`ExtractionEngine`) solo conoce las interfaces.

## 8. Errores y reintentos

| Clase | Ejemplos | ¿Reintento automático? |
|---|---|---|
| Transitorio | Timeout, 502/503/504, conexión cortada, *deadlock* en destino | Sí (política del pipeline) |
| Configuración | 401/403, tabla destino sin permisos, esquema incompatible | No → alerta |
| Datos | Conversión imposible, clave nula en `upsert` | No → alerta con muestra de filas (sin datos sensibles si se marca el origen como confidencial) |
| Delta | Token inválido | No → `BROKEN` |

## Tareas

- [ ] Modelo `run`, `run_step`, `delta_state`, `delta_state_history`.
- [ ] Conectores `ODP_EXTRACTOR` / `CDS_ODP` (comparten implementación ODP) y `CDS_ODATA`.
- [ ] Paginación incremental, conversión de tipos EDM → Arrow, spool Parquet.
- [ ] Máquina de estados de delta con commit en dos fases.
- [ ] Pseudo-delta por marca de agua.
- [ ] `ExtractionEngine` con pasos, cancelación, heartbeat y métricas.
- [ ] Acción de reinicialización de delta (API + auditoría).
- [ ] Pruebas con `sap-mock`: init, delta, repetición, token inválido, caída en cada paso (inyección de fallos).
- [ ] Prueba de volumen (≥ 10 M filas sintéticas) con memoria acotada.

## Criterios de aceptación

- Matar el worker en cualquier paso y relanzar no pierde ni duplica datos (prueba automatizada por cada paso).
- Un fallo de carga se reintenta sin volver a llamar a SAP.
- Un token inválido deja el pipeline en `BROKEN` con alerta y sin avanzar estado.
- El consumo de memoria de un run no crece con el número de filas.
