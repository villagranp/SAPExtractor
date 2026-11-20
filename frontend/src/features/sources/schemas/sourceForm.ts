import { z } from 'zod';
import type {
  DeltaConfigBody,
  DeltaMethod,
  FilterConditionBody,
  Inspection,
  SchemaColumn,
  SourceCreateRequest,
  SourceDetail,
  SourceType,
  SourceUpdateRequest,
} from '../api/queries';

/**
 * Formulario del asistente de origen (plan 05 §2). Los mensajes son claves de
 * `sources.errors.*`: la validación de formato se hace aquí y las reglas de negocio
 * (tipo de delta admitido, campo filtrable, valor del tipo correcto) las aplica la API.
 */
export const filterRowSchema = z.object({
  field: z.string().min(1, 'required'),
  op: z.enum(['eq', 'ne', 'gt', 'ge', 'lt', 'le']),
  value: z.string().trim().min(1, 'required').max(200, 'tooLong'),
});

export const sourceFormSchema = z
  .object({
    sap_system_id: z.string().min(1, 'required'),
    source_type: z.enum(['ODP_EXTRACTOR', 'CDS_ODP', 'CDS_ODATA']),
    service_name: z.string().min(1, 'required'),
    service_version: z.number().int().min(1),
    entity_set: z.string().min(1, 'required'),
    key_fields: z.array(z.string()).min(1, 'keys'),
    delta_method: z.enum(['ODQ', 'WATERMARK', 'NONE']),
    delta_semantics: z.enum(['after_image', 'additive', 'before_after', 'unknown']),
    delta_process: z.string().max(10),
    watermark_field: z.string(),
    watermark_kind: z.enum(['datetime', 'date', 'decimal_timestamp']),
    watermark_timezone: z.string(),
    watermark_overlap_seconds: z
      .string()
      .regex(/^\d+$/, 'overlap')
      .refine((v) => Number(v) <= 86_400, 'overlap'),
    filters: z.array(filterRowSchema).max(20, 'tooMany'),
    code: z
      .string()
      .trim()
      .regex(/^[A-Za-z][A-Za-z0-9_]{2,39}$/, 'code'),
    name: z.string().trim().min(1, 'required').max(200, 'tooLong'),
    description: z.string().max(2000, 'tooLong'),
    tags: z.string(),
    page_size: z
      .string()
      .regex(/^\d+$/, 'pageSize')
      .refine((v) => Number(v) >= 100 && Number(v) <= 50_000, 'pageSize'),
    preview_enabled: z.boolean(),
  })
  .superRefine((form, ctx) => {
    if (form.delta_method !== 'WATERMARK') return;
    if (!form.watermark_field) {
      ctx.addIssue({ code: 'custom', path: ['watermark_field'], message: 'required' });
    }
    if (!form.watermark_timezone.trim()) {
      ctx.addIssue({ code: 'custom', path: ['watermark_timezone'], message: 'required' });
    }
  });

export type SourceForm = z.infer<typeof sourceFormSchema>;
export type FilterRow = z.infer<typeof filterRowSchema>;

export const EMPTY_FORM: SourceForm = {
  sap_system_id: '',
  source_type: 'ODP_EXTRACTOR',
  service_name: '',
  service_version: 1,
  entity_set: '',
  key_fields: [],
  delta_method: 'NONE',
  delta_semantics: 'unknown',
  delta_process: '',
  watermark_field: '',
  watermark_kind: 'datetime',
  watermark_timezone: '',
  watermark_overlap_seconds: '300',
  filters: [],
  code: '',
  name: '',
  description: '',
  tags: '',
  page_size: '5000',
  preview_enabled: true,
};

/** Campos de cada paso, para validar solo lo visible al pulsar «Siguiente». */
export const STEP_FIELDS = {
  service: ['sap_system_id', 'source_type', 'service_name', 'entity_set'],
  schema: ['key_fields'],
  delta: [
    'delta_method',
    'delta_semantics',
    'watermark_field',
    'watermark_kind',
    'watermark_timezone',
    'watermark_overlap_seconds',
  ],
  filters: ['filters'],
  general: ['code', 'name', 'description', 'tags', 'page_size', 'preview_enabled'],
  summary: [],
} as const satisfies Record<string, readonly (keyof SourceForm)[]>;

export type StepId = keyof typeof STEP_FIELDS;

export const isOdp = (type: SourceType) => type !== 'CDS_ODATA';

/** Métodos de delta posibles para un tipo de origen (la API vuelve a comprobarlo). */
export function deltaMethodsFor(type: SourceType, supportsDelta: boolean): DeltaMethod[] {
  if (isOdp(type)) return supportsDelta ? ['ODQ', 'NONE'] : ['NONE'];
  return ['WATERMARK', 'NONE'];
}

