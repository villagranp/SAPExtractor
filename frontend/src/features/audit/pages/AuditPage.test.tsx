import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { AuditPage } from './AuditPage';

describe('AuditPage', () => {
  it('muestra eventos y el detalle con el diff antes/después', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AuditPage />);
    await user.click(await screen.findByText('role.updated', { selector: 'code' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Detalle del evento')).toBeInTheDocument();
    expect(within(dialog).getByText('["a"]')).toBeInTheDocument();
    expect(within(dialog).getByText('["a","b"]')).toBeInTheDocument();
  });

  it('verifica la integridad y muestra el resultado', async () => {
    server.use(
      http.post('*/api/v1/audit-events/verify', () =>
        HttpResponse.json({
          ok: false,
          checked: 10,
          verified_at: '2026-11-18T11:00:00Z',
          broken_seq: 11,
          broken_id: 'x',
          detail: 'modificado',
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AuditPage />);
    expect(await screen.findByText('Integridad sin verificar')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Verificar integridad' }));
    expect(await screen.findByText('Cadena rota en el evento 11')).toBeInTheDocument();
  });

  it('envía los filtros a la API', async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get('*/api/v1/audit-events', ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AuditPage />);
    await user.selectOptions(await screen.findByLabelText('Resultado'), 'denied');
    await screen.findByText('No hay eventos con estos filtros.');
    expect(seen.some((p) => p.get('outcome') === 'denied')).toBe(true);
  });
});
