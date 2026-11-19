import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useMe } from '@/features/auth/api/queries';
import { usePermissions } from '@/features/roles/api/queries';
import { PermissionMatrix } from '@/features/roles/components/PermissionMatrix';
import { describeError } from '@/shared/api/errors';
import { formatDate, formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  ConfirmDialog,
  DataTable,
  Dialog,
  FieldErrors,
  OneTimeSecret,
  PageHeader,
  StatusPill,
  TextField,
  type Column,
  type StatusTone,
} from '@/shared/ui';
import { useApiTokens, useCreateApiToken, useRevokeApiToken, type ApiToken } from '../api/queries';

const TONES: Record<ApiToken['status'], StatusTone> = {
  active: 'success',
  expired: 'neutral',
  revoked: 'danger',
};

function CreateTokenDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const me = useMe().data;
  const catalog = usePermissions();
  const create = useCreateApiToken();
  const [name, setName] = useState('');
  const [days, setDays] = useState('90');
  const [scopes, setScopes] = useState<string[]>([]);
  const error = create.error ? describeError(create.error) : null;
  // Solo se ofrecen los permisos que el propio usuario tiene (la API lo exige igualmente).
  const grantable = (catalog.data ?? []).filter((p) => me?.permissions.includes(p.code));

  const close = () => {
    setName('');
    setDays('90');
    setScopes([]);
    create.reset();
    onClose();
  };
  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    create.mutate({ name: name.trim(), scopes, expires_in_days: Number(days) });
  };

  if (create.data) {
    return (
      <Dialog
        open={open}
        title={t('apiTokens.createdTitle')}
        onClose={close}
        footer={<Button onClick={close}>{t('common.close')}</Button>}
      >
        <OneTimeSecret
          label={t('apiTokens.secretLabel')}
          value={create.data.secret}
          warning={t('apiTokens.secretWarning')}
          copyLabel={t('common.copy')}
          copiedLabel={t('common.copied')}
        />
        <p className="sx-muted">{t('apiTokens.usage')}</p>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      size="lg"
      title={t('apiTokens.new')}
      onClose={close}
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="sx-create-token"
            disabled={create.isPending || !name.trim() || scopes.length === 0}
          >
            {t('common.create')}
          </Button>
        </>
      }
    >
      <form id="sx-create-token" onSubmit={submit} noValidate>
        {error && <Alert tone="danger">{error.message}</Alert>}
        <TextField
          label={t('apiTokens.fields.name')}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
          required
        />
        <TextField
          label={t('apiTokens.fields.expiresInDays')}
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(e) => {
            setDays(e.target.value);
          }}
        />
        <FieldErrors errors={error?.fields.scopes} />
        <p>
          <strong>{t('apiTokens.fields.scopes')}</strong>
        </p>
        <PermissionMatrix permissions={grantable} selected={scopes} onChange={setScopes} />
      </form>
    </Dialog>
  );
}

export function ApiTokensPage() {
  const { t } = useTranslation();
  const tokens = useApiTokens();
  const revoke = useRevokeApiToken();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);

  const columns: Column<ApiToken>[] = [
    {
      key: 'name',
      header: t('apiTokens.fields.name'),
      render: (tk) => (
        <span>
          <strong>{tk.name}</strong>
          <br />
          <code className="sx-muted">{tk.prefix}…</code>
        </span>
      ),
    },
    { key: 'owner', header: t('apiTokens.fields.owner'), render: (tk) => tk.user.display_name },
    {
      key: 'scopes',
      header: t('apiTokens.fields.scopes'),
      render: (tk) => <span className="sx-muted">{tk.scopes.join(', ')}</span>,
    },
    {
      key: 'status',
      header: t('apiTokens.fields.status'),
      render: (tk) => (
        <StatusPill tone={TONES[tk.status]}>{t(`apiTokens.statuses.${tk.status}`)}</StatusPill>
      ),
    },
    {
      key: 'expires',
      header: t('apiTokens.fields.expires'),
      render: (tk) => formatDate(tk.expires_at),
    },
    {
      key: 'used',
      header: t('apiTokens.fields.lastUsed'),
      render: (tk) => formatDateTime(tk.last_used_at),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (tk) =>
        tk.status === 'active' && (
          <Button
            variant="ghost"
            onClick={() => {
              setRevoking(tk);
            }}
          >
            {t('apiTokens.revoke')}
          </Button>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title={t('apiTokens.title')}
        description={t('apiTokens.description')}
        actions={
          <Button
            onClick={() => {
              setCreating(true);
            }}
          >
            {t('apiTokens.new')}
          </Button>
        }
      />
      {tokens.error && <Alert tone="danger">{describeError(tokens.error).message}</Alert>}
      <DataTable
        caption={t('apiTokens.title')}
        columns={columns}
        rows={tokens.data ?? []}
        rowKey={(tk) => tk.id}
        loading={tokens.isPending}
        empty={t('apiTokens.empty')}
      />
      <CreateTokenDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
      />
      <ConfirmDialog
        open={revoking !== null}
        title={t('apiTokens.revoke')}
        message={t('apiTokens.revokeConfirm', { name: revoking?.name ?? '' })}
        confirmLabel={t('apiTokens.revoke')}
        cancelLabel={t('common.cancel')}
        danger
        busy={revoke.isPending}
        onClose={() => {
          setRevoking(null);
        }}
        onConfirm={() => {
          if (revoking)
            revoke.mutate(revoking.id, {
              onSuccess: () => {
                setRevoking(null);
              },
            });
        }}
      />
    </>
  );
}
