# 13 · Revisión del diseño y plan de cambios

> Estado: **decidido y trasladado a los planes el 19.11.2026** (decisiones en la sección 6) · Revisión del 18.11.2026
> Revisado: planes 00–12, ADR 0001–0005, ROADMAP y el código de las iteraciones 0–2.
> Los planes 00–12, el ROADMAP y el ADR 0002 ya recogen estos cambios. Este documento queda como registro del porqué; si difiere de un plan, manda el plan.

## 0. Resumen

La base es buena y no se toca: ODP vía OData como única vía soportada, el token que solo avanza tras confirmar la carga, monolito modular, PostgreSQL como cola, sesiones de servidor, cifrado *envelope*, auditoría dentro de la transacción y contrato OpenAPI verificado en CI. El código de It0–It2 sigue los planes (por ejemplo, la prueba de conexión confirma la transacción antes de llamar a SAP, así que el bloqueo de la cadena de auditoría nunca se retiene durante una llamada de red). Las librerías que faltan por incorporar (`pyarrow`, `pyodbc`, `snowflake-connector-python`, `croniter`, `authlib`) ya publican *wheels* para Python 3.14 (comprobado el 18.11.2026).

Lo que más cambia el resultado, en orden:

1. **El spike contra el SAP real sigue sin ejecutarse** y todo el motor (plan 06) descansa en hipótesis. Además faltan hipótesis que condicionan el diseño (C-01).
2. **El estado de delta está colgado del pipeline, pero en SAP pertenece a la suscripción ODQ.** Nadie cierra las suscripciones en SAP, y no está comprobado que dos pipelines sobre el mismo origen puedan convivir, que es el modelo elegido para tener más de un destino (C-02, decisión 2).
3. **El protocolo de confirmación está incompleto**: no hay clave de lote definida, ni protección contra un worker zombi, y el spool no funciona con varios workers tal como está descrito (C-03, C-04).
4. **El primer dato real llega en It5 y el motor lo construiría una sola persona.** Con dos desarrolladores conviene reordenar (sección 5).
5. **Los conectores abren tres vías de abuso** que el plan no cierra: peticiones a hosts arbitrarios, escritura en rutas arbitrarias e identificadores SQL sin validar (C-07).

## 1. Tabla de cambios

Prioridad: **P0** = resolver antes de empezar It3/It5 · **P1** = resolver al implementar el módulo · **P2** = mejora, se puede aplazar.

| ID | P | Cambio | Planes | Cuándo |
|---|---|---|---|---|
| C-01 | P0 | Ejecutar el spike y ampliarlo con 11 hipótesis nuevas | 01, 06, ADR 0002 | Fase 0 |
| C-02 | P0 | Estado de delta por pipeline con ciclo de vida de la suscripción en SAP | 00, 06, 08, 12 | Diseño Fase 0 · código It5 |
| C-03 | P0 | Clave de lote, tabla de control en destino y *fencing* | 06, 07 | It4 e It5 |
| C-04 | P0 | Spool: manifiesto, localidad entre workers, datos sensibles | 06, 08, 11 | It5 |
| C-05 | P0 | Sin reintentos transparentes en peticiones de init/delta | 04, 06 | It5 |
| C-06 | P0 | Una sola máquina de estados para run, intento y cola | 06, 08, 09 | Diseño Fase 0 · código It5–It6 |
| C-07 | P0 | Seguridad de conectores (hosts, rutas, identificadores, proxy) | 04, 07, 09, 11 | Fase 0 e It4 |
| C-08 | P1 | CDS vía OData: paginación por clave y tipos de marca de agua | 05, 06 | It3 e It5 |
| C-09 | P1 | Semántica de delta del extractor declarada por el usuario | 05, 06, 08 | It3 |
| C-10 | P1 | Tipos: fechas sin zona, importes, registros de borrado | 06, 07 | It4 e It5 |
| C-11 | P1 | SQL Server: `replace` sin `sp_rename`, claves anchas, rendimiento medido | 07 | It4b |
| C-12 | P1 | `DestinationWriter` recibe el spool, no solo lotes | 06, 07 | Fase 0 (contrato) |
| C-13 | P1 | Estrategia para cargas iniciales grandes | 06 | Tras el spike |
| C-14 | P1 | Límite por sistema SAP a nivel de run, no por petición | 04, 08 | It6 |
| C-15 | P1 | Logs en vivo por sondeo; sin archivo local en el worker | 03 | It5 |
| C-16 | P1 | Backfill solo para orígenes de imagen posterior | 08 | It6 |
| C-17 | P1 | Alerta de suscripción estancada | 09, 11 | It8 |
| C-18 | P2 | Ancla externa de la cadena de auditoría | 03 | It9 |
| C-19 | P1 | Asistente de pipeline en lugar de lienzo (decidido) | 00, 08, 10, 12 | It6 |
| C-20 | P1 | Instancia productiva única; coherencia y validación por entorno (decidido) | 00, 08, 11 | It6 e It9 |
| C-21 | P1 | Alinear el stack del frontend con lo instalado | 00, 10 | Antes de It3 |
| C-22 | P1 | Hacer verificable la definición de "Hecho" | ROADMAP, 01, 10 | Fase 0 |

