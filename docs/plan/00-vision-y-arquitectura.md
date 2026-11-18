# 00 · Visión, arquitectura y decisiones técnicas

> Proyecto: **SAPExtract** (nombre provisional)
> Estado: borrador para aprobación
> Fecha: 17.11.2026

## 1. Visión

SAPExtract es una plataforma web para **orquestar extracciones de datos desde SAP** hacia destinos analíticos, con una interfaz visual pensada para que un usuario funcional/técnico pueda:

1. Registrar sistemas SAP (conexiones).
2. Dar de alta **orígenes**: extractores (DataSources estándar y Z) y vistas CDS publicadas desde SAP.
3. Elegir un **destino**: SQL Server, archivos CSV o Snowflake.
4. Definir **cuándo** se ejecuta (frecuencia, ventanas, dependencias) y **cómo** (full, inicialización de delta, delta).
5. Monitorizar ejecuciones, volúmenes, errores y auditoría de todo lo que pasa.

A futuro (fuera del alcance inicial): **streaming** de datos casi en tiempo real (ver [12-streaming-roadmap-futuro.md](12-streaming-roadmap-futuro.md)).

## 2. Alcance

### Dentro del alcance inicial (MVP + v1)

| Área | Incluye |
|---|---|
| Administración | Usuarios, roles, permisos, alta/baja/bloqueo, sesiones, usuario administrador local, SSO con Microsoft Entra ID configurable desde la plataforma |
| Auditoría y logs | Bitácora de auditoría inmutable, logs técnicos, logs por ejecución, retención y exportación |
| Conexiones SAP | Alta de sistemas SAP vía OData (SAP Gateway), prueba de conexión, descubrimiento de servicios |
| Orígenes | Extractores ODP (estándar y Z) y CDS, con lectura de metadatos y vista previa |
| Deltas | Full, inicialización de delta (con/sin datos), delta, repetición, reinicio controlado, pseudo-delta por marca de tiempo |
| Destinos | SQL Server, CSV (disco local / carpeta compartida), Snowflake |
| Orquestación | Pipelines origen→destino, programación cron/intervalo, ejecución manual, reintentos, bloqueo de concurrencia, pausa/reanudación |
| Monitoreo | Dashboard, historial de ejecuciones, logs en vivo, alertas por correo / Teams |

### Fuera del alcance inicial

- Streaming / CDC en tiempo real.
- Transformaciones complejas (más allá de selección/renombrado de columnas y filtros).
- Destinos adicionales (Azure Data Lake, S3, Databricks, PostgreSQL…): la arquitectura los prevé como *plugins*.
- Escritura hacia SAP (la plataforma es de **solo lectura** sobre SAP).

## 3. Decisión clave: cómo se extrae de SAP

| Opción | Descripción | Decisión |
|---|---|---|
| **ODP vía OData (SAP Gateway)** | El extractor o la CDS se expone como servicio OData basado en ODP (SEGW → *Redefine → ODP Extraction*). El delta lo gestiona la ODQ de SAP mediante *delta tokens* (`Prefer: odata.track-changes`). | ✅ **Opción principal** para extractores y CDS con delta. |
| **CDS publicada como OData** (`@OData.publish`, o *service binding* RAP en S/4HANA) | Lectura de la CDS como servicio OData normal. Sin delta nativo salvo que pase por ODP. | ✅ Soportada para CDS sin ODP, con **pseudo-delta** por campo de fecha/hora de modificación. |
| ODP vía RFC (`RODPS_REPL_ODP_*`, pyrfc / NW RFC SDK) | Acceso directo a la API RFC de ODP. | ❌ **Descartada.** La nota SAP 3255746 indica que el uso de la API RFC de ODP por clientes y terceros no está permitido; solo se soportan las interfaces OData. Además exige el NW RFC SDK y su licenciamiento. |

Consecuencia: **todo origen SAP debe publicarse desde SAP como servicio OData**. La plataforma incluirá una guía para el equipo SAP (ver [05-origenes-sap.md](05-origenes-sap.md), sección "Lado SAP").

> ⚠️ Esta decisión se valida en el **Spike SAP** de la iteración 0: hay que comprobar versión de SAP (ECC / S/4HANA), disponibilidad de SAP Gateway, comportamiento del delta token y de la repetición de deltas en el sistema real.

