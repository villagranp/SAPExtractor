# 07 · Destinos (Snowflake, CSV, SQL Server)

**Iteración:** 4a (CSV y Snowflake, antes del motor) y 4b (SQL Server, tras el primer dato) · **Depende de:** 01, 02, 03 · **Bloquea a:** 06, 08

> Actualizado el 19.11.2026 con los cambios C-03, C-07, C-10, C-11 y C-12 del [plan 13](13-revision-y-plan-de-cambios.md). Snowflake es el destino del primer caso real, por eso va primero.

## Objetivo

Registrar destinos de forma visual, probarlos y escribir en ellos los datos extraídos con cargas **masivas, transaccionales e idempotentes**. Cada destino es un **plugin** que implementa `DestinationWriter` (ver plan 06), de forma que añadir destinos futuros (ADLS, S3, PostgreSQL, Databricks…) no toca el motor.

Orden de construcción: **CSV** (el más simple; permite probar el motor de punta a punta sin dependencias externas), **Snowflake** y, en la Fase 3, **SQL Server**.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `destination` | `id`, `code`, `name`, `type` (`snowflake` \| `csv` \| `sqlserver`), `config` (JSON no sensible, validado por esquema del plugin), `secret_id`, `environment`, `tags`, `status`, `last_test_at`, `last_test_result` |

La configuración de cada tipo se define con un **modelo Pydantic por plugin**; el frontend pinta el formulario a partir de su JSON Schema (`GET /api/v1/destination-types/{type}/schema`), así un plugin nuevo aparece en la UI sin código de frontend adicional.

`environment` se compara con el del sistema SAP del origen al crear el pipeline (plan 08 §6): la instancia es única para todos los entornos y un cruce de entornos exige confirmación.

### Seguridad de la configuración

- **Hosts.** El servidor, la cuenta o la URL del destino los escribe un usuario y la prueba de conexión devuelve detalles de red. Se valida contra una lista de hosts permitidos (`SX_DEST_ALLOWED_HOSTS`, sufijos DNS o CIDR; obligatoria en PRD), con el mismo criterio que los sistemas SAP (plan 04).
- **Rutas CSV.** Solo dentro de las raíces de `SX_CSV_ROOTS`: se resuelve la ruta real y se exige que quede dentro. Las variables del patrón de nombre se sanean.
- **Identificadores SQL.** `target_name`, esquema y `column_mapping` acaban en DDL y `MERGE` generados: se validan contra un patrón y siempre se citan; nunca se interpolan. Hay una prueba con nombres maliciosos.

## 2. Opciones comunes de carga (por pipeline)

| Opción | Valores |
|---|---|
| `load_mode` | `append` · `upsert` · `replace` |
| `target_name` | Tabla / prefijo de archivo; por defecto derivado del origen (`<sistema>_<origen>`) |
| `create_if_missing` | Crear tabla/carpeta automáticamente |
| `schema_evolution` | `add_columns` (defecto) · `ignore` · `fail` |
| `delete_handling` | `soft` (`_sx_deleted = 1`) · `hard` · `ignore` |
| `technical_columns` | Incluir `_sx_*` (por defecto sí) |
| `column_mapping` | Renombrado/exclusión de columnas (nombres SAP → nombres destino; opción *snake_case*) |

### Control de cargas

- Cada fila lleva `_sx_batch_id` (plan 06 §4).
- Tabla de control `__sx_load_state` en el esquema del destino: pipeline, `batch_id`, token resultante, filas, fecha. Se escribe **en la misma transacción** que el `apply`. Antes de cargar, el motor pregunta si el lote ya está aplicado (`applied_batch`) y, si lo está, no repite la carga.
- El DDL (crear la tabla, añadir columnas) se ejecuta en `prepare`, nunca dentro de la transacción de `apply`.
- `stage(spool)` recibe el spool: sus archivos Parquet y un iterador de lotes.

## 3. SQL Server (It4b)

