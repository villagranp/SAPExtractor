import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation } from 'react-router';
import { useMe } from '@/features/auth/api/queries';
import { Alert, EmptyState, Spinner } from '@/shared/ui';

/** Exige una sesión completa; si no, envía al login conservando la ruta pedida. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const me = useMe();
  const location = useLocation();

  if (me.isPending) return <Spinner label={t('common.loading')} />;
  if (me.isError) {
    return (
      <main style={{ padding: 'var(--sx-space-6)' }}>
        <Alert tone="danger">{t('login.apiUnavailable')}</Alert>
      </main>
    );
  }
  if (me.data?.stage !== 'active') {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}

/** Oculta una pantalla sin el permiso. La API devolvería 403 igualmente. */
export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const me = useMe();
  if (!me.data?.permissions.includes(permission)) {
    return <EmptyState title={t('forbidden.title')}>{t('forbidden.body')}</EmptyState>;
  }
  return children;
}
