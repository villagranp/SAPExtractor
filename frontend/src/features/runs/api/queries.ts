import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type RunSummary = S['RunSummary'];
export type RunDetail = S['RunDetail'];
export type RunBrief = S['RunBrief'];
export type RunStatus = S['RunStatus'];
export type RunMode = S['RunMode'];
export type RunTrigger = S['RunTrigger'];
export type RunLogLine = S['RunLogLine'];
export type RunStep = S['StepResponse'];
export type RunAttempt = S['AttemptResponse'];
export type AttemptOutcome = S['AttemptOutcome'];
export type DeltaState = S['DeltaStateResponse'];
export type DeltaStatus = S['DeltaStatus'];
export type Worker = S['WorkerResponse'];

export const RUN_STATUSES: RunStatus[] = ['queued', 'running', 'succeeded', 'failed', 'cancelled'];
export const RUN_TRIGGERS: RunTrigger[] = ['schedule', 'manual', 'api'];
const ACTIVE: RunStatus[] = ['queued', 'running'];
/** La pantalla consulta cada segundo mientras el run sigue vivo (plan 03 §2: menos de 2 s). */
const LIVE_MS = 1_000;

export function isActive(status: RunStatus | undefined): boolean {
  return status !== undefined && ACTIVE.includes(status);
}

export interface RunFilters {
  /** Id del pipeline (el parámetro de la API se llama `pipeline`). */
  pipeline?: string;
  status?: RunStatus;
  trigger?: RunTrigger;
  from?: string;
  to?: string;
  page: number;
  page_size: number;
}

export const runKeys = {
  all: ['runs'] as const,
  list: (filters: RunFilters) => ['runs', 'list', filters] as const,
  detail: (id: string) => ['runs', 'detail', id] as const,
  steps: (id: string) => ['runs', 'steps', id] as const,
  logs: (id: string) => ['runs', 'logs', id] as const,
  delta: (pipelineId: string) => ['runs', 'delta', pipelineId] as const,
  workers: ['workers'] as const,
};

export function useRuns(filters: RunFilters) {
  return useQuery({
    queryKey: runKeys.list(filters),
    queryFn: async () => unwrap(await api.GET('/api/v1/runs', { params: { query: filters } })),
    placeholderData: keepPreviousData,
    staleTime: 0,
    // Mientras haya algún run vivo en la página, la lista se refresca sola.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => isActive(r.status)) ? LIVE_MS * 2 : false,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: runKeys.detail(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/runs/{run_id}', { params: { path: { run_id: id } } })),
    enabled: Boolean(id),
    refetchInterval: (query) => (isActive(query.state.data?.status) ? LIVE_MS : false),
  });
}

/** Pasos del motor por intento; se refrescan mientras el run sigue vivo. */
export function useRunSteps(id: string, live: boolean) {
  return useQuery({
    queryKey: runKeys.steps(id),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/runs/{run_id}/steps', { params: { path: { run_id: id } } })),
    enabled: Boolean(id),
    refetchInterval: live ? LIVE_MS : false,
  });
}

/**
 * Log en vivo por cursor (plan 03 §2): cada consulta pide solo las líneas nuevas (`after`) y
 * se repite cada segundo hasta que la API indica que el run terminó. Las líneas se acumulan
 * sin duplicados (el cursor es el id de la línea).
 */
export function useRunLogs(id: string) {
  const [lines, setLines] = useState<RunLogLine[]>([]);
  const cursor = useRef<number | undefined>(undefined);
  const query = useQuery({
    queryKey: runKeys.logs(id),
    queryFn: async () => {
      const after = cursor.current;
      const page = unwrap(
        await api.GET('/api/v1/runs/{run_id}/logs', {
          params: { path: { run_id: id }, query: after === undefined ? {} : { after } },
        }),
      );
      if (page.items.length > 0) {
        cursor.current = page.next_after ?? after;
        setLines((previous) => {
          const seen = new Set(previous.map((line) => line.id));
          return [...previous, ...page.items.filter((line) => !seen.has(line.id))];
        });
      }
      return page;
    },
    enabled: Boolean(id),
    refetchInterval: (q) => (q.state.data?.finished ? false : LIVE_MS),
  });
  return { lines, error: query.error, finished: query.data?.finished ?? false };
}

/** Una ejecución cambia también la ficha y la lista de su pipeline. */
function useInvalidateRuns() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: runKeys.all }),
      queryClient.invalidateQueries({ queryKey: ['pipelines'] }),
    ]);
}

export function useCancelRun(id: string) {
  const invalidate = useInvalidateRuns();
  return useMutation({
    mutationFn: async (reason: string) =>
      unwrap(
        await api.POST('/api/v1/runs/{run_id}/cancel', {
          params: { path: { run_id: id } },
          body: { reason: reason || null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useRetryRun(id: string) {
  const invalidate = useInvalidateRuns();
  return useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/runs/{run_id}/retry', { params: { path: { run_id: id } } })),
    onSuccess: invalidate,
  });
}

/** Workers registrados: sirve para avisar cuando ninguno puede tomar ejecuciones. */
export function useWorkers(enabled = true) {
  return useQuery({
    queryKey: runKeys.workers,
    queryFn: async () => unwrap(await api.GET('/api/v1/workers')),
    enabled,
    staleTime: 15_000,
  });
}

export function useDeltaState(pipelineId: string, enabled = true) {
  return useQuery({
    queryKey: runKeys.delta(pipelineId),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/pipelines/{pipeline_id}/delta', {
          params: { path: { pipeline_id: pipelineId } },
        }),
      ),
    enabled: Boolean(pipelineId) && enabled,
    // Lo cambia el worker, no esta pantalla: siempre se pide de nuevo al volver a ella.
    staleTime: 0,
  });
}

export function useResetDelta(pipelineId: string) {
  const invalidate = useInvalidateRuns();
  return useMutation({
    mutationFn: async (reason: string) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/delta/reset', {
          params: { path: { pipeline_id: pipelineId } },
          body: { reason },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useResolveSubscription(pipelineId: string) {
  const invalidate = useInvalidateRuns();
  return useMutation({
    mutationFn: async (body: S['SubscriptionRequest']) =>
      unwrap(
        await api.POST('/api/v1/pipelines/{pipeline_id}/delta/sap-subscription', {
          params: { path: { pipeline_id: pipelineId } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}
