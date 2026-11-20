import type { FieldValues, Path, UseFormSetError } from 'react-hook-form';
import { describeError } from './errors';

/**
 * Vuelca los errores por campo de una respuesta Problem Details (`errors[]`) en un formulario
 * de React Hook Form. Devuelve el mensaje general cuando ningún error corresponde a un campo
 * conocido (para mostrarlo en un `Alert`), o `null` si todos se asignaron a campos.
 *
 * Los nombres de la API pueden venir anidados (`credentials.basic.password`): se asigna al
 * campo más largo del formulario que sea prefijo del nombre.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly Path<T>[],
): string | null {
  const described = describeError(error);
  const byLength = [...fields].sort((a, b) => b.length - a.length);
  let assigned = 0;
  for (const [apiField, messages] of Object.entries(described.fields)) {
    const target = byLength.find((f) => apiField === f || apiField.startsWith(`${f}.`));
    const message = messages[0];
    if (target && message) {
      setError(target, { type: 'server', message });
      assigned += 1;
    }
  }
  return assigned > 0 && assigned === Object.keys(described.fields).length
    ? null
    : described.message;
}