| Tema | Diseño |
|---|---|
| Conexión | Servidor, puerto, base de datos, esquema, autenticación SQL o **Entra ID (service principal)** / integrada si el host lo permite, `Encrypt=yes`, `TrustServerCertificate` configurable |
| Driver | ODBC Driver 18 + `pyodbc` |
| Staging | Tabla `<esquema>.__sx_stg_<target>_<batch>` creada por lote |
| Carga masiva | `fast_executemany` por lotes Arrow. Se mide con 10 M de filas frente al volumen esperado (D5); la carga tipo `bcp` se decide con ese dato |
| `upsert` | `MERGE` con deduplicación previa (`ROW_NUMBER() OVER (PARTITION BY clave ORDER BY _sx_record_seq DESC)`) dentro de una transacción |
| `replace` | `TRUNCATE` + `ALTER TABLE … SWITCH` (o `INSERT … SELECT`) en una transacción, sobre la tabla estable. No se usa `sp_rename`: la tabla nueva no hereda permisos, índices ni vistas dependientes |
| `append` | `DELETE` de las filas del mismo `_sx_batch_id` + `INSERT`, en una transacción |
| DDL | Mapeo de tipos (sección 6), PK en columnas clave (para `upsert`), índice por `_sx_batch_id`. `ddl-preview` valida el ancho de la clave (900 bytes en índice agrupado, 1700 en no agrupado) y, si se supera, ofrece índice único no agrupado o clave *hash* |
| Prueba | Conectar, comprobar permisos `CREATE TABLE`, `INSERT`, `ALTER` en el esquema |

## 4. Archivos CSV (It4a)

| Tema | Diseño |
|---|---|
| Ubicación | Ruta local del servidor o **carpeta compartida (UNC/SMB)** montada en el contenedor, siempre dentro de `SX_CSV_ROOTS`; (futuro: Azure Blob/ADLS, S3, SFTP como plugins) |
| Formato | Delimitador, comillas, escape, encabezado sí/no, codificación (UTF-8 por defecto, UTF-8 BOM para Excel), fin de línea, formato de fecha/decimal |
| Compresión | Ninguna o `gzip` |
| Organización | Patrón de nombre con variables: `{source}/{yyyy}/{MM}/{dd}/{source}_{mode}_{batch_id}_{part:04}.csv`. Con `batch_id`, repetir un lote sobrescribe los mismos archivos |
| Particionado | Corte por número de filas o tamaño (p. ej. 1 M filas / 500 MB) |
| Atomicidad | Escritura en `*.tmp` y renombrado al final; archivo `_manifest.json` por lote (archivos, filas, hash SHA-256, esquema, modo, token) y archivo `_SUCCESS`. Hacen de tabla de control |
| Modos | `append` = archivos nuevos por lote; `replace` = carpeta `current/` reemplazada; `upsert` **no aplica** (se avisa en el asistente) |
| Prueba | Crear, escribir y borrar un archivo de prueba en la ruta |

## 5. Snowflake (It4a)

| Tema | Diseño |
|---|---|
| Conexión | Cuenta (`<org>-<account>`), usuario de servicio, rol, *warehouse*, base de datos, esquema |
| Autenticación | **Key-pair** (clave privada cifrada en `secret`) como opción por defecto; OAuth como alternativa. No se recomienda contraseña para usuarios de servicio |
| Carga | `PUT` de los archivos Parquet del spool, tal cual, a un *stage* interno temporal → `COPY INTO` tabla staging (`MATCH_BY_COLUMN_NAME`) → en una transacción: `MERGE`/`INSERT` y fila en `__sx_load_state` → `REMOVE` del stage |
| `append` | `DELETE` de las filas del mismo `_sx_batch_id` + `INSERT`, en la transacción |
| `replace` | `INSERT OVERWRITE INTO <target> SELECT … FROM <staging>` dentro de la transacción: sustituye el contenido de forma atómica y conserva los permisos de la tabla. No se usa `SWAP WITH` ni `CREATE OR REPLACE`: en Snowflake una sentencia DDL confirma la transacción en curso, así que no podría ir junto a la tabla de control |
| Identificadores | Se normalizan a mayúsculas y se citan. Citar un nombre en minúsculas lo haría sensible a mayúsculas y obligaría a los consumidores a citarlo siempre |
| Costes | Uso del warehouse indicado; opción de *auto-suspend* recomendada en la guía; métricas de créditos fuera de alcance v1 |
| Tipos | Mapeo sección 6; `VARCHAR` sin longitud, `NUMBER(p,s)`, `TIMESTAMP_NTZ` sin conversión de zona |
| Prueba | Conectar, `USE ROLE/WAREHOUSE/DATABASE/SCHEMA`, comprobar `CREATE TABLE` y `CREATE STAGE` |