## 4. Arquitectura lógica

```
                    ┌──────────────────────────────────────────────┐
                    │               Navegador (SPA)                │
                    │  React + TypeScript (plantilla corporativa)  │
                    └───────────────┬──────────────────────────────┘
                                    │ HTTPS (cookie de sesión httpOnly)
                    ┌───────────────▼──────────────────────────────┐
                    │         Reverse proxy (Nginx / IIS)           │
                    └───────┬───────────────────────────┬──────────┘
                            │ /api                       │ /  (estáticos)
              ┌─────────────▼───────────┐                │
              │   API (FastAPI)         │◄── OIDC ──► Microsoft Entra ID
              │  - REST /api/v1         │
              │  - SSE eventos en vivo  │
              └──────┬──────────────────┘
                     │ SQLAlchemy
        ┌────────────▼────────────────────────────────────┐
        │     Base de metadatos (PostgreSQL)              │
        │  usuarios, roles, conexiones, pipelines,        │
        │  estado de delta, cola de trabajos, auditoría   │
        └───────▲──────────────────────▲──────────────────┘
                │                      │ SELECT … FOR UPDATE SKIP LOCKED
       ┌────────┴────────┐    ┌────────┴──────────────────────────┐
       │  Scheduler      │    │  Workers (N procesos)             │
       │  (líder único,  │    │  SourceConnector → Arrow batches  │
       │   advisory lock)│    │  → DestinationWriter              │
       └─────────────────┘    └──────┬──────────────┬─────────────┘
                                     │ OData/HTTPS  │
                              ┌──────▼─────┐  ┌─────▼──────────────────────┐
                              │ SAP Gateway│  │ SQL Server / CSV / Snowflake│
                              │ ODP / CDS  │  └────────────────────────────┘
                              └────────────┘
```

### Procesos (misma base de código, distintos *entrypoints*)

| Proceso | Responsabilidad | Escala |
|---|---|---|
| `api` | REST, autenticación, validaciones, SSE | Horizontal (stateless) |
| `scheduler` | Calcula próximas ejecuciones y encola trabajos | 1 activo (líder por *advisory lock*; réplicas en espera) |
| `worker` | Ejecuta extracciones y cargas | Horizontal; pools por prioridad |
| `frontend` | Archivos estáticos de la SPA | Servido por el proxy |

### Estilo de arquitectura del backend

**Monolito modular** con capas limpias (hexagonal) por módulo. Cada módulo de negocio es un paquete con la misma estructura:

```
backend/src/sapextract/<modulo>/
  api/              # routers FastAPI + esquemas Pydantic (DTO de entrada/salida)
  application/      # casos de uso / servicios (orquestan dominio + puertos)
  domain/           # entidades, value objects, reglas, excepciones (sin dependencias de frameworks)
  infrastructure/   # repositorios SQLAlchemy, adaptadores externos (SAP, Snowflake…)
```

Reglas:

- `domain` no importa nada de `api` ni de `infrastructure`.
- Los módulos se comunican por **servicios de aplicación** o **eventos de dominio** internos, nunca accediendo a tablas de otro módulo.
- Los conectores (orígenes y destinos) se implementan contra **interfaces (puertos)** y se registran en un *registry*: añadir un destino nuevo = añadir un plugin, sin tocar el motor.

## 5. Stack tecnológico

