import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan, useMe } from '@/features/auth/api/queries';
import { useRoles } from '@/features/roles/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  DataTable,
  DetailList,
  Dialog,
  FieldErrors,
  OneTimeSecret,
  PageHeader,
  Spinner,
  Tabs,
  TextField,
} from '@/shared/ui';
import {
  useResetPassword,
  useSetUserRoles,
  useUpdateUser,
  useUser,
  useUserAction,
  useUserSessions,
  type UserAction,
  type UserDetail,
} from '../api/queries';
import { UserStatusPill } from '../components/UserStatus';

type Tab = 'data' | 'roles' | 'sessions' | 'activity';

interface ActionDef {
  action: UserAction;
  permission: string;
  danger?: boolean;
  reason?: 'optional' | 'required';
  visible: (u: UserDetail) => boolean;
}

const ACTIONS: ActionDef[] = [
  {
    action: 'unlock',
    permission: 'user:update',
    visible: (u) => u.status === 'locked' || u.temporarily_locked,
  },
  {
    action: 'lock',
    permission: 'user:update',
    reason: 'optional',
    visible: (u) => u.status === 'active',
  },
  { action: 'reset-mfa', permission: 'user:update', visible: (u) => u.mfa_enabled },
  { action: 'revoke-sessions', permission: 'session:revoke', visible: () => true },
  { action: 'enable', permission: 'user:disable', visible: (u) => u.status === 'disabled' },
  {
    action: 'disable',
    permission: 'user:disable',
    danger: true,
    reason: 'optional',
    visible: (u) => u.status !== 'disabled',
  },
  {
    action: 'anonymize',
    permission: 'user:delete',
    danger: true,
    reason: 'required',
    visible: (u) => u.status === 'disabled',
  },
];

function EditUserForm({ user }: { user: UserDetail }) {
  const { t } = useTranslation();
  const update = useUpdateUser(user.id);
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email ?? '');
  const error = update.error ? describeError(update.error) : null;
  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    update.mutate({ display_name: displayName.trim(), email: email.trim(), version: user.version });
  };
  return (
    <form onSubmit={submit} noValidate>
      {error && !Object.keys(error.fields).length && <Alert tone="danger">{error.message}</Alert>}
      {update.isSuccess && <Alert tone="success">{t('common.saved')}</Alert>}
      <TextField
        label={t('users.fields.displayName')}
        value={displayName}
        onChange={(e) => {
          setDisplayName(e.target.value);
        }}
      />
      <TextField
        label={t('users.fields.email')}
        type="email"
        value={email}
        onChange={(e) => {
          setEmail(e.target.value);
        }}
      />
      <FieldErrors errors={error?.fields.email} />
      <Button type="submit" disabled={update.isPending || !displayName.trim()}>
        {t('common.save')}
      </Button>
    </form>
  );
}

function RolesTab({ user, editable }: { user: UserDetail; editable: boolean }) {
  const { t } = useTranslation();
  const roles = useRoles();
  const save = useSetUserRoles(user.id);
  const [selected, setSelected] = useState<string[]>(user.roles.map((r) => r.id));
  const error = save.error ? describeError(save.error) : null;
  return (
    <Card title={t('users.detail.rolesTitle')}>
      <p className="sx-muted">{t('users.detail.rolesHelp')}</p>
      {error && <Alert tone="danger">{error.message}</Alert>}
      {save.isSuccess && <Alert tone="success">{t('common.saved')}</Alert>}
      {(roles.data ?? []).map((role) => (
        <Checkbox
          key={role.id}
          label={role.name}
          description={role.description}
          disabled={!editable}
          checked={selected.includes(role.id)}
          onChange={(e) => {
            setSelected((ids) =>
              e.target.checked ? [...ids, role.id] : ids.filter((id) => id !== role.id),
            );
          }}
        />
      ))}
      {editable && (
        <Button
          onClick={() => {
            save.mutate(selected);
          }}
          disabled={save.isPending}
        >
          {t('users.detail.saveRoles')}
        </Button>
      )}
    </Card>
  );
}

function SessionsTab({ user }: { user: UserDetail }) {
  const { t } = useTranslation();
  const sessions = useUserSessions(user.id, true);
  return (
    <DataTable
      caption={t('users.detail.tabs.sessions')}
      columns={[
        { key: 'start', header: t('profile.started'), render: (s) => formatDateTime(s.created_at) },
        {
          key: 'seen',
          header: t('profile.lastSeen'),
          render: (s) => formatDateTime(s.last_seen_at),
        },
        { key: 'ip', header: t('profile.ip'), render: (s) => s.ip ?? '—' },
        {
          key: 'agent',
          header: t('profile.device'),
          render: (s) => <span className="sx-muted">{s.user_agent ?? '—'}</span>,
        },
      ]}
      rows={sessions.data ?? []}
      rowKey={(s) => s.id}
      loading={sessions.isPending}
      empty={t('profile.sessionsEmpty')}
    />
  );
}

