import { describe, expect, it } from 'vitest';
import { glColumns, glInspection, glSource } from '../test/fixtures';
import {
  EMPTY_FORM,
  deltaMethodsFor,
  formFieldOf,
  fromInspection,
  fromSource,
  sourceFormSchema,
  suggestCode,
  toCreateRequest,
  toUpdateRequest,
} from './sourceForm';

describe('sourceForm', () => {
  it('propone un código válido a partir del ODP', () => {
    expect(suggestCode('0FI_GL_14')).toBe('S_0FI_GL_14');
    expect(suggestCode('2LIS_11_VAITM')).toBe('S_2LIS_11_VAITM');
    expect(suggestCode('ZFI_CV-GL account')).toBe('ZFI_CV_GL_ACCOUNT');
    expect(suggestCode('x'.repeat(60))).toHaveLength(40);
  });

  it('solo ofrece los métodos de delta que admite el tipo', () => {
    expect(deltaMethodsFor('ODP_EXTRACTOR', true)).toEqual(['ODQ', 'NONE']);
    expect(deltaMethodsFor('CDS_ODP', false)).toEqual(['NONE']);
    expect(deltaMethodsFor('CDS_ODATA', false)).toEqual(['WATERMARK', 'NONE']);
  });

  it('toma claves, delta y nombre del $metadata', () => {
    const form = fromInspection({ ...EMPTY_FORM, sap_system_id: 's' }, glInspection);
    expect(form.key_fields).toEqual(['RBUKRS', 'BELNR']);
    expect(form.delta_method).toBe('ODQ');
    expect(form.delta_semantics).toBe('after_image');
    expect(form.delta_process).toBe('AIMD');
    expect(form.code).toBe('S_0FI_GL_14');
    expect(form.name).toBe('ZFI_WS_GL_ITEMS_SRV');
  });

  it('envía los filtros con el tipo del campo', () => {
    const form = {
      ...fromSource(glSource),
      filters: [
        { field: 'RBUKRS', op: 'eq' as const, value: '1000' },
        { field: 'GJAHR', op: 'ge' as const, value: '2025' },
        { field: 'DMBTR', op: 'gt' as const, value: '10.50' },
      ],
    };
    const body = toCreateRequest(form, glColumns);
    expect(body.default_filters).toEqual([
      { field: 'RBUKRS', op: 'eq', value: '1000' },
      { field: 'GJAHR', op: 'ge', value: 2025 },
      { field: 'DMBTR', op: 'gt', value: '10.50' },
    ]);
    expect(body.delta).toEqual({ method: 'ODQ', semantics: 'after_image' });
    expect(body.tags).toEqual(['fi']);
    const patch = toUpdateRequest(form, glColumns, 7);
    expect(patch.version).toBe(7);
    expect('code' in patch).toBe(false);
  });

  it('exige campo y zona horaria para la marca de agua', () => {
    const result = sourceFormSchema.safeParse({
      ...fromSource(glSource),
      source_type: 'CDS_ODATA',
      delta_method: 'WATERMARK',
    });
    expect(result.success).toBe(false);
    const paths = result.error?.issues.map((i) => i.path.join('.'));
    expect(paths).toEqual(expect.arrayContaining(['watermark_field', 'watermark_timezone']));
  });

  it('traduce los nombres de campo de la API', () => {
    expect(formFieldOf('default_filters[2]')).toBe('filters.2.value');
    expect(formFieldOf('key_fields')).toBe('key_fields');
  });
});
