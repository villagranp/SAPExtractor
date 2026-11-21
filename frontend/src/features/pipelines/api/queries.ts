import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type PipelineSummary = S['PipelineSummary'];
export type PipelineDetail = S['PipelineDetail'];
export type PipelineStatus = S['PipelineStatus'];
export type ExtractionMode = S['ExtractionMode'];
export type LoadMode = S['LoadMode'];
export type RunMode = S['RunMode'];
export type SchemaEvolution = S['SchemaEvolution'];
export type DeleteHandling = S['DeleteHandling'];
export type LogLevel = S['LogLevel'];
export type Finding = S['FindingResponse'];
export type ActivationReport = S['ActivationReportResponse'];
export type PipelineTest = S['PipelineTestResponse'];
export type CloneResult = S['CloneResponse'];
export type CloneSchemaDiff = S['CloneSchemaDiff'];
export type PipelineCreateRequest = S['PipelineCreateRequest'];
export type PipelineUpdateRequest = S['PipelineUpdateRequest'];
export type PipelineReviewRequest = S['PipelineReviewRequest'];
export type PipelineCloneRequest = S['PipelineCloneRequest'];
export type ScheduleBody = S['ScheduleBody'];
export type ScheduleResponse = S['ScheduleResponse'];
export type SchedulePreviewRequest = S['SchedulePreviewRequest'];
export type BlackoutWindowBody = S['BlackoutWindowBody'];
export type ConcurrencyGroup = S['ConcurrencyGroupResponse'];
export type SapEnvironment = S['SapEnvironment'];

export const PIPELINE_STATUSES: PipelineStatus[] = ['active', 'paused', 'draft'];
export const ENVIRONMENTS: SapEnvironment[] = ['DEV', 'QAS', 'PRD'];
export const SCHEMA_EVOLUTIONS: SchemaEvolution[] = ['add_columns', 'ignore', 'fail'];
export const DELETE_HANDLINGS: DeleteHandling[] = ['soft', 'hard', 'ignore'];
export const LOG_LEVELS: LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];

export interface PipelineFilters {
  q?: string;
  status?: PipelineStatus;
  environment?: SapEnvironment;
  source?: string;
  destination?: string;
  page: number;
  page_size: number;
}

export const pipelineKeys = {
  all: ['pipelines'] as const,
  list: (filters: PipelineFilters) => ['pipelines', 'list', filters] as const,
  detail: (id: string) => ['pipelines', 'detail', id] as const,
  schedules: (id: string) => ['pipelines', 'detail', id, 'schedules'] as const,
  review: (body: PipelineReviewRequest) => ['pipelines', 'review', body] as const,
  preview: (body: SchedulePreviewRequest | null) => ['schedule-preview', body] as const,
  groups: ['concurrency-groups'] as const,
};

/** Con una ejecución viva la lista se refresca sola: el estado cambia sin que nadie pulse nada. */
const LIVE_REFRESH_MS = 5_000;

export function usePipelines(filters: PipelineFilters) {
  return useQuery({
    queryKey: pipelineKeys.list(filters),
    queryFn: async () => unwrap(await api.GET('/api/v1/pipelines', { params: { query: filters } })),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.items.some((p) => p.live_run !== null) ? LIVE_REFRESH_MS : false,
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
    refetchInterval: (query) => (query.state.data?.live_run ? LIVE_REFRESH_MS : false),
  });
}

/** Avisos y bloqueos de una combinación aún sin guardar (validación en vivo del asistente). */
export function usePipelineReview(body: PipelineReviewRequest | null) {
  return useQuery({
    queryKey: pipelineKeys.review(
      body ?? { source_id: '', destination_id: '', extraction_mode: 'FULL', load_mode: 'append' },
    ),
    queryFn: async () =>
      unwrap(
        await api.POST('/api/v1/pipelines/review', {
          body: body as PipelineReviewRequest,
        }),
      ),
    enabled: body !== null,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
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

export function useUpdatePipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async (body: PipelineUpdateRequest) =>
      unwrap(
        await api.PATCH('/api/v1/pipelines/{pipeline_id}', {
          params: { path: { pipeline_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

/** Comprobación previa a activar: no cambia nada, solo informa. */
export function useCheckPipeline(id: string) {
  return useMutation({
    mutationFn: async (crossEnvironmentReason: string) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/validate', {
          params: { path: { pipeline_id: id } },
          body: { cross_environment_reason: crossEnvironmentReason || null },
        }),
      ),
  });
}

/** «Probar pipeline»: lee una muestra de SAP y la carga en un destino de prueba (plan 08 §6). */
export function useTestPipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/test-run', {
          params: { path: { pipeline_id: id } },
        }),
      ),
    // También si falla: el pipeline guarda el resultado de la última prueba.
    onSettled: invalidate,
  });
}

export function useActivatePipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async (crossEnvironmentReason: string) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/activate', {
          params: { path: { pipeline_id: id } },
          body: { cross_environment_reason: crossEnvironmentReason || null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export type StatusAction = 'pause' | 'resume' | 'delete';

export function usePipelineStatus(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async ({ action, reason }: { action: StatusAction; reason?: string }) => {
      const path = { pipeline_id: id };
      switch (action) {
        case 'pause':
          return unwrap(
            await api.POST('/api/v1/pipelines/{pipeline_id}/pause', {
              params: { path },
              body: { reason: reason || null },
            }),
          );
        case 'resume':
          return unwrap(
            await api.POST('/api/v1/pipelines/{pipeline_id}/resume', { params: { path } }),
          );
        case 'delete': {
          const result = await api.DELETE('/api/v1/pipelines/{pipeline_id}', {
            params: { path, query: reason ? { reason } : {} },
          });
          if (!result.response.ok) unwrap(result);
          return null;
        }
      }
    },
    onSuccess: invalidate,
  });
}

export function useClonePipeline(id: string) {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async (body: PipelineCloneRequest) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/clone', {
          params: { path: { pipeline_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useRunPipeline(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { mode?: RunMode; reason?: string }) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/run', {
          params: { path: { pipeline_id: id } },
          body: { mode: body.mode ?? null, reason: body.reason || null },
        }),
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: pipelineKeys.all }),
        queryClient.invalidateQueries({ queryKey: ['runs'] }),
      ]),
  });
}

export function useSchedules(id: string) {
  return useQuery({
    queryKey: pipelineKeys.schedules(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/pipelines/{pipeline_id}/schedules', {
          params: { path: { pipeline_id: id } },
        }),
      ),
    enabled: Boolean(id),
  });
}

/** Sustituye todas las programaciones del pipeline (como máximo cinco). */
export function usePutSchedules() {
  const invalidate = useInvalidatePipelines();
  return useMutation({
    mutationFn: async ({ id, schedules }: { id: string; schedules: ScheduleBody[] }) =>
      unwrap(
        await api.PUT('/api/v1/pipelines/{pipeline_id}/schedules', {
          params: { path: { pipeline_id: id } },
          body: { schedules },
        }),
      ),
    onSuccess: invalidate,
  });
}

/** Próximas ejecuciones de una programación aún sin guardar. */
export function useSchedulePreview(body: SchedulePreviewRequest | null) {
  return useQuery({
    queryKey: pipelineKeys.preview(body),
    queryFn: async () =>
      unwrap(await api.POST('/api/v1/schedules/preview', { body: body as SchedulePreviewRequest })),
    enabled: body !== null,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  });
}

export function useConcurrencyGroups() {
  return useQuery({
    queryKey: pipelineKeys.groups,
    queryFn: async () => unwrap(await api.GET('/api/v1/concurrency-groups')),
    staleTime: 60_000,
  });
}
