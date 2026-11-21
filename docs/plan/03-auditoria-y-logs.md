# 03 · Auditoría y logs

**Iteración:** 1 (base) y 5 (logs de ejecución completos, junto con el motor) · **Depende de:** 01 · **Bloquea a:** 02 y todos los módulos (todos emiten eventos)

## Objetivo

Trazabilidad completa y verificable de **quién hizo qué, cuándo, desde dónde y con qué resultado**, separando tres tipos de registro:

| Tipo | Para qué | Dónde se guarda | Quién lo consulta |
|---|---|---|---|
| **Auditoría** | Acciones de negocio y seguridad (login, cambios de configuración, ejecuciones manuales, reinicios de delta) | Tabla `audit_event` (append-only, encadenada por hash) | Auditor, Administrador |
| **Logs de ejecución** | Detalle de cada extracción/carga (pasos, páginas, filas, errores) | Tabla `run_log`, particionada por fecha | Operador, Administrador de datos |
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
- **Ancla externa** (It9, plan 13 C-18): la cadena y su ancla viven en la misma base, así que quien pueda escribir en ella puede recalcular la cadena entera. El hash de cabecera se firma con una clave derivada de la clave maestra y se emite a diario a los logs técnicos; la verificación se hace por tramos desde el último punto verificado.
- La IP del contexto solo se toma de cabeceras de proxy cuando la petición llega de un proxy de confianza (`SX_TRUSTED_PROXIES`, plan 11 §3).

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
| `run_step` | Pasos con inicio/fin/estado, por intento. La lista de pasos es la del plan 06 §2: `prepare`, `extract`, `load`, `commit_delta`, `cleanup` |
| `run_log` | Líneas con `ts`, `level`, `step`, `attempt`, `message`, `data` (JSON). Tabla particionada por fecha: la retención consiste en borrar particiones. Nivel configurable por pipeline (INFO por defecto, DEBUG temporal) |
| `run_metric` | Filas leídas/escritas/actualizadas/borradas, páginas, bytes, duración por paso, filas/seg |

No hay archivo de log en el disco del worker: con varias instancias, la API no podría leerlo.

- **En vivo**: el SSE de la API sondea `run_log` cada segundo con el cursor `after`, lo que cumple el criterio de menos de 2 s. `NOTIFY` se usa solo como aviso sin carga (su límite es de 8000 bytes y exigiría una conexión de escucha por réplica de API).
- Los logs de ejecución no incluyen filas de datos de SAP salvo en errores de datos, donde la muestra queda en `run_log` y se consulta desde el detalle del run.
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

**Iteración 1** ✅
- [x] Tabla `audit_event` particionada + trigger anti-modificación. *(Rol de BD restringido sin `UPDATE/DELETE` → iteración 9, con el despliegue de PRD.)*
- [x] `AuditService`, cálculo de diff y enmascarado.
- [x] Cadena de hash + comando y endpoint de verificación.
- [x] Logging estructurado con enmascarado y `request_id` (iteración 0).
- [x] Pantalla de auditoría con filtros, detalle y exportación.

**Lo construido en la iteración 1** (`backend/src/sapextract/audit/`, `frontend/src/features/audit/`):
- Particiones mensuales creadas por la función `sx.audit_ensure_partitions(n)` (migración + tarea diaria del scheduler, 3 meses por delante) y una partición `DEFAULT` de seguridad. Triggers que rechazan `UPDATE`, `DELETE` y `TRUNCATE` (también sobre cada partición).
- La escritura de la cadena se serializa con `pg_advisory_xact_lock` dentro de la transacción del cambio auditado; los fallos y denegaciones (`auth.login_failed`, `auth.access_denied`…) se registran en transacción propia.
- La verificación se detiene en el primer evento roto e indica si fue modificado o si falta/está desordenado el enlace; el resultado queda en `system_setting` (`audit.last_verification`) y genera `audit.verified` o `audit.verification_failed`.
- Cada endpoint que modifica datos declara sus eventos en el OpenAPI (`x-sx-audit`); una prueba lo exige. Otra prueba busca secretos sembrados (contraseñas, MFA, tokens) en la auditoría y en los logs.
- Exportación CSV (con protección contra fórmulas de Excel) o JSON, síncrona hasta 100 000 eventos; la asíncrona para volúmenes mayores queda para cuando haga falta.
- Pestaña «Actividad» reutilizable (`AuditTrail`) ya integrada en la ficha de usuario.
- Corregido en It1b: el total de la búsqueda siempre valía 1 (el `count(*)` perdía la tabla del `FROM`), así que la paginación no avanzaba. Prueba de integración añadida.

**Iteración 5**
- [x] `run_step` y `run_log` (particionada por mes con `sx.run_log_ensure_partitions`, que el scheduler invoca a diario). Las métricas van en `run_step.metrics`; `run_metric` agregada queda para la It8.
- [x] Logs en vivo por sondeo con cursor: `GET /runs/{id}/logs?after=` y la pantalla consulta cada segundo hasta que el run termina (sin SSE por ahora). Los tokens de delta no se escriben en el log.
- [x] Acciones de ejecución en el catálogo: `pipeline.run_requested`, `run.cancel_requested`, `run.succeeded`, `run.failed`, `run.cancelled`, `pipeline.delta_reset`, `pipeline.delta_broken`, `pipeline.sap_subscription_closed`, `pipeline.sap_subscription_left_open` (más `pipeline.created/updated/deleted` en configuración).
- [ ] Catálogo de códigos de error.
- [ ] Retención (borrado de particiones) y archivado.

**Iteración 9**
- [ ] Ancla externa de la cadena de auditoría y verificación por tramos.

## Criterios de aceptación

- Ningún endpoint que modifique configuración se mergea sin su evento de auditoría (verificado por prueba que recorre las rutas `POST/PUT/PATCH/DELETE`).
- Un `UPDATE` manual sobre `audit_event` hace fallar la verificación de integridad.
- Ningún secreto aparece en auditoría ni en logs (prueba automatizada que busca valores sembrados).
- Los logs de una ejecución en curso se ven en la UI con < 2 s de retraso.
