import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, FilterBar, PageHeader, Pagination, SelectField } from '@/shared/ui';
import { RUN_STATUSES, useRuns, type RunStatus } from '../api/queries';
import { RunsTable } from '../components/RunParts';

const PAGE_SIZE = 25;

/** Historial global de ejecuciones (plan 10 §3). */
export function RunsPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RunStatus | ''>('');
  const [page, setPage] = useState(1);
  const runs = useRuns({ page, page_size: PAGE_SIZE, ...(status ? { status } : {}) });
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
            setPage(1);
          }}
          options={RUN_STATUSES.map((s) => ({ value: s, label: t(`runs.statuses.${s}`) }))}
        />
      </FilterBar>
      {runs.error && <Alert tone="danger">{describeError(runs.error).message}</Alert>}
      <RunsTable
        runs={runs.data?.items ?? []}
        loading={runs.isPending}
        showPipeline
        empty={status ? t('runs.emptyFiltered') : t('runs.empty')}
      />
      {runs.data && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={runs.data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
    </>
  );
}
