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

## Tareas

**It4a (Fase 1)**
- [ ] Modelo `destination`, registro de plugins, formularios por JSON Schema.
- [ ] Lista de hosts permitidos, `SX_CSV_ROOTS` y validación de identificadores, con sus pruebas.
- [ ] Plugin **CSV** (primero: el más simple, permite probar el motor de punta a punta).
- [ ] Plugin **Snowflake** (key-pair, `PUT`/`COPY`/`MERGE`, `INSERT OVERWRITE`, tabla de control).
- [ ] Mapeo de tipos con pruebas unitarias exhaustivas.
- [ ] Pruebas de integración de Snowflake contra la cuenta de pruebas: job de CI con secretos, obligatorio en los PR que toquen `destinations/` o `extraction/`. La generación de SQL se prueba sin conexión.
- [ ] Pantallas 1–3.

**It4b (Fase 3)**
- [ ] Plugin **SQL Server** (staging, `MERGE`, `SWITCH`, DDL, evolución de esquema, tabla de control).
- [ ] Pruebas de integración con testcontainers y medición de `fast_executemany` con 10 M de filas.

## Criterios de aceptación

- CSV y Snowflake (It4a) y SQL Server (It4b) superan su prueba de conexión y una carga `append`, `upsert` (excepto CSV) y `replace`.
- Repetir la carga del mismo lote no duplica filas en ningún destino.
- Durante un `replace` el destino nunca queda vacío para los consumidores, y estos conservan sus permisos.
- Una ruta fuera de `SX_CSV_ROOTS`, un host no permitido o un identificador malicioso se rechazan.
