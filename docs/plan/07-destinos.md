# 07 · Destinos (SQL Server, CSV, Snowflake)

**Iteración:** 4 · **Depende de:** 01, 02, 03 · **Bloquea a:** 06, 08

## Objetivo

Registrar destinos de forma visual, probarlos y escribir en ellos los datos extraídos con cargas **masivas, transaccionales e idempotentes**. Cada destino es un **plugin** que implementa `DestinationWriter` (ver plan 06), de forma que añadir destinos futuros (ADLS, S3, PostgreSQL, Databricks…) no toca el motor.

## 1. Modelo de datos

| Tabla | Campos clave |
|---|---|
| `destination` | `id`, `code`, `name`, `type` (`sqlserver` \| `csv` \| `snowflake`), `config` (JSON no sensible, validado por esquema del plugin), `secret_id`, `environment`, `tags`, `status`, `last_test_at`, `last_test_result` |

La configuración de cada tipo se define con un **modelo Pydantic por plugin**; el frontend pinta el formulario a partir de su JSON Schema (`GET /api/v1/destination-types/{type}/schema`), así un plugin nuevo aparece en la UI sin código de frontend adicional.

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

## 3. SQL Server

| Tema | Diseño |
|---|---|
| Conexión | Servidor, puerto, base de datos, esquema, autenticación SQL o **Entra ID (service principal)** / integrada si el host lo permite, `Encrypt=yes`, `TrustServerCertificate` configurable |
| Driver | ODBC Driver 18 + `pyodbc` |
| Staging | Tabla `<esquema>.__sx_stg_<target>_<run>` creada por run |
| Carga masiva | `fast_executemany` por lotes Arrow (alternativa futura: `bcp` para volúmenes muy altos) |
| `upsert` | `MERGE` con deduplicación previa (`ROW_NUMBER() OVER (PARTITION BY clave ORDER BY _sx_record_seq DESC)`) dentro de una transacción |
| `replace` | Carga en `<target>__sx_new` y `sp_rename` / `ALTER SCHEMA TRANSFER` atómico en transacción |
| `append` | `DELETE` de las filas del mismo token + `INSERT`, en una transacción |
| DDL | Mapeo de tipos (sección 6), PK en columnas clave (para `upsert`), índice por `_sx_run_id` |
| Prueba | Conectar, comprobar permisos `CREATE TABLE`, `INSERT`, `ALTER` en el esquema |

## 4. Archivos CSV

| Tema | Diseño |
|---|---|
| Ubicación | Ruta local del servidor o **carpeta compartida (UNC/SMB)** montada en el contenedor; (futuro: Azure Blob/ADLS, S3, SFTP como plugins) |
| Formato | Delimitador, comillas, escape, encabezado sí/no, codificación (UTF-8 por defecto, UTF-8 BOM para Excel), fin de línea, formato de fecha/decimal |
| Compresión | Ninguna o `gzip` |
| Organización | Patrón de nombre con variables: `{source}/{yyyy}/{MM}/{dd}/{source}_{mode}_{run_id}_{part:04}.csv` |
| Particionado | Corte por número de filas o tamaño (p. ej. 1 M filas / 500 MB) |
| Atomicidad | Escritura en `*.tmp` y renombrado al final; archivo `_manifest.json` por run (archivos, filas, hash SHA-256, esquema, modo, token) y archivo `_SUCCESS` |
| Modos | `append` = archivos nuevos por run; `replace` = carpeta `current/` reemplazada; `upsert` **no aplica** (se avisa en el asistente) |
| Prueba | Crear, escribir y borrar un archivo de prueba en la ruta |

## 5. Snowflake

| Tema | Diseño |
|---|---|
| Conexión | Cuenta (`<org>-<account>`), usuario de servicio, rol, *warehouse*, base de datos, esquema |
| Autenticación | **Key-pair** (clave privada cifrada en `secret`) como opción por defecto; OAuth como alternativa. No se recomienda contraseña para usuarios de servicio |
| Carga | Spool Parquet → `PUT` a *stage* interno temporal → `COPY INTO` tabla staging (`MATCH_BY_COLUMN_NAME`) → `MERGE`/`INSERT` → `REMOVE` del stage |
| `replace` | `CREATE OR REPLACE TABLE … CLONE` / `ALTER TABLE … SWAP WITH` (atómico) |
| Costes | Uso del warehouse indicado; opción de *auto-suspend* recomendada en la guía; métricas de créditos fuera de alcance v1 |
| Tipos | Mapeo sección 6; `VARCHAR` sin longitud, `NUMBER(p,s)`, `TIMESTAMP_NTZ` en UTC |
| Prueba | Conectar, `USE ROLE/WAREHOUSE/DATABASE/SCHEMA`, comprobar `CREATE TABLE` y `CREATE STAGE` |

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
| `Edm.DateTime` / `DateTimeOffset` | `timestamp[us, UTC]` | `DATETIME2(6)` | `TIMESTAMP_NTZ` | ISO 8601 |
| `Edm.Time` | `time64[us]` | `TIME(6)` | `TIME` | `HH:MM:SS` |
| `Edm.Guid` | `string` | `UNIQUEIDENTIFIER` | `VARCHAR(36)` | texto |
| `Edm.Binary` | `binary` | `VARBINARY(MAX)` | `BINARY` | base64 |

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

1. **Destinos**: tarjetas por tipo (icono SQL Server / CSV / Snowflake), estado y pipelines asociados.
2. **Asistente de alta**: tipo → formulario dinámico → credenciales → prueba → guardar.
3. **Detalle**: *General · Pipelines · Actividad* + vista previa de DDL.

## Tareas

- [ ] Modelo `destination`, registro de plugins, formularios por JSON Schema.
- [ ] Plugin **CSV** (primero: el más simple, permite probar el motor de punta a punta).
- [ ] Plugin **SQL Server** (staging, MERGE, swap, DDL, evolución de esquema).
- [ ] Plugin **Snowflake** (key-pair, PUT/COPY/MERGE, SWAP).
- [ ] Mapeo de tipos con pruebas unitarias exhaustivas.
- [ ] Pruebas de integración: SQL Server con testcontainers; Snowflake contra una cuenta/BD de pruebas (en CI opcional con secretos).
- [ ] Pantallas 1–3.

## Criterios de aceptación

- Los tres destinos superan su prueba de conexión y una carga `append`, `upsert` (excepto CSV) y `replace`.
- Repetir la carga del mismo delta no duplica filas en ningún destino.
- Durante un `replace` el destino nunca queda vacío para los consumidores.
