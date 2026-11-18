# 02 · Usuarios, autenticación y roles

**Iteraciones:** 1 (local + RBAC) y 7 (Entra ID) · **Depende de:** 01, 03 (auditoría base) · **Bloquea a:** todos los módulos con permisos

## Objetivo

Gestionar de forma completa la identidad y el acceso a la plataforma:

- **Administrador local** que siempre puede entrar (acceso de emergencia / *break-glass*), aunque Entra ID no esté configurado o falle.
- **SSO con Microsoft Entra ID**, configurable desde la propia plataforma (sin redeploy).
- **Ciclo de vida de usuarios**: alta, modificación, asignación de roles, bloqueo, baja.
- **Control de acceso basado en roles (RBAC)** con permisos granulares.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `user` | `id`, `username` (único), `email`, `display_name`, `auth_source` (`local` \| `entra`), `external_id` (oid de Entra), `status` (`invited` \| `active` \| `locked` \| `disabled`), `is_break_glass`, `last_login_at`, `failed_attempts`, `locked_until`, `must_change_password`, `deleted_at` |
| `local_credential` | `user_id`, `password_hash` (argon2id), `password_changed_at`, `totp_secret` (cifrado), `totp_enabled`, `password_history` (últimos N hashes) |
| `role` | `id`, `code`, `name`, `description`, `is_system` (no editable) |
| `permission` | `code` (p. ej. `pipeline:run`), `module`, `description` — catálogo fijo definido en código |
| `role_permission` | `role_id`, `permission_code` |
| `user_role` | `user_id`, `role_id`, `assigned_by`, `assigned_at`, `source` (`manual` \| `entra_group`) |
| `session` | `id` (hash del token), `user_id`, `created_at`, `last_seen_at`, `expires_at`, `ip`, `user_agent`, `revoked_at`, `auth_method` |
| `identity_provider` | `id`, `type` (`entra_id`), `enabled`, `tenant_id`, `client_id`, `client_secret_ref` (secreto cifrado) o certificado, `redirect_uri`, `scopes`, `jit_provisioning`, `default_role_id`, `allowed_domains`, `require_group_membership` |
| `idp_group_mapping` | `identity_provider_id`, `group_object_id`, `group_name`, `role_id` |
| `api_token` | `id`, `user_id` (cuenta de servicio), `name`, `token_hash`, `scopes`, `expires_at`, `last_used_at`, `revoked_at` |

## 2. Roles y permisos

### Roles de sistema (predefinidos, no eliminables)

| Rol | Uso |
|---|---|
| **Administrador de plataforma** | Todo, incluido identidad, Entra ID, secretos y parámetros globales |
| **Administrador de datos** | Conexiones SAP, orígenes, destinos, pipelines, programación |
| **Operador** | Ejecutar, pausar, reintentar pipelines; ver logs |
| **Visualizador** | Solo lectura de pipelines, ejecuciones y dashboard |
| **Auditor** | Solo lectura de auditoría y logs; exportación |

Se pueden crear **roles personalizados** combinando permisos.

### Catálogo de permisos (formato `recurso:acción`)

| Módulo | Permisos |
|---|---|
| Identidad | `user:read`, `user:create`, `user:update`, `user:disable`, `user:delete`, `role:read`, `role:manage`, `session:revoke`, `idp:manage`, `api_token:manage` |
| Auditoría | `audit:read`, `audit:export`, `log:read` |
| Conexiones SAP | `sap_connection:read`, `sap_connection:manage`, `sap_connection:test` |
| Orígenes | `source:read`, `source:manage`, `source:preview` |
| Destinos | `destination:read`, `destination:manage`, `destination:test` |
| Pipelines | `pipeline:read`, `pipeline:manage`, `pipeline:run`, `pipeline:pause`, `pipeline:reset_delta` |
| Monitoreo | `dashboard:read`, `alert:manage` |
| Sistema | `settings:manage`, `secret:rotate` |

> `pipeline:reset_delta` es crítico (reinicia una suscripción de delta) y solo lo tiene el Administrador de plataforma por defecto.

Opcional (v1.1): **ámbito por etiqueta/área** (p. ej. un rol "Operador FI" solo ve pipelines etiquetados `FI`).

### Aplicación en backend

- Dependencia FastAPI `require_permission("pipeline:run")` en cada endpoint.
- Los permisos efectivos se calculan al iniciar sesión y se cachean en la sesión; se recalculan si cambian sus roles (evento `UserRolesChanged`).
- El frontend recibe la lista de permisos (`GET /api/v1/me`) **solo para ocultar/mostrar** opciones; la autorización real es siempre del backend.

