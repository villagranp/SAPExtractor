import { http, HttpResponse } from 'msw';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];

export const systemInfo: S['SystemInfo'] = {
  name: 'SAPExtract',
  version: '0.1.0',
  environment: 'dev',
  build_commit: 'abc1234',
};

export const healthyReport: S['HealthReport'] = {
  status: 'ok',
  checks: { database: { status: 'ok', detail: null }, migrations: { status: 'ok', detail: null } },
};

const ALL_PERMISSIONS = [
  'user:read',
  'user:create',
  'user:update',
  'user:disable',
  'user:delete',
  'role:read',
  'role:manage',
  'session:revoke',
  'api_token:manage',
  'audit:read',
  'audit:export',
];

export const adminRole: S['RoleRef'] = {
  id: '00000000-0000-7000-8000-000000000001',
  code: 'platform_admin',
  name: 'Administrador de plataforma',
};

export const adminMe: S['MeResponse'] = {
  id: '00000000-0000-7000-8000-0000000000aa',
  username: 'root',
  display_name: 'Administrador',
  email: null,
  auth_source: 'local',
  auth_method: 'local',
  stage: 'active',
  is_break_glass: true,
  mfa_enabled: false,
  roles: [adminRole],
  permissions: ALL_PERMISSIONS,
};

export const roles: S['RoleResponse'][] = [
  {
    ...adminRole,
    description: 'Todo',
    is_system: true,
    permissions: ALL_PERMISSIONS,
    user_count: 1,
    version: 1,
    updated_at: '2026-11-18T10:00:00Z',
  },
  {
    id: '00000000-0000-7000-8000-000000000002',
    code: 'viewer',
    name: 'Visualizador',
    description: 'Solo lectura',
    is_system: true,
    permissions: ['pipeline:read'],
    user_count: 0,
    version: 1,
    updated_at: '2026-11-18T10:00:00Z',
  },
];

export const permissionCatalog: S['PermissionResponse'][] = [
  { code: 'user:read', module: 'identity', module_name: 'Identidad', description: 'Ver usuarios' },
  {
    code: 'user:create',
    module: 'identity',
    module_name: 'Identidad',
    description: 'Crear usuarios',
  },
  { code: 'audit:read', module: 'audit', module_name: 'Auditoría', description: 'Consultar' },
];

export const sampleUser: S['UserSummary'] = {
  id: '00000000-0000-7000-8000-0000000000bb',
  username: 'jperez',
  display_name: 'Usuario de prueba',
  email: 'prueba@example.com',
  auth_source: 'local',
  status: 'active',
  is_break_glass: false,
  mfa_enabled: true,
  temporarily_locked: false,
  roles: [{ id: roles[1]?.id ?? '', code: 'viewer', name: 'Visualizador' }],
  last_login_at: '2026-11-18T09:00:00Z',
  created_at: '2026-11-01T09:00:00Z',
};

export const auditEvent: S['AuditEventResponse'] = {
  id: '00000000-0000-7000-8000-0000000000cc',
  seq: 42,
  occurred_at: '2026-11-18T10:30:00Z',
  actor_type: 'user',
  actor_id: adminMe.id,
  actor_name: 'root',
  action: 'role.updated',
  category: 'identity',
  resource_type: 'role',
  resource_id: '1',
  resource_name: 'Gestores',
  outcome: 'success',
  changes: { permissions: { before: ['a'], after: ['a', 'b'] } },
  context: { ip: '10.0.0.1' },
  reason: null,
  hash: 'f'.repeat(64),
  prev_hash: 'e'.repeat(64),
};

/** Handlers por defecto de MSW (API sana, admin con sesión). Cada prueba puede sobrescribirlos. */
export const handlers = [
  http.get('*/api/v1/system/info', () => HttpResponse.json(systemInfo)),
  http.get('*/api/v1/health/ready', () => HttpResponse.json(healthyReport)),
  http.get('*/api/v1/me', () => HttpResponse.json(adminMe)),
  http.get('*/api/v1/roles', () => HttpResponse.json(roles)),
  http.get('*/api/v1/permissions', () => HttpResponse.json(permissionCatalog)),
  http.get('*/api/v1/users', () =>
    HttpResponse.json({ items: [sampleUser], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/audit-events', () =>
    HttpResponse.json({ items: [auditEvent], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/audit-events/catalog', () =>
    HttpResponse.json([{ action: 'role.updated', category: 'identity' }]),
  ),
  http.get('*/api/v1/audit-events/integrity', () => HttpResponse.json({ last_verification: null })),
];

export function problem(status: number, code: string, detail: string) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, detail, code, errors: [] },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
