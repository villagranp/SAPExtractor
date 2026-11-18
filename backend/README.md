# SAPExtract · Backend

API (FastAPI), scheduler y workers. Arquitectura y decisiones en [`docs/plan`](../docs/plan/00-vision-y-arquitectura.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) y Python 3.14
- PostgreSQL 16 (el `docker compose` de desarrollo lo levanta)

## Comandos

```bash
uv sync                                   # dependencias (incluye grupo dev)
cp ../.env.example ../.env                # y ajustar valores
uv run sapextract db upgrade              # aplica migraciones
uv run uvicorn sapextract.main_api:create_app --factory --reload
uv run python -m sapextract.main_scheduler
uv run python -m sapextract.main_worker
```

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
