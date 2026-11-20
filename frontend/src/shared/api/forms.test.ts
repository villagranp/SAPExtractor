import { zodResolver } from '@hookform/resolvers/zod';
import { act, renderHook } from '@testing-library/react';
import { useForm } from 'react-hook-form';
import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { ApiError } from './client';
import { applyServerErrors } from './forms';

const schema = z.object({
  code: z.string().regex(/^[A-Z][A-Z0-9_]{2,29}$/, 'Código no válido'),
  credentials: z.object({ password: z.string().min(1, 'Obligatorio') }),
});
type Form = z.infer<typeof schema>;

function problem(errors: { field: string; message: string }[], detail = 'Revise los campos.') {
  return new ApiError(422, {
    type: 'about:blank',
    title: 'Error',
    status: 422,
    detail,
    code: 'SX-GEN-VALIDATION',
    errors,
  });
}

describe('applyServerErrors', () => {
  it('asigna los errores de la API a sus campos, también anidados', () => {
    const { result } = renderHook(() => useForm<Form>({ resolver: zodResolver(schema) }));
    let general: string | null = 'x';
    act(() => {
      general = applyServerErrors(
        problem([
          { field: 'code', message: 'Ya existe' },
          { field: 'credentials.password.basic', message: 'Demasiado corta' },
        ]),
        result.current.setError,
        ['code', 'credentials.password'],
      );
    });
    expect(general).toBeNull();
    expect(result.current.getFieldState('code').error?.message).toBe('Ya existe');
    expect(result.current.getFieldState('credentials.password').error?.message).toBe(
      'Demasiado corta',
    );
  });

  it('devuelve el mensaje general si un error no corresponde a ningún campo', () => {
    const { result } = renderHook(() => useForm<Form>());
    let general: string | null = null;
    act(() => {
      general = applyServerErrors(
        problem([{ field: 'environment', message: 'No válido' }], 'Datos no válidos'),
        result.current.setError,
        ['code'],
      );
    });
    expect(general).toBe('Datos no válidos');
  });

  it('valida con Zod en el navegador antes de llamar a la API', async () => {
    const { result } = renderHook(() =>
      useForm<Form>({
        resolver: zodResolver(schema),
        defaultValues: { code: 'x', credentials: { password: '' } },
      }),
    );
    let valid = true;
    await act(async () => {
      valid = await result.current.trigger();
    });
    expect(valid).toBe(false);
    expect(result.current.getFieldState('code').error?.message).toBe('Código no válido');
  });
});
