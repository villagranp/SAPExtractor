# SAPExtract · Backend

API (FastAPI), scheduler y workers. Arquitectura y decisiones en [`docs/plan`](../docs/plan/00-vision-y-arquitectura.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) y Python 3.14
- PostgreSQL 16 (el `docker compose` de desarrollo lo levanta)

## Comandos

```bash
uv sync                                   # dependencias (incluye grupo dev)
cp ../.env.example ../.env                # y ajustar valores
uv run sapextract db upgrade              # migraciones + catálogo de permisos/roles + admin inicial
uv run uvicorn sapextract.main_api:create_app --factory --reload
uv run python -m sapextract.main_scheduler
uv run python -m sapextract.main_worker
```

Administración desde el servidor:

```bash
uv run sapextract create-admin --username admin2 --generate   # admin local de emergencia (break-glass)
uv run sapextract reset-password --username admin             # recuperación: contraseña temporal
uv run sapextract audit verify                                # recalcula la cadena de hash de auditoría
uv run sapextract generate-master-key                         # valor para SX_MASTER_KEY
```

`db upgrade` crea el administrador inicial solo si la base no tiene usuarios y están definidas
`SX_BOOTSTRAP_ADMIN_USER` y `SX_BOOTSTRAP_ADMIN_PASSWORD` (contraseña temporal: se cambia al entrar).

## Reglas del módulo de identidad y auditoría

- Los endpoints exigen permisos con `require_permission("recurso:acción")` (`identity/api/deps.py`);
  las sesiones por cookie exigen además la cabecera `X-CSRF-Token` en métodos que modifican datos.
- Todo endpoint `POST/PUT/PATCH/DELETE` declara sus eventos con `openapi_extra=audited(...)`
  (o `not_audited("motivo")`); una prueba lo comprueba sobre el OpenAPI.
- La auditoría se escribe con `AuditService.record()` en la misma transacción que el cambio;
  los intentos fallidos o denegados, en una transacción propia.

Calidad:

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run pytest -m "not integration"        # unitarias
uv run pytest -m integration              # requiere Docker (testcontainers)
uv run sapextract export-openapi --output openapi.json
```

## Estructura

```
src/sapextract/
  core/            configuración, BD, errores (RFC 9457), logs, middleware, cifrado, salud
  migrations/      Alembic (script_location = sapextract:migrations)
  <modulo>/        api/ application/ domain/ infrastructure/
  main_api.py      main_scheduler.py      main_worker.py      cli.py
```
