import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type ApiToken = components['schemas']['ApiTokenResponse'];
export type ApiTokenCreateRequest = components['schemas']['ApiTokenCreateRequest'];

export const tokenKeys = { all: ['api-tokens'] as const };

export function useApiTokens() {
  return useQuery({
    queryKey: tokenKeys.all,
    queryFn: async () => unwrap(await api.GET('/api/v1/api-tokens')),
  });
}

export function useCreateApiToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ApiTokenCreateRequest) =>
      unwrap(await api.POST('/api/v1/api-tokens', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tokenKeys.all }),
  });
}

export function useRevokeApiToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.DELETE('/api/v1/api-tokens/{token_id}', { params: { path: { token_id: id } } }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tokenKeys.all }),
  });
}
