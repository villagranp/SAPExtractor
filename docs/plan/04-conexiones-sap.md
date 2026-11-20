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
| Reintentos | Exponencial con *jitter* en 429/502/503/504 y errores de red; nunca en 4xx de negocio. **Sin reintento** (`NO_RETRY`) en las peticiones que abren o avanzan un delta: lo decide el motor a nivel de run (plan 06 §3.2) |
| Limitación | Semáforo por proceso (`max_concurrent_requests`) para la API: explorador y vista previa. Las extracciones se limitan por **runs simultáneos por sistema** al tomar trabajo (`max_concurrent_runs`, plan 08 §3), sin semáforo compartido en BD |
| Hosts permitidos | `base_url`, `proxy_url` y la URL de token OAuth se validan contra `SX_SAP_ALLOWED_HOSTS` (sufijos DNS o CIDR; obligatoria en PRD). Siempre se bloquea 169.254.0.0/16 y, fuera de DEV, *loopback*. La IP se resuelve y valida antes de conectar |
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

- Usuario técnico de comunicación (tipo *System*, sin diálogo) con roles que incluyan `S_SERVICE` para los servicios publicados y autorizaciones de lectura ODP/ODQ. Por decisión del 19.11.2026 su autorización de lectura es amplia, para poder extraer cualquier origen; sigue siendo de solo lectura. El acceso a los datos se controla con los permisos de la plataforma, no en SAP.
- Si el spike muestra que dos suscripciones de delta no conviven sobre el mismo entity set y usuario (H6, H18), puede hacer falta un usuario técnico por pipeline o un segundo servicio: se decide con el equipo SAP (plan 06 §3.3).
- Servicios activados en `/IWFND/MAINT_SERVICE` y alcanzables desde el servidor de la plataforma (firewall/puertos HTTPS).
- Certificado TLS del Gateway (o CA interna) para cargarlo en la plataforma.

## Tareas

**Iteración 2** ✅
- [x] Modelo `sap_system` + `secret` + migraciones (`0004_sap_connections`, con `sap_catalog_cache`).
- [x] `SapODataClient` con estrategias de autenticación, reintentos y semáforo. *(Semáforo por proceso; la coordinación entre procesos con advisory locks llega con los workers, It5–6.)*
- [x] Prueba de conexión por pasos.
- [x] Lectura del catálogo y clasificación ODP / CDS.
- [x] Parser de `$metadata` (EDMX V2/V4) → modelo interno de entidades y propiedades.
- [x] Endpoints y permisos (`sap_connection:*`).
- [x] Pantallas 1–3.
- [x] Pruebas contra `tools/sap-mock` (incluye 401, 403, timeout, certificado inválido).
- [ ] Validar contra el SAP real de AG (depende del spike de It0 y de que Basis publique los servicios).

**Fase 0 (plan 13, C-07 a)**
- [x] `SX_SAP_ALLOWED_HOSTS`: validación de `base_url`, `proxy_url` y URL de token antes de conectar, con pruebas (host no permitido, IP de metadatos, *loopback* fuera de DEV, DNS que resuelve a una IP bloqueada).
  - Política reutilizable en `core/network.py` (`HostPolicy`) para destinos (It4) y *webhooks* (It8). Entradas: sufijos DNS (`empresa.local`) o redes (`10.20.0.0/16`); vacía = cualquiera salvo lo bloqueado siempre.
  - Al guardar o probar se comprueba sin resolver DNS y el error va al campo (`base_url`, `proxy_url`, `credentials.token_url`; código `SX-NET-HOST-NOT-ALLOWED`, 422).
  - Al conectar, `core/http_guard.py` resuelve el host, valida **todas** sus IP y conecta con la IP ya validada (el SNI y el certificado siguen usando el nombre): un DNS que cambie de respuesta entre la validación y la conexión no lleva a una dirección bloqueada. La sonda de red hace lo mismo. Con proxy se valida el proxy por IP y el destino por nombre.
  - Se aplica al cliente OData, al servidor de tokens OAuth y a la prueba de conexión. *Loopback* solo en `dev`/`test`.

## Lo construido en la iteración 2

Código: `backend/src/sapextract/sap_connections/`, `backend/src/sapextract/core/secret_store.py` y `frontend/src/features/sap-systems/`.

**Diferencias con el diseño inicial (decididas al implementar):**

| Tema | Diseño | Implementado | Motivo |
|---|---|---|---|
| Estado del sistema | `status` libre | `draft` → `active` ↔ `inactive`; activar exige `last_test_ok` y `test_outdated = false` | "No se puede marcar como active sin una prueba correcta" (§2.1), también tras editar la conexión o rotar la credencial |
| Resultado de la prueba | `last_test_result` | + `last_test_ok` y `test_outdated` | Semáforo en listados y bloqueo de activación sin abrir el JSON |
| Datos no secretos de la credencial | — | `credential_info` (usuario, client_id, sujeto y caducidad del certificado) y `ca_bundle_info` | La UI muestra "SXTECH · ••••••" y las caducidades sin descifrar nada |
| Contenido de `secret` | Credencial | JSON cifrado (AES-GCM envelope) ligado a su id (`aad = secret:<id>`) | Un texto cifrado copiado a otra fila no se puede descifrar |
| Edición de credenciales | En el asistente | Endpoint propio `credentials/rotate` (cambia también el tipo de autenticación) + prueba automática | El PATCH nunca transporta secretos; la rotación queda auditada aparte |
| Baja | Borrado | Baja lógica: se destruyen los secretos y se libera el código (índice único parcial) | No conservar credenciales sin uso; la auditoría mantiene la referencia |
| Errores de SAP en la API | — | Siempre 502/504 con código `SX-SAP-*` | Un 401 de SAP no debe parecer una sesión caducada de SAPExtract |
| Catálogo | Consulta por servicio | `ServiceCollection?$expand=EntitySets` (sin `$expand` si Gateway lo rechaza) y caché por sistema (`SX_SAP_CATALOG_TTL_MINUTES`, 60 min) | Clasificar cientos de servicios con una sola lectura; sin entity sets, se clasifica al abrir el servicio |
| Catálogo V4 | — | Solo el catálogo V2 (`CATALOGSERVICE;v=2`); el parser sí lee `$metadata` V4 | Los service binding V4 se explorarán cuando un caso real lo pida |
| Semáforo de concurrencia | Compartido entre workers (BD) | Por proceso (`ConcurrencyLimiter`) | Aún no hay workers que extraigan (It5) |

