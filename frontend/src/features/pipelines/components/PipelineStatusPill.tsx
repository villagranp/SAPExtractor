import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { PipelineStatus } from '../api/queries';

const TONES: Record<PipelineStatus, StatusTone> = {
  draft: 'info',
  active: 'success',
  paused: 'warning',
};

export function PipelineStatusPill({ status }: { status: PipelineStatus }) {
  const { t } = useTranslation();
  return <StatusPill tone={TONES[status]}>{t(`pipelines.statuses.${status}`)}</StatusPill>;
}
