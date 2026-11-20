# 01 · Fundaciones del proyecto

**Iteración:** 0 · **Depende de:** nada · **Bloquea a:** todos los módulos

## Objetivo

Dejar listo el esqueleto técnico sobre el que se construirán todos los módulos: monorepo, backend y frontend arrancando, base de metadatos con migraciones, calidad automatizada, contenedores y un *spike* que valide la conexión real con SAP.

## 1. Estructura del monorepo

```
SAPExtract/
├── backend/
│   ├── pyproject.toml            # uv, ruff, mypy, pytest
│   ├── alembic.ini
│   ├── migrations/               # Alembic
│   ├── src/sapextract/
│   │   ├── main_api.py           # entrypoint API
│   │   ├── main_scheduler.py     # entrypoint scheduler
│   │   ├── main_worker.py        # entrypoint worker
│   │   ├── cli.py                # comandos (crear admin, rotar clave, etc.)
│   │   ├── core/                 # config, db, seguridad, errores, logging, DI, eventos
│   │   ├── identity/             # módulo 02
│   │   ├── audit/                # módulo 03
│   │   ├── sap_connections/      # módulo 04
│   │   ├── sources/              # módulo 05
│   │   ├── extraction/           # módulo 06 (motor, deltas, conectores origen)
│   │   ├── destinations/         # módulo 07
│   │   ├── orchestration/        # módulo 08
│   │   └── monitoring/           # módulo 09
│   └── tests/
│       ├── unit/
│       ├── integration/          # testcontainers (PostgreSQL, SQL Server)
│       └── fixtures/sap/         # respuestas OData grabadas ($metadata, páginas, deltas)
├── frontend/
│   ├── package.json              # pnpm
│   ├── vite.config.ts
│   ├── src/
│   │   ├── app/                  # router, providers, layout
│   │   ├── shared/               # design system, hooks, utils, cliente API generado
│   │   └── features/<modulo>/    # pantallas por módulo (api, components, pages, schemas)
│   └── tests/e2e/                # Playwright
├── deploy/
│   ├── docker/                   # Dockerfiles
│   ├── compose/                  # docker-compose.dev.yml / .prod.yml
│   └── nginx/
├── tools/sap-mock/               # servidor OData simulado para desarrollo y pruebas
├── docs/
│   ├── plan/                     # estos planes
│   ├── adr/                      # Architecture Decision Records
│   └── runbooks/
├── .github/workflows/            # CI (o azure-pipelines.yml, ver D4)
├── .editorconfig
├── .gitignore
├── CLAUDE.md
└── README.md
```

## 2. Backend: núcleo (`core/`)

| Componente | Detalle |
|---|---|
| `config.py` | `Settings` con `pydantic-settings`; prefijo `SX_`; validación al arranque (falla rápido si falta algo). |
| `db.py` | Engine SQLAlchemy 2.0, `SessionLocal`, patrón *Unit of Work* (`with uow: ... uow.commit()`). |
| `errors.py` | Jerarquía de excepciones de dominio → *handler* global que responde Problem Details (RFC 9457). |
| `logging.py` | structlog JSON; contexto `request_id`, `user_id`, `run_id`; filtro que **enmascara secretos**. |
| `security/crypto.py` | Cifrado AES-256-GCM *envelope*: clave maestra (`SX_MASTER_KEY` o Azure Key Vault) cifra claves de datos. Soporta rotación (`key_version`). |
| `middleware.py` | `request_id`, cabeceras de seguridad (CSP, HSTS, X-Content-Type-Options, Referrer-Policy), límite de tamaño de petición. |
| `pagination.py` | Paginación estándar `?page=&page_size=&sort=&filter=` → respuesta `{items, total, page, page_size}`. |
| `events.py` | Bus de eventos de dominio en proceso (p. ej. `UserDeactivated` → auditoría, revocar sesiones). |
| `health.py` | `/api/v1/health/live`, `/api/v1/health/ready` (BD, migraciones al día) y `/api/v1/system/info`. |

