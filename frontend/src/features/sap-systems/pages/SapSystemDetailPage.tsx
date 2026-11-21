import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDate, formatDateTime } from '@/shared/format';
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
  useSapSystem,
  useSapSystemStatus,
  useTestSapSystem,
  type SapSystemDetail,
  type StatusAction,
} from '../api/queries';
import { ServiceExplorer } from '../components/ServiceExplorer';
import { DuplicateDialog, RotateCredentialsDialog } from '../components/SystemDialogs';
import { LastTestPill, SystemStatusPill } from '../components/SystemPills';
import { TestReportView } from '../components/TestReportView';

type Tab = 'general' | 'services' | 'sources' | 'activity';

function credentialSummary(s: SapSystemDetail, t: (key: string) => string): string {
  const info = s.credential_info;
  const who =
    info.username ??
    info.client_id ??
    info.certificate_subject ??
    t('sapSystems.credentials.stored');
  const expiry = info.certificate_not_after
    ? ` · ${t('sapSystems.credentials.expires')} ${formatDate(info.certificate_not_after)}`
    : '';
  return `${t(`sapSystems.authTypes.${s.auth_type}`)} · ${who} · ••••••${expiry}`;
}

function GeneralTab({ system, canTest }: { system: SapSystemDetail; canTest: boolean }) {
  const { t } = useTranslation();
  const test = useTestSapSystem(system.id);
  const report = system.last_test_result;
  return (
    <div className="sx-two-columns" style={{ alignItems: 'start' }}>
      <Card title={t('sapSystems.detail.connectionTitle')}>
        <DetailList
          items={[
            { label: t('sapSystems.fields.name'), value: system.name },
            { label: t('sapSystems.fields.description'), value: system.description },
            {
              label: t('sapSystems.fields.environment'),
              value: t(`sapSystems.environments.${system.environment}`),
            },
            {
              label: t('sapSystems.fields.systemType'),
              value: t(`sapSystems.systemTypes.${system.system_type}`),
            },
            { label: t('sapSystems.fields.baseUrl'), value: <code>{system.base_url}</code> },
            {
              label: t('sapSystems.fields.clientLanguage'),
              value: `${system.sap_client} / ${system.language}`,
            },
            { label: t('sapSystems.fields.credentials'), value: credentialSummary(system, t) },
            {
              label: t('sapSystems.fields.tlsVerify'),
              value: system.tls_verify ? t('common.yes') : t('common.no'),
            },
            {
              label: t('sapSystems.fields.caBundle'),
              value: system.ca_bundle.length
                ? system.ca_bundle
                    .map((c) => `${c.subject} (${formatDate(c.not_after)})`)
                    .join(' · ')
                : null,
            },
            { label: t('sapSystems.fields.proxyUrl'), value: system.proxy_url },
            {
              label: t('sapSystems.fields.limits'),
              value: t('sapSystems.detail.limits', {
                timeout: system.timeout_seconds,
                parallel: system.max_concurrent_requests,
                runs: system.max_concurrent_runs,
              }),
            },
            { label: t('sapSystems.fields.tags'), value: system.tags.join(', ') || null },
            {
              label: t('sapSystems.detail.updated'),
              value: `${formatDateTime(system.updated_at)} · ${system.updated_by ?? '—'}`,
            },
          ]}
        />
      </Card>
      <Card title={t('sapSystems.detail.lastTestTitle')}>
        {test.error && <Alert tone="danger">{describeError(test.error).message}</Alert>}
        {test.isPending ? (
          <Spinner label={t('sapSystems.wizard.testing')} />
        ) : report ? (
          <TestReportView report={report} outdated={system.test_outdated} />
        ) : (
          <p className="sx-muted">{t('sapSystems.detail.noTest')}</p>
        )}
        {canTest && (
          <div style={{ marginTop: 'var(--sx-space-4)' }}>
            <Button
              onClick={() => {
                test.mutate();
              }}
              disabled={test.isPending}
            >
              {t('sapSystems.detail.actions.test')}
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

export function SapSystemDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { systemId = '' } = useParams();
  const system = useSapSystem(systemId);
  const status = useSapSystemStatus(systemId);
  const canManage = useCan('sap_connection:manage');
  const canTest = useCan('sap_connection:test');
  const [tab, setTab] = useState<Tab>('general');
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [rotating, setRotating] = useState(false);
  const [duplicating, setDuplicating] = useState(false);

  if (system.isPending) return <Spinner label={t('common.loading')} />;
  if (system.error) return <Alert tone="danger">{describeError(system.error).message}</Alert>;
  const s = system.data;
  const canActivate = s.status !== 'active' && s.last_test_ok === true && !s.test_outdated;

  const confirm = (reason: string) => {
    if (!pending) return;
    status.mutate(
      { action: pending.action, ...(reason ? { reason } : {}) },
      {
        onSuccess: () => {
          setPending(null);
          if (pending.action === 'delete') void navigate('/sap-systems');
        },
      },
    );
  };

  return (
    <>
      <PageHeader
        title={
          <span className="sx-row">
            {s.code} <SystemStatusPill status={s.status} /> <LastTestPill system={s} />
          </span>
        }
        description={s.name}
        actions={
          <div className="sx-row">
            <Link to="/sap-systems">{t('sapSystems.detail.back')}</Link>
            {canManage && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => void navigate(`/sap-systems/${s.id}/edit`)}
                >
                  {t('sapSystems.detail.actions.edit')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setRotating(true);
                  }}
                >
                  {t('sapSystems.detail.actions.rotate')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setDuplicating(true);
                  }}
                >
                  {t('sapSystems.detail.actions.duplicate')}
                </Button>
                {s.status === 'active' ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setPending({ action: 'deactivate', danger: false, reason: true });
                    }}
                  >
                    {t('sapSystems.detail.actions.deactivate')}
                  </Button>
                ) : (
                  <Button
                    disabled={!canActivate}
                    title={canActivate ? undefined : t('sapSystems.detail.activateBlocked')}
                    onClick={() => {
                      setPending({ action: 'activate', danger: false, reason: false });
                    }}
                  >
                    {t('sapSystems.detail.actions.activate')}
                  </Button>
                )}
                <Button
                  variant="danger"
                  onClick={() => {
                    setPending({ action: 'delete', danger: true, reason: true });
                  }}
                >
                  {t('sapSystems.detail.actions.delete')}
                </Button>
              </>
            )}
          </div>
        }
      />
      {s.status === 'draft' && <Alert tone="info">{t('sapSystems.detail.draftBanner')}</Alert>}
      {s.status === 'inactive' && (
        <Alert tone="warning" title={t('sapSystems.detail.inactiveBanner')}>
          {s.status_reason}
        </Alert>
      )}
      <Tabs
        label={t('sapSystems.detail.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={(['general', 'services', 'sources', 'activity'] as const).map((id) => ({
          id,
          label: t(`sapSystems.detail.tabs.${id}`),
        }))}
      />
      {tab === 'general' && <GeneralTab system={s} canTest={canTest} />}
      {tab === 'services' && <ServiceExplorer systemId={s.id} />}
      {tab === 'sources' && (
        <EmptyState title={t('sapSystems.detail.sourcesTitle')}>
          <p>{t('sapSystems.detail.sourcesSoon')}</p>
          {s.dependents.length > 0 && (
            <ul>
              {s.dependents.map((d) => (
                <li key={`${d.kind}-${d.id}`}>
                  {d.kind}: {d.name}
                </li>
              ))}
            </ul>
          )}
        </EmptyState>
      )}
      {tab === 'activity' && <AuditTrail resourceId={s.id} />}

      <RotateCredentialsDialog
        system={s}
        open={rotating}
        onClose={() => {
          setRotating(false);
        }}
      />
      <DuplicateDialog
        system={s}
        open={duplicating}
        onClose={() => {
          setDuplicating(false);
        }}
      />
      <ConfirmDialog
        key={pending?.action ?? 'none'}
        open={pending !== null}
        title={pending ? t(`sapSystems.confirm.${pending.action}.title`, { code: s.code }) : ''}
        message={pending ? t(`sapSystems.confirm.${pending.action}.message`) : ''}
        confirmLabel={pending ? t(`sapSystems.detail.actions.${pending.action}`) : ''}
        cancelLabel={t('common.cancel')}
        danger={pending?.danger ?? false}
        busy={status.isPending}
        {...(pending?.reason ? { reasonLabel: t('sapSystems.confirm.reason') } : {})}
        {...(pending?.action === 'delete'
          ? {
              confirmText: s.code,
              confirmTextLabel: t('sapSystems.confirm.typeCode', { code: s.code }),
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
