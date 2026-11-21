# ADR 0006 · Contratos del motor: esquema, lote, spool, run y puertos

**Estado:** Propuesto — pendiente de revisión por los dos desarrolladores · **Fecha:** 20.11.2026

## Contexto

En la Fase 2 (plan 13 §5) el motor se construye entre dos personas, partido por el spool. Dev A extrae de SAP hasta el spool (It5, extracción) y Dev B carga desde el spool en el destino (It5, carga). Antes, en la Fase 1, Dev A hace los orígenes (It3) y Dev B los destinos CSV y Snowflake (It4a).

Para trabajar en paralelo sin bloquearse, lo que cruza esa frontera tiene que estar fijado antes:

- cómo se describe un esquema;
- qué identifica un lote;
- qué hay en el spool y cómo se sabe que es válido;
- qué es un run y un intento;
- qué interfaces implementan conectores y destinos;
- qué deja escrito el destino para no cargar dos veces.

Este ADR fija esos contratos (cambios C-03, C-04, C-06 y C-12 del plan 13). **No** fija el estado de delta (`delta_state`, transiciones y cierre de la suscripción en SAP): sigue como borrador en el plan 06 §3.6 y depende del spike (H6, H10, H11, H12 y H18). Se cerrará en un ADR posterior. Todo lo de aquí vale sea cual sea el resultado del spike.

## Decisión

### 1. Dónde viven los contratos

Paquete nuevo `backend/src/sapextract/dataflow/`: un núcleo compartido que no pertenece a ningún módulo. Solo tiene `domain/`, sin acceso a la base de datos ni a la red. Admite `pyarrow`, porque los lotes son Arrow.

| Archivo | Contenido |
|---|---|
| `dataflow/domain/schema.py` | `EntitySchema`, `Column`, columnas técnicas, hash de esquema |
| `dataflow/domain/types.py` | Mapeo EDM → Arrow (fuente única de la tabla del plan 07 §6) |
| `dataflow/domain/batch.py` | `RunMode`, `LoadMode`, `BatchInfo` y cálculo de `batch_id` |
| `dataflow/domain/spool.py` | `SpoolManifest`, `SpoolFile`, protocolo `Spool` |
| `dataflow/domain/ports.py` | `SourceConnector`, `ReadResult`, `DestinationWriter` y sus tipos |
| `dataflow/domain/errors.py` | `DataflowError` y `ErrorKind` |

`sources/`, `extraction/` y `destinations/` dependen de `dataflow/`; `dataflow/` no depende de ninguno de ellos. Cambiar un archivo de `dataflow/` exige un PR revisado por los dos desarrolladores.

### 2. Esquema de entidad

```python
@dataclass(frozen=True)
class Column:
    name: str                      # nombre técnico en SAP (p. ej. BELNR); en el spool no se renombra
    edm_type: str                  # Edm.String, Edm.Decimal…
    arrow_type: pa.DataType        # según dataflow/domain/types.py
    nullable: bool
    is_key: bool
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    label: str | None = None       # sap:label
    display_format: str | None = None   # "Date" → date32

@dataclass(frozen=True)
class EntitySchema:
    columns: tuple[Column, ...]    # en el orden del $metadata, sin columnas técnicas
    keys: tuple[str, ...]
    def arrow_schema(self) -> pa.Schema: ...   # columnas de origen + técnicas (§3)
    def schema_hash(self) -> str: ...          # sha256 del JSON canónico (nombre, tipo EDM, longitudes, clave)
```