El comportamiento transaccional de `INSERT OVERWRITE` y del DDL está tomado de la documentación de Snowflake (consultada el 19.11.2026) y se comprueba con la cuenta de pruebas en It4a.

## 6. Mapeo de tipos

| EDM (OData) | Arrow | SQL Server | Snowflake | CSV |
|---|---|---|---|---|
| `Edm.String` (MaxLength n) | `string` | `NVARCHAR(n)` (o `MAX`) | `VARCHAR` | texto |
| `Edm.Int16/32` | `int16/int32` | `SMALLINT/INT` | `NUMBER(5/10,0)` | número |
| `Edm.Int64` | `int64` | `BIGINT` | `NUMBER(19,0)` | número |
| `Edm.Decimal` (p,s) | `decimal128(p,s)` | `DECIMAL(p,s)` | `NUMBER(p,s)` | número con `.` |
| `Edm.Double` | `float64` | `FLOAT` | `FLOAT` | número |
| `Edm.Boolean` | `bool` | `BIT` | `BOOLEAN` | `true/false` |
| `Edm.DateTime` (sap:display-format=Date) | `date32` | `DATE` | `DATE` | `YYYY-MM-DD` |
| `Edm.DateTime` | `timestamp[us]` (sin zona) | `DATETIME2(6)` | `TIMESTAMP_NTZ` | ISO 8601 sin zona |
| `Edm.DateTimeOffset` | `timestamp[us, UTC]` | `DATETIME2(6)` en UTC | `TIMESTAMP_NTZ` en UTC | ISO 8601 con `Z` |
| `Edm.Time` | `time64[us]` | `TIME(6)` | `TIME` | `HH:MM:SS` |
| `Edm.Guid` | `string` | `UNIQUEIDENTIFIER` | `VARCHAR(36)` | texto |
| `Edm.Binary` | `binary` | `VARBINARY(MAX)` | `BINARY` | base64 |

Las fechas y horas ABAP clásicas son hora local del sistema sin zona: `Edm.DateTime` se guarda tal cual, sin convertir a UTC (a confirmar en el spike, H15). Solo `Edm.DateTimeOffset` es un instante.

El mapeo es editable por columna en el pipeline (p. ej. forzar `VARCHAR` en un NUMC).

## 7. API (resumen)

```
GET    /api/v1/destination-types                 # plugins disponibles
GET    /api/v1/destination-types/{type}/schema   # JSON Schema del formulario
GET    /api/v1/destinations     POST /api/v1/destinations
GET    /api/v1/destinations/{id}  PATCH …  DELETE …
POST   /api/v1/destinations/test                 # sin guardar
POST   /api/v1/destinations/{id}/test
POST   /api/v1/destinations/{id}/credentials/rotate
POST   /api/v1/destinations/{id}/ddl-preview     # DDL que se generaría para un origen
```

## 8. Pantallas

1. **Destinos**: tarjetas por tipo (icono Snowflake / CSV / SQL Server), entorno, estado y pipelines asociados.
2. **Asistente de alta**: tipo → formulario dinámico → credenciales → prueba → guardar.
3. **Detalle**: *General · Pipelines · Actividad* + vista previa de DDL.

## Lo construido en la iteración 4a

Código: `backend/src/sapextract/dataflow/` (contratos del ADR 0006), `backend/src/sapextract/destinations/` y `frontend/src/features/destinations/`. Batería de conformidad: `backend/tests/contract/`.

**Diferencias con el diseño inicial (decididas al implementar):**

