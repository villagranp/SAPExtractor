import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe, problem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { RequireAuth, RequirePermission } from './guards';
import { visibleNavigation } from './layout/navigation';

const loginRoute = [{ path: '/login', element: <p>Pantalla de login</p> }];

describe('RequireAuth', () => {
  it('sin sesión redirige al login', async () => {
    server.use(http.get('*/api/v1/me', () => problem(401, 'SX-AUTH-UNAUTHENTICATED', 'x')));
    const { router } = renderWithProviders(
      <RequireAuth>
        <p>Privado</p>
      </RequireAuth>,
      { route: '/admin/audit', routes: loginRoute },
    );
    expect(await screen.findByText('Pantalla de login')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?next=%2Fadmin%2Faudit');
  });

  it('con el login a medias también redirige', async () => {
    server.use(
      http.get('*/api/v1/me', () => HttpResponse.json({ ...adminMe, stage: 'password_change' })),
    );
    renderWithProviders(
      <RequireAuth>
        <p>Privado</p>
      </RequireAuth>,
      { route: '/', routes: loginRoute },
    );
    expect(await screen.findByText('Pantalla de login')).toBeInTheDocument();
  });

  it('con sesión activa muestra el contenido', async () => {
    renderWithProviders(
      <RequireAuth>
        <p>Privado</p>
      </RequireAuth>,
    );
    expect(await screen.findByText('Privado')).toBeInTheDocument();
  });
});

describe('RequirePermission', () => {
  it('sin el permiso muestra «Sin acceso»', async () => {
    server.use(http.get('*/api/v1/me', () => HttpResponse.json({ ...adminMe, permissions: [] })));
    renderWithProviders(
      <RequirePermission permission="audit:read">
        <p>Auditoría</p>
      </RequirePermission>,
    );
    expect(await screen.findByText('Sin acceso')).toBeInTheDocument();
    expect(screen.queryByText('Auditoría')).not.toBeInTheDocument();
  });
});

describe('visibleNavigation', () => {
  it('oculta opciones sin permiso y secciones vacías no', () => {
    const labels = (perms: string[]) =>
      visibleNavigation(perms).flatMap((s) => s.items.map((i) => i.labelKey));
    expect(labels([])).not.toContain('nav.users');
    expect(labels([])).not.toContain('nav.alerts');
    expect(labels(['dashboard:read'])).toEqual(
      expect.arrayContaining(['nav.dashboard', 'nav.alerts']),
    );
    expect(labels([])).not.toContain('nav.identityProvider');
    expect(labels(['idp:manage'])).toContain('nav.identityProvider');
    expect(labels(['user:read', 'audit:read'])).toEqual(
      expect.arrayContaining(['nav.users', 'nav.audit']),
    );
    expect(labels(['user:read'])).not.toContain('nav.apiTokens');
  });
});