Todos están trasladados a los planes, salvo C-07 e, que no se aplica (decisión 6).

## 2. Cambios P0

### C-01 · Spike: ruta crítica y más hipótesis

**Problema.** El ADR 0002 tiene H1–H7 en "pendiente". It3 depende de cómo es un `$metadata` real y It5 depende de todo lo demás. Varias decisiones del plan 06 se apoyan en comportamientos que nadie ha comprobado y que no figuran entre las hipótesis.

**Cambio.** El equipo SAP está disponible y el spike empieza de inmediato (decisión 1). El script `tools/spike-sap/spike_odp.py` cubre las hipótesis nuevas desde el 20.11.2026 (plan de sesiones en su README). Primero H6, H12 y H18: condicionan el modelo "otro destino = otro pipeline" (decisión 2).

| # | Hipótesis nueva | Por qué importa |
|---|---|---|
| H8 | Tiempo hasta la primera página en una init grande frente a los límites de Gateway (tiempo máximo de proceso de diálogo, *timeout* de ICM). ¿Admite el servicio `Prefer: respond-async`? | Si la init síncrona de un extractor grande no cabe en el límite, hace falta otro diseño de init |
| H9 | ¿Existe una init **sin datos** vía OData? | El plan 06 lista el modo `DELTA_INIT_NO_DATA` como disponible |
| H10 | Volver a pedir la misma página (`$skiptoken`) tras un corte: ¿devuelve lo mismo? Volver a lanzar la init: ¿crea otra suscripción? | Define qué se puede reintentar (C-05) y si la init es reanudable |
| H11 | La repetición con el token anterior, ¿devuelve el mismo conjunto o **todo lo ocurrido desde ese token hasta ahora**? | El script compara recuentos (`same_row_count`): si entran cambios entre las dos lecturas dará falso aunque la repetición funcione. Comparar por claves y `ODQ_ENTITYCNTR`. El mock ya se comporta como "desde el token hasta ahora" |
| H12 | `SubscribedTo<EntitySet>` y `TerminateDeltasFor<EntitySet>`: la documentación de SAP para ODP vía OData los describe. Confirmar que aparecen en el `$metadata` de AG, su método HTTP, su efecto en ODQMON y su alcance (¿todas las suscripciones del entity set para el usuario técnico?) | Permite cerrar suscripciones desde la plataforma (C-02) |
| H13 | Retención de la ODQ: cuánto tiempo sigue siendo repetible un delta ya confirmado (periodo de recuperación, habitualmente 24 h; confirmar con Basis) y qué ocurre con una suscripción que deja de leer | Un pipeline parado varios días puede perder la posibilidad de repetir y hacer crecer la cola en SAP (C-17) |
| H14 | Importes en monedas sin decimales (CLP, JPY…): ¿llegan en formato interno o externo? Comparar con SAP GUI | Error clásico de factor 100 (C-10) |
| H15 | `Edm.DateTime`: ¿hora local del sistema o UTC, según el tipo ABAP del campo? | El plan 07 lo trata todo como UTC (C-10) |
| H16 | Vista previa con `$top` sobre un extractor grande: duración y carga en ODQMON, no solo "no crea suscripción" | La vista previa del asistente (plan 05 §2) podría lanzar una extracción completa en SAP |
| H17 | Registros con `ODQ_CHANGEMODE = D`: ¿solo traen las claves informadas? | Afecta al borrado lógico en `upsert` (C-10) |
| H18 | Dos inits con `odata.track-changes` sobre el mismo entity set, con los mismos filtros y el mismo usuario técnico: ¿quedan dos cadenas de delta independientes, o la segunda sustituye o comparte la primera? | Decisión 2: un segundo destino es un segundo pipeline sobre el mismo origen. Si no conviven, ese pipeline no puede ir en delta tal cual (C-02). Afecta igual a una instancia de QA de la plataforma que lea los mismos servicios (C-20) |

El mock añade `SubscribedTo…` y `TerminateDeltasFor…` cuando H12 se confirme. Si al empezar la fase del motor el spike sigue pendiente, ver 5.4.

### C-02 · Estado de delta: ciclo de vida de la suscripción en SAP

**Problema.** El plan 06 §3.1 define `delta_state` "uno por pipeline", y un pipeline es origen + destino. En SAP, la suscripción ODQ pertenece al servicio, al entity set, al usuario técnico y a los filtros de la init; el destino no interviene. Dos consecuencias:

