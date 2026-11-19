# 09 · Monitoreo y alertas

**Iteración:** 8 · **Depende de:** 03, 06, 08

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

- [ ] Endpoints de dashboard con consultas agregadas eficientes (índices / vistas materializadas).
- [ ] SSE global de eventos.
- [ ] Motor de reglas de alerta (evaluación en scheduler + eventos del worker).
- [ ] Canales correo, Teams, webhook con prueba de envío y validación de hosts permitidos.
- [ ] Reglas de suscripción estancada y de suscripción sin cerrar.
- [ ] Agrupación, cooldown y notificación de recuperación.
- [ ] `/metrics` Prometheus.
- [ ] Pantallas: dashboard, historial, detalle de run, alertas, configuración de reglas/canales.

## Criterios de aceptación

- Un fallo definitivo genera **un** aviso en el canal configurado en < 1 min con enlace al run, y un aviso de recuperación cuando vuelve a ir bien.
- El dashboard carga en < 2 s con 500 pipelines y 30 días de historial.
- El progreso de un run en curso se actualiza en pantalla sin recargar.
