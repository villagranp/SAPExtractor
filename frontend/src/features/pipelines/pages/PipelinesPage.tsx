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
  Pagination,
  SelectField,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  EXTRACTION_MODES,
  usePipelines,
  type ExtractionMode,
  type PipelineSummary,
} from '../api/queries';

const PAGE_SIZE = 25;

export function PipelinesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canManage = useCan('pipeline:manage');
  const [q, setQ] = useState('');
  const [mode, setMode] = useState<ExtractionMode | ''>('');
  const [page, setPage] = useState(1);
  const filtered = Boolean(q.trim() || mode);
  const pipelines = usePipelines({
    page,
    page_size: PAGE_SIZE,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(mode ? { extraction_mode: mode } : {}),
  });

  const columns: Column<PipelineSummary>[] = [
    {
      key: 'pipeline',
      header: t('pipelines.fields.pipeline'),
      render: (p) => (
        <span>
          <strong>{p.code}</strong>
          <br />
          <span className="sx-muted">{p.name}</span>
        </span>
      ),
    },
    { key: 'source', header: t('pipelines.fields.source'), render: (p) => p.source.code },
    {
      key: 'destination',
      header: t('pipelines.fields.destination'),
      render: (p) => p.destination.code,
    },
    {
      key: 'mode',
      header: t('pipelines.fields.mode'),
      render: (p) => t(`pipelines.modes.${p.extraction_mode}`),
    },
    {
      key: 'load',
      header: t('pipelines.fields.loadMode'),
      render: (p) => t(`destinations.loadModes.${String(p.load_options.load_mode)}`),
    },
    {
      key: 'updated',
      header: t('pipelines.fields.updated'),
      render: (p) => formatDateTime(p.updated_at),
    },
  ];

  const newButton = canManage && (
    <Button onClick={() => void navigate('/pipelines/new')}>{t('pipelines.new')}</Button>
  );

  return (
    <>
      <PageHeader
        title={t('pipelines.title')}
        description={t('pipelines.description')}
        actions={newButton}
      />
      <FilterBar>
        <TextField
          label={t('common.search')}
          type="search"
          value={q}
          placeholder={t('pipelines.searchPlaceholder')}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <SelectField
          label={t('pipelines.fields.mode')}
          placeholder={t('common.all')}
          value={mode}
          onChange={(e) => {
            setMode(e.target.value as ExtractionMode | '');
            setPage(1);
          }}
          options={EXTRACTION_MODES.map((m) => ({ value: m, label: t(`pipelines.modes.${m}`) }))}
        />
      </FilterBar>
      {pipelines.error && <Alert tone="danger">{describeError(pipelines.error).message}</Alert>}
      {pipelines.data?.total === 0 && !filtered ? (
        // Sin botón propio: la cabecera ya ofrece el alta.
        <EmptyState title={t('pipelines.emptyFirstTitle')}>
          <p>{t('pipelines.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <DataTable
            caption={t('pipelines.title')}
            columns={columns}
            rows={pipelines.data?.items ?? []}
            rowKey={(p) => p.id}
            loading={pipelines.isPending}
            empty={t('pipelines.empty')}
            onRowClick={(p) => void navigate(`/pipelines/${p.id}`)}
          />
          {pipelines.data && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={pipelines.data.total}
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
