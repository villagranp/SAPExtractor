# 03 · Auditoría y logs

**Iteración:** 1 (base) y 5 (logs de ejecución completos, junto con el motor) · **Depende de:** 01 · **Bloquea a:** 02 y todos los módulos (todos emiten eventos)

## Objetivo

Trazabilidad completa y verificable de **quién hizo qué, cuándo, desde dónde y con qué resultado**, separando tres tipos de registro:

| Tipo | Para qué | Dónde se guarda | Quién lo consulta |
|---|---|---|---|
| **Auditoría** | Acciones de negocio y seguridad (login, cambios de configuración, ejecuciones manuales, reinicios de delta) | Tabla `audit_event` (append-only, encadenada por hash) | Auditor, Administrador |
| **Logs de ejecución** | Detalle de cada extracción/carga (pasos, páginas, filas, errores) | Tablas `run_log` + archivo por ejecución | Operador, Administrador de datos |
| **Logs técnicos** | Diagnóstico de la aplicación (excepciones, peticiones HTTP, rendimiento) | stdout JSON → archivo rotado / colector (Loki, ELK, Azure Monitor) | Soporte técnico |

## 1. Auditoría

### Modelo `audit_event`

| Campo | Descripción |
|---|---|
| `id` | UUID v7 |
| `occurred_at` | Marca de tiempo UTC |
| `actor_type` | `user` \| `api_token` \| `system` (scheduler, worker) |
| `actor_id`, `actor_name` | Identificador y nombre visible en el momento del evento |
| `action` | Código estable, p. ej. `pipeline.updated`, `auth.login_failed` |
| `category` | `auth`, `identity`, `config`, `execution`, `security`, `system` |
| `resource_type`, `resource_id`, `resource_name` | Objeto afectado |
| `outcome` | `success` \| `failure` \| `denied` |
| `changes` | JSON con diff `{campo: {antes, después}}`; secretos sustituidos por `"***"` |
| `context` | IP, user agent, `request_id`, `run_id` |
| `reason` | Motivo obligatorio en acciones críticas (p. ej. reinicio de delta) |
| `prev_hash`, `hash` | SHA-256 encadenado (`hash = sha256(prev_hash + contenido canónico)`) |

Garantías:

- **Solo inserción**: el usuario de BD de la aplicación no tiene `UPDATE`/`DELETE` sobre `audit_event` (permisos de PostgreSQL + *trigger* que lo impide).
- **Evidencia de manipulación**: verificación de la cadena de hash (`sapextract audit verify` y botón en la UI).
- La auditoría se escribe **en la misma transacción** que el cambio auditado (si el cambio falla, no queda evento de éxito; los intentos fallidos/denegados se registran en una transacción aparte).

### Catálogo inicial de eventos

| Categoría | Eventos |
|---|---|
| auth | `auth.login_succeeded`, `auth.login_failed`, `auth.logout`, `auth.mfa_enabled`, `auth.password_changed`, `auth.session_expired`, `auth.access_denied` |
| identity | `user.created`, `user.provisioned`, `user.updated`, `user.roles_changed`, `user.locked`, `user.unlocked`, `user.disabled`, `user.enabled`, `user.anonymized`, `role.created/updated/deleted`, `idp.updated`, `api_token.created/revoked`, `session.revoked` |
| config | `sap_connection.*`, `source.*`, `destination.*`, `pipeline.*`, `schedule.*`, `settings.updated` |
| execution | `run.triggered_manual`, `run.cancelled`, `run.retried`, `pipeline.paused`, `pipeline.resumed`, `delta.reset`, `delta.reinitialized` |
| security | `secret.rotated`, `secret.revealed` (si alguna vez se permite), `audit.exported`, `audit.verification_failed` |
| system | `system.started`, `migration.applied`, `scheduler.leader_changed` |

### Implementación

- Servicio `AuditService.record(...)` usado desde la capa de aplicación (nunca desde routers).
- Decorador/ayudante para calcular el diff entre el estado anterior y el nuevo de una entidad, con lista de campos sensibles a enmascarar declarada en cada modelo.
- Índices por `occurred_at`, `actor_id`, `resource_type+resource_id`, `action`.
- Particionado mensual de la tabla (PostgreSQL declarativo) para retención eficiente.

### Pantalla "Auditoría"

