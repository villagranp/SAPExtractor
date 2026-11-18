# 08 · Orquestación y programación

**Iteración:** 6 · **Depende de:** 05, 06, 07 · **Bloquea a:** 09

## Objetivo

Que un usuario defina **visualmente** qué se extrae, hacia dónde y **con qué frecuencia**, y que la plataforma lo ejecute de forma confiable: programación, cola de trabajos, concurrencia, reintentos, pausas y ejecución manual.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `pipeline` | `id`, `code`, `name`, `description`, `source_id`, `destination_id`, `extraction_mode` (`FULL` \| `DELTA` \| `WATERMARK`), `load_options` (JSON, ver plan 07), `filters`, `column_mapping`, `priority` (1–5), `timeout_minutes`, `retry_policy` (máx. intentos, backoff), `concurrency_group`, `log_level`, `status` (`draft` \| `active` \| `paused` \| `broken`), `tags`, `owner_id` |
| `schedule` | `id`, `pipeline_id`, `type` (`cron` \| `interval` \| `manual_only`), `cron_expression`, `interval_minutes`, `timezone` (por defecto la zona horaria global de la plataforma, configurable en Ajustes), `start_at`, `end_at`, `blackout_windows` (JSON), `enabled`, `next_fire_at`, `last_fire_at`, `misfire_policy` (`skip` \| `run_once`) |
| `job` (cola) | `id`, `pipeline_id`, `run_id`, `trigger` (`schedule` \| `manual` \| `retry` \| `api` \| `dependency`), `priority`, `available_at`, `status` (`queued` \| `claimed` \| `done` \| `dead`), `claimed_by`, `claimed_at`, `attempt` |
| `run` | (plan 06) + `job_id`, `triggered_by`, `attempt`, `heartbeat_at` |
| `worker` | `id`, `hostname`, `pid`, `pools`, `started_at`, `heartbeat_at`, `status`, `current_run_id` |
| `concurrency_group` | `code`, `max_parallel` (p. ej. limitar a 2 extracciones simultáneas contra PRD en horario laboral) |

## 2. Scheduler

- Proceso `scheduler` con **un único líder** (`pg_try_advisory_lock`); réplicas en espera toman el relevo si el líder cae.
- Bucle cada ~5 s:
  1. Busca `schedule.next_fire_at <= now()` y `enabled`.
  2. Respeta ventanas de exclusión (*blackout*: p. ej. cierre contable, ventana de backup de SAP).
  3. Si el pipeline ya tiene un run activo → aplica política (`skip` por defecto, registra "ejecución omitida").
  4. Inserta `job` y calcula el siguiente `next_fire_at` con `croniter` en la zona horaria del schedule (maneja cambios de horario).
  5. Al arrancar tras una caída: `misfire_policy` decide si lanzar una única ejecución de recuperación o saltar.
- Tareas internas del sistema también programadas aquí: retención de logs (plan 03), detección de runs huérfanos, verificación de integridad de auditoría, refresco de catálogos SAP.

## 3. Cola y workers

- Toma de trabajos con `SELECT … FROM job WHERE status='queued' AND available_at<=now() ORDER BY priority, available_at FOR UPDATE SKIP LOCKED LIMIT 1`.
- Antes de ejecutar, el worker comprueba:
  - **Bloqueo por pipeline** (nunca dos runs del mismo pipeline: obligatorio para el delta).
  - **Límite del grupo de concurrencia** y del **sistema SAP** (`max_concurrent_requests`).
  - Si no hay hueco → devuelve el job a la cola con `available_at` + pequeño retraso.
- Pools de workers (p. ej. `default`, `heavy`) asignables por pipeline para separar cargas grandes.
- Apagado ordenado: al recibir SIGTERM, el worker termina la página actual, marca el run como interrumpido-reintentable y libera el job.
- Notificación inmediata a workers ociosos por `LISTEN/NOTIFY` (sin esperar al sondeo).

## 4. Reintentos

- Política por pipeline: `max_attempts` (defecto 3), backoff exponencial (1, 5, 15 min) con jitter.
- Solo errores **transitorios** (plan 06 §8). Errores de configuración/datos/delta no se reintentan.
- Al agotar intentos: run `failed`, job `dead`, alerta (plan 09).
- **Reintento manual** desde la UI (permiso `pipeline:run`), reutilizando spool si existe.