| Capa | Tecnología | Motivo |
|---|---|---|
| Lenguaje backend | **Python 3.14** | Ecosistema de datos (Arrow, conectores Snowflake/SQL Server), rapidez de desarrollo |
| Framework API | **FastAPI** + **Pydantic v2** | Tipado, validación, OpenAPI automático (se genera el cliente TS del frontend) |
| ORM / migraciones | **SQLAlchemy 2.0** + **Alembic** | Estándar maduro, migraciones versionadas |
| Base de metadatos | **PostgreSQL 16** | Cola transaccional (`SKIP LOCKED`), `LISTEN/NOTIFY`, JSONB, *advisory locks* |
| Cliente HTTP / OData | **httpx** + **lxml** (parser de `$metadata`) | Asíncrono/síncrono, timeouts finos, reintentos |
| Datos en memoria | **Apache Arrow (pyarrow)** | Lotes columnares, tipado fuerte, escritura eficiente a CSV/Parquet |
| SQL Server | **pyodbc** + ODBC Driver 18 (`fast_executemany`) | Carga masiva a *staging* + `MERGE` |
| Snowflake | **snowflake-connector-python** (autenticación por *key-pair*) | `PUT` a stage interno + `COPY INTO` + `MERGE` |
| Programación | **croniter** + scheduler propio | Cron con zona horaria; sin dependencia de Redis/Celery |
| Cola de trabajos | Tabla en PostgreSQL (`SKIP LOCKED`) | Transaccional, auditable, menos infraestructura |
| Autenticación | **Authlib** (OIDC Entra ID), **argon2-cffi** (contraseñas locales), **pyotp** (MFA local) | Estándares OIDC/PKCE |
| Cifrado de secretos | **cryptography** (AES-256-GCM, cifrado *envelope*) | Credenciales SAP/BD nunca en claro |
| Logs | **structlog** (JSON) + OpenTelemetry (opcional) | Logs estructurados correlacionados por `request_id` / `run_id` |
| Calidad backend | **uv**, **ruff**, **mypy (strict)**, **pytest**, **testcontainers** | Gestión de dependencias rápida, lint, tipado, pruebas de integración reales |
| Frontend | **React 19 + TypeScript + Vite** | SPA moderna; se adapta a la plantilla que se entregue |
| Estado servidor | **TanStack Query** | Caché, reintentos, invalidación |
| Tablas | **TanStack Table** | Listados con filtros, orden y paginación en servidor |
| Formularios | **React Hook Form + Zod** | Validación declarativa compartida con el contrato OpenAPI |
| Diseñador visual | **React Flow (@xyflow/react)** | Lienzo origen → destino arrastrable |
| Cliente API | **openapi-typescript** + `openapi-fetch` | Tipos generados desde el OpenAPI del backend: contrato único |
| i18n | **react-i18next** (es por defecto) | Preparado para otros idiomas |
| Calidad frontend | **pnpm**, **ESLint**, **Prettier**, **Vitest**, **Testing Library**, **Playwright** | Unitarias, componentes y E2E |
| Contenedores | **Docker** + **docker compose** | Mismo artefacto en DEV/QA/PRD |

> La librería de componentes visuales **no se fija todavía**: depende de la plantilla que se entregará. El plan [10-frontend-y-ux.md](10-frontend-y-ux.md) define una capa de *design system* propia para que la plantilla se integre sin reescribir pantallas.

## 6. Separación backend / frontend

- **Repositorio único (monorepo)** con dos aplicaciones independientes: `backend/` y `frontend/`.
- El **contrato** entre ambos es el OpenAPI que genera FastAPI (`/api/v1/openapi.json`). El frontend genera sus tipos a partir de él en CI; un cambio incompatible rompe el build.
- El frontend no contiene lógica de negocio: valida formato (UX) y delega reglas al backend.
- Versionado de API por prefijo (`/api/v1`). Cambios incompatibles → `/api/v2`.
- Errores con formato estándar **RFC 9457 (Problem Details)**: `type`, `title`, `status`, `detail`, `instance`, `errors[]`, `request_id`.

## 7. Principios y buenas prácticas transversales

1. **Seguridad por defecto**: mínimo privilegio (RBAC), secretos cifrados, cookies `httpOnly`/`Secure`/`SameSite`, CSRF, cabeceras de seguridad, sin secretos en logs.
2. **Todo cambio de configuración se audita** (quién, qué, cuándo, desde dónde, antes/después).
3. **Idempotencia** en cargas: una ejecución reintentada no duplica datos.
4. **El token de delta solo avanza si la carga en destino fue exitosa.**
5. **Observabilidad**: cada petición y ejecución lleva un identificador de correlación.
6. **Configuración por entorno** con `pydantic-settings` (variables de entorno / `.env`), nunca valores en código.
7. **Pruebas**: pirámide (unitarias de dominio > integración con contenedores > E2E críticos). Cobertura mínima 80 % en `domain` y `application`.
8. **Clean Code**: tipado estricto, funciones pequeñas, nombres explícitos, sin lógica en routers, inyección de dependencias por constructor.
9. **Migraciones** siempre con Alembic, revisadas en PR; nunca `create_all` en producción.
10. **Documentación viva**: ADRs en `docs/adr/` para cada decisión relevante.

