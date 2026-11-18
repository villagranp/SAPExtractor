import { expect, test } from '@playwright/test';

test('la página de inicio muestra el estado de la plataforma', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Estado de la plataforma' })).toBeVisible();
  await expect(page.getByText('Operativa').first()).toBeVisible();
});

test('rutas desconocidas muestran 404 amigable', async ({ page }) => {
  await page.goto('/no-existe');
  await expect(page.getByText('Página no encontrada')).toBeVisible();
});
