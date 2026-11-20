import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { glSource, sourceHandlers } from '../test/fixtures';
import { SourceDetailPage } from './SourceDetailPage';

const page = () =>
  renderWithProviders(<SourceDetailPage />, {
    route: `/sources/${glSource.id}`,
    routes: [{ path: '/sources/:sourceId', element: <SourceDetailPage /> }],
  });

describe('SourceDetailPage', () => {
  it('muestra la configuración y activa un borrador', async () => {
    let activated = false;
    server.use(
      ...sourceHandlers,
      http.post('*/api/v1/sources/:id/activate', () => {
        activated = true;
        return HttpResponse.json({ ...glSource, status: 'active' });
      }),
    );
    const user = userEvent.setup();
    page();
    expect(await screen.findByText('FI_GL_14')).toBeInTheDocument();
    expect(screen.getByText(glSource.filter_expression)).toBeInTheDocument();
    expect(screen.getByText('Borrador')).toBeInTheDocument();
    // Las acciones aparecen cuando llegan los permisos (`/me`).
    await user.click(await screen.findByRole('button', { name: 'Activar' }));
    expect(activated).toBe(true);
  });

  it('lee la vista previa bajo demanda', async () => {
    server.use(
      ...sourceHandlers,
      http.post('*/api/v1/sources/:id/preview', () =>
        HttpResponse.json({
          columns: ['RBUKRS', 'BELNR', 'BUDAT'],
          rows: [{ RBUKRS: '1000', BELNR: '0100000001', BUDAT: '2026-01-31' }],
          truncated: true,
          count: null,
          elapsed_ms: 120,
        }),
      ),
    );
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole('tab', { name: 'Vista previa' }));
    await user.click(screen.getByRole('button', { name: 'Leer filas' }));
    expect(await screen.findByText('0100000001')).toBeInTheDocument();
    expect(screen.getByText(/1 filas en 120 ms\. Hay más filas en SAP\./)).toBeInTheDocument();
  });

  it('compara versiones del esquema', async () => {
    server.use(...sourceHandlers);
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole('tab', { name: 'Esquema' }));
    expect(await screen.findByText('Columnas nuevas:')).toBeInTheDocument();
    expect(screen.getAllByText('GJAHR').length).toBeGreaterThan(0);
  });
});
