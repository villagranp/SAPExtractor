import { http, HttpResponse } from 'msw';
import type { components } from '@/shared/api/schema';
import { roles } from '@/test/handlers';

type S = components['schemas'];

const viewer = roles[1] as S['RoleResponse'];

export const emptyProvider: S['EntraProviderResponse'] = {
  configured: false,
  redirect_uri: 'https://sx.empresa.local/api/v1/auth/entra/callback',
  warnings: [],
  version: null,
  enabled: false,
  tenant_id: null,
  client_id: null,
  credential: null,
  jit_provisioning: false,
  default_role: null,
  allowed_domains: [],
  require_group_membership: true,
  group_mappings: [],
  last_test: null,
  test_current: false,
  updated_at: null,
  updated_by: null,
};

export const okEntraReport: S['EntraTestReportResponse'] = {
  ok: true,
  summary: 'Entra ID responde y acepta la credencial.',
  error_code: null,
  started_at: '2026-11-20T10:00:00Z',
  finished_at: '2026-11-20T10:00:01Z',
  steps: [
    {
      name: 'discovery',
      status: 'ok',
      message: 'El inquilino publica su configuración OpenID.',
      duration_ms: 80,
      error_code: null,
      details: { issuer: 'https://login.microsoftonline.com/t/v2.0' },
    },
    {
      name: 'keys',
      status: 'ok',
      message: '3 claves de firma disponibles para validar los tokens.',
      duration_ms: 40,
      error_code: null,
      details: {},
    },
    {
      name: 'credential',
      status: 'ok',
      message: 'Entra acepta el client ID y la credencial.',
      duration_ms: 120,
      error_code: null,
      details: {},
    },
  ],
};

export const badSecretReport: S['EntraTestReportResponse'] = {
  ...okEntraReport,
  ok: false,
  summary: 'El secreto de cliente no es válido. Copie el valor del secreto, no su ID.',
  error_code: 'AADSTS7000215',
  steps: [
    okEntraReport.steps[0] as S['EntraTestStepResponse'],
    okEntraReport.steps[1] as S['EntraTestStepResponse'],
    {
      name: 'credential',
      status: 'failed',
      message: 'El secreto de cliente no es válido. Copie el valor del secreto, no su ID.',
      duration_ms: 90,
      error_code: 'AADSTS7000215',
      details: { correlation_id: 'corr-123' },
    },
  ],
};

export const configuredProvider: S['EntraProviderResponse'] = {
  ...emptyProvider,
  configured: true,
  version: 3,
  tenant_id: '11111111-2222-3333-4444-555555555555',
  client_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
  credential: {
    credential_type: 'client_secret',
    expires_at: '2027-11-01T23:59:59Z',
    updated_at: '2026-11-20T09:00:00Z',
    certificate_subject: null,
    certificate_thumbprint: null,
  },
  default_role: { id: viewer.id, code: viewer.code, name: viewer.name },
  allowed_domains: ['empresa.com'],
  group_mappings: [
    {
      id: '00000000-0000-7000-8000-0000000000f1',
      group_object_id: '0a0a0a0a-1111-2222-3333-444444444444',
      group_name: 'SG-SAPExtract-Lectura',
      role: { id: viewer.id, code: viewer.code, name: viewer.name },
    },
  ],
  last_test: okEntraReport,
  test_current: true,
  updated_at: '2026-11-20T09:00:00Z',
  updated_by: 'root',
};

export const entraHandlers = (provider: S['EntraProviderResponse']) => [
  http.get('*/api/v1/identity-providers/entra', () => HttpResponse.json(provider)),
  http.get('*/api/v1/roles', () => HttpResponse.json(roles)),
];
