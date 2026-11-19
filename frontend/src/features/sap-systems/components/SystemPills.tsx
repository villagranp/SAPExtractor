import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { SapSystemSummary, SystemStatus } from '../api/queries';

const STATUS_TONES: Record<SystemStatus, StatusTone> = {
  active: 'success',
  draft: 'info',
  inactive: 'neutral',
};

export function SystemStatusPill({ status }: { status: SystemStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={STATUS_TONES[status]}>{t(`sapSystems.statuses.${status}`)}</StatusPill>;
}

/** Semáforo de la última prueba: ● verde/rojo, o pendiente si nunca se probó o cambió. */
export function LastTestPill({ system }: { system: SapSystemSummary }) {
  const { t } = useTranslation();
  if (system.last_test_at === null || system.last_test_ok === null) {
    return <StatusPill tone="neutral">{t('sapSystems.test.never')}</StatusPill>;
  }
  if (system.test_outdated) {
    return <StatusPill tone="warning">{t('sapSystems.test.outdated')}</StatusPill>;
  }
  return system.last_test_ok ? (
    <StatusPill tone="success">● {t('sapSystems.test.ok')}</StatusPill>
  ) : (
    <StatusPill tone="danger">● {t('sapSystems.test.failed')}</StatusPill>
  );
}
