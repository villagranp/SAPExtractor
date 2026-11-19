import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type AuditEvent = components['schemas']['AuditEventResponse'];
export type AuditCategory = components['schemas']['Category'];
export type AuditOutcome = components['schemas']['Outcome'];
export type AuditVerification = components['schemas']['AuditVerificationResponse'];

export interface AuditFilters {
  from?: string;
  to?: string;
  actor?: string;
  action?: string;
  category?: AuditCategory;
  outcome?: AuditOutcome;
  resource_type?: string;
  resource_id?: string;
}

export const auditKeys = {
  all: ['audit'] as const,
  list: (filters: AuditFilters, page: number, pageSize: number) =>
    ['audit', 'list', filters, page, pageSize] as const,
  catalog: ['audit', 'catalog'] as const,
  integrity: ['audit', 'integrity'] as const,
};

export function useAuditEvents(filters: AuditFilters, page: number, pageSize: number) {
  return useQuery({
    queryKey: auditKeys.list(filters, page, pageSize),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/audit-events', {
          params: { query: { ...filters, page, page_size: pageSize } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useAuditCatalog() {
  return useQuery({
    queryKey: auditKeys.catalog,
    queryFn: async () => unwrap(await api.GET('/api/v1/audit-events/catalog')),
    staleTime: 10 * 60_000,
  });
}

export function useAuditIntegrity() {
  return useQuery({
    queryKey: auditKeys.integrity,
    queryFn: async () => unwrap(await api.GET('/api/v1/audit-events/integrity')),
  });
}

export function useVerifyAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/audit-events/verify')),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: auditKeys.all }),
  });
}

/** Descarga la exportación (el backend registra el evento audit.exported). */
export function useExportAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ filters, format }: { filters: AuditFilters; format: 'csv' | 'json' }) => {
      const result = await api.POST('/api/v1/audit-events/export', {
        body: { ...filters, format },
        parseAs: 'blob',
      });
      if (!result.response.ok) unwrap(result);
      const blob = result.data as unknown as Blob;
      const disposition = result.response.headers.get('content-disposition') ?? '';
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `auditoria.${format}`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: auditKeys.all }),
  });
}
