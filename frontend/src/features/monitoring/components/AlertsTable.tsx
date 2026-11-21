import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, Button, ConfirmDialog, DataTable, Pagination, type Column } from '@/shared/ui';
import {
  useAckAlert,
  useAlerts,
  useResolveAlert,
  type Alert as AlertRow,
  type AlertStatus,
} from '../api/queries';
import { AlertStatusPill, SeverityPill } from './MonitoringParts';

const PAGE_SIZE = 25;

function resourceLink(alert: AlertRow): string | null {
  if (typeof alert.details.run_id === 'string') return `/runs/${alert.details.run_id}`;
  if (alert.pipeline_id) return `/pipelines/${alert.pipeline_id}`;
  if (alert.resource_type === 'sap_system') return `/sap-systems/${alert.resource_id}`;
  if (alert.resource_type === 'destination') return `/destinations/${alert.resource_id}`;
  return null;
}

/** Alertas vivas (con reconocer y cerrar) o el historial completo. */
export function AlertsTable({ live, status }: { live: boolean; status?: AlertStatus }) {
  const { t } = useTranslation();
  const canAck = useCan('alert:ack');
  const [page, setPage] = useState(1);
  const [closing, setClosing] = useState<AlertRow | null>(null);
  const alerts = useAlerts({
    page,
    page_size: PAGE_SIZE,
    ...(live ? { live: true } : {}),
    ...(status ? { status } : {}),
  });
  const ack = useAckAlert();
  const resolve = useResolveAlert();
  const error = ack.error ?? alerts.error;

  const columns: Column<AlertRow>[] = [
    {
      key: 'severity',
      header: t('monitoring.alerts.severity'),
      render: (a) => <SeverityPill severity={a.severity} />,
    },
    {
      key: 'alert',
      header: t('monitoring.alerts.alert'),
      render: (a) => {
        const link = resourceLink(a);
        return (
          <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
            <strong>
              {t(`monitoring.ruleTypes.${a.type}.label`)}:{' '}
              {link ? <Link to={link}>{a.resource_name}</Link> : a.resource_name}
            </strong>
            <span>{a.message}</span>
          </span>
        );
      },
    },
    {
      key: 'since',
      header: t('monitoring.alerts.since'),
      render: (a) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <span>{formatDateTime(a.first_seen_at)}</span>
          {a.count > 1 && (
            <span className="sx-muted">{t('monitoring.alerts.count', { count: a.count })}</span>
          )}
        </span>
      ),
    },
    {
      key: 'status',
      header: t('monitoring.alerts.status'),
      render: (a) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <AlertStatusPill status={a.status} />
          {a.acknowledged_by && a.status !== 'resolved' && (
            <span className="sx-muted">{a.acknowledged_by}</span>
          )}
          {a.resolved_at && <span className="sx-muted">{formatDateTime(a.resolved_at)}</span>}
        </span>
      ),
    },
    ...(canAck && live
      ? [
          {
            key: 'actions',
            header: t('common.actions'),
            render: (a: AlertRow) => (
              <span className="sx-row">
                {a.status === 'open' && (
                  <Button
                    variant="secondary"
                    disabled={ack.isPending}
                    onClick={() => {
                      ack.mutate(a.id);
                    }}
                  >
                    {t('monitoring.alerts.ack')}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={() => {
                    setClosing(a);
                  }}
                >
                  {t('monitoring.alerts.resolve')}
                </Button>
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      {error && <Alert tone="danger">{describeError(error).message}</Alert>}
      <DataTable
        caption={live ? t('monitoring.alerts.liveTitle') : t('monitoring.alerts.historyTitle')}
        columns={columns}
        rows={alerts.data?.items ?? []}
        rowKey={(a) => a.id}
        loading={alerts.isPending}
        empty={live ? t('monitoring.alerts.emptyLive') : t('monitoring.alerts.emptyHistory')}
      />
      {alerts.data && alerts.data.total > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={alerts.data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
      <ConfirmDialog
        key={closing?.id ?? 'none'}
        open={closing !== null}
        title={t('monitoring.alerts.resolveTitle')}
        message={t('monitoring.alerts.resolveMessage')}
        confirmLabel={t('monitoring.alerts.resolve')}
        cancelLabel={t('common.cancel')}
        busy={resolve.isPending}
        reasonLabel={t('monitoring.alerts.reason')}
        error={
          resolve.error ? (
            <Alert tone="danger">{describeError(resolve.error).message}</Alert>
          ) : undefined
        }
        onConfirm={(reason) => {
          if (!closing) return;
          resolve.mutate(
            { id: closing.id, reason },
            {
              onSuccess: () => {
                setClosing(null);
              },
            },
          );
        }}
        onClose={() => {
          resolve.reset();
          setClosing(null);
        }}
      />
    </>
  );
}
