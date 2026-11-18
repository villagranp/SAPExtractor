# CLAUDE.md — SAPExtract

Plataforma para orquestar extracciones SAP (ODP/CDS vía OData) con deltas hacia SQL Server, CSV y Snowflake. Nombre provisional.

## Cómo trabajar en este repositorio

- La fuente de verdad del diseño son los planes en `docs/plan/`. Antes de implementar un módulo, lee `00-vision-y-arquitectura.md`, `ROADMAP.md` y el plan del módulo.
- Se implementa **una iteración a la vez** según `ROADMAP.md`. Al terminar, actualiza el plan del módulo con lo realmente construido y marca las tareas.
- Documentación, mensajes de UI y planes en **español**; código, identificadores y commits en inglés (Conventional Commits).

## Reglas de arquitectura

- Monorepo: `backend/` (Python/FastAPI) y `frontend/` (React/TS) separados; el contrato es el OpenAPI (`/api/v1`).
- Backend = monolito modular; cada módulo con capas `api/ application/ domain/ infrastructure/`. `domain` no depende de frameworks. Nada de lógica en routers.
- Conectores de origen y destino solo a través de las interfaces `SourceConnector` / `DestinationWriter` (plan 06).
- Toda acción que cambie configuración o ejecute algo emite un evento de auditoría (plan 03). Nunca secretos en logs, auditoría ni respuestas.
- El delta token solo avanza tras confirmar la carga en destino (plan 06 §3).
- Extracción SAP solo vía OData (no API RFC de ODP; ver plan 00 §3).
- Frontend: las pantallas (`features/*`) solo importan componentes de `src/shared/ui`, nunca de la plantilla directamente.
- Errores HTTP en formato Problem Details (RFC 9457).

## Comandos

- Todo el entorno: `docker compose up --build` (API :8000, web :5173, mock SAP :8081, PostgreSQL :5432).
- Backend (`backend/`): `uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest` (integración necesita Docker).
- Frontend (`frontend/`): `pnpm format && pnpm lint && pnpm typecheck && pnpm test && pnpm build`.
- Tras cambiar la API: `cd backend && uv run sapextract export-openapi --output openapi.json`, luego `cd frontend && pnpm gen:api` (CI verifica que coincidan).
- Mock SAP (`tools/sap-mock/`): `uv run pytest`. Spike contra SAP real: `tools/spike-sap/README.md`.
- En Windows, si pytest no puede escribir en el TEMP del sistema, use `--basetemp=<carpeta temporal accesible>`.

## Calidad

- Backend: `uv`, `ruff`, `mypy --strict`, `pytest` (integración con testcontainers).
- Frontend: `pnpm`, `eslint`, `prettier`, `tsc`, `vitest`, `playwright`.
- Migraciones solo con Alembic.

## Objetos en SAP

Cualquier objeto Z (CDS, proyectos SEGW, programas) sigue el estándar de nomenclatura AG (skill `estandares-y-nomenclatura-ag`); ver plan 05 §5.4 para los casos que el estándar no cubre.
