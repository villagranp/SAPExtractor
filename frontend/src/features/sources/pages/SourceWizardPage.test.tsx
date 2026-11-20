import { screen, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { sapSystem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { glSource, sourceHandlers } from '../test/fixtures';
import { SourceWizardPage } from './SourceWizardPage';

async function chooseService(user: UserEvent) {
  await screen.findByRole('option', { name: /S4D_100/ });
  await user.selectOptions(screen.getByLabelText('Sistema SAP'), sapSystem.id);
  const services = await screen.findByRole('table', { name: 'Servicios del catálogo' });
  const row = (await within(services).findByText('ZFI_WS_GL_ITEMS_SRV')).closest('tr');
  if (!row) throw new Error('sin fila');
  await user.click(within(row).getByRole('button', { name: 'Elegir' }));
  await user.selectOptions(await screen.findByLabelText('Entity set'), 'EntityOf0FI_GL_14');
  await user.click(screen.getByRole('button', { name: 'Leer esquema' }));
}

const next = (user: UserEvent) => user.click(screen.getByRole('button', { name: 'Siguiente' }));

describe('SourceWizardPage', () => {
  it('crea un origen desde el catálogo con claves, delta y filtro', async () => {
    let saved: Record<string, unknown> = {};
    server.use(
      ...sourceHandlers,
      http.post('*/api/v1/sources', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(glSource, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderWithProviders(<SourceWizardPage />, {
      routes: [{ path: '/sources/:id', element: <p>detalle</p> }],
    });
    await chooseService(user);

    expect(await screen.findByRole('heading', { name: 'Esquema y claves' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'RBUKRS' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'BUDAT' })).not.toBeChecked();
    await next(user);

    expect(screen.getByRole('radio', { name: 'Delta de la ODQ' })).toBeChecked();
    expect(screen.getByText(/suele usar el proceso AIMD/)).toBeInTheDocument();
    const process = screen.getByLabelText(/Proceso de delta en RSA2/);
    await user.clear(process);
    await user.type(process, 'abr');
    expect(screen.getByLabelText('Proceso de delta del extractor')).toHaveValue('before_after');
    await next(user);

    await user.click(screen.getByRole('button', { name: 'Añadir condición' }));
    await user.type(screen.getByLabelText('Valor'), '1000');
    await next(user);

    const code = screen.getByLabelText('Código');
    expect(code).toHaveValue('S_0FI_GL_14');
    await user.clear(code);
    await user.type(code, 'fi_gl_14');
    await next(user);

    expect(screen.getByText('RBUKRS es igual a 1000')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Guardar borrador' }));
    expect(await screen.findByText('detalle')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/sources/${glSource.id}`);
    expect(saved).toMatchObject({
      sap_system_id: sapSystem.id,
      service_name: 'ZFI_WS_GL_ITEMS_SRV',
      entity_set: 'EntityOf0FI_GL_14',
      source_type: 'ODP_EXTRACTOR',
      code: 'FI_GL_14',
      key_fields: ['RBUKRS', 'BELNR'],
      delta: { method: 'ODQ', semantics: 'before_after' },
      default_filters: [{ field: 'RBUKRS', op: 'eq', value: '1000' }],
      page_size: 5000,
      preview_enabled: true,
    });
  });

  it('no avanza sin claves y vuelve al paso del error que devuelve la API', async () => {
    server.use(
      ...sourceHandlers,
      http.post('*/api/v1/sources', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Regla de negocio incumplida',
            status: 422,
            detail: 'RBUKRS admite como máximo 4 caracteres.',
            code: 'SX-SRC-FILTER-VALUE',
            errors: [{ field: 'default_filters[0]', message: 'Máximo 4 caracteres.' }],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SourceWizardPage />);
    await chooseService(user);

    await screen.findByRole('heading', { name: 'Esquema y claves' });
    await user.click(screen.getByRole('checkbox', { name: 'RBUKRS' }));
    await user.click(screen.getByRole('checkbox', { name: 'BELNR' }));
    await next(user);
    expect(screen.getByText(/Marque al menos un campo clave/)).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'RBUKRS' }));
    await next(user);
    await next(user);

    await user.click(screen.getByRole('button', { name: 'Añadir condición' }));
    await user.type(screen.getByLabelText('Valor'), '12345');
    await next(user);
    await next(user);
    await user.click(screen.getByRole('button', { name: 'Guardar borrador' }));

    expect(await screen.findByText('Máximo 4 caracteres.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Filtros' })).toBeInTheDocument();
  });
});
