import { http, HttpResponse } from 'msw';
import type { components } from '@/shared/api/schema';
import { catalogPage, sapSystem } from '@/test/handlers';

type S = components['schemas'];

const column = (c: Partial<S['SchemaColumnResponse']> & { name: string }) => ({
  label: null,
  edm_type: 'Edm.String',
  arrow_type: 'string',
  nullable: true,
  is_key: false,
  max_length: null,
  precision: null,
  scale: null,
  display_format: null,
  filterable: true,
  ...c,
});

export const glColumns: S['SchemaColumnResponse'][] = [
  column({ name: 'RBUKRS', label: 'Sociedad', max_length: 4, is_key: true, nullable: false }),
  column({ name: 'BELNR', label: 'Documento', max_length: 10, is_key: true, nullable: false }),
  column({
    name: 'BUDAT',
    label: 'Fecha contabilización',
    edm_type: 'Edm.DateTime',
    arrow_type: 'date32[day]',
    display_format: 'Date',
  }),
  column({
    name: 'DMBTR',
    label: 'Importe ML',
    edm_type: 'Edm.Decimal',
    arrow_type: 'decimal128(17, 2)',
    precision: 17,
    scale: 2,
  }),
  column({ name: 'GJAHR', label: 'Ejercicio', edm_type: 'Edm.Int32', arrow_type: 'int32' }),
];

export const systemRef: S['SourceSystemRef'] = {
  id: sapSystem.id,
  code: sapSystem.code,
  name: sapSystem.name,
  environment: sapSystem.environment,
  status: sapSystem.status,
};

export const glInspection: S['InspectionResponse'] = {
  sap_system: systemRef,
  service_name: 'ZFI_WS_GL_ITEMS_SRV',
  service_version: 1,
  service_title: 'ZFI_WS_GL_ITEMS_SRV',
  service_description: 'ODP: partidas de mayor (0FI_GL_14)',
  entity_set: 'EntityOf0FI_GL_14',
  entity_set_label: null,
  odp_role: 'entity',
  odp_name: '0FI_GL_14',
  odata_version: '2.0',
  suggested_type: 'ODP_EXTRACTOR',
  supports_delta: true,
  keys: ['RBUKRS', 'BELNR'],
  columns: glColumns,
  watermark_candidates: ['BUDAT', 'DMBTR'],
  semantics_hint: { delta_process: 'AIMD', semantics: 'after_image' },
  delta_processes: {
    ABR: 'before_after',
    AIE: 'after_image',
    AIMD: 'after_image',
    ADD: 'additive',
  },
};

export const glEntitySets: S['ServiceEntitySetsResponse'] = {
  service: catalogPage.items[0] as S['CatalogServiceResponse'],
  odata_version: '2.0',
  entity_sets: [
    {
      name: 'EntityOf0FI_GL_14',
      entity_type: 'ZFI_WS_GL_ITEMS_SRV.EntityOf0FI_GL_14Type',
      label: null,
      odp_role: 'entity',
      odp_name: '0FI_GL_14',
      supports_delta: true,
      change_tracking: true,
      pageable: true,
      countable: true,
      keys: ['RBUKRS', 'BELNR'],
      properties: [],
    },
    {
      name: 'DeltaLinksOfEntityOf0FI_GL_14',
      entity_type: 'ZFI_WS_GL_ITEMS_SRV.DeltaLinksOfEntityOf0FI_GL_14Type',
      label: null,
      odp_role: 'delta_links',
      odp_name: null,
      supports_delta: false,
      change_tracking: false,
      pageable: true,
      countable: true,
      keys: ['DeltaToken'],
      properties: [],
    },
  ],
};

export const glSource: S['SourceDetail'] = {
  id: '00000000-0000-7000-8000-0000000000aa',
  code: 'FI_GL_14',
  name: 'Partidas de mayor',
  source_type: 'ODP_EXTRACTOR',
  status: 'draft',
  sap_system: systemRef,
  odp_name: '0FI_GL_14',
  service_name: 'ZFI_WS_GL_ITEMS_SRV',
  entity_set: 'EntityOf0FI_GL_14',
  delta_method: 'ODQ',
  delta_semantics: 'after_image',
  schema_version: 2,
  tags: ['fi'],
  updated_at: '2026-11-20T10:00:00Z',
  description: null,
  odp_context: 'SAPI',
  service_version: 1,
  service_path: '/sap/opu/odata/sap/ZFI_WS_GL_ITEMS_SRV',
  odata_version: '2.0',
  supports_delta: true,
  watermark_field: null,
  watermark_kind: null,
  watermark_timezone: null,
  watermark_overlap_seconds: null,
  key_fields: ['RBUKRS', 'BELNR'],
  default_filters: [{ field: 'RBUKRS', op: 'eq', value: '1000' }],
  filter_expression: "RBUKRS eq '1000'",
  page_size: 5000,
  preview_enabled: true,
  status_reason: null,
  schema_checked_at: '2026-11-20T10:00:00Z',
  schema: {
    version: 2,
    hash: 'a'.repeat(64),
    detected_at: '2026-11-20T10:00:00Z',
    detected_by: 'root',
    column_count: glColumns.length,
  },
  columns: glColumns,
  created_at: '2026-11-20T09:00:00Z',
  created_by: 'root',
  updated_by: 'root',
  version: 1,
};

export const sourceHandlers = [
  http.get('*/api/v1/sources', () =>
    HttpResponse.json({ items: [glSource], total: 1, page: 1, page_size: 25 }),
  ),
  http.get('*/api/v1/sources/:id', () => HttpResponse.json(glSource)),
  http.get('*/api/v1/sources/:id/schema/versions', () =>
    HttpResponse.json([
      { ...glSource.schema, version: 2 },
      { ...glSource.schema, version: 1, column_count: 4 },
    ]),
  ),
  http.get('*/api/v1/sources/:id/schema/diff', () =>
    HttpResponse.json({
      from_version: 1,
      to_version: 2,
      breaking: false,
      reordered: false,
      added: [{ name: 'GJAHR', edm_type: 'Edm.Int32', is_key: false, label: 'Ejercicio' }],
      removed: [],
      changed: [],
    }),
  ),
  http.get('*/api/v1/sap-systems/:id/services/:service/entity-sets', () =>
    HttpResponse.json(glEntitySets),
  ),
  http.post('*/api/v1/sources/inspect', () => HttpResponse.json(glInspection)),
];
