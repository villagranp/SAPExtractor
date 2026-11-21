import { useMutation, useQueryClient } from '@tanstack/react-query';
import { pipelineKeys } from '@/features/pipelines/api/queries';
import { api, unwrap } from '@/shared/api/client';

export { useConcurrencyGroups, type ConcurrencyGroup } from '@/features/pipelines/api/queries';

function useInvalidateGroups() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: pipelineKeys.groups });
}

/** Crea el grupo o cambia su límite y su descripción (la API hace las dos cosas con un PUT). */
export function useSaveGroup() {
  const invalidate = useInvalidateGroups();
  return useMutation({
    mutationFn: async (group: { code: string; max_parallel: number; description: string }) =>
      unwrap(
        await api.PUT('/api/v1/concurrency-groups/{code}', {
          params: { path: { code: group.code } },
          body: { max_parallel: group.max_parallel, description: group.description || null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteGroup() {
  const invalidate = useInvalidateGroups();
  return useMutation({
    mutationFn: async (code: string) => {
      const result = await api.DELETE('/api/v1/concurrency-groups/{code}', {
        params: { path: { code } },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: invalidate,
  });
}
