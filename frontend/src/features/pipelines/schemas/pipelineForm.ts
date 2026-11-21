import { z } from 'zod';
import type { DestinationTypeInfo } from '@/features/destinations/api/queries';
import type { SchemaColumn, SourceDetail, SourceSummary } from '@/features/sources/api/queries';
import type {
  ExtractionMode,
  LoadMode,
  PipelineCreateRequest,
  PipelineDetail,
  PipelineUpdateRequest,
} from '../api/queries';

/**
 * Formulario del asistente de pipeline (plan 08 §6). Los mensajes son claves de
 * `pipelines.errors.*`. Aquí solo se valida el formato; las reglas de negocio (modo que el
 * destino no admite, delta sin soporte, nombres que chocan) las aplica la API y llegan como
 * avisos en vivo o como errores por campo.
 */
const filterRowSchema = z.object({
  field: z.string().min(1, 'required'),
  op: z.enum(['eq', 'ne', 'gt', 'ge', 'lt', 'le']),
  value: z.string().trim().min(1, 'required').max(200, 'tooLong'),
});

const columnRowSchema = z.object({
  name: z.string(),
  include: z.boolean(),
  target: z.string().trim().max(128, 'tooLong'),
});

const wholeNumber = (min: number, max: number, message: string) =>
  z
    .string()
    .regex(/^\d+$/, message)
    .refine((v) => Number(v) >= min && Number(v) <= max, message);

