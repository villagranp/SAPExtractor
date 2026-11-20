import type { SchemaColumn } from '../api/queries';

/** Tipo EDM con longitud o precisión, como lo muestra SAP: `Edm.Decimal(17,2)`. */
export function columnType(c: SchemaColumn): string {
  if (c.max_length !== null) return `${c.edm_type}(${String(c.max_length)})`;
  if (c.precision !== null) {
    return `${c.edm_type}(${String(c.precision)}${c.scale !== null ? `,${String(c.scale)}` : ''})`;
  }
  return c.display_format ? `${c.edm_type} · ${c.display_format}` : c.edm_type;
}

/** Valor de una celda (vista previa o diff) como texto; vacío como raya. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }
  return '—';
}
