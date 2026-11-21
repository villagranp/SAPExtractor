import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { IconBell } from '@/shared/ui';
import { useAlertCounts } from '../api/queries';
import '../monitoring.css';

/** Campana de la cabecera: alertas abiertas, con enlace a la pantalla de alertas. */
export function AlertsBell() {
  const { t } = useTranslation();
  const canRead = useCan('dashboard:read');
  const counts = useAlertCounts(canRead);
  if (!canRead) return null;
  const open = counts.data?.open ?? 0;
  const label = open > 0 ? t('monitoring.bell.open', { count: open }) : t('monitoring.bell.none');
  return (
    <Link to="/alerts" className="sx-iconkey sx-bell" aria-label={label} title={label}>
      <IconBell size={18} aria-hidden="true" />
      {open > 0 && (
        <span className="sx-bell__count" aria-hidden="true">
          {open > 99 ? '99+' : open}
        </span>
      )}
    </Link>
  );
}