- Se construye a partir del `ServiceMetadata` que ya produce el parser de `$metadata` (`sap_connections/infrastructure/metadata_parser.py`). Lo que versiona el plan 05 (`source_schema_version.hash`) es `schema_hash()`.
- **Pendiente de acordar (20.11.2026):** `schema_hash()` no incluye `display_format`, pero ese campo cambia el tipo Arrow (`date32` o `timestamp`). Si SAP cambia solo el formato, el hash no cambia y un spool antiguo seguiría pareciendo válido. Los orígenes (It3) lo compensan: crean versión nueva si cambia el hash **o** si el diff de columnas detecta el cambio. Propuesta: añadir `display_format` al hash (cambio en `dataflow/`, revisión de los dos).
- Los campos ODQ (`ODQ_CHANGEMODE`, `ODQ_ENTITYCNTR`) son columnas de origen normales: se conservan tal cual (en `append` sobre orígenes aditivos son necesarias, plan 06 §3.4).
- El renombrado y la exclusión de columnas (`column_mapping`, plan 07 §2) **no** se aplican en el spool: los aplica el destino al crear la tabla y al cargar. Así el spool es igual para cualquier destino.

### 3. Columnas técnicas

Van en cada fila del spool, con estos tipos Arrow. El conector produce `_sx_extracted_at`, `_sx_change_mode` y `_sx_record_seq`; el paso `extract` del motor añade `_sx_batch_id` y `_sx_run_id` al escribir el spool.

| Columna | Tipo | Valor |
|---|---|---|
| `_sx_batch_id` | `string` | `batch_id` del lote (§4) |
| `_sx_run_id` | `string` | Run que extrajo (informativo) |
| `_sx_extracted_at` | `timestamp[us, UTC]` | Momento de lectura de la página |
| `_sx_change_mode` | `string` (nulo) | `C`, `U` o `D` a partir de `ODQ_CHANGEMODE` (vacío en una init se guarda como `C`); nulo en `FULL` y `WATERMARK` |
| `_sx_record_seq` | `int64` | Orden de llegada dentro del lote, desde 1, sin huecos. Decide el último registro por clave en `upsert` |

`_sx_deleted` no está en el spool: es una columna del destino que el `apply` calcula a partir de `_sx_change_mode = 'D'` y de `delete_handling` (plan 07 §2).

### 4. Identidad del lote

`batch_id` es la clave de idempotencia en el destino y en el spool. Su formato es `<tipo>-<24 hex>`: el tipo es una letra y los 24 hex (96 bits) son el prefijo del SHA-256 de una tupla canónica unida por `|`.

| Modo del run | Tipo | Tupla | Por qué |
|---|---|---|---|
| `DELTA` | `d` | `pipeline_id`, `generation`, `start_token` | Repetir el delta con el mismo token de partida produce el mismo lote, aunque SAP devuelva más filas la segunda vez (H11) |
| `WATERMARK` | `w` | `pipeline_id`, `window_start`, `window_end` (ISO 8601) | La misma ventana es el mismo lote |
| `DELTA_INIT` | `i` | `pipeline_id`, `generation`, `run_id` | Una init es única por generación |
| `FULL` | `f` | `pipeline_id`, `run_id` | Cada carga completa es un lote propio |

- El run fija su `batch_id` en el **primer** intento y lo conserva en todos los siguientes (guarda `start_token` y `delta_generation` o la ventana).
- Solo contiene `[a-z0-9-]`: es seguro en nombres de archivo, carpetas y literales SQL.
- Implementación única en `dataflow/domain/batch.py`, con pruebas de los cuatro casos.

### 5. Run, intento y cola

Una sola máquina de estados (plan 06 §2). La cola son los runs en `queued`; no hay tabla `job`.

| Tabla | Campos que fija este ADR |
|---|---|
| `run` | `id`, `pipeline_id`, `mode` (`FULL` \| `DELTA_INIT` \| `DELTA` \| `WATERMARK`), `status` (`queued` \| `running` \| `succeeded` \| `failed` \| `cancelled`), `batch_id`, `delta_generation`, `start_token`, `window_start`, `window_end`, `lease_epoch`, `claimed_by`, `heartbeat_at`, `available_at`, `priority`, `trigger`, `created_at`, `finished_at` |
| `run_attempt` | `run_id`, `number`, `worker_id`, `lease_epoch`, `started_at`, `finished_at`, `outcome`, `error_code`, `error_kind`, `spool_worker_id` (worker que tiene un spool válido de este lote, o nulo) |
| `run_step` | `run_id`, `attempt`, `step` (`prepare` \| `extract` \| `load` \| `commit_delta` \| `cleanup`), `started_at`, `finished_at`, `status`, `metrics` (JSON) |

