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
  'pipeline:read',
  'pipeline:manage',
  'pipeline:run',
  'pipeline:pause',
  'pipeline:reset_delta',
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
  max_concurrent_runs: 2,
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

// ---------------------------------------------------------------- motor y orquestación
export const glSource: S['SourceSummary'] = {
  id: '01930000-0000-7000-8000-0000000000a1',
  code: 'FI_GL_ITEMS',
  name: 'Partidas de mayor',
  source_type: 'ODP_EXTRACTOR',
  status: 'active',
  sap_system: {
    id: sapSystem.id,
    code: sapSystem.code,
    name: sapSystem.name,
    environment: 'QAS',
    status: 'active',
  },
  odp_name: '0FI_GL_14',
  service_name: 'ZFI_WS_GL_ITEMS_SRV',
  entity_set: 'EntityOf0FI_GL_14',
  delta_method: 'ODQ',
  delta_semantics: 'additive',
  schema_version: 1,
  tags: [],
  updated_at: '2026-11-20T09:00:00Z',
};

export const materialSource: S['SourceSummary'] = {
  ...glSource,
  id: '01930000-0000-7000-8000-0000000000a2',
  code: 'MM_MATERIAL',
  name: 'Materiales',
  source_type: 'CDS_ODATA',
  odp_name: null,
  service_name: 'ZMM_CV_MATERIAL_CDS',
  entity_set: 'ZMM_CV_MATERIAL',
  delta_method: 'WATERMARK',
  delta_semantics: null,
};

export const glPipeline: S['PipelineDetail'] = {
  id: '01930000-0000-7000-8000-0000000000b1',
  code: 'FI_GL_CSV',
  name: 'Mayor hacia CSV',
  status: 'active',
  extraction_mode: 'DELTA',
  load_mode: 'append',
  source: {
    id: glSource.id,
    code: glSource.code,
    name: glSource.name,
    delta_method: 'ODQ',
    delta_semantics: 'additive',
  },
  sap_system: glSource.sap_system,
  destination: {
    id: csvDestination.id,
    code: csvDestination.code,
    name: csvDestination.name,
    type: 'csv',
    environment: 'QAS',
  },
  cross_environment: false,
  priority: 3,
  worker_pool: 'default',
  concurrency_group: null,
  tags: [],
  next_fire_at: null,
  live_run: null,
  last_run: null,
  updated_at: '2026-11-20T09:30:00Z',
  description: null,
  load: {
    load_mode: 'append',
    target_name: 'GL_ITEMS',
    create_if_missing: true,
    schema_evolution: 'add_columns',
    delete_handling: 'ignore',
    technical_columns: true,
    column_mapping: {},
  },
  filters: null,
  timeout_minutes: 120,
  retry_policy: { max_attempts: 3, backoff_minutes: [1, 5, 15], jitter: true },
  log_level: 'INFO',
  status_reason: null,
  cross_environment_reason: null,
  cloned_from_id: null,
  owner_id: null,
  activated_at: '2026-11-20T09:31:00Z',
  created_at: '2026-11-20T09:30:00Z',
  created_by: 'root',
  updated_by: 'root',
  version: 2,
  findings: [],
};

const PIPELINE_SUMMARY_KEYS = [
  'id',
  'code',
  'name',
  'status',
  'extraction_mode',
  'load_mode',
  'source',
  'sap_system',
  'destination',
  'cross_environment',
  'priority',
  'worker_pool',
  'concurrency_group',
  'tags',
  'next_fire_at',
  'live_run',
  'last_run',
  'updated_at',
] as const satisfies readonly (keyof S['PipelineSummary'])[];

export const glPipelineSummary = Object.fromEntries(
  PIPELINE_SUMMARY_KEYS.map((k) => [k, glPipeline[k]]),
) as S['PipelineSummary'];

