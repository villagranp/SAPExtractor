import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
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
  Pagination,
  SelectField,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  SOURCE_STATUSES,
  SOURCE_TYPES,
  useSources,
  type SourceStatus,
  type SourceSummary,
  type SourceType,
} from '../api/queries';
import {
  DeltaSummary,
  EnvironmentPill,
  SourceStatusPill,
  SourceTypeLabel,
} from '../components/SourcePills';

const PAGE_SIZE = 25;

export function SourcesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canManage = useCan('source:manage');
  const [q, setQ] = useState('');
  const [type, setType] = useState<SourceType | ''>('');
  const [status, setStatus] = useState<SourceStatus | ''>('');
  const [page, setPage] = useState(1);
  const filtered = Boolean(q.trim() || type || status);

  const sources = useSources({
    page,
    page_size: PAGE_SIZE,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(type ? { type } : {}),
    ...(status ? { status } : {}),
  });

  const columns: Column<SourceSummary>[] = [
    {
      key: 'source',
      header: t('sources.fields.source'),
      render: (s) => (
        <span>
          <strong>{s.code}</strong>
          <br />
          <span className="sx-muted">{s.name}</span>
        </span>
      ),
    },
    {
      key: 'system',
      header: t('sources.fields.system'),
      render: (s) => (
        <span className="sx-row">
          <span>{s.sap_system.code}</span>
          <EnvironmentPill environment={s.sap_system.environment} />
        </span>
      ),
    },
    {
      key: 'type',
      header: t('sources.fields.type'),
      render: (s) => (
        <span>
          <SourceTypeLabel type={s.source_type} />
          <br />
          <code className="sx-muted">{s.odp_name ?? s.entity_set}</code>
        </span>
      ),
    },
    {
      key: 'delta',
      header: t('sources.fields.delta'),
      render: (s) => <DeltaSummary method={s.delta_method} semantics={s.delta_semantics} />,
    },
    {
      key: 'schema',
      header: t('sources.fields.schemaVersion'),
      align: 'right',
      render: (s) => `v${String(s.schema_version)}`,
    },
    {
      key: 'status',
      header: t('sources.fields.status'),
      render: (s) => <SourceStatusPill status={s.status} />,
    },
    {
      key: 'updated',
      header: t('sources.fields.updatedAt'),
      render: (s) => <span className="sx-muted">{formatDateTime(s.updated_at)}</span>,
    },
  ];

  const newButton = canManage && (
    <Button onClick={() => void navigate('/sources/new')}>{t('sources.new')}</Button>
  );

  return (
    <>
      <PageHeader
        title={t('sources.title')}
        description={
          <>
            {t('sources.description')} <Link to="/sources/guide">{t('sources.guideLink')}</Link>
          </>
        }
        actions={newButton}
      />
      <FilterBar>
        <TextField
          label={t('common.search')}
          type="search"
          value={q}
          placeholder={t('sources.searchPlaceholder')}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <SelectField
          label={t('sources.fields.type')}
          placeholder={t('common.all')}
          value={type}
          onChange={(e) => {
            setType(e.target.value as SourceType | '');
            setPage(1);
          }}
          options={SOURCE_TYPES.map((value) => ({ value, label: t(`sources.types.${value}`) }))}
        />
        <SelectField
          label={t('sources.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as SourceStatus | '');
            setPage(1);
          }}
          options={SOURCE_STATUSES.map((value) => ({
            value,
            label: t(`sources.statuses.${value}`),
          }))}
        />
      </FilterBar>
      {sources.error && <Alert tone="danger">{describeError(sources.error).message}</Alert>}
      {sources.data?.total === 0 && !filtered ? (
        // Sin acción propia: «Nuevo origen» ya está en la cabecera.
        <EmptyState title={t('sources.emptyFirstTitle')}>
          <p>{t('sources.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <DataTable
            caption={t('sources.title')}
            columns={columns}
            rows={sources.data?.items ?? []}
            rowKey={(s) => s.id}
            loading={sources.isPending}
            empty={t('sources.empty')}
            onRowClick={(s) => void navigate(`/sources/${s.id}`)}
          />
          {sources.data && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={sources.data.total}
              onPageChange={setPage}
              labels={{
                previous: t('common.previous'),
                next: t('common.next'),
                summary: t('common.pageSummary'),
              }}
            />
          )}
        </>
      )}
    </>
  );
}