`run_attempt.outcome`:

| Valor | Significado |
|---|---|
| `succeeded` | El intento confirmó el run |
| `failed_transient` | Falló con un error transitorio y el run vuelve a `queued` si quedan intentos |
| `failed` | Falló sin reintento (configuración, datos o delta) o se agotaron los intentos |
| `stale` | Sin latido; lo cierra el scheduler |
| `fenced` | Perdió el derecho a confirmar (su `lease_epoch` ya no es el vigente) |
| `interrupted` | Apagado ordenado del worker (SIGTERM); el run vuelve a `queued` sin gastar un intento |
| `cancelled` | Cancelado por el usuario |

**Arrendamiento (*lease*) y *fencing*.** Al tomar el run, el worker incrementa `run.lease_epoch` y crea el intento con ese valor. Cada latido y cada escritura del worker sobre el run, sus pasos o el estado del pipeline llevan `AND lease_epoch = :epoch`. Si no afectan a ninguna fila, el worker deja de trabajar y cierra su intento como `fenced`. La toma con `FOR UPDATE SKIP LOCKED` y los límites por pipeline, sistema SAP y grupo son los del plan 08 §3. La parte del arrendamiento que toca `delta_state` (plan 06 §3.6) sigue la misma regla y se cerrará con el spike.

### 6. Spool

**Ubicación.** El spool de un lote vive en `<SX_SPOOL_DIR>/<batch_id>/`, en el disco del worker que lo extrajo. Un intento posterior en el mismo worker lo reutiliza; en otro worker, se vuelve a extraer (plan 06 §2).

**Contenido.**

```
<SX_SPOOL_DIR>/<batch_id>/
  part-00001.parquet
  part-00002.parquet
  …
  _spool.json        ← se escribe al final; sin él, el spool no es válido
```

- **Parquet:** esquema `EntitySchema.arrow_schema()` (columnas de origen + técnicas), compresión `zstd`, como máximo 1 M de filas por archivo y grupos de filas de unas 128 K filas.
- Cada archivo se escribe como `*.tmp`, se sincroniza a disco y se renombra; `_spool.json` igual, el último.
- Permisos solo para el usuario del servicio, borrado en `cleanup`, retención máxima de 72 h y fuera de los backups (plan 06 §2, plan 11).

**Manifiesto `_spool.json` (versión 1).**

```json
{
  "version": 1,
  "batch_id": "d-3f9a…",
  "pipeline_id": "…",
  "run_id": "…",
  "mode": "DELTA",
  "start_token": "D000000012345",
  "next_token": "D000000012399",
  "window_start": null,
  "window_end": null,
  "schema_hash": "…",
  "schema": [ { "name": "BELNR", "edm_type": "Edm.String", "arrow_type": "string", "nullable": false, "is_key": true } ],
  "files": [ { "name": "part-00001.parquet", "rows": 1000000, "bytes": 48213344, "sha256": "…" } ],
  "rows": 1203411,
  "worker_id": "…",
  "created_at": "2026-11-20T10:15:00Z"
}
```

`next_token` es el token que devolvió SAP al agotar las páginas; queda pendiente de confirmar hasta `commit_delta`. Un spool es **válido** si existe `_spool.json`, su `batch_id` es el del run, su `schema_hash` coincide con el esquema vigente y cada archivo existe con su tamaño. El SHA-256 se comprueba al cargar.

### 7. Puertos

