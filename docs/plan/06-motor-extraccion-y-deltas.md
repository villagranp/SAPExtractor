# 06 · Motor de extracción y gestión de deltas

**Iteración:** 5 · **Depende de:** 01 (spike), 04, 05, 07 · **Bloquea a:** 08, 09

> Actualizado el 19.11.2026 con los cambios C-02 a C-06, C-08 a C-10, C-12 y C-13 del [plan 13](13-revision-y-plan-de-cambios.md), y el 20.11.2026 con el diseño del estado de delta (§3.6). Lo marcado con H*n* depende del spike (ADR 0002).

## Objetivo

Ejecutar de forma **fiable, reanudable e idempotente** la lectura de un origen SAP y su carga en un destino, gestionando correctamente los **deltas** de la ODQ y el pseudo-delta por marca de agua.

> Este es el núcleo de la plataforma. Antes de implementarlo deben estar documentados los resultados del **Spike SAP** (ADR 0002) y congelados los contratos de este plan en el ADR 0006.

## 1. Conceptos

| Concepto | Definición |
|---|---|
| **Pipeline** | Un origen + un destino + opciones (módulo 08). Un destino adicional es otro pipeline |
| **Run** (ejecución) | Ejecución lógica de un **lote** de un pipeline. Tiene uno o varios intentos (`run_attempt`) |
| **Lote** (`batch_id`) | Identificador determinista de lo que el run debe cargar. En delta se deriva del pipeline, de la generación del estado de delta y del **token de partida**; en `WATERMARK`, del pipeline y la ventana; en `FULL` y `DELTA_INIT`, del run. Es la clave de idempotencia en destino |
| **Modo del pipeline** | `FULL`, `DELTA`, `WATERMARK` (módulo 08) |
| **Modo del run** | `FULL`, `DELTA_INIT`, `DELTA`, `WATERMARK`. Repetir un delta no es un modo: es un intento nuevo con el mismo token de partida. `DELTA_INIT_NO_DATA` solo se añade si el spike confirma que existe vía OData (H9) |
| **Delta token** | Identificador que devuelve SAP (`__delta` / `!deltatoken`) para pedir los cambios desde la última petición |
| **Spool** | Lotes extraídos guardados temporalmente en disco (Parquet) entre la extracción y la carga |

## 2. Ciclo de vida de una ejecución

Una sola máquina de estados para run, intento y cola. No hay tabla `job`: la cola son los runs en `queued` (módulo 08).

```
queued ──► running ──► succeeded
              ├──────► cancelled
              └──────► intento fallido
                         ├─ transitorio y quedan intentos ──► queued (mismo run, intento nuevo)
                         └─ en otro caso ──────────────────► failed
```

Un worker que deja de dar latido pierde el run: el scheduler cierra ese intento como fallido (`stale`) y el run vuelve a `queued`.

Pasos internos (`run_step`). Esta es la única lista; el plan 03 la referencia.

| Paso | Qué hace |
|---|---|
| `prepare` | Carga el estado de delta, valida el esquema y prueba el destino. Consulta la tabla de control del destino: si el lote pendiente ya está aplicado, salta a `commit_delta` |
| `extract` | Lee página a página de SAP → convierte a Arrow → escribe el spool Parquet. Al terminar escribe el manifiesto del spool y guarda el **token pendiente** |
| `load` | `stage(spool)` y `apply(stage, mode, batch)`: aplica (`append` / `upsert` / `replace`) y escribe la tabla de control del destino en la misma transacción |
| `commit_delta` | En la base de metadatos y con *fencing* (3.3): `current_token ← pending_token`, marca de agua, run `succeeded`, en una transacción |
| `cleanup` | Borra el spool, emite métricas y eventos |

### Spool

- **Manifiesto.** `_spool.json` se escribe al final y de forma atómica: `batch_id`, token pendiente, archivos con filas y hash, esquema. Un spool sin manifiesto es inválido: se borra y se vuelve a extraer.
- **Localidad.** El spool vive en el disco del worker que extrajo. El intento registra qué worker lo tiene: si el reintento lo toma el mismo worker, reutiliza el spool sin volver a pedir a SAP; si lo toma otro, vuelve a extraer con el mismo token de partida. Un volumen compartido queda como alternativa si la repetición resulta cara.
- **Datos en claro.** El spool contiene datos de SAP sin cifrar. Directorio con permisos solo para el usuario del servicio, borrado al terminar por defecto, retención máxima de 72 h y fuera de los backups (plan 11).

