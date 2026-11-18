import { defineConfig, devices } from '@playwright/test';

/**
 * E2E contra el entorno levantado con docker compose (o `pnpm dev` + backend local).
 * Instalar navegadores una vez: `pnpm exec playwright install chromium`.
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
