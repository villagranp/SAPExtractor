import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { deltaRun } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { RunDetailPage } from './RunDetailPage';
import { RunsPage } from './RunsPage';

const route = {
  route: `/runs/${deltaRun.id}`,
  routes: [{ path: '/runs/:runId', element: <RunDetailPage /> }],
};

describe('RunsPage', () => {
  it('lista las ejecuciones con pipeline, modo, estado y filas', async () => {
    renderWithProviders(<RunsPage />);
    const row = (await screen.findByText('FI_GL_CSV')).closest('tr') as HTMLElement;
    const cells = within(row);
    expect(cells.getByText('Delta')).toBeInTheDocument();
    expect(cells.getByText('Correcta')).toBeInTheDocument();
    expect(cells.getByText('10')).toBeInTheDocument();
    expect(cells.getByText('1 de 3')).toBeInTheDocument();
  });
});

describe('RunDetailPage', () => {
  it('muestra resumen, tokens, log, pasos e intentos', async () => {
    renderWithProviders(<RunDetailPage />, route);
    expect(await screen.findByText('Token de delta confirmado.')).toBeInTheDocument();
    expect(screen.getByText('D000000000250')).toBeInTheDocument();
    expect(screen.getByText('D000000000260')).toBeInTheDocument();
    expect(screen.getByText(deltaRun.batch_id ?? '')).toBeInTheDocument();
    const steps = screen.getByRole('table', { name: 'Pasos' });
    expect(within(steps).getAllByRole('row')).toHaveLength(6);
    expect(within(steps).getByText('Confirmar')).toBeInTheDocument();
    const attempts = screen.getByRole('table', { name: 'Intentos' });
    expect(within(attempts).getByText('Correcto')).toBeInTheDocument();
    // Terminado: sin cancelar y sin aviso de actualización en vivo.
    expect(screen.queryByRole('button', { name: 'Cancelar ejecución' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Se actualiza cada segundo/)).not.toBeInTheDocument();
  });

  it('un run fallido explica el error', async () => {
    server.use(
      http.get('*/api/v1/runs/:id', () =>
        HttpResponse.json({
          ...deltaRun,
          status: 'failed',
          error_code: 'SX-SAP-DELTA-INVALID',
          error_message: 'SAP rechazó el token de delta.',
        }),
      ),
    );
    renderWithProviders(<RunDetailPage />, route);
    expect(await screen.findByText('SAP rechazó el token de delta.')).toBeInTheDocument();
    expect(screen.getByText('SX-SAP-DELTA-INVALID')).toBeInTheDocument();
  });

  it('cancela un run en curso', async () => {
    let cancelled = false;
    server.use(
      http.get('*/api/v1/runs/:id', () =>
        HttpResponse.json({ ...deltaRun, status: 'running', finished_at: null }),
      ),
      http.get('*/api/v1/runs/:id/logs', () =>
        HttpResponse.json({ items: [], next_after: null, finished: false }),
      ),
      http.post('*/api/v1/runs/:id/cancel', () => {
        cancelled = true;
        return HttpResponse.json({ ...deltaRun, status: 'running', cancel_requested: true });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<RunDetailPage />, route);
    expect(await screen.findByText(/Se actualiza cada segundo/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancelar ejecución' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancelar ejecución' }));
    await screen.findByRole('button', { name: 'Cancelar ejecución' });
    expect(cancelled).toBe(true);
  });
});
