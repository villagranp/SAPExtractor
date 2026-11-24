import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { formatDateTime } from '@/shared/format';
import { Alert, DataTable, type Column, type PaginationProps } from '@/shared/ui';
import { useWorkers, type RunSummary } from '../api/queries';
import { formatDuration, useNumbers } from '../format';
import { RunStatusPill } from './RunParts';

export interface RunsTableProps {
  runs: RunSummary[];
  loading: boolean;
  caption: string;
  empty: string;
  /** En la ficha de un pipeline la columna del pipeline sobra. */
  showPipeline?: boolean;
  /** Página pedida a la API; sin ella, la tabla pagina en el navegador. */
  pagination?: PaginationProps;
}

/** Tabla de ejecuciones; la fecha de cada fila enlaza con su detalle. */
export function RunsTable({
  runs,
  loading,
  caption,
  empty,
  showPipeline = true,
  pagination,
}: RunsTableProps) {
  const { t } = useTranslation();
  const { rows } = useNumbers();
  const columns: Column<RunSummary>[] = [
    {
      key: 'created',
      header: t('runs.fields.created'),
      render: (r) => (
        <span>
          <Link to={`/runs/${r.id}`}>{formatDateTime(r.created_at)}</Link>
          <br />
          <span className="sx-muted">
            {r.triggered_by
              ? t(`runs.triggeredBy.${r.trigger}`, { user: r.triggered_by })
              : t(`runs.triggers.${r.trigger}`)}
          </span>
        </span>
      ),
    },
    ...(showPipeline
      ? [
          {
            key: 'pipeline',
            header: t('runs.fields.pipeline'),
            render: (r: RunSummary) => (
              <span>
                <Link to={`/pipelines/${r.pipeline.id}`}>{r.pipeline.code}</Link>
                <br />
                <span className="sx-muted">{r.pipeline.name}</span>
              </span>
            ),
          },
        ]
      : []),
    {
      key: 'status',
      header: t('runs.fields.status'),
      render: (r) => <RunStatusPill status={r.status} />,
    },
    { key: 'mode', header: t('runs.fields.mode'), render: (r) => t(`runs.modes.${r.mode}`) },
    { key: 'rows', header: t('runs.fields.rows'), render: (r) => rows(r.rows_extracted) },
    {
      key: 'duration',
      header: t('runs.fields.duration'),
      render: (r) => formatDuration(r.started_at, r.finished_at),
    },
    {
      key: 'error',
      header: t('runs.fields.error'),
      render: (r) => (r.error_code ? <code>{r.error_code}</code> : '—'),
    },
  ];
  return (
    <DataTable
      caption={caption}
      columns={columns}
      rows={runs}
      rowKey={(r) => r.id}
      loading={loading}
      empty={empty}
      stacked
      {...(pagination ? { pagination } : {})}
    />
  );
}

/**
 * Aviso cuando hay ejecuciones en cola y ningún worker puede tomarlas: sin él, la cola parece
 * atascada sin motivo.
 */
export function IdleWorkersNotice({ queued }: { queued: boolean }) {
  const { t } = useTranslation();
  const workers = useWorkers(queued);
  if (!queued || !workers.data) return null;
  const online = workers.data.filter((w) => w.status === 'online');
  if (online.some((w) => w.has_executor)) return null;
  return (
    <Alert tone="warning" title={t('runs.workers.noneTitle')}>
      {online.length === 0 ? t('runs.workers.noneOnline') : t('runs.workers.noExecutor')}
    </Alert>
  );
}
