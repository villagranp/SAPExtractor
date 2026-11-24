import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  DataTable,
  EmptyState,
  FilterBar,
  PageHeader,
  usePageState,
  SelectField,
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  ENVIRONMENTS,
  useSapSystems,
  type SapEnvironment,
  type SapSystemSummary,
  type SystemStatus,
} from '../api/queries';
import { LastTestPill, SystemStatusPill } from '../components/SystemPills';

export function SapSystemsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canManage = useCan('sap_connection:manage');
  const [q, setQ] = useState('');
  const [environment, setEnvironment] = useState<SapEnvironment | ''>('');
  const [status, setStatus] = useState<SystemStatus | ''>('');
  const { page, setPage, pageSize, setPageSize } = usePageState();
  const filtered = Boolean(q.trim() || environment || status);

  const systems = useSapSystems({
    page,
    page_size: pageSize,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(environment ? { environment } : {}),
    ...(status ? { status } : {}),
  });

  const columns: Column<SapSystemSummary>[] = [
    {
      key: 'system',
      header: t('sapSystems.fields.system'),
      render: (s) => (
        <span>
          <strong>{s.code}</strong>
          <br />
          <span className="sx-muted">{s.name}</span>
        </span>
      ),
    },
    {
      key: 'env',
      header: t('sapSystems.fields.environment'),
      render: (s) => (
        <StatusPill tone={s.environment === 'PRD' ? 'warning' : 'neutral'}>
          {s.environment}
        </StatusPill>
      ),
    },
    {
      key: 'type',
      header: t('sapSystems.fields.systemType'),
      render: (s) => t(`sapSystems.systemTypes.${s.system_type}`),
    },
    {
      key: 'url',
      header: t('sapSystems.fields.baseUrl'),
      render: (s) => (
        <span>
          <code>{s.base_url}</code>
          <br />
          <span className="sx-muted">
            {t('sapSystems.fields.client')} {s.sap_client}
          </span>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('sapSystems.fields.status'),
      render: (s) => <SystemStatusPill status={s.status} />,
    },
    {
      key: 'test',
      header: t('sapSystems.fields.lastTest'),
      render: (s) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <LastTestPill system={s} />
          <span className="sx-muted">{formatDateTime(s.last_test_at)}</span>
        </span>
      ),
    },
    {
      key: 'tags',
      header: t('sapSystems.fields.tags'),
      render: (s) => (s.tags.length ? s.tags.join(', ') : '—'),
    },
  ];

  const newButton = canManage && (
    <Button onClick={() => void navigate('/sap-systems/new')}>{t('sapSystems.new')}</Button>
  );

  return (
    <>
      <PageHeader
        title={t('sapSystems.title')}
        description={t('sapSystems.description')}
        actions={newButton}
      />
      <FilterBar>
        <TextField
          label={t('common.search')}
          type="search"
          value={q}
          placeholder={t('sapSystems.searchPlaceholder')}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <SelectField
          label={t('sapSystems.fields.environment')}
          placeholder={t('common.all')}
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value as SapEnvironment | '');
            setPage(1);
          }}
          options={ENVIRONMENTS.map((env) => ({
            value: env,
            label: t(`sapSystems.environments.${env}`),
          }))}
        />
        <SelectField
          label={t('sapSystems.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as SystemStatus | '');
            setPage(1);
          }}
          options={(['active', 'draft', 'inactive'] as const).map((s) => ({
            value: s,
            label: t(`sapSystems.statuses.${s}`),
          }))}
        />
      </FilterBar>
      {systems.error && <Alert tone="danger">{describeError(systems.error).message}</Alert>}
      {systems.data?.total === 0 && !filtered ? (
        // Sin acción propia: «Nuevo sistema SAP» ya está en la cabecera.
        <EmptyState title={t('sapSystems.emptyFirstTitle')}>
          <p>{t('sapSystems.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <DataTable
            caption={t('sapSystems.title')}
            columns={columns}
            rows={systems.data?.items ?? []}
            rowKey={(s) => s.id}
            loading={systems.isPending}
            empty={t('sapSystems.empty')}
            onRowClick={(s) => void navigate(`/sap-systems/${s.id}`)}
            pagination={{
              page,
              pageSize,
              total: systems.data?.total ?? 0,
              onPageChange: setPage,
              onPageSizeChange: setPageSize,
            }}
          />
        </>
      )}
    </>
  );
}
