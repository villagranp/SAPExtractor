import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type Me = components['schemas']['MeResponse'];
export type SessionStage = components['schemas']['SessionStage'];
export type SessionInfo = components['schemas']['SessionResponse'];
export type MfaEnrollment = components['schemas']['MfaEnrollmentResponse'];

export const authKeys = {
  me: ['auth', 'me'] as const,
  mySessions: ['auth', 'me', 'sessions'] as const,
};

/** Sesión actual; `null` si no hay sesión (401). */
export function useMe() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: async (): Promise<Me | null> => {
      const result = await api.GET('/api/v1/me');
      if (result.response.status === 401) return null;
      return unwrap(result);
    },
    staleTime: 60_000,
  });
}

/** Permite mostrar u ocultar opciones. La autorización real la hace siempre el backend. */
export function useCan(permission: string): boolean {
  const { data } = useMe();
  return data?.permissions.includes(permission) ?? false;
}

function useRefreshMe() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: authKeys.me });
}

/** Los 401 de estos pasos son «credenciales incorrectas», no «sesión caducada». */
const AUTH_STEP = ['auth-step'] as const;

export function useLogin() {
  const refresh = useRefreshMe();
  return useMutation({
    mutationKey: AUTH_STEP,
    mutationFn: async (body: { username: string; password: string }) =>
      unwrap(await api.POST('/api/v1/auth/login', { body })),
    onSuccess: refresh,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/v1/auth/logout');
    },
    onSettled: () => {
      queryClient.clear();
      queryClient.setQueryData(authKeys.me, null);
    },
  });
}

export function useVerifyMfa() {
  const refresh = useRefreshMe();
  return useMutation({
    mutationKey: AUTH_STEP,
    mutationFn: async (code: string) =>
      unwrap(await api.POST('/api/v1/auth/mfa/verify', { body: { code } })),
    // También tras un fallo: demasiados intentos bloquean la cuenta y cierran la sesión.
    onSettled: refresh,
  });
}

export function useChangePassword() {
  const refresh = useRefreshMe();
  return useMutation({
    mutationFn: async (body: { current_password?: string | null; new_password: string }) =>
      unwrap(await api.POST('/api/v1/auth/password/change', { body })),
    onSuccess: refresh,
  });
}

export function useStartMfaSetup() {
  return useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/auth/mfa/setup')),
  });
}

export function useEnableMfa() {
  const refresh = useRefreshMe();
  return useMutation({
    mutationFn: async (code: string) =>
      unwrap(await api.POST('/api/v1/auth/mfa/enable', { body: { code } })),
    onSuccess: refresh,
  });
}

export function useMySessions() {
  return useQuery({
    queryKey: authKeys.mySessions,
    queryFn: async () => unwrap(await api.GET('/api/v1/me/sessions')),
  });
}

export function useRevokeMySession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (sessionId: string) =>
      unwrap(
        await api.DELETE('/api/v1/me/sessions/{session_id}', {
          params: { path: { session_id: sessionId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: authKeys.mySessions }),
  });
}

/** Solo rutas internas: evita redirecciones abiertas con ?next=https://... o //dominio. */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/login')) {
    return '/';
  }
  return value;
}
