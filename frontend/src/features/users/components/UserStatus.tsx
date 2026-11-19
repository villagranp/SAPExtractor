import { useTranslation } from 'react-i18next';
import { StatusPill, type StatusTone } from '@/shared/ui';
import type { UserSummary } from '../api/queries';

const TONES: Record<UserSummary['status'], StatusTone> = {
  active: 'success',
  invited: 'info',
  locked: 'warning',
  disabled: 'neutral',
};

export function UserStatusPill({ user }: { user: UserSummary }) {
  const { t } = useTranslation();
  return (
    <span className="sx-row">
      <StatusPill tone={TONES[user.status]}>{t(`users.statuses.${user.status}`)}</StatusPill>
      {user.temporarily_locked && <StatusPill tone="warning">{t('users.tempLocked')}</StatusPill>}
    </span>
  );
}
