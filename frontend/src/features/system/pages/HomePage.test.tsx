import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/setup';
import { renderWithProviders } from '@/test/render';
import { HomePage } from './HomePage';

describe('HomePage', () => {
  it('muestra la plataforma operativa y la versión', async () => {
    renderWithProviders(<HomePage />);
    expect(await screen.findByText('0.1.0')).toBeInTheDocument();
    expect(await screen.findAllByText('Operativa')).toHaveLength(3);
    expect(screen.getByText('abc1234')).toBeInTheDocument();
  });

  it('muestra el detalle cuando hay migraciones pendientes (503)', async () => {
    server.use(
      http.get('*/api/v1/health/ready', () =>
        HttpResponse.json(
          {
            status: 'fail',
            checks: {
              database: { status: 'ok', detail: null },
              migrations: { status: 'fail', detail: 'Migraciones pendientes' },
            },
          },
          { status: 503 },
        ),
      ),
    );
    renderWithProviders(<HomePage />);
    expect(await screen.findByText('Migraciones pendientes')).toBeInTheDocument();
    expect(screen.getByText('Con fallos')).toBeInTheDocument();
  });

  it('avisa cuando la API no responde', async () => {
    server.use(http.get('*/api/v1/health/ready', () => HttpResponse.error()));
    renderWithProviders(<HomePage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Sin conexión con la API');
  });
});
