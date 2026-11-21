import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  adminMe,
  csvDestination,
  deltaRunSummary,
  glPipeline,
  glSource,
  initializedDelta,
  materialSource,
  problem,
} from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { PipelineDetailPage } from './PipelineDetailPage';
import { PipelineNewPage } from './PipelineNewPage';
import { PipelinesPage } from './PipelinesPage';

const detailRoute = {
  route: `/pipelines/${glPipeline.id}`,
  routes: [
    { path: '/pipelines/:pipelineId', element: <PipelineDetailPage /> },
    { path: '/runs/:runId', element: <p>detalle del run</p> },
    { path: '/pipelines', element: <p>lista</p> },
  ],
};

describe('PipelinesPage', () => {
  it('lista pipelines con origen, destino y modos', async () => {
    renderWithProviders(<PipelinesPage />);
    const row = (await screen.findByText('FI_GL_CSV')).closest('tr') as HTMLElement;
    const cells = within(row);
    expect(cells.getByText(glSource.code)).toBeInTheDocument();
    expect(cells.getByText(csvDestination.code)).toBeInTheDocument();
    expect(cells.getByText('Delta ODQ')).toBeInTheDocument();
    expect(cells.getByText('Añadir')).toBeInTheDocument();
  });

  it('sin pipelines invita a crear el primero con un solo botón', async () => {
    server.use(
      http.get('*/api/v1/pipelines', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 }),
      ),
    );
    renderWithProviders(<PipelinesPage />);
    expect(await screen.findByText('Aún no hay pipelines')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Nuevo pipeline' })).toHaveLength(1);
  });
});

