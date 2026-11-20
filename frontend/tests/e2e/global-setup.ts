import { request, type FullConfig } from '@playwright/test';

/**
 * Deja listo el administrador de las pruebas E2E.
 *
 * En CI la base de datos es nueva: el administrador inicial del compose tiene una contraseña
 * temporal (E2E_BOOTSTRAP_PASSWORD) que hay que cambiar en el primer acceso. Este paso hace
 * ese cambio por la API y fija E2E_ADMIN_PASSWORD. Si el administrador ya está activo
 * (ejecuciones posteriores en local), no hace nada.
 */
export default async function globalSetup(config: FullConfig) {
  const user = process.env.E2E_ADMIN_USER;
  const temporary = process.env.E2E_BOOTSTRAP_PASSWORD;
  const password = process.env.E2E_ADMIN_PASSWORD;
  if (!user || !temporary || !password) return;

  const baseURL = config.projects[0]?.use.baseURL ?? 'http://localhost:5173';
  const api = await request.newContext({ baseURL });
  try {
    const login = await api.post('/api/v1/auth/login', {
      data: { username: user, password: temporary },
    });
    if (!login.ok()) return; // la temporal ya no vale: el administrador está activo
    const { stage } = (await login.json()) as { stage: string };
    if (stage !== 'password_change') return;
    const cookies = (await api.storageState()).cookies;
    const csrf = cookies.find((c) => c.name === 'sx_csrf')?.value ?? '';
    const change = await api.post('/api/v1/auth/password/change', {
      data: { new_password: password },
      headers: { 'X-CSRF-Token': decodeURIComponent(csrf) },
    });
    if (!change.ok()) {
      throw new Error(`No se pudo activar el administrador E2E: HTTP ${String(change.status())}`);
    }
  } finally {
    await api.dispose();
  }
}
