import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  csvDestination,
  destinationOkReport,
  sapSystem,
  snowflakeDestination,
} from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { DestinationDetailPage } from './DestinationDetailPage';

function renderDetail(id: string) {
  return renderWithProviders(<DestinationDetailPage />, {
    route: `/destinations/${id}`,
    routes: [{ path: '/destinations/:destinationId', element: <DestinationDetailPage /> }],
  });
}

describe('DestinationDetailPage', () => {
  it('muestra la configuración, la huella de la clave y la última prueba', async () => {
    renderDetail(snowflakeDestination.id);
    expect(await screen.findByText('SHA256:abc123')).toBeInTheDocument();
    expect(screen.getByText('SX_LOADER')).toBeInTheDocument();
    expect(screen.getByText('Privilegios de carga')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rotar claves' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Desactivar' })).toBeInTheDocument();
  });

  it('un CSV sin probar no ofrece rotar claves ni permite activar', async () => {
    renderDetail(csvDestination.id);
    expect(await screen.findByText('Este destino aún no se ha probado.')).toBeInTheDocument();
    expect(screen.getByText('Separador')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rotar claves' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();
  });

  it('rota el par de claves y muestra la prueba con la clave nueva', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/destinations/:id/credentials/rotate', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ destination: snowflakeDestination, test: destinationOkReport });
      }),
    );
    const user = userEvent.setup();
    renderDetail(snowflakeDestination.id);
    await user.click(await screen.findByRole('button', { name: 'Rotar claves' }));
    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Guardar y probar' });
    expect(save).toBeDisabled();
    await user.click(within(dialog).getByLabelText('Clave privada (PEM)'));
    await user.paste('-----BEGIN PRIVATE KEY-----');
    await user.click(save);
    expect(await within(dialog).findByText('Destino accesible')).toBeInTheDocument();
    expect(body).toEqual({
      secret: { private_key_pem: '-----BEGIN PRIVATE KEY-----' },
      version: snowflakeDestination.version,
    });
  });

  it('genera la vista previa del DDL a partir de un entity set SAP', async () => {
    let request: Record<string, unknown> = {};
    server.use(
      http.get('*/api/v1/sap-systems/:id/services/:name/entity-sets', () =>
        HttpResponse.json({
          service: { technical_name: 'ZFI_WS_GL_ITEMS_SRV', version: 1 },
          odata_version: '2.0',
          entity_sets: [
            { name: 'EntityOf0FI_GL_14', entity_type: 'T', label: null, odp_role: 'entity' },
            {
              name: 'DeltaLinksOfEntityOf0FI_GL_14',
              entity_type: 'D',
              label: null,
              odp_role: 'delta_links',
            },
          ],
        }),
      ),
      http.post('*/api/v1/destinations/:id/ddl-preview', async ({ request: r }) => {
        request = (await r.json()) as Record<string, unknown>;
        return HttpResponse.json({
          statements: ['CREATE TABLE IF NOT EXISTS "RAW"."SAP"."FI_GL_14" (...)'],
          keys: ['BELNR'],
          columns: [
            {
              source: 'BELNR',
              target: 'BELNR',
              edm_type: 'Edm.String',
              arrow_type: 'string',
              is_key: true,
              label: 'Documento',
            },
          ],
        });
      }),
    );
    const user = userEvent.setup();
    renderDetail(snowflakeDestination.id);
    await user.click(await screen.findByRole('tab', { name: 'Vista previa de DDL' }));
    await user.selectOptions(
      await screen.findByLabelText('Sistema SAP'),
      await screen.findByRole('option', { name: `${sapSystem.code} · ${sapSystem.name}` }),
    );
    const service = screen.getByLabelText('Servicio');
    await user.selectOptions(
      service,
      await within(service).findByRole('option', { name: /^ZFI_WS_GL_ITEMS_SRV/ }),
    );
    const entitySet = screen.getByLabelText('Entity set');
    await user.selectOptions(
      entitySet,
      await within(entitySet).findByRole('option', { name: 'EntityOf0FI_GL_14' }),
    );
    expect(within(entitySet).queryByText('DeltaLinksOfEntityOf0FI_GL_14')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Nombre en destino/)).toHaveValue('FI_GL_14');
    await user.click(screen.getByRole('button', { name: 'Generar vista previa' }));
    expect(await screen.findByTestId('ddl-statements')).toHaveTextContent('"RAW"."SAP"."FI_GL_14"');
    expect(screen.getByText('Documento')).toBeInTheDocument();
    expect(request).toMatchObject({
      source: { service: 'ZFI_WS_GL_ITEMS_SRV', entity_set: 'EntityOf0FI_GL_14' },
      target_name: 'FI_GL_14',
      load_mode: 'append',
    });
  });
});