## 8. Modelo de dominio (vista general)

```
SapSystem (conexión) 1───* Source (extractor ODP | CDS)
Source 1───* Pipeline *───1 Destination
Pipeline 1───1 DeltaState            (token, modo, estado de inicialización)
Pipeline 1───* Schedule
Pipeline 1───* Run 1───* RunStep / RunLog
User *───* Role *───* Permission
AuditEvent (append-only, encadenado por hash)
IdentityProviderConfig (Entra ID)  ·  Secret (cifrado)
```

Detalle de cada entidad en el plan del módulo correspondiente.

## 9. Mapa de planes

| # | Plan | Contenido |
|---|---|---|
| 00 | Este documento | Visión, arquitectura, stack |
| 01 | [Fundaciones](01-fundaciones.md) | Monorepo, esqueletos, CI, Docker, estándares de código |
| 02 | [Usuarios, autenticación y roles](02-usuarios-autenticacion-roles.md) | Admin local, Entra ID, RBAC, ciclo de vida de usuarios |
| 03 | [Auditoría y logs](03-auditoria-y-logs.md) | Bitácora inmutable, logs técnicos y de ejecución |
| 04 | [Conexiones SAP](04-conexiones-sap.md) | Sistemas SAP, credenciales, descubrimiento |
| 05 | [Orígenes SAP](05-origenes-sap.md) | Extractores ODP, CDS, metadatos, guía lado SAP |
| 06 | [Motor de extracción y deltas](06-motor-extraccion-y-deltas.md) | Lectura paginada, gestión de deltas, idempotencia |
| 07 | [Destinos](07-destinos.md) | SQL Server, CSV, Snowflake, mapeo de tipos |
| 08 | [Orquestación y programación](08-orquestacion-y-programacion.md) | Pipelines, scheduler, cola, reintentos |
| 09 | [Monitoreo y alertas](09-monitoreo-y-alertas.md) | Dashboard, métricas, notificaciones |
| 10 | [Frontend y UX](10-frontend-y-ux.md) | Integración de plantilla, pantallas, asistentes |
| 11 | [Despliegue y operación](11-despliegue-y-operacion.md) | Entornos, contenedores, backups, runbooks |
| 12 | [Streaming y evolución futura](12-streaming-roadmap-futuro.md) | Streaming, nuevos destinos, DAGs |
| — | [ROADMAP](ROADMAP.md) | Orden de iteraciones y dependencias |

## 10. Decisiones abiertas (a confirmar)

| # | Pregunta | Impacto | Valor por defecto asumido |
|---|---|---|---|
| D1 | ¿Versión de SAP: ECC (con Gateway) o S/4HANA? ¿Uno o varios sistemas? | Forma de publicar CDS, anotaciones disponibles | Ambos soportados vía OData |
| D2 | ¿Servidor de despliegue: Linux con Docker o Windows Server? | Empaquetado y servicios | Docker (Linux); alternativa Windows documentada en plan 11 |
| D3 | ¿Base de metadatos: PostgreSQL o se exige SQL Server corporativo? | Cola y bloqueos (se usan funciones de PostgreSQL) | PostgreSQL |
| D4 | ¿Herramienta de CI/CD: GitHub Actions o Azure DevOps? | Pipelines de build | GitHub Actions (traducible) |
| D5 | Volumen esperado (filas/día por origen) y ventanas de carga en SAP | Tamaño de paquete, paralelismo, workers | 1–50 M filas/día |
| D6 | ¿SAP es accesible directamente desde el servidor o vía SAP Cloud Connector / proxy? | Conectividad | Acceso directo HTTPS a Gateway |
| D7 | Canal de alertas: SMTP, Microsoft Graph (correo) y/o Teams | Módulo 09 | SMTP + webhook de Teams |
| D8 | Nombre definitivo de la plataforma | Branding, paquetes | `sapextract` (renombrable) |