## 5. Acciones sobre pipelines

| Acción | Permiso | Efecto |
|---|---|---|
| Ejecutar ahora | `pipeline:run` | Job `manual` con prioridad alta; permite elegir modo (p. ej. `FULL` puntual) si el usuario tiene permiso |
| Pausar / reanudar | `pipeline:pause` | Suspende schedules; el run en curso termina |
| Cancelar run | `pipeline:run` | Cancelación cooperativa |
| Reinicializar delta | `pipeline:reset_delta` | Ver plan 06 §3.3 |
| Clonar | `pipeline:manage` | Copia sin estado de delta |
| Backfill / recarga completa | `pipeline:manage` | Run `FULL` puntual con `replace` sin tocar la suscripción de delta (si el origen lo permite) |
| Activar | `pipeline:manage` | Requiere prueba exitosa de origen y destino |

## 6. Diseñador visual de pipelines

Pantalla principal de la plataforma (detalle UX en plan 10):

- **Lienzo** (React Flow) con nodos **Origen SAP → (Filtros/Mapeo) → Destino**; arrastrar desde paneles laterales de orígenes y destinos.
- Panel de propiedades del nodo seleccionado.
- **Programación** en lenguaje natural + avanzado:
  - Selector simple: "Cada N minutos/horas", "Diario a las HH:MM", "Días laborables", "Mensual día N".
  - Modo experto: expresión cron con validación y **vista previa de las próximas 10 ejecuciones** en la zona horaria elegida.
  - Ventanas de exclusión en calendario visual.
- **Validación en vivo**: avisos (p. ej. `upsert` sin clave, extractor aditivo con `upsert`, CSV con `upsert`, origen sin delta en modo `DELTA`).
- **Simulación**: "Probar pipeline" ejecuta con `$top` limitado hacia un destino de prueba sin crear suscripción.

### Dependencias (v1.1)

- Encadenar pipelines: "ejecutar B cuando A termine con éxito" (`trigger = dependency`).
- Evolución a DAG completo en plan 12.

## 7. API (resumen)

```
GET    /api/v1/pipelines?status=&source=&destination=&tag=
POST   /api/v1/pipelines
GET    /api/v1/pipelines/{id}    PATCH …   DELETE …
POST   /api/v1/pipelines/{id}/validate
POST   /api/v1/pipelines/{id}/activate | /pause | /resume | /clone
POST   /api/v1/pipelines/{id}/run          {mode?, reason?}
POST   /api/v1/pipelines/{id}/test-run
POST   /api/v1/pipelines/{id}/delta/reset  {reason, truncate_destination}
GET    /api/v1/pipelines/{id}/delta-state
GET    /api/v1/pipelines/{id}/schedules    PUT …
POST   /api/v1/schedules/preview           {cron, timezone} → próximas fechas
GET    /api/v1/runs?pipeline=&status=&from=&to=
GET    /api/v1/runs/{id}
POST   /api/v1/runs/{id}/cancel | /retry
GET    /api/v1/workers
GET    /api/v1/concurrency-groups   PUT …
```

## Tareas

- [ ] Modelos `pipeline`, `schedule`, `job`, `worker`, `concurrency_group`.
- [ ] Scheduler con liderazgo, blackout, misfire y tareas de sistema.
- [ ] Cola `SKIP LOCKED` + LISTEN/NOTIFY + bloqueos por pipeline/grupo/sistema.
- [ ] Workers con pools, heartbeat y apagado ordenado.
- [ ] Reintentos con backoff y clasificación de errores.
- [ ] Acciones de pipeline con permisos y auditoría.
- [ ] API de vista previa de cron.
- [ ] Diseñador visual, constructor de programación y validaciones en vivo (con plan 10).
- [ ] Pruebas: concurrencia (dos workers no toman el mismo job), caída del líder, cambio de horario, blackout.

## Criterios de aceptación

- Un usuario crea un pipeline desde el lienzo, programa "cada 30 min en días laborables" y ve las próximas ejecuciones.
- Con 3 workers y 20 pipelines, nunca hay dos runs simultáneos del mismo pipeline ni se superan los límites por sistema SAP.
- Matar el scheduler líder: otro toma el relevo en < 30 s sin duplicar ejecuciones.
