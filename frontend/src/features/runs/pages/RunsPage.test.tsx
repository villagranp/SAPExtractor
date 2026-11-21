import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  failedRun,
  idleWorker,
  pipelineHandlers,
  queuedRun,
} from '@/features/pipelines/test/fixtures';
import { deltaRun } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { formatDuration } from '../format';
import { RunDetailPage } from './RunDetailPage';
import { RunsPage } from './RunsPage';

function renderRun(id: string) {
  return renderWithProviders(<RunDetailPage />, {
    route: `/runs/${id}`,
    routes: [{ path: '/runs/:runId', element: <RunDetailPage /> }],
  });
}

describe('formatDuration', () => {
  it('usa la unidad que toca y admite ejecuciones sin terminar', () => {
    expect(formatDuration(null, null)).toBe('—');
    expect(formatDuration('2026-11-21T09:00:00Z', '2026-11-21T09:00:45Z')).toBe('45 s');
    expect(formatDuration('2026-11-21T09:00:00Z', '2026-11-21T09:03:05Z')).toBe('3 min 05 s');
    expect(formatDuration('2026-11-21T09:00:00Z', '2026-11-21T11:10:00Z')).toBe('2 h 10 min');
    expect(formatDuration('2026-11-21T09:00:00Z', null, Date.parse('2026-11-21T09:00:20Z'))).toBe(
      '20 s',
    );
  });
});

describe('RunsPage', () => {
  it('lista las ejecuciones y avisa cuando ningún worker puede tomarlas', async () => {
    server.use(...pipelineHandlers);
    renderWithProviders(<RunsPage />);
    expect(await screen.findByText('En cola', { selector: '.sx-pill' })).toBeInTheDocument();
    const failed = screen
      .getByText('Fallida', { selector: '.sx-pill' })
      .closest('tr') as HTMLElement;
    expect(within(failed).getByText('GL_A_SNOWFLAKE')).toBeInTheDocument();
    expect(within(failed).getByText('A mano, por root')).toBeInTheDocument();
    expect(within(failed).getByText('SX-SAP-503')).toBeInTheDocument();
    expect(within(failed).getByText('7 min 37 s')).toBeInTheDocument();
    expect(within(failed).getAllByRole('link')[0]).toHaveAttribute('href', `/runs/${failedRun.id}`);
    expect(within(failed).getByRole('link', { name: 'GL_A_SNOWFLAKE' })).toHaveAttribute(
      'href',
      `/pipelines/${failedRun.pipeline.id}`,
    );
    expect(
      await screen.findByText(/no tienen instalado el motor de extracción/),
    ).toBeInTheDocument();
  });

  it('no avisa si hay un worker con motor', async () => {
    server.use(
      http.get('*/api/v1/workers', () =>
        HttpResponse.json([{ ...idleWorker, has_executor: true }]),
      ),
      ...pipelineHandlers,
    );
    renderWithProviders(<RunsPage />);
    expect(await screen.findByText('En cola', { selector: '.sx-pill' })).toBeInTheDocument();
    expect(screen.queryByText('Ningún worker puede tomar ejecuciones')).not.toBeInTheDocument();
  });

  it('envía los filtros a la API', async () => {
    const queries: string[] = [];
    server.use(
      http.get('*/api/v1/runs', ({ request }) => {
        queries.push(new URL(request.url).search);
        return HttpResponse.json({ items: [failedRun], total: 1, page: 1, page_size: 25 });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderWithProviders(<RunsPage />, { route: '/runs?pipeline=p-1' });
    expect(await screen.findByText('Solo las ejecuciones de GL_A_SNOWFLAKE.')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Estado'), 'Fallida');
    await screen.findByText('Fallida', { selector: '.sx-pill' });
    expect(queries.at(-1)).toContain('pipeline=p-1');
    expect(queries.at(-1)).toContain('status=failed');
  });
});

describe('RunDetailPage con datos del motor', () => {
  it('muestra cifras, tokens, log, pasos e intentos', async () => {
    renderRun(deltaRun.id);
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

  it('un run en curso avisa de que el log se actualiza solo', async () => {
    server.use(
      http.get('*/api/v1/runs/:id', () =>
        HttpResponse.json({ ...deltaRun, status: 'running', finished_at: null }),
      ),
      http.get('*/api/v1/runs/:id/logs', () =>
        HttpResponse.json({ items: [], next_after: null, finished: false }),
      ),
    );
    renderRun(deltaRun.id);
    expect(await screen.findByText(/Se actualiza cada segundo/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancelar ejecución' })).toBeInTheDocument();
  });
});

describe('RunDetailPage', () => {
  it('muestra el error, los intentos y permite reintentar', async () => {
    let retried = false;
    server.use(
      http.post('*/api/v1/runs/:id/retry', () => {
        retried = true;
        return HttpResponse.json({ ...failedRun, status: 'queued' });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderRun(failedRun.id);
    expect(
      await screen.findByRole('heading', { name: /Ejecución de GL_A_SNOWFLAKE/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('La ejecución falló tras 2 intentos (SX-SAP-503)')).toBeInTheDocument();
    expect(screen.getByText('2 de 2 permitidos')).toBeInTheDocument();
    expect(screen.getByText('Fallo transitorio')).toBeInTheDocument();
    expect(screen.getByText('Fallido')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancelar ejecución' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reintentar' }));
    await screen.findByRole('button', { name: 'Reintentar' });
    expect(retried).toBe(true);
  });

  it('cancela una ejecución en cola con su motivo', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/runs/:id/cancel', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...queuedRun, status: 'cancelled' });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderRun(queuedRun.id);
    expect(
      await screen.findByText('Ningún worker ha tomado todavía esta ejecución.'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancelar ejecución' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText('La ejecución sale de la cola sin llegar a empezar.'),
    ).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Motivo (opcional)'), 'Lanzada por error');
    await user.click(within(dialog).getByRole('button', { name: 'Cancelar ejecución' }));
    await screen.findByRole('button', { name: 'Cancelar ejecución' });
    expect(body).toEqual({ reason: 'Lanzada por error' });
  });
});
