import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/shared/format';
import { Alert, Checklist, type StatusTone } from '@/shared/ui';
import type { TestReport, TestStep } from '../api/queries';

const TONES: Record<TestStep['status'], StatusTone> = {
  ok: 'success',
  warning: 'warning',
  failed: 'danger',
  skipped: 'neutral',
};

function stepMeta(step: TestStep): string | null {
  if (step.status === 'skipped') return null;
  const code = step.error_code ? ` · ${step.error_code}` : '';
  return `${String(step.duration_ms)} ms${code}`;
}

/** Detalles técnicos útiles para diagnosticar (sin secretos: el backend nunca los envía). */
function StepDetails({ step }: { step: TestStep }) {
  const { t } = useTranslation();
  const d = step.details as Record<string, unknown>;
  const lines: string[] = [];
  if (Array.isArray(d.addresses)) lines.push(`IP: ${(d.addresses as string[]).join(', ')}`);
  const cert = (d.certificate ?? d.presented_certificate) as Record<string, string> | undefined;
  if (cert?.subject) {
    lines.push(
      t('sapSystems.report.certificate', {
        subject: cert.subject,
        issuer: cert.issuer,
        date: formatDateTime(cert.not_after),
      }),
    );
  }
  if (typeof d.sap_message === 'string') lines.push(`SAP: ${d.sap_message}`);
  if (typeof d.transaction_id === 'string') lines.push(`Transaction ID: ${d.transaction_id}`);
  if (!lines.length) return null;
  return (
    <ul className="sx-muted" style={{ margin: 'var(--sx-space-1) 0 0', paddingLeft: '1.2em' }}>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

export function TestReportView({
  report,
  outdated = false,
}: {
  report: TestReport;
  outdated?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="sx-stack" data-testid="test-report">
      <Alert
        tone={report.ok ? 'success' : 'danger'}
        title={report.ok ? t('sapSystems.report.ok') : t('sapSystems.report.failed')}
      >
        {report.summary}
        <div className="sx-muted">
          {t('sapSystems.report.testedAt', { date: formatDateTime(report.finished_at) })}
        </div>
      </Alert>
      {outdated && <Alert tone="warning">{t('sapSystems.report.outdated')}</Alert>}
      <Checklist
        label={t('sapSystems.report.title')}
        items={report.steps.map((step) => ({
          id: step.name,
          title: t(`sapSystems.report.steps.${step.name}`),
          tone: TONES[step.status],
          statusLabel: t(`sapSystems.report.statuses.${step.status}`),
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
