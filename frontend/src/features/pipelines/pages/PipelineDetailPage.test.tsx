import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { initializedDelta, problem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import {
  activePipeline,
  draftPipeline,
  okActivation,
  pipelineHandlers,
  queuedRun,
} from '../test/fixtures';
import { PipelineDetailPage } from './PipelineDetailPage';

function renderDetail(id: string) {
  return renderWithProviders(<PipelineDetailPage />, {
    route: `/pipelines/${id}`,
    routes: [
      { path: '/pipelines/:pipelineId', element: <PipelineDetailPage /> },
      { path: '/runs/:runId', element: <p>Detalle de la ejecución</p> },
      { path: '/pipelines', element: <p>Lista de pipelines</p> },
    ],
  });
}

describe('PipelineDetailPage', () => {
  it('muestra el recorrido, los avisos y la configuración de carga', async () => {
    server.use(...pipelineHandlers);
    renderDetail(activePipeline.id);
    const route = await screen.findByRole('group', { name: 'Recorrido del pipeline' });
    expect(within(route).getByText('FI_GL_14')).toBeInTheDocument();
    expect(within(route).getByText('SNOW_FIN_QAS')).toBeInTheDocument();
    expect(within(route).getByText('DEV')).toBeInTheDocument();
    expect(within(route).getByText('QAS')).toBeInTheDocument();
    // El motivo confirmado sustituye al aviso genérico de entornos distintos.
    expect(
      screen.queryByText('El sistema SAP es DEV y el destino es QAS.'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('Motivo: Carga de validación acordada con finanzas'),
    ).toBeInTheDocument();
    expect(screen.getByText('S4D_100_FI_GL_14')).toBeInTheDocument();
    expect(screen.getByText('1 excluida y 1 renombrada')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ejecutar ahora' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Comprobar y activar' })).not.toBeInTheDocument();
  });

  it('dice la programación en una frase y permite editarla', async () => {
    let saved: Record<string, unknown> = {};
    server.use(
      http.put('*/api/v1/pipelines/:id/schedules', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([]);
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(activePipeline.id);
    await user.click(await screen.findByRole('tab', { name: 'Programación' }));
    expect(await screen.findByText('Cada 30 minutos, de lunes a viernes')).toBeInTheDocument();
    expect(screen.getByText('Una ventana de exclusión')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Editar programación' }));
    await user.selectOptions(await screen.findByLabelText('Cada'), '15 minutos');
    await user.click(screen.getByRole('button', { name: 'Guardar programación' }));
    await screen.findByRole('button', { name: 'Editar programación' });
    expect(saved).toMatchObject({
      schedules: [
        {
          type: 'cron',
          cron_expression: '*/15 * * * 1-5',
          timezone: 'America/Guatemala',
          blackout_windows: [{ kind: 'weekly', start: '22:00', end: '02:00' }],
        },
      ],
    });
  });

  it('comprueba y activa un borrador', async () => {
    let activated = false;
    server.use(
      http.post('*/api/v1/pipelines/:id/validate', () => HttpResponse.json(okActivation)),
      http.post('*/api/v1/pipelines/:id/activate', () => {
        activated = true;
        return HttpResponse.json({ ...draftPipeline, status: 'active' });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(draftPipeline.id);
    expect(
      await screen.findByText('Este pipeline es un borrador: no se ejecuta hasta que lo active.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ejecutar ahora' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Comprobar y activar' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('El origen está activo.')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Correcto')).toHaveLength(okActivation.checks.length);
    await user.click(within(dialog).getByRole('button', { name: 'Activar pipeline' }));
    await screen.findByRole('button', { name: 'Editar' });
    expect(activated).toBe(true);
  });

  it('no deja activar si la comprobación falla', async () => {
    server.use(
      http.post('*/api/v1/pipelines/:id/validate', () =>
        HttpResponse.json({
          ...okActivation,
          ok: false,
          checks: [
            {
              code: 'destination_test',
              ok: false,
              message: 'El destino CSV_INTERCAMBIO no está activo: pruébelo de nuevo.',
            },
          ],
        }),
      ),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(draftPipeline.id);
    await user.click(await screen.findByRole('button', { name: 'Comprobar y activar' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      await within(dialog).findByText(
        'El destino CSV_INTERCAMBIO no está activo: pruébelo de nuevo.',
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('Falla')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Activar pipeline' })).toBeDisabled();
  });

  it('avisa del delta al pausar y envía el motivo', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/pipelines/:id/pause', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          pipeline: { ...activePipeline, status: 'paused' },
          warnings: [],
        });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(activePipeline.id);
    await user.click(await screen.findByRole('button', { name: 'Pausar' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/la cola de delta sigue creciendo en SAP/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Motivo'), 'Cierre contable');
    await user.click(within(dialog).getByRole('button', { name: 'Pausar' }));
    await screen.findByRole('button', { name: 'Editar' });
    expect(body).toEqual({ reason: 'Cierre contable' });
  });

  it('lanza una ejecución y abre su detalle', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/pipelines/:id/run', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(queuedRun, { status: 201 });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(activePipeline.id);
    await user.click(await screen.findByRole('button', { name: 'Ejecutar ahora' }));
    const dialog = await screen.findByRole('dialog');
    await user.selectOptions(within(dialog).getByLabelText('Qué se extrae'), 'Recarga completa');
    expect(within(dialog).getByText(/No toca la suscripción de delta/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Ejecutar ahora' }));
    expect(await screen.findByText('Detalle de la ejecución')).toBeInTheDocument();
    expect(body).toEqual({ mode: 'FULL', reason: null });
  });

  it('reinicializa el delta escribiendo el código y el motivo', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/pipelines/:id/delta/reset', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...initializedDelta, status: 'NOT_INITIALIZED' });
      }),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(activePipeline.id);
    await user.click(await screen.findByRole('tab', { name: 'Delta' }));
    expect(await screen.findByText(initializedDelta.current_token ?? '')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reinicializar delta' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Reinicializar' });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Motivo'), 'Cambio de filtros en SAP');
    await user.type(
      within(dialog).getByLabelText('Escriba GL_A_SNOWFLAKE para confirmar'),
      'GL_A_SNOWFLAKE',
    );
    await user.click(confirm);
    await screen.findByRole('button', { name: 'Editar' });
    expect(body).toEqual({ reason: 'Cambio de filtros en SAP' });
  });

  it('un pipeline de carga completa no tiene pestaña de delta', async () => {
    server.use(...pipelineHandlers);
    renderDetail(draftPipeline.id);
    expect(await screen.findByRole('tab', { name: 'Programación' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Delta' })).not.toBeInTheDocument();
  });

  it('explica por qué no se pudo eliminar', async () => {
    server.use(
      http.delete('*/api/v1/pipelines/:id', () =>
        problem(
          409,
          'SX-PIPE-RUN-LIVE',
          'El pipeline tiene un run en cola o en curso: cancélelo antes de eliminarlo.',
        ),
      ),
      ...pipelineHandlers,
    );
    const user = userEvent.setup();
    renderDetail(activePipeline.id);
    await user.click(await screen.findByRole('button', { name: 'Eliminar' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Eliminar' });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText('Escriba GL_A_SNOWFLAKE para confirmar'),
      'GL_A_SNOWFLAKE',
    );
    await user.click(confirm);
    expect(
      await within(dialog).findByText(
        'El pipeline tiene un run en cola o en curso: cancélelo antes de eliminarlo.',
      ),
    ).toBeInTheDocument();
  });
});
