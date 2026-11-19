import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type SapSystemSummary = S['SapSystemSummary'];
export type SapSystemDetail = S['SapSystemDetail'];
export type SapEnvironment = S['SapEnvironment'];
export type SystemType = S['SystemType'];
export type SystemStatus = S['SystemStatus'];
export type AuthType = S['AuthType'];
export type TestReport = S['TestReportResponse'];
export type TestStep = S['TestStepResponse'];
export type CatalogService = S['CatalogServiceResponse'];
export type ServiceKind = S['ServiceKind'];
export type EntitySet = S['EntitySetResponse'];
export type SapSystemCreateRequest = S['SapSystemCreateRequest'];
export type SapSystemTestRequest = S['SapSystemTestRequest'];
export type SapSystemUpdateRequest = S['SapSystemUpdateRequest'];
export type SapSystemDuplicateRequest = S['SapSystemDuplicateRequest'];
export type CredentialsIn = SapSystemCreateRequest['credentials'];

export const ENVIRONMENTS: SapEnvironment[] = ['DEV', 'QAS', 'PRD'];
export const SYSTEM_TYPES: SystemType[] = ['S4HANA_ONPREM', 'ECC', 'S4HANA_CLOUD'];
export const AUTH_TYPES: AuthType[] = ['basic', 'x509', 'oauth2_client_credentials'];
export const SERVICE_KINDS: ServiceKind[] = ['odp', 'cds', 'other', 'unknown'];

export interface SapSystemFilters {
  q?: string;
  environment?: SapEnvironment;
  status?: SystemStatus;
  page: number;
  page_size: number;
}

export interface CatalogFilters {
  search?: string;
  type?: ServiceKind[];
  page: number;
  page_size: number;
}

export const sapKeys = {
  all: ['sap-systems'] as const,
  list: (filters: SapSystemFilters) => ['sap-systems', 'list', filters] as const,
  detail: (id: string) => ['sap-systems', 'detail', id] as const,
  services: (id: string, filters: CatalogFilters) =>
    ['sap-systems', 'detail', id, 'services', filters] as const,
  entitySets: (id: string, service: string, version: number) =>
    ['sap-systems', 'detail', id, 'entity-sets', service, version] as const,
};

export function useSapSystems(filters: SapSystemFilters) {
  return useQuery({
    queryKey: sapKeys.list(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/sap-systems', { params: { query: filters } })),
    placeholderData: keepPreviousData,
  });
}

export function useSapSystem(id: string) {
  return useQuery({
    queryKey: sapKeys.detail(id),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/sap-systems/{system_id}', { params: { path: { system_id: id } } }),
      ),
    enabled: Boolean(id),
  });
}

function useInvalidateSystems() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: sapKeys.all });
}

export function useCreateSapSystem() {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async (body: SapSystemCreateRequest) =>
      unwrap(await api.POST('/api/v1/sap-systems', { body })),
    onSuccess: invalidate,
  });
}

/** Prueba del asistente sin guardar. */
export function useTestDraftConnection() {
  return useMutation({
    mutationFn: async (body: SapSystemTestRequest) =>
      unwrap(await api.POST('/api/v1/sap-systems/test', { body })),
  });
}

export function useUpdateSapSystem(id: string) {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async (body: SapSystemUpdateRequest) =>
      unwrap(
        await api.PATCH('/api/v1/sap-systems/{system_id}', {
          params: { path: { system_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useTestSapSystem(id: string) {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/sap-systems/{system_id}/test', {
          params: { path: { system_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useRotateCredentials(id: string) {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async (body: { credentials: CredentialsIn; version?: number }) =>
      unwrap(
        await api.POST('/api/v1/sap-systems/{system_id}/credentials/rotate', {
          params: { path: { system_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDuplicateSapSystem(id: string) {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async (body: SapSystemDuplicateRequest) =>
      unwrap(
        await api.POST('/api/v1/sap-systems/{system_id}/duplicate', {
          params: { path: { system_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export type StatusAction = 'activate' | 'deactivate' | 'delete';

export function useSapSystemStatus(id: string) {
  const invalidate = useInvalidateSystems();
  return useMutation({
    mutationFn: async ({ action, reason }: { action: StatusAction; reason?: string }) => {
      const path = { system_id: id };
      switch (action) {
        case 'activate':
          return unwrap(
            await api.POST('/api/v1/sap-systems/{system_id}/activate', { params: { path } }),
          );
        case 'deactivate':
          return unwrap(
            await api.POST('/api/v1/sap-systems/{system_id}/deactivate', {
              params: { path },
              body: { reason: reason || null },
            }),
          );
        case 'delete': {
          const result = await api.DELETE('/api/v1/sap-systems/{system_id}', {
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

export function useCatalog(id: string, filters: CatalogFilters) {
  return useQuery({
    queryKey: sapKeys.services(id, filters),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/sap-systems/{system_id}/services', {
          params: { path: { system_id: id }, query: filters },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/** Relee el catálogo desde SAP (ignora la caché del servidor). */
export function useRefreshCatalog(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.GET('/api/v1/sap-systems/{system_id}/services', {
          params: { path: { system_id: id }, query: { refresh: true, page_size: 1 } },
        }),
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['sap-systems', 'detail', id, 'services'] }),
  });
}

export function useEntitySets(id: string, service: CatalogService | null) {
  return useQuery({
    queryKey: sapKeys.entitySets(id, service?.technical_name ?? '', service?.version ?? 1),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/sap-systems/{system_id}/services/{service_name}/entity-sets', {
          params: {
            path: { system_id: id, service_name: service?.technical_name ?? '' },
            query: { version: service?.version ?? 1 },
          },
        }),
      ),
    enabled: service !== null,
    staleTime: 5 * 60_000,
  });
}
