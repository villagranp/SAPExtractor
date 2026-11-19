import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type UserSummary = components['schemas']['UserSummary'];
export type UserDetail = components['schemas']['UserDetail'];
export type UserStatus = components['schemas']['UserStatus'];
export type AuthSource = components['schemas']['AuthSource'];
export type UserCreateRequest = components['schemas']['UserCreateRequest'];
export type SessionInfo = components['schemas']['SessionResponse'];

export interface UserFilters {
  q?: string;
  status?: UserStatus;
  auth_source?: AuthSource;
  role_id?: string;
  page: number;
  page_size: number;
}

export const userKeys = {
  all: ['users'] as const,
  list: (filters: UserFilters) => ['users', 'list', filters] as const,
  detail: (id: string) => ['users', 'detail', id] as const,
  sessions: (id: string) => ['users', 'detail', id, 'sessions'] as const,
};

export function useUsers(filters: UserFilters) {
  return useQuery({
    queryKey: userKeys.list(filters),
    queryFn: async () => unwrap(await api.GET('/api/v1/users', { params: { query: filters } })),
    placeholderData: keepPreviousData,
  });
}

export function useUser(id: string) {
  return useQuery({
    queryKey: userKeys.detail(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/users/{user_id}', { params: { path: { user_id: id } } })),
  });
}

export function useUserSessions(id: string, enabled: boolean) {
  return useQuery({
    queryKey: userKeys.sessions(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/users/{user_id}/sessions', { params: { path: { user_id: id } } }),
      ),
    enabled,
  });
}

function useInvalidateUsers() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: userKeys.all });
}

export function useCreateUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (body: UserCreateRequest) =>
      unwrap(await api.POST('/api/v1/users', { body })),
    onSuccess: invalidate,
  });
}

export function useUpdateUser(id: string) {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (body: components['schemas']['UserUpdateRequest']) =>
      unwrap(
        await api.PATCH('/api/v1/users/{user_id}', { params: { path: { user_id: id } }, body }),
      ),
    onSuccess: invalidate,
  });
}

export function useSetUserRoles(id: string) {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (roleIds: string[]) =>
      unwrap(
        await api.PUT('/api/v1/users/{user_id}/roles', {
          params: { path: { user_id: id } },
          body: { role_ids: roleIds },
        }),
      ),
    onSuccess: invalidate,
  });
}

export type UserAction =
  'disable' | 'enable' | 'lock' | 'unlock' | 'reset-mfa' | 'anonymize' | 'revoke-sessions';

/** Acciones del ciclo de vida que devuelven el usuario actualizado (o el nº de sesiones). */
export function useUserAction(id: string) {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async ({ action, reason }: { action: UserAction; reason?: string }) => {
      const path = { user_id: id };
      switch (action) {
        case 'disable':
          return unwrap(
            await api.POST('/api/v1/users/{user_id}/disable', {
              params: { path },
              body: { reason: reason || null },
            }),
          );
        case 'enable':
          return unwrap(await api.POST('/api/v1/users/{user_id}/enable', { params: { path } }));
        case 'lock':
          return unwrap(
            await api.POST('/api/v1/users/{user_id}/lock', {
              params: { path },
              body: { reason: reason || null },
            }),
          );
        case 'unlock':
          return unwrap(await api.POST('/api/v1/users/{user_id}/unlock', { params: { path } }));
        case 'reset-mfa':
          return unwrap(await api.POST('/api/v1/users/{user_id}/reset-mfa', { params: { path } }));
        case 'anonymize':
          return unwrap(
            await api.POST('/api/v1/users/{user_id}/anonymize', {
              params: { path },
              body: { reason: reason ?? '' },
            }),
          );
        case 'revoke-sessions':
          return unwrap(await api.DELETE('/api/v1/users/{user_id}/sessions', { params: { path } }));
      }
    },
    onSuccess: invalidate,
  });
}

export function useResetPassword(id: string) {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/users/{user_id}/reset-password', {
          params: { path: { user_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}
