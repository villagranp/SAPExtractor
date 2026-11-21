import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type PipelineSummary = S['PipelineSummary'];
export type PipelineDetail = S['PipelineDetail'];
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
  });
}

export function useCreatePipeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: PipelineCreateRequest) =>
      unwrap(await api.POST('/api/v1/pipelines', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: pipelineKeys.all }),
  });
}

export function useDeletePipeline(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (reason: string) => {
      const result = await api.DELETE('/api/v1/pipelines/{pipeline_id}', {
        params: { path: { pipeline_id: id }, query: reason ? { reason } : {} },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: pipelineKeys.all }),
  });
}
