import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { activePipeline, pipelineHandlers } from '../test/fixtures';
import { PipelinesPage } from './PipelinesPage';

describe('PipelinesPage', () => {
  it('lista cada pipeline con su recorrido, su estado y su última ejecución', async () => {
    server.use(...pipelineHandlers);
    renderWithProviders(<PipelinesPage />);
    const link = await screen.findByRole('link', { name: 'GL_A_SNOWFLAKE' });
    expect(link).toHaveAttribute('href', `/pipelines/${activePipeline.id}`);
    const row = link.closest('tr') as HTMLElement;
    expect(within(row).getByText('FI_GL_14')).toBeInTheDocument();
    expect(within(row).getByText('SNOW_FIN_QAS')).toBeInTheDocument();
    expect(within(row).getByText('Delta')).toBeInTheDocument();
    expect(within(row).getByText('Fusionar por clave')).toBeInTheDocument();
    expect(within(row).getByText('Activo')).toBeInTheDocument();
    expect(within(row).getByText('Entornos distintos')).toBeInTheDocument();
    expect(within(row).getByText('Correcta')).toBeInTheDocument();

    const draft = (await screen.findByRole('link', { name: 'GL_A_CSV' })).closest(
      'tr',
    ) as HTMLElement;
    expect(within(draft).getByText('Borrador')).toBeInTheDocument();
    expect(within(draft).getByText('Sin ejecuciones')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nuevo pipeline' })).toBeInTheDocument();
  });

  it('sin pipelines explica qué hace falta, sin repetir el botón de la cabecera', async () => {
    server.use(
      http.get('*/api/v1/pipelines', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 }),
      ),
      ...pipelineHandlers,
    );
    renderWithProviders(<PipelinesPage />);
    expect(
      await screen.findByRole('heading', { name: 'Aún no hay pipelines' }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Nuevo pipeline' })).toHaveLength(1);
  });

  it('filtra por el destino que llega en la URL', async () => {
    let query = '';
    server.use(
      http.get('*/api/v1/pipelines', ({ request }) => {
        query = new URL(request.url).search;
        return HttpResponse.json({ items: [activePipeline], total: 1, page: 1, page_size: 25 });
      }),
      ...pipelineHandlers,
    );
    renderWithProviders(<PipelinesPage />, { route: '/pipelines?destination=d-1' });
    expect(
      await screen.findByText('Solo los pipelines que cargan en el destino elegido.'),
    ).toBeInTheDocument();
    await screen.findByRole('link', { name: 'GL_A_SNOWFLAKE' });
    expect(query).toContain('destination=d-1');
  });
});