```python
class ErrorKind(StrEnum):
    TRANSIENT = "transient"          # se reintenta como intento nuevo
    CONFIGURATION = "configuration"  # 401/403, permisos, esquema incompatible
    DATA = "data"                    # conversión imposible, clave nula en upsert
    DELTA = "delta"                  # token rechazado → BROKEN

class DataflowError(Exception):
    kind: ErrorKind
    code: str                        # SX-SAP-*, SX-DST-*, SX-SRC-*


@dataclass(frozen=True)
class ReadContext:
    run_id: UUID
    batch_id: str
    page_size: int
    page_timeout_seconds: int
    is_cancelled: Callable[[], bool]           # se consulta entre páginas
    on_page: Callable[[PageMetrics], None]     # métricas por página (filas, bytes, latencia)


class ReadResult(Protocol):
    def batches(self) -> Iterator[pa.RecordBatch]: ...   # con columnas técnicas, salvo _sx_batch_id/_sx_run_id
    def next_token(self) -> str | None: ...              # disponible al agotar batches; None en FULL/WATERMARK


class SourceConnector(Protocol):
    def schema(self) -> EntitySchema: ...
    def read_full(self, filters: Filters, ctx: ReadContext) -> ReadResult: ...
    def read_delta_init(self, filters: Filters, ctx: ReadContext) -> ReadResult: ...
    def read_delta(self, token: str, ctx: ReadContext) -> ReadResult: ...
    def read_window(self, window: Window, ctx: ReadContext) -> ReadResult: ...
    def read_sample(self, filters: Filters, ctx: ReadContext, *, rows: int) -> ReadResult: ...


class Spool(Protocol):
    @property
    def manifest(self) -> SpoolManifest: ...
    def parquet_files(self) -> Sequence[Path]: ...
    def batches(self) -> Iterator[pa.RecordBatch]: ...


class DestinationWriter(Protocol):
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> PreparedTarget: ...
    def applied_batch(self, target: PreparedTarget, batch_id: str) -> AppliedBatch | None: ...
    def stage(self, target: PreparedTarget, spool: Spool) -> StageInfo: ...
    def apply(self, stage: StageInfo, batch: BatchInfo) -> LoadResult: ...
    def cleanup(self, stage: StageInfo) -> None: ...
    def drop_test_target(self, target: PreparedTarget) -> None: ...
```

Reglas de cada método:

| Método | Regla |
|---|---|
| `SourceConnector.read_*` | No escribe nada en la plataforma. Lanza `DataflowError` con su `ErrorKind`. Las peticiones de init y delta van sin reintento HTTP (`NO_RETRY`, plan 06 §3.2). Comprueba `is_cancelled()` entre páginas |
| `DestinationWriter.prepare` | Todo el DDL (crear tabla, añadir columnas según `schema_evolution`, crear `__sx_load_state`). Nunca dentro de la transacción de `apply`. Idempotente |
| `applied_batch` | Lee la tabla de control (§8). Si devuelve un resultado, el motor salta `stage` y `apply` y confirma con el `next_token` guardado allí |
| `stage` | Lleva el spool al destino sin tocar la tabla final (Snowflake: `PUT` de los Parquet tal cual + `COPY` a staging; CSV: escribe `*.tmp`). Puede repetirse |
| `apply` | **Una transacción**: aplica `append` / `upsert` / `replace` (plan 06 §4, plan 07) y escribe la fila de la tabla de control. O todo o nada |
| `cleanup` | Borra el staging. Idempotente; un fallo aquí no falla el run |
| `SourceConnector.read_sample` | Solo para «Probar pipeline» (plan 08). Hasta `rows` filas con `$top`, sin `Prefer: odata.track-changes`: nunca abre suscripción ni devuelve token |
| `drop_test_target` | Solo para «Probar pipeline». Borra un destino de prueba (`SXTEST_<8 hex>_<nombre>`) y sus filas de control. Idempotente. Con cualquier otro nombre lanza `SX-DST-NOT-TEST-TARGET` sin tocar nada |

Los valores de `LoadOptions` son los del plan 07 §2 (`load_mode`, `target_name`, `schema_evolution`, `delete_handling`, `technical_columns`, `column_mapping`). `BatchInfo` lleva `pipeline_id`, `run_id`, `batch_id`, `mode`, `next_token`, `window_end` y `schema_hash`.

### 8. Tabla de control en el destino

`__sx_load_state`, en el esquema del destino (Snowflake y SQL Server), con clave (`pipeline_id`, `batch_id`):