Convenciones:

- Tablas de metadatos en esquema `sx` de PostgreSQL; nombres en `snake_case`; PK `id` UUID v7 (ordenable).
- Columnas comunes: `created_at`, `created_by`, `updated_at`, `updated_by`, `version` (bloqueo optimista).
- Borrado lógico (`deleted_at`) en entidades de configuración; nunca en auditoría.

## 3. Frontend: esqueleto

- Vite + React + TS en modo `strict`.
- Router con *layout* base (menú lateral, cabecera, área de contenido) **provisional** hasta recibir la plantilla.
- Proveedores: TanStack Query, i18n, tema, sesión.
- Script `pnpm gen:api` que genera `src/shared/api/schema.d.ts` desde el OpenAPI del backend.
- Página de *login* y página 404 de ejemplo.

## 4. Calidad y CI

| Paso | Backend | Frontend |
|---|---|---|
| Formato / lint | `ruff format --check`, `ruff check` | `prettier --check`, `eslint` |
| Tipos | `mypy --strict` | `tsc --noEmit` |
| Pruebas | `pytest` (unit + integración con testcontainers), con cobertura mínima (`fail_under` 80 % en `domain` y `application`) y aserción de una sola cabeza de Alembic | `vitest`; Playwright (E2E del flujo principal contra `docker compose` y el mock) con `axe` |
| Contrato | Exporta `openapi.json` como artefacto | `gen:api` + `tsc` contra el OpenAPI recién generado |
| Seguridad | `pip-audit`, `bandit` | `pnpm audit` |
| Build | Imagen Docker | Imagen Docker (Nginx con estáticos) |

Además: *pre-commit hooks* (ruff, prettier, detección de secretos con `gitleaks`), *Conventional Commits*, ramas `main` + `feature/*`, PR con revisión obligatoria.

## 5. Contenedores (desarrollo)

`deploy/compose/docker-compose.dev.yml`:

| Servicio | Imagen | Puerto |
|---|---|---|
| `db` | postgres:16 | 5432 |
| `api` | backend (recarga en caliente) | 8000 |
| `scheduler` | backend | — |
| `worker` | backend | — |
| `frontend` | node (vite dev) | 5173 |
| `sap-mock` | tools/sap-mock | 8081 |
| `mssql` (opcional) | SQL Server 2022 | 1433 |

## 6. Mock de SAP (`tools/sap-mock`)

Servidor FastAPI mínimo que imita SAP Gateway para desarrollar sin SAP:

- `GET /sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection` (catálogo).
- `$metadata` de un servicio ODP de ejemplo y de una CDS.
- Paginación con `$skiptoken`, respuestas `Prefer: odata.track-changes`, `__delta` links, registros con `ODQ_CHANGEMODE`.
- Escenarios de error configurables (401, 500, *timeout*, token de delta inválido).

Se alimenta con las respuestas reales grabadas en el Spike SAP (anonimizadas).

## 7. Spike SAP (validación técnica temprana)

Objetivo: confirmar las hipótesis de [06-motor-extraccion-y-deltas.md](06-motor-extraccion-y-deltas.md) antes de construir el motor.

Tareas:

1. Con el equipo SAP, publicar **un extractor estándar** (p. ej. un DataSource de FI o SD con delta) vía SEGW ODP y **una CDS** con anotaciones de extracción.
2. Desde un script Python aislado:
   - Leer `$metadata`.
   - Ejecutar inicialización de delta con datos y paginar hasta el final.
   - Obtener el *delta link*, generar cambios en SAP y leer el delta.
   - **Repetir** el mismo delta (re-solicitar con el token anterior) y documentar el comportamiento.
   - Medir rendimiento (filas/seg) con distintos tamaños de página.
3. Revisar en ODQMON la suscripción creada y cómo se elimina.
4. Grabar respuestas para el mock y documentar resultados en `docs/adr/0002-extraccion-sap-odp-odata.md`.