- La regla "dos pipelines delta sobre el mismo origen y filtros: bloqueado" (06 §3.3) choca con la decisión 2: un destino adicional es otro pipeline 1:1 sobre el mismo origen. Que dos cadenas de delta independientes convivan sobre el mismo entity set y usuario técnico no está comprobado (H6, H18).
- Nadie cierra la suscripción en SAP al borrar un pipeline, deprecar un origen o reinicializar: el plan solo "documenta cerrarla en ODQMON". Las suscripciones huérfanas siguen acumulando datos en la cola de SAP.

**Cambio (revisado tras la decisión 2).** La primera versión de esta propuesta creaba una tabla `delta_subscription` aparte para compartirla entre destinos. Descartado el multi-destino, sobra: el estado de delta sigue siendo uno por pipeline.

- `delta_state` añade `generation` (sube en cada reinicialización), `lease_epoch` (C-03) y `sap_close_pending` (la suscripción anterior sigue abierta en SAP).
- Dos pipelines delta sobre el mismo origen: cada uno abre su propia suscripción. Se permite solo si el spike confirma que conviven (H6, H18); hasta entonces sigue bloqueado por validación. Si no conviven, alternativas a evaluar con el equipo SAP: un segundo servicio SEGW sobre el mismo DataSource, un usuario técnico por pipeline, o el segundo pipeline en `FULL` o `WATERMARK`.
- Si `TerminateDeltasFor…` cierra todas las suscripciones del entity set (alcance de H12), cerrar la de un pipeline rompería la del otro: en ese caso el cierre automático solo se usa cuando no queda otro pipeline delta sobre el mismo origen.
- Ciclo de vida explícito y auditado: se crea en la init y se termina al reinicializar, al borrar el pipeline y al deprecar el origen, llamando a `TerminateDeltasFor…` si H12 lo confirma. Si no, tarea manual guiada que se da por cerrada cuando `SubscribedTo…` responde que ya no existe.
- Plan 00 §2 dice "solo lectura sobre SAP". Matizar: solo lectura de datos de negocio; la plataforma gestiona sus propias suscripciones.

### C-03 · Clave de lote, tabla de control en destino y *fencing*

**Problemas.**

1. La clave de idempotencia no está definida. El plan 06 §4 habla de "`_sx_run_id` lógico (clave = token origen)", pero las columnas técnicas no incluyen el token y un reintento es otro run. El plan 07 §4 nombra los CSV con `{run_id}` mientras el 06 §4 pide un nombre determinista por token.
2. Tras una caída entre la confirmación en destino y la confirmación en metadatos, el siguiente run repite la extracción y la carga completas. Funciona, pero cuesta una extracción entera y depende de que SAP todavía pueda repetir (H13).
3. El scheduler marca como `stale` un run sin latido y libera el bloqueo, pero el worker original puede seguir vivo (pausa larga, corte de red con la BD) y confirmar el token más tarde.

**Cambio.**

- `batch_id`: identificador determinista del lote, derivado de la suscripción y del **token de partida** (en `FULL` y `WATERMARK`, del pipeline y la ventana). Columna `_sx_batch_id` en destino; `_sx_run_id` queda como dato informativo. En CSV, carpeta y archivos por `batch_id`.
- Tabla de control en el destino, `__sx_load_state` (pipeline, `batch_id`, token resultante, filas, fecha), escrita **en la misma transacción** que el `apply` en SQL Server y Snowflake. En CSV ese papel lo cumplen `_manifest.json` y `_SUCCESS`. En `prepare`, si el lote pendiente ya figura como aplicado, se salta `load` y se confirma el token leído de la tabla de control.
- *Fencing*: `lease_epoch` se incrementa cada vez que un worker toma el run. `commit_delta` actualiza con `WHERE pending_run_id = :run AND lease_epoch = :epoch` y aborta si no afecta a ninguna fila. El latido comprueba el mismo valor y el worker se detiene si lo perdió.
- Si la repetición devuelve más filas que la primera vez (H11), el resultado sigue siendo correcto porque `batch_id` depende del token de partida: `append` borra por `batch_id` e inserta; `upsert` hace `MERGE`.

### C-04 · Spool

**Problemas.**

1. El plan 11 §2 pone el spool en disco local de cada worker, con 2–N workers; los planes 06 §2 y 08 §4 dicen que el reintento reutiliza el spool. Si el reintento lo toma otro worker, el spool no está.
2. "Spool válido" no está definido.
3. El spool contiene datos de SAP en claro (finanzas, RR. HH.) y el plan permite conservarlo N días.

**Cambio.**

- Manifiesto `_spool.json` escrito al final y de forma atómica: `batch_id`, token pendiente, archivos con filas y hash, esquema. Un spool sin manifiesto es inválido: se borra y se vuelve a extraer.
- Localidad por afinidad: el reintento registra qué worker tiene el spool; si lo toma otro, vuelve a extraer por repetición. No exige infraestructura. Un volumen compartido queda como alternativa si la repetición resulta cara.
- Directorio con permisos solo para el usuario del servicio, borrado al terminar por defecto, retención máxima corta (72 h) y fuera de los backups. El cifrado del volumen es responsabilidad de la infraestructura y se documenta en el plan 11.

