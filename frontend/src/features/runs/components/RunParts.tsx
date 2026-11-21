import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, StatusPill, type StatusTone } from '@/shared/ui';
import { useRunLogs, type AttemptOutcome, type DeltaStatus, type RunStatus } from '../api/queries';

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