- Tabla con filtros: rango de fechas, actor, categoría, acción, recurso, resultado.
- Detalle con el diff visual (antes/después).
- Exportación CSV / JSON (genera a su vez un evento `audit.exported`).
- Indicador "Integridad verificada ✔ / ✖" con fecha de la última verificación.
- Desde cualquier recurso (usuario, pipeline, conexión) una pestaña **Actividad** que muestra su auditoría filtrada.

## 2. Logs de ejecución

Cada ejecución (`run`) de un pipeline genera:

| Elemento | Detalle |
|---|---|
| `run_step` | Pasos con inicio/fin/estado: `prepare`, `extract`, `stage`, `load`, `commit_delta`, `cleanup` |
| `run_log` | Líneas con `ts`, `level`, `step`, `message`, `data` (JSON). Nivel configurable por pipeline (INFO por defecto, DEBUG temporal) |
| `run_metric` | Filas leídas/escritas/actualizadas/borradas, páginas, bytes, duración por paso, filas/seg |
| Archivo de log | `logs/runs/<yyyy>/<mm>/<run_id>.jsonl` para ejecuciones muy verbosas (la UI lo lee bajo demanda) |

- **En vivo**: el worker publica (`pg_notify`) y la API reenvía por **SSE** a la pantalla de detalle de la ejecución.
- Errores con **código propio** (`SX-SAP-401`, `SX-SAP-DELTA-INVALID`, `SX-DST-SQL-MERGE`…) y mensaje accionable ("Revise las credenciales de la conexión X").

## 3. Logs técnicos

- structlog en JSON a stdout (12-factor); en despliegues sin colector, `RotatingFileHandler` en `logs/app/`.
- Campos estándar: `ts`, `level`, `logger`, `event`, `request_id`, `user_id`, `run_id`, `module`, `duration_ms`.
- **Enmascarado** obligatorio de: contraseñas, secretos, tokens, cabeceras `Authorization`/`Cookie`, cadenas de conexión.
- Log de acceso HTTP (método, ruta, estado, duración) sin cuerpos.
- OpenTelemetry (trazas) activable por configuración para v1.1.

## 4. Retención y depuración

| Registro | Retención por defecto | Configurable |
|---|---|---|
| Auditoría | 5 años (se archiva a CSV/Parquet firmado antes de borrar particiones) | Sí, con mínimo de 1 año |
| Logs de ejecución | 90 días | Sí |
| Métricas de ejecución | 2 años (agregadas) | Sí |
| Logs técnicos | 30 días | Sí |

Tarea de mantenimiento diaria ejecutada por el scheduler (auditada como `system.retention_applied`).

## 5. API (resumen)

```
GET  /api/v1/audit-events?from=&to=&actor=&action=&category=&resource_type=&resource_id=&outcome=
GET  /api/v1/audit-events/{id}
POST /api/v1/audit-events/export          # asíncrono para volúmenes grandes
POST /api/v1/audit-events/verify
GET  /api/v1/runs/{id}/logs?level=&step=&after=
GET  /api/v1/runs/{id}/logs/stream        # SSE
GET  /api/v1/settings/retention    PUT /api/v1/settings/retention
```

## Tareas

**Iteración 1**
- [ ] Tabla `audit_event` particionada + trigger anti-modificación + rol de BD restringido.
- [ ] `AuditService`, cálculo de diff y enmascarado.
- [ ] Cadena de hash + comando y endpoint de verificación.
- [ ] Logging estructurado con enmascarado y `request_id`.
- [ ] Pantalla de auditoría con filtros, detalle y exportación.

**Iteración 5**
- [ ] `run_step`, `run_log`, `run_metric` + archivo JSONL.
- [ ] SSE de logs en vivo.
- [ ] Catálogo de códigos de error.
- [ ] Retención y archivado.

## Criterios de aceptación

- Ningún endpoint que modifique configuración se mergea sin su evento de auditoría (verificado por prueba que recorre las rutas `POST/PUT/PATCH/DELETE`).
- Un `UPDATE` manual sobre `audit_event` hace fallar la verificación de integridad.
- Ningún secreto aparece en auditoría ni en logs (prueba automatizada que busca valores sembrados).
- Los logs de una ejecución en curso se ven en la UI con < 2 s de retraso.
