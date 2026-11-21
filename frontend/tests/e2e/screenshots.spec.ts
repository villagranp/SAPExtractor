import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

/*
 * Capturas de las pantallas principales en tema claro, grafito y densidad compacta, para
 * revisar el diseño (no comparan píxeles). Solo se ejecuta con E2E_SCREENSHOTS=<carpeta>:
 *   E2E_SCREENSHOTS=../tmp/capturas pnpm test:e2e screenshots
 */
const OUT = process.env.E2E_SCREENSHOTS;
const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;

const SCREENS: [string, string][] = [
  ['home', '/'],
  ['sap-systems', '/sap-systems'],
  ['sap-wizard', '/sap-systems/new'],
  ['sources', '/sources'],
  ['source-wizard', '/sources/new'],
  ['source-guide', '/sources/guide'],
  ['destinations', '/destinations'],
  ['destination-wizard', '/destinations/new?type=snowflake'],
  ['pipelines', '/pipelines'],
  ['pipeline-new', '/pipelines/new'],
  ['runs', '/runs'],
  ['dashboard', '/dashboard'],
  ['alerts', '/alerts'],
  ['users', '/admin/users'],
  ['audit', '/admin/audit'],
  ['identity-provider', '/admin/identity-provider'],
  ['identity-provider-guide', '/admin/identity-provider/guide'],
  ['profile', '/profile'],
  ['ui-kit', '/ui-kit'],
];

async function prefer(page: Page, theme: string, density: string) {
  const prefs = JSON.stringify({ theme, density });
  await page.evaluate(`window.localStorage.setItem('sx-preferences', ${JSON.stringify(prefs)})`);
}

test.describe('capturas', () => {
  test.skip(!OUT || !ADMIN_USER || !ADMIN_PASSWORD, 'Defina E2E_SCREENSHOTS y el administrador');
  test.use({ viewport: { width: 1440, height: 900 } });

  test('pantallas principales', async ({ page }) => {
    test.setTimeout(180_000);
    mkdirSync(OUT ?? '.', { recursive: true });
    await page.goto('/login');
    await page.screenshot({ path: `${OUT ?? '.'}/login-light.png` });
    await page.getByLabel('Usuario').fill(ADMIN_USER ?? '');
    await page.getByLabel('Contraseña', { exact: true }).fill(ADMIN_PASSWORD ?? '');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();

    for (const [theme, density] of [
      ['light', 'comfortable'],
      ['dark', 'comfortable'],
      ['light', 'compact'],
    ] as const) {
      await prefer(page, theme, density);
      for (const [name, path] of SCREENS) {
        if (density === 'compact' && !['sap-systems', 'audit'].includes(name)) continue;
        await page.goto(path);
        await page.waitForLoadState('networkidle');
        await expect(page.getByText('Comprobando…')).toHaveCount(0);
        await page.screenshot({
          path: `${OUT ?? '.'}/${name}-${theme}-${density}.png`,
          fullPage: true,
        });
      }
    }
  });
});