## 3. Protocolo de delta (ODQ vía OData)

### 3.1 Estado (`delta_state`, uno por pipeline)

Cada pipeline delta tiene su propia suscripción en SAP. No hay multi-destino: un segundo destino es un segundo pipeline con su propio estado.

| Campo | Descripción |
|---|---|
| `status` | `NOT_INITIALIZED` \| `INITIALIZING` \| `INITIALIZED` \| `BROKEN` |
| `generation` | Sube en cada reinicialización; forma parte del `batch_id` |
| `current_token` | Último token **confirmado** (carga exitosa) |
| `pending_token` | Token obtenido en un run que aún no se ha confirmado |
| `pending_run_id` | Run dueño del token pendiente |
| `lease_epoch` | Sube cada vez que un worker toma el run (3.3) |
| `init_filters_hash` | Filtros usados en la inicialización (si cambian, hay que reinicializar) |
| `sap_close_pending` | La suscripción de una generación anterior sigue abierta en SAP (3.5) |
| `initialized_at`, `last_delta_at` | Fechas |
| `watermark_value` | Para `WATERMARK` |
| `history` | Tabla `delta_state_history` con cada transición (auditada) |

El estado `broken` del pipeline se deriva de `delta_state.status`; no se guarda dos veces.

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
  → load KO → current_token sigue = Tn

Reintento sin spool válido:
  → se vuelve a pedir con Tn  ⇒ la ODQ debe devolver de nuevo ese delta
     [a confirmar en el spike: H2, H10, H11, H13]
