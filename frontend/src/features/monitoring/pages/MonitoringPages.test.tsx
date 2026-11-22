import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe, emailChannel, failedAlert } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { AlertsBell } from '../components/AlertsBell';
import { AlertsPage } from './AlertsPage';
import { DashboardPage } from './DashboardPage';

describe('DashboardPage', () => {
  it('resume el estado: indicadores, runs en curso, fallos, frescura y workers', async () => {
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText('Pipelines activos')).toBeInTheDocument();
    expect(await screen.findByText('125 mil')).toBeInTheDocument();
    expect(screen.getByText('95,3 %')).toBeInTheDocument();
    const live = screen.getByRole('table', { name: 'Ejecuciones en curso' });
    expect(within(live).getByText('3000 filas en 3 páginas')).toBeInTheDocument();
    expect(within(live).getByText('Extraer')).toBeInTheDocument();
    const failures = screen.getByRole('table', { name: 'Fallos recientes' });
    expect(within(failures).getByText('SX-DST-DOWN')).toBeInTheDocument();
    const freshness = await screen.findByRole('table', { name: 'Frescura de los datos' });
    expect(within(freshness).getByText('Con retraso')).toBeInTheDocument();
    expect(within(freshness).getByText('Hace 260 min')).toBeInTheDocument();
    expect(screen.getByText('worker-1')).toBeInTheDocument();
    expect(
      await screen.findByRole('link', { name: /^FI_GL_CSV: Delta, Fallida/ }),
    ).toBeInTheDocument();
  });
});

describe('AlertsPage', () => {
  it('reconoce una alerta abierta', async () => {
    let acked = false;
    server.use(
      http.post('*/api/v1/alerts/:id/ack', () => {
        acked = true;
        return HttpResponse.json({ ...failedAlert, status: 'acknowledged' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AlertsPage />);
    const row = (await screen.findByText(/El último run falló/)).closest('tr') as HTMLElement;
    expect(within(row).getByText('Crítica')).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'FI_GL_CSV' })).toBeInTheDocument();
    expect(within(row).getByText('2 veces')).toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Reconocer' }));
    expect(acked).toBe(true);
  });

  it('crea una regla de frescura con su parámetro', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/alert-rules', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...body, id: 'x' }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AlertsPage />);
    await user.click(await screen.findByRole('tab', { name: 'Reglas' }));
    await user.click(await screen.findByRole('button', { name: 'Nueva regla' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Nombre'), 'SLA finanzas');
    await user.selectOptions(within(dialog).getByLabelText(/^Tipo/), 'freshness');
    await user.selectOptions(within(dialog).getByLabelText('Ámbito'), 'tag');
    await user.type(within(dialog).getByLabelText('Etiqueta'), 'finanzas');
    const minutes = within(dialog).getByLabelText(/^Minutos/);
    await user.clear(minutes);
    await user.type(minutes, '60');
    await user.click(within(dialog).getByRole('button', { name: 'Guardar regla' }));
    expect(body).toMatchObject({
      name: 'SLA finanzas',
      type: 'freshness',
      scope: 'tag',
      scope_value: 'finanzas',
      params: { minutes: 60 },
      severity: 'warning',
    });
  });

  it('crea un canal de correo, lo suscribe y lo prueba', async () => {
    let created: Record<string, unknown> = {};
    let subscribed: Record<string, unknown> = {};
    let tested = false;
    server.use(
      http.post('*/api/v1/notification-channels', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(emailChannel, { status: 201 });
      }),
      http.post('*/api/v1/alert-subscriptions', async ({ request }) => {
        subscribed = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { id: 's1', ...subscribed, created_at: '2026-11-20T10:00:00Z', created_by: 'root' },
          { status: 201 },
        );
      }),
      http.post('*/api/v1/notification-channels/:id/test', () => {
        tested = true;
        return HttpResponse.json({ ...emailChannel, last_test_ok: true });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AlertsPage />);
    await user.click(await screen.findByRole('tab', { name: 'Canales' }));
    await user.click(await screen.findByRole('button', { name: 'Nuevo canal' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Nombre'), 'Equipo de datos');
    await user.type(
      within(dialog).getByLabelText(/^Destinatarios/),
      'datos@example.com, jefe@example.com',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Crear canal' }));
    await screen.findByRole('button', { name: 'Nuevo canal' });
    expect(created).toMatchObject({
      type: 'email',
      config: { recipients: ['datos@example.com', 'jefe@example.com'] },
    });
    expect(subscribed).toEqual({
      channel_id: emailChannel.id,
      rule_id: null,
      min_severity: 'warning',
    });
    const row = (await screen.findByText('datos@example.com')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Enviar prueba' }));
    expect(tested).toBe(true);
  });

  it('un lector no ve los canales ni las acciones', async () => {
    server.use(
      http.get('*/api/v1/me', () =>
        HttpResponse.json({ ...adminMe, permissions: ['dashboard:read'] }),
      ),
    );
    renderWithProviders(<AlertsPage />);
    await screen.findByText(/El último run falló/);
    expect(screen.queryByRole('tab', { name: 'Canales' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reconocer' })).not.toBeInTheDocument();
  });
});

describe('AlertsBell', () => {
  it('muestra las alertas abiertas y lleva a la pantalla de alertas', async () => {
    renderWithProviders(<AlertsBell />);
    const bell = await screen.findByRole('link', { name: '1 alertas abiertas' });
    expect(bell).toHaveAttribute('href', '/alerts');
  });
});
