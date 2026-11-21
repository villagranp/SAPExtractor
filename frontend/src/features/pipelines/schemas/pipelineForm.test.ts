import { describe, expect, it } from 'vitest';
import { glColumns, glSource } from '@/features/sources/test/fixtures';
import { activePipeline, draftPipeline } from '../test/fixtures';
import {
  EMPTY_FORM,
  columnMapping,
  columnRows,
  extractionModesFor,
  formFieldOf,
  fromPipeline,
  suggestCode,
  suggestLoadMode,
  toCreateRequest,
  toUpdateRequest,
  type PipelineForm,
} from './pipelineForm';

const form = (changes: Partial<PipelineForm>): PipelineForm => ({
  ...EMPTY_FORM,
  source_id: glSource.id,
  destination_id: activePipeline.destination.id,
  code: 'gl_a_snowflake',
  name: ' Partidas de mayor ',
  columns: columnRows(glColumns),
  ...changes,
});

describe('modos según el origen y el destino', () => {
  it('ofrece el modo incremental que el origen admite, y siempre la carga completa', () => {
    expect(extractionModesFor({ delta_method: 'ODQ', delta_semantics: 'after_image' })).toEqual([
      'DELTA',
      'FULL',
    ]);
    expect(extractionModesFor({ delta_method: 'WATERMARK', delta_semantics: null })).toEqual([
      'WATERMARK',
      'FULL',
    ]);
    expect(extractionModesFor({ delta_method: 'NONE', delta_semantics: null })).toEqual(['FULL']);
  });

  it('propone el modo de carga dentro de lo que admite el destino', () => {
    const afterImage = { delta_method: 'ODQ', delta_semantics: 'after_image' } as const;
    const additive = { delta_method: 'ODQ', delta_semantics: 'additive' } as const;
    const all = ['append', 'replace', 'upsert'] as const;
    expect(suggestLoadMode('DELTA', afterImage, all)).toBe('upsert');
    expect(suggestLoadMode('DELTA', additive, all)).toBe('append');
    expect(suggestLoadMode('FULL', afterImage, all)).toBe('replace');
    // Un CSV no fusiona: el delta se añade.
    expect(suggestLoadMode('DELTA', afterImage, ['append', 'replace'])).toBe('append');
  });

  it('propone un código válido a partir del origen y del destino', () => {
    expect(suggestCode('FI_GL_14', 'SNOW_FIN_QAS')).toBe('FI_GL_14_TO_SNOW_FIN_QAS');
    expect(suggestCode('0FI_GL_14', 'x')).toBe('P_0FI_GL_14_TO_X');
    expect(suggestCode('A'.repeat(30), 'B'.repeat(30))).toHaveLength(40);
  });
});

describe('mapeo de columnas', () => {
  it('solo envía lo que cambia: null excluye y un texto renombra', () => {
    const rows = columnRows(glColumns, { GJAHR: null, DMBTR: 'IMPORTE_ML' });
    expect(rows.find((r) => r.name === 'GJAHR')).toMatchObject({ include: false, target: '' });
    expect(rows.find((r) => r.name === 'DMBTR')).toMatchObject({
      include: true,
      target: 'IMPORTE_ML',
    });
    expect(columnMapping(rows)).toEqual({ GJAHR: null, DMBTR: 'IMPORTE_ML' });
    expect(columnMapping(columnRows(glColumns))).toEqual({});
  });
});

describe('peticiones a la API', () => {
  it('crea con el código en mayúsculas y los filtros del origen por defecto', () => {
    const body = toCreateRequest(
      form({ extraction_mode: 'DELTA', load_mode: 'upsert', tags: 'fi, cierre' }),
      glColumns,
    );
    expect(body).toMatchObject({
      code: 'GL_A_SNOWFLAKE',
      name: 'Partidas de mayor',
      extraction_mode: 'DELTA',
      filters: null,
      priority: 3,
      timeout_minutes: 120,
      retry_policy: { max_attempts: 3, backoff_minutes: [1, 5, 15], jitter: true },
      concurrency_group: null,
      worker_pool: null,
      tags: ['fi', 'cierre'],
    });
    expect(body.load).toMatchObject({
      load_mode: 'upsert',
      target_name: null,
      delete_handling: null,
      column_mapping: {},
    });
  });

  it('envía los filtros propios con el valor del tipo de la columna', () => {
    const body = toCreateRequest(
      form({
        own_filters: true,
        filters: [
          { field: 'GJAHR', op: 'ge', value: '2026' },
          { field: 'RBUKRS', op: 'eq', value: ' 1000 ' },
        ],
      }),
      glColumns,
    );
    expect(body.filters).toEqual([
      { field: 'GJAHR', op: 'ge', value: 2026 },
      { field: 'RBUKRS', op: 'eq', value: '1000' },
    ]);
  });

  it('en un pipeline activo no envía origen, destino ni modo de extracción', () => {
    const active = toUpdateRequest(
      fromPipeline(activePipeline, glSource),
      glColumns,
      activePipeline,
    );
    expect(active).not.toHaveProperty('source_id');
    expect(active).not.toHaveProperty('extraction_mode');
    expect(active).toMatchObject({ version: 3, use_source_filters: true });
    expect(active.load?.column_mapping).toEqual({ GJAHR: null, DMBTR: 'IMPORTE_ML' });

    const draft = toUpdateRequest(fromPipeline(draftPipeline, glSource), glColumns, draftPipeline);
    expect(draft).toMatchObject({
      source_id: draftPipeline.source.id,
      destination_id: draftPipeline.destination.id,
      extraction_mode: 'FULL',
    });
  });

  it('lleva cada error de la API a su campo del formulario', () => {
    expect(formFieldOf('load.target_name')).toBe('target_name');
    expect(formFieldOf('load.column_mapping')).toBe('columns');
    expect(formFieldOf('filters[2].value')).toBe('filters.2.value');
    expect(formFieldOf('retry_policy.max_attempts')).toBe('max_attempts');
    expect(formFieldOf('code')).toBe('code');
  });
});
