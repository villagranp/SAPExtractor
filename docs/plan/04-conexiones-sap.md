# 04 · Conexiones SAP

**Iteración:** 2 · **Depende de:** 01, 02, 03 · **Bloquea a:** 05, 06

## Objetivo

Permitir que un Administrador de datos registre, de forma visual, **sistemas SAP** a los que la plataforma se conectará vía **OData (SAP Gateway)**, pruebe la conexión y explore los servicios publicados.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `sap_system` | `id`, `code` (p. ej. `S4P_300`), `name`, `description`, `environment` (`DEV` \| `QAS` \| `PRD`), `system_type` (`ECC` \| `S4HANA_ONPREM` \| `S4HANA_CLOUD`), `base_url` (p. ej. `https://gateway.empresa.local:44300`), `sap_client` (mandante), `language`, `auth_type`, `credential_secret_id`, `tls_verify`, `ca_bundle_secret_id`, `proxy_url`, `timeout_seconds`, `max_concurrent_requests`, `tags`, `status`, `last_test_at`, `last_test_result` |
| `secret` | `id`, `kind`, `ciphertext`, `key_version`, `created_at`, `rotated_at` (genérico, compartido con destinos e IdP) |

### Tipos de autenticación

| `auth_type` | Uso | Notas |
|---|---|---|
| `basic` | Usuario técnico SAP + contraseña | Usuario de comunicación (tipo *System*) sin diálogo |
| `x509` | Certificado de cliente | Recomendado en producción si Gateway lo permite |
| `oauth2_client_credentials` | S/4HANA Cloud / BTP | Para escenarios cloud |

Siempre: mandante (`sap-client`) e idioma (`sap-language`) enviados como parámetros.

## 2. Funcionalidades

### 2.1 Alta / edición (asistente)

1. **Datos generales**: código, nombre, entorno, tipo de sistema, etiquetas.
2. **Conexión**: URL base de Gateway, mandante, idioma, TLS (verificar / CA propia), proxy, timeout.
3. **Credenciales**: según tipo; los secretos se envían una vez y no se vuelven a mostrar (solo "••••• — Cambiar").
4. **Prueba**: botón *Probar conexión* (ver 2.2). No se puede marcar como `active` sin una prueba correcta.
5. **Resumen y guardar**.

### 2.2 Prueba de conexión

Secuencia con resultado paso a paso visible en la UI:

1. Resolución DNS y conexión TCP/TLS (muestra caducidad del certificado del servidor).
2. `GET /sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/` con credenciales → valida autenticación (401/403 diferenciados).
3. Lectura de cabeceras (`sap-server`, versión de Gateway si está disponible).
4. Medición de latencia.

Resultado guardado en `last_test_result` y auditado (`sap_connection.tested`).

### 2.3 Explorador de servicios

- Consulta el **catálogo de Gateway** (`ServiceCollection`) con búsqueda por nombre/descr. y paginación.
- Identifica y marca:
  - Servicios **ODP** (generados con *Redefine → ODP Extraction*): entidades tipo `EntityOf…` / `FactsOf…` y conjuntos `DeltaLinksOf…`.
  - Servicios de **CDS** publicados (`@OData.publish` / *service binding*).
- Para un servicio: lista sus *entity sets*, propiedades y si soporta *change tracking* (delta).
- Desde aquí se lanza el asistente de **alta de origen** (módulo 05) con el servicio ya seleccionado.
- Caché de catálogo por sistema (TTL configurable, botón "Refrescar").

### 2.4 Otras acciones

- Duplicar conexión (p. ej. de QAS a PRD cambiando URL y credenciales).
- Desactivar (impide nuevas ejecuciones; los pipelines asociados muestran aviso).
- Rotar credencial (auditada; prueba automática tras el cambio).
- Ver pipelines que dependen de la conexión antes de editar/borrar (no se puede borrar si hay pipelines activos).

## 3. Componente técnico: `SapODataClient`

Ubicación: `sap_connections/infrastructure/odata_client.py`. Reutilizado por orígenes y motor.

| Responsabilidad | Detalle |
|---|---|
| Sesión HTTP | `httpx.Client` con *keep-alive*, timeouts (conexión/lectura), TLS, proxy |
| Autenticación | Estrategias intercambiables (`BasicAuth`, `X509Auth`, `OAuth2ClientCredentials`) |
| CSRF SAP | `x-csrf-token: fetch` solo si se necesitara (lectura no lo requiere; se deja preparado) |
| Formato | `$format=json` (OData V2) / JSON nativo en V4 |
| Reintentos | Exponencial con *jitter* en 429/502/503/504 y errores de red; nunca en 4xx de negocio |
| Limitación | Semáforo por sistema (`max_concurrent_requests`) compartido entre workers mediante *advisory locks* / contador en BD |
| Versiones OData | Interfaz común con adaptadores V2 y V4 |
| Errores | Traduce errores SAP (`error.message.value`, `innererror`) a excepciones de dominio con código `SX-SAP-*` |
| Trazas | Log de cada petición (URL sin credenciales, estado, duración, bytes) |

## 4. API (resumen)

```
GET    /api/v1/sap-systems                POST /api/v1/sap-systems
GET    /api/v1/sap-systems/{id}           PATCH /api/v1/sap-systems/{id}
DELETE /api/v1/sap-systems/{id}
POST   /api/v1/sap-systems/test            # prueba sin guardar (asistente)
POST   /api/v1/sap-systems/{id}/test
POST   /api/v1/sap-systems/{id}/credentials/rotate
POST   /api/v1/sap-systems/{id}/duplicate
GET    /api/v1/sap-systems/{id}/services?search=&type=odp|cds|all
GET    /api/v1/sap-systems/{id}/services/{service}/entity-sets
GET    /api/v1/sap-systems/{id}/services/{service}/metadata
```

## 5. Pantallas

1. **Sistemas SAP**: tarjetas o tabla con estado (● verde/rojo), entorno, último test, nº de orígenes/pipelines.
2. **Asistente de alta/edición** (pasos de 2.1).
3. **Detalle de sistema**: pestañas *General · Servicios (explorador) · Orígenes · Actividad*.

## Requisitos lado SAP (a entregar al equipo Basis)

- Usuario técnico de comunicación con roles que incluyan `S_SERVICE` para los servicios publicados y autorizaciones de lectura ODP/ODQ correspondientes.
- Servicios activados en `/IWFND/MAINT_SERVICE` y alcanzables desde el servidor de la plataforma (firewall/puertos HTTPS).
- Certificado TLS del Gateway (o CA interna) para cargarlo en la plataforma.

## Tareas

- [ ] Modelo `sap_system` + `secret` + migraciones.
- [ ] `SapODataClient` con estrategias de autenticación, reintentos y semáforo.
- [ ] Prueba de conexión por pasos.
- [ ] Lectura del catálogo y clasificación ODP / CDS.
- [ ] Parser de `$metadata` (EDMX V2/V4) → modelo interno de entidades y propiedades.
- [ ] Endpoints y permisos (`sap_connection:*`).
- [ ] Pantallas 1–3.
- [ ] Pruebas contra `tools/sap-mock` (incluye 401, 403, timeout, certificado inválido).

## Criterios de aceptación

- Un usuario registra un sistema, prueba la conexión y ve la lista de servicios ODP y CDS publicados.
- La contraseña/certificado nunca vuelve al navegador ni aparece en logs.
- Un error de credenciales muestra un mensaje claro y distinto de un error de red.
