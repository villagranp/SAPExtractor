# 08 · Orquestación y programación

**Iteración:** 6 · **Depende de:** 05, 06, 07 · **Bloquea a:** 09

> Actualizado el 19.11.2026 con los cambios C-06, C-14, C-16, C-17, C-19 y C-20 del [plan 13](13-revision-y-plan-de-cambios.md).

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

- [ ] Modelos `schedule`, `worker`, `concurrency_group`. Hechos en la It5: `pipeline` mínimo (sin `priority`, `concurrency_group`, `worker_pool`, `log_level`, `owner_id`), las columnas de cola de `run` y `run_attempt` (plan 06, «Lo construido en la iteración 5»).
- [ ] Ampliar en la It6 lo adelantado en la It5: toma con límites por sistema y grupo, *jitter* en los reintentos, reintento manual, pausa y activación con comprobaciones.
- [ ] Scheduler con liderazgo, blackout, misfire, detección de intentos sin latido y tareas de sistema.
- [ ] Cola `SKIP LOCKED` sobre `run` + LISTEN/NOTIFY + límites por pipeline, grupo y sistema en la consulta de toma.
- [ ] Workers con pools, heartbeat y apagado ordenado.
- [ ] Reintentos con backoff y clasificación de errores.
- [ ] Acciones de pipeline con permisos y auditoría, incluido el clonado hacia otro entorno.
- [ ] Comprobación antes de activar y aviso de entornos cruzados.
- [ ] API de vista previa de cron.
- [ ] Asistente de pipeline, constructor de programación y validaciones en vivo (con plan 10).
- [ ] Pruebas: concurrencia (dos workers no toman el mismo run ni superan el límite por sistema), caída del líder, cambio de horario, blackout.

## Criterios de aceptación

- Un usuario crea un pipeline desde el asistente, programa "cada 30 min en días laborables" y ve las próximas ejecuciones.
- Con 3 workers y 20 pipelines, nunca hay dos runs simultáneos del mismo pipeline ni se superan los límites por sistema SAP.
- Matar el scheduler líder: otro toma el relevo en < 30 s sin duplicar ejecuciones.
- Un pipeline clonado de DEV a PRD muestra las diferencias de esquema y no se puede activar sin pasar la comprobación.
