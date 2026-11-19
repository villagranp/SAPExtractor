import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { useRoles } from '@/features/roles/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  DataTable,
  FilterBar,
  PageHeader,
  Pagination,
  SelectField,
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import { useUsers, type AuthSource, type UserStatus, type UserSummary } from '../api/queries';
import { CreateUserDialog } from '../components/CreateUserDialog';
import { UserStatusPill } from '../components/UserStatus';

const PAGE_SIZE = 25;

export function UsersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canCreate = useCan('user:create');
  const roles = useRoles();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<UserStatus | ''>('');
  const [authSource, setAuthSource] = useState<AuthSource | ''>('');
  const [roleId, setRoleId] = useState('');
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);

  const users = useUsers({
    page,
    page_size: PAGE_SIZE,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(status ? { status } : {}),
    ...(authSource ? { auth_source: authSource } : {}),
    ...(roleId ? { role_id: roleId } : {}),
  });

  const filter =
    <T,>(setter: (value: T) => void) =>
    (value: T) => {
      setter(value);
      setPage(1);
    };

  const columns: Column<UserSummary>[] = [
    {
      key: 'user',
      header: t('users.fields.username'),
      render: (u) => (
        <span>
          <strong>{u.display_name}</strong>
          <br />
          <span className="sx-muted">
            {u.username}
            {u.is_break_glass && ` · ${t('users.breakGlass')}`}
          </span>
        </span>
      ),
    },
    { key: 'email', header: t('users.fields.email'), render: (u) => u.email ?? '—' },
    {
      key: 'roles',
      header: t('users.fields.roles'),
      render: (u) => u.roles.map((r) => r.name).join(', ') || '—',
    },
    {
      key: 'source',
      header: t('users.fields.authSource'),
      render: (u) => (
        <span className="sx-row">
          {t(`users.authSources.${u.auth_source}`)}
          {u.mfa_enabled && <StatusPill tone="info">MFA</StatusPill>}
        </span>
      ),
    },
    { key: 'status', header: t('users.fields.status'), render: (u) => <UserStatusPill user={u} /> },
    {
      key: 'last',
      header: t('users.fields.lastLogin'),
      render: (u) => formatDateTime(u.last_login_at),
    },
  ];

  return (
    <>
      <PageHeader
        title={t('users.title')}
        description={t('users.description')}
        actions={
          canCreate && (
            <Button
              onClick={() => {
                setCreating(true);
              }}
            >
              {t('users.new')}
            </Button>
          )
        }
      />
      <FilterBar>
        <TextField
          label={t('common.search')}
          type="search"
          value={q}
          placeholder={t('users.searchPlaceholder')}
          onChange={(e) => {
            filter(setQ)(e.target.value);
          }}
        />
        <SelectField
          label={t('users.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            filter(setStatus)(e.target.value as UserStatus | '');
          }}
          options={(['active', 'invited', 'locked', 'disabled'] as const).map((s) => ({
            value: s,
            label: t(`users.statuses.${s}`),
          }))}
        />
        <SelectField
          label={t('users.fields.authSource')}
          placeholder={t('common.all')}
          value={authSource}
          onChange={(e) => {
            filter(setAuthSource)(e.target.value as AuthSource | '');
          }}
          options={(['local', 'entra'] as const).map((s) => ({
            value: s,
            label: t(`users.authSources.${s}`),
          }))}
        />
        <SelectField
          label={t('users.fields.role')}
          placeholder={t('common.all')}
          value={roleId}
          onChange={(e) => {
            filter(setRoleId)(e.target.value);
          }}
          options={(roles.data ?? []).map((r) => ({ value: r.id, label: r.name }))}
        />
      </FilterBar>
      {users.error && <Alert tone="danger">{describeError(users.error).message}</Alert>}
      <DataTable
        caption={t('users.title')}
        columns={columns}
        rows={users.data?.items ?? []}
        rowKey={(u) => u.id}
        loading={users.isPending}
        empty={t('users.empty')}
        onRowClick={(u) => void navigate(`/admin/users/${u.id}`)}
      />
      {users.data && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={users.data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
      <CreateUserDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
      />
    </>
  );
}
