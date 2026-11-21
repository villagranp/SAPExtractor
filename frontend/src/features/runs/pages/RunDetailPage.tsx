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
  useRun,
  useRunSteps,
  type RunAttempt,
  type RunStep,
} from '../api/queries';
import { OutcomePill, RunLog, RunStatusPill } from '../components/RunParts';
import { useModeLabel, useNumbers } from '../format';
import '../runs.css';

export function RunDetailPage() {
  const { t } = useTranslation();
  const { runId = '' } = useParams();
  const run = useRun(runId);
  const steps = useRunSteps(runId, isActive(run.data?.status));
  const cancel = useCancelRun(runId);
  const canRun = useCan('pipeline:run');
  const mode = useModeLabel();
  const { rows, seconds } = useNumbers();
  const [confirming, setConfirming] = useState(false);

  if (run.isPending) return <Spinner label={t('common.loading')} />;
  if (run.error) return <Alert tone="danger">{describeError(run.error).message}</Alert>;
  const r = run.data;
  const live = isActive(r.status);

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
    { key: 'worker', header: t('runs.fields.worker'), render: (a) => <code>{a.worker_id}</code> },
    {
      key: 'outcome',
      header: t('runs.fields.outcome'),
      render: (a) => <OutcomePill outcome={a.outcome} />,
    },
    {
      key: 'spool',
      header: t('runs.fields.spool'),
      render: (a) => (a.has_spool ? t('common.yes') : t('common.no')),
    },
    {
      key: 'error',
      header: t('runs.fields.error'),
      render: (a) => (a.error_code ? <code>{a.error_code}</code> : '—'),
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow={r.pipeline.code}
        title={
          <span className="sx-row">
            {mode(r.mode)} <RunStatusPill status={r.status} />
          </span>
        }
        description={t('runs.detail.requestedBy', {
          user: r.triggered_by ?? '—',
          when: formatDateTime(r.created_at),
        })}
        actions={
          <div className="sx-row">
            <Link to={`/pipelines/${r.pipeline.id}`}>{t('runs.detail.toPipeline')}</Link>
            {canRun && live && (
              <Button
                variant="danger"
                disabled={r.cancel_requested_at !== null}
                onClick={() => {
                  setConfirming(true);
                }}
              >
                {r.cancel_requested_at !== null
                  ? t('runs.detail.cancelling')
                  : t('runs.detail.cancel')}
              </Button>
            )}
          </div>
        }
      />
      {r.status === 'failed' && r.error_message && (
        <Alert tone="danger" title={r.error_code ?? undefined}>
          {r.error_message}
        </Alert>
      )}
      {r.status === 'queued' && r.attempts.length > 0 && (
        <Alert tone="info">
          {t('runs.detail.retryAt', { when: formatDateTime(r.available_at) })}
        </Alert>
      )}
      <div className="sx-run__cards">
        <Card title={t('runs.detail.summaryTitle')}>
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
              {
                label: t('runs.fields.attempts'),
                value: String(r.attempts.length),
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
              { label: t('runs.fields.started'), value: formatDateTime(r.started_at) },
              { label: t('runs.fields.finished'), value: formatDateTime(r.finished_at) },
            ]}
          />
        </Card>
        <Card title={t('runs.detail.logTitle')}>
          <RunLog key={r.id} runId={r.id} />
        </Card>
        <Card title={t('runs.detail.stepsTitle')}>
          <DataTable
            caption={t('runs.detail.stepsTitle')}
            columns={stepColumns}
            rows={steps.data ?? []}
            rowKey={(s) => `${s.attempt}-${s.step}-${s.started_at}`}
            empty={t('runs.detail.noSteps')}
          />
        </Card>
        <Card title={t('runs.detail.attemptsTitle')}>
          <DataTable
            caption={t('runs.detail.attemptsTitle')}
            columns={attemptColumns}
            rows={r.attempts}
            rowKey={(a) => String(a.number)}
            empty={t('runs.detail.noAttempts')}
          />
        </Card>
      </div>
      <ConfirmDialog
        open={confirming}
        title={t('runs.detail.cancelTitle')}
        message={t('runs.detail.cancelMessage')}
        confirmLabel={t('runs.detail.cancel')}
        cancelLabel={t('common.close')}
        danger
        busy={cancel.isPending}
        error={
          cancel.error ? (
            <Alert tone="danger">{describeError(cancel.error).message}</Alert>
          ) : undefined
        }
        onConfirm={() => {
          cancel.mutate(undefined, {
            onSuccess: () => {
              setConfirming(false);
            },
          });
        }}
        onClose={() => {
          cancel.reset();
          setConfirming(false);
        }}
      />
    </>
  );
}
