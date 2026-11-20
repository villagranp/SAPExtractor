import { expect, test, type Page } from '@playwright/test';

/*
 * E2E contra el entorno levantado (docker compose). Necesitan un administrador ACTIVO
 * (ya hizo el cambio de contraseña inicial):
 *   E2E_ADMIN_USER=root E2E_ADMIN_PASSWORD=... pnpm test:e2e
 * Sin esas variables solo se ejecutan las pruebas públicas.
 * El flujo SAP usa el mock del compose (desde la API: http://sap-mock:8081, sxmock/sxmock);
 * se puede apuntar a otro con E2E_SAP_URL, E2E_SAP_USER y E2E_SAP_PASSWORD.
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;
const SAP_URL = process.env.E2E_SAP_URL ?? 'http://sap-mock:8081';
const SAP_USER = process.env.E2E_SAP_USER ?? 'sxmock';
const SAP_PASSWORD = process.env.E2E_SAP_PASSWORD ?? 'sxmock';

/** Enlace del menú principal (el inicio también tiene enlaces como «Ver usuarios»). */
function menuLink(page: Page, name: string) {
  return page.getByRole('complementary', { name: 'Menú principal' }).getByRole('link', { name });
}

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

    await menuLink(page, 'Usuarios').click();
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
    await expect(menuLink(other, 'Usuarios')).toHaveCount(0);
    await other.goto('/admin/audit');
    await expect(other.getByText('Sin acceso')).toBeVisible();

    // La acción quedó en la auditoría (antes se cierra el diálogo modal del alta).
    await dialog.getByRole('button', { name: 'Cerrar' }).last().click();
    await menuLink(page, 'Auditoría').click();
    await expect(page.getByRole('table').getByText('user.created').first()).toBeVisible();
  });

  test('alta de sistema SAP → prueba por pasos → explorador ODP/CDS', async ({ page }) => {
    await login(page, ADMIN_USER ?? '', ADMIN_PASSWORD ?? '');
    await menuLink(page, 'Sistemas SAP').click();
    await page.getByRole('button', { name: 'Nuevo sistema SAP' }).click();

    const code = `E2E_${Date.now().toString(36).toUpperCase()}`;
    await page.getByLabel('Código').fill(code);
    await page.getByLabel('Nombre').fill('Gateway E2E');
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByLabel('URL de Gateway').fill(SAP_URL);
    await page.getByLabel('Mandante').fill('100');
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByLabel('Usuario técnico').fill(SAP_USER);
    await page.getByLabel('Contraseña').fill(SAP_PASSWORD);
    await page.getByRole('button', { name: 'Siguiente' }).click();

    await page.getByRole('button', { name: 'Probar conexión' }).click();
    await expect(page.getByText('Conexión correcta', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByRole('button', { name: 'Guardar' }).click();

    await expect(page.getByRole('heading', { name: new RegExp(code) })).toBeVisible();
    await expect(page.getByText('Activo', { exact: true })).toBeVisible();
    // La contraseña no vuelve al navegador.
    await expect(page.getByText(SAP_PASSWORD, { exact: true })).toHaveCount(0);

    await page.getByRole('tab', { name: 'Servicios' }).click();
    await expect(page.getByTestId('catalog-summary')).toContainText('3 ODP · 1 CDS');
    await page.getByText('ZFI_WS_GL_ITEMS_SRV').click();
    await expect(page.getByRole('dialog').getByText('EntityOf0FI_GL_14').first()).toBeVisible();
  });
  test('alta de destino CSV → prueba → activación → auditoría', async ({ page }) => {
    await login(page, ADMIN_USER ?? '', ADMIN_PASSWORD ?? '');
    await menuLink(page, 'Destinos').click();
    await page.getByRole('button', { name: 'Nuevo destino Archivos CSV' }).click();

    const code = `E2E_CSV_${Date.now().toString(36).toUpperCase()}`;
    await page.getByLabel('Código').fill(code);
    await page.getByLabel('Nombre').fill('Carpeta E2E');
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByLabel('Carpeta raíz').selectOption({ index: 1 });
    await page.getByLabel(/^Subcarpeta/).fill(`e2e/${code.toLowerCase()}`);
    await page.getByRole('button', { name: 'Siguiente' }).click();

    await page.getByRole('button', { name: 'Probar destino' }).click();
    await expect(page.getByText('Destino accesible', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByRole('button', { name: 'Guardar' }).click();

    await expect(page.getByRole('heading', { name: new RegExp(code) })).toBeVisible();
    await expect(page.getByText('Activo', { exact: true })).toBeVisible();
    await expect(page.getByText('Escritura y espacio libre')).toBeVisible();

    await page.getByRole('tab', { name: 'Actividad' }).click();
    await expect(page.getByText('destination.created').first()).toBeVisible();
  });
});
