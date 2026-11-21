import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { glSource } from '@/features/sources/test/fixtures';
import { problem, snowflakeDestination } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { activePipeline, draftPipeline, pipelineHandlers } from '../test/fixtures';
import { PipelineWizardPage } from './PipelineWizardPage';

function renderWizard(route = '/pipelines/new') {
  return renderWithProviders(<PipelineWizardPage />, {
    route,
    routes: [
      // «new» antes que «:pipelineId», igual que en el router de la aplicación.
      { path: '/pipelines/new', element: <PipelineWizardPage /> },
      { path: '/pipelines/:pipelineId/edit', element: <PipelineWizardPage /> },
      { path: '/pipelines/:pipelineId', element: <p>Ficha del pipeline</p> },
    ],
  });
}

const next = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('button', { name: 'Siguiente' }));

describe('PipelineWizardPage', () => {
  it('crea un pipeline cada 30 minutos en días laborables y muestra las próximas ejecuciones', async () => {
    let created: Record<string, unknown> = {};
    let schedules: Record<string, unknown> = {};
    let previewed: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/schedules/preview', async ({ request }) => {
        previewed = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          timezone: 'America/Guatemala',
          fires: ['2026-11-23T12:00:00Z', '2026-11-23T12:30:00Z'],
        });
      }),
      http.post('*/api/v1/pipelines', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(draftPipeline, { status: 201 });
      }),
      http.put('*/api/v1/pipelines/:id/schedules', async ({ request }) => {
        schedules = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([]);
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderWizard();

    // Origen: no se avanza sin elegir uno.
    expect(await screen.findByText('Sin origen elegido')).toBeInTheDocument();
    await next(user);
    expect(await screen.findByText('Elija un origen para continuar.')).toBeInTheDocument();
    await user.click(await screen.findByRole('radio', { name: /FI_GL_14/ }));
    await next(user);

    // Destino.
    await user.click(await screen.findByRole('radio', { name: /SNOW_FIN_QAS/ }));
    const route = screen.getByRole('group', { name: 'Recorrido del pipeline' });
    expect(await within(route).findByText('Snowflake finanzas')).toBeInTheDocument();
    await next(user);

    // Carga: delta con imágenes posteriores hacia Snowflake propone fusionar por clave.
    expect(await screen.findByRole('radio', { name: 'Delta' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Fusionar por clave' })).toBeChecked();
    expect(
      screen.getByText('Filtros por defecto del origen: RBUKRS es igual a 1000.'),
    ).toBeInTheDocument();
    await next(user);

    // Programación con frases.
    await user.click(await screen.findByRole('radio', { name: 'Con programación' }));
    await user.selectOptions(screen.getByLabelText('Repetir'), 'Cada pocos minutos');
    await user.click(screen.getByRole('button', { name: 'De lunes a viernes' }));
    expect(await screen.findByText('Cada 30 minutos, de lunes a viernes')).toBeInTheDocument();
    expect(screen.getByText('*/30 * * * 1-5')).toBeInTheDocument();
    const preview = screen.getByRole('complementary', { name: 'Próximas ejecuciones' });
    expect(await within(preview).findAllByRole('listitem')).toHaveLength(2);
    expect(within(preview).getByText('Horas de America/Guatemala')).toBeInTheDocument();
    // La vista previa espera a que el usuario termine de elegir.
    await waitFor(() => {
      expect(previewed).toMatchObject({ type: 'cron', cron_expression: '*/30 * * * 1-5' });
    });
    await next(user);

    // Nombre: se propone a partir del origen y del destino.
    expect(await screen.findByLabelText('Código')).toHaveValue('FI_GL_14_TO_SNOW_FIN_QAS');
    expect(screen.getByLabelText('Nombre')).toHaveValue(
      'Partidas de mayor hacia Snowflake finanzas',
    );
    await next(user);

    await user.click(await screen.findByRole('button', { name: 'Guardar borrador' }));
    expect(await screen.findByText('Ficha del pipeline')).toBeInTheDocument();
    expect(created).toMatchObject({
      code: 'FI_GL_14_TO_SNOW_FIN_QAS',
      source_id: glSource.id,
      destination_id: snowflakeDestination.id,
      extraction_mode: 'DELTA',
      load: { load_mode: 'upsert', column_mapping: {} },
      filters: null,
    });
    expect(schedules).toMatchObject({
      schedules: [{ type: 'cron', cron_expression: '*/30 * * * 1-5', enabled: true }],
    });
  });

  it('muestra en vivo los avisos de la combinación elegida', async () => {
    server.use(
      http.post('*/api/v1/pipelines/review', () =>
        HttpResponse.json([
          {
            severity: 'error',
            code: 'SX-PIPE-UPSERT-NOT-AFTER-IMAGE',
            field: 'load_mode',
            message: 'Fusionar por clave necesita un delta con imágenes posteriores.',
          },
          {
            severity: 'warning',
            code: 'SX-PIPE-CROSS-ENVIRONMENT',
            field: 'destination_id',
            message: 'El sistema SAP es DEV y el destino es QAS.',
          },
        ]),
      ),
      ...pipelineHandlers,
    );
    renderWizard(`/pipelines/new?source=${glSource.id}&destination=${snowflakeDestination.id}`);
    expect(await screen.findByText('Un bloqueo impide activar el pipeline')).toBeInTheDocument();
    expect(
      screen.getByText('Fusionar por clave necesita un delta con imágenes posteriores.'),
    ).toBeInTheDocument();
    // Un solo aviso se muestra tal cual, sin título genérico.
    expect(screen.getByText('El sistema SAP es DEV y el destino es QAS.')).toBeInTheDocument();
    expect(screen.queryByText('Un aviso')).not.toBeInTheDocument();
  });

  it('al editar un pipeline activo no ofrece cambiar origen ni destino y vuelve al paso del error', async () => {
    let patched: Record<string, unknown> = {};
    server.use(
      http.patch('*/api/v1/pipelines/:id', async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Error',
            status: 422,
            detail: 'Revise los datos.',
            code: 'SX-DST-IDENTIFIER-INVALID',
            errors: [{ field: 'load.target_name', message: '«mi tabla» no es un nombre válido.' }],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        );
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderWizard(`/pipelines/${activePipeline.id}/edit`);

    expect(
      await screen.findByRole('heading', { name: 'Editar GL_A_SNOWFLAKE' }),
    ).toBeInTheDocument();
    const steps = screen.getByRole('list', { name: 'Pasos del asistente' });
    expect(within(steps).queryByText('Origen')).not.toBeInTheDocument();
    expect(await screen.findByLabelText('Nombre en destino de DMBTR')).toHaveValue('IMPORTE_ML');
    expect(screen.getByLabelText('Cargar la columna GJAHR')).not.toBeChecked();
    // La programación guardada llega convertida en frases.
    await next(user);
    expect(await screen.findByText('Cada 30 minutos, de lunes a viernes')).toBeInTheDocument();
    await next(user);
    await next(user);
    await user.click(await screen.findByRole('button', { name: 'Guardar cambios' }));

    expect(await screen.findByText('«mi tabla» no es un nombre válido.')).toBeInTheDocument();
    expect(screen.getByLabelText('Tabla o archivo de destino')).toBeInTheDocument();
    expect(patched).toMatchObject({ version: 3, use_source_filters: true });
    expect(patched).not.toHaveProperty('source_id');
  });

  it('sin orígenes, lleva a crear uno', async () => {
    server.use(
      http.get('*/api/v1/sources', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50 }),
      ),
      ...pipelineHandlers,
    );
    renderWizard();
    expect(await screen.findByRole('heading', { name: 'Aún no hay orígenes' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Crear un origen' })).toHaveAttribute(
      'href',
      '/sources/new',
    );
  });

  it('avisa si no puede leer el pipeline que se quiere editar', async () => {
    server.use(
      http.get('*/api/v1/pipelines/:id', () =>
        problem(404, 'SX-PIPE-NOT-FOUND', 'El pipeline no existe.'),
      ),
      ...pipelineHandlers,
    );
    renderWizard('/pipelines/desconocido/edit');
    expect(await screen.findByText('El pipeline no existe.')).toBeInTheDocument();
  });
});
