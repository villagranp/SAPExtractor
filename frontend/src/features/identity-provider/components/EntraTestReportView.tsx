import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/shared/format';
import { Alert, Checklist, type StatusTone } from '@/shared/ui';
import type { EntraTestReport, EntraTestStep } from '../api/queries';

const TONES: Record<EntraTestStep['status'], StatusTone> = {
  ok: 'success',
  warning: 'warning',
  failed: 'danger',
  skipped: 'neutral',
};

/** Datos para que IT busque el error en los registros de inicio de sesión de Entra. */
function StepDetails({ step }: { step: EntraTestStep }) {
  const { t } = useTranslation();
  const d = step.details as Record<string, unknown>;
  const lines: string[] = [];
  if (typeof d.issuer === 'string')
    lines.push(t('identityProvider.report.issuer', { value: d.issuer }));
  if (typeof d.correlation_id === 'string')
    lines.push(t('identityProvider.report.correlation', { value: d.correlation_id }));
  if (typeof d.trace_id === 'string')
    lines.push(t('identityProvider.report.trace', { value: d.trace_id }));
  if (!lines.length) return null;
  return (
    <ul className="sx-muted sx-idp__details">
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

export function EntraTestReportView({
  report,
  outdated = false,
}: {
  report: EntraTestReport;
  outdated?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="sx-stack" data-testid="entra-test-report">
      <Alert
        tone={report.ok ? 'success' : 'danger'}
        title={report.ok ? t('identityProvider.report.ok') : t('identityProvider.report.failed')}
      >
        {report.summary}
        <div className="sx-muted">
          {t('identityProvider.report.testedAt', { date: formatDateTime(report.finished_at) })}
        </div>
      </Alert>
      {outdated && <Alert tone="warning">{t('identityProvider.report.outdated')}</Alert>}
      <Checklist
        label={t('identityProvider.report.title')}
        items={report.steps.map((step) => ({
          id: step.name,
          title: t(`identityProvider.report.steps.${step.name}`),
          tone: TONES[step.status],
          statusLabel: t(`identityProvider.report.statuses.${step.status}`),
          description: (
            <>
              <span>{step.message}</span>
              <StepDetails step={step} />
            </>
          ),
          meta:
            step.status === 'skipped'
              ? null
              : (step.error_code ?? `${String(step.duration_ms)} ms`),
        }))}
      />
    </div>
  );
}
