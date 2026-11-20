import { defineConfig, devices } from '@playwright/test';

/**
 * E2E contra el entorno levantado con docker compose (o `pnpm dev` + backend local).
 * Instalar navegadores una vez: `pnpm exec playwright install chromium`.
 * `global-setup.ts` activa el administrador E2E si recibe su contraseña temporal (CI).
 */
export default defineConfig({
  testDir: './tests/e2e',
  globalSetup: './tests/e2e/global-setup.ts',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
