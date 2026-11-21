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
  'idp:manage',
  'api_token:manage',
  'audit:read',
  'audit:export',
  'sap_connection:read',
  'sap_connection:manage',
  'sap_connection:test',
  'source:read',
  'source:manage',
  'source:preview',
  'destination:read',
  'destination:manage',
  'destination:test',
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

// ------------------------------------------------------------------ destinos (plan 07)
export const csvConfigSchema = {
  title: 'CSV',
  type: 'object',
  required: ['root'],
  properties: {
    root: { title: 'Carpeta raíz', type: 'string', enum: ['/data/csv'] },
    path: { title: 'Subcarpeta', type: 'string', default: '', maxLength: 300 },
    delimiter: { title: 'Separador', type: 'string', enum: [',', ';', '|', '\t'], default: ',' },
    header: { title: 'Fila de encabezado', type: 'boolean', default: true },
    compression: { title: 'Compresión', type: 'string', enum: ['none', 'gzip'], default: 'none' },
    max_rows_per_file: {
      title: 'Filas máximas por archivo',
      type: 'integer',
      default: 1000000,
      minimum: 1000,
      maximum: 20000000,
    },
  },
};

export const snowflakeConfigSchema = {
  title: 'Snowflake',
  type: 'object',
  required: ['account', 'user', 'role', 'warehouse', 'database', 'schema_name'],
  properties: {
    account: { title: 'Cuenta', type: 'string' },
    user: { title: 'Usuario de servicio', type: 'string' },
    role: { title: 'Rol', type: 'string' },
    warehouse: { title: 'Warehouse', type: 'string' },
    database: { title: 'Base de datos', type: 'string' },
    schema_name: { title: 'Esquema', type: 'string' },
  },
};

export const snowflakeSecretSchema = {
  title: 'Par de claves',
  type: 'object',
  required: ['private_key_pem'],
  properties: {
    private_key_pem: {
      title: 'Clave privada (PEM)',
      type: 'string',
      format: 'password',
      writeOnly: true,
    },
    private_key_passphrase: {
      title: 'Contraseña de la clave',
      anyOf: [{ type: 'string', format: 'password', writeOnly: true }, { type: 'null' }],
      default: null,
    },
  },
};

export const destinationTypes: S['DestinationTypeResponse'][] = [
  {
    type: 'csv',
    label: 'Archivos CSV',
    requires_secret: false,
    supported_modes: ['append', 'replace'],
  },
  {
    type: 'snowflake',
    label: 'Snowflake',
    requires_secret: true,
    supported_modes: ['append', 'replace', 'upsert'],
  },
];

function okStep(name: string, message: string): S['CheckStepResponse'] {
  return { name, status: 'ok', message, duration_ms: 20, error_code: null, details: {} };
}

export const destinationOkReport: S['CheckReportResponse'] = {
  ok: true,
  summary: 'Conexión correcta.',
  error_code: null,
  started_at: '2026-11-20T10:00:00Z',
  finished_at: '2026-11-20T10:00:02Z',
  steps: [
    okStep('network', 'ab12345.snowflakecomputing.com:443 accesible.'),
    okStep('authentication', 'Snowflake aceptó el par de claves.'),
    okStep('context', 'Rol SX_ROLE, warehouse SX_WH, RAW.SAP.'),
    okStep('privileges', 'Puede crear tablas y stages.'),
  ],
};

export const csvOkReport: S['CheckReportResponse'] = {
  ...destinationOkReport,
  steps: [okStep('path', 'Carpeta lista.'), okStep('write', 'Escritura correcta.')],
};

export const snowflakeDestination: S['DestinationDetail'] = {
  id: '00000000-0000-7000-8000-0000000000d1',
  code: 'SNOW_FIN_QAS',
  name: 'Snowflake finanzas',
  description: null,
  type: 'snowflake',
  environment: 'QAS',
  status: 'active',
  tags: ['finanzas'],
  target: 'ab12345.east-us-2.azure · RAW.SAP',
  credential_info: { public_key_fingerprint: 'SHA256:abc123' },
  last_test_at: '2026-11-20T10:00:02Z',
  last_test_ok: true,
  test_outdated: false,
  updated_at: '2026-11-20T10:00:02Z',
  version: 2,
  config: {
    account: 'ab12345.east-us-2.azure',
    user: 'SX_LOADER',
    role: 'SX_ROLE',
    warehouse: 'SX_WH',
    database: 'RAW',
    schema_name: 'SAP',
    login_timeout_seconds: 30,
  },
  status_reason: null,
  last_test_result: destinationOkReport,
  dependents: [],
  created_at: '2026-11-20T09:00:00Z',
  created_by: 'admin',
  updated_by: 'admin',
};

export const csvDestination: S['DestinationDetail'] = {
  ...snowflakeDestination,
  id: '00000000-0000-7000-8000-0000000000d2',
  code: 'CSV_INTERCAMBIO',
  name: 'Carpeta de intercambio',
  type: 'csv',
  environment: 'DEV',
  status: 'draft',
  target: '/data/csv/fi',
  credential_info: {},
  last_test_at: null,
  last_test_ok: null,
  last_test_result: null,
  config: { root: '/data/csv', path: 'fi', delimiter: ';', header: true, compression: 'none' },
};

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
  http.get('*/api/v1/destination-types', () => HttpResponse.json(destinationTypes)),
  http.get('*/api/v1/destination-types/:type/schema', ({ params }) => {
    const info = destinationTypes.find((t) => t.type === params.type);
    return HttpResponse.json({
      ...info,
      config_schema: params.type === 'csv' ? csvConfigSchema : snowflakeConfigSchema,
      secret_schema: params.type === 'csv' ? null : snowflakeSecretSchema,
    });
  }),
  http.get('*/api/v1/destinations', () =>
    HttpResponse.json({
      items: [snowflakeDestination, csvDestination],
      total: 2,
      page: 1,
      page_size: 25,
    }),
  ),
  http.get('*/api/v1/destinations/:id', ({ params }) =>
    HttpResponse.json(params.id === csvDestination.id ? csvDestination : snowflakeDestination),
  ),
];

export function problem(status: number, code: string, detail: string) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, detail, code, errors: [] },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
