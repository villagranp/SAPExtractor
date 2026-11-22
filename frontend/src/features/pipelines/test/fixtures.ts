import { http, HttpResponse } from 'msw';
import { glSource, sourceHandlers, systemRef } from '@/features/sources/test/fixtures';
import type { components } from '@/shared/api/schema';
import { csvDestination, snowflakeDestination } from '@/test/handlers';

type S = components['schemas'];

const finishedRun: S['RunBrief'] = {
  id: '00000000-0000-7000-8000-00000000r001',
  mode: 'DELTA',
  status: 'succeeded',
  trigger: 'schedule',
  created_at: '2026-11-20T06:00:00Z',
  started_at: '2026-11-20T06:00:02Z',
  finished_at: '2026-11-20T06:03:12Z',
  error_code: null,
};

export const activePipeline: S['PipelineDetail'] = {
  id: '00000000-0000-7000-8000-00000000p001',
  code: 'GL_A_SNOWFLAKE',
  name: 'Partidas de mayor hacia Snowflake',
  status: 'active',
  extraction_mode: 'DELTA',
  load_mode: 'upsert',
  source: {
    id: glSource.id,
    code: glSource.code,
    name: glSource.name,
    delta_method: 'ODQ',
    delta_semantics: 'after_image',
  },
  sap_system: systemRef,
  destination: {
    id: snowflakeDestination.id,
    code: snowflakeDestination.code,
    name: snowflakeDestination.name,
    type: 'snowflake',
    environment: 'QAS',
  },
  cross_environment: true,
  priority: 3,
  worker_pool: 'default',
  concurrency_group: null,
  tags: ['fi'],
  next_fire_at: '2026-11-23T08:30:00Z',
  upstream: null,
  downstream: [],
  live_run: null,
  last_run: finishedRun,
  updated_at: '2026-11-20T10:00:00Z',
  description: null,
  load: {
    load_mode: 'upsert',
    target_name: 'S4D_100_FI_GL_14',
    create_if_missing: true,
    schema_evolution: 'add_columns',
    delete_handling: 'soft',
    technical_columns: true,
    column_mapping: { GJAHR: null, DMBTR: 'IMPORTE_ML' },
  },
  filters: null,
  timeout_minutes: 120,
  retry_policy: { max_attempts: 3, backoff_minutes: [1, 5, 15], jitter: true },
  log_level: 'INFO',
  status_reason: null,
  cross_environment_reason: 'Carga de validación acordada con finanzas',
  cloned_from_id: null,
  owner_id: null,
  activated_at: '2026-11-20T09:30:00Z',
  created_at: '2026-11-20T09:00:00Z',
  created_by: 'root',
  updated_by: 'root',
  version: 3,
  last_test_at: '2026-11-19T15:00:00Z',
  last_test_ok: true,
  test_outdated: false,
  findings: [
    {
      severity: 'warning',
      code: 'SX-PIPE-CROSS-ENVIRONMENT',
      field: 'destination_id',
      message: 'El sistema SAP es DEV y el destino es QAS.',
    },
  ],
};

export const draftPipeline: S['PipelineDetail'] = {
  ...activePipeline,
  id: '00000000-0000-7000-8000-00000000p002',
  code: 'GL_A_CSV',
  name: 'Partidas de mayor hacia CSV',
  status: 'draft',
  extraction_mode: 'FULL',
  load_mode: 'replace',
  destination: {
    id: csvDestination.id,
    code: csvDestination.code,
    name: csvDestination.name,
    type: 'csv',
    environment: 'DEV',
  },
  cross_environment: false,
  cross_environment_reason: null,
  next_fire_at: null,
  upstream: null,
  downstream: [],
  last_run: null,
  activated_at: null,
  load: { ...activePipeline.load, load_mode: 'replace', column_mapping: {} },
  last_test_at: null,
  last_test_ok: null,
  findings: [],
};

