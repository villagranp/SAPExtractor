import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  DataTable,
  EmptyState,
  FilterBar,
  PageHeader,
  usePageState,
  SelectField,
  StatusPill,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  DESTINATION_TYPES,
  ENVIRONMENTS,
  useDestinations,
  useDestinationTypes,
  type DestinationEnvironment,
  type DestinationStatus,
  type DestinationSummary,
  type DestinationType,
} from '../api/queries';
import { DestinationStatusPill, LastTestPill } from '../components/DestinationPills';

/** Tarjetas de los tipos disponibles: describen cada destino y abren el asistente. */
function TypeCards({ canManage }: { canManage: boolean }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const types = useDestinationTypes();
  if (!types.data) return null;
  // «Añadir, Reemplazar y Upsert (MERGE)»: lista natural del idioma, no «A · B · C».
  const list = new Intl.ListFormat(i18n.resolvedLanguage ?? 'es', { type: 'conjunction' });
  return (
    <div
      className="sx-two-columns"
      style={{ alignItems: 'stretch', marginBottom: 'var(--sx-space-5)' }}
    >
      {types.data.map((type) => (
        <Card key={type.type} title={type.label}>
          <div className="sx-stack">
            <p className="sx-muted" style={{ margin: 0 }}>
              {t(`destinations.types.${type.type}.description`)}
            </p>
            <p style={{ margin: 0 }}>
              {t('destinations.fields.loadModes')}:{' '}
              {list.format(type.supported_modes.map((m) => t(`destinations.loadModes.${m}`)))}
            </p>
            {canManage && (
              <div>
                <Button
                  variant="secondary"
                  onClick={() => void navigate(`/destinations/new?type=${type.type}`)}
                >
                  {t('destinations.newOfType', { type: type.label })}
                </Button>
              </div>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

export function DestinationsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const canManage = useCan('destination:manage');
  const [q, setQ] = useState('');
  const [type, setType] = useState<DestinationType | ''>('');
  const [environment, setEnvironment] = useState<DestinationEnvironment | ''>('');
  const [status, setStatus] = useState<DestinationStatus | ''>('');
  const { page, setPage, pageSize, setPageSize } = usePageState();
  const filtered = Boolean(q.trim() || type || environment || status);

  const destinations = useDestinations({
    page,
    page_size: pageSize,
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(type ? { type } : {}),
    ...(environment ? { environment } : {}),
    ...(status ? { status } : {}),
  });

  const columns: Column<DestinationSummary>[] = [
    {
      key: 'destination',
      header: t('destinations.fields.destination'),
      render: (d) => (
        <span>
          <strong>{d.code}</strong>
          <br />
          <span className="sx-muted">{d.name}</span>
        </span>
      ),
    },
    {
      key: 'type',
      header: t('destinations.fields.type'),
      render: (d) => t(`destinations.types.${d.type}.label`),
    },
    {
      key: 'env',
      header: t('destinations.fields.environment'),
      render: (d) => (
        <StatusPill tone={d.environment === 'PRD' ? 'warning' : 'neutral'}>
          {d.environment}
        </StatusPill>
      ),
    },
    {
      key: 'target',
      header: t('destinations.fields.target'),
      render: (d) => <code>{d.target}</code>,
    },
    {
      key: 'status',
      header: t('destinations.fields.status'),
      render: (d) => <DestinationStatusPill status={d.status} />,
    },
    {
      key: 'test',
      header: t('destinations.fields.lastTest'),
      render: (d) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <LastTestPill destination={d} />
          <span className="sx-muted">{formatDateTime(d.last_test_at)}</span>
        </span>
      ),
    },
  ];

  const newButton = canManage && (
    <Button onClick={() => void navigate('/destinations/new')}>{t('destinations.new')}</Button>
  );
  const reset = () => {
    setPage(1);
  };

  return (
    <>
      <PageHeader
        title={t('destinations.title')}
        description={t('destinations.description')}
        actions={newButton}
      />
      <TypeCards canManage={canManage} />
      <FilterBar>
        <TextField
          label={t('common.search')}
          type="search"
          value={q}
          placeholder={t('destinations.searchPlaceholder')}
          onChange={(e) => {
            setQ(e.target.value);
            reset();
          }}
        />
        <SelectField
          label={t('destinations.fields.type')}
          placeholder={t('common.all')}
          value={type}
          onChange={(e) => {
            setType(e.target.value as DestinationType | '');
            reset();
          }}
          options={DESTINATION_TYPES.map((value) => ({
            value,
            label: t(`destinations.types.${value}.label`),
          }))}
        />
        <SelectField
          label={t('destinations.fields.environment')}
          placeholder={t('common.all')}
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value as DestinationEnvironment | '');
            reset();
          }}
          options={ENVIRONMENTS.map((env) => ({
            value: env,
            label: t(`destinations.environments.${env}`),
          }))}
        />
        <SelectField
          label={t('destinations.fields.status')}
          placeholder={t('common.all')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as DestinationStatus | '');
            reset();
          }}
          options={(['active', 'draft', 'inactive'] as const).map((s) => ({
            value: s,
            label: t(`destinations.statuses.${s}`),
          }))}
        />
      </FilterBar>
      {destinations.error && (
        <Alert tone="danger">{describeError(destinations.error).message}</Alert>
      )}
      {destinations.data?.total === 0 && !filtered ? (
        // Sin botón propio: la cabecera y las tarjetas de tipo ya ofrecen el alta.
        <EmptyState title={t('destinations.emptyFirstTitle')}>
          <p>{t('destinations.emptyFirst')}</p>
        </EmptyState>
      ) : (
        <>
          <DataTable
            caption={t('destinations.title')}
            columns={columns}
            rows={destinations.data?.items ?? []}
            rowKey={(d) => d.id}
            loading={destinations.isPending}
            empty={t('destinations.empty')}
            onRowClick={(d) => void navigate(`/destinations/${d.id}`)}
            pagination={{
              page,
              pageSize,
              total: destinations.data?.total ?? 0,
              onPageChange: setPage,
              onPageSizeChange: setPageSize,
            }}
          />
        </>
      )}
    </>
  );
}
