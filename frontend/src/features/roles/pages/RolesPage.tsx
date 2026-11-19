import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { Alert, DataTable, PageHeader, StatusPill, type Column } from '@/shared/ui';
import { useRoles, type Role } from '../api/queries';

export function RolesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const roles = useRoles();
  const canManage = useCan('role:manage');

  const columns: Column<Role>[] = [
    {
      key: 'name',
      header: t('roles.fields.name'),
      render: (r) => (
        <span>
          <strong>{r.name}</strong>
          <br />
          <span className="sx-muted">{r.description}</span>
        </span>
      ),
    },
    { key: 'code', header: t('roles.fields.code'), render: (r) => <code>{r.code}</code> },
    {
      key: 'type',
      header: t('roles.fields.type'),
      render: (r) =>
        r.is_system ? (
          <StatusPill tone="info">{t('roles.system')}</StatusPill>
        ) : (
          <StatusPill tone="neutral">{t('roles.custom')}</StatusPill>
        ),
    },
    {
      key: 'permissions',
      header: t('roles.fields.permissions'),
      align: 'right',
      render: (r) => r.permissions.length,
    },
    { key: 'users', header: t('roles.fields.users'), align: 'right', render: (r) => r.user_count },
  ];

  return (
    <>
      <PageHeader
        title={t('roles.title')}
        description={t('roles.description')}
        actions={
          canManage && (
            <Link to="/admin/roles/new" className="sx-btn sx-btn--primary">
              {t('roles.new')}
            </Link>
          )
        }
      />
      {roles.error && <Alert tone="danger">{describeError(roles.error).message}</Alert>}
      <DataTable
        caption={t('roles.title')}
        columns={columns}
        rows={roles.data ?? []}
        rowKey={(r) => r.id}
        loading={roles.isPending}
        onRowClick={(r) => void navigate(`/admin/roles/${r.id}`)}
      />
    </>
  );
}
