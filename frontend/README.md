# SAPExtract · Frontend

SPA en React + TypeScript (Vite). Diseño y reglas en [plan 10](../docs/plan/10-frontend-y-ux.md).

## Comandos

```bash
pnpm install
pnpm dev            # http://localhost:5173 (proxy /api → http://localhost:8000)
pnpm test           # Vitest + Testing Library + MSW
pnpm lint && pnpm typecheck && pnpm format:check
pnpm build
pnpm gen:api        # regenera src/shared/api/schema.d.ts desde ../backend/openapi.json
pnpm exec playwright install chromium && pnpm test:e2e   # E2E contra el entorno levantado
```

Tras cambiar la API: `cd ../backend && uv run sapextract export-openapi --output openapi.json`, luego `pnpm gen:api`. CI falla si el contrato versionado no coincide.

## Reglas

- Las pantallas (`src/features/*`) solo usan componentes de `@/shared/ui`. La plantilla corporativa se integrará reimplementando `shared/ui` y `shared/theme/tokens.css`, sin tocar las pantallas.
- Llamadas a la API solo con el cliente tipado `api` + `unwrap` (`@/shared/api/client`) dentro de hooks de TanStack Query.
- Textos visibles en `src/shared/i18n/locales/*.json` (español por defecto).
- `/ui-kit` (solo en desarrollo) muestra todos los componentes base.
