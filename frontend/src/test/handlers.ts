import { http, HttpResponse } from 'msw';
import type { components } from '@/shared/api/schema';

type HealthReport = components['schemas']['HealthReport'];
type SystemInfo = components['schemas']['SystemInfo'];

export const systemInfo: SystemInfo = {
  name: 'SAPExtract',
  version: '0.1.0',
  environment: 'dev',
  build_commit: 'abc1234',
};

export const healthyReport: HealthReport = {
  status: 'ok',
  checks: { database: { status: 'ok', detail: null }, migrations: { status: 'ok', detail: null } },
};

/** Handlers por defecto de MSW (API sana). Cada prueba puede sobrescribirlos con server.use(). */
export const handlers = [
  http.get('*/api/v1/system/info', () => HttpResponse.json(systemInfo)),
  http.get('*/api/v1/health/ready', () => HttpResponse.json(healthyReport)),
];
