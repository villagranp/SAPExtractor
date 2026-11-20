/**
 * Utilidades del formulario generado a partir del JSON Schema de cada tipo de destino
 * (`/destination-types/{type}/schema`): valores iniciales, carga útil y validación mínima.
 */
export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  enum?: (string | number)[];
  default?: unknown;
  format?: string;
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  anyOf?: JsonSchemaProperty[];
}

export interface JsonObjectSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export type FormValues = Record<string, string | boolean>;

/** Une `anyOf: [X, null]` (campos opcionales de Pydantic) en el propio X. */
function unwrap(prop: JsonSchemaProperty): JsonSchemaProperty {
  const inner = prop.anyOf?.find((p) => p.type !== 'null');
  if (!inner) return prop;
  const merged: JsonSchemaProperty = { ...inner, ...prop };
  delete merged.anyOf;
  return merged;
}

export function properties(schema: JsonObjectSchema): [string, JsonSchemaProperty][] {
  return Object.entries(schema.properties ?? {}).map(([k, p]) => [k, unwrap(p)]);
}

/** Valores iniciales: los `default` del esquema o lo ya guardado. */
export function initialValues(
  schema: JsonObjectSchema,
  saved: Record<string, unknown> = {},
): FormValues {
  const values: FormValues = {};
  for (const [key, prop] of properties(schema)) {
    const current = saved[key] ?? prop.default;
    if (prop.type === 'boolean') values[key] = current === undefined ? false : Boolean(current);
    else if (current === undefined || current === null) values[key] = '';
    else values[key] = typeof current === 'string' ? current : JSON.stringify(current);
  }
  return values;
}

/** Valores del formulario → objeto para la API (números como número, vacíos fuera). */
export function toPayload(schema: JsonObjectSchema, values: FormValues): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of properties(schema)) {
    const value = values[key];
    if (prop.type === 'boolean') out[key] = Boolean(value);
    else if (typeof value === 'string' && value.trim() !== '') {
      out[key] = prop.type === 'integer' || prop.type === 'number' ? Number(value) : value;
    } else if (typeof value === 'string' && prop.enum?.includes(value)) out[key] = value;
  }
  return out;
}

/** Campos obligatorios vacíos o fuera de rango (la API valida el resto). */
export function missingFields(schema: JsonObjectSchema, values: FormValues): string[] {
  const required = new Set(schema.required ?? []);
  const issues: string[] = [];
  for (const [key, prop] of properties(schema)) {
    const value = values[key];
    if (prop.type === 'boolean') continue;
    const text = typeof value === 'string' ? value : '';
    if (required.has(key) && text.trim() === '' && !prop.enum?.includes(text)) {
      issues.push(key);
    } else if (prop.type === 'integer' && text.trim() !== '') {
      const n = Number(text);
      if (
        !Number.isInteger(n) ||
        (prop.minimum !== undefined && n < prop.minimum) ||
        (prop.maximum !== undefined && n > prop.maximum)
      ) {
        issues.push(key);
      }
    }
  }
  return issues;
}
