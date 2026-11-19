import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type Role = components['schemas']['RoleResponse'];
export type Permission = components['schemas']['PermissionResponse'];
export type RoleCreateRequest = components['schemas']['RoleCreateRequest'];
export type RoleUpdateRequest = components['schemas']['RoleUpdateRequest'];

export const roleKeys = {
  all: ['roles'] as const,
  list: ['roles', 'list'] as const,
  detail: (id: string) => ['roles', 'detail', id] as const,
  permissions: ['roles', 'permissions'] as const,
};

export function useRoles() {
  return useQuery({
    queryKey: roleKeys.list,
    queryFn: async () => unwrap(await api.GET('/api/v1/roles')),
  });
}

export function useRole(id: string | undefined) {
  return useQuery({
    queryKey: roleKeys.detail(id ?? ''),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/roles/{role_id}', { params: { path: { role_id: id ?? '' } } })),
    enabled: id !== undefined,
  });
}

export function usePermissions() {
  return useQuery({
    queryKey: roleKeys.permissions,
    queryFn: async () => unwrap(await api.GET('/api/v1/permissions')),
    staleTime: 10 * 60_000,
  });
}

function useInvalidateRoles() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: roleKeys.all });
}

export function useCreateRole() {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async (body: RoleCreateRequest) =>
      unwrap(await api.POST('/api/v1/roles', { body })),
    onSuccess: invalidate,
  });
}

export function useUpdateRole(id: string) {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async (body: RoleUpdateRequest) =>
      unwrap(
        await api.PATCH('/api/v1/roles/{role_id}', { params: { path: { role_id: id } }, body }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteRole(id: string) {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async () => {
      const result = await api.DELETE('/api/v1/roles/{role_id}', {
        params: { path: { role_id: id } },
      });
      if (!result.response.ok) unwrap(result);
    },
    onSuccess: invalidate,
  });
}

/** Agrupa el catálogo por módulo conservando el orden de llegada. */
export function groupByModule(
  permissions: Permission[],
): { module: string; name: string; items: Permission[] }[] {
  const groups = new Map<string, { module: string; name: string; items: Permission[] }>();
  for (const p of permissions) {
    const group = groups.get(p.module) ?? { module: p.module, name: p.module_name, items: [] };
    group.items.push(p);
    groups.set(p.module, group);
  }
  return [...groups.values()];
}
