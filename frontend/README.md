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

Las E2E del flujo de administración necesitan un administrador ya activo (que haya hecho el cambio de contraseña inicial): `E2E_ADMIN_USER=admin E2E_ADMIN_PASSWORD=... pnpm test:e2e`. Sin esas variables solo se ejecutan las pruebas públicas.

Tras cambiar la API: `cd ../backend && uv run sapextract export-openapi --output openapi.json`, luego `pnpm gen:api`. CI falla si el contrato versionado no coincide.

## Reglas

- Las pantallas (`src/features/*`) solo usan componentes de `@/shared/ui`. La plantilla corporativa se integrará reimplementando `shared/ui` y `shared/theme/tokens.css`, sin tocar las pantallas.
- Llamadas a la API solo con el cliente tipado `api` + `unwrap` (`@/shared/api/client`) dentro de hooks de TanStack Query.
- Textos visibles en `src/shared/i18n/locales/*.json`. El español (`es.json`) es el idioma de referencia y debe estar completo; `en.json` cubre la navegación, el login y los textos comunes, y lo que falte se muestra en español (`fallbackLng: 'es'`).
- Sesión: `useMe()` es la fuente de verdad. `RequireAuth` envía al login si no hay sesión activa; `RequirePermission` / `useCan()` solo ocultan opciones (la API responde 403 igualmente). Un 401 en cualquier consulta devuelve al login.
- Las mutaciones envían la cabecera `X-CSRF-Token` automáticamente (cookie `sx_csrf`).
- Secretos que solo se muestran una vez (contraseñas temporales, tokens API): componente `OneTimeSecret`.
- `/ui-kit` (solo en desarrollo) muestra todos los componentes base.
