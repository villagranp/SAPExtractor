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
  'sap_connection:read',
  'sap_connection:manage',
  'sap_connection:test',
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

export const okReport: S['TestReportResponse'] = {
  ok: true,
  summary: 'Conexión correcta.',
  error_code: null,
  started_at: '2026-11-18T10:00:00Z',
  finished_at: '2026-11-18T10:00:01Z',
  steps: [
    {
      name: 'network',
      status: 'ok',
      message: 'gw:443 accesible por TLSv1.3.',
      duration_ms: 12,
      error_code: null,
      details: {},
    },
    {
      name: 'authentication',
      status: 'ok',
      message: 'Gateway aceptó las credenciales (catálogo accesible).',
      duration_ms: 30,
      error_code: null,
      details: {},
    },
    {
      name: 'server_info',
      status: 'ok',
      message: 'Gateway responde: OData 2.0.',
      duration_ms: 0,
      error_code: null,
      details: {},
    },
    {
      name: 'latency',
      status: 'ok',
      message: 'Latencia media 20 ms.',
      duration_ms: 60,
      error_code: null,
      details: {},
    },
  ],
};

export const authFailedReport: S['TestReportResponse'] = {
  ...okReport,
  ok: false,
  summary: 'SAP rechazó las credenciales (HTTP 401).',
  error_code: 'SX-SAP-AUTH',
  steps: [
    okReport.steps[0] as S['TestStepResponse'],
    {
      name: 'authentication',
      status: 'failed',
      message: 'SAP rechazó las credenciales (HTTP 401).',
      duration_ms: 25,
      error_code: 'SX-SAP-AUTH',
      details: { http_status: 401 },
    },
    {
      name: 'server_info',
      status: 'skipped',
      message: 'No se ejecutó.',
      duration_ms: 0,
      error_code: null,
      details: {},
    },
    {
      name: 'latency',
      status: 'skipped',
      message: 'No se ejecutó.',
      duration_ms: 0,
      error_code: null,
      details: {},
    },
  ],
};

export const sapSystem: S['SapSystemDetail'] = {
  id: '00000000-0000-7000-8000-0000000000dd',
  code: 'S4D_100',
  name: 'S/4HANA desarrollo',
  description: null,
  environment: 'DEV',
  system_type: 'S4HANA_ONPREM',
  base_url: 'https://gw.empresa.local:44300',
  sap_client: '100',
  language: 'ES',
  auth_type: 'basic',
  credential_info: {
    username: 'SXTECH',
    client_id: null,
    token_url: null,
    scope: null,
    certificate_subject: null,
    certificate_issuer: null,
    certificate_not_after: null,
  },
  tls_verify: true,
  ca_bundle: [],
  proxy_url: null,
  timeout_seconds: 60,
  max_concurrent_requests: 4,
  status: 'active',
  status_reason: null,
  tags: ['fi'],
  last_test_at: '2026-11-18T10:00:01Z',
  last_test_ok: true,
  last_test_result: okReport,
  test_outdated: false,
  created_at: '2026-11-18T09:00:00Z',
  created_by: 'root',
  updated_at: '2026-11-18T10:00:01Z',
  updated_by: 'root',
  version: 2,
  dependents: [],
};

export const catalogPage: S['CatalogPageResponse'] = {
  items: [
    {
      technical_name: 'ZFI_WS_GL_ITEMS_SRV',
      version: 1,
      service_id: 'ZFI_WS_GL_ITEMS_SRV_0001',
      title: 'ZFI_WS_GL_ITEMS_SRV',
      description: 'ODP: partidas de mayor (0FI_GL_14)',
      service_path: '/sap/opu/odata/sap/ZFI_WS_GL_ITEMS_SRV',
      kind: 'odp',
      classified_by: 'entity_sets',
      supports_delta: true,
      entity_sets: ['EntityOf0FI_GL_14', 'DeltaLinksOfEntityOf0FI_GL_14'],
      odp_entity_sets: ['EntityOf0FI_GL_14'],
    },
    {
      technical_name: 'ZMM_CV_MATERIAL_CDS',
      version: 1,
      service_id: 'ZMM_CV_MATERIAL_CDS_0001',
      title: 'ZMM_CV_MATERIAL_CDS',
      description: 'CDS publicada: maestro de materiales',
      service_path: '/sap/opu/odata/sap/ZMM_CV_MATERIAL_CDS',
      kind: 'cds',
      classified_by: 'entity_sets',
      supports_delta: false,
      entity_sets: ['ZMM_CV_MATERIAL'],
      odp_entity_sets: [],
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
  fetched_at: '2026-11-18T10:05:00Z',
  from_cache: false,
  counts: { odp: 1, cds: 1, other: 0, unknown: 0 },
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
  http.get('*/api/v1/sap-systems', () =>
    HttpResponse.json({ items: [sapSystem], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/sap-systems/:id', () => HttpResponse.json(sapSystem)),
  http.get('*/api/v1/sap-systems/:id/services', () => HttpResponse.json(catalogPage)),
];

export function problem(status: number, code: string, detail: string) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, detail, code, errors: [] },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
