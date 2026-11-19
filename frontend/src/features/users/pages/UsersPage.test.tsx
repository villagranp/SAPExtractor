import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe, roles, sampleUser } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { UsersPage } from './UsersPage';

describe('UsersPage', () => {
  it('lista usuarios con estado, roles y origen', async () => {
    renderWithProviders(<UsersPage />);
    const row = (await screen.findByText('Usuario de prueba')).closest('tr');
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText('Visualizador')).toBeInTheDocument();
    expect(cells.getByText('Activo')).toBeInTheDocument();
    expect(cells.getByText('MFA')).toBeInTheDocument();
    expect(screen.getByText('1–1 de 1')).toBeInTheDocument();
  });

  it('filtra por estado enviando el parámetro a la API', async () => {
    const seen: string[] = [];
    server.use(
      http.get('*/api/v1/users', ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('status') ?? '');
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<UsersPage />);
    await user.selectOptions(await screen.findByLabelText('Estado'), 'disabled');
    expect(await screen.findByText('No hay usuarios con estos filtros.')).toBeInTheDocument();
    expect(seen).toContain('disabled');
  });

  it('el alta muestra la contraseña temporal una sola vez', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/users', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            user: {
              ...sampleUser,
              username: 'nuevo',
              must_change_password: true,
              failed_attempts: 0,
              locked_until: null,
              anonymized_at: null,
              created_by: 'root',
              updated_at: sampleUser.created_at,
              updated_by: 'root',
              version: 1,
            },
            temporary_password: 'Tmp-Secreta.123',
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<UsersPage />);
    await user.click(await screen.findByRole('button', { name: 'Nuevo usuario' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Usuario'), 'nuevo');
    await user.type(within(dialog).getByLabelText('Nombre visible'), 'Nuevo usuario');
    await user.click(within(dialog).getByLabelText(/^Visualizador/));
    await user.click(within(dialog).getByRole('button', { name: 'Crear usuario' }));

    expect(await screen.findByTestId('one-time-secret')).toHaveTextContent('Tmp-Secreta.123');
    expect(body).toMatchObject({
      username: 'nuevo',
      auth_source: 'local',
      role_ids: [roles[1]?.id],
    });
  });

  it('sin permiso de alta no ofrece el botón', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['user:read', 'role:read'] }),
      ),
    );
    renderWithProviders(<UsersPage />);
    await screen.findByText('Usuario de prueba');
    expect(screen.queryByRole('button', { name: 'Nuevo usuario' })).not.toBeInTheDocument();
  });
});
