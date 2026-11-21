# 08 · Orquestación y programación

**Iteración:** 6 · **Depende de:** 05, 06, 07 · **Bloquea a:** 09

> Actualizado el 19.11.2026 con los cambios C-06, C-14, C-16, C-17, C-19 y C-20 del [plan 13](13-revision-y-plan-de-cambios.md).
>
> **Estado (21.11.2026):** el backend de esta iteración está construido (Dev A), adelantado a la It5 como prevé el plan 13 §5.4 mientras el spike sigue pendiente. Las pantallas también están construidas (21.11.2026, ver [Pantallas](#pantallas-it6-21112026)). Falta lo que depende del motor. El detalle del backend está en [Lo construido](#lo-construido-it6-backend-21112026).

## Objetivo

Que un usuario defina de forma guiada qué se extrae, hacia dónde y **con qué frecuencia**, y que la plataforma lo ejecute de forma confiable: programación, cola de trabajos, concurrencia, reintentos, pausas y ejecución manual.

Un pipeline es siempre **un origen hacia un destino**. No hay multi-destino: si hace falta otro destino, se crea otro pipeline.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `pipeline` | `id`, `code`, `name`, `description`, `source_id`, `destination_id`, `extraction_mode` (`FULL` \| `DELTA` \| `WATERMARK`), `load_options` (JSON, ver plan 07), `filters`, `column_mapping`, `priority` (1–5), `timeout_minutes`, `retry_policy` (máx. intentos, backoff), `concurrency_group`, `worker_pool`, `log_level`, `status` (`draft` \| `active` \| `paused`), `tags`, `owner_id` |
| `schedule` | `id`, `pipeline_id`, `type` (`cron` \| `interval` \| `manual_only`), `cron_expression`, `interval_minutes`, `timezone` (por defecto la zona horaria global de la plataforma, configurable en Ajustes), `start_at`, `end_at`, `blackout_windows` (JSON), `enabled`, `next_fire_at`, `last_fire_at`, `misfire_policy` (`skip` \| `run_once`) |
| `run` | (plan 06) + `trigger` (`schedule` \| `manual` \| `api` \| `dependency`), `triggered_by`, `priority`, `available_at`, `claimed_by`, `claimed_at`, `heartbeat_at`, `lease_epoch`, `status` (`queued` \| `running` \| `succeeded` \| `failed` \| `cancelled`) |
| `run_attempt` | `run_id`, `number`, `worker_id`, `started_at`, `finished_at`, `outcome`, `error_code`, `lease_epoch`, `has_spool` |
| `worker` | `id`, `hostname`, `pid`, `pools`, `started_at`, `heartbeat_at`, `status`, `current_run_id` |
| `concurrency_group` | `code`, `max_parallel` (p. ej. limitar a 2 extracciones simultáneas contra PRD en horario laboral) |

No existe tabla `job`: la cola son los runs en `queued`. Un reintento es un intento nuevo del mismo run, no un run nuevo. El estado "roto" de un pipeline no se guarda: se deriva de `delta_state.status = BROKEN` (plan 06).

`sap_system` añade `max_concurrent_runs` (límite de runs simultáneos contra ese sistema).

## 2. Scheduler

- Proceso `scheduler` con **un único líder** (`pg_try_advisory_lock`); réplicas en espera toman el relevo si el líder cae.
- Bucle cada ~5 s:
  1. Busca `schedule.next_fire_at <= now()` y `enabled`.
  2. Respeta ventanas de exclusión (*blackout*: p. ej. cierre contable, ventana de backup de SAP).
  3. Si el pipeline ya tiene un run en cola o en curso → aplica política (`skip` por defecto, registra "ejecución omitida").
  4. Inserta un `run` en `queued` y calcula el siguiente `next_fire_at` con `croniter` en la zona horaria del schedule (maneja cambios de horario).
  5. Al arrancar tras una caída: `misfire_policy` decide si lanzar una única ejecución de recuperación o saltar.
- Detecta intentos sin latido: cierra el intento como `stale` y devuelve el run a `queued` (el *fencing* del plan 06 §3.3 impide que el worker anterior confirme nada).
- Tareas internas del sistema también programadas aquí: retención de logs (plan 03), verificación de integridad de auditoría, refresco de catálogos SAP.

## 3. Cola y workers

- Toma de trabajo sobre `run`, en una sola consulta que ya aplica los límites:

  ```sql
  SELECT … FROM run r
  WHERE r.status = 'queued' AND r.available_at <= now()
    AND pipeline.worker_pool = ANY(:pools)
    AND NOT EXISTS (run en curso del mismo pipeline)
    AND runs en curso del sistema SAP   < sap_system.max_concurrent_runs
    AND runs en curso del grupo         < concurrency_group.max_parallel
  ORDER BY r.priority, r.available_at
  FOR UPDATE SKIP LOCKED LIMIT 1
  ```

  Los runs sin hueco se quedan en la cola; no se toman para devolverlos después.
- **Bloqueo por pipeline** (nunca dos runs en curso del mismo pipeline: obligatorio para el delta), garantizado además por un índice único parcial.
- **Límites por sistema SAP y por grupo**: la comprobación se serializa con un *advisory lock* por sistema dentro de la transacción de toma, para que dos workers no superen el límite a la vez. Como cada run hace una petición a SAP a la vez, limitar runs basta; el semáforo por petición del cliente OData queda solo para la API (explorador y vista previa).
- Al tomar el run, el worker incrementa `lease_epoch` y crea el `run_attempt`.
- **Pools de workers** (p. ej. `default`, `heavy`, `prd`) asignables por pipeline. Con una sola instancia para todos los entornos SAP, se reserva un pool para los pipelines de PRD: las pruebas sobre DEV y QAS no retrasan las cargas productivas.
- Apagado ordenado: al recibir SIGTERM, el worker termina la página actual, cierra el intento como interrumpido y devuelve el run a `queued`.
- Notificación inmediata a workers ociosos por `LISTEN/NOTIFY` (sin esperar al sondeo).

## 4. Reintentos

- Política por pipeline: `max_attempts` (defecto 3), backoff exponencial (1, 5, 15 min) con jitter. Cada reintento es un `run_attempt` nuevo del mismo run.
- Solo errores **transitorios** (plan 06 §8). Errores de configuración/datos/delta no se reintentan.
- Al agotar intentos: run `failed`, alerta (plan 09).
- El reintento reutiliza el spool si lo toma el worker que lo tiene (`has_spool`); si lo toma otro, vuelve a extraer con el mismo token de partida (plan 06 §2).
- **Reintento manual** desde la UI (permiso `pipeline:run`): añade un intento al run fallido.

## 5. Acciones sobre pipelines

| Acción | Permiso | Efecto |
|---|---|---|
| Ejecutar ahora | `pipeline:run` | Run `manual` con prioridad alta; permite elegir modo (p. ej. `FULL` puntual) si el usuario tiene permiso |
| Pausar / reanudar | `pipeline:pause` | Suspende schedules; el run en curso termina. En un pipeline delta, avisa de que la cola sigue creciendo en SAP y de que una pausa larga puede obligar a reinicializar |
| Cancelar run | `pipeline:run` | Cancelación cooperativa |
| Reinicializar delta | `pipeline:reset_delta` | Ver plan 06 §3.3; cierra la suscripción anterior en SAP |
| Clonar | `pipeline:manage` | Copia sin estado de delta. Admite otro sistema SAP y otro destino (pasar de DEV a QAS o a PRD): vuelve a leer el `$metadata` en el sistema elegido, muestra el diff de esquema contra el origen de partida y deja el pipeline en `draft` |
| Backfill / recarga completa | `pipeline:manage` | Run `FULL` puntual con `replace` sin tocar la suscripción de delta. Solo si el origen es `delta_semantics = after_image`: en orígenes aditivos duplicaría importes y en los extractores LO el `FULL` lee las *setup tables*. En el resto hay que reinicializar |
| Activar | `pipeline:manage` | Requiere la comprobación previa (abajo) |
| Eliminar | `pipeline:manage` | Cierra la suscripción de delta en SAP (plan 06 §3.5) |

### Comprobación antes de activar

La misma en cualquier entorno, para que un pipeline validado en DEV se comporte igual en QAS y en PRD:

1. Prueba vigente del sistema SAP y del destino.
2. El servicio está publicado en ese sistema y el esquema no tiene diferencias incompatibles con el del origen del que se clonó.
3. Ejecución de prueba correcta (`test-run`).
4. Si el entorno del sistema SAP y el del destino no coinciden (por ejemplo SAP QAS hacia un destino PRD), confirmación explícita con motivo, auditada.

## 6. Asistente de pipeline

Sustituye al lienzo previsto inicialmente: con un origen y un destino por pipeline, un lienzo no aporta nada sobre un asistente (detalle UX en plan 10).

- **Pasos**: origen → destino → carga (modo, claves, mapeo de columnas, filtros) → programación → resumen. El detalle del pipeline muestra origen y destino en la cabecera, con su entorno.
- **Programación** en lenguaje natural + avanzado:
  - Selector simple: "Cada N minutos/horas", "Diario a las HH:MM", "Días laborables", "Mensual día N".
  - Modo experto: expresión cron con validación y **vista previa de las próximas 10 ejecuciones** en la zona horaria elegida.
  - Ventanas de exclusión en calendario visual.
- **Validación en vivo**: avisos (p. ej. `upsert` sin clave, origen aditivo con `upsert`, CSV con `upsert`, origen sin delta en modo `DELTA`, segundo pipeline delta sobre un origen que ya tiene uno, entornos distintos en origen y destino).
- **Simulación**: "Probar pipeline" ejecuta con `$top` limitado hacia un destino de prueba sin crear suscripción.

### Dependencias (v1.1)

- Encadenar pipelines: "ejecutar B cuando A termine con éxito" (`trigger = dependency`).
- Evolución a DAG completo en plan 12.

## 7. API (resumen)

```
GET    /api/v1/pipelines?status=&source=&destination=&environment=&tag=
POST   /api/v1/pipelines
GET    /api/v1/pipelines/{id}    PATCH …   DELETE …
POST   /api/v1/pipelines/{id}/validate
POST   /api/v1/pipelines/{id}/activate | /pause | /resume
POST   /api/v1/pipelines/{id}/clone        {sap_system_id?, destination_id?}
POST   /api/v1/pipelines/{id}/run          {mode?, reason?}
POST   /api/v1/pipelines/{id}/test-run
POST   /api/v1/pipelines/{id}/delta/reset  {reason, truncate_destination}
GET    /api/v1/pipelines/{id}/delta-state
GET    /api/v1/pipelines/{id}/schedules    PUT …
POST   /api/v1/schedules/preview           {cron, timezone} → próximas fechas
GET    /api/v1/runs?pipeline=&status=&from=&to=
GET    /api/v1/runs/{id}                   # incluye sus intentos
POST   /api/v1/runs/{id}/cancel | /retry
GET    /api/v1/workers
GET    /api/v1/concurrency-groups   PUT …
```

## Tareas

- [x] Modelos `pipeline`, `schedule`, `worker`, `concurrency_group`; tablas `run` y `run_attempt` (migración `0008_orchestration`; `run_step` y `delta_state` quedan para la It5).
- [x] Scheduler con liderazgo, blackout, misfire y detección de intentos sin latido. Las tareas diarias de sistema (particiones, retención del log de ejecución, verificación de la cadena de auditoría y refresco de catálogos SAP) corren desde el 21.11.2026; ver [Tareas de sistema](#tareas-de-sistema-del-scheduler-21112026).
- [x] Cola `SKIP LOCKED` sobre `run` + LISTEN/NOTIFY + límites por pipeline, grupo y sistema en la consulta de toma.
- [x] Workers con pools, heartbeat y apagado ordenado.
- [x] Reintentos con backoff y clasificación de errores.
- [x] Acciones de pipeline con permisos y auditoría, incluido el clonado hacia otro entorno. Reinicializar delta, el estado de delta y el cierre de la suscripción al eliminar llegaron con el motor (It5); `test-run`, el 21.11.2026 (ver [Probar pipeline](#probar-pipeline-21112026)).
- [x] Comprobación antes de activar y aviso de entornos cruzados, con la ejecución de prueba como punto obligatorio desde el 21.11.2026.
- [x] API de vista previa de cron.
- [x] Asistente de pipeline, constructor de programación y validaciones en vivo (con plan 10). Ver [Pantallas](#pantallas-it6-21112026).
- [x] Pruebas: concurrencia (dos workers no toman el mismo run ni superan el límite por sistema), caída del líder, cambio de horario, blackout.

**Conexión con el motor (It5, 21.11.2026).** El worker ejecuta `ExtractionEngine` como `RunExecutor`. Se añadió un segundo puerto, `EngineHooks` (`orchestration/domain/ports.py`), que la orquestación llama en cuatro puntos: antes de encolar a mano (`check_run`: 409 si el delta está roto o tiene una suscripción sin cerrar), al terminar un run sin éxito en el worker, al recuperar runs caídos y al cancelar uno en cola (`run_finished`: suelta el estado de delta), y al borrar un pipeline (`pipeline_deleted`). `reap_stale` recibe el gancho como `on_final`. Sin motor registrado (`hooks=None`) la orquestación funciona igual. Detalle en el plan 06, «Lo construido en la iteración 5».

## Criterios de aceptación

- Un usuario crea un pipeline desde el asistente, programa "cada 30 min en días laborables" y ve las próximas ejecuciones.
- Con 3 workers y 20 pipelines, nunca hay dos runs simultáneos del mismo pipeline ni se superan los límites por sistema SAP.
- Matar el scheduler líder: otro toma el relevo en < 30 s sin duplicar ejecuciones.
- Un pipeline clonado de DEV a PRD muestra las diferencias de esquema y no se puede activar sin pasar la comprobación.

## Lo construido (It6 backend, 21.11.2026)

Módulo `backend/src/sapextract/orchestration/` con sus cuatro capas, migración `0008_orchestration` y los procesos `main_scheduler` y `main_worker`. Lo que sigue es lo que difiere del diseño de arriba o lo concreta.

### Frontera con el motor (It5)

La orquestación reparte el trabajo y lleva el arrendamiento; no sabe extraer. Entrega cada run a un `RunExecutor` (`orchestration/domain/ports.py`):

```python
class RunExecutor(Protocol):
    def execute(self, ctx: RunContext) -> None: ...
```

- `RunContext` lleva `run_id`, `pipeline_id`, `mode`, `attempt`, `lease_epoch`, `worker_id` y `stop_reason()`. El motor llama a `ctx.checkpoint()` entre páginas y entre pasos; lanza `RunStoppedError` si hay que parar (cancelación, apagado, plazo o *fencing*).
- Si `execute` termina sin error, el run queda `succeeded`. Si el motor ya lo marcó en la transacción de `commit_delta` (plan 06 §3.6), el worker lo reconoce y cierra el intento como correcto.
- `DataflowError` decide el reintento por su `ErrorKind`. Una excepción inesperada falla el run con `SX-RUN-UNEXPECTED` y solo guarda el tipo de la excepción, no su texto.
- El motor se registra en `main_worker.build_executor`. **Hasta entonces el worker arranca, da latido y no toma runs**: quedan en la cola (`worker.has_executor = false`, visible en `GET /workers`).
- Un pipeline `DELTA` encola runs en modo `DELTA`; el motor lo convierte en `DELTA_INIT` en su primer intento si el estado de delta no está inicializado. `batch_id`, `delta_generation`, `start_token` y la ventana son columnas de `run` que escribe el motor.

### Modelo

- `run` y `run_attempt` siguen el ADR 0006 §5. Además: `run.attempts_budget` (intentos fallidos que puede gastar), `cancel_requested_at/by`, `schedule_id`, `reason` y el último error (`error_code`, `error_kind`, `error_message`).
- **Un pipeline tiene como máximo un run vivo** (en cola o en curso), por índice único parcial. Es más estricto que "nunca dos en curso": un segundo run en cola no aporta nada a un delta.
- `schedule` añade `last_skip_at` y `last_skip_reason` (`blackout`, `misfire`, `already_live`): es el registro de "ejecución omitida".
- `pipeline` añade `cloned_from_id`, `cross_environment_reason`, `activated_at` y `status_reason`. `column_mapping` va en su columna; el resto de opciones de carga, en `load_options`.
- `worker.id` es un UUID nuevo en cada arranque; `has_executor` indica si puede tomar runs.
- `sap_system.max_concurrent_runs`: por defecto 2, de 1 a 16. Se edita en el paso «Conexión» del asistente de sistemas SAP y se ve en su ficha.

### Cola y workers

- La toma serializa con **un único *advisory lock*** (`CLAIM_LOCK`), no uno por sistema: el sistema SAP del candidato no se conoce antes de elegirlo y la toma dura milisegundos.
- Prioridad: 1 es la más urgente (por defecto 3). Un run manual entra con prioridad 1.
- Latido cada `SX_WORKER_HEARTBEAT_SECONDS` (15 s). Sin latido durante `SX_RUN_STALE_SECONDS` (90 s) el scheduler cierra el intento como `stale` y **sube `lease_epoch` en ese momento**, no solo en la siguiente toma: el worker anterior queda fuera de inmediato.
- Un intento `stale` gasta presupuesto de reintentos; uno `interrupted` (SIGTERM) no. Así un run que tumba al worker no se repite sin fin.
- El plazo `timeout_minutes` se mide por intento y cuenta como error transitorio (`SX-RUN-TIMEOUT`).
- Pools: `SX_WORKER_POOLS` (por defecto `["default"]`).
- **Una pausa retiene también lo que está en cola**: la toma solo elige runs de pipelines activos, así que un run en cola (o en espera de reintento) aguarda a que el pipeline se reanude. El reintento manual exige pipeline activo (`SX-PIPE-NOT-ACTIVE`), igual que el run manual. El run en curso termina.
- Un error inesperado del ejecutor se registra con su tipo y los marcos de la traza (archivo, línea, función), **sin el texto de la excepción**, que puede llevar datos o credenciales; tampoco se guarda en el run. Es la regla general de los logs (plan 03 §3): vale igual para el scheduler y el bucle del worker.

### Scheduler

- Tipos `cron` (cinco campos), `interval` y `manual_only`. El selector simple del asistente ("cada N minutos", "días laborables") lo traduce el frontend a cron o intervalo.
- Ventanas de exclusión semanales (pueden cruzar la medianoche) o entre dos fechas.
- Un disparo cuenta como atrasado a partir de `SX_SCHEDULER_MISFIRE_GRACE_SECONDS` (60 s). Con `skip` se omite; con `run_once` se lanza una sola ejecución de recuperación.
- Cambio de hora: la hora que no existe en primavera se lanza a la siguiente válida; la hora repetida de otoño no relanza un trabajo de hora fija, pero un trabajo horario corre cada hora real.
- Al activar, reanudar o cambiar la programación, el próximo disparo se recalcula desde ese momento: una pausa larga no lanza ejecuciones de golpe.

### Acciones y validación

- **Comprobación previa** (`POST /pipelines/{id}/validate` y al activar): configuración sin bloqueos, origen activo, prueba vigente del sistema SAP y del destino, servicio publicado (lee el `$metadata`), esquema guardado compatible con el de SAP, esquema compatible con el del pipeline de partida si es un clon en otro sistema, y motivo de al menos 10 caracteres si los entornos no coinciden. Desde el 21.11.2026 incluye la ejecución de prueba (`test_run`), entre la prueba del destino y el servicio publicado: tiene que haber una prueba correcta de la configuración actual.
- **Bloqueos de configuración**: modo de carga que el destino no admite, `DELTA` o `WATERMARK` sin soporte en el origen, `replace` en un pipeline incremental, `upsert` sobre un delta que no trae imágenes posteriores y segundo pipeline delta sobre el mismo origen (hasta H6 y H18). Un borrador se guarda con bloqueos; no se activa. Un pipeline activo o en pausa no admite un cambio que añada un bloqueo.
- `POST /pipelines/review` da los avisos de una combinación aún sin guardar, para la validación en vivo del asistente. No llama a SAP.
- **Clonado** a otro sistema SAP: reutiliza el origen equivalente de ese sistema (mismo servicio y entity set) o lo crea leyendo el `$metadata`; crearlo exige además `source:manage`. Devuelve el diff de esquema. La programación se copia.
- **Ejecutar ahora** solo sobre pipelines activos. `mode: FULL` sobre un pipeline incremental es la recarga completa: exige `pipeline:manage` y, en delta, un origen `after_image`.
- **Reintento manual**: solo la última ejecución del pipeline y solo si falló; añade un intento al mismo run.
- **Eliminar**: baja lógica, con el código libre de nuevo; no se permite con un run vivo. Un pipeline impide eliminar su origen (cualquier estado) y, si está activo, su destino y su sistema SAP.
- Auditoría: `pipeline.created`, `updated`, `deleted`, `cloned`, `activated`, `paused`, `resumed`; `schedule.updated`; `run.triggered_manual`, `cancelled`, `retried`; `concurrency_group.updated`, `deleted`. Los runs programados no se auditan: quedan en `run`.

### API (lo que cambia respecto al resumen del §7)

```
POST   /api/v1/pipelines/review                    # avisos sin guardar
POST   /api/v1/pipelines/{id}/validate | /activate  {cross_environment_reason?}
POST   /api/v1/pipelines/{id}/pause                {reason?} → {pipeline, warnings}
POST   /api/v1/pipelines/{id}/clone                {code, name?, sap_system_id?, destination_id?, source_code?}
PUT    /api/v1/pipelines/{id}/schedules            {schedules: [...]}   # sustituye todas (máx. 5)
POST   /api/v1/schedules/preview                   {type, cron_expression | interval_minutes, timezone?, count?}
GET    /api/v1/runs?pipeline=&status=&trigger=&from=&to=
PUT    /api/v1/concurrency-groups/{code}           DELETE …
```

`delta/reset` y el estado de delta (`GET /pipelines/{id}/delta`) son del motor (plan 06). `POST /pipelines/{id}/test-run` se añadió el 21.11.2026.

### Pruebas

- `tests/unit/test_orchestration_domain.py`: reintentos, avisos, cron, cambio de hora, exclusiones.
- `tests/integration/test_orchestration.py`: la API contra PostgreSQL, el Gateway simulado y un destino CSV.
- `tests/integration/test_orchestration_queue.py`: scheduler, cola y workers con un ejecutor de pruebas (`tests/support/orchestration.py`). Incluye el criterio de 3 workers y 20 pipelines y el relevo del líder.

El criterio del asistente («cada 30 min en días laborables» con sus próximas ejecuciones) lo cubre `PipelineWizardPage.test.tsx`. De punta a punta queda sin demostrar el del clon de DEV a PRD: en las pruebas el sistema SAP de PRD exige HTTPS, así que el clon se prueba de DEV a QAS.

## Pantallas (It6, 21.11.2026)

`frontend/src/features/pipelines/` y `frontend/src/features/runs/`, solo con componentes de `shared/ui` y tokens. Rutas: `/pipelines`, `/pipelines/new`, `/pipelines/:id`, `/pipelines/:id/edit`, `/runs` y `/runs/:id`, todas con `pipeline:read` (alta y edición, `pipeline:manage`). «Pipelines» y «Ejecuciones» dejan de verse como iteración futura en el carril.

### Lista y ficha de pipeline

- La lista muestra el recorrido (origen y destino con su entorno), el modo de extracción y de carga, el estado, la próxima ejecución y la última. Filtra por texto, estado y entorno SAP; admite `?destination=` y `?source=` (desde la ficha de un destino).
- La ficha abre con el **recorrido**: placa del origen, conducto con el modo de extracción y de carga, y placa del destino. Es el único elemento distintivo de estas pantallas; el conducto se ve en azul de aura cuando el pipeline está activo. Debajo, los avisos y bloqueos de la configuración.
- Pestañas: resumen, ejecuciones, delta (solo en pipelines incrementales), programación y actividad. La pestaña «Delta» es la del motor (plan 06): estado, token vigente, historial, reinicialización y confirmación de la suscripción que queda abierta en SAP.
- Acciones según permiso y estado: ejecutar ahora (con recarga completa si el pipeline es incremental y el usuario tiene `pipeline:manage`), comprobar y activar, pausar (con el aviso de la cola de delta), reanudar, editar, clonar y eliminar (escribiendo el código).
- **Comprobar y activar** lanza la comprobación previa al abrir el diálogo y enseña cada punto con su resultado. Si los entornos no coinciden pide el motivo (10 caracteres como mínimo) y solo entonces deja activar.
- **Clonar** pide el código y, si se quiere, otro sistema SAP y otro destino; al terminar abre la copia con el diff de esquema (columnas nuevas, eliminadas y cambiadas).

### Asistente

- Pasos: origen, destino, carga, programación, nombre y ejecución, y resumen. Es un paso más que en el §6: el código, el nombre, la prioridad, el plazo y los reintentos van en un paso propio, como en el asistente de orígenes. En un pipeline activo o en pausa no aparecen los pasos de origen y destino y el modo de extracción queda fijo.
- El recorrido se va completando arriba mientras se elige. La **validación en vivo** llama a `POST /pipelines/review` con cada combinación y muestra bloqueos y avisos bajo el recorrido.
- Los modos se proponen según lo elegido: el incremental que admita el origen, y el modo de carga que admita el destino (un delta con imágenes posteriores propone fusionar por clave; una carga completa, reemplazar).
- Carga: tabla o archivo de destino, qué hacer si SAP añade columnas o borra filas, columnas técnicas, mapeo de columnas (excluir y renombrar; las claves no se pueden excluir) y filtros propios o los del origen.
- Se guarda como borrador (`POST /pipelines` y, si hay programación, `PUT /pipelines/{id}/schedules`). Si la programación falla, el pipeline ya está guardado y la ficha lo explica.
- Botón «Probar» (desde el 21.11.2026): ver [Probar pipeline](#probar-pipeline-21112026).

### Constructor de programación

- «Con programación» o «Solo a mano» (sin programaciones). Hasta cinco programaciones por pipeline.
- **Con frases**: cada 5, 10, 15, 20 o 30 minutos; cada 1, 2, 3, 4, 6, 8 o 12 horas; una vez al día a una hora; una vez al mes; con los días de la semana que se marquen. Siempre se traduce a cron (`*/30 * * * 1-5`), alineado con el reloj. **Expresión cron** para lo demás. Una programación de tipo `interval` creada por API se conserva como «Intervalo fijo».
- Vista previa de las próximas 10 ejecuciones en la zona de la programación (`POST /schedules/preview`), con la frase que la describe y su cron.
- Zona horaria (por defecto la de la plataforma), política si una ejecución no se lanza a su hora y casilla para detenerla sin borrarla.
- Ventanas de exclusión semanales (pueden cruzar la medianoche) o entre dos fechas, con una banda de lunes a domingo que marca las horas excluidas. Las fechas de una ventana entre fechas se escriben en la hora del equipo.

### Ejecuciones

- Historial global con filtros de estado, quién la lanzó y fechas; `?pipeline=` lo acota a un pipeline. Se refresca solo mientras haya ejecuciones en cola o en curso.
- Detalle: error con su código y cómo seguir según su clase, resumen, tiempos, cifras del motor (filas extraídas, insertadas, actualizadas y borradas), lote y tokens, log en vivo, pasos e intentos. Cancelar (con motivo) si está en cola o en curso; reintentar si falló.
- **Aviso cuando ningún worker puede tomar ejecuciones** (`GET /workers`): sin workers en línea, o con workers sin el motor, la cola no avanza y la pantalla lo dice.

### Fusión con las pantallas del motor (21.11.2026)

La It5 llegó a `main` con unas pantallas mínimas de pipelines y ejecuciones (alta en un formulario, activar y ejecutar). Se unieron con estas: quedan el asistente, la programación, el clonado y las acciones de aquí, y de las del motor la pestaña Delta, los pasos, el log en vivo y las cifras de cada ejecución. El formulario de alta anterior se retiró. El historial de un pipeline filtra con `?pipeline=`: las pantallas del motor enviaban `pipeline_id`, que la API ignora, y mostraban las ejecuciones de todos.

### Ajustes: grupos de concurrencia

`/admin/settings` (`features/settings/`, permiso `pipeline:read`; crear, cambiar y eliminar piden `pipeline:manage`). Lista cada grupo con su límite, cuántos pipelines lo usan y cuántas ejecuciones tiene en curso; el alta y la edición van en un diálogo y un grupo con pipelines no se puede eliminar (la API lo explica). Los workers se ven en el panel de monitoreo (plan 09), así que aquí solo hay un enlace.

### Probar pipeline (21.11.2026)

`POST /api/v1/pipelines/{id}/test-run` (permiso `pipeline:manage`, auditado como `pipeline.tested`). Es síncrono y no crea ningún run: lo ejecuta el motor (`extraction/application/trial.py`) en el proceso de la API.

| Paso | Qué hace | Qué no toca |
|---|---|---|
| `configuration` | Resuelve el pipeline igual que un run: sistema SAP y destino activos, esquema, filtros y opciones de carga | — |
| `read` | Lee de SAP las primeras filas (`SX_TEST_RUN_ROWS`, 100 por defecto) con `$top` y los filtros del pipeline, **sin** `Prefer: odata.track-changes`, y las pasa por el conversor y el spool | No abre suscripción en la ODQ ni mueve el estado de delta |
| `load` | Carga la muestra en un destino de prueba `SXTEST_<8 hex>_<nombre>` con el modo de carga y el mapeo del pipeline (`prepare`, `stage`, `apply`) | La tabla o carpeta real |
| `cleanup` | Borra el destino de prueba y sus filas de control (`DestinationWriter.drop_test_target`), también si la carga falló | Solo acepta nombres `SXTEST_…`: con cualquier otro se niega |

- La respuesta trae cada paso con su estado, su texto y, si falló, el código de error. Un fallo al borrar el destino de prueba se avisa pero no invalida la prueba.
- La lectura tiene un tope de `SX_TEST_RUN_TIMEOUT_SECONDS` (60 s) y usa un conector con ese timeout.
- El resultado queda en el pipeline (`last_test_at`, `last_test_ok`, `last_test_result`) junto con la **huella** de lo probado: origen, destino, modo, opciones de carga, mapeo y filtros efectivos (migración `0011_pipeline_test`). Si algo de eso cambia, la prueba deja de estar vigente (`test_outdated`); cambiar el nombre, la prioridad o la programación no la invalida. Guardar la prueba no sube la versión del pipeline.
- **Comprobación previa**: el punto `test_run` exige una prueba correcta y vigente. Un clon no hereda la prueba: hay que probarlo en su entorno.
- Excepción: si el origen tiene desactivada la lectura de muestra (`preview_enabled = false`, hipótesis H16: `$top` podría lanzar la extracción completa), la prueba se rechaza con `SX-PIPE-TEST-DISABLED` y la comprobación previa no la exige.
- Pantallas: botón «Probar» en el detalle (con `pipeline:manage`, en cualquier estado) y, en «Comprobar y activar», el botón «Probar pipeline» cuando falta la prueba; al terminar bien, la comprobación se repite sola.

### Tareas de sistema del scheduler (21.11.2026)

El líder lanza las tareas diarias en un hilo aparte (`MaintenanceThread`): una llamada lenta a SAP no retrasa la programación. Cada tarea guarda en `system_setting` (`maintenance.last_run`) cuándo terminó bien por última vez: un reinicio no la repite antes de 24 h y una que falla se reintenta en la siguiente pasada (cada hora). Una tarea que falla no impide las demás.

| Tarea | Qué hace |
|---|---|
| `partitions` | Crea las particiones mensuales de auditoría y del log de ejecución (3 meses por delante) |
| `run_log_retention` | Borra las particiones de `run_log` cuyo mes entero es anterior a `SX_RUN_LOG_RETENTION_DAYS` (90). Si borra algo, queda auditado como `system.retention_applied` (plan 03 §4) |
| `audit_verification` | Verifica la cadena de hash completa y guarda el resultado; deja `audit.verified` o `audit.verification_failed` con actor `scheduler` |
| `sap_catalogs` | Relee de SAP el catálogo de servicios de cada sistema activo; un sistema que no responde conserva su caché |

Código: `core/maintenance.py` (ejecutor), `extraction/application/maintenance.py` (retención), `SapExplorerService.refresh_all` y `main_scheduler.system_tasks`.

### Decisiones aceptadas (21.11.2026)

Estas cuatro diferencias con el diseño inicial se dan por buenas para seguir avanzando (decisión de Dev A); si más adelante hace falta cambiarlas, se revisan:

- **Un solo run vivo por pipeline** (en cola o en curso), no solo «nunca dos en curso».
- **Un único bloqueo de toma** de la cola, en lugar de uno por sistema SAP.
- **La pausa retiene también lo que está en cola**; el run en curso termina.
- **Paso «Nombre y ejecución» en el asistente**, además de los cinco del §6.

Los 18 textos de interfaz que coincidían en las dos versiones de las pantallas se quedan con la redacción de las pantallas de orquestación.

### Cierre de la iteración (21.11.2026)

Criterios de aceptación comprobados:

- Asistente, «cada 30 minutos en días laborables» y próximas ejecuciones: pruebas de pantalla y E2E.
- Sin dos runs simultáneos del mismo pipeline ni por encima del límite del sistema, y relevo del líder sin duplicar: `tests/integration/test_orchestration_queue.py`.
- **Clon de DEV a PRD**: `test_clone_from_dev_to_prd_is_tested_and_activated_there` usa un segundo Gateway simulado con TLS como sistema PRD (PRD exige HTTPS verificado). El clon queda en borrador, no hereda la prueba, no se activa sin pasar la comprobación y se activa tras probarlo en PRD.
- **E2E** (`frontend/tests/e2e/`) contra `docker compose` con el mock SAP: asistente → «Probar pipeline» → activar → «Ejecutar ahora» → worker → inicialización confirmada → pestaña Delta. 9 pruebas correctas, axe incluido.

### Pendiente

- Dependencias entre pipelines (`trigger = dependency`): v1.1.
- Hito H3 «Autónoma»: falta validar el motor contra el SAP real (spike, ADR 0002).
- Prueba de usabilidad del asistente con 2 o 3 usuarios (plan 10).
- Retención: la API `/settings/retention` y el archivado firmado de la auditoría siguen en el plan 03; hoy la retención del log se fija con `SX_RUN_LOG_RETENTION_DAYS`.