/** Código propuesto a partir del ODP o del entity set: letras, números y guion bajo. */
export function suggestCode(base: string): string {
  const clean = base
    .toUpperCase()
    .replace(/[^A-Z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
  const prefixed = /^[A-Z]/.test(clean) ? clean : `S_${clean}`;
  return prefixed.slice(0, 40);
}

/** Valores iniciales tras leer el `$metadata` de un entity set nuevo. */
export function fromInspection(form: SourceForm, inspection: Inspection): SourceForm {
  const type = inspection.suggested_type;
  const odq = isOdp(type) && inspection.supports_delta;
  const hint = inspection.semantics_hint;
  return {
    ...form,
    source_type: type,
    key_fields: [...inspection.keys],
    delta_method: odq ? 'ODQ' : 'NONE',
    delta_semantics: hint?.semantics ?? 'unknown',
    delta_process: hint?.delta_process ?? '',
    watermark_field: '',
    filters: [],
    code: form.code || suggestCode(inspection.odp_name ?? inspection.entity_set),
    name:
      form.name ||
      inspection.entity_set_label ||
      inspection.service_title ||
      inspection.odp_name ||
      inspection.entity_set,
  };
}

/** Formulario de edición a partir de un origen guardado. */
export function fromSource(source: SourceDetail): SourceForm {
  return {
    ...EMPTY_FORM,
    sap_system_id: source.sap_system.id,
    source_type: source.source_type,
    service_name: source.service_name,
    service_version: source.service_version,
    entity_set: source.entity_set,
    key_fields: [...source.key_fields],
    delta_method: source.delta_method,
    delta_semantics: source.delta_semantics ?? 'unknown',
    watermark_field: source.watermark_field ?? '',
    watermark_kind: source.watermark_kind ?? 'datetime',
    watermark_timezone: source.watermark_timezone ?? '',
    watermark_overlap_seconds: String(source.watermark_overlap_seconds ?? 300),
    filters: source.default_filters.map((f) => ({
      field: f.field,
      op: f.op,
      value: String(f.value),
    })),
    code: source.code,
    name: source.name,
    description: source.description ?? '',
    tags: source.tags.join(', '),
    page_size: String(source.page_size),
    preview_enabled: source.preview_enabled,
  };
}

const INTEGER_TYPES = new Set(['Edm.Byte', 'Edm.SByte', 'Edm.Int16', 'Edm.Int32', 'Edm.Int64']);

/** Valor tipado del filtro: booleano y enteros como tales; el resto como texto. */
export function filterValue(row: FilterRow, columns: SchemaColumn[]): FilterConditionBody['value'] {
  const column = columns.find((c) => c.name === row.field);
  if (column?.edm_type === 'Edm.Boolean') return row.value === 'true';
  if (column && INTEGER_TYPES.has(column.edm_type) && /^-?\d+$/.test(row.value)) {
    return Number(row.value);
  }
  return row.value.trim();
}

function tagList(tags: string): string[] {
  return tags
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

function deltaBody(form: SourceForm): DeltaConfigBody {
  if (form.delta_method === 'ODQ') return { method: 'ODQ', semantics: form.delta_semantics };
  if (form.delta_method === 'WATERMARK') {
    return {
      method: 'WATERMARK',
      watermark_field: form.watermark_field,
      watermark_kind: form.watermark_kind,
      watermark_timezone: form.watermark_timezone.trim(),
      watermark_overlap_seconds: Number(form.watermark_overlap_seconds),
    };
  }
  return { method: 'NONE' };
}

export function toCreateRequest(form: SourceForm, columns: SchemaColumn[]): SourceCreateRequest {
  return {
    sap_system_id: form.sap_system_id,
    service_name: form.service_name,
    service_version: form.service_version,
    entity_set: form.entity_set,
    source_type: form.source_type,
    code: form.code.trim().toUpperCase(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    tags: tagList(form.tags),
    key_fields: form.key_fields,
    delta: deltaBody(form),
    default_filters: form.filters.map((row) => ({
      field: row.field,
      op: row.op,
      value: filterValue(row, columns),
    })),
    page_size: Number(form.page_size),
    preview_enabled: form.preview_enabled,
  };
}

export function toUpdateRequest(
  form: SourceForm,
  columns: SchemaColumn[],
  version: number,
): SourceUpdateRequest {
  return {
    version,
    name: form.name.trim(),
    description: form.description.trim(),
    tags: tagList(form.tags),
    key_fields: form.key_fields,
    delta: deltaBody(form),
    default_filters: form.filters.map((row) => ({
      field: row.field,
      op: row.op,
      value: filterValue(row, columns),
    })),
    page_size: Number(form.page_size),
    preview_enabled: form.preview_enabled,
  };
}

/** Errores de la API (`default_filters[0]`, `delta_method`…) → campos del formulario. */
export function formFieldOf(apiField: string): string {
  const filter = /^default_filters\[(\d+)\]/.exec(apiField);
  if (filter) return `filters.${filter[1] ?? '0'}.value`;
  return apiField;
}
