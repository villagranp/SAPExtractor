import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/shared/format';
import { Dialog, DetailList } from '@/shared/ui';
import type { AuditEvent } from '../api/queries';
import { OutcomePill } from './OutcomePill';

function show(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

interface Change {
  before?: unknown;
  after?: unknown;
}

function isChange(value: unknown): value is Change {
  return typeof value === 'object' && value !== null && ('before' in value || 'after' in value);
}

export function AuditEventDialog({
  event,
  onClose,
}: {
  event: AuditEvent | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const changes = Object.entries(event?.changes ?? {});
  return (
    <Dialog open={event !== null} title={t('audit.detail.title')} onClose={onClose} size="lg">
      {event && (
        <div className="sx-stack">
          <DetailList
            items={[
              { label: t('audit.columns.when'), value: formatDateTime(event.occurred_at) },
              { label: t('audit.columns.action'), value: <code>{event.action}</code> },
              {
                label: t('audit.columns.category'),
                value: t(`audit.categories.${event.category}`),
              },
              { label: t('audit.columns.outcome'), value: <OutcomePill outcome={event.outcome} /> },
              {
                label: t('audit.columns.actor'),
                value: `${event.actor_name} (${t(`audit.actorTypes.${event.actor_type}`)})`,
              },
              {
                label: t('audit.columns.resource'),
                value: event.resource_type
                  ? `${event.resource_type} · ${event.resource_name ?? event.resource_id ?? ''}`
                  : '—',
              },
              { label: t('audit.detail.reason'), value: event.reason },
              { label: t('audit.detail.seq'), value: event.seq },
            ]}
          />
          <section>
            <h3>{t('audit.detail.changes')}</h3>
            {changes.length === 0 ? (
              <p className="sx-muted">{t('audit.detail.noChanges')}</p>
            ) : (
              <table className="sx-diff">
                <thead>
                  <tr>
                    <th>{t('audit.detail.field')}</th>
                    <th>{t('audit.detail.before')}</th>
                    <th>{t('audit.detail.after')}</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map(([field, change]) => (
                    <tr key={field}>
                      <td>
                        <code>{field}</code>
                      </td>
                      <td className="sx-diff__before">
                        {isChange(change) ? show(change.before) : '—'}
                      </td>
                      <td className="sx-diff__after">
                        {isChange(change) ? show(change.after) : show(change)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section>
            <h3>{t('audit.detail.context')}</h3>
            <pre className="sx-code-block">{JSON.stringify(event.context, null, 2)}</pre>
          </section>
          <p className="sx-muted">
            {t('audit.detail.hash')}: <code>{event.hash}</code>
          </p>
        </div>
      )}
    </Dialog>
  );
}
