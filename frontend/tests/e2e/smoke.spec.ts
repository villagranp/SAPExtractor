import { expect, test, type Page } from '@playwright/test';

/*
 * E2E contra el entorno levantado (docker compose). Necesitan un administrador ACTIVO
 * (ya hizo el cambio de contraseña inicial):
 *   E2E_ADMIN_USER=root E2E_ADMIN_PASSWORD=... pnpm test:e2e
 * Sin esas variables solo se ejecutan las pruebas públicas.
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;

async function login(page: Page, username: string, password: string) {
  await page.goto('/login');
  await page.getByLabel('Usuario').fill(username);
  await page.getByLabel('Contraseña', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Entrar' }).click();
}

test('sin sesión, la aplicación lleva al login', async ({ page }) => {
  await page.goto('/admin/users');
  await expect(page).toHaveURL(/\/login\?next=%2Fadmin%2Fusers/);
  await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible();
});

test('credenciales incorrectas muestran un error genérico', async ({ page }) => {
  await login(page, 'no-existe', 'Cualquiera-12345');
  await expect(page.getByRole('alert')).toContainText('Usuario o contraseña incorrectos');
});

test.describe('con administrador', () => {
  test.skip(!ADMIN_USER || !ADMIN_PASSWORD, 'Defina E2E_ADMIN_USER y E2E_ADMIN_PASSWORD');

  test('alta de usuario → rol → primer acceso → acceso restringido', async ({ page, browser }) => {
    await login(page, ADMIN_USER ?? '', ADMIN_PASSWORD ?? '');
    await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();

    await page.getByRole('link', { name: 'Usuarios' }).click();
    await page.getByRole('button', { name: 'Nuevo usuario' }).click();
    const username = `e2e${Date.now().toString(36)}`;
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Usuario').fill(username);
    await dialog.getByLabel('Nombre visible').fill('Usuario E2E');
    await dialog.getByLabel(/^Visualizador/).check();
    await dialog.getByRole('button', { name: 'Crear usuario' }).click();
    const temporary = (await page.getByTestId('one-time-secret').textContent()) ?? '';
    expect(temporary.length).toBeGreaterThan(11);

    // Primer acceso del nuevo usuario en otro contexto (otra cookie de sesión).
    const other = await (await browser.newContext()).newPage();
    await login(other, username, temporary);
    await expect(other.getByText('Cambie su contraseña')).toBeVisible();
    const password = 'E2e.Clave-Segura-2026';
    await other.getByLabel('Nueva contraseña', { exact: true }).fill(password);
    await other.getByLabel('Repita la nueva contraseña').fill(password);
    await other.getByRole('button', { name: 'Cambiar contraseña' }).click();
    await expect(other.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();
    // El visualizador no ve la administración y la URL directa no da acceso.
    await expect(other.getByRole('link', { name: 'Usuarios' })).toHaveCount(0);
    await other.goto('/admin/audit');
    await expect(other.getByText('Sin acceso')).toBeVisible();

    // La acción quedó en la auditoría (antes se cierra el diálogo modal del alta).
    await dialog.getByRole('button', { name: 'Cerrar' }).last().click();
    await page.getByRole('link', { name: 'Auditoría' }).click();
    await expect(page.getByRole('table').getByText('user.created').first()).toBeVisible();
  });
});