export const deltaRun: S['RunDetail'] = {
  id: '01930000-0000-7000-8000-0000000000c1',
  pipeline: { id: glPipeline.id, code: glPipeline.code, name: glPipeline.name },
  mode: 'DELTA',
  status: 'succeeded',
  trigger: 'manual',
  triggered_by: 'root',
  reason: null,
  priority: 1,
  created_at: '2026-11-20T10:00:00Z',
  available_at: '2026-11-20T10:00:00Z',
  started_at: '2026-11-20T10:00:01Z',
  finished_at: '2026-11-20T10:00:09Z',
  cancel_requested_at: null,
  error_code: null,
  error_kind: null,
  error_message: null,
  rows_extracted: 10,
  attempts: [
    {
      number: 1,
      worker_id: '01930000-0000-7000-8000-0000000000d1',
      started_at: '2026-11-20T10:00:01Z',
      finished_at: '2026-11-20T10:00:09Z',
      outcome: 'succeeded',
      error_code: null,
      error_kind: null,
      error_message: null,
      has_spool: true,
    },
  ],
  attempts_budget: 3,
  claimed_by: null,
  heartbeat_at: null,
  cancel_requested_by: null,
  batch_id: 'd-0123456789abcdef01234567',
  delta_generation: 1,
  start_token: 'D000000000250',
  window_start: null,
  window_end: null,
  next_token: 'D000000000260',
  rows_inserted: 10,
  rows_updated: 0,
  rows_deleted: 0,
};

const RUN_SUMMARY_KEYS = [
  'id',
  'pipeline',
  'mode',
  'status',
  'trigger',
  'triggered_by',
  'reason',
  'priority',
  'created_at',
  'available_at',
  'started_at',
  'finished_at',
  'cancel_requested_at',
  'error_code',
  'error_kind',
  'error_message',
  'rows_extracted',
] as const satisfies readonly (keyof S['RunSummary'])[];

export const deltaRunSummary = Object.fromEntries(
  RUN_SUMMARY_KEYS.map((k) => [k, deltaRun[k]]),
) as S['RunSummary'];

export const runSteps: S['StepResponse'][] = (
  ['prepare', 'extract', 'load', 'commit_delta', 'cleanup'] as const
).map((step) => ({
  attempt: 1,
  step,
  status: 'succeeded' as const,
  started_at: '2026-11-20T10:00:02Z',
  finished_at: '2026-11-20T10:00:03Z',
  metrics: { duration_ms: 1200 },
}));

export const runLog: S['RunLogPage'] = {
  items: [
    {
      id: 1,
      ts: '2026-11-20T10:00:01Z',
      attempt: 1,
      step: null,
      level: 'INFO',
      message: 'Intento 1 en el worker 01930000.',
      data: {},
    },
    {
      id: 2,
      ts: '2026-11-20T10:00:05Z',
      attempt: 1,
      step: 'extract',
      level: 'INFO',
      message: 'Extracción terminada: 10 filas en 1 páginas.',
      data: {},
    },
    {
      id: 3,
      ts: '2026-11-20T10:00:09Z',
      attempt: 1,
      step: 'commit_delta',
      level: 'INFO',
      message: 'Token de delta confirmado.',
      data: {},
    },
  ],
  next_after: 3,
  finished: true,
};

export const initializedDelta: S['DeltaStateResponse'] = {
  status: 'INITIALIZED',
  generation: 1,
  current_token: 'D000000000260',
  pending_run_id: null,
  sap_close_pending: false,
  requires_reinit: false,
  watermark_value: null,
  initialized_at: '2026-11-20T09:45:00Z',
  last_delta_at: '2026-11-20T10:00:09Z',
  history: [
    {
      at: '2026-11-20T09:45:00Z',
      event: 'initialized',
      from_status: 'INITIALIZING',
      to_status: 'INITIALIZED',
      generation: 1,
      run_id: null,
      actor: 'worker',
      reason: null,
    },
  ],
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
  http.get('*/api/v1/sources', () =>
    HttpResponse.json({ items: [glSource, materialSource], total: 2, page: 1, page_size: 200 }),
  ),
  http.get('*/api/v1/pipelines', () =>
    HttpResponse.json({ items: [glPipelineSummary], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/pipelines/:id', () => HttpResponse.json(glPipeline)),
  http.get('*/api/v1/pipelines/:id/delta', () => HttpResponse.json(initializedDelta)),
  http.get('*/api/v1/runs', () =>
    HttpResponse.json({ items: [deltaRunSummary], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/runs/:id', () => HttpResponse.json(deltaRun)),
  http.get('*/api/v1/runs/:id/steps', () => HttpResponse.json(runSteps)),
  http.get('*/api/v1/runs/:id/logs', () => HttpResponse.json(runLog)),
];

export function problem(status: number, code: string, detail: string) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, detail, code, errors: [] },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