## 3. Autenticación local

- **Administrador inicial**: se crea con `sapextract create-admin` (CLI) o, en el primer arranque, desde variables `SX_BOOTSTRAP_ADMIN_USER` + contraseña temporal. Obliga a cambiar la contraseña en el primer acceso.
- **Hash**: argon2id con parámetros OWASP vigentes.
- **Política de contraseñas** configurable: longitud mínima 12, comprobación contra lista de contraseñas comprometidas, historial (no repetir las últimas 5), caducidad opcional.
- **Bloqueo**: N intentos fallidos → bloqueo temporal con retardo progresivo; aviso en auditoría.
- **MFA local (TOTP)**: obligatorio para el administrador local en producción (configurable).
- **Usuarios locales adicionales**: permitidos pero deshabilitables por política (`local_login_enabled_for = break_glass_only | all`) para forzar Entra ID al resto.
- **Recuperación**: no hay "olvidé mi contraseña" por correo para el admin local; se restablece por CLI en el servidor (auditado).

## 4. Microsoft Entra ID (SSO)

### Flujo (patrón BFF, el navegador nunca ve tokens)

1. Usuario pulsa "Entrar con Microsoft" → `GET /api/v1/auth/entra/login`.
2. Backend redirige a Entra con **authorization code + PKCE**, `state` y `nonce`.
3. Entra devuelve a `/api/v1/auth/entra/callback`; el backend canjea el código, **valida el id_token** (firma por JWKS, `iss`, `aud`, `exp`, `nonce`, `tid`).
4. Resolución de usuario por `oid`:
   - Existe y activo → actualizar datos y roles por grupo.
   - No existe y `jit_provisioning = true` → crear con rol por defecto / grupos.
   - No existe y JIT desactivado → acceso denegado (requiere alta previa).
5. Se crea **sesión de servidor** y se envía cookie `sx_session` (`httpOnly`, `Secure`, `SameSite=Lax`).

### Configuración desde la plataforma (pantalla "Proveedor de identidad")

- Campos: tenant ID, client ID, secreto **o** certificado, URI de redirección (calculada y mostrada para copiar), dominios permitidos, JIT sí/no, rol por defecto.
- **Mapeo grupos → roles**: tabla editable (Object ID del grupo + nombre + rol). Los grupos llegan en el claim `groups` (configurar en el *App registration*); si el usuario supera el límite de grupos del token se consulta Microsoft Graph (`/me/transitiveMemberOf`) con el permiso `GroupMember.Read.All` (opcional).
- **Botón "Probar configuración"**: valida que el endpoint de *discovery* del tenant responde y que el client ID existe, sin guardar.
- Activar/desactivar Entra sin reiniciar (la configuración se lee de BD con caché invalidable).
- Guía integrada con los pasos del *App registration* (URI de redirección, claim de grupos, secreto/certificado).

### Sincronización

- En cada login se re-evalúan los grupos → roles con `source = entra_group` (los roles asignados manualmente se conservan).
- Tarea programada opcional (v1.1): desactivar usuarios que ya no existan o estén deshabilitados en Entra (requiere `User.Read.All`).

## 5. Ciclo de vida de usuarios

| Acción | Detalle | Auditoría |
|---|---|---|
| **Alta manual** | Local (con contraseña temporal) o Entra (se pre-registra por correo/UPN y se enlaza en el primer login) | `user.created` |
| **Alta JIT** | Automática en primer login Entra | `user.provisioned` |
| **Edición** | Nombre, correo, roles | `user.updated`, `user.roles_changed` (con diff) |
| **Bloqueo / desbloqueo** | Manual o por intentos fallidos | `user.locked`, `user.unlocked` |
| **Baja (desactivar)** | Estado `disabled`, revoca sesiones y tokens API; conserva historial | `user.disabled` |
| **Reactivar** | De `disabled` a `active` | `user.enabled` |
| **Eliminación** | Solo usuarios sin actividad o por petición formal; se **anonimiza** (no se borra físicamente) para no romper la auditoría | `user.anonymized` |
| **Revocar sesiones** | Individual o todas las de un usuario | `session.revoked` |

Reglas:

- No se puede desactivar/eliminar al **último administrador de plataforma activo** ni al usuario *break-glass*.
- Un usuario no puede modificar sus propios roles.

## 6. Sesiones y seguridad web

