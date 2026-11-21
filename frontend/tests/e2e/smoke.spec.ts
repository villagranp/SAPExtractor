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

    // Origen desde el explorador: el asistente llega con el servicio y el entity set elegidos.
    await page.getByRole('dialog').getByRole('button', { name: 'Crear origen' }).click();
    await expect(page.getByRole('heading', { name: 'Nuevo origen', level: 1 })).toBeVisible();
    await page.getByRole('button', { name: 'Leer esquema' }).click();
    await expect(page.getByRole('heading', { name: 'Esquema y claves' })).toBeVisible();
    await expect(page.getByRole('checkbox', { name: 'BELNR' })).toBeChecked();
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await expect(page.getByRole('radio', { name: 'Delta de la ODQ' })).toBeChecked();
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByRole('button', { name: 'Añadir condición' }).click();
    await page.getByLabel('Valor').fill('1000');
    await page.getByRole('button', { name: 'Siguiente' }).click();
    const sourceCode = `SRC_${code}`;
    await page.getByLabel('Código').fill(sourceCode);
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await page.getByRole('button', { name: 'Guardar borrador' }).click();

    await expect(page.getByRole('heading', { name: new RegExp(sourceCode) })).toBeVisible();
    await expect(page.getByText("RBUKRS eq '1000'")).toBeVisible();
    await page.getByRole('tab', { name: 'Vista previa' }).click();
    await page.getByRole('button', { name: 'Leer filas' }).click();
    const preview = page.getByRole('table', { name: new RegExp(sourceCode) });
    await expect(preview.getByRole('cell', { name: '1000' }).first()).toBeVisible();
    await page.getByRole('button', { name: 'Activar' }).click();
    await expect(page.getByText('Activo', { exact: true })).toBeVisible();
  });

  test('configuración de Entra ID → grupos → guía → auditoría', async ({ page }) => {
    await login(page, ADMIN_USER ?? '', ADMIN_PASSWORD ?? '');
    await menuLink(page, 'Microsoft Entra ID').click();
    await expect(page.getByRole('heading', { name: 'Microsoft Entra ID', level: 1 })).toBeVisible();
    await expect(page.getByLabel('URI de redirección')).toHaveValue(
      /\/api\/v1\/auth\/entra\/callback$/,
    );

    const secret = `E2E~${Date.now().toString(36)}.secreto-de-prueba`;
    await page
      .getByLabel('ID de inquilino (directorio)')
      .fill('11111111-2222-3333-4444-555555555555');
    await page
      .getByLabel('ID de aplicación (cliente)')
      .fill('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee');
    // El radio está oculto tras su tecla: se pulsa la etiqueta, como hace el usuario.
    await page.locator('label').filter({ hasText: 'Secreto de cliente' }).click();
    await expect(page.getByRole('radio', { name: 'Secreto de cliente' })).toBeChecked();
    await page.getByLabel('Valor del secreto').fill(secret);
    await page.getByRole('button', { name: 'Guardar', exact: true }).click();
    await expect(page.getByText('Configuración guardada.')).toBeVisible();
    // El secreto no vuelve al navegador.
    await expect(page.getByLabel('Valor del secreto')).toHaveValue('');
    await expect(page.getByText(secret)).toHaveCount(0);
    // Sin prueba correcta no se puede activar.
    await expect(page.getByRole('button', { name: 'Activar Entra ID' })).toBeDisabled();

    const group = `${Date.now().toString(16).slice(-8)}-1111-2222-3333-444444444444`;
    await page.getByRole('button', { name: 'Añadir grupo' }).click();
    await page.getByLabel('ID de objeto del grupo').last().fill(group);
    await page.getByLabel('Nombre del grupo').last().fill('SG-SAPExtract-Lectura');
    await page.getByLabel('Rol', { exact: true }).last().selectOption({ label: 'Visualizador' });
    await page.getByRole('button', { name: 'Guardar grupos' }).click();
    await expect(page.getByText('Grupos guardados.')).toBeVisible();

    await page.getByRole('link', { name: 'Guía del registro de aplicación' }).click();
    await expect(
      page.getByRole('heading', { name: 'Registro de aplicación en Entra ID', level: 1 }),
    ).toBeVisible();

    await menuLink(page, 'Auditoría').click();
    await expect(
      page.getByRole('table').getByText('idp.group_mappings_changed').first(),
    ).toBeVisible();
  });

  test('pipeline delta → ejecutar ahora → worker → inicialización confirmada', async ({ page }) => {
    test.setTimeout(120_000);
    await login(page, ADMIN_USER ?? '', ADMIN_PASSWORD ?? '');
    await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();
    // Origen y destino por la API (sus pantallas ya tienen su propio flujo arriba). Las llamadas
    // salen del propio navegador para usar su cookie de sesión y la cabecera CSRF del SPA.
    const cookies = await page.context().cookies();
    const csrf = decodeURIComponent(cookies.find((c) => c.name === 'sx_csrf')?.value ?? '');
    const post = async (path: string, data: unknown) => {
      const result = await page.evaluate(
        async ({ url, body, token }) => {
          const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
            body: JSON.stringify(body),
          });
          return { ok: response.ok, text: await response.text() };
        },
        { url: `/api/v1${path}`, body: data, token: csrf },
      );
      expect(result.ok, `${path}: ${result.text}`).toBeTruthy();
      return JSON.parse(result.text) as Record<string, unknown>;
    };
    const suffix = Date.now().toString(36).toUpperCase();
    const system = (await post('/sap-systems', {
      code: `E2E_ENG_${suffix}`,
      name: 'Gateway del motor',
      environment: 'DEV',
      system_type: 'S4HANA_ONPREM',
      base_url: SAP_URL,
      sap_client: '100',
      language: 'ES',
      credentials: { auth_type: 'basic', username: SAP_USER, password: SAP_PASSWORD },
      activate: true,
    })) as { system: { id: string } };
    const source = (await post('/sources', {
      sap_system_id: system.system.id,
      code: `SRC_ENG_${suffix}`,
      name: 'Mayor E2E',
      service_name: 'ZFI_WS_GL_ITEMS_SRV',
      entity_set: 'EntityOf0FI_GL_14',
      delta: { method: 'ODQ', semantics: 'additive' },
    })) as { id: string; code: string };
    await post(`/sources/${source.id}/activate`, {});
    const types = await page.evaluate(async () =>
      (await fetch('/api/v1/destination-types/csv/schema')).json(),
    );
    const root = (types as { config_schema: { properties: { root: { enum: string[] } } } })
      .config_schema.properties.root.enum[0];
    const destination = (await post('/destinations', {
      code: `CSV_ENG_${suffix}`,
      name: 'Carpeta del motor E2E',
      type: 'csv',
      environment: 'DEV',
      config: { root, path: `e2e/engine-${suffix.toLowerCase()}` },
      run_test: true,
      activate: true,
    })) as { destination: { code: string } };

    await menuLink(page, 'Pipelines').click();
    await page.getByRole('button', { name: 'Nuevo pipeline' }).click();
    const code = `PPL_${suffix}`;
    await page.getByLabel(/^Código/).fill(code);
    await page.getByLabel('Nombre').fill('Mayor hacia CSV');
    await page
      .getByLabel('Origen', { exact: true })
      .selectOption({ label: `${source.code} (Mayor E2E)` });
    await page
      .getByLabel('Destino', { exact: true })
      .selectOption({ label: `${destination.destination.code} (Carpeta del motor E2E)` });
    await expect(page.getByLabel(/^Modo de extracción/)).toHaveValue('DELTA');
    await expect(page.getByLabel(/^Modo de carga/)).toHaveValue('append');
    await page.getByLabel(/^Tabla o carpeta/).fill('GL_ITEMS');
    await page.getByRole('button', { name: 'Crear pipeline' }).click();

    await expect(page.getByRole('heading', { name: new RegExp(code) })).toBeVisible();
    await expect(page.getByText(/^Borrador: actívelo/)).toBeVisible();
    await page.getByRole('button', { name: 'Activar' }).click();
    await page.getByRole('button', { name: 'Ejecutar ahora' }).click();
    await expect(page.getByRole('heading', { name: /Inicialización del delta/ })).toBeVisible();
    // El worker del compose toma el run: el log llega en vivo hasta la confirmación.
    await expect(page.getByText('Delta inicializado: el token queda confirmado.')).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText('Correcta', { exact: true }).first()).toBeVisible();

    await page.getByRole('link', { name: 'Ir al pipeline' }).click();
    await page.getByRole('tab', { name: 'Delta' }).click();
    await expect(page.getByText('Inicializado', { exact: true }).first()).toBeVisible();
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
