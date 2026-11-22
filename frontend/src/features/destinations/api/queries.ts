import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type DestinationSummary = S['DestinationSummary'];
export type DestinationDetail = S['DestinationDetail'];
export type DestinationType = S['DestinationType'];
export type DestinationStatus = S['DestinationStatus'];
export type DestinationEnvironment = S['DestinationEnvironment'];
export type DestinationTypeInfo = S['DestinationTypeResponse'];
export type DestinationTypeSchema = S['DestinationTypeSchemaResponse'];
export type CheckReport = S['CheckReportResponse'];
export type CheckStep = S['CheckStepResponse'];
export type DestinationCreateRequest = S['DestinationCreateRequest'];
export type DestinationTestRequest = S['DestinationTestRequest'];
export type DestinationUpdateRequest = S['DestinationUpdateRequest'];
export type DdlPreviewRequest = S['DdlPreviewRequest'];
export type DdlPreview = S['DdlPreviewResponse'];
export type LoadMode = S['LoadMode'];

export const DESTINATION_TYPES: DestinationType[] = ['snowflake', 'sqlserver', 'csv'];
export const ENVIRONMENTS: DestinationEnvironment[] = ['DEV', 'QAS', 'PRD'];
export const LOAD_MODES: LoadMode[] = ['upsert', 'append', 'replace'];

export interface DestinationFilters {
  q?: string;
  type?: DestinationType;
  environment?: DestinationEnvironment;
  status?: DestinationStatus;
  page: number;
  page_size: number;
}

export const destinationKeys = {
  all: ['destinations'] as const,
  types: ['destination-types'] as const,
  typeSchema: (type: DestinationType) => ['destination-types', type, 'schema'] as const,
  list: (filters: DestinationFilters) => ['destinations', 'list', filters] as const,
  detail: (id: string) => ['destinations', 'detail', id] as const,
};

export function useDestinationTypes() {
  return useQuery({
    queryKey: destinationKeys.types,
    queryFn: async () => unwrap(await api.GET('/api/v1/destination-types')),
    staleTime: 10 * 60_000,
  });
}

export function useDestinationTypeSchema(type: DestinationType | null) {
  return useQuery({
    queryKey: destinationKeys.typeSchema(type ?? 'csv'),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/destination-types/{destination_type}/schema', {
          params: { path: { destination_type: type ?? 'csv' } },
        }),
      ),
    enabled: type !== null,
    staleTime: 10 * 60_000,
  });
}

export function useDestinations(filters: DestinationFilters) {
  return useQuery({
    queryKey: destinationKeys.list(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/destinations', { params: { query: filters } })),
    placeholderData: keepPreviousData,
  });
}

export function useDestination(id: string) {
  return useQuery({
    queryKey: destinationKeys.detail(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/destinations/{destination_id}', {
          params: { path: { destination_id: id } },
        }),
      ),
    enabled: Boolean(id),
  });
}

function useInvalidateDestinations() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: destinationKeys.all });
}

export function useCreateDestination() {
  const invalidate = useInvalidateDestinations();
  return useMutation({
    mutationFn: async (body: DestinationCreateRequest) =>
      unwrap(await api.POST('/api/v1/destinations', { body })),
    onSuccess: invalidate,
  });
}

/** Prueba del asistente sin guardar. */
export function useTestDraftDestination() {
  return useMutation({
    mutationFn: async (body: DestinationTestRequest) =>
      unwrap(await api.POST('/api/v1/destinations/test', { body })),
  });
}

export function useUpdateDestination(id: string) {
  const invalidate = useInvalidateDestinations();
  return useMutation({
    mutationFn: async (body: DestinationUpdateRequest) =>
      unwrap(
        await api.PATCH('/api/v1/destinations/{destination_id}', {
          params: { path: { destination_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useTestDestination(id: string) {
  const invalidate = useInvalidateDestinations();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/destinations/{destination_id}/test', {
          params: { path: { destination_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useRotateDestinationSecret(id: string) {
  const invalidate = useInvalidateDestinations();
  return useMutation({
    mutationFn: async (body: { secret: Record<string, unknown>; version?: number }) =>
      unwrap(
        await api.POST('/api/v1/destinations/{destination_id}/credentials/rotate', {
          params: { path: { destination_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export type StatusAction = 'activate' | 'deactivate' | 'delete';

export function useDestinationStatus(id: string) {
  const invalidate = useInvalidateDestinations();
  return useMutation({
    mutationFn: async ({ action, reason }: { action: StatusAction; reason?: string }) => {
      const path = { destination_id: id };
      switch (action) {
        case 'activate':
          return unwrap(
            await api.POST('/api/v1/destinations/{destination_id}/activate', {
              params: { path },
            }),
          );
        case 'deactivate':
          return unwrap(
            await api.POST('/api/v1/destinations/{destination_id}/deactivate', {
              params: { path },
              body: { reason: reason || null },
            }),
          );
        case 'delete': {
          const result = await api.DELETE('/api/v1/destinations/{destination_id}', {
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

export function useDdlPreview(id: string) {
  return useMutation({
    mutationFn: async (body: DdlPreviewRequest) =>
      unwrap(
        await api.POST('/api/v1/destinations/{destination_id}/ddl-preview', {
          params: { path: { destination_id: id } },
          body,
        }),
      ),
  });
}
