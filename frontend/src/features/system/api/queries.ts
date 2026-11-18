import { useQuery } from '@tanstack/react-query';
import { api, ApiError, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

export type HealthReport = components['schemas']['HealthReport'];
export type SystemInfo = components['schemas']['SystemInfo'];

export const systemKeys = {
  info: ['system', 'info'] as const,
  ready: ['system', 'health', 'ready'] as const,
};

export function useSystemInfo() {
  return useQuery({
    queryKey: systemKeys.info,
    queryFn: async () => unwrap(await api.GET('/api/v1/system/info')),
    staleTime: 5 * 60_000,
  });
}

/** Estado de preparación. Un 503 también trae el informe con el detalle de cada comprobación. */
export function useReadiness() {
  return useQuery({
    queryKey: systemKeys.ready,
    queryFn: async (): Promise<HealthReport> => {
      const result = await api.GET('/api/v1/health/ready');
      if (result.data) return result.data;
      const body: unknown = result.error;
      if (result.response.status === 503 && typeof body === 'object' && body && 'checks' in body) {
        return body as HealthReport;
      }
      throw new ApiError(result.response.status, null);
    },
    refetchInterval: 30_000,
    retry: false,
  });
}