```

Regla de oro: **el token confirmado solo avanza en la misma transacción que marca el run como exitoso, y solo después de que el destino confirmó la carga.**

Las peticiones que abren o avanzan un delta se envían **sin reintento automático** en el cliente HTTP (`NO_RETRY`): un *timeout* no garantiza que SAP no procesara la petición, y reintentar a ciegas puede abrir otra petición en la ODQ. El reintento se decide a nivel de run, según H10.

Si la repetición devuelve más filas que la primera vez (todo lo ocurrido desde Tn hasta ahora, H11), el resultado sigue siendo correcto: el `batch_id` depende del token de partida, no del contenido.

### 3.3 Garantías y casos límite

| Caso | Tratamiento |
|---|---|
| Caída entre el commit en destino y el commit en metadatos | En `prepare`, la tabla de control del destino indica que el lote ya está aplicado: se confirma el token guardado allí, sin volver a extraer ni cargar |
| Worker dado por caído que sigue vivo (pausa larga, corte de red con la BD) | *Fencing*: `lease_epoch` sube cada vez que un worker toma el run. `commit_delta` actualiza con `WHERE pending_run_id = :run AND lease_epoch = :epoch` y aborta si no afecta a ninguna fila. El latido comprueba el mismo valor y el worker se detiene si lo perdió |
| Dos pipelines delta sobre el mismo origen (un segundo destino) | Cada pipeline abre su propia suscripción. Se permite solo si el spike confirma que conviven dos cadenas de delta sobre el mismo entity set y usuario técnico (H6, H18); hasta entonces, bloqueado por validación. Si no conviven, alternativas a evaluar con el equipo SAP: segundo servicio SEGW sobre el mismo DataSource, usuario técnico por pipeline, o el segundo pipeline en `FULL` o `WATERMARK` |
| Filtros cambiados tras la inicialización | El pipeline pasa a requerir **reinicialización** explícita |
| Token rechazado por SAP (suscripción borrada en ODQMON, caducada) | Error `SX-SAP-DELTA-INVALID`, `status = BROKEN`, alerta; el usuario decide reinicializar |
| Reinicializar | Acción con permiso `pipeline:reset_delta`, motivo obligatorio, confirmación doble; opción de vaciar el destino; cierra la suscripción anterior en SAP (3.5) |
| Pipeline delta pausado o roto durante días | La cola crece en SAP y puede perderse la posibilidad de repetir (H13): aviso al pausar y alerta de suscripción estancada (plan 09) |
| Run cancelado | Cancelación cooperativa (se comprueba entre páginas); el token no avanza |

### 3.4 Semántica de los registros delta ODP

Los servicios ODP añaden campos técnicos, entre ellos `ODQ_CHANGEMODE` (C/U/D) y `ODQ_ENTITYCNTR`. El `$metadata` no expone el proceso de delta del extractor, así que no se detecta: lo declara el usuario en el origen (`source.delta_semantics`, plan 05).

| `delta_semantics` | Ejemplo | Modo de aplicación |
|---|---|---|
| `after_image` | Muchos extractores maestros y CDS con CDC | `upsert` por clave; `D` → borrado lógico (`_sx_deleted = 1`) o físico (configurable) |
| `additive` / `before_after` | Extractores LO (2LIS) | `append` (histórico) conservando `ODQ_CHANGEMODE` y `ODQ_ENTITYCNTR`; la consolidación se hace aguas abajo |
| `unknown` | Sin clasificar | Solo `append` |

El asistente de pipeline propone el modo a partir de ese campo y bloquea las combinaciones peligrosas (`upsert` sobre un origen aditivo).

En `upsert` con borrado lógico, un registro `D` solo marca `_sx_deleted`; no sobrescribe las demás columnas con vacíos (H17).

### 3.5 Ciclo de vida de la suscripción en SAP

La suscripción se crea en la init y la plataforma es responsable de cerrarla: al reinicializar, al borrar el pipeline y al deprecar el origen. Cada cierre se audita.

- Si el spike confirma `TerminateDeltasFor<EntitySet>` (H12), la plataforma lo llama. Si esa función cierra todas las suscripciones del entity set, solo se llama cuando no queda otro pipeline delta sobre el mismo origen.
- Si no existe o no se puede usar, queda una tarea manual guiada (ODQMON) con `sap_close_pending = true`, que se da por cerrada cuando `SubscribedTo<EntitySet>` responde que ya no existe o el usuario lo confirma.

### 3.6 Diseño del estado de delta (C-02)

> Borrador del 20.11.2026. Pasa al ADR 0006 cuando el spike cierre H6, H10, H11, H12 y H18; la tabla final de esta sección dice qué cambia según cada resultado.

**Tablas** (esquema `sx`).

```
delta_state                     -- una fila por pipeline DELTA o WATERMARK
  pipeline_id        uuid PK, FK pipeline
  status             text NOT NULL   -- NOT_INITIALIZED | INITIALIZING | INITIALIZED | BROKEN
  generation         int  NOT NULL DEFAULT 0
  current_token      text NULL
  pending_token      text NULL
  pending_run_id     uuid NULL, FK run
  lease_epoch        bigint NOT NULL DEFAULT 0
  init_filters_hash  text NULL
  sap_close_pending  bool NOT NULL DEFAULT false
  watermark_value    jsonb NULL
  initialized_at, last_delta_at, updated_at  timestamptz
  CHECK (status <> 'INITIALIZED' OR current_token IS NOT NULL)
  CHECK (pending_token IS NULL OR pending_run_id IS NOT NULL)

delta_state_history             -- solo cambios de status, generación o cierre en SAP
  id bigserial, pipeline_id, at, event, from_status, to_status, generation,
  run_id NULL, actor_id NULL, reason NULL, token_before NULL, token_after NULL
