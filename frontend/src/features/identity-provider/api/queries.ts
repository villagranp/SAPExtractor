import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];

export type EntraProvider = S['EntraProviderResponse'];
export type EntraUpdateRequest = S['EntraProviderUpdateRequest'];
export type EntraTestRequest = S['EntraTestRequest'];
export type EntraTestReport = S['EntraTestReportResponse'];
export type EntraTestStep = S['EntraTestStepResponse'];
export type EntraCredentialIn = S['EntraCredentialIn'];
export type CredentialType = S['CredentialType'];
export type GroupMappingIn = S['GroupMappingIn'];

export const entraKeys = { provider: ['identity-provider', 'entra'] as const };

export function useEntraProvider() {
  return useQuery({
    queryKey: entraKeys.provider,
    queryFn: async () => unwrap(await api.GET('/api/v1/identity-providers/entra')),
  });
}

/** Las mutaciones devuelven la configuración completa: se escribe en la caché tal cual. */
function useStore() {
  const queryClient = useQueryClient();
  return (provider: EntraProvider) => {
    queryClient.setQueryData(entraKeys.provider, provider);
  };
}

export function useSaveEntra() {
  const store = useStore();
  return useMutation({
    mutationFn: async (body: EntraUpdateRequest) =>
      unwrap(await api.PUT('/api/v1/identity-providers/entra', { body })),
    onSuccess: store,
  });
}

export function useTestEntra() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: EntraTestRequest) =>
      unwrap(await api.POST('/api/v1/identity-providers/entra/test', { body })),
    // La prueba queda guardada en la configuración (decide si se puede activar).
    onSuccess: () => queryClient.invalidateQueries({ queryKey: entraKeys.provider }),
  });
}

export function useSetEntraEnabled() {
  const store = useStore();
  return useMutation({
    mutationFn: async ({
      enabled,
      version,
      reason,
    }: {
      enabled: boolean;
      version: number;
      reason?: string;
    }) =>
      unwrap(
        enabled
          ? await api.POST('/api/v1/identity-providers/entra/enable', { body: { version } })
          : await api.POST('/api/v1/identity-providers/entra/disable', {
              body: { version, reason: reason ?? null },
            }),
      ),
    onSuccess: store,
  });
}

export function useSaveGroupMappings() {
  const store = useStore();
  return useMutation({
    mutationFn: async (body: { version: number; mappings: GroupMappingIn[] }) =>
      unwrap(await api.PUT('/api/v1/identity-providers/entra/group-mappings', { body })),
    onSuccess: store,
  });
}
