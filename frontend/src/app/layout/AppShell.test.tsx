import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';
import { resetPreferencesCache } from '@/shared/theme/preferences';
import { adminMe } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { AppShell } from './AppShell';
import { locate, navigation } from './navigation';

afterEach(() => {
  window.localStorage.clear();
  resetPreferencesCache();
  document.documentElement.removeAttribute('data-theme');
});

function renderShell(route: string) {
  return renderWithProviders(<AppShell />, {
    route,
    routes: [
      { path: '/', element: <AppShell />, children: [{ path: '*', element: <p>Contenido</p> }] },
    ],
  });
}

describe('AppShell', () => {
  it('muestra el carril con las secciones permitidas y la ruta actual', async () => {
    renderShell('/sap-systems/abc');
    const menu = await screen.findByRole('complementary', { name: 'Menú principal' });
    expect(await within(menu).findByRole('link', { name: 'Sistemas SAP' })).toHaveClass('active');
    expect(within(menu).getByRole('link', { name: 'Usuarios' })).toBeInTheDocument();
    // Las iteraciones futuras se ven deshabilitadas, no como enlaces.
    expect(within(menu).getByRole('link', { name: 'Pipelines' })).toBeInTheDocument();
    expect(within(menu).queryByRole('link', { name: /Alertas/ })).not.toBeInTheDocument();
    const crumbs = screen.getByRole('navigation', { name: 'Ruta' });
    expect(crumbs).toHaveTextContent('SAPExtractDatosSistemas SAP'); // los separadores › son CSS
    expect(screen.getByRole('link', { name: 'Mi perfil (Administrador)' })).toBeInTheDocument();
  });

  it('oculta lo que el usuario no puede ver', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['sap_connection:read'] }),
      ),
    );
    renderShell('/');
    const menu = await screen.findByRole('complementary', { name: 'Menú principal' });
    expect(await within(menu).findByRole('link', { name: 'Sistemas SAP' })).toBeInTheDocument();
    expect(within(menu).queryByRole('link', { name: 'Usuarios' })).not.toBeInTheDocument();
  });

  it('cambia entre el tema claro y el grafito', async () => {
    const user = userEvent.setup();
    renderShell('/');
    await user.click(await screen.findByRole('button', { name: 'Cambiar al tema grafito' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    await user.click(screen.getByRole('button', { name: 'Cambiar al tema claro' }));
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('locate', () => {
  it('elige el elemento de menú con el prefijo más largo', () => {
    expect(locate('/')?.item.to).toBe('/');
    expect(locate('/admin/users/42')?.item.to).toBe('/admin/users');
    expect(locate('/admin/userss')).toBeNull();
    expect(locate('/sap-systems/new', navigation)?.section.titleKey).toBe('nav.sectionData');
  });
});
