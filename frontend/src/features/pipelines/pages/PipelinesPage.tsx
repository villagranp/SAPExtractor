import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { RunStatusPill } from '@/features/runs/components/RunParts';
import { EnvironmentPill } from '@/features/sources/components/SourcePills';
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
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  ENVIRONMENTS,
  PIPELINE_STATUSES,
  usePipelines,
  type PipelineStatus,
  type PipelineSummary,
  type SapEnvironment,
} from '../api/queries';
import { PipelineStatusPill } from '../components/PipelinePills';
import '../pipelines.css';

const PAGE_SIZE = 25;

export function PipelinesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canManage = useCan('pipeline:manage');
  const [params] = useSearchParams();
  const destination = params.get('destination') ?? '';
  const source = params.get('source') ?? '';
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<PipelineStatus | ''>('');
  const [environment, setEnvironment] = useState<SapEnvironment | ''>('');
  const [page, setPage] = useState(1);
  const filtered = Boolean(q.trim() || status || environment || destination || source);

  const pipelines = usePipelines({
    page,
    page_size: PAGE_SIZE,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(status ? { status } : {}),
    ...(environment ? { environment } : {}),
    ...(destination ? { destination } : {}),
    ...(source ? { source } : {}),
  });

  const columns: Column<PipelineSummary>[] = [
    {
      key: 'pipeline',
      header: t('pipelines.fields.pipeline'),
      render: (p) => (
        <span>
          <Link to={`/pipelines/${p.id}`}>
            <strong>{p.code}</strong>
          </Link>
          <br />
          <span className="sx-muted">{p.name}</span>
        </span>
      ),
    },
    {
      key: 'route',
      header: t('pipelines.fields.route'),
      render: (p) => (
        <dl className="sx-pipelines__mini-route">
          <div>
            <dt>{t('pipelines.route.source')}</dt>
            <dd>
              {p.source.code} <EnvironmentPill environment={p.sap_system.environment} />
            </dd>
          </div>
          <div>
            <dt>{t('pipelines.route.destination')}</dt>
            <dd>
              {p.destination.code} <EnvironmentPill environment={p.destination.environment} />
            </dd>
          </div>
        </dl>
      ),
    },
    {
      key: 'mode',
      header: t('pipelines.fields.mode'),
      render: (p) => (
        <span>
          {t(`pipelines.extractionModes.${p.extraction_mode}`)}
          <br />
          <span className="sx-muted">{t(`pipelines.loadModes.${p.load_mode}`)}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: t('pipelines.fields.status'),
      render: (p) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)', justifyItems: 'start' }}>
          <PipelineStatusPill status={p.status} />
          {p.cross_environment && (
            <StatusPill tone="warning">{t('pipelines.crossEnvironment')}</StatusPill>
          )}
        </span>
      ),
    },
    {
      key: 'next',
      header: t('pipelines.fields.nextFire'),
      render: (p) =>
        p.live_run ? (
          <RunStatusPill status={p.live_run.status} />
        ) : p.next_fire_at && p.status === 'active' ? (
          formatDateTime(p.next_fire_at)
        ) : (
          <span className="sx-muted">
            {p.status === 'active' ? t('pipelines.schedule.manualOnly') : '—'}
          </span>
        ),
    },
    {
      key: 'last',
      header: t('pipelines.fields.lastRun'),
      render: (p) =>
        p.last_run ? (
          <span className="sx-stack" style={{ gap: 'var(--sx-space-1)', justifyItems: 'start' }}>
            <RunStatusPill status={p.last_run.status} />
            <Link className="sx-muted" to={`/runs/${p.last_run.id}`}>
              {formatDateTime(p.last_run.finished_at ?? p.last_run.created_at)}
            </Link>
          </span>
        ) : (
          <span className="sx-muted">{t('pipelines.neverRun')}</span>
        ),
    },
  ];

  const newButton = canManage && (
    <Button onClick={() => void navigate('/pipelines/new')}>{t('pipelines.new')}</Button>
  );
  const reset = () => {
    setPage(1);
  };

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
            reset();
          }}
        />
        <SelectField
          label={t('pipelines.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as PipelineStatus | '');
            reset();
          }}
          options={PIPELINE_STATUSES.map((value) => ({
            value,
            label: t(`pipelines.statuses.${value}`),
          }))}
        />
        <SelectField
          label={t('pipelines.fields.sapEnvironment')}
          placeholder={t('common.all')}
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value as SapEnvironment | '');
            reset();
          }}
          options={ENVIRONMENTS.map((value) => ({ value, label: value }))}
        />
      </FilterBar>
      {(destination || source) && (
        <Alert tone="info">
          {t(destination ? 'pipelines.onlyDestination' : 'pipelines.onlySource')}{' '}
          <Link to="/pipelines">{t('pipelines.seeAll')}</Link>
        </Alert>
      )}
      {pipelines.error && <Alert tone="danger">{describeError(pipelines.error).message}</Alert>}
      {pipelines.data?.total === 0 && !filtered ? (
        // Sin botón propio: la cabecera ya ofrece el alta.
        <EmptyState title={t('pipelines.emptyFirstTitle')}>
          <p>{t('pipelines.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <div className="sx-pipelines__list">
            <DataTable
              caption={t('pipelines.title')}
              columns={columns}
              rows={pipelines.data?.items ?? []}
              rowKey={(p) => p.id}
              loading={pipelines.isPending}
              empty={t('pipelines.empty')}
              stacked
            />
          </div>
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
