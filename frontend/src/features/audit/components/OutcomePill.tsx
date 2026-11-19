import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { AuditOutcome } from '../api/queries';

const TONES: Record<AuditOutcome, StatusTone> = {
  success: 'success',
  failure: 'danger',
  denied: 'warning',
};

export function OutcomePill({ outcome }: { outcome: AuditOutcome }) {
  const { t } = useTranslation();
  return <StatusPill tone={TONES[outcome]}>{t(`audit.outcomes.${outcome}`)}</StatusPill>;
}
