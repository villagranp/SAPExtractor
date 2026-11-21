import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { RunStatusPill } from '@/features/runs/components/RunParts';
import type { RunMode, RunStatus } from '@/features/runs/api/queries';
import { useModeLabel, useNumbers } from '@/features/runs/format';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Card,
  DataTable,
  FilterBar,
  Metric,
  PageHeader,
  SelectField,
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  useDashboardSummary,
  useFreshness,
  useTimeline,
  type DashboardFilters,
  type FailedRun,
  type FreshnessRow,
  type LiveRun,
  type SystemState,
  type WorkerState,
} from '../api/queries';
import { FreshnessPill, Timeline } from '../components/MonitoringParts';
import '../monitoring.css';

const ENVIRONMENTS = ['DEV', 'QAS', 'PRD'] as const;
const HOURS = 24;

/** Panel de monitoreo (plan 09 §1): se refresca solo cada pocos segundos. */
export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const mode = useModeLabel();
  const { rows } = useNumbers();
  const [environment, setEnvironment] = useState('');
  const [tag, setTag] = useState('');
  const filters: DashboardFilters = {
    ...(environment ? { environment } : {}),
    ...(tag.trim() ? { tag: tag.trim() } : {}),
  };
  const summary = useDashboardSummary(filters);
  const timeline = useTimeline(filters, HOURS);
  const freshness = useFreshness(filters);
  const percent = new Intl.NumberFormat(i18n.resolvedLanguage ?? 'es', {
    style: 'percent',
    maximumFractionDigits: 1,
  });
  const s = summary.data;
  const openAlerts = s ? Object.values(s.alerts_open).reduce((a, b) => a + b, 0) : 0;

  const liveColumns: Column<LiveRun>[] = [
    {
      key: 'pipeline',
      header: t('runs.fields.pipeline'),
      render: (r) => <strong>{r.pipeline_code}</strong>,
    },
    { key: 'mode', header: t('runs.fields.mode'), render: (r) => mode(r.mode as RunMode) },
    {
      key: 'status',
      header: t('runs.fields.status'),
      render: (r) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <RunStatusPill status={r.status as RunStatus} />
          {r.step && <span className="sx-muted">{t(`runs.steps.${r.step}`)}</span>}
        </span>
      ),
    },
    {
      key: 'progress',
      header: t('monitoring.dashboard.progress'),
      render: (r) =>
        r.rows === null
          ? '—'
          : t('monitoring.dashboard.progressValue', { rows: rows(r.rows), pages: r.pages ?? 0 }),
    },
    {
      key: 'started',
      header: t('runs.fields.started'),
      render: (r) => formatDateTime(r.started_at),
    },
  ];
  const failureColumns: Column<FailedRun>[] = [
    {
      key: 'pipeline',
      header: t('runs.fields.pipeline'),
      render: (r) => <strong>{r.pipeline_code}</strong>,
    },
    {
      key: 'error',
      header: t('runs.fields.error'),
      render: (r) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <code>{r.error_code ?? '—'}</code>
          {r.error_message && <span className="sx-muted">{r.error_message}</span>}
        </span>
      ),
    },
    {
      key: 'finished',
      header: t('runs.fields.finished'),
      render: (r) => formatDateTime(r.finished_at),
    },
  ];
  const freshnessColumns: Column<FreshnessRow>[] = [
    {
      key: 'pipeline',
      header: t('runs.fields.pipeline'),
      render: (r) => <strong>{r.pipeline_code}</strong>,
    },
    {
      key: 'state',
      header: t('monitoring.freshness.state'),
      render: (r) => <FreshnessPill row={r} />,
    },
    {
      key: 'last',
      header: t('monitoring.freshness.lastSuccess'),
      render: (r) =>
        r.minutes_since === null
          ? t('monitoring.freshness.never')
          : t('monitoring.freshness.ago', { minutes: rows(r.minutes_since) }),
    },
    {
      key: 'sla',
      header: t('monitoring.freshness.sla'),
      render: (r) =>
        r.sla_minutes === null
          ? '—'
          : t('monitoring.freshness.slaValue', { minutes: r.sla_minutes }),
    },
  ];
  const workerColumns: Column<WorkerState>[] = [
    { key: 'host', header: t('monitoring.workers.host'), render: (w) => <code>{w.hostname}</code> },
    {
      key: 'status',
      header: t('runs.fields.status'),
      render: (w) => (
        <StatusPill tone={w.status === 'online' ? 'success' : 'warning'}>
          {t(`monitoring.workers.statuses.${w.status}`)}
        </StatusPill>
      ),
    },
    {
      key: 'busy',
      header: t('monitoring.workers.busy'),
      render: (w) =>
        w.current_run_id ? (
          <Link to={`/runs/${w.current_run_id}`}>{t('monitoring.workers.running')}</Link>
        ) : (
          t('monitoring.workers.idle')
        ),
    },
    {
      key: 'beat',
      header: t('monitoring.workers.heartbeat'),
      render: (w) => formatDateTime(w.heartbeat_at),
    },
  ];
  const systemColumns: Column<SystemState>[] = [
    {
      key: 'code',
      header: t('monitoring.systems.system'),
      render: (x) => <strong>{x.code}</strong>,
    },
    { key: 'env', header: t('monitoring.systems.environment'), render: (x) => x.environment },
    {
      key: 'test',
      header: t('monitoring.systems.lastTest'),
      render: (x) =>
        x.last_test_ok === null ? (
          <StatusPill tone="neutral">{t('monitoring.systems.never')}</StatusPill>
        ) : x.last_test_ok ? (
          <StatusPill tone="success">{t('monitoring.systems.ok')}</StatusPill>
        ) : (
          <StatusPill tone="danger">{t('monitoring.systems.failed')}</StatusPill>
        ),
    },
    { key: 'running', header: t('monitoring.systems.running'), render: (x) => rows(x.running) },
  ];

  return (
    <>
      <PageHeader
        title={t('monitoring.dashboard.title')}
        description={t('monitoring.dashboard.description')}
      />
      <FilterBar>
        <SelectField
          label={t('monitoring.dashboard.environment')}
          placeholder={t('common.all')}
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value);
          }}
          options={ENVIRONMENTS.map((env) => ({ value: env, label: env }))}
        />
        <TextField
          label={t('monitoring.dashboard.tag')}
          value={tag}
          onChange={(e) => {
            setTag(e.target.value);
          }}
        />
      </FilterBar>
      {summary.error && <Alert tone="danger">{describeError(summary.error).message}</Alert>}
      <div className="sx-dashboard__grid">
        <Card title={t('monitoring.dashboard.kpisTitle')}>
          <div className="sx-dashboard__kpis">
            <Metric
              label={t('monitoring.kpis.pipelines')}
              value={s ? rows(s.pipelines_active) : '—'}
            />
            <Metric
              label={t('monitoring.kpis.succeeded')}
              value={s ? rows(s.runs_24h.succeeded) : '—'}
            />
            <Metric label={t('monitoring.kpis.failed')} value={s ? rows(s.runs_24h.failed) : '—'} />
            <Metric
              label={t('monitoring.kpis.live')}
              value={s ? rows(s.runs_24h.running + s.runs_24h.queued) : '—'}
            />
            <Metric label={t('monitoring.kpis.rows')} value={s ? rows(s.rows_24h) : '—'} />
            <Metric
              label={t('monitoring.kpis.successRate')}
              value={s?.success_rate_7d != null ? percent.format(s.success_rate_7d) : '—'}
            />
            <Metric
              label={t('monitoring.kpis.broken')}
              value={s ? rows(s.pipelines_broken) : '—'}
            />
            <Metric label={t('monitoring.kpis.alerts')} value={s ? rows(openAlerts) : '—'}>
              {openAlerts > 0 && <Link to="/alerts">{t('monitoring.kpis.seeAlerts')}</Link>}
            </Metric>
          </div>
        </Card>
        <Card title={t('monitoring.dashboard.liveTitle')}>
          <DataTable
            caption={t('monitoring.dashboard.liveTitle')}
            columns={liveColumns}
            rows={s?.live_runs ?? []}
            rowKey={(r) => r.id}
            loading={summary.isPending}
            empty={t('monitoring.dashboard.noLive')}
            onRowClick={(r) => void navigate(`/runs/${r.id}`)}
          />
        </Card>
        <Card title={t('monitoring.timeline.title')}>
          {timeline.error ? (
            <Alert tone="danger">{describeError(timeline.error).message}</Alert>
          ) : (
            <Timeline runs={timeline.data ?? []} hours={HOURS} now={new Date()} />
          )}
        </Card>
        <div className="sx-dashboard__split">
          <Card title={t('monitoring.dashboard.failuresTitle')}>
            <DataTable
              caption={t('monitoring.dashboard.failuresTitle')}
              columns={failureColumns}
              rows={s?.recent_failures ?? []}
              rowKey={(r) => r.id}
              loading={summary.isPending}
              empty={t('monitoring.dashboard.noFailures')}
              onRowClick={(r) => void navigate(`/runs/${r.id}`)}
            />
          </Card>
          <Card title={t('monitoring.freshness.title')}>
            <DataTable
              caption={t('monitoring.freshness.title')}
              columns={freshnessColumns}
              rows={freshness.data ?? []}
              rowKey={(r) => r.pipeline_id}
              loading={freshness.isPending}
              empty={t('monitoring.freshness.empty')}
              onRowClick={(r) => void navigate(`/pipelines/${r.pipeline_id}`)}
            />
          </Card>
        </div>
        <div className="sx-dashboard__split">
          <Card title={t('monitoring.workers.title')}>
            <DataTable
              caption={t('monitoring.workers.title')}
              columns={workerColumns}
              rows={s?.workers ?? []}
              rowKey={(w) => w.id}
              loading={summary.isPending}
              empty={t('monitoring.workers.empty')}
            />
          </Card>
          <Card title={t('monitoring.systems.title')}>
            <DataTable
              caption={t('monitoring.systems.title')}
              columns={systemColumns}
              rows={s?.sap_systems ?? []}
              rowKey={(x) => x.id}
              loading={summary.isPending}
              empty={t('monitoring.systems.empty')}
              onRowClick={(x) => void navigate(`/sap-systems/${x.id}`)}
            />
          </Card>
        </div>
      </div>
    </>
  );
}
