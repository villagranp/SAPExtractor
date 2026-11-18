# SAPExtract (nombre provisional)

Plataforma web para **orquestar extracciones de datos desde SAP** — extractores ODP (estándar y Z) y vistas CDS publicadas vía OData — con **gestión de deltas** y carga hacia **SQL Server, archivos CSV o Snowflake**, con administración de usuarios (admin local + Microsoft Entra ID), auditoría y monitoreo.

> Estado: **iteración 0 completada** (fundaciones). Ver [ROADMAP](docs/plan/ROADMAP.md).

## Arranque rápido (desarrollo)

Requisitos: Docker, y para trabajar fuera de contenedores [uv](https://docs.astral.sh/uv/) (Python 3.14) y Node 24 + pnpm.

```bash
cp .env.example .env
docker compose up --build
```

| Servicio | URL |
|---|---|
| Interfaz web | http://localhost:5173 |
| API (Swagger) | http://localhost:8000/api/v1/docs |
| Salud | http://localhost:8000/api/v1/health/ready |
| Mock de SAP Gateway | http://localhost:8081/_mock/docs (usuario/clave de desarrollo: `sxmock`) |
| PostgreSQL | localhost:5432 (`sapextract` / `sapextract`, solo desarrollo) |

SQL Server de pruebas opcional: `docker compose --profile mssql up`.

## Estructura

```
backend/          API, scheduler y workers (Python/FastAPI)       → backend/README.md
frontend/         SPA (React/TypeScript/Vite)                      → frontend/README.md
tools/sap-mock/   Simulador de SAP Gateway con ODP/deltas
tools/spike-sap/  Script de validación contra el SAP real          → tools/spike-sap/README.md
deploy/           Dockerfiles, docker compose, Nginx
docs/plan/        Planes por módulo y roadmap
docs/adr/         Decisiones de arquitectura
```

## Calidad

CI (`.github/workflows/ci.yml`) ejecuta lint, tipos, pruebas (incluida integración con PostgreSQL real), auditoría de dependencias, verificación del contrato OpenAPI ↔ tipos del frontend y build de imágenes. En local: `uvx pre-commit install`.

## Planes

| Documento | Contenido |
|---|---|
| [00 · Visión y arquitectura](docs/plan/00-vision-y-arquitectura.md) | Alcance, decisión de extracción SAP, arquitectura, stack, decisiones abiertas |
| [ROADMAP](docs/plan/ROADMAP.md) | Iteraciones, hitos, dependencias, riesgos |
| [01 · Fundaciones](docs/plan/01-fundaciones.md) | Monorepo, CI, Docker, mock de SAP, spike SAP |
| [02 · Usuarios, autenticación y roles](docs/plan/02-usuarios-autenticacion-roles.md) | Admin local, Entra ID, RBAC, ciclo de vida |
| [03 · Auditoría y logs](docs/plan/03-auditoria-y-logs.md) | Bitácora inmutable, logs de ejecución y técnicos |
| [04 · Conexiones SAP](docs/plan/04-conexiones-sap.md) | Sistemas SAP, credenciales, explorador de servicios |
| [05 · Orígenes SAP](docs/plan/05-origenes-sap.md) | Extractores ODP, CDS, esquema, guía lado SAP |
| [06 · Motor de extracción y deltas](docs/plan/06-motor-extraccion-y-deltas.md) | Ejecución, protocolo de delta, idempotencia |
| [07 · Destinos](docs/plan/07-destinos.md) | SQL Server, CSV, Snowflake, mapeo de tipos |
| [08 · Orquestación y programación](docs/plan/08-orquestacion-y-programacion.md) | Pipelines, scheduler, cola, workers, reintentos |
| [09 · Monitoreo y alertas](docs/plan/09-monitoreo-y-alertas.md) | Dashboard, alertas, métricas |
| [10 · Frontend y UX](docs/plan/10-frontend-y-ux.md) | Integración de plantilla, design system, pantallas |
| [11 · Despliegue y operación](docs/plan/11-despliegue-y-operacion.md) | Entornos, backups, runbooks, seguridad |
| [12 · Streaming y evolución](docs/plan/12-streaming-roadmap-futuro.md) | Streaming y mejoras futuras |

## Stack (resumen)

- **Backend:** Python 3.14, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL 16, pyarrow, httpx.
- **Frontend:** React + TypeScript + Vite, TanStack Query/Table, React Hook Form + Zod, React Flow.
- **Destinos:** pyodbc (SQL Server), snowflake-connector-python, CSV con pyarrow.
- **Infra:** Docker / docker compose, Nginx.
