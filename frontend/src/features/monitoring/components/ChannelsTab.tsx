import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  ConfirmDialog,
  DataTable,
  Dialog,
  SelectField,
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  useChannels,
  useCreateChannel,
  useDeleteChannel,
  useRules,
  useSubscribe,
  useSubscriptions,
  useTestChannel,
  useUnsubscribe,
  type Channel,
  type ChannelType,
  type Severity,
} from '../api/queries';

const TYPES: ChannelType[] = ['email', 'teams', 'webhook'];
const SEVERITIES: Severity[] = ['critical', 'warning', 'info'];

function NewChannelDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const create = useCreateChannel();
  const subscribe = useSubscribe();
  const [name, setName] = useState('');
  const [type, setType] = useState<ChannelType>('email');
  const [recipients, setRecipients] = useState('');
  const [url, setUrl] = useState('');
  const [hmac, setHmac] = useState('');
  const [severity, setSeverity] = useState<Severity>('warning');
  const error = create.error ?? subscribe.error;
  const fields = create.error ? describeError(create.error).fields : {};
  const field = (key: string) => fields[key]?.[0];

  const submit = () => {
    const body =
      type === 'email'
        ? { name, type, config: { recipients: recipients.split(/[,;\s]+/).filter(Boolean) } }
        : type === 'teams'
          ? { name, type, config: {}, secret: { url } }
          : { name, type, config: { url }, ...(hmac ? { secret: { hmac_secret: hmac } } : {}) };
    create.mutate(
      { enabled: true, ...body },
      {
        onSuccess: (channel) => {
          // Por defecto recibe todas las reglas desde la gravedad elegida.
          subscribe.mutate(
            { channel_id: channel.id, rule_id: null, min_severity: severity },
            { onSuccess: onClose },
          );
        },
      },
    );
  };

  return (
    <Dialog
      open
      title={t('monitoring.channels.newTitle')}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} disabled={create.isPending || subscribe.isPending}>
            {t('monitoring.channels.create')}
          </Button>
        </>
      }
    >
      <div className="sx-stack">
        {error && Object.keys(fields).length === 0 && (
          <Alert tone="danger">{describeError(error).message}</Alert>
        )}
        <div className="sx-two-columns">
          <TextField
            label={t('monitoring.channels.name')}
            value={name}
            error={field('name')}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
          <SelectField
            label={t('monitoring.channels.type')}
            value={type}
            onChange={(e) => {
              setType(e.target.value as ChannelType);
            }}
            options={TYPES.map((x) => ({ value: x, label: t(`monitoring.channelTypes.${x}`) }))}
          />
        </div>
        {type === 'email' && (
          <TextField
            label={t('monitoring.channels.recipients')}
            hint={t('monitoring.channels.recipientsHint')}
            value={recipients}
            error={field('config.recipients')}
            onChange={(e) => {
              setRecipients(e.target.value);
            }}
          />
        )}
        {type !== 'email' && (
          <TextField
            label={
              type === 'teams' ? t('monitoring.channels.teamsUrl') : t('monitoring.channels.url')
            }
            hint={type === 'teams' ? t('monitoring.channels.teamsHint') : undefined}
            value={url}
            type={type === 'teams' ? 'password' : 'url'}
            autoComplete="off"
            error={field(type === 'teams' ? 'secret.url' : 'config.url')}
            onChange={(e) => {
              setUrl(e.target.value);
            }}
          />
        )}
        {type === 'webhook' && (
          <TextField
            label={t('monitoring.channels.hmac')}
            hint={t('monitoring.channels.hmacHint')}
            type="password"
            autoComplete="off"
            value={hmac}
            error={field('secret.hmac_secret')}
            onChange={(e) => {
              setHmac(e.target.value);
            }}
          />
        )}
        <SelectField
          label={t('monitoring.channels.minSeverity')}
          hint={t('monitoring.channels.minSeverityHint')}
          value={severity}
          onChange={(e) => {
            setSeverity(e.target.value as Severity);
          }}
          options={SEVERITIES.map((s) => ({ value: s, label: t(`monitoring.severities.${s}`) }))}
        />
      </div>
    </Dialog>
  );
}