| Columna | Contenido |
|---|---|
| `pipeline_id`, `batch_id` | Identidad del lote |
| `run_id`, `mode` | Quién y cómo lo cargó |
| `next_token`, `window_end` | Lo que el motor debe confirmar si se cayó antes de `commit_delta` |
| `rows_inserted`, `rows_updated`, `rows_deleted` | Resultado del `apply` |
| `schema_hash` | Esquema con el que se cargó |
| `applied_at` | Momento de la confirmación en el destino |

En CSV, el papel de esta tabla lo cumplen `_manifest.json` y `_SUCCESS` en la carpeta del lote (plan 07 §4), con los mismos campos.

### 9. Pruebas compartidas y dobles de prueba

Para que cada uno avance sin el otro:

- **Batería de conformidad** en `backend/tests/contract/`, que todo `DestinationWriter` debe pasar:
  - aplicar dos veces el mismo lote no duplica;
  - `applied_batch` lo ve tras `apply`;
  - un fallo inyectado dentro de `apply` no deja ni filas ni fila de control;
  - `replace` no deja el destino vacío a mitad de carga.

  Y todo `SourceConnector`:
  - el esquema coincide con el `$metadata`;
  - las columnas técnicas son correctas;
  - `_sx_record_seq` no tiene huecos;
  - respeta la cancelación;
  - `next_token` solo está disponible al final.
- **Dobles:** `InMemoryWriter` (destino en memoria que cumple la batería) para que Dev A pruebe la extracción, y un `Spool` de muestra generado desde el mock SAP para que Dev B pruebe la carga.
- La batería se escribe al empezar la Fase 1, junto a los dobles, antes que los conectores y destinos reales.

## Notas de implementación (It4a, 20.11.2026)

Lo construido en la It4a sigue este ADR con estas diferencias, que la revisión conjunta debe confirmar:

- **`dataflow/infrastructure/parquet_spool.py`.** El ADR dejaba en `dataflow/` solo `domain/`. La lectura y escritura del spool (`SpoolWriter`, `DirectorySpool`) es igual para la extracción y para la carga, así que vive aquí y no se duplica en `extraction/` y `destinations/`. No accede a la base de datos ni a la red; solo al disco del spool.
- **Escritura atómica en Windows.** Para sincronizar el archivo antes de renombrarlo se abre en modo `r+b` (Windows no permite `fsync` sobre un descriptor de solo lectura).
- **`_sx_deleted` en CSV.** Se escribe como columna del archivo (`true`/`false`) igual que en Snowflake.
- **Dobles de prueba.** `tests/support/dataflow.py` (`GL_SCHEMA`, `make_spool`, `InMemoryWriter`) y la batería `tests/contract/destination_contract.py`, que pasan memoria, CSV y CSV gzip; Snowflake la pasa con la cuenta de pruebas.

## Notas de implementación (It6, 21.11.2026)

La orquestación se construyó antes que el motor (plan 13 §5.4). Diferencias con el §5 que la revisión conjunta debe confirmar:

- **`run` y `run_attempt` los crea la It6**, no la It5 (migración `0008_orchestration`). `run_step` sigue siendo de la It5.
- **`run_attempt.error_message`** y, en `run`, `attempts_budget`, `cancel_requested_at/by`, `schedule_id`, `reason`, `triggered_by`, `claimed_at`, `started_at` y el último error. No cambian los campos que fijó este ADR.
- **`lease_epoch` sube también cuando el scheduler recupera un intento sin latido**, no solo al tomar el run. El worker anterior queda fuera desde ese momento, sin esperar a que otro tome el run.
- **Un solo run vivo por pipeline** (en cola o en curso), por índice único parcial.
- **El motor se conecta por `RunExecutor`** (`orchestration/domain/ports.py`), que recibe el `lease_epoch` del intento en `RunContext`. Es una interfaz nueva entre los dos desarrolladores: cambiarla pide la misma revisión conjunta que `dataflow/`.
- `worker_id` y `spool_worker_id` son UUID. El identificador del worker cambia en cada arranque, así que un spool no se reconoce tras reiniciar el proceso aunque siga en el mismo disco: si la It5 quiere reutilizarlo, tendrá que identificar el disco y no el proceso.

