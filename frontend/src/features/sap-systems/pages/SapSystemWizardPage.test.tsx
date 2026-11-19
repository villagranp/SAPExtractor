import { screen } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { authFailedReport, okReport, problem, sapSystem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { SapSystemWizardPage } from './SapSystemWizardPage';

async function fillUntilTest(user: UserEvent) {
  await user.type(await screen.findByLabelText('Código'), 's4d_100');
  await user.type(screen.getByLabelText('Nombre'), 'S/4HANA desarrollo');
  await user.click(screen.getByRole('button', { name: 'Siguiente' }));

  await user.type(await screen.findByLabelText('URL de Gateway'), 'https://gw.empresa.local:44300');
  await user.type(screen.getByLabelText('Mandante'), '100');
  await user.click(screen.getByRole('button', { name: 'Siguiente' }));

  await user.type(await screen.findByLabelText('Usuario técnico'), 'sxtech');
  await user.type(screen.getByLabelText('Contraseña'), 'Secreta-123');
  await user.click(screen.getByRole('button', { name: 'Siguiente' }));
}

describe('SapSystemWizardPage', () => {
  it('prueba la conexión y guarda activando el sistema', async () => {
    let tested: Record<string, unknown> = {};
    let saved: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/sap-systems/test', async ({ request }) => {
        tested = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(okReport);
      }),
      http.post('*/api/v1/sap-systems', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ system: sapSystem, test: okReport }, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderWithProviders(<SapSystemWizardPage />, {
      routes: [{ path: '/sap-systems/:id', element: <p>detalle</p> }],
    });
    await fillUntilTest(user);

    await user.click(screen.getByRole('button', { name: 'Probar conexión' }));
    expect(await screen.findByText('Conexión correcta')).toBeInTheDocument();
    expect(screen.getByText('Autenticación en Gateway')).toBeInTheDocument();
    expect(tested).toMatchObject({
      base_url: 'https://gw.empresa.local:44300',
      sap_client: '100',
      credentials: { auth_type: 'basic', username: 'sxtech', password: 'Secreta-123' },
    });

    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(screen.getByLabelText(/^Activar al guardar/)).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    expect(await screen.findByText('detalle')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/sap-systems/${sapSystem.id}`);
    expect(saved).toMatchObject({
      code: 'S4D_100',
      environment: 'DEV',
      system_type: 'S4HANA_ONPREM',
      run_test: true,
      activate: true,
    });
  });

  it('no avanza con datos de formato incorrecto', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SapSystemWizardPage />);
    await user.type(await screen.findByLabelText('Código'), 'S4D');
    await user.type(screen.getByLabelText('Nombre'), 'Desarrollo');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.type(await screen.findByLabelText('URL de Gateway'), 'gw.local');
    await user.type(screen.getByLabelText('Mandante'), '10');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(screen.getByText('Indique una URL que empiece por http:// o https://.')).toBeVisible();
    expect(screen.getByText('El mandante tiene 3 dígitos.')).toBeVisible();
    expect(screen.queryByLabelText('Usuario técnico')).not.toBeInTheDocument();
  });

  it('una prueba fallida impide activar y muestra el motivo', async () => {
    server.use(http.post('*/api/v1/sap-systems/test', () => HttpResponse.json(authFailedReport)));
    const user = userEvent.setup();
    renderWithProviders(<SapSystemWizardPage />);
    await fillUntilTest(user);
    await user.click(screen.getByRole('button', { name: 'Probar conexión' }));
    expect(await screen.findByText('La prueba falló')).toBeInTheDocument();
    expect(screen.getAllByText('SAP rechazó las credenciales (HTTP 401).').length).toBeGreaterThan(
      0,
    );
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(screen.getByLabelText(/^Activar al guardar/)).toBeDisabled();
  });

  it('lleva al paso del campo que rechaza la API', async () => {
    server.use(
      http.post('*/api/v1/sap-systems/test', () => HttpResponse.json(okReport)),
      http.post('*/api/v1/sap-systems', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Conflicto',
            status: 409,
            detail: 'Ya existe un sistema SAP con el código «S4D_100».',
            code: 'SX-SAP-CODE-TAKEN',
            errors: [{ field: 'code', message: 'Código en uso.' }],
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SapSystemWizardPage />);
    await fillUntilTest(user);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByText('Código en uso.')).toBeInTheDocument();
    expect(screen.getByLabelText('Código')).toHaveValue('s4d_100');
  });

  it('muestra los errores de la prueba sin guardar', async () => {
    server.use(
      http.post('*/api/v1/sap-systems/test', () =>
        problem(422, 'SX-SAP-CA-INVALID', 'El certificado de la CA no es un PEM válido.'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<SapSystemWizardPage />);
    await fillUntilTest(user);
    await user.click(screen.getByRole('button', { name: 'Probar conexión' }));
    expect(
      await screen.findByText('El certificado de la CA no es un PEM válido.'),
    ).toBeInTheDocument();
  });
});
