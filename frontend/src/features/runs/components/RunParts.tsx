import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, DataTable, StatusPill, type Column, type StatusTone } from '@/shared/ui';
import {
  useRunLogs,
  type AttemptOutcome,
  type DeltaStatus,
  type RunStatus,
  type RunSummary,
} from '../api/queries';
import { useModeLabel, useNumbers } from '../format';

const RUN_TONES: Record<RunStatus, StatusTone> = {
  queued: 'neutral',
  running: 'info',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'warning',
};

const DELTA_TONES: Record<DeltaStatus, StatusTone> = {
  NOT_INITIALIZED: 'neutral',
  INITIALIZING: 'info',
  INITIALIZED: 'success',
  BROKEN: 'danger',
};

const OUTCOME_TONES: Record<AttemptOutcome, StatusTone> = {
  succeeded: 'success',
  failed_transient: 'warning',
  failed: 'danger',
  stale: 'warning',
  fenced: 'neutral',
  interrupted: 'neutral',
  cancelled: 'warning',
};

export function RunStatusPill({ status }: { status: RunStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={RUN_TONES[status]}>{t(`runs.statuses.${status}`)}</StatusPill>;
}

export function DeltaStatusPill({ status }: { status: DeltaStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={DELTA_TONES[status]}>{t(`runs.deltaStatuses.${status}`)}</StatusPill>;
}

export function OutcomePill({ outcome }: { outcome: AttemptOutcome | null }) {
  const { t } = useTranslation();
  if (outcome === null) return <StatusPill tone="info">{t('runs.statuses.running')}</StatusPill>;
  return <StatusPill tone={OUTCOME_TONES[outcome]}>{t(`runs.outcomes.${outcome}`)}</StatusPill>;
}

export function RunsTable({
  runs,
  loading,
  showPipeline,
  empty,
}: {
  runs: RunSummary[];
  loading: boolean;
  showPipeline: boolean;
  empty: string;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const mode = useModeLabel();
  const { rows } = useNumbers();
  const columns: Column<RunSummary>[] = [
    ...(showPipeline
      ? [
          {
            key: 'pipeline',
            header: t('runs.fields.pipeline'),
            render: (r: RunSummary) => <strong>{r.pipeline_code}</strong>,
          },
        ]
      : []),
    { key: 'mode', header: t('runs.fields.mode'), render: (r) => mode(r.mode) },
    {
      key: 'status',
      header: t('runs.fields.status'),
      render: (r) => <RunStatusPill status={r.status} />,
    },
    {
      key: 'rows',
      header: t('runs.fields.rows'),
      render: (r) => rows(r.rows_extracted),
    },
    {
      key: 'attempts',
      header: t('runs.fields.attempts'),
      render: (r) => t('runs.attemptsOf', { used: r.attempts_used, max: r.max_attempts }),
    },
    {
      key: 'created',
      header: t('runs.fields.requested'),
      render: (r) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <span>{formatDateTime(r.created_at)}</span>
          <span className="sx-muted">{r.triggered_by ?? '—'}</span>
        </span>
      ),
    },
    {
      key: 'finished',
      header: t('runs.fields.finished'),
      render: (r) => formatDateTime(r.finished_at),
    },
  ];
  return (
    <DataTable
      caption={t('runs.title')}
      columns={columns}
      rows={runs}
      rowKey={(r) => r.id}
      loading={loading}
      empty={empty}
      onRowClick={(r) => void navigate(`/runs/${r.id}`)}
    />
  );
}

/** Log de la ejecución: se actualiza solo mientras el run sigue vivo. */
export function RunLog({ runId }: { runId: string }) {
  const { t } = useTranslation();
  const { lines, error, finished } = useRunLogs(runId);
  return (
    <div className="sx-stack">
      {error && <Alert tone="danger">{describeError(error).message}</Alert>}
      <ol className="sx-run-log" aria-label={t('runs.detail.logTitle')} aria-live="polite">
        {lines.map((line) => (
          <li
            key={line.id}
            className={`sx-run-log__line sx-run-log__line--${line.level.toLowerCase()}`}
          >
            <time dateTime={line.ts}>{formatDateTime(line.ts)}</time>
            <span className="sx-run-log__step">
              {line.step ? t(`runs.steps.${line.step}`) : t('runs.detail.general')}
            </span>
            <span>{line.message}</span>
          </li>
        ))}
      </ol>
      {lines.length === 0 && <p className="sx-muted">{t('runs.detail.noLog')}</p>}
      {!finished && <p className="sx-muted">{t('runs.detail.live')}</p>}
    </div>
  );
}