## Notas de implementación (It5, 21.11.2026)

El motor se construyó contra el mock y sobre la orquestación de la It6 (detalle en el plan 06, «Lo construido en la iteración 5»). Diferencias que la revisión conjunta debe confirmar:

- **`run`** añade `next_token` y `rows_extracted/inserted/updated/deleted`; **`run_attempt`** añade `sap_requested` (el intento llegó a pedir datos a SAP). Migración `0009_engine`, que crea además `run_step`, `run_log`, `delta_state` y `delta_state_history`.
- **`run_step`** usa `skipped` para `extract` y `load` cuando la tabla de control del destino ya tiene el lote.
- **Estado de delta**: se implementa el borrador del plan 06 §3.6 con opciones conservadoras; el ADR posterior al spike lo fijará. `delta_state.lease_epoch` copia el del run al empezar el intento.
- **Puerto nuevo `EngineHooks`** entre orquestación y motor (`orchestration/domain/ports.py`), junto a `RunExecutor`: misma regla de revisión conjunta.
- **`SourceConnector`**: implementado por `ODataSourceConnector`, que además expone `server_now()` (hora de SAP para la ventana de marca de agua). Los filtros llegan como `{"$filter": …}`.

## Notas de implementación (cierre de la It6, 21.11.2026)

- Los puertos ganan `SourceConnector.read_sample` y `DestinationWriter.drop_test_target` para la ejecución de prueba de un pipeline (plan 08, «Probar pipeline»). El resto del contrato no cambia; la batería de contrato de destinos comprueba que un destino de prueba se borra y que uno real se rechaza.

## Fuera de este ADR (pendiente del spike)

| Tema | Dónde | Depende de |
|---|---|---|
| Tabla `delta_state`, transiciones, *fencing* sobre el estado y `commit_delta` | Plan 06 §3.6 (borrador) | H6, H10, H11, H18 |
| Cierre de la suscripción en SAP (`terminate_delta` en el conector) | Plan 06 §3.5 | H12 |
| Modo `DELTA_INIT_NO_DATA` | Plan 06 §1 | H9 |
| Reintento automático de una init | Plan 06 §3.6 | H10 |
| Estrategia de cargas iniciales grandes | Plan 06 §6 | H8–H10 |
| Código real de token inválido (`SX-SAP-DELTA-INVALID`) | Conector ODP | H7 |

Cuando el spike cierre esas hipótesis, un ADR nuevo fijará el estado de delta. Si algún resultado obliga a cambiar algo de este, ese ADR lo reemplazará en la parte afectada.

## Alternativas descartadas

- **Contratos dentro de `extraction/`**: los destinos dependerían del módulo del motor, y los dos desarrolladores tocarían el mismo paquete a diario.
- **`DestinationWriter` que recibe lotes Arrow en lugar del spool**: obligaría a Snowflake a reescribir el Parquet que el spool ya tiene (C-12).
- **`batch_id` igual al `run_id`**: un reintento en otro worker o una repetición del delta no serían idempotentes (C-03).
- **Spool en un volumen compartido**: no hace falta infraestructura nueva mientras la repetición en SAP sea posible. Queda como alternativa si medir la repetición la muestra cara.
- **Aplicar `column_mapping` en el spool**: el spool dependería del destino y no se podría reutilizar ni inspeccionar con los nombres de SAP.

## Consecuencias

- Dev A (orígenes, extracción) y Dev B (destinos, carga) trabajan en paralelo contra estos tipos y los dobles de prueba; la integración real se hace en la It5 con la batería de conformidad ya en verde.
- La primera tarea de la Fase 1, de los dos juntos, es crear `dataflow/` con estos tipos, el cálculo de `batch_id`, el formato del manifiesto y los dobles, en un PR corto revisado por ambos.
- Las tablas `run`, `run_attempt` y `run_step` se crean en la It5 con los campos de §5; `delta_state` espera al ADR posterior al spike.
