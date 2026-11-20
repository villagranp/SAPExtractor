import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DetailList,
  EmptyState,
  PageHeader,
  Spinner,
  Tabs,
} from '@/shared/ui';
import {
  useDestination,
  useDestinationStatus,
  useDestinationTypes,
  useTestDestination,
  type DestinationDetail,
  type StatusAction,
} from '../api/queries';
import { CheckReportView } from '../components/CheckReportView';
import { DdlPreviewPanel } from '../components/DdlPreviewPanel';
import { DestinationStatusPill, LastTestPill } from '../components/DestinationPills';
import { RotateSecretDialog } from '../components/RotateSecretDialog';

type Tab = 'general' | 'ddl' | 'pipelines' | 'activity';

function configValue(value: unknown, t: (key: string) => string): string {
  if (typeof value === 'boolean') return value ? t('common.yes') : t('common.no');
  if (value === '\t') return t('destinations.form.enumLabels.tab');
  if (value === '\n') return t('destinations.form.enumLabels.lf');
  if (value === '\r\n') return t('destinations.form.enumLabels.crlf');
  if (value === null || value === undefined || value === '') return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function GeneralTab({
  destination,
  canTest,
}: {
  destination: DestinationDetail;
  canTest: boolean;
}) {
  const { t } = useTranslation();
  const test = useTestDestination(destination.id);
  const report = destination.last_test_result;
  const fingerprint = destination.credential_info.public_key_fingerprint as string | undefined;
  return (
    <div className="sx-two-columns" style={{ alignItems: 'start' }}>
      <Card title={t('destinations.detail.configTitle')}>
        <DetailList
          items={[
            { label: t('destinations.fields.name'), value: destination.name },
            { label: t('destinations.fields.description'), value: destination.description },
            {
              label: t('destinations.fields.type'),
              value: t(`destinations.types.${destination.type}.label`),
            },
            {
              label: t('destinations.fields.environment'),
              value: t(`destinations.environments.${destination.environment}`),
            },
            { label: t('destinations.fields.target'), value: <code>{destination.target}</code> },
            ...Object.entries(destination.config).map(([key, value]) => ({
              label: t(`destinations.config.${key}`, { defaultValue: key }),
              value: configValue(value, t),
            })),
            ...(fingerprint
              ? [
                  {
                    label: t('destinations.fields.credentials'),
                    value: (
                      <span>
                        {t('destinations.detail.keyPair')} · <code>{fingerprint}</code>
                      </span>
                    ),
                  },
                ]
              : []),
            { label: t('destinations.fields.tags'), value: destination.tags.join(', ') || null },
            {
              label: t('destinations.detail.updated'),
              value: `${formatDateTime(destination.updated_at)} · ${destination.updated_by ?? '—'}`,
            },
          ]}
        />
      </Card>
      <Card title={t('destinations.detail.lastTestTitle')}>
        {test.error && <Alert tone="danger">{describeError(test.error).message}</Alert>}
        {test.isPending ? (
          <Spinner label={t('destinations.wizard.testing')} />
        ) : report ? (
          <CheckReportView report={report} outdated={destination.test_outdated} />
        ) : (
          <p className="sx-muted">{t('destinations.detail.noTest')}</p>
        )}
        {canTest && (
          <div style={{ marginTop: 'var(--sx-space-4)' }}>
            <Button
              onClick={() => {
                test.mutate();
              }}
              disabled={test.isPending}
            >
              {t('destinations.detail.actions.test')}
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

interface PendingAction {
  action: StatusAction;
  danger: boolean;
  reason: boolean;
}

export function DestinationDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { destinationId = '' } = useParams();
  const destination = useDestination(destinationId);
  const types = useDestinationTypes();
  const status = useDestinationStatus(destinationId);
  const canManage = useCan('destination:manage');
  const canTest = useCan('destination:test');
  const [tab, setTab] = useState<Tab>('general');
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [rotating, setRotating] = useState(false);

  if (destination.isPending) return <Spinner label={t('common.loading')} />;
  if (destination.error) {
    return <Alert tone="danger">{describeError(destination.error).message}</Alert>;
  }
  const d = destination.data;
  const typeInfo = types.data?.find((x) => x.type === d.type);
  const canActivate = d.status !== 'active' && d.last_test_ok === true && !d.test_outdated;

  const confirm = (reason: string) => {
    if (!pending) return;
    status.mutate(
      { action: pending.action, ...(reason ? { reason } : {}) },
      {
        onSuccess: () => {
          setPending(null);
          if (pending.action === 'delete') void navigate('/destinations');
        },
      },
    );
  };

  return (
    <>
      <PageHeader
        title={
          <span className="sx-row">
            {d.code} <DestinationStatusPill status={d.status} /> <LastTestPill destination={d} />
          </span>
        }
        description={d.name}
        actions={
          <div className="sx-row">
            <Link to="/destinations">{t('destinations.detail.back')}</Link>
            {canManage && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => void navigate(`/destinations/${d.id}/edit`)}
                >
                  {t('destinations.detail.actions.edit')}
                </Button>
                {typeInfo?.requires_secret && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setRotating(true);
                    }}
                  >
                    {t('destinations.detail.actions.rotate')}
                  </Button>
                )}
                {d.status === 'active' ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setPending({ action: 'deactivate', danger: false, reason: true });
                    }}
                  >
                    {t('destinations.detail.actions.deactivate')}
                  </Button>
                ) : (
                  <Button
                    disabled={!canActivate}
                    title={canActivate ? undefined : t('destinations.detail.activateBlocked')}
                    onClick={() => {
                      setPending({ action: 'activate', danger: false, reason: false });
                    }}
                  >
                    {t('destinations.detail.actions.activate')}
                  </Button>
                )}
                <Button
                  variant="danger"
                  onClick={() => {
                    setPending({ action: 'delete', danger: true, reason: true });
                  }}
                >
                  {t('destinations.detail.actions.delete')}
                </Button>
              </>
            )}
          </div>
        }
      />
      {d.status === 'draft' && <Alert tone="info">{t('destinations.detail.draftBanner')}</Alert>}
      {d.status === 'inactive' && (
        <Alert tone="warning" title={t('destinations.detail.inactiveBanner')}>
          {d.status_reason}
        </Alert>
      )}
      <Tabs
        label={t('destinations.detail.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={(['general', 'ddl', 'pipelines', 'activity'] as const).map((id) => ({
          id,
          label: t(`destinations.detail.tabs.${id}`),
        }))}
      />
      {tab === 'general' && <GeneralTab destination={d} canTest={canTest} />}
      {tab === 'ddl' && (
        <DdlPreviewPanel destination={d} supportedModes={typeInfo?.supported_modes ?? ['append']} />
      )}
      {tab === 'pipelines' && (
        <EmptyState title={t('destinations.detail.pipelinesTitle')}>
          <p>{t('destinations.detail.pipelinesSoon')}</p>
          {(d.dependents ?? []).length > 0 && (
            <ul>
              {(d.dependents ?? []).map((x) => (
                <li key={`${x.kind}-${x.id}`}>
                  {x.kind}: {x.name}
                </li>
              ))}
            </ul>
          )}
        </EmptyState>
      )}
      {tab === 'activity' && <AuditTrail resourceId={d.id} />}

      <RotateSecretDialog
        destination={d}
        open={rotating}
        onClose={() => {
          setRotating(false);
        }}
      />
      <ConfirmDialog
        key={pending?.action ?? 'none'}
        open={pending !== null}
        title={pending ? t(`destinations.confirm.${pending.action}.title`, { code: d.code }) : ''}
        message={pending ? t(`destinations.confirm.${pending.action}.message`) : ''}
        confirmLabel={pending ? t(`destinations.detail.actions.${pending.action}`) : ''}
        cancelLabel={t('common.cancel')}
        danger={pending?.danger ?? false}
        busy={status.isPending}
        {...(pending?.reason ? { reasonLabel: t('destinations.confirm.reason') } : {})}
        {...(pending?.action === 'delete'
          ? {
              confirmText: d.code,
              confirmTextLabel: t('destinations.confirm.typeCode', { code: d.code }),
            }
          : {})}
        error={
          status.error ? (
            <Alert tone="danger">{describeError(status.error).message}</Alert>
          ) : undefined
        }
        onConfirm={confirm}
        onClose={() => {
          status.reset();
          setPending(null);
        }}
      />
    </>
  );
}