```

El run guarda, en su primer intento, `delta_generation` y `start_token` (el `current_token` de ese momento). Los intentos siguientes los reutilizan, así el `batch_id` no cambia entre intentos. Los tokens no son secretos (sin credenciales no sirven), pero no se registran en los logs de aplicación: solo en el estado, el historial y el detalle del run.

**Transiciones.**

| Evento | Desde | Hacia | Cambios | Registro |
|---|---|---|---|---|
| Un worker toma un run `DELTA_INIT` | `NOT_INITIALIZED` | `INITIALIZING` | `generation + 1`, `pending_run_id = run`, `lease_epoch + 1`, `init_filters_hash`, tokens a `NULL` | Historial |
| Un worker toma un run `DELTA` | `INITIALIZED` | `INITIALIZED` | `pending_run_id = run`, `lease_epoch + 1` | — |
| `extract` termina, o `prepare` encuentra el lote ya aplicado en el destino | `INITIALIZING`, `INITIALIZED` | igual | `pending_token = T` (con *fencing*) | — |
| `commit_delta` de una init | `INITIALIZING` | `INITIALIZED` | `current_token = pending_token`, `initialized_at`, `pending_*` a `NULL` | Historial |
| `commit_delta` de un delta | `INITIALIZED` | `INITIALIZED` | `current_token = pending_token`, `last_delta_at`, `pending_*` a `NULL` | Detalle del run |
| Intento fallido con reintento pendiente | — | igual | Nada: el run sigue siendo el dueño (`pending_run_id`) | — |
| Run `failed` o `cancelled` en una init | `INITIALIZING` | `NOT_INITIALIZED` | `pending_*` a `NULL`; `sap_close_pending = true` (la init cortada puede quedar abierta, H10) | Historial |
| Run `failed` o `cancelled` en un delta | `INITIALIZED` | `INITIALIZED` | `pending_*` a `NULL` | — |
| SAP rechaza el token (`SX-SAP-DELTA-INVALID`, código real en H7) | `INITIALIZED` | `BROKEN` | `pending_*` a `NULL` | Historial, alerta y auditoría `pipeline.delta_broken` |
| El usuario reinicializa | `INITIALIZED`, `BROKEN` | `NOT_INITIALIZED` | `current_token = NULL`, `sap_close_pending = true`; después, intento de cierre | Historial y auditoría `pipeline.delta_reset` (motivo obligatorio) |
| Cierre confirmado en SAP | — | igual | `sap_close_pending = false` | Historial y auditoría `pipeline.sap_subscription_closed` |
| El usuario deja abierta la suscripción anterior | — | igual | `sap_close_pending = false` | Historial y auditoría `pipeline.sap_subscription_left_open` (motivo) |

Con los filtros cambiados (`init_filters_hash` distinto del actual), la validación bloquea los runs `DELTA` hasta reinicializar. Ese estado se calcula, no se guarda.

**Toma del run y *fencing*.** En la misma transacción que el *claim* (plan 08 §3):

```sql
UPDATE sx.delta_state
   SET pending_run_id = :run, lease_epoch = lease_epoch + 1, updated_at = now()
 WHERE pipeline_id = :pipeline
   AND (pending_run_id IS NULL OR pending_run_id = :run)
RETURNING lease_epoch, generation, current_token;
-- 0 filas: otro run es dueño del estado → no se toma
```

Cuando el scheduler da por caído un intento, el run vuelve a `queued` sin soltar `pending_run_id`: el siguiente worker lo toma con `lease_epoch + 1` y el anterior queda fuera. El latido y cada escritura del worker sobre el estado llevan `AND lease_epoch = :epoch`.

**`commit_delta`**, una transacción:

```sql
UPDATE sx.delta_state
   SET current_token = pending_token, pending_token = NULL, pending_run_id = NULL,
       status = 'INITIALIZED', last_delta_at = now(),
       initialized_at = COALESCE(initialized_at, now()), updated_at = now()
 WHERE pipeline_id = :pipeline AND pending_run_id = :run
   AND lease_epoch = :epoch AND generation = :generation
   AND pending_token IS NOT NULL;
