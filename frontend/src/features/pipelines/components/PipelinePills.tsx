import { useTranslation } from 'react-i18next';
import { Alert, StatusPill, type StatusTone } from '@/shared/ui';
import type { Finding, PipelineStatus } from '../api/queries';

const STATUS_TONES: Record<PipelineStatus, StatusTone> = {
  active: 'success',
  paused: 'warning',
  draft: 'info',
};

export function PipelineStatusPill({ status }: { status: PipelineStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={STATUS_TONES[status]}>{t(`pipelines.statuses.${status}`)}</StatusPill>;
}

/**
 * Avisos y bloqueos de la configuración (plan 08 §6, validación en vivo). Los bloqueos
 * impiden activar; los avisos solo informan. El texto lo redacta la API.
 */
function FindingMessages({ findings }: { findings: Finding[] }) {
  const [only] = findings;
  if (findings.length === 1 && only) return <>{only.message}</>;
  return (
    <ul>
      {findings.map((f) => (
        <li key={f.code}>{f.message}</li>
      ))}
    </ul>
  );
}

export function FindingsList({ findings }: { findings: Finding[] }) {
  const { t } = useTranslation();
  const errors = findings.filter((f) => f.severity === 'error');
  const warnings = findings.filter((f) => f.severity === 'warning');
  if (findings.length === 0) return null;
  return (
    <div className="sx-pipelines__findings">
      {errors.length > 0 && (
        <Alert tone="danger" title={t('pipelines.findings.errors', { count: errors.length })}>
          <FindingMessages findings={errors} />
        </Alert>
      )}
      {warnings.length > 0 && (
        // Un solo aviso se lee tal cual; el título solo ayuda cuando hay varios.
        <Alert
          tone="warning"
          {...(warnings.length > 1
            ? { title: t('pipelines.findings.warnings', { count: warnings.length }) }
            : {})}
        >
          <FindingMessages findings={warnings} />
        </Alert>
      )}
    </div>
  );
}