El ADR lista las hipótesis H1–H18. Desde el 20.11.2026 el script `tools/spike-sap/spike_odp.py` las cubre todas; cada prueba se elige con `--hypotheses` y el plan de sesiones está en `tools/spike-sap/README.md`. Orden: primero H6, H12 y H18, porque deciden si dos pipelines pueden leer en delta el mismo origen.

## 8. ADRs iniciales

| ADR | Título |
|---|---|
| 0001 | Monolito modular Python/FastAPI + SPA React |
| 0002 | Extracción SAP vía ODP/OData (resultado del spike) |
| 0003 | PostgreSQL como base de metadatos y cola de trabajos |
| 0004 | Sesiones de servidor con cookie httpOnly (patrón BFF) en lugar de JWT en el navegador |
| 0005 | Cifrado *envelope* de secretos |
| 0006 | Contratos del motor: esquema, lote, spool, run y puertos (propuesto; el estado de delta va en un ADR posterior al spike) |

## Tareas

- [x] Crear monorepo, `.gitignore`, `.editorconfig`, `README.md`, `CLAUDE.md`.
- [x] Backend: proyecto `uv`, estructura de paquetes, `core/`, entrypoints, `/health`.
- [x] Alembic configurado con primera migración (esquema `sx`, tabla `system_setting`).
- [x] Frontend: Vite + React + TS, router, layout provisional, `gen:api`.
- [x] Docker compose de desarrollo funcionando con un comando (`docker compose up --build`).
- [x] CI con todos los pasos de la tabla de calidad (`.github/workflows/ci.yml`, pendiente decisión D4).
- [x] Pre-commit hooks (`.pre-commit-config.yaml` + gitleaks).
- [x] `tools/sap-mock` con catálogo, `$metadata`, paginación, init/delta/repetición y escenarios de error.
- [x] Script del spike (`tools/spike-sap`) y ADR 0002 con hipótesis.
- [ ] **Ejecutar el spike contra el SAP real de AG** y registrar resultados en ADR 0002 (requiere servicios publicados por el equipo SAP).

**Fase 0 (plan 13)**
- [x] Ampliar `spike_odp.py` con H8–H18 y comparar la repetición por claves y `ODQ_ENTITYCNTR`, no por recuento (20.11.2026). La comparación usa una huella por registro: claves, `ODQ_CHANGEMODE`, `ODQ_ENTITYCNTR` y contenido. `tools/sap-mock/tests/test_spike_against_mock.py` ejecuta todas las pruebas contra el mock en CI. Se corrigió además un fallo del script anterior: con `--client`, httpx sustituía la query de los enlaces de SAP y se perdían `$skiptoken` y `!deltatoken`.
- [ ] Mock: `SubscribedTo…` y `TerminateDeltasFor…` cuando H12 se confirme; respuestas reales grabadas en el spike.
- [x] CI: `fail_under` de cobertura, job de Playwright con `axe`, aserción de una sola cabeza de Alembic.
  - Cobertura: `coverage report --fail-under=80` sobre `*/domain/*` y `*/application/*` (hoy, 90 %).
  - Alembic: `tests/unit/test_migrations.py` exige una sola cabeza, cadena lineal y numeración correlativa.
  - E2E: job `e2e` que levanta `docker compose`, activa el administrador inicial (`tests/e2e/global-setup.ts`) y ejecuta Playwright con el flujo de usuarios, el de sistemas SAP y `axe` (cero incumplimientos graves o críticos en login, inicio, sistemas SAP, asistente, usuarios y auditoría).
- [x] ADR 0006 con los contratos del motor ([propuesto el 20.11.2026](../adr/0006-contratos-del-motor.md), pendiente de revisión conjunta). El estado de delta se fija en un ADR posterior, tras el spike.

## Criterios de aceptación

- `docker compose up` levanta todo y `GET /api/v1/health/ready` responde 200.
- El frontend muestra el layout y consume `/api/v1/health` con el cliente tipado.
- CI en verde con lint, tipos, pruebas y build de imágenes.
- ADR 0002 documenta el comportamiento real del delta en el SAP de AG.
