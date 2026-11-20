import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/shared/format';
import { Alert, Checklist, type StatusTone } from '@/shared/ui';
import type { CheckReport, CheckStep } from '../api/queries';

const TONES: Record<string, StatusTone> = {
  ok: 'success',
  warning: 'warning',
  failed: 'danger',
  skipped: 'neutral',
};

function stepMeta(step: CheckStep): string | null {
  if (step.status === 'skipped') return null;
  const code = step.error_code ? ` · ${step.error_code}` : '';
  return `${String(step.duration_ms)} ms${code}`;
}

/** Datos técnicos de diagnóstico (el backend nunca envía secretos). */
function StepDetails({ step }: { step: CheckStep }) {
  const d = step.details as Record<string, unknown>;
  const lines = Object.entries(d)
    .filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
    .map(([k, v]) => `${k}: ${String(v)}`);
  if (Array.isArray(d.addresses)) lines.push(`IP: ${(d.addresses as string[]).join(', ')}`);
  if (Array.isArray(d.missing)) lines.push((d.missing as string[]).join(', '));
  if (!lines.length) return null;
  return (
    <ul className="sx-muted" style={{ margin: 'var(--sx-space-1) 0 0', paddingLeft: '1.2em' }}>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

export function CheckReportView({
  report,
  outdated = false,
}: {
  report: CheckReport;
  outdated?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="sx-stack" data-testid="test-report">
      <Alert
        tone={report.ok ? 'success' : 'danger'}
        title={report.ok ? t('destinations.report.ok') : t('destinations.report.failed')}
      >
        {report.summary}
        <div className="sx-muted">
          {t('destinations.report.testedAt', { date: formatDateTime(report.finished_at) })}
        </div>
      </Alert>
      {outdated && <Alert tone="warning">{t('destinations.report.outdated')}</Alert>}
      <Checklist
        label={t('destinations.report.title')}
        items={report.steps.map((step) => ({
          id: step.name,
          title: t(`destinations.report.steps.${step.name}`, { defaultValue: step.name }),
          tone: TONES[step.status] ?? 'neutral',
          statusLabel: t(`destinations.report.statuses.${step.status}`, {
            defaultValue: step.status,
          }),
          description: (
            <>
              <span>{step.message}</span>
              <StepDetails step={step} />
            </>
          ),
          meta: stepMeta(step),
        }))}
      />
    </div>
  );
}
