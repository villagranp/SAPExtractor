# Solicitudes externas de la Fase 0

Borradores listos para enviar (plan 13 §5.4). Los dos están en la ruta crítica: sin la cuenta de Snowflake no hay primer dato real (H2) y sin el *App registration* no hay salida a PRD (decisión 7 del plan 13). Ajustar nombres y hosts antes de enviarlos.

Los secretos que devuelvan (secreto o certificado de Entra ID, clave privada de Snowflake) **no se envían por correo ni chat**: se entregan por el gestor de secretos corporativo o se cargan directamente en la plataforma.

---

## 1. Microsoft Entra ID: *App registration* (para IT)

**Asunto:** Registro de aplicación en Entra ID para SAPExtract (SSO)

Necesitamos dos registros de aplicación (uno para QA y otro para PRD) para el inicio de sesión único en SAPExtract, la plataforma interna de extracción de datos de SAP. El administrador local de emergencia se mantiene; el resto de usuarios entrará con su cuenta corporativa.

| Dato | Valor solicitado |
|---|---|
| Nombre | `SAPExtract QA` y `SAPExtract PRD` |
| Tipo de cuenta | Solo este directorio (un único inquilino) |
| Plataforma | Web |
| URI de redirección | `https://<host-qa>/api/v1/auth/entra/callback` y `https://<host-prd>/api/v1/auth/entra/callback` (hosts por confirmar) |
| Flujo | *Authorization code* con PKCE; no se necesita flujo implícito ni tokens de acceso por el navegador |
| Credencial | Certificado (preferido) o secreto de cliente con caducidad de 12 meses; avisar 30 días antes de que caduque |
| Permisos de Microsoft Graph (delegados) | `openid`, `profile`, `email`, `User.Read` |
| Permiso opcional | `GroupMember.Read.All` (delegado, requiere consentimiento de administrador): solo si algún usuario pertenece a más de 200 grupos y el token no puede incluirlos todos |
| Configuración del token | Añadir el *claim* `groups` (grupos de seguridad, como identificador de objeto) en el token de ID |

**Grupos de seguridad** que asignan los roles de la plataforma (nombres orientativos):

| Grupo | Rol en SAPExtract |
|---|---|
| `SG-SAPExtract-Administradores` | Administrador de plataforma |
| `SG-SAPExtract-AdminDatos` | Administrador de datos |
| `SG-SAPExtract-Operadores` | Operador |
| `SG-SAPExtract-Lectura` | Visualizador |
| `SG-SAPExtract-Auditores` | Auditor |

**Lo que necesitamos de vuelta:** ID de inquilino (*tenant ID*), ID de aplicación (*client ID*) de cada registro, el certificado o secreto (por canal seguro) y el *Object ID* de cada grupo.

---

## 2. Snowflake: cuenta y base de pruebas (para el equipo de datos)

**Asunto:** Entorno de pruebas de Snowflake para SAPExtract

SAPExtract cargará datos de SAP en Snowflake. Para desarrollar y probar el conector (y ejecutar sus pruebas automáticas) necesitamos un entorno aislado de los datos productivos.

| Dato | Valor solicitado |
|---|---|
| Cuenta | Identificador `<org>-<account>` y región |
| *Warehouse* | `SAPEXTRACT_TEST_WH`, tamaño XS, `AUTO_SUSPEND = 60`, `AUTO_RESUME = TRUE`, con un *resource monitor* de créditos mensual |
| Base de datos y esquema | `SAPEXTRACT_TEST` con el esquema `RAW` |
| Rol | `SAPEXTRACT_LOADER` con `USAGE` sobre el *warehouse*, la base y el esquema, y `CREATE TABLE` y `CREATE STAGE` sobre el esquema (será propietario de las tablas que cree) |
| Usuario | `SAPEXTRACT_SVC`, de tipo `SERVICE`, rol por defecto `SAPEXTRACT_LOADER`, **autenticación por par de claves** (sin contraseña). Nosotros generamos el par y enviamos solo la clave pública |
| Red | Si la cuenta tiene *network policy*: permitir la IP de salida del servidor de la plataforma. Para las pruebas automáticas de CI (GitHub Actions, IP variables) necesitamos una excepción en la cuenta de pruebas o un *runner* propio con IP fija |

**Lo que necesitamos de vuelta:** identificador de cuenta, confirmación de que el usuario tiene asignada nuestra clave pública y la *network policy* aplicada.

Antes del primer caso real se pedirá un entorno equivalente sobre la base de destino definitiva (plan 07 §5).
