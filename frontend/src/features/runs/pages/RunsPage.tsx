import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  EmptyState,
  FilterBar,
  PageHeader,
  usePageState,
  SelectField,
  TextField,
} from '@/shared/ui';
import {
  RUN_STATUSES,
  RUN_TRIGGERS,
  useRuns,
  type RunStatus,
  type RunTrigger,
} from '../api/queries';
import { IdleWorkersNotice, RunsTable } from '../components/RunsTable';

/** Día elegido (zona del navegador) → instante ISO de su comienzo o de su final. */
function dayBoundary(day: string, end: boolean): string | undefined {
  if (!day) return undefined;
  const date = new Date(`${day}T${end ? '23:59:59.999' : '00:00:00'}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function RunsPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const pipeline = params.get('pipeline') ?? '';
  const [status, setStatus] = useState<RunStatus | ''>('');
  const [trigger, setTrigger] = useState<RunTrigger | ''>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { page, setPage, pageSize, setPageSize } = usePageState();
  const fromIso = dayBoundary(from, false);
  const toIso = dayBoundary(to, true);
  const filtered = Boolean(status || trigger || from || to || pipeline);

  const runs = useRuns({
    page,
    page_size: pageSize,
    ...(pipeline ? { pipeline } : {}),
    ...(status ? { status } : {}),
    ...(trigger ? { trigger } : {}),
    ...(fromIso ? { from: fromIso } : {}),
    ...(toIso ? { to: toIso } : {}),
  });
  const items = runs.data?.items ?? [];
  const pipelineCode = items[0]?.pipeline.code;
  const reset = () => {
    setPage(1);
  };

  return (
    <>
      <PageHeader title={t('runs.title')} description={t('runs.description')} />
      <FilterBar>
        <SelectField
          label={t('runs.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as RunStatus | '');
            reset();
          }}
          options={RUN_STATUSES.map((value) => ({ value, label: t(`runs.statuses.${value}`) }))}
        />
        <SelectField
          label={t('runs.fields.trigger')}
          placeholder={t('common.all')}
          value={trigger}
          onChange={(e) => {
            setTrigger(e.target.value as RunTrigger | '');
            reset();
          }}
          options={RUN_TRIGGERS.map((value) => ({ value, label: t(`runs.triggers.${value}`) }))}
        />
        <TextField
          label={t('runs.fields.from')}
          type="date"
          value={from}
          max={to || undefined}
          onChange={(e) => {
            setFrom(e.target.value);
            reset();
          }}
        />
        <TextField
          label={t('runs.fields.to')}
          type="date"
          value={to}
          min={from || undefined}
          onChange={(e) => {
            setTo(e.target.value);
            reset();
          }}
        />
      </FilterBar>
      {pipeline && (
        <Alert tone="info">
          {pipelineCode
            ? t('runs.onlyPipeline', { code: pipelineCode })
            : t('runs.onlyOnePipeline')}{' '}
          <Link to="/runs">{t('runs.seeAll')}</Link>
        </Alert>
      )}
      {runs.error && <Alert tone="danger">{describeError(runs.error).message}</Alert>}
      <IdleWorkersNotice queued={items.some((r) => r.status === 'queued')} />
      {runs.data?.total === 0 && !filtered ? (
        <EmptyState
          title={t('runs.emptyFirstTitle')}
          action={<Link to="/pipelines">{t('runs.goToPipelines')}</Link>}
        >
          <p>{t('runs.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <RunsTable
            runs={items}
            loading={runs.isPending}
            caption={t('runs.title')}
            empty={t('runs.empty')}
            pagination={{
              page,
              pageSize,
              total: runs.data?.total ?? 0,
              onPageChange: setPage,
              onPageSizeChange: setPageSize,
            }}
          />
        </>
      )}
    </>
  );
}
