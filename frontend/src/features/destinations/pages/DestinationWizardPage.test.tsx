import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  csvDestination,
  csvOkReport,
  destinationOkReport,
  problem,
  snowflakeDestination,
} from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { DestinationWizardPage } from './DestinationWizardPage';

const PEM = '-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----';

describe('DestinationWizardPage', () => {
  it('da de alta un Snowflake con par de claves, lo prueba y lo activa', async () => {
    let tested: Record<string, unknown> = {};
    let saved: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/destinations/test', async ({ request }) => {
        tested = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(destinationOkReport);
      }),
      http.post('*/api/v1/destinations', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { destination: snowflakeDestination, test: destinationOkReport },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    const { router } = renderWithProviders(<DestinationWizardPage />, {
      route: '/nuevo?type=snowflake',
      routes: [{ path: '/destinations/:id', element: <p>detalle</p> }],
    });

    await user.type(await screen.findByLabelText('Código'), 'snow_fin_qas');
    await user.type(screen.getByLabelText('Nombre'), 'Snowflake finanzas');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    await user.type(await screen.findByLabelText('Cuenta'), 'ab12345.east-us-2.azure');
    await user.type(screen.getByLabelText('Usuario de servicio'), 'sx_loader');
    await user.type(screen.getByLabelText('Rol'), 'sx_role');
    await user.type(screen.getByLabelText('Warehouse'), 'sx_wh');
    await user.type(screen.getByLabelText('Base de datos'), 'raw');
    await user.type(screen.getByLabelText('Esquema'), 'sap');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    // Sin clave privada no avanza.
    await user.click(await screen.findByRole('button', { name: 'Siguiente' }));
    expect(screen.getByText('Campo obligatorio.')).toBeInTheDocument();
    await user.click(screen.getByLabelText('Clave privada (PEM)'));
    await user.paste(PEM);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    await user.click(await screen.findByRole('button', { name: 'Probar destino' }));
    expect(await screen.findByText('Destino accesible')).toBeInTheDocument();
    expect(screen.getByText('Autenticación')).toBeInTheDocument();
    expect(tested).toMatchObject({
      type: 'snowflake',
      config: { account: 'ab12345.east-us-2.azure', database: 'raw' },
      secret: { private_key_pem: PEM },
    });

    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(screen.getByLabelText(/^Activar al guardar/)).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Guardar' }));

    expect(await screen.findByText('detalle')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/destinations/${snowflakeDestination.id}`);
    expect(saved).toMatchObject({
      code: 'SNOW_FIN_QAS',
      type: 'snowflake',
      environment: 'DEV',
      activate: true,
      secret: { private_key_pem: PEM },
    });
  });

  it('da de alta un SQL Server con service principal y envía el secreto aparte', async () => {
    let tested: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/destinations/test', async ({ request }) => {
        tested = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(destinationOkReport);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<DestinationWizardPage />, { route: '/nuevo?type=sqlserver' });

    await user.type(await screen.findByLabelText('Código'), 'mssql_dwh');
    await user.type(screen.getByLabelText('Nombre'), 'Almacén corporativo');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    await user.type(await screen.findByLabelText('Servidor'), 'sql01.empresa.local');
    await user.type(screen.getByLabelText('Base de datos'), 'DWH');
    await user.selectOptions(
      screen.getByLabelText(/^Autenticación/),
      screen.getByRole('option', { name: 'Service principal de Entra ID' }),
    );
    await user.type(screen.getByLabelText('Usuario'), '00000000-app');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    expect(await screen.findByText('Usuario SQL o service principal')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Contraseña o secreto de cliente'), 'secreto');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    await user.click(await screen.findByRole('button', { name: 'Probar destino' }));
    expect(await screen.findByText('Destino accesible')).toBeInTheDocument();
    expect(tested).toMatchObject({
      type: 'sqlserver',
      config: {
        host: 'sql01.empresa.local',
        port: 1433,
        database: 'DWH',
        authentication: 'entra_service_principal',
        user: '00000000-app',
      },
      secret: { password: 'secreto' },
    });
  });

  it('da de alta un CSV eligiendo el tipo y muestra los errores de ruta de la API', async () => {
    server.use(
      http.post('*/api/v1/destinations/test', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Regla de negocio incumplida',
            status: 422,
            detail: 'La ruta no puede salir de la carpeta raíz.',
            code: 'SX-DST-CSV-PATH',
            errors: [
              {
                field: 'config.path',
                message: 'La ruta no puede salir de la carpeta raíz.',
                code: 'SX-DST-CSV-PATH',
              },
            ],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<DestinationWizardPage />, { route: '/destinations/new' });
    await user.click(await screen.findByRole('radio', { name: 'Archivos CSV' }));
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.type(await screen.findByLabelText('Código'), 'csv_fi');
    await user.type(screen.getByLabelText('Nombre'), 'Carpeta FI');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));

    await user.selectOptions(await screen.findByLabelText('Carpeta raíz'), '/data/csv');
    await user.type(screen.getByLabelText(/^Subcarpeta/), '../fuera');
    await user.selectOptions(screen.getByLabelText(/^Separador/), '\t');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    // CSV no tiene paso de credenciales: va directo a la prueba.
    await user.click(await screen.findByRole('button', { name: 'Probar destino' }));
    expect(
      await screen.findAllByText('La ruta no puede salir de la carpeta raíz.'),
    ).not.toHaveLength(0);
  });

  it('edita un CSV reutilizando la prueba sin credenciales y envía solo los cambios', async () => {
    let tested: Record<string, unknown> = {};
    let patched: Record<string, unknown> = {};
    server.use(
      http.post('*/api/v1/destinations/test', async ({ request }) => {
        tested = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(csvOkReport);
      }),
      http.patch('*/api/v1/destinations/:id', async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(csvDestination);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<DestinationWizardPage />, {
      route: `/destinations/${csvDestination.id}/edit`,
      routes: [
        { path: '/destinations/:destinationId/edit', element: <DestinationWizardPage /> },
        { path: '/destinations/:id', element: <p>detalle</p> },
      ],
    });
    expect(await screen.findByLabelText('Código')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    const path = await screen.findByLabelText(/^Subcarpeta/);
    expect(path).toHaveValue('fi');
    await user.clear(path);
    await user.type(path, 'fi/gl');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.click(await screen.findByRole('button', { name: 'Probar destino' }));
    await screen.findByText('Destino accesible');
    expect(tested).toMatchObject({ type: 'csv', reuse_credentials_from: csvDestination.id });
    expect(tested).not.toHaveProperty('secret');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByText('detalle')).toBeInTheDocument();
    expect(patched).toEqual({
      version: csvDestination.version,
      config: expect.objectContaining({
        root: '/data/csv',
        path: 'fi/gl',
        delimiter: ';',
      }) as unknown,
    });
  });

  it('un error de guardado lleva al paso con el campo marcado', async () => {
    server.use(
      http.post('*/api/v1/destinations', () =>
        problem(409, 'SX-DST-CODE-TAKEN', 'Ya existe un destino con el código «CSV_FI».'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<DestinationWizardPage />, { route: '/destinations/new?type=csv' });
    await user.type(await screen.findByLabelText('Código'), 'csv_fi');
    await user.type(screen.getByLabelText('Nombre'), 'Carpeta FI');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.selectOptions(await screen.findByLabelText('Carpeta raíz'), '/data/csv');
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await user.click(await screen.findByRole('button', { name: 'Siguiente' }));
    expect(screen.getByLabelText(/^Activar al guardar/)).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    expect(
      await screen.findByText('Ya existe un destino con el código «CSV_FI».'),
    ).toBeInTheDocument();
  });
});