export function UserDetailPage() {
  const { t } = useTranslation();
  const { userId = '' } = useParams();
  const me = useMe().data;
  const user = useUser(userId);
  const action = useUserAction(userId);
  const resetPassword = useResetPassword(userId);
  const [tab, setTab] = useState<Tab>('data');
  const [pending, setPending] = useState<ActionDef | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const canEdit = useCan('user:update');
  const can = (permission: string) => me?.permissions.includes(permission) ?? false;

  if (user.isPending) return <Spinner label={t('common.loading')} />;
  if (user.error) return <Alert tone="danger">{describeError(user.error).message}</Alert>;
  const u = user.data;
  const isSelf = me?.id === u.id;
  const anonymized = u.anonymized_at !== null;
  const visibleActions = anonymized
    ? []
    : ACTIONS.filter((a) => can(a.permission) && a.visible(u) && !isSelf);
  const actionError = action.error ? describeError(action.error).message : null;

  return (
    <>
      <p>
        <Link to="/admin/users">← {t('users.title')}</Link>
      </p>
      <PageHeader
        title={u.display_name}
        description={
          <span className="sx-row">
            <code>{u.username}</code>
            <UserStatusPill user={u} />
            {u.is_break_glass && <span className="sx-muted">{t('users.breakGlass')}</span>}
          </span>
        }
        actions={
          <div className="sx-row">
            {canEdit && !anonymized && !isSelf && u.auth_source === 'local' && (
              <Button
                variant="secondary"
                onClick={() => {
                  setResetOpen(true);
                }}
              >
                {t('users.actions.reset-password')}
              </Button>
            )}
            {visibleActions.map((a) => (
              <Button
                key={a.action}
                variant={a.danger ? 'danger' : 'secondary'}
                onClick={() => {
                  action.reset();
                  setPending(a);
                }}
              >
                {t(`users.actions.${a.action}`)}
              </Button>
            ))}
          </div>
        }
      />
      {isSelf && <Alert tone="info">{t('users.detail.selfHint')}</Alert>}
      {anonymized && <Alert tone="neutral">{t('users.detail.anonymized')}</Alert>}
      {action.isSuccess && !pending && <Alert tone="success">{t('common.done')}</Alert>}

      <Tabs
        label={t('users.detail.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={(['data', 'roles', 'sessions', 'activity'] as const).map((id) => ({
          id,
          label: t(`users.detail.tabs.${id}`),
        }))}
      />
      {tab === 'data' && (
        <div className="sx-two-columns">
          <Card title={t('users.detail.info')}>
            <DetailList
              items={[
                {
                  label: t('users.fields.authSource'),
                  value: t(`users.authSources.${u.auth_source}`),
                },
                { label: t('users.fields.email'), value: u.email },
                { label: 'MFA', value: u.mfa_enabled ? t('common.yes') : t('common.no') },
                { label: t('users.fields.lastLogin'), value: formatDateTime(u.last_login_at) },
                { label: t('users.fields.failedAttempts'), value: u.failed_attempts },
                { label: t('users.fields.lockedUntil'), value: formatDateTime(u.locked_until) },
                {
                  label: t('users.fields.mustChange'),
                  value: u.must_change_password ? t('common.yes') : t('common.no'),
                },
                {
                  label: t('users.fields.created'),
                  value: `${formatDateTime(u.created_at)} · ${u.created_by ?? '—'}`,
                },
                {
                  label: t('users.fields.updated'),
                  value: `${formatDateTime(u.updated_at)} · ${u.updated_by ?? '—'}`,
                },
              ]}
            />
          </Card>
          {canEdit && !anonymized && (
            <Card title={t('users.detail.edit')}>
              <EditUserForm key={u.version} user={u} />
            </Card>
          )}
        </div>
      )}
      {tab === 'roles' && (
        <RolesTab
          key={u.version}
          user={u}
          editable={canEdit && !anonymized && !isSelf && !u.is_break_glass}
        />
      )}
      {tab === 'sessions' && <SessionsTab user={u} />}
      {tab === 'activity' && <AuditTrail resourceId={u.id} />}

      <ConfirmDialog
        key={pending?.action ?? 'none'}
        open={pending !== null}
        title={pending ? t(`users.actions.${pending.action}`) : ''}
        message={pending ? t(`users.confirm.${pending.action}`, { name: u.display_name }) : ''}
        confirmLabel={t('common.confirm')}
        cancelLabel={t('common.cancel')}
        danger={pending?.danger ?? false}
        busy={action.isPending}
        {...(pending?.reason ? { reasonLabel: t('users.detail.reason') } : {})}
        reasonRequired={pending?.reason === 'required'}
        error={actionError && <Alert tone="danger">{actionError}</Alert>}
        onClose={() => {
          setPending(null);
        }}
        onConfirm={(reason) => {
          if (!pending) return;
          action.mutate(
            { action: pending.action, ...(reason ? { reason } : {}) },
            {
              onSuccess: () => {
                setPending(null);
              },
            },
          );
        }}
      />

      <Dialog
        open={resetOpen}
        title={t('users.actions.reset-password')}
        onClose={() => {
          resetPassword.reset();
          setResetOpen(false);
        }}
        footer={
          resetPassword.data ? undefined : (
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setResetOpen(false);
                }}
              >
                {t('common.cancel')}
              </Button>
              <Button
                onClick={() => {
                  resetPassword.mutate();
                }}
                disabled={resetPassword.isPending}
              >
                {t('common.confirm')}
              </Button>
            </>
          )
        }
      >
        {resetPassword.data ? (
          <OneTimeSecret
            label={t('users.create.tempPassword')}
            value={resetPassword.data.temporary_password}
            warning={t('users.create.tempPasswordWarning')}
            copyLabel={t('common.copy')}
            copiedLabel={t('common.copied')}
          />
        ) : (
          <>
            <p>{t('users.confirm.reset-password', { name: u.display_name })}</p>
            {resetPassword.error && (
              <Alert tone="danger">{describeError(resetPassword.error).message}</Alert>
            )}
          </>
        )}
      </Dialog>
    </>
  );
}
