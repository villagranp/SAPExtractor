import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { components } from '@/shared/api/schema';
import { adminMe, problem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { SettingsPage } from './SettingsPage';

const nightGroup: components['schemas']['ConcurrencyGroupResponse'] = {
  code: 'PRD_NOCHE',
  description: 'Cargas nocturnas contra producción',
  max_parallel: 2,
  pipelines: 3,
  running: 1,
  updated_at: '2026-11-21T10:00:00Z',
  updated_by: 'root',
};

const withGroups = (groups: unknown[]) =>
  http.get('*/api/v1/concurrency-groups', () => HttpResponse.json(groups));

describe('SettingsPage', () => {
  it('lista los grupos con su límite, sus pipelines y lo que tienen en curso', async () => {
    server.use(withGroups([nightGroup]));
    renderWithProviders(<SettingsPage />);
    const row = (await screen.findByText('PRD_NOCHE')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Cargas nocturnas contra producción')).toBeInTheDocument();
    expect(within(row).getByText('2 a la vez')).toBeInTheDocument();
    expect(within(row).getByText('3')).toBeInTheDocument();
    expect(within(row).getByText('1')).toBeInTheDocument();
  });

  it('crea un grupo con el código en mayúsculas', async () => {
    let path = '';
    let body: Record<string, unknown> = {};
    server.use(
      withGroups([]),
      http.put('*/api/v1/concurrency-groups/:code', async ({ request, params }) => {
        path = String(params.code);
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...nightGroup, code: path });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText(/Aún no hay grupos/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Nuevo grupo' }));
    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Guardar grupo' });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Código'), 'qas_dia');
    await user.clear(within(dialog).getByLabelText('Ejecuciones a la vez'));
    await user.type(within(dialog).getByLabelText('Ejecuciones a la vez'), '4');
    await user.click(save);
    await screen.findByRole('button', { name: 'Nuevo grupo' });
    expect(path).toBe('QAS_DIA');
    expect(body).toEqual({ max_parallel: 4, description: null });
  });

  it('explica por qué no se puede eliminar un grupo con pipelines', async () => {
    server.use(
      withGroups([nightGroup]),
      http.delete('*/api/v1/concurrency-groups/:code', () =>
        problem(409, 'SX-PIPE-GROUP-IN-USE', 'El grupo tiene pipelines: quítelos antes.'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SettingsPage />);
    await user.click(await screen.findByRole('button', { name: 'Eliminar el grupo PRD_NOCHE' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Eliminar grupo' }));
    expect(
      await within(dialog).findByText('El grupo tiene pipelines: quítelos antes.'),
    ).toBeInTheDocument();
  });

  it('quien solo consulta no ve las acciones', async () => {
    server.use(
      withGroups([nightGroup]),
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['pipeline:read'] }),
      ),
    );
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText('PRD_NOCHE')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nuevo grupo' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Eliminar el grupo/ })).not.toBeInTheDocument();
  });
});