- Sesión de servidor con expiración por inactividad (30 min, configurable) y absoluta (12 h).
- Protección **CSRF**: cookie `sx_csrf` + cabecera `X-CSRF-Token` en métodos no seguros.
- Rotación del identificador de sesión al iniciar sesión y al elevar privilegios.
- *Rate limiting* en `/auth/*`.
- Pantalla "Mis sesiones" y "Sesiones activas" (admin).
- **Tokens API** para cuentas de servicio (p. ej. disparar un pipeline desde otra herramienta): `Authorization: Bearer`, con *scopes* y caducidad; se muestran una sola vez.

## 7. API (resumen)

```
POST   /api/v1/auth/login                 # local
POST   /api/v1/auth/logout
GET    /api/v1/auth/entra/login
GET    /api/v1/auth/entra/callback
POST   /api/v1/auth/password/change
POST   /api/v1/auth/mfa/setup | /verify
GET    /api/v1/me                         # perfil + permisos
GET    /api/v1/me/sessions

GET    /api/v1/users            POST /api/v1/users
GET    /api/v1/users/{id}       PATCH /api/v1/users/{id}
POST   /api/v1/users/{id}/disable | /enable | /unlock | /reset-password | /anonymize
PUT    /api/v1/users/{id}/roles
DELETE /api/v1/users/{id}/sessions

GET    /api/v1/roles            POST /api/v1/roles
PATCH  /api/v1/roles/{id}       DELETE /api/v1/roles/{id}
GET    /api/v1/permissions

GET    /api/v1/identity-providers/entra
PUT    /api/v1/identity-providers/entra
POST   /api/v1/identity-providers/entra/test
PUT    /api/v1/identity-providers/entra/group-mappings

GET    /api/v1/api-tokens       POST /api/v1/api-tokens    DELETE /api/v1/api-tokens/{id}
```

## 8. Pantallas

1. **Login**: usuario/contraseña local + botón "Entrar con Microsoft" (solo si Entra está activo).
2. **Cambio de contraseña obligatorio** / configuración MFA.
3. **Usuarios**: tabla con filtros (estado, origen, rol), acciones masivas (desactivar), detalle con pestañas *Datos · Roles · Sesiones · Actividad (auditoría filtrada)*.
4. **Roles**: lista y editor con matriz de permisos agrupada por módulo (casillas).
5. **Proveedor de identidad (Entra ID)**: formulario + mapeo de grupos + prueba + guía.
6. **Tokens API**.
7. **Mi perfil**.

## Tareas

**Iteración 1 (local + RBAC)**
- [ ] Modelo y migraciones (`user`, `local_credential`, `role`, `permission`, `user_role`, `session`).
- [ ] Semilla de permisos y roles de sistema (idempotente en cada arranque).
- [ ] CLI `create-admin` y `reset-password`.
- [ ] Login/logout local, sesiones de servidor, CSRF, bloqueo por intentos, rate limiting.
- [ ] MFA TOTP.
- [ ] Dependencia `require_permission` y `GET /me`.
- [ ] CRUD de usuarios y roles con reglas de negocio (último admin, auto-edición).
- [ ] Tokens API.
- [ ] Pantallas 1–4, 6, 7.
- [ ] Eventos de auditoría para todas las acciones.

**Iteración 7 (Entra ID)**
- [ ] `identity_provider` + `idp_group_mapping` con secreto cifrado.
- [ ] Flujo OIDC con Authlib (PKCE, state, nonce, validación de id_token).
- [ ] JIT y mapeo de grupos; fallback a Graph para exceso de grupos.
- [ ] Pantalla 5 con prueba de configuración y guía.
- [ ] Pruebas con un tenant de desarrollo.

## Criterios de aceptación

- El admin local entra aunque Entra esté desactivado o mal configurado.
- Un usuario sin permiso recibe 403 en la API aunque fuerce la URL.
- Desactivar un usuario cierra sus sesiones activas en < 1 minuto.
- Un usuario de Entra en el grupo mapeado entra y recibe el rol correcto; al sacarlo del grupo pierde el rol en su siguiente login.
- Toda acción del módulo genera un evento de auditoría con el diff (sin secretos).

## Pruebas clave

- Unitarias: política de contraseñas, cálculo de permisos efectivos, reglas de último admin.
- Integración: login local, bloqueo, CSRF, expiración de sesión.
- Entra: validación de id_token con JWKS simulado (firma inválida, `aud` incorrecto, `nonce` reutilizado).
- E2E: alta de usuario → asignar rol → login → acceso restringido.
