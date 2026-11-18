import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { EmptyState } from '@/shared/ui';

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <EmptyState title={t('notFound.title')} action={<Link to="/">{t('notFound.back')}</Link>}>
      <p>{t('notFound.body')}</p>
    </EmptyState>
  );
}
