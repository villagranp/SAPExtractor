import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { DestinationsPage } from './DestinationsPage';

describe('DestinationsPage', () => {
  it('lista destinos con tipo, ubicación, estado y última prueba', async () => {
    renderWithProviders(<DestinationsPage />);
    const row = (await screen.findByText('SNOW_FIN_QAS')).closest('tr');
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText('Snowflake')).toBeInTheDocument();
    expect(cells.getByText('ab12345.east-us-2.azure · RAW.SAP')).toBeInTheDocument();
    expect(cells.getByText('Activo')).toBeInTheDocument();
    expect(cells.getByText('Correcta')).toBeInTheDocument();
    const csv = within(screen.getByText('CSV_INTERCAMBIO').closest('tr') as HTMLElement);
    expect(csv.getByText('Borrador')).toBeInTheDocument();
    expect(csv.getByText('Sin probar')).toBeInTheDocument();
  });

  it('muestra una tarjeta por tipo con sus modos de carga', async () => {
    renderWithProviders(<DestinationsPage />);
    expect(
      await screen.findByRole('button', { name: 'Nuevo destino Snowflake' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nuevo destino Archivos CSV' })).toBeInTheDocument();
    expect(screen.getByText(/Añadir · Reemplazar · Upsert/)).toBeInTheDocument();
  });

  it('sin destinos invita a registrar el primero', async () => {
    server.use(
      http.get('*/api/v1/destinations', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 }),
      ),
    );
    renderWithProviders(<DestinationsPage />);
    expect(await screen.findByText('Aún no hay destinos')).toBeInTheDocument();
  });

  it('un lector no ve las acciones de alta', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['destination:read'] }),
      ),
    );
    renderWithProviders(<DestinationsPage />);
    await screen.findByText('SNOW_FIN_QAS');
    expect(screen.queryByRole('button', { name: 'Nuevo destino' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Nuevo destino Snowflake' }),
    ).not.toBeInTheDocument();
  });
});