### C-05 · Reintentos transparentes en init y delta

**Problema.** `SapODataClient._send` reintenta por defecto hasta tres veces ante *timeout*, 502, 503 y 504 ([odata_client.py:397](../../backend/src/sapextract/sap_connections/infrastructure/odata_client.py#L397)). Para el catálogo y los metadatos es correcto. Para la init con `odata.track-changes` o para una petición de delta, un *timeout* no garantiza que SAP no procesara la petición: reintentar a ciegas puede abrir una segunda petición o suscripción en la ODQ. Además, `iter_pages` corta a las 500 páginas (`MAX_PAGES`), pensado para el catálogo.

**Cambio.**

- El motor usa `NO_RETRY` (ya existe) en las peticiones que abren o avanzan un delta, y decide el reintento a nivel de run según H10.
- Iterador de páginas propio para el motor, sin tope de páginas y con límite de bytes por página como protección.
- El cuerpo de cada página puede seguir leyéndose entero en memoria: queda acotado por `page_size`. Con eso sobra el *parsing* incremental con `ijson` del plan 06 §6: misma garantía de memoria, menos complejidad.

### C-06 · Run, intento y cola: una sola máquina de estados

**Problema.** En el plan 06 §2 un run fallido vuelve a `queued` (el mismo run). En el plan 08 un reintento es un `job` nuevo con `trigger = retry`, y existen `run.attempt` y `job.attempt`. `job.status` y `run.status` describen lo mismo dos veces, igual que `pipeline.status = broken` y `delta_state.status = BROKEN`.

**Cambio.**

- **Run** = ejecución lógica de un lote (`batch_id`). Tiene N **intentos** (`run_attempt`: worker, inicio, fin, error, `lease_epoch`).
- La cola es el propio run en estado `queued` (`available_at`, `priority`, `claimed_by`). La tabla `job` desaparece (decisión 4).
- El estado "roto" del pipeline se deriva de la suscripción; no se guarda dos veces.

### C-07 · Seguridad de conectores

| | Problema | Cambio | Cuándo |
|---|---|---|---|
| a | `base_url`, `proxy_url` y la URL de token OAuth los escribe un usuario, y la prueba de conexión devuelve DNS, TCP, TLS y el certificado presentado: sirve para explorar la red interna. `normalize_base_url` solo valida esquema y host | `SX_SAP_ALLOWED_HOSTS` (sufijos DNS o CIDR; obligatorio en PRD). Bloquear siempre 169.254.0.0/16 y, fuera de DEV, *loopback*. Resolver y validar la IP antes de conectar. Mismo criterio para destinos (It4) y *webhooks* de alertas (It8) | Fase 0 |
| b | El destino CSV acepta cualquier ruta del servidor: escritura arbitraria de archivos | `SX_CSV_ROOTS` con las raíces permitidas; resolver la ruta real y exigir que quede dentro; sanear las variables del patrón de nombre | It4 |
| c | `target_name`, esquema y `column_mapping` acaban en DDL y `MERGE` generados | Validar contra un patrón y citar siempre el identificador; nunca interpolar. Prueba con nombres maliciosos | It4 |
| d | La imagen arranca con `--forwarded-allow-ips "*"` ([backend.Dockerfile:52](../../deploy/docker/backend.Dockerfile#L52)): quien alcance el puerto 8000 falsifica su IP, que es la que usan la auditoría y el límite de intentos ([deps.py:46](../../backend/src/sapextract/identity/api/deps.py#L46)) | `SX_TRUSTED_PROXIES` con la red del proxy; por defecto, solo *loopback* | Fase 0 |
| e | La vista previa lee SAP con el usuario técnico: el RBAC de la plataforma es la única barrera sobre datos sensibles | **No se aplica** (decisión 6): no hay tratamiento especial y el usuario técnico tiene autorización amplia de lectura. La barrera es el RBAC de la plataforma: los permisos sobre orígenes y pipelines se dan solo a personas capacitadas. Único ajuste: las alertas no llevan filas de datos en el cuerpo del aviso (plan 09) | — |

## 3. Cambios P1 y P2

### C-08 · CDS vía OData: paginación y marca de agua

- **Paginación.** El plan 06 §6 asume `__next` del servidor. Los servicios CDS publicados sin ODP normalmente no paginan solos: se pagina con `$top/$skip`, que en tablas grandes se vuelve lento y da resultados inconsistentes si entran filas durante la lectura. Cambio: paginación por clave (`$orderby` por las claves y `$filter` "mayor que la última clave leída"; con claves compuestas, filtro lexicográfico). `$skip` solo para tablas pequeñas; `__next` si el servicio lo ofrece.
- **Marca de agua.** El plan 06 §5 asume un campo fecha-hora. En la práctica hay campos de solo fecha (`AEDAT`), *timestamps* decimales (`Edm.Decimal`) y `DateTimeOffset`, y el literal del filtro cambia entre V2 y V4. Cambio: `watermark_kind` (`datetime`, `date`, `decimal_timestamp`) con su literal y su solape (en fechas, días completos). La hora de referencia sale de la cabecera `Date` de SAP y la zona horaria del campo se declara en el origen.

### C-09 · Semántica de delta declarada

Los planes 06 §3.4 y 08 §6 dicen que el asistente sugiere `upsert` o `append` "según el origen". El `$metadata` no expone el proceso de delta del extractor, así que no se puede detectar. Cambio: campo `source.delta_semantics` (`after_image`, `additive`, `before_after`, `unknown`) que informa el usuario con ayuda de la guía (RSA2), más una tabla de valores conocidos para los extractores estándar frecuentes. Con `unknown` solo se permite `append`. Añadirlo a la guía del plan 05 §5.

### C-10 · Tipos y datos

- **Fechas.** El plan 07 §6 convierte `Edm.DateTime` a `timestamp[us, UTC]`. Las fechas y horas ABAP clásicas son hora local sin zona. Cambio: `timestamp[us]` sin zona hacia `DATETIME2` y `TIMESTAMP_NTZ`, sin conversión; solo `DateTimeOffset` se trata como instante UTC (H15).
- **Importes.** Si llegan en formato interno (H14), exponer la moneda de referencia junto al importe y avisar en el asistente; la conversión queda como opción.
- **Borrados.** En `upsert` con borrado lógico, un registro `D` solo marca `_sx_deleted`; no sobrescribe las demás columnas con vacíos (H17).

### C-11 · SQL Server

- **`replace`.** Con `sp_rename`, la tabla nueva no hereda los permisos concedidos sobre la tabla anterior ni sus índices o vistas dependientes: los consumidores pierden acceso tras cada carga. Cambio: `TRUNCATE` + `ALTER TABLE … SWITCH` (o `INSERT … SELECT`) dentro de una transacción, sobre la tabla estable.
- **Claves anchas.** La PK sobre claves SAP puede superar el límite de clave de índice (900 bytes agrupado, 1700 no agrupado). Cambio: validarlo en `ddl-preview` y ofrecer índice único no agrupado o clave *hash*.
- **Rendimiento.** Medir `fast_executemany` en It4b con 10 M de filas frente al volumen de D5. La carga masiva tipo `bcp` se decide con ese dato, no se deja como "futuro".

### C-12 · `DestinationWriter` recibe el spool

`stage(batches)` obliga a Snowflake a reescribir Parquet cuando el spool ya es Parquet y podría subirse tal cual. Cambio: `stage(spool)`, donde el spool expone sus archivos Parquet y un iterador de lotes; `apply(stage, mode, batch_id)` escribe la tabla de control (C-03). Se congela en la Fase 0 junto con C-03 y C-06 (ADR 0006).

### C-13 · Cargas iniciales grandes

Un run lee de forma secuencial y el rendimiento de OData está por medir (H5). Una init de cientos de millones de filas puede durar muchas horas, y un corte la reinicia. El ROADMAP solo prevé ajustar la página, las ventanas y el paralelismo entre pipelines. Cambio, a decidir con H8–H10 en un ADR: init particionada (cargas `FULL` por rangos de filtro en paralelo más init sin datos) o, si H9 falla, init reanudable desde el spool y ventana acordada con Basis. Añadir un criterio de aceptación de volumen al plan 06.

### C-14 · Límite por sistema SAP a nivel de run

El plan 04 §3 prevé un semáforo por petición HTTP compartido entre procesos mediante la BD: una ida y vuelta a la BD por página. Como cada run hace una petición a la vez, basta limitar los **runs simultáneos por sistema** al tomar trabajo, en la misma consulta que aplica el bloqueo por pipeline y el grupo de concurrencia (filtrar en el `SELECT`, no "tomar y devolver a la cola"). El semáforo en proceso actual se queda para la API (explorador y vista previa).

### C-15 · Logs en vivo

El plan 03 §2 publica cada línea por `pg_notify` (carga máxima de 8000 bytes y una conexión de escucha por réplica de API) y guarda un archivo JSONL en el disco del worker, que la API no puede leer cuando hay varias instancias. Cambio: `run_log` en tabla particionada por fecha (la retención es borrar particiones); el SSE de la API sondea con el cursor `after` cada segundo, que cumple el criterio de menos de 2 s; `NOTIFY` solo como aviso sin carga; se elimina el archivo JSONL.

### C-16 · Backfill restringido

El plan 08 §5 permite un `FULL` con `replace` "sin tocar la suscripción de delta". Solo es correcto con imagen posterior y `upsert`. En orígenes aditivos duplica importes, y en los extractores LO el `FULL` lee las *setup tables*. Cambio: permitirlo solo con `delta_semantics = after_image`; en el resto, exigir reinicialización.

### C-17 · Suscripción estancada

Un pipeline delta pausado o roto durante días hace crecer la cola en SAP y puede perder la posibilidad de repetir (H13). Cambio: regla de alerta "suscripción sin lectura en N días" en el plan 09 §3, aviso al pausar un pipeline delta y ampliar el *runbook* 4.

### C-18 · Ancla externa de la auditoría (P2)

La cadena y su ancla (`audit.chain_anchor`) viven en la misma base de datos: quien pueda escribir en ella puede recalcular la cadena entera. Cambio de bajo coste: firmar el hash de cabecera con una clave derivada de la clave maestra y emitirlo a diario a los logs técnicos; verificación por tramos desde el último punto verificado.

### C-19 · Asistente de pipeline en lugar de lienzo (decidido)

Un pipeline es un origen hacia un destino, y así se queda: el multi-destino se descarta (decisión 2) y un destino adicional es otro pipeline. Un lienzo de dos nodos no aporta nada sobre un asistente y cuesta React Flow, arrastre, accesibilidad por teclado y pruebas. Se sustituye por un asistente de pipeline (origen, destino, carga, programación, resumen); el detalle muestra origen y destino en la cabecera. React Flow sale del stack. Una vista de grafo solo se reconsideraría con las dependencias entre pipelines (plan 12).

### C-20 · Instancia única y flujo por entorno (decidido)

Decisión 3: una sola instancia productiva conecta con SAP DEV, QAS y PRD; la usan pocas personas capacitadas y casi todo corre de forma automática. `sap_system.environment` y `destination.environment` ya existen. El plan 11, que describía una instancia por entorno, se corrige. No hace falta promoción por exportación e importación; la exportación queda como respaldo.

Lo que se añade para que el flujo funcione igual en cada entorno:

1. **Coherencia de entorno.** Un pipeline cuyo origen y destino son de entornos distintos (por ejemplo SAP QAS hacia un destino PRD) exige confirmación explícita con motivo, auditada.
2. **Clonar a otro entorno.** La acción de clonar admite otro sistema SAP y otro destino: vuelve a leer el `$metadata` allí, muestra el diff de esquema contra el origen de partida y deja el pipeline en borrador, sin estado de delta.
3. **Comprobación antes de activar.** Prueba vigente de sistema y destino, servicio publicado en ese sistema, esquema sin diferencias incompatibles y ejecución de prueba correcta.
4. **Capacidad.** Límite de runs por sistema SAP (C-14) y un *pool* de workers reservado para PRD, para que las pruebas sobre DEV y QAS no retrasen las cargas productivas.
5. **Medición por sistema.** Los límites de Gateway, la retención de la ODQ y el rendimiento son de cada sistema: DEV no representa a PRD. La parte no invasiva del spike se repite en QAS y PRD antes de activar pipelines allí.
6. **Entorno a la vista.** Distintivo de entorno en listas y detalles, y filtro global por entorno.
7. **Etapas de la plataforma.** DEV y QA siguen existiendo como despliegues para validar versiones de la propia plataforma antes de PRD. No abren deltas sobre los servicios que usa la instancia productiva (mock o usuario técnico propio): competirían por la misma suscripción (H6, H18).

Riesgos aceptados con esta decisión: las credenciales de los tres entornos SAP viven en la misma base (cifradas), y una caída o un error de la instancia afecta a todos los entornos a la vez.

### C-21 · Stack del frontend

El plan 00 §5 fija React Hook Form, Zod y TanStack Table; `frontend/package.json` no los incluye y las pantallas de It1–It2 se hicieron sin ellos. Antes de los asistentes grandes hay que adoptar o retirar. Recomendado: adoptar React Hook Form y Zod en It3 (los formularios dinámicos de It4 los aprovechan) y TanStack Table solo si `shared/ui/data.tsx` se queda corto.

### C-22 · Definición de "Hecho" verificable

- Cobertura ≥ 80 %: CI ejecuta `pytest --cov` sin umbral. Añadir `fail_under` para `domain` y `application`.
- E2E del flujo principal: existe `frontend/tests/e2e/smoke.spec.ts`, pero CI no ejecuta Playwright. Añadir un job con `docker compose` y el mock.
- `axe` (plan 10 §5) no está instalado.
- Con dos personas creando migraciones: aserción explícita de *head* único de Alembic.
- Plan 10: las tareas de It0 siguen sin marcar aunque están hechas.

## 4. Inconsistencias entre planes

| Dónde | Inconsistencia | Resolución |
|---|---|---|
| 03 §2 y 06 §2 | Pasos del run: el 03 incluye `stage`; el 06 no | Una sola lista en el 06; el 03 la referencia |
| 05 §4 y 06 §7 | `read_watermark(desde, hasta)` frente a `read_window(field, start, end)` | Queda `read_window` |
| 06 §4 y 07 §4 | CSV "determinista por token" frente a patrón con `{run_id}` | `{batch_id}` (C-03) |
| 06 §1 y 08 §1 | Seis modos de run frente a tres `extraction_mode` de pipeline | Separar modo del pipeline y modo del run; `DELTA_REPEAT` no es un modo elegible, es un reintento |
| 01 §7 | Cita `0002-spike-sap-odp-odata.md`; el archivo es `0002-extraccion-sap-odp-odata.md` | Corregir la cita |
| 00 §2 | "Solo lectura sobre SAP" frente a terminar suscripciones | Matizar (C-02) |
| 06 §3.3 y 12 §2 | "Usar un pipeline con varios destinos (v1.1)" | Multi-destino descartado (decisión 2) |
| 11 §1 y 04 §1 | Instancia por entorno frente a `sap_system.environment` | Instancia productiva única (C-20) |
| 11 §6 | "Usuario técnico SAP con mínimo privilegio" | Solo lectura con autorización amplia (decisión 6) |
| ROADMAP | It1 e It2 con ✅ sin E2E en CI ni umbral de cobertura | C-22 |

## 5. Plan de ejecución con dos desarrolladores

### 5.1 Sobre el reparto propuesto

El reparto por paquetes (Dev A flujo SAP, Dev B destinos y plataforma), It3 en paralelo con It4 y la plantilla como trabajo flotante están bien. Cuatro ajustes:

1. **Dev A lleva sola toda la ruta crítica** (It3 → It5 → It6 → It8). Desde la fase 2, Dev B trabaja fuera de ella: el calendario es la suma del trabajo de A y el núcleo lo conoce una sola persona.
2. **El motor lo construye una persona mientras la otra hace Entra ID**, y las pruebas de caída llegan dos fases después, con la orquestación ya montada encima. Esas pruebas son criterio de aceptación de It5 (plan 06), no un trabajo posterior.
3. **It6 es la iteración más cargada** de backend y de pantallas a la vez; se puede partir.
4. **Entra ID no desbloquea nada hasta PRD.** Lo único urgente es pedir el *App registration* a IT.

### 5.2 Principios

- Contratos congelados antes de paralelizar (ADR 0006: `SourceConnector`, `DestinationWriter`, esquema, spool, run y suscripción).
- Los dos en el motor, partidos por el spool: uno extrae hasta el spool, el otro carga desde el spool.
- Primer dato cuanto antes y en el destino del primer caso real: CSV y Snowflake primero; SQL Server después (decisión 5).
- It6 e It8 se parten en backend y pantallas.
- Todo PR lo revisa la otra persona.

### 5.3 Fases

Duraciones estimadas a partir del tamaño de cada plan y de las 2 semanas por iteración del ROADMAP; se recalculan al cerrar la Fase 1 con la velocidad real.

| Fase | Duración | Dev A (flujo SAP) | Dev B (destinos y plataforma) | Entregable |
|---|---|---|---|---|
| **0 · Preparación** | 1 semana | Spike ampliado con el equipo SAP, ya (C-01): primero H6, H12 y H18; respuestas reales al mock; diseño del estado de delta (C-02) | C-07 a y d; C-22 (E2E y cobertura en CI); C-21; pedir la cuenta de pruebas de Snowflake y el *App registration* de Entra ID | Juntos, 1–2 días: ADR 0006 con C-03, C-06 y C-12 |
| **1 · Origen y destino** | 2–3 semanas | **It3 Orígenes** (`sources/`): `inspect`, esquema versionado, vista previa, asistente | **It4a Destinos** (`destinations/`): registro de plugins, CSV, **Snowflake**, tabla de control, `ddl-preview`, asistente | Origen y destino creados desde la UI |
| **2 · Motor** | 3 semanas | **It5, extracción** (`extraction/`): conectores ODP y CDS, paginación, EDM → Arrow, spool con manifiesto, máquina de delta, cierre de suscripción | **It5, carga**: pasos `load` y `commit_delta`, idempotencia por `batch_id`, *fencing*, inyección de fallos en cada paso, pantalla mínima de run (ejecutar ahora, log, estado de delta) | **H2 · Primer dato**: SAP → CSV y Snowflake con init, delta y repetición; pruebas de caída en verde |
| **3 · Orquestación** | 3 semanas | **It6 backend** (`orchestration/`): cola, scheduler, workers, reintentos, límites por sistema y grupo, acciones de pipeline, clonado entre entornos | **It6 pantallas**: asistente de pipeline, constructor de programación, lista, detalle e historial. Después, **It4b SQL Server** | **H3 · Autónoma** |
| **4 · Operación** | 2–3 semanas | **It8 backend** (`monitoring/`): reglas, canales, frescura, `/metrics`; prueba de volumen de 10 M de filas | **It7 Entra ID** (`identity/`); dashboard y pantallas de alertas | SSO, alertas y dashboard |
| **5 · Endurecimiento** | 2 semanas | **It9**, entre los dos: QA y PRD, SSO activo en PRD, backups probados, *runbooks*, revisión de seguridad, C-18 | | **H4 · v1.0** |
| Flotante | 1–2 semanas | — | **It1b Plantilla** (`shared/ui`, layout) cuando llegue; desplaza lo que B tenga en curso fuera de la ruta crítica | Layout definitivo y `/ui-kit` |

Total estimado: 13–15 semanas, más It1b.

### 5.4 Dependencias externas y qué hacer si fallan

| Dependencia | Pedir en | Si no llega a tiempo |
|---|---|---|
| Spike en el SAP real (equipo SAP y Basis) | Fase 0 | La Fase 2 se construye contra el mock, It5 no se da por cerrada y no se conecta a datos reales. Se adelantan It6 backend e It7 |
| *App registration* de Entra ID (IT) | Fase 0 | El SSO es condición para salir a PRD (decisión 7): sin él, QA sigue con login local y la fecha de v1.0 se mueve. Está en la ruta crítica de v1.0 aunque It7 se construya en la Fase 4 |
| Cuenta y base de pruebas de Snowflake | Fase 0 | Snowflake es el primer destino (decisión 5), así que está en la ruta crítica de H2. Mientras llega, Dev B construye CSV y adelanta SQL Server (contenedor local); H2 se demuestra en CSV y el primer caso real espera a Snowflake |
| Plantilla corporativa | — | Se llega a v1.0 con el layout provisional; It1b después |

### 5.5 Acuerdos de trabajo

- **Revisión cruzada.** En la Fase 2 cada uno revisa la mitad del otro: al terminar, los dos conocen el motor completo.
- **Migraciones.** Una sola cadena. Quien integra segundo hace *rebase* y renumera; CI falla con dos *heads*.
- **Contrato generado.** `backend/openapi.json` y `frontend/src/shared/api/schema.d.ts` no se resuelven a mano: *rebase* y regenerar.
- **Archivos que ambos tocan.** `identity/domain/permissions.py`, `audit/domain/model.py`, el registro de routers, la navegación y `shared/ui/index.ts`: cambios pequeños e integrados pronto, en PR aparte si hace falta.
- **`shared/ui`.** Lo mantiene Dev B. Dev A pide el componente; no lo crea dentro de `features/`.
- **Ramas.** `feature/*` de vida corta (3 días como máximo) contra `main`; lo que quede a medias se oculta tras permiso o ruta no enlazada.
- **Ritmo.** Sincronización diaria de 15 minutos y demo al cerrar cada fase.

## 6. Decisiones tomadas (19.11.2026)

| # | Pregunta | Decisión | Consecuencia en los planes |
|---|---|---|---|
| 1 | Fecha del spike con el equipo SAP | El equipo está disponible; empieza de inmediato | Fase 0. Primero H6, H12 y H18. Antes hay que ampliar el script del spike |
| 2 | C-19: lienzo editable en v1 o en v1.1 | No habrá multi-destino: un destino adicional es otro pipeline 1:1 | Asistente en lugar de lienzo y React Flow fuera del stack (C-19). Multi-destino fuera del roadmap (plan 12). Estado de delta por pipeline, sin tabla aparte (C-02). Dos pipelines delta sobre el mismo origen dependen de H18 |
| 3 | C-20: una instancia multi-entorno o una por entorno | Una instancia para todo; reforzar que el flujo funcione bien en cada entorno | C-20 revisado: coherencia de entorno, clonado entre entornos, comprobación antes de activar, capacidad reservada para PRD |
| 4 | C-06: eliminar la tabla `job` | Sí | La cola es el run en `queued`; intentos en `run_attempt` (planes 06 y 08) |
| 5 | Orden de los destinos | Snowflake es el primero | It4a = CSV y Snowflake; It4b = SQL Server en la Fase 3. La cuenta de pruebas de Snowflake se pide en la Fase 0 |
| 6 | C-07 e: orígenes con datos sensibles | Sin tratamiento especial; el usuario técnico necesita autorización amplia para extraer todo | C-07 e no se aplica. Riesgo aceptado: quien tenga permiso sobre orígenes ve en la vista previa cualquier dato que el usuario técnico pueda leer |
| 7 | Entra ID en la Fase 4 | De acuerdo para QA; PRD debe salir ya con SSO | It7 en la Fase 4. SSO activo es criterio de salida a PRD (planes 02 y 11) |

Lecturas que conviene confirmar, porque las respuestas no las dicen de forma literal:

- **Decisión 2.** Se entiende que, sin multi-destino, el lienzo no se construye ni en v1 ni en v1.1.
- **Decisiones 3 y 7 juntas.** Se entiende que hay una sola instancia productiva conectada a todos los entornos SAP, y además un despliegue de QA de la propia plataforma para validar versiones antes de PRD.

Lo que sigue dependiendo del spike: que dos pipelines delta convivan sobre el mismo origen (H6, H18), el cierre de suscripciones desde la plataforma (H12), la init sin datos (H9), la estrategia de cargas iniciales grandes (H8–H10) y el tratamiento de importes y fechas (H14, H15).
