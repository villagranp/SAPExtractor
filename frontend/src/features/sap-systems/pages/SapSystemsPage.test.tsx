import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { SapSystemsPage } from './SapSystemsPage';

describe('SapSystemsPage', () => {
  it('lista sistemas con entorno, estado y semáforo de la última prueba', async () => {
    renderWithProviders(<SapSystemsPage />);
    const row = (await screen.findByText('S4D_100')).closest('tr');
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText('DEV')).toBeInTheDocument();
    expect(cells.getByText('Activo')).toBeInTheDocument();
    expect(cells.getByText('Correcta')).toBeInTheDocument();
    expect(cells.getByText('https://gw.empresa.local:44300')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nuevo sistema SAP' })).toBeInTheDocument();
  });

  it('sin sistemas invita a registrar el primero', async () => {
    server.use(
      http.get('*/api/v1/sap-systems', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 }),
      ),
    );
    renderWithProviders(<SapSystemsPage />);
    expect(await screen.findByText('Aún no hay sistemas SAP')).toBeInTheDocument();
  });

  it('un lector no ve el botón de alta', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['sap_connection:read'] }),
      ),
    );
    renderWithProviders(<SapSystemsPage />);
    await screen.findByText('S4D_100');
    expect(screen.queryByRole('button', { name: 'Nuevo sistema SAP' })).not.toBeInTheDocument();
  });
});
