import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap } from '@/shared/api/client';
import type { components } from '@/shared/api/schema';

type S = components['schemas'];
export type DashboardSummary = S['DashboardSummary'];
export type LiveRun = S['LiveRun'];
export type FailedRun = S['FailedRun'];
export type WorkerState = S['WorkerState'];
export type SystemState = S['SystemState'];
export type TimelineRun = S['TimelineRun'];
export type FreshnessRow = S['FreshnessRow'];
export type Alert = S['AlertResponse'];
export type AlertDetail = S['AlertDetail'];
export type AlertStatus = S['AlertStatus'];
export type AlertType = S['AlertType'];
export type Severity = S['AlertSeverity'];
export type RuleType = S['RuleTypeResponse'];
export type Rule = S['RuleResponse'];
export type RuleRequest = S['RuleRequest'];
export type Channel = S['ChannelResponse'];
export type ChannelType = S['ChannelType'];
export type ChannelCreateRequest = S['ChannelCreateRequest'];
export type Subscription = S['AlertSubscriptionResponse'];

/** El panel se refresca solo: el progreso de los runs se ve sin recargar (plan 09). */
const LIVE_MS = 5_000;

export interface DashboardFilters {
  environment?: string;
  tag?: string;
}

export const monitoringKeys = {
  all: ['monitoring'] as const,
  summary: (f: DashboardFilters) => ['monitoring', 'summary', f] as const,
  timeline: (f: DashboardFilters, hours: number) => ['monitoring', 'timeline', f, hours] as const,
  freshness: (f: DashboardFilters) => ['monitoring', 'freshness', f] as const,
  alerts: (q: AlertQuery) => ['monitoring', 'alerts', q] as const,
  alert: (id: string) => ['monitoring', 'alert', id] as const,
  counts: ['monitoring', 'counts'] as const,
  ruleTypes: ['monitoring', 'rule-types'] as const,
  rules: ['monitoring', 'rules'] as const,
  channels: ['monitoring', 'channels'] as const,
  subscriptions: ['monitoring', 'subscriptions'] as const,
};

function clean(filters: DashboardFilters): DashboardFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, v]) => Boolean(v)));
}

export function useDashboardSummary(filters: DashboardFilters) {
  return useQuery({
    queryKey: monitoringKeys.summary(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/dashboard/summary', { params: { query: clean(filters) } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_MS,
    staleTime: 0,
  });
}

export function useTimeline(filters: DashboardFilters, hours: number) {
  return useQuery({
    queryKey: monitoringKeys.timeline(filters, hours),
    queryFn: async () =>
      unwrap(
        await api.GET('/api/v1/dashboard/timeline', {
          params: { query: { ...clean(filters), hours } },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_MS * 6,
  });
}

export function useFreshness(filters: DashboardFilters) {
  return useQuery({
    queryKey: monitoringKeys.freshness(filters),
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/dashboard/freshness', { params: { query: clean(filters) } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_MS * 6,
  });
}

export interface AlertQuery {
  live?: boolean;
  status?: AlertStatus;
  page: number;
  page_size: number;
}

export function useAlerts(query: AlertQuery) {
  return useQuery({
    queryKey: monitoringKeys.alerts(query),
    queryFn: async () => unwrap(await api.GET('/api/v1/alerts', { params: { query } })),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_MS * 2,
  });
}

/** Alertas abiertas para la campana de la cabecera. */
export function useAlertCounts(enabled: boolean) {
  return useQuery({
    queryKey: monitoringKeys.counts,
    queryFn: async () => unwrap(await api.GET('/api/v1/alerts/counts')),
    enabled,
    refetchInterval: 30_000,
  });
}

function useInvalidateMonitoring() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: monitoringKeys.all });
}

export function useAckAlert() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.POST('/api/v1/alerts/{alert_id}/ack', { params: { path: { alert_id: id } } }),
      ),
    onSuccess: invalidate,
  });
}

export function useResolveAlert() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) =>
      unwrap(
        await api.POST('/api/v1/alerts/{alert_id}/resolve', {
          params: { path: { alert_id: id } },
          body: { reason: reason || null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useRuleTypes() {
  return useQuery({
    queryKey: monitoringKeys.ruleTypes,
    queryFn: async () => unwrap(await api.GET('/api/v1/alert-rule-types')),
    staleTime: 10 * 60_000,
  });
}

export function useRules() {
  return useQuery({
    queryKey: monitoringKeys.rules,
    queryFn: async () => unwrap(await api.GET('/api/v1/alert-rules')),
  });
}

export function useSaveRule() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string | null; body: RuleRequest }) =>
      id
        ? unwrap(
            await api.PUT('/api/v1/alert-rules/{rule_id}', {
              params: { path: { rule_id: id } },
              body,
            }),
          )
        : unwrap(await api.POST('/api/v1/alert-rules', { body })),
    onSuccess: invalidate,
  });
}

export function useDeleteRule() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await api.DELETE('/api/v1/alert-rules/{rule_id}', {
        params: { path: { rule_id: id } },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: invalidate,
  });
}

export function useChannels(enabled: boolean) {
  return useQuery({
    queryKey: monitoringKeys.channels,
    queryFn: async () => unwrap(await api.GET('/api/v1/notification-channels')),
    enabled,
  });
}

export function useCreateChannel() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (body: ChannelCreateRequest) =>
      unwrap(await api.POST('/api/v1/notification-channels', { body })),
    onSuccess: invalidate,
  });
}

export function useTestChannel() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.POST('/api/v1/notification-channels/{channel_id}/test', {
          params: { path: { channel_id: id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteChannel() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await api.DELETE('/api/v1/notification-channels/{channel_id}', {
        params: { path: { channel_id: id } },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: invalidate,
  });
}

export function useSubscriptions(enabled: boolean) {
  return useQuery({
    queryKey: monitoringKeys.subscriptions,
    queryFn: async () => unwrap(await api.GET('/api/v1/alert-subscriptions')),
    enabled,
  });
}

export function useSubscribe() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (body: S['AlertSubscriptionRequest']) =>
      unwrap(await api.POST('/api/v1/alert-subscriptions', { body })),
    onSuccess: invalidate,
  });
}

export function useUnsubscribe() {
  const invalidate = useInvalidateMonitoring();
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await api.DELETE('/api/v1/alert-subscriptions/{subscription_id}', {
        params: { path: { subscription_id: id } },
      });
      if (!result.response.ok) unwrap(result);
      return null;
    },
    onSuccess: invalidate,
  });
}
