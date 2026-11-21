import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type PipelineSummary = S['PipelineSummary'];
export type PipelineDetail = S['PipelineDetail'];
export type PipelineStatus = S['PipelineStatus'];
export type ExtractionMode = S['ExtractionMode'];
export type PipelineCreateRequest = S['PipelineCreateRequest'];

export const EXTRACTION_MODES: ExtractionMode[] = ['DELTA', 'WATERMARK', 'FULL'];

export interface PipelineFilters {
  q?: string;
  extraction_mode?: ExtractionMode;
  page: number;
  page_size: number;
}

export const pipelineKeys = {
  all: ['pipelines'] as const,
  list: (filters: PipelineFilters) => ['pipelines', 'list', filters] as const,
  detail: (id: string) => ['pipelines', 'detail', id] as const,
};

export function usePipelines(filters: PipelineFilters) {
  return useQuery({
    queryKey: pipelineKeys.list(filters),
    queryFn: async () => unwrap(await api.GET('/api/v1/pipelines', { params: { query: filters } })),
    placeholderData: keepPreviousData,
  });
}

export function usePipeline(id: string) {
  return useQuery({
    queryKey: pipelineKeys.detail(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/pipelines/{pipeline_id}', {
          params: { path: { pipeline_id: id } },
        }),
      ),
    enabled: Boolean(id),
    staleTime: 0, // su último run lo cambia el worker, no esta pantalla
  });
}

function useInvalidatePipelines() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: pipelineKeys.all });
}

export function useCreatePipeline() {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async (body: PipelineCreateRequest) =>
      unwrap(await api.POST('/api/v1/pipelines', { body })),
    onSuccess: invalidate,
  });
}

/** Activa el pipeline tras la comprobación previa (plan 08 §5). */
export function useActivatePipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/activate', {
          params: { path: { pipeline_id: id } },
          body: {},
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeletePipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async (reason: string) => {
      const result = await api.DELETE('/api/v1/pipelines/{pipeline_id}', {
        params: { path: { pipeline_id: id }, query: reason ? { reason } : {} },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: invalidate,
  });
}