export const pipelineFormSchema = z.object({
  source_id: z.string().min(1, 'source'),
  destination_id: z.string().min(1, 'destination'),
  extraction_mode: z.enum(['FULL', 'DELTA', 'WATERMARK']),
  load_mode: z.enum(['append', 'upsert', 'replace']),
  target_name: z.string().trim().max(128, 'tooLong'),
  create_if_missing: z.boolean(),
  schema_evolution: z.enum(['add_columns', 'ignore', 'fail']),
  delete_handling: z.enum(['auto', 'soft', 'hard', 'ignore']),
  technical_columns: z.boolean(),
  columns: z.array(columnRowSchema),
  own_filters: z.boolean(),
  filters: z.array(filterRowSchema).max(20, 'tooMany'),
  code: z
    .string()
    .trim()
    .regex(/^[A-Za-z][A-Za-z0-9_]{2,39}$/, 'code'),
  name: z.string().trim().min(1, 'required').max(200, 'tooLong'),
  description: z.string().max(2000, 'tooLong'),
  tags: z.string(),
  priority: z.enum(['1', '2', '3', '4', '5']),
  timeout_minutes: wholeNumber(1, 2880, 'timeout'),
  max_attempts: wholeNumber(1, 10, 'attempts'),
  concurrency_group: z.string(),
  worker_pool: z
    .string()
    .trim()
    .regex(/^$|^[A-Za-z][A-Za-z0-9_-]{0,29}$/, 'pool'),
  log_level: z.enum(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
});

export type PipelineForm = z.infer<typeof pipelineFormSchema>;
export type ColumnRow = z.infer<typeof columnRowSchema>;

export const EMPTY_FORM: PipelineForm = {
  source_id: '',
  destination_id: '',
  extraction_mode: 'FULL',
  load_mode: 'append',
  target_name: '',
  create_if_missing: true,
  schema_evolution: 'add_columns',
  delete_handling: 'auto',
  technical_columns: true,
  columns: [],
  own_filters: false,
  filters: [],
  code: '',
  name: '',
  description: '',
  tags: '',
  priority: '3',
  timeout_minutes: '120',
  max_attempts: '3',
  concurrency_group: '',
  worker_pool: '',
  log_level: 'INFO',
};

/** Campos de cada paso, para validar solo lo visible al pulsar «Siguiente». */
export const STEP_FIELDS = {
  source: ['source_id'],
  destination: ['destination_id'],
  load: [
    'extraction_mode',
    'load_mode',
    'target_name',
    'schema_evolution',
    'delete_handling',
    'columns',
    'filters',
  ],
  schedule: [],
  general: [
    'code',
    'name',
    'description',
    'tags',
    'priority',
    'timeout_minutes',
    'max_attempts',
    'concurrency_group',
    'worker_pool',
    'log_level',
  ],
  summary: [],
} as const satisfies Record<string, readonly (keyof PipelineForm)[]>;

export type StepId = keyof typeof STEP_FIELDS;

type SourceDelta = Pick<SourceSummary, 'delta_method' | 'delta_semantics'>;

/** Modos de extracción que el origen admite; el incremental va primero porque es el habitual. */
export function extractionModesFor(source: SourceDelta | null | undefined): ExtractionMode[] {
  if (source?.delta_method === 'ODQ') return ['DELTA', 'FULL'];
  if (source?.delta_method === 'WATERMARK') return ['WATERMARK', 'FULL'];
  return ['FULL'];
}

/**
 * Modo de carga propuesto: una carga completa reemplaza; un delta con imágenes posteriores
 * fusiona por clave; el resto añade. Siempre dentro de lo que admite el destino.
 */
export function suggestLoadMode(
  extraction: ExtractionMode,
  source: SourceDelta | null | undefined,
  supported: readonly LoadMode[],
): LoadMode {
  const wanted: LoadMode[] =
    extraction === 'FULL'
      ? ['replace', 'append']
      : extraction === 'WATERMARK' || source?.delta_semantics === 'after_image'
        ? ['upsert', 'append']
        : ['append'];
  return wanted.find((mode) => supported.includes(mode)) ?? supported[0] ?? 'append';
}

export function supportedModes(
  types: DestinationTypeInfo[] | undefined,
  type: string | undefined,
): LoadMode[] {
  return types?.find((t) => t.type === type)?.supported_modes ?? [];
}

/** Código propuesto: «origen_destino», en mayúsculas y sin pasar de 40 caracteres. */
export function suggestCode(sourceCode: string, destinationCode: string): string {
  const clean = `${sourceCode}_TO_${destinationCode}`
    .toUpperCase()
    .replace(/[^A-Z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
  return (/^[A-Z]/.test(clean) ? clean : `P_${clean}`).slice(0, 40);
}

/** Filas del mapeo de columnas a partir del esquema del origen y del mapeo guardado. */
export function columnRows(
  columns: SchemaColumn[],
  mapping: Record<string, string | null> = {},
): ColumnRow[] {
  return columns.map((column) => ({
    name: column.name,
    include: mapping[column.name] !== null,
    target: mapping[column.name] ?? '',
  }));
}

/** Solo viaja lo que cambia el valor por defecto: `null` excluye y un texto renombra. */
export function columnMapping(rows: ColumnRow[]): Record<string, string | null> {
  const mapping: Record<string, string | null> = {};
  for (const row of rows) {
    if (!row.include) mapping[row.name] = null;
    else if (row.target.trim()) mapping[row.name] = row.target.trim();
  }
  return mapping;
}

const INTEGER_TYPES = new Set(['Edm.Byte', 'Edm.SByte', 'Edm.Int16', 'Edm.Int32', 'Edm.Int64']);

function filterBodies(form: PipelineForm, columns: SchemaColumn[]) {
  return form.filters.map((row) => {
    const column = columns.find((c) => c.name === row.field);
    let value: boolean | number | string = row.value.trim();
    if (column?.edm_type === 'Edm.Boolean') value = row.value === 'true';
    else if (column && INTEGER_TYPES.has(column.edm_type) && /^-?\d+$/.test(row.value)) {
      value = Number(row.value);
    }
    return { field: row.field, op: row.op, value };
  });
}

function tagList(tags: string): string[] {
  return tags
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

function loadBody(form: PipelineForm) {
  return {
    load_mode: form.load_mode,
    target_name: form.target_name.trim() || null,
    create_if_missing: form.create_if_missing,
    schema_evolution: form.schema_evolution,
    delete_handling: form.delete_handling === 'auto' ? null : form.delete_handling,
    technical_columns: form.technical_columns,
    column_mapping: columnMapping(form.columns),
  };
}

function retryBody(form: PipelineForm, previous?: PipelineDetail['retry_policy']) {
  return {
    max_attempts: Number(form.max_attempts),
    backoff_minutes: previous?.backoff_minutes ?? [1, 5, 15],
    jitter: previous?.jitter ?? true,
  };
}

export function toCreateRequest(
  form: PipelineForm,
  columns: SchemaColumn[],
): PipelineCreateRequest {
  return {
    code: form.code.trim().toUpperCase(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    source_id: form.source_id,
    destination_id: form.destination_id,
    extraction_mode: form.extraction_mode,
    load: loadBody(form),
    filters: form.own_filters ? filterBodies(form, columns) : null,
    priority: Number(form.priority),
    timeout_minutes: Number(form.timeout_minutes),
    retry_policy: retryBody(form),
    concurrency_group: form.concurrency_group || null,
    worker_pool: form.worker_pool.trim().toLowerCase() || null,
    log_level: form.log_level,
    tags: tagList(form.tags),
  };
}

/** En borrador también viajan origen, destino y modo: en un pipeline activo la API no los admite. */
export function toUpdateRequest(
  form: PipelineForm,
  columns: SchemaColumn[],
  existing: PipelineDetail,
): PipelineUpdateRequest {
  const draft = existing.status === 'draft';
  return {
    version: existing.version,
    name: form.name.trim(),
    description: form.description.trim(),
    ...(draft
      ? {
          source_id: form.source_id,
          destination_id: form.destination_id,
          extraction_mode: form.extraction_mode,
        }
      : {}),
    load: loadBody(form),
    ...(form.own_filters ? { filters: filterBodies(form, columns) } : {}),
    use_source_filters: !form.own_filters,
    priority: Number(form.priority),
    timeout_minutes: Number(form.timeout_minutes),
    retry_policy: retryBody(form, existing.retry_policy),
    concurrency_group: form.concurrency_group,
    worker_pool: form.worker_pool.trim().toLowerCase() || null,
    log_level: form.log_level,
    tags: tagList(form.tags),
  };
}

/** Formulario de edición a partir de un pipeline guardado y del esquema de su origen. */
export function fromPipeline(pipeline: PipelineDetail, source?: SourceDetail): PipelineForm {
  const stored = new Set(['soft', 'hard', 'ignore']);
  return {
    source_id: pipeline.source.id,
    destination_id: pipeline.destination.id,
    extraction_mode: pipeline.extraction_mode,
    load_mode: pipeline.load.load_mode,
    target_name: pipeline.load.target_name,
    create_if_missing: pipeline.load.create_if_missing,
    schema_evolution: pipeline.load.schema_evolution,
    delete_handling: stored.has(pipeline.load.delete_handling)
      ? pipeline.load.delete_handling
      : 'auto',
    technical_columns: pipeline.load.technical_columns,
    columns: columnRows(source?.columns ?? [], pipeline.load.column_mapping),
    own_filters: pipeline.filters !== null,
    filters: (pipeline.filters ?? []).map((f) => ({
      field: f.field,
      op: f.op,
      value: String(f.value),
    })),
    code: pipeline.code,
    name: pipeline.name,
    description: pipeline.description ?? '',
    tags: pipeline.tags.join(', '),
    priority: String(pipeline.priority) as PipelineForm['priority'],
    timeout_minutes: String(pipeline.timeout_minutes),
    max_attempts: String(pipeline.retry_policy.max_attempts),
    concurrency_group: pipeline.concurrency_group ?? '',
    worker_pool: pipeline.worker_pool,
    log_level: pipeline.log_level,
  };
}

/** Errores de la API (`load.target_name`, `filters[0]`, `retry_policy.max_attempts`…) → campo. */
export function formFieldOf(apiField: string): string {
  const filter = /^filters\[(\d+)\]/.exec(apiField);
  if (filter) return `filters.${filter[1] ?? '0'}.value`;
  if (apiField === 'load.column_mapping' || apiField === 'column_mapping') return 'columns';
  if (apiField === 'retry_policy.max_attempts' || apiField === 'retry_policy')
    return 'max_attempts';
  if (apiField === 'source' || apiField === 'source_id') return 'source_id';
  if (apiField === 'destination' || apiField === 'destination_id') return 'destination_id';
  return apiField.startsWith('load.') ? apiField.slice('load.'.length) : apiField;
}
