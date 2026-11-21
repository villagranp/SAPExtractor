import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { components } from '@/shared/api/schema';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import {
  badSecretReport,
  configuredProvider,
  emptyProvider,
  entraHandlers,
  okEntraReport,
} from '../test/fixtures';
import { IdentityProviderPage } from './IdentityProviderPage';

type S = components['schemas'];

const TENANT = '11111111-2222-3333-4444-555555555555';
const CLIENT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const page = () =>
  renderWithProviders(<IdentityProviderPage />, { route: '/admin/identity-provider' });

describe('IdentityProviderPage', () => {
  it('guarda la primera configuración con un secreto', async () => {
    let body: S['EntraProviderUpdateRequest'] | undefined;
    server.use(
      ...entraHandlers(emptyProvider),
      http.put('*/api/v1/identity-providers/entra', async ({ request }) => {
        body = (await request.json()) as S['EntraProviderUpdateRequest'];
        return HttpResponse.json({ ...configuredProvider, last_test: null, test_current: false });
      }),
    );
    const user = userEvent.setup();
    page();
    expect(await screen.findByText('Sin configurar')).toBeInTheDocument();
    expect(screen.getByLabelText('URI de redirección')).toHaveValue(emptyProvider.redirect_uri);
    await user.type(screen.getByLabelText('ID de inquilino (directorio)'), TENANT);
    await user.type(screen.getByLabelText('ID de aplicación (cliente)'), CLIENT);
    await user.click(screen.getByRole('radio', { name: 'Secreto de cliente' }));
    await user.type(screen.getByLabelText('Valor del secreto'), 's3cr3t-valor');
    await user.type(screen.getByLabelText(/^Dominios de correo/), 'Empresa.com, filial.es');
    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    expect(await screen.findByText('Configuración guardada.')).toBeInTheDocument();
    expect(body).toMatchObject({
      version: null,
      tenant_id: TENANT,
      client_id: CLIENT,
      credential: { credential_type: 'client_secret', client_secret: 's3cr3t-valor' },
      allowed_domains: ['Empresa.com', 'filial.es'],
      require_group_membership: true,
    });
    // El secreto no se queda en el formulario tras guardar.
    expect(screen.getByLabelText('Valor del secreto')).toHaveValue('');
  });

  it('valida los GUID antes de llamar a la API', async () => {
    server.use(...entraHandlers(emptyProvider));
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText('ID de inquilino (directorio)'), 'contoso');
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    expect(
      await screen.findAllByText('Debe ser un GUID: 00000000-0000-0000-0000-000000000000.'),
    ).toHaveLength(2);
  });

  it('prueba el borrador y muestra el error de Entra', async () => {
    let tested: S['EntraTestRequest'] | undefined;
    server.use(
      ...entraHandlers(configuredProvider),
      http.post('*/api/v1/identity-providers/entra/test', async ({ request }) => {
        tested = (await request.json()) as S['EntraTestRequest'];
        return HttpResponse.json(badSecretReport);
      }),
    );
    const user = userEvent.setup();
    page();
    await user.type(await screen.findByLabelText('Valor del secreto'), 'mal');
    await user.click(screen.getByRole('button', { name: 'Probar configuración' }));
    const report = await screen.findByText('La prueba falló');
    expect(report).toBeInTheDocument();
    expect(tested?.credential).toMatchObject({ client_secret: 'mal' });
    const steps = screen.getByRole('list', { name: 'Resultado de la prueba' });
    expect(within(steps).getByText('AADSTS7000215')).toBeInTheDocument();
    expect(within(steps).getByText('ID de correlación: corr-123')).toBeInTheDocument();
  });

  it('conserva la credencial guardada si no se escribe otra', async () => {
    let body: S['EntraProviderUpdateRequest'] | undefined;
    server.use(
      ...entraHandlers(configuredProvider),
      http.put('*/api/v1/identity-providers/entra', async ({ request }) => {
        body = (await request.json()) as S['EntraProviderUpdateRequest'];
        return HttpResponse.json(configuredProvider);
      }),
    );
    const user = userEvent.setup();
    page();
    expect(
      await screen.findByText('Déjelo vacío para conservar el secreto guardado.'),
    ).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    await screen.findByText('Configuración guardada.');
    expect(body?.credential).toBeNull();
    expect(body?.version).toBe(3);
    expect(body?.default_role_id).toBe(configuredProvider.default_role?.id);
  });

  it('activa Entra ID tras confirmar', async () => {
    let enabledWith: unknown;
    server.use(
      ...entraHandlers(configuredProvider),
      http.post('*/api/v1/identity-providers/entra/enable', async ({ request }) => {
        enabledWith = await request.json();
        return HttpResponse.json({ ...configuredProvider, enabled: true, version: 4 });
      }),
    );
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole('button', { name: 'Activar Entra ID' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Activar Entra ID' }));
    expect(await screen.findByText('Activo')).toBeInTheDocument();
    expect(enabledWith).toEqual({ version: 3 });
  });

  it('no deja activar sin una prueba vigente', async () => {
    server.use(...entraHandlers({ ...configuredProvider, test_current: false }));
    page();
    expect(await screen.findByRole('button', { name: 'Activar Entra ID' })).toBeDisabled();
    expect(
      screen.getByText('La configuración cambió desde esta prueba: vuelva a probarla.'),
    ).toBeInTheDocument();
  });

  it('edita el mapeo de grupos', async () => {
    let mappings: unknown;
    server.use(
      ...entraHandlers(configuredProvider),
      http.put('*/api/v1/identity-providers/entra/group-mappings', async ({ request }) => {
        mappings = await request.json();
        return HttpResponse.json({ ...configuredProvider, version: 4 });
      }),
    );
    const user = userEvent.setup();
    page();
    expect(await screen.findByDisplayValue('SG-SAPExtract-Lectura')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Añadir grupo' }));
    const ids = screen.getAllByLabelText('ID de objeto del grupo');
    await user.type(ids[1] as HTMLElement, '0b0b0b0b-1111-2222-3333-444444444444');
    await user.type(
      screen.getAllByLabelText('Nombre del grupo')[1] as HTMLElement,
      'SG-SAPExtract-Administradores',
    );
    await user.selectOptions(
      screen.getAllByLabelText('Rol')[1] as HTMLElement,
      'Administrador de plataforma',
    );
    await user.click(screen.getByRole('button', { name: 'Quitar el grupo 1' }));
    await user.click(screen.getByRole('button', { name: 'Guardar grupos' }));
    expect(await screen.findByText('Grupos guardados.')).toBeInTheDocument();
    expect(mappings).toEqual({
      version: 3,
      mappings: [
        {
          group_object_id: '0b0b0b0b-1111-2222-3333-444444444444',
          group_name: 'SG-SAPExtract-Administradores',
          role_id: '00000000-0000-7000-8000-000000000001',
        },
      ],
    });
  });

  it('muestra la última prueba guardada', async () => {
    server.use(...entraHandlers({ ...configuredProvider, last_test: okEntraReport }));
    page();
    expect(await screen.findByText('Entra ID acepta la configuración')).toBeInTheDocument();
  });
});