export const weekdaySchedule: S['ScheduleResponse'] = {
  id: '00000000-0000-7000-8000-00000000s001',
  type: 'cron',
  cron_expression: '*/30 * * * 1-5',
  interval_minutes: null,
  timezone: 'America/Guatemala',
  start_at: null,
  end_at: null,
  blackout_windows: [
    {
      kind: 'weekly',
      label: 'Copia de seguridad',
      days: [0, 1, 2, 3, 4],
      start: '22:00',
      end: '02:00',
    },
  ],
  misfire_policy: 'skip',
  enabled: true,
  next_fire_at: '2026-11-23T08:30:00Z',
  upstream: null,
  last_fire_at: '2026-11-20T23:30:00Z',
  last_skip_at: null,
  last_skip_reason: null,
};

/** `draftPipeline` encadenado detrás de `activePipeline`. */
export const dependencySchedule: S['ScheduleResponse'] = {
  ...weekdaySchedule,
  id: '00000000-0000-7000-8000-00000000s002',
  type: 'dependency',
  cron_expression: null,
  blackout_windows: [],
  next_fire_at: null,
  last_fire_at: '2026-11-21T09:08:00Z',
  upstream: {
    id: activePipeline.id,
    code: activePipeline.code,
    name: activePipeline.name,
    status: 'active',
  },
};

export const previewFires = [
  '2026-11-23T12:00:00Z',
  '2026-11-23T12:30:00Z',
  '2026-11-23T13:00:00Z',
];

export const okActivation: S['ActivationReportResponse'] = {
  ok: true,
  checks: [
    { code: 'configuration', ok: true, message: 'La configuración no tiene bloqueos.' },
    { code: 'source_active', ok: true, message: 'El origen está activo.' },
    {
      code: 'sap_system_test',
      ok: true,
      message: 'El sistema SAP está activo y su prueba de conexión sigue vigente.',
    },
    {
      code: 'destination_test',
      ok: true,
      message: 'El destino está activo y su prueba sigue vigente.',
    },
    {
      code: 'test_run',
      ok: true,
      message: 'La prueba del pipeline fue correcta y sigue vigente.',
    },
    { code: 'service_published', ok: true, message: 'El servicio responde en este sistema.' },
    { code: 'schema_current', ok: true, message: 'El esquema guardado coincide con el de SAP.' },
  ],
  findings: [],
};

export const okTrial: S['PipelineTestResponse'] = {
  ok: true,
  tested_at: '2026-11-21T10:00:00Z',
  rows_read: 100,
  rows_loaded: 100,
  target: 'SXTEST_0a1b2c3d_MAT',
  duration_ms: 840,
  steps: [
    {
      step: 'configuration',
      status: 'succeeded',
      message: 'El pipeline, su origen y su destino se pueden usar.',
      duration_ms: 12,
      error_code: null,
    },
    {
      step: 'read',
      status: 'succeeded',
      message: 'SAP devolvió 100 filas en 320 ms. No se abrió ninguna suscripción de delta.',
      duration_ms: 350,
      error_code: null,
    },
    {
      step: 'load',
      status: 'succeeded',
      message: '100 filas en el destino de prueba SXTEST_0a1b2c3d_MAT de CSV_INTERCAMBIO.',
      duration_ms: 400,
      error_code: null,
    },
    {
      step: 'cleanup',
      status: 'succeeded',
      message: 'El destino de prueba SXTEST_0a1b2c3d_MAT se borró.',
      duration_ms: 20,
      error_code: null,
    },
  ],
};

export const failedTrial: S['PipelineTestResponse'] = {
  ...okTrial,
  ok: false,
  rows_read: 0,
  rows_loaded: 0,
  target: null,
  steps: [
    {
      step: 'configuration',
      status: 'succeeded',
      message: 'El pipeline, su origen y su destino se pueden usar.',
      duration_ms: 12,
      error_code: null,
    },
    {
      step: 'read',
      status: 'failed',
      message: 'SAP respondió 503: el servicio no está disponible.',
      duration_ms: 90,
      error_code: 'SX-SAP-503',
    },
    {
      step: 'load',
      status: 'skipped',
      message: 'No se ejecutó: un paso anterior falló.',
      duration_ms: null,
      error_code: null,
    },
    {
      step: 'cleanup',
      status: 'skipped',
      message: 'No se ejecutó: un paso anterior falló.',
      duration_ms: null,
      error_code: null,
    },
  ],
};

