import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { DeltaMethod, DeltaSemantics, SourceStatus, SourceType } from '../api/queries';

const STATUS_TONES: Record<SourceStatus, StatusTone> = {
  draft: 'neutral',
  active: 'success',
  deprecated: 'warning',
};

export function SourceStatusPill({ status }: { status: SourceStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={STATUS_TONES[status]}>{t(`sources.statuses.${status}`)}</StatusPill>;
}

export function SourceTypeLabel({ type }: { type: SourceType }) {
  const { t } = useTranslation();
  return <span>{t(`sources.types.${type}`)}</span>;
}

/** «Delta ODQ · imagen posterior», «Marca de agua · CHANGED_AT» o «Solo completa». */
export function DeltaSummary({
  method,
  semantics,
  watermarkField,
}: {
  method: DeltaMethod;
  semantics: DeltaSemantics | null;
  watermarkField?: string | null;
}) {
  const { t } = useTranslation();
  if (method === 'ODQ') {
    return (
      <span className="sx-stack" style={{ gap: 'var(--sx-space-1)', justifyItems: 'start' }}>
        <StatusPill tone="success">{t('sources.deltaMethods.ODQ')}</StatusPill>
        <span className="sx-muted">{t(`sources.semantics.${semantics ?? 'unknown'}`)}</span>
      </span>
    );
  }
  if (method === 'WATERMARK') {
    return (
      <span className="sx-stack" style={{ gap: 'var(--sx-space-1)', justifyItems: 'start' }}>
        <StatusPill tone="info">{t('sources.deltaMethods.WATERMARK')}</StatusPill>
        {watermarkField && <code className="sx-muted">{watermarkField}</code>}
      </span>
    );
  }
  return <span className="sx-muted">{t('sources.deltaMethods.NONE')}</span>;
}

export function EnvironmentPill({ environment }: { environment: string }) {
  return (
    <StatusPill tone={environment === 'PRD' ? 'warning' : 'neutral'}>{environment}</StatusPill>
  );
}