function SubscriptionsDialog({ channel, onClose }: { channel: Channel; onClose: () => void }) {
  const { t } = useTranslation();
  const subscriptions = useSubscriptions(true);
  const rules = useRules();
  const subscribe = useSubscribe();
  const unsubscribe = useUnsubscribe();
  const [rule, setRule] = useState('');
  const [severity, setSeverity] = useState<Severity>('warning');
  const mine = (subscriptions.data ?? []).filter((s) => s.channel_id === channel.id);
  const ruleName = (id: string | null) =>
    id === null
      ? t('monitoring.channels.allRules')
      : (rules.data?.find((r) => r.id === id)?.name ?? id);
  const error = subscribe.error ?? unsubscribe.error;

  return (
    <Dialog
      open
      size="lg"
      title={t('monitoring.channels.subscriptionsTitle', { name: channel.name })}
      onClose={onClose}
      closeLabel={t('common.close')}
    >
      <div className="sx-stack">
        {error && <Alert tone="danger">{describeError(error).message}</Alert>}
        <DataTable
          caption={t('monitoring.channels.subscriptionsTitle', { name: channel.name })}
          columns={[
            {
              key: 'rule',
              header: t('monitoring.channels.rule'),
              render: (s) => ruleName(s.rule_id),
            },
            {
              key: 'sev',
              header: t('monitoring.channels.minSeverity'),
              render: (s) => t(`monitoring.severities.${s.min_severity}`),
            },
            {
              key: 'remove',
              header: t('common.actions'),
              render: (s) => (
                <Button
                  variant="ghost"
                  onClick={() => {
                    unsubscribe.mutate(s.id);
                  }}
                >
                  {t('monitoring.channels.unsubscribe')}
                </Button>
              ),
            },
          ]}
          rows={mine}
          rowKey={(s) => s.id}
          loading={subscriptions.isPending}
          empty={t('monitoring.channels.noSubscriptions')}
        />
        <div className="sx-two-columns">
          <SelectField
            label={t('monitoring.channels.rule')}
            value={rule}
            onChange={(e) => {
              setRule(e.target.value);
            }}
            options={[
              { value: '', label: t('monitoring.channels.allRules') },
              ...(rules.data ?? []).map((r) => ({ value: r.id, label: r.name })),
            ]}
          />
          <SelectField
            label={t('monitoring.channels.minSeverity')}
            value={severity}
            onChange={(e) => {
              setSeverity(e.target.value as Severity);
            }}
            options={SEVERITIES.map((s) => ({ value: s, label: t(`monitoring.severities.${s}`) }))}
          />
        </div>
        <div>
          <Button
            disabled={subscribe.isPending}
            onClick={() => {
              subscribe.mutate({
                channel_id: channel.id,
                rule_id: rule || null,
                min_severity: severity,
              });
            }}
          >
            {t('monitoring.channels.subscribe')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export function ChannelsTab() {
  const { t } = useTranslation();
  const channels = useChannels(true);
  const test = useTestChannel();
  const remove = useDeleteChannel();
  const [creating, setCreating] = useState(false);
  const [managing, setManaging] = useState<Channel | null>(null);
  const [deleting, setDeleting] = useState<Channel | null>(null);

  const target = (c: Channel) => {
    if (c.type === 'email') {
      const list = Array.isArray(c.config.recipients) ? (c.config.recipients as string[]) : [];
      return list.join(', ');
    }
    if (c.type === 'teams') return t('monitoring.channels.teamsStored');
    return typeof c.config.url === 'string' ? c.config.url : '—';
  };

  const columns: Column<Channel>[] = [
    {
      key: 'name',
      header: t('monitoring.channels.name'),
      render: (c) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <strong>{c.name}</strong>
          <span className="sx-muted">{t(`monitoring.channelTypes.${c.type}`)}</span>
        </span>
      ),
    },
    {
      key: 'target',
      header: t('monitoring.channels.target'),
      render: (c) => <span style={{ overflowWrap: 'anywhere' }}>{target(c)}</span>,
    },
    {
      key: 'test',
      header: t('monitoring.channels.lastTest'),
      render: (c) =>
        c.last_test_ok === null ? (
          <StatusPill tone="neutral">{t('monitoring.channels.untested')}</StatusPill>
        ) : (
          <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
            <StatusPill tone={c.last_test_ok ? 'success' : 'danger'}>
              {c.last_test_ok
                ? t('monitoring.channels.testOk')
                : t('monitoring.channels.testFailed')}
            </StatusPill>
            <span className="sx-muted">{c.last_test_error ?? formatDateTime(c.last_test_at)}</span>
          </span>
        ),
    },
    {
      key: 'actions',
      header: t('common.actions'),
      render: (c) => (
        <span className="sx-row">
          <Button
            variant="secondary"
            disabled={test.isPending}
            onClick={() => {
              test.mutate(c.id);
            }}
          >
            {t('monitoring.channels.test')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setManaging(c);
            }}
          >
            {t('monitoring.channels.subscriptions')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDeleting(c);
            }}
          >
            {t('common.delete')}
          </Button>
        </span>
      ),
    },
  ];

  return (
    <div className="sx-stack">
      <div>
        <Button
          onClick={() => {
            setCreating(true);
          }}
        >
          {t('monitoring.channels.new')}
        </Button>
      </div>
      {(channels.error ?? test.error) && (
        <Alert tone="danger">{describeError(channels.error ?? test.error).message}</Alert>
      )}
      <DataTable
        caption={t('monitoring.alerts.tabs.channels')}
        columns={columns}
        rows={channels.data ?? []}
        rowKey={(c) => c.id}
        loading={channels.isPending}
        empty={t('monitoring.channels.empty')}
      />
      {creating && (
        <NewChannelDialog
          onClose={() => {
            setCreating(false);
          }}
        />
      )}
      {managing && (
        <SubscriptionsDialog
          channel={managing}
          onClose={() => {
            setManaging(null);
          }}
        />
      )}
      <ConfirmDialog
        key={deleting?.id ?? 'none'}
        open={deleting !== null}
        title={t('monitoring.channels.deleteTitle', { name: deleting?.name ?? '' })}
        message={t('monitoring.channels.deleteMessage')}
        confirmLabel={t('common.delete')}
        cancelLabel={t('common.cancel')}
        danger
        busy={remove.isPending}
        onConfirm={() => {
          if (deleting)
            remove.mutate(deleting.id, {
              onSuccess: () => {
                setDeleting(null);
              },
            });
        }}
        onClose={() => {
          setDeleting(null);
        }}
      />
    </div>
  );
}