export const failedRun: S['RunDetail'] = {
  id: '00000000-0000-7000-8000-00000000r002',
  pipeline: { id: activePipeline.id, code: activePipeline.code, name: activePipeline.name },
  mode: 'DELTA',
  status: 'failed',
  trigger: 'manual',
  triggered_by: 'root',
  reason: 'Carga pedida por finanzas',
  priority: 1,
  created_at: '2026-11-21T09:00:00Z',
  available_at: '2026-11-21T09:00:00Z',
  started_at: '2026-11-21T09:00:03Z',
  finished_at: '2026-11-21T09:07:40Z',
  cancel_requested_at: null,
  error_code: 'SX-SAP-503',
  error_kind: 'transient',
  error_message: 'Gateway no disponible (503)',
  attempts: [
    {
      number: 1,
      worker_id: '00000000-0000-7000-8000-00000000w001',
      started_at: '2026-11-21T09:00:03Z',
      finished_at: '2026-11-21T09:00:40Z',
      outcome: 'failed_transient',
      has_spool: false,
      error_code: 'SX-SAP-503',
      error_kind: 'transient',
      error_message: 'Gateway no disponible (503)',
    },
    {
      number: 2,
      worker_id: '00000000-0000-7000-8000-00000000w001',
      started_at: '2026-11-21T09:06:40Z',
      finished_at: '2026-11-21T09:07:40Z',
      outcome: 'failed',
      has_spool: false,
      error_code: 'SX-SAP-503',
      error_kind: 'transient',
      error_message: 'Gateway no disponible (503)',
    },
  ],
  attempts_budget: 2,
  claimed_by: null,
  heartbeat_at: null,
  cancel_requested_by: null,
  batch_id: null,
  delta_generation: null,
  start_token: null,
  window_start: null,
  window_end: null,
};

export const queuedRun: S['RunDetail'] = {
  ...failedRun,
  id: '00000000-0000-7000-8000-00000000r003',
  status: 'queued',
  created_at: '2026-11-21T10:00:00Z',
  available_at: '2026-11-21T10:00:00Z',
  started_at: null,
  finished_at: null,
  error_code: null,
  error_kind: null,
  error_message: null,
  attempts: [],
  attempts_budget: 3,
};

export const idleWorker: S['WorkerResponse'] = {
  id: '00000000-0000-7000-8000-00000000w001',
  hostname: 'sx-worker-1',
  pid: 41,
  pools: ['default'],
  status: 'online',
  has_executor: false,
  current_run_id: null,
  started_at: '2026-11-21T08:00:00Z',
  heartbeat_at: '2026-11-21T10:00:05Z',
};

const page = <T>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 25 });

/** Handlers de pipelines y ejecuciones; incluyen los de orígenes (el asistente los lee). */
export const pipelineHandlers = [
  ...sourceHandlers,
  http.get('*/api/v1/pipelines', () => HttpResponse.json(page([activePipeline, draftPipeline]))),
  http.get('*/api/v1/pipelines/:id', ({ params }) =>
    HttpResponse.json(params.id === draftPipeline.id ? draftPipeline : activePipeline),
  ),
  http.get('*/api/v1/pipelines/:id/schedules', ({ params }) =>
    HttpResponse.json(params.id === draftPipeline.id ? [] : [weekdaySchedule]),
  ),
  http.post('*/api/v1/pipelines/review', () => HttpResponse.json([])),
  http.post('*/api/v1/schedules/preview', () =>
    HttpResponse.json({ timezone: 'America/Guatemala', fires: previewFires }),
  ),
  http.get('*/api/v1/concurrency-groups', () => HttpResponse.json([])),
  http.get('*/api/v1/workers', () => HttpResponse.json([idleWorker])),
  http.get('*/api/v1/runs', () => HttpResponse.json(page([queuedRun, failedRun]))),
  http.get('*/api/v1/runs/:id', ({ params }) =>
    HttpResponse.json(params.id === queuedRun.id ? queuedRun : failedRun),
  ),
];
