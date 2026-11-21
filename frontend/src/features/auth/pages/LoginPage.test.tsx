import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { adminMe, problem } from '@/test/handlers';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { safeNext } from '../api/queries';
import { LoginPage } from './LoginPage';

const destination = [{ path: '/admin/users', element: <p>Pantalla de destino</p> }];

describe('LoginPage', () => {
  it('guía por contraseña temporal → cambio obligatorio → destino', async () => {
    let stage: string | null = null;
    server.use(
      http.get('*/api/v1/me', () =>
        stage
          ? HttpResponse.json({ ...adminMe, stage })
          : problem(401, 'SX-AUTH-UNAUTHENTICATED', 'x'),
      ),
      http.post('*/api/v1/auth/login', () => {
        stage = 'password_change';
        return HttpResponse.json({ stage });
      }),
      http.post('*/api/v1/auth/password/change', async ({ request }) => {
        const body = (await request.json()) as { current_password: unknown };
        expect(body.current_password).toBeNull(); // forzado: no pide la actual
        stage = 'active';
        return HttpResponse.json({ stage });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />, { route: '/login?next=/admin/users', routes: destination });

    await user.type(await screen.findByLabelText('Usuario'), 'root');
    await user.type(screen.getByLabelText('Contraseña'), 'Temporal.123456');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));

    expect(await screen.findByText('Cambie su contraseña')).toBeInTheDocument();
    expect(screen.queryByLabelText('Contraseña actual')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Nueva contraseña'), 'Nueva.Clave-Segura-1');
    await user.type(screen.getByLabelText('Repita la nueva contraseña'), 'Nueva.Clave-Segura-1');
    await user.click(screen.getByRole('button', { name: 'Cambiar contraseña' }));

    expect(await screen.findByText('Pantalla de destino')).toBeInTheDocument();
  });

  it('muestra el error de credenciales sin revelar el motivo', async () => {
    server.use(
      http.get('*/api/v1/me', () => problem(401, 'SX-AUTH-UNAUTHENTICATED', 'x')),
      http.post('*/api/v1/auth/login', () =>
        problem(401, 'SX-AUTH-INVALID-CREDENTIALS', 'Usuario o contraseña incorrectos.'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />, { route: '/login' });
    await user.type(await screen.findByLabelText('Usuario'), 'root');
    await user.type(screen.getByLabelText('Contraseña'), 'mala');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Usuario o contraseña incorrectos.');
    // Tras el 401 del login seguimos en el formulario (no es una sesión caducada).
    const passwordField = screen.getByLabelText('Contraseña');
    expect(passwordField).toHaveValue('');
    expect(passwordField).toHaveFocus();
    expect(passwordField).toHaveAttribute('aria-invalid', 'true');
    expect(passwordField.closest('.sx-shaking')).not.toBeNull();
    expect(screen.queryByText(/se bloquea temporalmente/)).not.toBeInTheDocument();

    // Al segundo rechazo seguido se avisa del bloqueo.
    await user.type(passwordField, 'otra');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(await screen.findByText(/se bloquea temporalmente/)).toBeInTheDocument();
  });

  it('otros fallos del login siguen en una alerta, sin sacudida', async () => {
    server.use(
      http.get('*/api/v1/me', () => problem(401, 'SX-AUTH-UNAUTHENTICATED', 'x')),
      http.post('*/api/v1/auth/login', () =>
        problem(429, 'SX-GEN-RATE-LIMIT', 'Demasiados intentos seguidos. Espere 30 segundos.'),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />, { route: '/login' });
    await user.type(await screen.findByLabelText('Usuario'), 'root');
    await user.type(screen.getByLabelText('Contraseña'), 'clave');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Espere 30 segundos');
    expect(screen.getByLabelText('Contraseña')).not.toHaveAttribute('aria-invalid');
    expect(document.querySelector('.sx-shaking')).toBeNull();
  });

  it('pide el código TOTP cuando la sesión está en el paso mfa', async () => {
    server.use(http.get('*/api/v1/me', () => HttpResponse.json({ ...adminMe, stage: 'mfa' })));
    renderWithProviders(<LoginPage />, { route: '/login' });
    expect(await screen.findByText('Verificación en dos pasos')).toBeInTheDocument();
    expect(screen.getByLabelText('Código de verificación')).toBeInTheDocument();
  });

  it('con sesión activa redirige al destino', async () => {
    renderWithProviders(<LoginPage />, { route: '/login?next=/admin/users', routes: destination });
    await waitFor(() => {
      expect(screen.getByText('Pantalla de destino')).toBeInTheDocument();
    });
  });
});

describe('safeNext', () => {
  it('solo acepta rutas internas', () => {
    expect(safeNext('/admin/users?page=2')).toBe('/admin/users?page=2');
    expect(safeNext('https://malicioso.example')).toBe('/');
    expect(safeNext('//malicioso.example')).toBe('/');
    expect(safeNext('/login')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });
});
