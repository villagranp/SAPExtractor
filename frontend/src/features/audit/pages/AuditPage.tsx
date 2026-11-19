import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  FilterBar,
  PageHeader,
  SelectField,
  StatusPill,
  TextField,
} from '@/shared/ui';
import {
  useAuditCatalog,
  useAuditIntegrity,
  useExportAudit,
  useVerifyAudit,
  type AuditCategory,
  type AuditFilters,
  type AuditOutcome,
} from '../api/queries';
import { AuditTrail } from '../components/AuditTrail';

const CATEGORIES: AuditCategory[] = [
  'auth',
  'identity',
  'config',
  'execution',
  'security',
  'system',
];
const OUTCOMES: AuditOutcome[] = ['success', 'failure', 'denied'];

/** Fecha del input (AAAA-MM-DD, hora local) → ISO; `endOfDay` incluye el día completo. */
function toIso(date: string, endOfDay = false): string | undefined {
  if (!date) return undefined;
  const value = new Date(`${date}T00:00:00`);
  if (endOfDay) value.setDate(value.getDate() + 1);
  return value.toISOString();
}

function IntegrityStatus() {
  const { t } = useTranslation();
  const integrity = useAuditIntegrity();
  const verify = useVerifyAudit();
  const canVerify = useCan('audit:export');
  const last = verify.data ?? integrity.data?.last_verification ?? null;
  return (
    <div className="sx-row">
      {last ? (
        <StatusPill tone={last.ok ? 'success' : 'danger'}>
          {last.ok
            ? t('audit.integrity.ok', { count: last.checked })
            : t('audit.integrity.fail', { seq: last.broken_seq })}
        </StatusPill>
      ) : (
        <StatusPill tone="neutral">{t('audit.integrity.never')}</StatusPill>
      )}
      {last && <span className="sx-muted">{formatDateTime(last.verified_at)}</span>}
      {canVerify && (
        <Button
          variant="secondary"
          onClick={() => {
            verify.mutate();
          }}
          disabled={verify.isPending}
        >
          {verify.isPending ? t('audit.integrity.verifying') : t('audit.integrity.verify')}
        </Button>
      )}
    </div>
  );
}

export function AuditPage() {
  const { t } = useTranslation();
  const catalog = useAuditCatalog();
  const exporter = useExportAudit();
  const canExport = useCan('audit:export');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [category, setCategory] = useState<AuditCategory | ''>('');
  const [outcome, setOutcome] = useState<AuditOutcome | ''>('');

  const fromIso = toIso(from);
  const toIsoValue = toIso(to, true);
  const filters: AuditFilters = {
    ...(fromIso ? { from: fromIso } : {}),
    ...(toIsoValue ? { to: toIsoValue } : {}),
    ...(actor.trim() ? { actor: actor.trim() } : {}),
    ...(action ? { action } : {}),
    ...(category ? { category } : {}),
    ...(outcome ? { outcome } : {}),
  };

  return (
    <>
      <PageHeader
        title={t('audit.title')}
        description={t('audit.description')}
        actions={<IntegrityStatus />}
      />
      <FilterBar
        actions={
          canExport && (
            <>
              <Button
                variant="secondary"
                disabled={exporter.isPending}
                onClick={() => {
                  exporter.mutate({ filters, format: 'csv' });
                }}
              >
                {t('audit.export.csv')}
              </Button>
              <Button
                variant="secondary"
                disabled={exporter.isPending}
                onClick={() => {
                  exporter.mutate({ filters, format: 'json' });
                }}
              >
                {t('audit.export.json')}
              </Button>
            </>
          )
        }
      >
        <TextField
          label={t('audit.filters.from')}
          type="date"
          value={from}
          onChange={(e) => {
            setFrom(e.target.value);
          }}
        />
        <TextField
          label={t('audit.filters.to')}
          type="date"
          value={to}
          onChange={(e) => {
            setTo(e.target.value);
          }}
        />
        <TextField
          label={t('audit.filters.actor')}
          value={actor}
          onChange={(e) => {
            setActor(e.target.value);
          }}
        />
        <SelectField
          label={t('audit.filters.category')}
          placeholder={t('common.all')}
          value={category}
          onChange={(e) => {
            setCategory(e.target.value as AuditCategory | '');
          }}
          options={CATEGORIES.map((c) => ({ value: c, label: t(`audit.categories.${c}`) }))}
        />
        <SelectField
          label={t('audit.filters.action')}
          placeholder={t('common.all')}
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
          }}
          options={(catalog.data ?? [])
            .filter((a) => !category || a.category === category)
            .map((a) => ({ value: a.action, label: a.action }))}
        />
        <SelectField
          label={t('audit.filters.outcome')}
          placeholder={t('common.all')}
          value={outcome}
          onChange={(e) => {
            setOutcome(e.target.value as AuditOutcome | '');
          }}
          options={OUTCOMES.map((o) => ({ value: o, label: t(`audit.outcomes.${o}`) }))}
        />
      </FilterBar>
      {exporter.error && <Alert tone="danger">{describeError(exporter.error).message}</Alert>}
      <AuditTrail key={JSON.stringify(filters)} filters={filters} />
    </>
  );
}