-- 0 filas: intento sin derecho a confirmar → ROLLBACK, el intento acaba como `fenced`
UPDATE sx.run SET status = 'succeeded', finished_at = now() WHERE id = :run AND status = 'running';
INSERT INTO sx.delta_state_history (...)   -- solo si cambió status
COMMIT;
```

La condición sobre `generation` impide que un run de antes de una reinicialización confirme su token en la generación nueva.

**Reinicialización y cierre en SAP.** El orden importa si `TerminateDeltasFor…` cierra todas las suscripciones del entity set para el usuario técnico (alcance de H12): llamado después de la init nueva, la cerraría también.

1. Rechazar si hay un run del pipeline en curso (409), o cancelarlo primero si el usuario lo pide.
2. Transacción: `NOT_INITIALIZED`, `current_token = NULL`, `sap_close_pending = true`, historial y auditoría.
3. Si se puede cerrar desde la plataforma (H12 confirmado y ningún otro pipeline delta lee el mismo entity set con el mismo usuario técnico), llamar a `TerminateDeltasFor…` y comprobar con `SubscribedTo…`. Si responde que ya no existe: `sap_close_pending = false`.
4. Si no, tarea manual guiada en ODQMON.
5. La init nueva no se encola mientras `sap_close_pending = true`, salvo que el usuario declare que deja abierta la anterior (motivo, auditado). Así la plataforma nunca cierra a ciegas una suscripción que acaba de abrir.

Borrar un pipeline delta lo marca como borrado (`deleted_at`) y conserva su `delta_state` mientras `sap_close_pending = true`; la regla "Suscripción sin cerrar" (plan 09) lo avisa hasta que se cierre.

**Qué cambia según el spike.**

| Resultado | Consecuencia en este diseño |
|---|---|
| H6 o H18 no se cumplen (no conviven dos cadenas sobre el mismo entity set y usuario) | Un segundo pipeline `DELTA` sobre el mismo origen queda bloqueado por validación; alternativas de 3.3 |
| H18: las dos inits devuelven el mismo token o comparten cola | Igual que la fila anterior: es una sola suscripción |
| H11: tras una repetición solo vale el token de la primera lectura | `pending_token` no se sobrescribe en un reintento sin spool si SAP devuelve un token distinto; se conserva el primero recibido para ese `start_token` |
| H11: vale el token de la repetición (lo esperado) | Sin cambios: `pending_token` es el último token recibido |
| H12 no existe | Paso 3 de la reinicialización desaparece; el cierre siempre es manual |
| H12 cierra solo la suscripción del usuario y filtro concretos | Se puede cerrar aunque haya otro pipeline sobre el mismo origen |
| H10: una init cortada no deja nada abierto en SAP | El fallo de una init no marca `sap_close_pending` |
| H10: relanzar la init crea otra suscripción | El reintento automático de un run `DELTA_INIT` se desactiva; reintentar exige cerrar antes |
| H13: periodo de recuperación de *N* horas | Umbral de la alerta de suscripción estancada (plan 09) por debajo de *N* |
| H7: código y mensaje reales | Regla de clasificación de `SX-SAP-DELTA-INVALID` en el conector |

## 4. Idempotencia en destino

- Columnas técnicas añadidas a cada fila: `_sx_batch_id`, `_sx_run_id` (informativo), `_sx_extracted_at`, `_sx_change_mode`, `_sx_record_seq`, `_sx_deleted`.
- **Tabla de control en el destino**, `__sx_load_state` (pipeline, `batch_id`, token resultante, filas, fecha), escrita en la misma transacción que el `apply` en Snowflake y SQL Server. En CSV ese papel lo cumplen `_manifest.json` y `_SUCCESS` (plan 07).
- `upsert`: `MERGE` por clave sobre una tabla de staging deduplicada (gana el último registro por clave según orden de llegada / `ODQ_ENTITYCNTR`).
- `append`: antes de insertar se eliminan las filas del mismo `_sx_batch_id`, de modo que repetir el lote no duplica.
- `replace` (full): sustitución atómica del contenido, sin dejar el destino vacío durante la carga y sin perder los permisos concedidos sobre la tabla (el mecanismo depende del destino, plan 07).
- CSV: escritura a archivo temporal y renombrado atómico; carpeta y nombres por `batch_id`, así repetir sobrescribe los mismos archivos.

## 5. Pseudo-delta por marca de agua (`WATERMARK`)

Para `CDS_ODATA` sin delta nativo:

```
desde = watermark_value - overlap        (p. ej. 10 min; en campos de solo fecha, días completos)
hasta = ahora_SAP - safety_lag           (p. ej. 2 min, evita transacciones en curso)
GET <EntitySet>?$filter=<campo> ge <desde> and <campo> lt <hasta>
→ upsert por clave → watermark_value = hasta
```

- `ahora_SAP` sale de la cabecera `Date` de la respuesta de SAP, no del reloj del worker.
- El campo puede ser fecha-hora, solo fecha (`AEDAT`) o *timestamp* decimal: `source.watermark_kind` (`datetime`, `date`, `decimal_timestamp`) fija el literal del filtro (distinto en V2 y V4) y el solape. La zona horaria del campo se declara en el origen.

Limitación documentada: no detecta borrados (se recomienda una reconciliación full periódica configurable, p. ej. semanal).

## 6. Lectura y conversión de datos

| Tema | Decisión |
|---|---|
| Paginación ODP | Paginación de servidor (`__next` / `$skiptoken`) con `Prefer: odata.maxpagesize=<page_size>`; nunca `$skip` en delta |
| Paginación `CDS_ODATA` | `__next` si el servicio lo ofrece. Si no, paginación por clave: `$orderby` por las claves y `$filter` "mayor que la última clave leída" (con claves compuestas, filtro lexicográfico). `$top/$skip` solo para tablas pequeñas |
| Iterador de páginas | Propio del motor: sin tope de páginas (el `MAX_PAGES` del cliente es para el catálogo) y con límite de bytes por página como protección |
| Memoria | Cada página se lee entera: su tamaño lo acota `page_size`. Lotes Arrow de tamaño fijo y spool a Parquet: memoria acotada independiente del volumen. No se usa *parsing* incremental |
| Reintentos HTTP | Sin reintento automático en las peticiones de init y delta (3.2); sí en lecturas sin estado (`$metadata`, full por páginas con `$skiptoken` si H10 confirma que es repetible) |
| Tipos OData V2 | `/Date(ms)/` de `Edm.DateTime` → fecha y hora **sin zona**, sin conversión (H15); `Edm.DateTimeOffset` → instante UTC; `Edm.Decimal` (string) → decimal con precisión del `$metadata`; `Edm.Time` (`PT12H30M00S`) → time; fechas SAP vacías (`00000000`) → NULL |
| Importes | Si llegan en formato interno (H14), se expone la moneda de referencia junto al importe y el asistente lo avisa; la conversión queda como opción |
| Textos | Se respetan ceros a la izquierda (NUMC como texto); recorte de espacios configurable |
| Paralelismo | Un run lee secuencialmente (el delta es secuencial); el paralelismo es **entre pipelines**, limitado por sistema SAP (plan 08) |
| Rendimiento | Métricas por página (latencia SAP, filas/seg); objetivo de referencia a fijar tras el spike (H5) |
| Timeouts | Timeout de lectura por página y timeout global por run (configurable por pipeline) |
| Heartbeat | El worker actualiza `run.heartbeat_at` cada 15 s y comprueba su `lease_epoch`; el scheduler cierra como `stale` los intentos sin latido |

### Cargas iniciales grandes

Una init de cientos de millones de filas puede durar muchas horas y un corte la reinicia. La estrategia se decide con H8–H10 en un ADR: init particionada (cargas `FULL` por rangos de filtro en paralelo más init sin datos) o, si H9 falla, init reanudable desde el spool y ventana acordada con Basis.

## 7. Interfaces (puertos) y plugins

```python
class SourceConnector(Protocol):
    def schema(self) -> EntitySchema: ...
    def read_full(self, filters: Filters) -> Iterator[pa.RecordBatch]: ...
    def read_delta_init(self, filters: Filters) -> DeltaReadResult: ...
    def read_delta(self, token: DeltaToken) -> DeltaReadResult: ...
    def read_window(self, field: str, start: WatermarkValue, end: WatermarkValue) -> Iterator[pa.RecordBatch]: ...
    def terminate_delta(self) -> TerminationResult: ...   # solo si H12 se confirma