| Tema | Diseño | Implementado | Motivo |
|---|---|---|---|
| Estado del destino | `status` | `draft` → `active` ↔ `inactive`, `last_test_ok`, `test_outdated` y `credential_info`, como los sistemas SAP (plan 04) | Activar exige una prueba correcta con la configuración y las credenciales actuales |
| Credenciales | Clave privada en `secret` | Igual (AES-GCM envelope, `aad = secret:<id>`). Edición solo por `credentials/rotate`, que prueba enseguida | El PATCH nunca transporta secretos; la rotación se audita como `SECURITY` |
| Autenticación Snowflake | Key-pair u OAuth | Solo key-pair (PKCS#8, con o sin contraseña) | Es lo recomendado para usuarios de servicio; OAuth se añadirá si un cliente lo exige |
| Organización CSV | `{source}/{yyyy}/{MM}/{dd}/…_{batch_id}_{part}.csv` | `<destino>/<target>/batches/<batch_id>/part-NNNNN.csv[.gz]` (append) y `<target>/current/` (replace) | La ruta solo depende del `batch_id`: repetir el lote siempre cae en la misma carpeta (idempotencia sin fechas) |
| Formato CSV | Comillas, escape, formato de fecha/decimal | Separador, codificación (`utf-8`/`utf-8-sig`), encabezado, fin de línea, gzip y filas por archivo | Fechas ISO 8601 y decimales con `.` fijos (tabla §6); el resto, cuando un consumidor lo pida |
| Tabla de control CSV | `_manifest.json` + `_SUCCESS` | Además `<target>/_sx_load_state/<batch_id>.json`, escrito tras publicar | `applied_batch` lee un archivo pequeño sin recorrer carpetas |
| `replace` CSV | Carpeta `current/` reemplazada | Se escribe en `.tmp-<batch_id>` y se intercambia con `current/` por renombrado | Hay una ventana mínima (dos renombrados) sin `current/`; en un sistema de archivos no existe un intercambio atómico de carpetas |
| Prueba de red Snowflake | Lista de hosts | Comprobación estática del host (`<cuenta>.snowflakecomputing.com`) y resolución previa | El conector abre sus propios sockets: un *DNS rebinding* entre la comprobación y la conexión no queda cubierto. Se documenta como riesgo residual |
| Vista previa de DDL | Para un origen | Recibe `{sap_system_id, service, version, entity_set}` y las opciones de carga, lee el `$metadata` del explorador SAP y genera el DDL sin ejecutarlo | Los orígenes (It3) aún no existen; cuando lleguen, se aceptará también un `source_id` |

**Plugins** (`destinations/application/plugins.py`). Cada tipo aporta su modelo Pydantic de configuración y de secreto, los modos que admite, `validate` (hosts y raíces), `credential_info`, `test`, `open_writer` y `ddl`. El frontend pinta el formulario a partir de `GET /destination-types/{type}/schema` (la lista de raíces CSV llega como `enum`).

**Prueba de conexión por pasos:**

- *CSV*: `path` (la carpeta queda dentro de una raíz permitida, sin `..`, rutas absolutas ni enlaces que salgan de ella) y `write` (crea, lee y borra un archivo; aviso con menos de 1 GB libre).
- *Snowflake*: `network` (host permitido y resolución), `authentication` (par de claves; códigos `SX-DST-AUTH`), `context` (`CURRENT_ROLE/WAREHOUSE/DATABASE/SCHEMA`) y `privileges` (crear tabla y stage temporales; `SX-DST-PRIVILEGES`).

**Carga en Snowflake** (`snowflake_plugin.py`, SQL en `snowflake_sql.py`, probado sin conexión):

1. `prepare`: tabla de control `__SX_LOAD_STATE`, `CREATE TABLE IF NOT EXISTS` y evolución de esquema (`add_columns`/`ignore`/`fail`), todo fuera de la transacción.
2. `stage`: stage y tabla staging temporales `__SX_STAGE_<lote>`/`__SX_STG_<lote>`, `PUT` de los Parquet del spool y `COPY INTO … MATCH_BY_COLUMN_NAME = CASE_SENSITIVE`.
3. `apply`: `BEGIN` → `MERGE` (deduplicado por clave con `QUALIFY ROW_NUMBER() … ORDER BY _sx_record_seq DESC`), `DELETE`+`INSERT` (append) o `INSERT OVERWRITE` (replace) → fila de control → `COMMIT`. Ante cualquier error, `ROLLBACK`.
4. `cleanup`: borra la tabla staging y el stage.

Identificadores en mayúsculas y siempre citados; los nombres SAP con `/` se convierten (`/BIC/ZFIELD` → `BIC_ZFIELD`) y un dígito inicial recibe el prefijo `C_`. Los valores van siempre como parámetros.

**Reglas añadidas:**

- Código `^[A-Z][A-Z0-9_]{2,29}$`; identificadores SQL `^[A-Za-z_][A-Za-z0-9_]{0,127}$` (`SX-DST-IDENTIFIER-INVALID`).
- `column_mapping` no puede excluir una columna clave ni producir dos columnas con el mismo nombre (`SX-DST-MAPPING-*`).
- CSV no admite `upsert` (`SX-DST-MODE-UNSUPPORTED`) ni `delete_handling = hard`.
- Errores del conector clasificados (`ErrorKind`): autenticación y privilegios son de configuración (no se reintentan); el resto, transitorios.
- PRD exige `SX_DEST_ALLOWED_HOSTS`. En el compose de desarrollo: `SX_CSV_ROOTS=["/data/csv"]` (volumen `csvdata`) y `SX_DEST_ALLOWED_HOSTS=["snowflakecomputing.com"]`.
- Auditoría: `destination.created|updated|deleted|tested|activated|deactivated` (`CONFIG`) y `destination.credentials_rotated` (`SECURITY`); el secreto aparece siempre como `***`.
- Los pipelines (It6) registrarán sus dependencias en `DestinationsToolkit.dependents`: impiden borrar un destino con pipelines activos y se listan en la pestaña *Pipelines*.

**Pruebas:** unitarias del núcleo `dataflow` (esquema, `batch_id`, spool, tipos) y de los dominios de destino; batería de conformidad (`DestinationContract`) en memoria, CSV y CSV gzip, y en Snowflake real cuando hay `SX_TEST_SNOWFLAKE_*`; integración de la API con PostgreSQL real (CSV real y Snowflake con la prueba de red simulada); MSW y Vitest para las pantallas; E2E del alta de un destino CSV y accesibilidad de las pantallas nuevas.

**API real:** la del §7 más `POST /destinations/{id}/activate` y `POST /destinations/{id}/deactivate`. Permisos `destination:read|manage|test`.

## Tareas

**It4a (Fase 1)**
- [x] Modelo `destination`, registro de plugins, formularios por JSON Schema.
- [x] Lista de hosts permitidos, `SX_CSV_ROOTS` y validación de identificadores, con sus pruebas.
- [x] Plugin **CSV** (primero: el más simple, permite probar el motor de punta a punta).
- [x] Plugin **Snowflake** (key-pair, `PUT`/`COPY`/`MERGE`, `INSERT OVERWRITE`, tabla de control).
- [x] Mapeo de tipos con pruebas unitarias exhaustivas.
- [ ] Pruebas de integración de Snowflake contra la cuenta de pruebas: job de CI con secretos, obligatorio en los PR que toquen `destinations/` o `extraction/`. La generación de SQL se prueba sin conexión. *La batería ya existe (`TestSnowflakeDestination`) y se activa con `SX_TEST_SNOWFLAKE_*`; falta ejecutarla con la cuenta de pruebas y crear el job de CI con sus secretos.*
- [x] Pantallas 1–3.

**It4b (Fase 3)**
- [ ] Plugin **SQL Server** (staging, `MERGE`, `SWITCH`, DDL, evolución de esquema, tabla de control).
- [ ] Pruebas de integración con testcontainers y medición de `fast_executemany` con 10 M de filas.

## Criterios de aceptación

- CSV y Snowflake (It4a) y SQL Server (It4b) superan su prueba de conexión y una carga `append`, `upsert` (excepto CSV) y `replace`.
- Repetir la carga del mismo lote no duplica filas en ningún destino.
- Durante un `replace` el destino nunca queda vacío para los consumidores, y estos conservan sus permisos.
- Una ruta fuera de `SX_CSV_ROOTS`, un host no permitido o un identificador malicioso se rechazan.
