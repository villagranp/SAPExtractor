import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

/*
 * Accesibilidad (WCAG 2.1 AA, plan 10 §5) de las pantallas principales con axe.
 * Se exige cero incumplimientos graves o críticos; los menores se revisan en It1b,
 * al integrar la plantilla corporativa.
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;
const BLOCKING = new Set(['serious', 'critical']);

async function expectAccessible(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const blocking = results.violations
    .filter((v) => BLOCKING.has(v.impact ?? ''))
    .map(
      (v) => `${v.id} (${v.impact ?? '?'}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
    );
  expect(blocking, `Accesibilidad de ${label}`).toEqual([]);
}

test('login accesible', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
  await expectAccessible(page, 'login');
});

test.describe('pantallas con sesión', () => {
  test.skip(!ADMIN_USER || !ADMIN_PASSWORD, 'Defina E2E_ADMIN_USER y E2E_ADMIN_PASSWORD');

  test('pantallas principales en tema claro y grafito', async ({ page }) => {
    test.setTimeout(300_000); // 16 pantallas × 2 temas con axe
    await page.goto('/login');
    await page.getByLabel('Usuario').fill(ADMIN_USER ?? '');
    await page.getByLabel('Contraseña', { exact: true }).fill(ADMIN_PASSWORD ?? '');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();

    const screens: [string, string][] = [
      ['/sap-systems', 'Sistemas SAP'],
      ['/sap-systems/new', 'Nuevo sistema SAP'],
      ['/sources', 'Orígenes'],
      ['/sources/new', 'Nuevo origen'],
      ['/sources/guide', 'Cómo publicar un origen en SAP'],
      ['/destinations', 'Destinos'],
      ['/destinations/new', 'Nuevo destino'],
      ['/pipelines', 'Pipelines'],
      ['/pipelines/new', 'Nuevo pipeline'],
      ['/runs', 'Ejecuciones'],
      ['/dashboard', 'Panel'],
      ['/alerts', 'Alertas'],
      ['/admin/users', 'Usuarios'],
      ['/admin/audit', 'Auditoría'],
      ['/admin/identity-provider', 'Microsoft Entra ID'],
      ['/admin/identity-provider/guide', 'Registro de aplicación en Entra ID'],
      ['/profile', 'Mi perfil'],
    ];
    // Los dos temas: el contraste se comprueba en claro y en grafito.
    for (const theme of ['light', 'dark'] as const) {
      const prefs = JSON.stringify({ theme });
      await page.evaluate(
        `window.localStorage.setItem('sx-preferences', ${JSON.stringify(prefs)})`,
      );
      for (const [path, heading] of [['/', 'Estado de la plataforma'], ...screens]) {
        await page.goto(path);
        await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
        await expectAccessible(page, `${path} (${theme})`);
      }
    }
  });
});
