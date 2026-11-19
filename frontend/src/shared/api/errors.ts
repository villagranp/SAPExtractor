import { ApiError } from './client';

export interface DescribedError {
  /** Mensaje para el usuario (detail del Problem Details o genérico). */
  message: string;
  /** Código estable SX-... si la API lo devolvió. */
  code: string | null;
  /** Errores por campo: `{ new_password: ["...", "..."] }`. */
  fields: Record<string, string[]>;
}

const GENERIC = 'No se pudo completar la operación. Inténtelo de nuevo.';

/** Normaliza cualquier error de una mutación/consulta para mostrarlo en pantalla. */
export function describeError(error: unknown): DescribedError {
  if (error instanceof ApiError) {
    const fields: Record<string, string[]> = {};
    for (const issue of error.problem?.errors ?? []) {
      (fields[issue.field] ??= []).push(issue.message);
    }
    return {
      message: error.problem?.detail ?? error.problem?.title ?? GENERIC,
      code: error.problem?.code ?? null,
      fields,
    };
  }
  return { message: GENERIC, code: null, fields: {} };
}
