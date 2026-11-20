import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { DestinationStatus, DestinationSummary } from '../api/queries';

const STATUS_TONES: Record<DestinationStatus, StatusTone> = {
  active: 'success',
  draft: 'info',
  inactive: 'neutral',
};

export function DestinationStatusPill({ status }: { status: DestinationStatus }) {
  const { t } = useTranslation();
  return (
    <StatusPill tone={STATUS_TONES[status]}>{t(`destinations.statuses.${status}`)}</StatusPill>
  );
}

/** Semáforo de la última prueba: verde/rojo, o pendiente si nunca se probó o cambió. */
export function LastTestPill({ destination }: { destination: DestinationSummary }) {
  const { t } = useTranslation();
  if (destination.last_test_at === null || destination.last_test_ok === null) {
    return <StatusPill tone="neutral">{t('destinations.test.never')}</StatusPill>;
  }
  if (destination.test_outdated) {
    return <StatusPill tone="warning">{t('destinations.test.outdated')}</StatusPill>;
  }
  return destination.last_test_ok ? (
    <StatusPill tone="success">{t('destinations.test.ok')}</StatusPill>
  ) : (
    <StatusPill tone="danger">{t('destinations.test.failed')}</StatusPill>
  );
}