class DeltaReadResult(Protocol):
    batches: Iterator[pa.RecordBatch]
    def next_token(self) -> DeltaToken: ...   # disponible al agotar batches

class Spool(Protocol):
    manifest: SpoolManifest
    def parquet_files(self) -> Sequence[Path]: ...
    def batches(self) -> Iterator[pa.RecordBatch]: ...

class DestinationWriter(Protocol):          # módulo 07
    def prepare(self, schema: EntitySchema, options: LoadOptions) -> None: ...
    def applied_batch(self, pipeline_id: UUID, batch_id: str) -> AppliedBatch | None: ...
    def stage(self, spool: Spool) -> StageInfo: ...
    def apply(self, stage: StageInfo, mode: LoadMode, batch: BatchInfo) -> LoadResult: ...   # transaccional; escribe la tabla de control
    def cleanup(self, stage: StageInfo) -> None: ...
```

`stage` recibe el spool y no solo lotes: Snowflake sube los archivos Parquet tal cual, sin reescribirlos. Las firmas definitivas, las columnas técnicas, el formato de `batch_id` y del manifiesto del spool y la tabla de control están en el [ADR 0006](../adr/0006-contratos-del-motor.md); si difieren de este resumen, manda el ADR.

Registro de plugins por tipo (`ODP_EXTRACTOR`, `CDS_ODP`, `CDS_ODATA` / `csv`, `snowflake`, `sqlserver`). El motor (`ExtractionEngine`) solo conoce las interfaces.

## 8. Errores y reintentos

| Clase | Ejemplos | ¿Reintento automático? |
|---|---|---|
| Transitorio | Timeout, 502/503/504, conexión cortada, *deadlock* en destino | Sí, como intento nuevo del run (política del pipeline) |
| Configuración | 401/403, tabla destino sin permisos, esquema incompatible | No → alerta |
| Datos | Conversión imposible, clave nula en `upsert` | No → alerta con la clave y la columna afectadas; la muestra de filas se consulta en el detalle del run y no viaja en el aviso |
| Delta | Token inválido | No → `BROKEN` |

## Tareas

- [x] Diseño del estado de delta: transiciones, *fencing*, `commit_delta` y cierre en SAP (§3.6, borrador del 20.11.2026; se cierra con el spike).
- [x] ADR 0006 con los contratos (`SourceConnector`, `DestinationWriter`, spool, run e intentos, `batch_id`, tabla de control), propuesto el 20.11.2026 y pendiente de revisión conjunta. El estado de delta queda fuera hasta el spike (§3.6).
- [ ] Paquete `dataflow/` con esos tipos, batería de conformidad y dobles de prueba (inicio de la Fase 1).
- [ ] Modelo `run`, `run_attempt`, `run_step`, `delta_state`, `delta_state_history`.
- [ ] Conectores `ODP_EXTRACTOR` / `CDS_ODP` (comparten implementación ODP) y `CDS_ODATA` (paginación por clave).
- [ ] Iterador de páginas del motor, conversión de tipos EDM → Arrow, spool Parquet con manifiesto.
- [ ] Máquina de estados de delta con commit en dos fases, `batch_id` y *fencing*.
- [ ] Salto de `load` cuando la tabla de control del destino ya tiene el lote.
- [ ] Pseudo-delta por marca de agua con `watermark_kind`.
- [ ] `ExtractionEngine` con pasos, cancelación, heartbeat y métricas.
- [ ] Reinicialización de delta y cierre de la suscripción en SAP (API + auditoría).
- [ ] Pruebas con `sap-mock`: init, delta, repetición, token inválido, caída en cada paso (inyección de fallos), worker zombi, reintento en otro worker.
- [ ] Prueba de volumen (≥ 10 M filas sintéticas) con memoria acotada.
- [ ] ADR de cargas iniciales grandes, tras el spike.

## Criterios de aceptación

- Matar el worker en cualquier paso y relanzar no pierde ni duplica datos (prueba automatizada por cada paso), en CSV y en Snowflake.
- Un fallo de carga se reintenta sin volver a llamar a SAP cuando el spool sigue en el mismo worker.
- Un worker dado por caído que vuelve no puede confirmar el token.
- Un token inválido deja el pipeline en `BROKEN` con alerta y sin avanzar estado.
- El consumo de memoria de un run no crece con el número de filas.
- Criterio de volumen (filas/hora en una init real) fijado tras el spike y cumplido.
