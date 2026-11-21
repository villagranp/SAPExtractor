import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  DetailList,
  Metric,
  PageHeader,
  Spinner,
  type Column,
} from '@/shared/ui';
import {
  isActive,
  useCancelRun,
  useRetryRun,
  useRun,
  useRunSteps,
  type RunAttempt,
  type RunStep,
} from '../api/queries';
import { OutcomePill, RunLog, RunStatusPill } from '../components/RunParts';
import { IdleWorkersNotice } from '../components/RunsTable';
import { formatDuration, useModeLabel, useNumbers } from '../format';
import '../runs.css';

export function RunDetailPage() {
  const { t } = useTranslation();
  const { runId = '' } = useParams();
  const run = useRun(runId);
  const steps = useRunSteps(runId, isActive(run.data?.status));
  const cancel = useCancelRun(runId);
  const retry = useRetryRun(runId);
  const canRun = useCan('pipeline:run');
  const mode = useModeLabel();
  const { rows, seconds } = useNumbers();
  const [cancelling, setCancelling] = useState(false);

  if (run.isPending) return <Spinner label={t('common.loading')} />;
  if (run.error) return <Alert tone="danger">{describeError(run.error).message}</Alert>;
  const r = run.data;
  const live = isActive(r.status);
  const failedAttempts = r.attempts.filter(
    (a) => a.outcome === 'failed_transient' || a.outcome === 'stale' || a.outcome === 'failed',
  ).length;

  const stepColumns: Column<RunStep>[] = [
    { key: 'attempt', header: t('runs.fields.attempt'), render: (s) => s.attempt },
    { key: 'step', header: t('runs.fields.step'), render: (s) => t(`runs.steps.${s.step}`) },
    {
      key: 'status',
      header: t('runs.fields.status'),
      render: (s) => t(`runs.stepStatuses.${s.status}`),
    },
    {
      key: 'started',
      header: t('runs.fields.started'),
      render: (s) => formatDateTime(s.started_at),
    },
    {
      key: 'duration',
      header: t('runs.fields.duration'),
      render: (s) => seconds(s.metrics.duration_ms),
    },
  ];
  const attemptColumns: Column<RunAttempt>[] = [
    { key: 'number', header: t('runs.fields.attempt'), render: (a) => a.number },
    {
      key: 'outcome',
      header: t('runs.fields.outcome'),
      render: (a) => <OutcomePill outcome={a.outcome} />,
    },
    {
      key: 'started',
      header: t('runs.fields.started'),
      render: (a) => formatDateTime(a.started_at),
    },
    {
      key: 'duration',
      header: t('runs.fields.duration'),
      render: (a) => formatDuration(a.started_at, a.finished_at),
    },
    {
      key: 'spool',
      header: t('runs.fields.spool'),
      render: (a) => (a.has_spool ? t('common.yes') : t('common.no')),
    },
    {
      key: 'error',
      header: t('runs.fields.error'),
      render: (a) =>
        a.error_code ? (
          <span>
            <code>{a.error_code}</code>
            <br />
            <span className="sx-muted">{a.error_message}</span>
          </span>
        ) : (
          '—'
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title={
          <span className="sx-row">
            {t('runs.detail.title', { code: r.pipeline.code })} <RunStatusPill status={r.status} />
          </span>
        }
        description={formatDateTime(r.created_at)}
        actions={
          <div className="sx-row">
            <Link to="/runs">{t('runs.detail.back')}</Link>
            {canRun && live && (
              <Button
                variant="secondary"
                disabled={r.cancel_requested_at !== null}
                onClick={() => {
                  setCancelling(true);
                }}
              >
                {t('runs.detail.cancel')}
              </Button>
            )}
            {canRun && r.status === 'failed' && (
              <Button
                disabled={retry.isPending}
                onClick={() => {
                  retry.mutate();
                }}
              >
                {t('runs.detail.retry')}
              </Button>
            )}
          </div>
        }
      />
      {retry.error && <Alert tone="danger">{describeError(retry.error).message}</Alert>}
      {r.cancel_requested_at !== null && live && (
        <Alert tone="warning" title={t('runs.detail.cancelRequested')}>
          {t('runs.detail.cancelRequestedHint')}
        </Alert>
      )}
      {r.error_code && (
        <Alert
          tone={r.status === 'failed' ? 'danger' : 'warning'}
          title={
            r.status === 'failed'
              ? t('runs.detail.failedTitle', { count: r.attempts.length, code: r.error_code })
              : t('runs.detail.errorTitle', { code: r.error_code })
          }
        >
          <p>{r.error_message}</p>
          {r.status === 'failed' && r.error_kind && <p>{t(`runs.errorNext.${r.error_kind}`)}</p>}
        </Alert>
      )}
      {r.status === 'queued' && r.attempts.length > 0 && (
        <Alert tone="info">
          {t('runs.detail.retryAt', { when: formatDateTime(r.available_at) })}
        </Alert>
      )}
      <IdleWorkersNotice queued={r.status === 'queued'} />

      <div className="sx-run__cards">
        <div className="sx-two-columns" style={{ alignItems: 'start' }}>
          <Card title={t('runs.detail.summaryTitle')}>
            <DetailList
              items={[
                {
                  label: t('runs.fields.pipeline'),
                  value: (
                    <Link to={`/pipelines/${r.pipeline.id}`}>
                      {r.pipeline.code}, {r.pipeline.name}
                    </Link>
                  ),
                },
                { label: t('runs.fields.mode'), value: mode(r.mode) },
                {
                  label: t('runs.fields.trigger'),
                  value: r.triggered_by
                    ? t(`runs.triggeredBy.${r.trigger}`, { user: r.triggered_by })
                    : t(`runs.triggers.${r.trigger}`),
                },
                { label: t('runs.fields.reason'), value: r.reason },
                {
                  label: t('runs.fields.priority'),
                  value: t(`pipelines.priorities.${r.priority}`),
                },
                {
                  label: t('runs.fields.failedAttempts'),
                  value: t('runs.detail.attemptsBudget', {
                    used: failedAttempts,
                    budget: r.attempts_budget,
                  }),
                },
              ]}
            />
          </Card>
          <Card title={t('runs.detail.timesTitle')}>
            <DetailList
              items={[
                { label: t('runs.fields.created'), value: formatDateTime(r.created_at) },
                ...(r.status === 'queued'
                  ? [{ label: t('runs.fields.availableAt'), value: formatDateTime(r.available_at) }]
                  : []),
                { label: t('runs.fields.started'), value: formatDateTime(r.started_at) },
                { label: t('runs.fields.finished'), value: formatDateTime(r.finished_at) },
                {
                  label: t('runs.fields.duration'),
                  value: formatDuration(r.started_at, r.finished_at),
                },
                ...(r.status === 'running'
                  ? [{ label: t('runs.fields.heartbeat'), value: formatDateTime(r.heartbeat_at) }]
                  : []),
              ]}
            />
          </Card>
        </div>

        <Card title={t('runs.detail.dataTitle')}>
          <div className="sx-stack">
            <div className="sx-run__metrics">
              <Metric label={t('runs.fields.rows')} value={rows(r.rows_extracted)} />
              <Metric label={t('runs.fields.inserted')} value={rows(r.rows_inserted)} />
              <Metric label={t('runs.fields.updated')} value={rows(r.rows_updated)} />
              <Metric label={t('runs.fields.deleted')} value={rows(r.rows_deleted)} />
            </div>
            <DetailList
              items={[
                {
                  label: t('runs.fields.batch'),
                  value: r.batch_id ? <code>{r.batch_id}</code> : null,
                },
                ...(r.delta_generation !== null
                  ? [{ label: t('runs.fields.generation'), value: String(r.delta_generation) }]
                  : []),
                ...(r.start_token
                  ? [
                      {
                        label: t('runs.fields.startToken'),
                        value: <span className="sx-run__token">{r.start_token}</span>,
                      },
                    ]
                  : []),
                ...(r.next_token
                  ? [
                      {
                        label: t('runs.fields.nextToken'),
                        value: <span className="sx-run__token">{r.next_token}</span>,
                      },
                    ]
                  : []),
                ...(r.window_end
                  ? [
                      {
                        label: t('runs.fields.window'),
                        value: t('runs.detail.window', {
                          from: r.window_start ?? '—',
                          to: r.window_end,
                        }),
                      },
                    ]
                  : []),
              ]}
            />
          </div>
        </Card>

        <Card title={t('runs.detail.logTitle')}>
          <RunLog key={r.id} runId={r.id} />
        </Card>

        <section>
          <h2 className="sx-card__title">{t('runs.detail.stepsTitle')}</h2>
          <DataTable
            caption={t('runs.detail.stepsTitle')}
            columns={stepColumns}
            rows={steps.data ?? []}
            rowKey={(s) => `${String(s.attempt)}-${s.step}-${s.started_at}`}
            empty={t('runs.detail.noSteps')}
            stacked
          />
        </section>

        <section>
          <h2 className="sx-card__title">{t('runs.detail.attemptsTitle')}</h2>
          <DataTable
            caption={t('runs.detail.attemptsTitle')}
            columns={attemptColumns}
            rows={r.attempts}
            rowKey={(a) => String(a.number)}
            empty={t('runs.detail.noAttempts')}
            stacked
          />
        </section>
      </div>

      <ConfirmDialog
        key={cancelling ? 'open' : 'closed'}
        open={cancelling}
        title={t('runs.cancel.title', { code: r.pipeline.code })}
        message={t(`runs.cancel.message.${r.status === 'running' ? 'running' : 'queued'}`)}
        confirmLabel={t('runs.detail.cancel')}
        cancelLabel={t('runs.cancel.keep')}
        busy={cancel.isPending}
        reasonLabel={t('runs.cancel.reason')}
        error={
          cancel.error ? (
            <Alert tone="danger">{describeError(cancel.error).message}</Alert>
          ) : undefined
        }
        onConfirm={(reason) => {
          cancel.mutate(reason, {
            onSuccess: () => {
              setCancelling(false);
            },
          });
        }}
        onClose={() => {
          cancel.reset();
          setCancelling(false);
        }}
      />
    </>
  );
}
