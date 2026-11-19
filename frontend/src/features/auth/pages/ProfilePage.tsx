import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  DataTable,
  DetailList,
  PageHeader,
  StatusPill,
  type Column,
} from '@/shared/ui';
import { useMe, useMySessions, useRevokeMySession, type SessionInfo } from '../api/queries';
import { ChangePasswordForm } from '../components/ChangePasswordForm';
import { MfaSetup } from '../components/MfaSetup';

export function ProfilePage() {
  const { t } = useTranslation();
  const me = useMe().data;
  const sessions = useMySessions();
  const revoke = useRevokeMySession();
  if (!me) return null;
  const isLocal = me.auth_source === 'local';

  const columns: Column<SessionInfo>[] = [
    {
      key: 'created',
      header: t('profile.started'),
      render: (s) => formatDateTime(s.created_at),
    },
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
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (s) =>
        s.current ? (
          <StatusPill tone="info">{t('profile.current')}</StatusPill>
        ) : (
          <Button
            variant="ghost"
            onClick={() => {
              revoke.mutate(s.id);
            }}
            disabled={revoke.isPending}
          >
            {t('profile.revoke')}
          </Button>
        ),
    },
  ];

  return (
    <>
      <PageHeader title={t('profile.title')} description={t('profile.description')} />
      <div className="sx-two-columns">
        <Card title={t('profile.data')}>
          <DetailList
            items={[
              { label: t('users.fields.username'), value: me.username },
              { label: t('users.fields.displayName'), value: me.display_name },
              { label: t('users.fields.email'), value: me.email },
              {
                label: t('users.fields.authSource'),
                value: t(`users.authSources.${me.auth_source}`),
              },
              {
                label: t('users.fields.roles'),
                value: me.roles.map((r) => r.name).join(', ') || '—',
              },
            ]}
          />
        </Card>
        <Card title={t('profile.security')}>
          {isLocal ? (
            me.mfa_enabled ? (
              <Alert tone="success">{t('profile.mfaOn')}</Alert>
            ) : (
              <MfaSetup />
            )
          ) : (
            <Alert tone="info">{t('profile.entraManaged')}</Alert>
          )}
        </Card>
        {isLocal && (
          <Card title={t('profile.changePassword')}>
            <ChangePasswordForm />
          </Card>
        )}
      </div>
      <h2 style={{ marginTop: 'var(--sx-space-5)' }}>{t('profile.sessions')}</h2>
      <DataTable
        caption={t('profile.sessions')}
        columns={columns}
        rows={sessions.data ?? []}
        rowKey={(s) => s.id}
        loading={sessions.isPending}
        empty={t('profile.sessionsEmpty')}
      />
    </>
  );
}
