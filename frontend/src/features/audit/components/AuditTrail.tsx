import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, DataTable, Pagination, type Column } from '@/shared/ui';
import { useAuditEvents, type AuditEvent, type AuditFilters } from '../api/queries';
import { AuditEventDialog } from './AuditEventDialog';
import { OutcomePill } from './OutcomePill';

const PAGE_SIZE = 25;

export interface AuditTrailProps {
  filters?: AuditFilters;
  /** Atajo para la pestaña «Actividad» de un recurso (usuario, pipeline, conexión…). */
  resourceId?: string;
}

/** Tabla de eventos de auditoría con detalle. Reutilizable desde cualquier recurso. */
export function AuditTrail({ filters = {}, resourceId }: AuditTrailProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const effective: AuditFilters = resourceId ? { ...filters, resource_id: resourceId } : filters;
  const events = useAuditEvents(effective, page, PAGE_SIZE);

  const columns: Column<AuditEvent>[] = [
    {
      key: 'when',
      header: t('audit.columns.when'),
      width: '170px',
      render: (e) => formatDateTime(e.occurred_at),
    },
    { key: 'actor', header: t('audit.columns.actor'), render: (e) => e.actor_name },
    { key: 'action', header: t('audit.columns.action'), render: (e) => <code>{e.action}</code> },
    {
      key: 'resource',
      header: t('audit.columns.resource'),
      render: (e) =>
        e.resource_type ? (
          <span>
            {e.resource_name ?? e.resource_id}
            <br />
            <span className="sx-muted">{e.resource_type}</span>
          </span>
        ) : (
          '—'
        ),
    },
    {
      key: 'outcome',
      header: t('audit.columns.outcome'),
      render: (e) => <OutcomePill outcome={e.outcome} />,
    },
  ];

  return (
    <>
      {events.error && <Alert tone="danger">{describeError(events.error).message}</Alert>}
      <DataTable
        caption={t('audit.title')}
        columns={columns}
        rows={events.data?.items ?? []}
        rowKey={(e) => e.id}
        loading={events.isPending}
        empty={t('audit.empty')}
        onRowClick={setSelected}
      />
      {events.data && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={events.data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
      <AuditEventDialog
        event={selected}
        onClose={() => {
          setSelected(null);
        }}
      />
    </>
  );
}
