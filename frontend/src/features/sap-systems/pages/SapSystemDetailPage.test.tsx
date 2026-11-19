import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { sapSystem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { SapSystemDetailPage } from './SapSystemDetailPage';

const route = `/sap-systems/${sapSystem.id}`;

function renderDetail() {
  return renderWithProviders(<SapSystemDetailPage />, {
    route,
    routes: [
      { path: '/sap-systems/:systemId', element: <SapSystemDetailPage /> },
      { path: '/sap-systems', element: <p>listado</p> },
    ],
  });
}

describe('SapSystemDetailPage', () => {
  it('muestra la conexión, la credencial enmascarada y la última prueba', async () => {
    renderDetail();
    expect(await screen.findByText('Última prueba de conexión')).toBeInTheDocument();
    expect(screen.getByText('Usuario y contraseña · SXTECH · ••••••')).toBeInTheDocument();
    expect(screen.getByTestId('test-report')).toHaveTextContent('Conexión correcta');
    expect(screen.getByRole('button', { name: 'Desactivar' })).toBeInTheDocument();
  });

  it('el explorador clasifica servicios y abre sus entity sets', async () => {
    server.use(
      http.get('*/api/v1/sap-systems/:id/services/:service/entity-sets', () =>
        HttpResponse.json({
          service: {
            technical_name: 'ZFI_WS_GL_ITEMS_SRV',
            version: 1,
            service_id: 'ZFI_WS_GL_ITEMS_SRV_0001',
            title: null,
            description: 'ODP: partidas de mayor (0FI_GL_14)',
            service_path: '/sap/opu/odata/sap/ZFI_WS_GL_ITEMS_SRV',
            kind: 'odp',
            classified_by: 'entity_sets',
            supports_delta: true,
            entity_sets: ['EntityOf0FI_GL_14'],
            odp_entity_sets: ['EntityOf0FI_GL_14'],
          },
          odata_version: '2.0',
          entity_sets: [
            {
              name: 'EntityOf0FI_GL_14',
              entity_type: 'X.EntityOf0FI_GL_14Type',
              label: 'Partidas',
              odp_role: 'entity',
              odp_name: '0FI_GL_14',
              supports_delta: true,
              change_tracking: true,
              pageable: true,
              countable: true,
              keys: ['BELNR'],
              properties: [
                {
                  name: 'BELNR',
                  edm_type: 'Edm.String',
                  nullable: false,
                  is_key: true,
                  max_length: 10,
                  precision: null,
                  scale: null,
                  label: 'Documento',
                  filterable: true,
                  sortable: true,
                  display_format: null,
                },
              ],
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByRole('tab', { name: 'Servicios' }));
    expect(await screen.findByTestId('catalog-summary')).toHaveTextContent('1 ODP · 1 CDS');
    const cds = (await screen.findByText('ZMM_CV_MATERIAL_CDS')).closest('tr') as HTMLElement;
    expect(within(cds).getByText('CDS')).toBeInTheDocument();
    expect(within(cds).getByText(/Solo full/)).toBeInTheDocument();

    await user.click(screen.getByText('ZFI_WS_GL_ITEMS_SRV'));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Extractor (EntityOf)')).toBeInTheDocument();
    expect(within(dialog).getByText('Documento')).toBeInTheDocument();
    expect(within(dialog).getByText('Edm.String(10)')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Crear origen' })).toBeDisabled();
  });

  it('eliminar exige escribir el código y un motivo', async () => {
    let deleted = false;
    server.use(
      http.delete('*/api/v1/sap-systems/:id', () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByRole('button', { name: 'Eliminar' }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Eliminar' });
    await user.type(within(dialog).getByLabelText('Motivo (queda en la auditoría)'), 'Retirado');
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Escriba S4D_100 para confirmar'), 'S4D_100');
    await user.click(confirm);
    expect(await screen.findByText('listado')).toBeInTheDocument();
    expect(deleted).toBe(true);
  });
});