describe('PipelineNewPage', () => {
  it('propone modo y carga según el origen y crea el pipeline', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/pipelines', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(glPipeline, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderWithProviders(<PipelineNewPage />, {
      route: '/nuevo',
      routes: [{ path: '/pipelines/:pipelineId', element: <p>detalle</p> }],
    });
    await user.type(await screen.findByLabelText(/^Código/), 'fi_gl_csv');
    await user.type(screen.getByLabelText('Nombre'), 'Mayor hacia CSV');
    await user.selectOptions(
      await screen.findByLabelText('Origen'),
      await screen.findByRole('option', { name: 'FI_GL_ITEMS (Partidas de mayor)' }),
    );
    await user.selectOptions(
      screen.getByLabelText('Destino'),
      await screen.findByRole('option', { name: /^CSV_INTERCAMBIO/ }),
    );
    const mode = screen.getByLabelText(/^Modo de extracción/);
    expect(mode).toHaveValue('DELTA');
    // Origen aditivo hacia CSV: solo «Añadir» (ni upsert ni reemplazo en delta).
    const load = screen.getByLabelText(/^Modo de carga/);
    expect(
      within(load)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Seleccione…', 'Añadir']);
    expect(load).toHaveValue('append');
    await user.type(screen.getByLabelText(/^Tabla o carpeta/), 'GL_ITEMS');
    await user.click(screen.getByRole('button', { name: 'Crear pipeline' }));

    expect(await screen.findByText('detalle')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/pipelines/${glPipeline.id}`);
    expect(body).toMatchObject({
      code: 'FI_GL_CSV',
      source_id: glSource.id,
      destination_id: csvDestination.id,
      extraction_mode: 'DELTA',
      load_options: { load_mode: 'append', target_name: 'GL_ITEMS' },
    });
  });

  it('un origen con marca de agua ofrece marca de agua o carga completa', async () => {
    const user = userEvent.setup();
    renderWithProviders(<PipelineNewPage />);
    await user.selectOptions(
      await screen.findByLabelText('Origen'),
      await screen.findByRole('option', { name: /^MM_MATERIAL/ }),
    );
    const mode = screen.getByLabelText(/^Modo de extracción/);
    expect(
      within(mode)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Seleccione…', 'Marca de agua', 'Carga completa']);
    expect(mode).toHaveValue('WATERMARK');
    expect(screen.getByText(/cada ejecución lee los cambios/)).toBeInTheDocument();
    expect(materialSource.delta_method).toBe('WATERMARK');
  });

  it('muestra el error de la API en el campo', async () => {
    server.use(
      http.post('*/api/v1/pipelines', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Regla',
            status: 422,
            detail: 'El pipeline OTRO ya lee el delta de este entity set.',
            code: 'SX-PPL-DELTA-SHARED-SOURCE',
            errors: [
              {
                field: 'extraction_mode',
                message: 'El pipeline OTRO ya lee el delta de este entity set.',
                code: 'SX-PPL-DELTA-SHARED-SOURCE',
              },
            ],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PipelineNewPage />);
    await user.type(await screen.findByLabelText(/^Código/), 'fi_gl_dos');
    await user.type(screen.getByLabelText('Nombre'), 'Segundo');
    await user.selectOptions(
      await screen.findByLabelText('Origen'),
      await screen.findByRole('option', { name: /^FI_GL_ITEMS/ }),
    );
    await user.selectOptions(
      screen.getByLabelText('Destino'),
      await screen.findByRole('option', { name: /^CSV_INTERCAMBIO/ }),
    );
    await user.type(screen.getByLabelText(/^Tabla o carpeta/), 'GL2');
    await user.click(screen.getByRole('button', { name: 'Crear pipeline' }));
    expect(
      await screen.findByText('El pipeline OTRO ya lee el delta de este entity set.'),
    ).toBeInTheDocument();
  });
});

describe('PipelineDetailPage', () => {
  it('muestra el flujo y las ejecuciones, y «Ejecutar ahora» abre el run', async () => {
    let requested = false;
    server.use(
      http.post('*/api/v1/pipelines/:id/runs', () => {
        requested = true;
        return HttpResponse.json(deltaRunSummary, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderWithProviders(<PipelineDetailPage />, detailRoute);
    expect(
      await screen.findByText('FI_GL_ITEMS hacia CSV_INTERCAMBIO, en GL_ITEMS'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Correcta')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ejecutar ahora' }));
    expect(await screen.findByText('detalle del run')).toBeInTheDocument();
    expect(requested).toBe(true);
    expect(router.state.location.pathname).toBe(`/runs/${deltaRunSummary.id}`);
  });

  it('explica por qué no se puede ejecutar', async () => {
    server.use(
      http.post('*/api/v1/pipelines/:id/runs', () =>
        problem(409, 'SX-PPL-DELTA-BROKEN', 'El delta del pipeline está roto.'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<PipelineDetailPage />, detailRoute);
    await user.click(await screen.findByRole('button', { name: 'Ejecutar ahora' }));
    expect(await screen.findByText('El delta del pipeline está roto.')).toBeInTheDocument();
  });

  it('reinicializa el delta con código y motivo', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/pipelines/:id/delta/reset', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...initializedDelta,
          status: 'NOT_INITIALIZED',
          current_token: null,
          sap_close_pending: true,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PipelineDetailPage />, detailRoute);
    await user.click(await screen.findByRole('tab', { name: 'Delta' }));
    expect(await screen.findByText('Inicializado')).toBeInTheDocument(); // la píldora
    expect(screen.getByText('D000000000260')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reinicializar delta' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/^Motivo/), 'Suscripción borrada en ODQMON');
    await user.type(within(dialog).getByLabelText(/^Escriba FI_GL_CSV/), 'FI_GL_CSV');
    await user.click(within(dialog).getByRole('button', { name: 'Reinicializar' }));
    await screen.findByRole('tab', { name: 'Delta' });
    expect(body).toEqual({ reason: 'Suscripción borrada en ODQMON' });
  });

  it('un lector no ve las acciones', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['pipeline:read'] }),
      ),
    );
    renderWithProviders(<PipelineDetailPage />, detailRoute);
    await screen.findByText('FI_GL_ITEMS hacia CSV_INTERCAMBIO, en GL_ITEMS');
    expect(screen.queryByRole('button', { name: 'Ejecutar ahora' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Eliminar' })).not.toBeInTheDocument();
  });
});
