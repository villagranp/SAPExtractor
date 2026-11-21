# 09 · Monitoreo y alertas

**Iteración:** 8 · **Depende de:** 03, 06, 08

> **Estado (21.11.2026):** construida contra el mock, sobre la It5 y la It6. Faltan el SSE (hoy, sondeo), la regla de seguridad, `run_metric_daily` y validar el criterio de 500 pipelines con volumen real. Detalle en [Lo construido](#lo-construido-en-la-iteración-8).

## Objetivo

Que cualquier usuario autorizado sepa **de un vistazo** si las extracciones están sanas, y que los responsables sean **avisados** cuando algo falla o se retrasa.

## 1. Dashboard principal

| Bloque | Contenido |
|---|---|
| KPIs (tarjetas) | Pipelines activos · Runs hoy (éxito / fallo / en curso) · Filas cargadas hoy · Pipelines en `broken` · Tasa de éxito 7 días |
| Ejecuciones en curso | Lista en vivo (SSE) con barra de progreso (páginas/filas), duración y botón cancelar |
| Línea de tiempo (Gantt) | Runs de las últimas 24 h por pipeline; detecta solapes y ventanas cargadas |
| Fallos recientes | Últimos errores con código, mensaje accionable y acceso directo al log |
| Frescura de datos | Por pipeline: "último dato cargado hace X" vs SLA esperado (semáforo) |
| Por sistema SAP | Estado de conexión, peticiones/min, latencia media |
| Workers | Activos, ocupados, último latido |

Filtros globales: entorno, sistema SAP, etiqueta, propietario.

## 2. Historial y detalle de ejecución

- **Historial**: tabla filtrable (pipeline, estado, modo, disparador, fechas), exportable.
- **Detalle de run**: cabecera (estado, modo, token usado/obtenido abreviado, quién lo lanzó), intentos del run (worker, duración, error), pasos con duración, métricas (filas leídas/insertadas/actualizadas/borradas, MB, filas/seg), logs en vivo con filtro por nivel, botones *Reintentar* / *Cancelar*.
- **Tendencias por pipeline**: duración y volumen por ejecución (detecta degradaciones y cargas anómalas, p. ej. un delta que trae 0 filas varios días seguidos).

## 3. Alertas

### Reglas

| Regla | Disparador | Defecto |
|---|---|---|
| Run fallido | Run `failed` tras agotar reintentos | Activa |
| Pipeline roto | `delta_state = BROKEN` o esquema incompatible | Activa |
| Retraso / SLA | Sin carga exitosa en X minutos (frescura) | Configurable por pipeline |
| Duración anómala | Run > N × mediana de los últimos 10 | Configurable |
| Volumen anómalo | 0 filas en N deltas seguidos, o > N × lo habitual | Configurable |
| Suscripción estancada | Pipeline delta sin lectura confirmada en N días (pausado, roto o sin programar): la cola crece en SAP y puede perderse la posibilidad de repetir. N se fija con la retención real de la ODQ (H13) | Activa |
| Suscripción sin cerrar | `delta_state.sap_close_pending` durante más de N días (plan 06 §3.5) | Activa |
| Conexión caída | Prueba periódica de sistema SAP / destino falla | Activa |
| Infraestructura | Sin workers vivos, scheduler sin líder, disco de spool > 85 % | Activa |
| Seguridad | Múltiples logins fallidos, verificación de auditoría fallida, cambio en IdP | Activa (solo admins) |

### Canales

| Canal | Implementación |
|---|---|
| Correo | SMTP (TLS) o Microsoft Graph `sendMail` con app registration |
| Microsoft Teams | Webhook de canal / Workflows (Power Automate) |
| Webhook genérico | POST JSON firmado (HMAC) para integrarse con otras herramientas (ITSM, etc.) |
| En la aplicación | Campana de notificaciones con no leídas |

### Gestión

- **Suscripciones**: por pipeline, por etiqueta o global; por usuario o por lista de distribución.
- **Anti-ruido**: agrupación (un aviso por incidente, no por reintento), *cooldown*, aviso de **recuperación** cuando vuelve a funcionar.
- Plantillas de mensaje con enlace directo al run. Los avisos **no llevan filas de datos de SAP**: indican el error, la clave y la columna afectadas, y la muestra se consulta en la plataforma, bajo sus permisos. El correo y Teams quedan fuera del control de acceso de la plataforma.
- Las URL de *webhook* (Teams y genérico) se validan contra una lista de hosts permitidos, igual que las conexiones SAP (plan 04 §3): las escribe un usuario y el servidor hace la petición.
- Estado de alerta: `open` → `acknowledged` (con usuario) → `resolved`.

## 4. Métricas técnicas y salud

- `/metrics` en formato **Prometheus** (protegido): runs por estado, duración, filas, latencia SAP, cola pendiente, workers vivos.
- `/health/live` y `/health/ready` para el orquestador de contenedores.
- (v1.1) Trazas OpenTelemetry hacia el colector corporativo.

## 5. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `alert_rule` | `id`, `type`, `scope` (global / pipeline / tag), `params` (JSON), `severity`, `enabled` |
| `notification_channel` | `id`, `type` (`email` \| `teams` \| `webhook`), `config`, `secret_id`, `enabled` |
| `alert_subscription` | `rule_id`, `channel_id`, `recipients` |
| `alert` | `id`, `rule_id`, `resource`, `status`, `first_seen_at`, `last_seen_at`, `count`, `acknowledged_by`, `resolved_at` |
| `notification` | Envíos realizados (canal, resultado, error) |
| `run_metric_daily` | Agregados diarios para tendencias (retención larga) |

## 6. API (resumen)

```
GET  /api/v1/dashboard/summary
GET  /api/v1/dashboard/timeline?from=&to=
GET  /api/v1/dashboard/freshness
GET  /api/v1/events/stream                 # SSE: runs, alertas, workers
GET  /api/v1/alerts?status=   POST /api/v1/alerts/{id}/ack | /resolve
CRUD /api/v1/alert-rules  /api/v1/notification-channels  /api/v1/alert-subscriptions
POST /api/v1/notification-channels/{id}/test
GET  /metrics
```

## Tareas

- [x] Endpoints de dashboard con consultas agregadas (índices en `run.finished_at` y `(pipeline_id, finished_at)`; sin vistas materializadas por ahora).
- [ ] SSE global de eventos. Sustituido por sondeo (panel cada 5 s, campana cada 30 s); queda para cuando el sondeo no baste.
- [x] Motor de reglas de alerta, evaluado por el scheduler líder cada `SX_ALERT_EVAL_SECONDS`. Sin eventos del worker: el estado de la base basta y no duplica la lógica.
- [x] Canales correo (SMTP), Teams y webhook con prueba de envío y validación de hosts permitidos (`SX_ALERT_ALLOWED_HOSTS`).
- [x] Reglas de suscripción estancada y de suscripción sin cerrar (7 y 3 días por defecto, a la espera de la retención real de la ODQ, H13).
- [x] Agrupación, cooldown y notificación de recuperación.
- [x] `/metrics` Prometheus (en `/api/v1/metrics`, con token).
- [x] Pantallas: panel y alertas (abiertas, historial, reglas, canales). El historial y el detalle de run son de la It5.
- [ ] Regla de seguridad (logins fallidos, verificación de auditoría, cambio en IdP) y reglas de infraestructura de scheduler sin líder y disco de spool.
- [ ] `run_metric_daily` y tendencias por pipeline.
- [ ] Prueba de carga del panel con 500 pipelines y 30 días de historial.

## Criterios de aceptación

- Un fallo definitivo genera **un** aviso en el canal configurado en < 1 min con enlace al run, y un aviso de recuperación cuando vuelve a ir bien.
- El dashboard carga en < 2 s con 500 pipelines y 30 días de historial.
- El progreso de un run en curso se actualiza en pantalla sin recargar.

## Lo construido en la iteración 8

Construida el 21.11.2026 contra el mock, sobre la It5 y la It6. Código: `backend/src/sapextract/monitoring/` y `frontend/src/features/monitoring/`. Migración `0010_monitoring`. Pruebas: `tests/unit/test_monitoring_domain.py`, `tests/integration/test_monitoring.py` y `features/monitoring/pages/MonitoringPages.test.tsx`.

**Evaluación.** `AlertEngine` (`application/evaluator.py`) corre en el scheduler líder cada `SX_ALERT_EVAL_SECONDS` (20 s): recorre las reglas activas, calcula los hallazgos a partir del estado de la base y los concilia con las alertas vivas. Hay **una alerta por incidente** (índice único parcial por regla y recurso mientras está `open` o `acknowledged`): si persiste, sube `count` y `last_seen_at`; si desaparece, se resuelve sola y se envía el aviso de recuperación. Una regla que vuelve a dispararse antes de `SX_ALERT_COOLDOWN_MINUTES` (10) abre la alerta sin avisar (`notified = false`). Un fallo de evaluación se registra (`alert_evaluation_failed`) y no tumba al scheduler.

| Regla | Hallazgo | Parámetros (defecto) |
|---|---|---|
| `run_failed` | Último run terminado del pipeline en `failed`; cuenta los fallos seguidos, sin contar los cancelados | — |
| `delta_broken` | `delta_state = BROKEN` | — |
| `freshness` | Pipeline activo sin run correcto en `minutes`; sin ninguno, cuenta desde su activación | `minutes` (240) |
| `duration_anomaly` | Último run > `factor` × mediana de los `samples` anteriores | `factor` (3), `samples` (5) |
| `volume_anomaly` | `zero_runs` runs delta seguidos sin filas (no aplica a `FULL`) | `zero_runs` (3) |
| `stale_subscription` | Pipeline delta sin lectura confirmada en `days` | `days` (7) |
| `unclosed_subscription` | `sap_close_pending` durante más de `days` | `days` (3) |
| `connection_down` | Última prueba fallida de un sistema SAP o de un destino | — |
| `no_workers` | Runs en cola y ningún worker vivo con ejecutor | — |

Las reglas de pipeline admiten ámbito global, por pipeline (código) o por etiqueta; las de conexión y workers, solo global. La migración crea seis reglas globales por defecto: run fallido, delta roto, suscripción estancada, suscripción sin cerrar, conexión caída y sin workers. La frescura, la duración y el volumen se crean desde la pantalla.

**Avisos.** `Notifier` envía a cada canal activo suscrito a la regla (o a todas) con gravedad mínima, una vez por canal, y guarda cada envío en `notification` con su resultado. El mensaje lleva regla, recurso, gravedad, el texto de la alerta y un enlace a la plataforma (`SX_PUBLIC_URL`), nunca datos de SAP.

| Canal | Envío | Configuración |
|---|---|---|
| Correo | SMTP con STARTTLS (`SX_SMTP_*`) | Lista de destinatarios en el canal |
| Teams | POST `{"text": …}` al webhook del canal | La URL es secreto cifrado: no vuelve en la API |
| Webhook | POST JSON con `X-SX-Signature: sha256=…` (HMAC) | URL en claro y secreto HMAC opcional (≥ 16 caracteres), cifrado |

Las URL de Teams y webhook pasan por la política de hosts de `SX_ALERT_ALLOWED_HOSTS` y exigen `https` fuera de dev y test. «Enviar prueba» guarda el resultado en el canal y en auditoría.

**Panel.** `GET /dashboard/summary` (KPIs de 24 h, tasa de éxito de 7 días, runs en curso con paso y progreso desde `run_log`, fallos recientes, workers, sistemas SAP), `/timeline?hours=` (hasta 168) y `/freshness` (semáforo con el SLA de la regla de frescura más específica: pipeline, después etiqueta, después global). Filtros por entorno, sistema SAP y etiqueta; la pantalla ofrece entorno y etiqueta.

**Permisos y auditoría.** Nuevo `alert:ack` (operador y administrador de datos) para reconocer y cerrar; `alert:manage` para reglas, canales y suscripciones; `dashboard:read` para ver. Acciones nuevas: `alert.acknowledged`, `alert.resolved`, `alert_rule.*`, `notification_channel.created/updated/deleted/tested`, `alert_subscription.created/deleted`.

**Métricas.** `GET /api/v1/metrics` en texto de Prometheus, con `Authorization: Bearer` y `SX_METRICS_TOKEN`; sin token configurado responde 404. Fuera del OpenAPI. Series: `sx_runs{status}`, `sx_queue_pending`, `sx_run_duration_seconds_avg`, `sx_rows_extracted`, `sx_workers_alive`, `sx_workers_busy`, `sx_alerts_open{severity}`, `sx_pipelines_broken`.

**Diferencias con el diseño (decididas al implementar):**

- Sin SSE: el panel se refresca cada 5 s y la campana cada 30 s. Basta para el criterio «el progreso se actualiza sin recargar».
- La evaluación es solo por estado, en el scheduler; el worker no emite eventos. Con 20 s de intervalo el aviso de un fallo llega en < 1 min.
- Correo solo por SMTP; Microsoft Graph `sendMail` queda para cuando IT lo pida.
- Las suscripciones son de canal (no de usuario): una lista de distribución es un canal de correo con varios destinatarios. Al crear un canal, la pantalla lo suscribe a todas las reglas con la gravedad elegida.
- La campana muestra las alertas abiertas, no notificaciones no leídas por usuario.
- La duración anómala compara con la mediana de los 5 runs anteriores (configurable), no de 10. El volumen anómalo solo detecta ceros seguidos, no «> N × lo habitual».
- Por sistema SAP el panel muestra la última prueba y los runs en curso; peticiones por minuto y latencia esperan a `run_metric` (plan 03).
- `/metrics` vive bajo `/api/v1` para pasar por el mismo proxy que la API.