**Prueba de conexión por pasos** (`application/connection_tester.py`), cada paso con estado `ok | warning | failed | skipped`, duración y código de error:

1. *Red, DNS y TLS*: resolución, TCP y saludo TLS con el contexto real (verificación, CA propia, certificado de cliente). Si el certificado no es de confianza se muestra el certificado presentado; aviso si caduca en menos de 30 días o si la conexión va por HTTP. Con proxy se comprueba el proxy y TLS se valida en el paso 2.
2. *Autenticación*: `GET CATALOGSERVICE;v=2/` sin reintentos: 401 (`SX-SAP-AUTH`), 403 (`SX-SAP-FORBIDDEN`), 404, timeout (`SX-SAP-TIMEOUT`), TLS (`SX-SAP-TLS`), red (`SX-SAP-NETWORK`), DNS (`SX-SAP-DNS`), OAuth (`SX-SAP-OAUTH`).
3. *Servidor*: comprueba que la respuesta es OData de Gateway (detecta portales HTML de SSO) y recoge `sap-server`, `dataserviceversion`…
4. *Latencia*: tres muestras (aviso si la media supera 2 s).

Guardar el resultado no cambia la versión del sistema (no invalida un formulario de edición abierto). La prueba del asistente (`POST /sap-systems/test`) también se audita y puede reutilizar la credencial guardada de un sistema existente (`reuse_credentials_from`) sin que vuelva al navegador.

**Reglas añadidas:**

- PRD exige HTTPS y verificación TLS. La URL base no admite credenciales, parámetros ni fragmentos.
- Código `^[A-Z][A-Z0-9_]{2,29}$`, mandante de 3 dígitos, idioma SAP de 1–2 letras, timeout de 5 a 600 s, de 1 a 32 peticiones simultáneas.
- X.509: se comprueba que la clave privada corresponde al certificado y su contraseña; el material se carga en OpenSSL desde un directorio temporal privado que se borra al instante.
- OAuth 2.0: token cacheado hasta 60 s antes de caducar; ante un 401 se renueva una vez.
- `__next` se reancla a la URL base configurada (Gateway informa a menudo su nombre interno de host).
- `$metadata` se analiza con `defusedxml` (sin DTD ni entidades externas).
- Auditoría: `sap_connection.created|updated|deleted|tested|credentials_rotated|activated|deactivated`; la credencial aparece siempre como `***`.
- Los módulos de orígenes (It3) y pipelines (It6) registran sus dependencias en `SapToolkit.dependents`: impiden borrar el sistema si hay objetos activos y se listan en la pestaña *Orígenes*.

**API real:** la del §4 más `POST /sap-systems/{id}/activate` y `POST /sap-systems/{id}/deactivate`. `GET …/services` admite `type` repetible (`odp`, `cds`, `other`, `unknown`), `search`, paginación y `refresh=true` (relee de SAP). Los nombres de servicio con espacio de nombres (`/NS/SERVICIO`) se aceptan en la ruta.

**Frontend:** lista con semáforo de la última prueba; asistente de 5 pasos (*Datos generales · Conexión · Credenciales · Prueba · Resumen*; en edición, sin el paso de credenciales); detalle con pestañas *General · Servicios · Orígenes · Actividad*, diálogos *Cambiar credenciales* y *Duplicar*, y eliminación que exige escribir el código y un motivo. Nuevos componentes de `shared/ui`: `Stepper`, `TextAreaField` y `Checklist`; `ConfirmDialog` admite `confirmText`.

**Mock SAP:** el catálogo admite `$expand=EntitySets`, las respuestas llevan `sap-server`/`dataserviceversion` y el usuario no distingue mayúsculas (como SAP). Las pruebas de integración lo levantan en proceso (también con TLS autofirmado) desde `backend/tests/support/sap_gateway.py`.

**Pendiente para iteraciones posteriores:** borrador del asistente guardado automáticamente (plan 10 §4.1), límite de runs simultáneos por sistema (`max_concurrent_runs`, It6; sustituye al semáforo entre procesos previsto), recuento de orígenes/pipelines en el listado (It3/It6), catálogo OData V4 y aviso proactivo de caducidad de certificados (It8, alertas).

## Criterios de aceptación

- Un usuario registra un sistema, prueba la conexión y ve la lista de servicios ODP y CDS publicados.
- La contraseña/certificado nunca vuelve al navegador ni aparece en logs.
- Un error de credenciales muestra un mensaje claro y distinto de un error de red.
