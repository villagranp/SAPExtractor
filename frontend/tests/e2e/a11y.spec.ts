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

  test('inicio, sistemas SAP, asistente, usuarios y auditoría', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Usuario').fill(ADMIN_USER ?? '');
    await page.getByLabel('Contraseña', { exact: true }).fill(ADMIN_PASSWORD ?? '');
    await page.getByRole('button', { name: 'Entrar' }).click();
    await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();
    await expectAccessible(page, 'inicio');

    const screens: [string, string][] = [
      ['/sap-systems', 'Sistemas SAP'],
      ['/sap-systems/new', 'Nuevo sistema SAP'],
      ['/admin/users', 'Usuarios'],
      ['/admin/audit', 'Auditoría'],
    ];
    for (const [path, heading] of screens) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
      await expectAccessible(page, path);
    }
  });
});
