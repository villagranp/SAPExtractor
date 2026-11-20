import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type SourceSummary = S['SourceSummary'];
export type SourceDetail = S['SourceDetail'];
export type SourceType = S['SourceType'];
export type SourceStatus = S['SourceStatus'];
export type DeltaMethod = S['DeltaMethod'];
export type DeltaSemantics = S['DeltaSemantics'];
export type WatermarkKind = S['WatermarkKind'];
export type FilterOperator = S['FilterOperator'];
export type Inspection = S['InspectionResponse'];
export type SchemaColumn = S['SchemaColumnResponse'];
export type SchemaVersion = S['SchemaVersionResponse'];
export type SchemaDiff = S['SchemaDiffResponse'];
export type SchemaRefresh = S['SchemaRefreshResponse'];
export type Preview = S['PreviewResponse'];
export type SourceCreateRequest = S['SourceCreateRequest'];
export type SourceUpdateRequest = S['SourceUpdateRequest'];
export type SourceInspectRequest = S['SourceInspectRequest'];
export type DeltaConfigBody = S['DeltaConfigBody'];
export type FilterConditionBody = S['FilterConditionBody'];

export const SOURCE_TYPES: SourceType[] = ['ODP_EXTRACTOR', 'CDS_ODP', 'CDS_ODATA'];
export const SOURCE_STATUSES: SourceStatus[] = ['draft', 'active', 'deprecated'];
export const DELTA_SEMANTICS: DeltaSemantics[] = [
  'after_image',
  'additive',
  'before_after',
  'unknown',
];
export const WATERMARK_KINDS: WatermarkKind[] = ['datetime', 'date', 'decimal_timestamp'];
export const FILTER_OPERATORS: FilterOperator[] = ['eq', 'ne', 'gt', 'ge', 'lt', 'le'];

export interface SourceFilters {
  q?: string;
  type?: SourceType;
  status?: SourceStatus;
  system?: string;
  page: number;
  page_size: number;
}

export const sourceKeys = {
  all: ['sources'] as const,
  list: (filters: SourceFilters) => ['sources', 'list', filters] as const,
  detail: (id: string) => ['sources', 'detail', id] as const,
  versions: (id: string) => ['sources', 'detail', id, 'versions'] as const,
  diff: (id: string, from: number, to: number) =>
    ['sources', 'detail', id, 'diff', from, to] as const,
};

export function useSources(filters: SourceFilters) {
  return useQuery({
    queryKey: sourceKeys.list(filters),
    queryFn: async () => unwrap(await api.GET('/api/v1/sources', { params: { query: filters } })),
    placeholderData: keepPreviousData,
  });
}

export function useSource(id: string) {
  return useQuery({
    queryKey: sourceKeys.detail(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/sources/{source_id}', { params: { path: { source_id: id } } })),
    enabled: Boolean(id),
  });
}

export function useSchemaVersions(id: string) {
  return useQuery({
    queryKey: sourceKeys.versions(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/sources/{source_id}/schema/versions', {
          params: { path: { source_id: id } },
        }),
      ),
  });
}

export function useSchemaDiff(id: string, from: number | null, to: number | null) {
  return useQuery({
    queryKey: sourceKeys.diff(id, from ?? 0, to ?? 0),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/sources/{source_id}/schema/diff', {
          params: { path: { source_id: id }, query: { from: from ?? 1, to: to ?? 1 } },
        }),
      ),
    enabled: from !== null && to !== null && from !== to,
  });
}

/** Lee el `$metadata` de un entity set sin guardar nada (asistente). */
export function useInspectEntitySet() {
  return useMutation({
    mutationFn: async (body: SourceInspectRequest) =>
      unwrap(await api.POST('/api/v1/sources/inspect', { body })),
  });
}

function useInvalidateSources() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: sourceKeys.all });
}

export function useCreateSource() {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async (body: SourceCreateRequest) =>
      unwrap(await api.POST('/api/v1/sources', { body })),
    onSuccess: invalidate,
  });
}

export function useUpdateSource(id: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async (body: SourceUpdateRequest) =>
      unwrap(
        await api.PATCH('/api/v1/sources/{source_id}', {
          params: { path: { source_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useActivateSource(id: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/sources/{source_id}/activate', {
          params: { path: { source_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeprecateSource(id: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async (reason: string) =>
      unwrap(
        await api.POST('/api/v1/sources/{source_id}/deprecate', {
          params: { path: { source_id: id } },
          body: { reason: reason || null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteSource(id: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async (reason: string) => {
      const result = await api.DELETE('/api/v1/sources/{source_id}', {
        params: { path: { source_id: id }, query: reason ? { reason } : {} },
      });
      if (result.error) unwrap(result);
    },
    onSuccess: invalidate,
  });
}

export function usePreviewSource(id: string) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/sources/{source_id}/preview', {
          params: { path: { source_id: id } },
        }),
      ),
  });
}

export function useRefreshSchema(id: string) {
  const invalidate = useInvalidateSources();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/sources/{source_id}/schema/refresh', {
          params: { path: { source_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}
