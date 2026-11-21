import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { formatDateTime } from '@/shared/format';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { AlertStatus, FreshnessRow, Severity, TimelineRun } from '../api/queries';

const SEVERITY_TONES: Record<Severity, StatusTone> = {
  critical: 'danger',
  warning: 'warning',
  info: 'info',
};

const STATUS_TONES: Record<AlertStatus, StatusTone> = {
  open: 'danger',
  acknowledged: 'warning',
  resolved: 'success',
};

const FRESHNESS_TONES: Record<string, StatusTone> = {
  ok: 'success',
  warning: 'warning',
  late: 'danger',
  no_sla: 'neutral',
  unknown: 'neutral',
};

export function SeverityPill({ severity }: { severity: Severity }) {
  const { t } = useTranslation();
  return (
    <StatusPill tone={SEVERITY_TONES[severity]}>
      {t(`monitoring.severities.${severity}`)}
    </StatusPill>
  );
}

export function AlertStatusPill({ status }: { status: AlertStatus }) {
  const { t } = useTranslation();
  return (
    <StatusPill tone={STATUS_TONES[status]}>{t(`monitoring.alertStatuses.${status}`)}</StatusPill>
  );
}

export function FreshnessPill({ row }: { row: FreshnessRow }) {
  const { t } = useTranslation();
  return (
    <StatusPill tone={FRESHNESS_TONES[row.state] ?? 'neutral'}>
      {t(`monitoring.freshness.states.${row.state}`)}
    </StatusPill>
  );
}

/**
 * Línea de tiempo de los runs de las últimas horas, una fila por pipeline (plan 09 §1).
 * Cada barra es un enlace al run; el color marca el estado y el texto lo repite para lectores.
 */
export function Timeline({ runs, hours, now }: { runs: TimelineRun[]; hours: number; now: Date }) {
  const { t } = useTranslation();
  const end = now.getTime();
  const start = end - hours * 3_600_000;
  const span = end - start;
  const byPipeline = new Map<string, TimelineRun[]>();
  for (const run of runs) {
    const list = byPipeline.get(run.pipeline_code) ?? [];
    list.push(run);
    byPipeline.set(run.pipeline_code, list);
  }
  if (byPipeline.size === 0) {
    return <p className="sx-muted">{t('monitoring.timeline.empty')}</p>;
  }
  return (
    <div className="sx-timeline" role="list" aria-label={t('monitoring.timeline.title')}>
      {[...byPipeline.entries()].map(([code, items]) => (
        <div key={code} className="sx-timeline__row" role="listitem">
          <span className="sx-timeline__label">{code}</span>
          <span className="sx-timeline__track">
            {items.map((run) => {
              const from = Math.max(new Date(run.started_at).getTime(), start);
              const to = run.finished_at ? new Date(run.finished_at).getTime() : end;
              const left = ((from - start) / span) * 100;
              const width = Math.max(((to - from) / span) * 100, 0.4);
              const label = t('monitoring.timeline.bar', {
                pipeline: code,
                mode: t(`runs.modes.${run.mode}`),
                status: t(`runs.statuses.${run.status}`),
                from: formatDateTime(run.started_at),
                to: run.finished_at
                  ? formatDateTime(run.finished_at)
                  : t('monitoring.timeline.now'),
              });
              return (
                <Link
                  key={run.id}
                  to={`/runs/${run.id}`}
                  className={`sx-timeline__bar sx-timeline__bar--${run.status}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  aria-label={label}
                  title={label}
                />
              );
            })}
          </span>
        </div>
      ))}
    </div>
  );
}
