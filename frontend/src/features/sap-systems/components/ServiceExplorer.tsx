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
  Dialog,
  FilterBar,
  Pagination,
  SelectField,
  StatusPill,
  TextField,
  type Column,
  type StatusTone,
} from '@/shared/ui';
import {
  SERVICE_KINDS,
  useCatalog,
  useEntitySets,
  useRefreshCatalog,
  type CatalogService,
  type EntitySet,
  type ServiceKind,
} from '../api/queries';

const PAGE_SIZE = 25;
const KIND_TONES: Record<ServiceKind, StatusTone> = {
  odp: 'success',
  cds: 'info',
  other: 'neutral',
  unknown: 'warning',
};

export function KindPill({ kind }: { kind: ServiceKind }) {
  const { t } = useTranslation();
  return <StatusPill tone={KIND_TONES[kind]}>{t(`sapSystems.explorer.kinds.${kind}`)}</StatusPill>;
}

function DeltaMark({ value }: { value: boolean | null }) {
  const { t } = useTranslation();
  if (value === null)
    return <span className="sx-muted">{t('sapSystems.explorer.deltaUnknown')}</span>;
  return value ? (
    <StatusPill tone="success">✔ {t('sapSystems.explorer.deltaYes')}</StatusPill>
  ) : (
    <span className="sx-muted">✖ {t('sapSystems.explorer.deltaNo')}</span>
  );
}

function EntitySetDetail({ entitySet }: { entitySet: EntitySet }) {
  const { t } = useTranslation();
  return (
    <DataTable
      caption={t('sapSystems.explorer.propertiesOf', { name: entitySet.name })}
      rows={entitySet.properties}
      rowKey={(p) => p.name}
      empty="—"
      columns={[
        {
          key: 'name',
          header: t('sapSystems.explorer.columns.property'),
          render: (p) => (
            <span>
              <code>{p.name}</code>
              {p.is_key && (
                <>
                  {' '}
                  <StatusPill tone="info">{t('sapSystems.explorer.key')}</StatusPill>
                </>
              )}
            </span>
          ),
        },
        {
          key: 'label',
          header: t('sapSystems.explorer.columns.label'),
          render: (p) => p.label ?? '—',
        },
        {
          key: 'type',
          header: t('sapSystems.explorer.columns.type'),
          render: (p) => {
            const size =
              p.max_length !== null
                ? `(${String(p.max_length)})`
                : p.precision !== null
                  ? `(${String(p.precision)}${p.scale !== null ? `,${String(p.scale)}` : ''})`
                  : '';
            return (
              <code>
                {p.edm_type}
                {size}
              </code>
            );
          },
        },
        {
          key: 'filter',
          header: t('sapSystems.explorer.columns.filterable'),
          render: (p) => (p.filterable ? t('common.yes') : t('common.no')),
        },
      ]}
    />
  );
}

function ServiceDialog({
  systemId,
  service,
  onClose,
}: {
  systemId: string;
  service: CatalogService | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const detail = useEntitySets(systemId, service);
  const navigate = useNavigate();
  const canCreateSource = useCan('source:manage');
  const [selected, setSelected] = useState<string | null>(null);
  const sets = detail.data?.entity_sets ?? [];
  const current =
    sets.find((s) => s.name === selected) ?? sets.find((s) => s.odp_role !== 'delta_links') ?? null;

  return (
    <Dialog
      open={service !== null}
      onClose={() => {
        setSelected(null);
        onClose();
      }}
      size="lg"
      title={service?.technical_name ?? ''}
      closeLabel={t('common.close')}
      footer={
        canCreateSource && service && current && current.odp_role !== 'delta_links' ? (
          <Button
            onClick={() =>
              void navigate(
                `/sources/new?${new URLSearchParams({
                  system: systemId,
                  service: service.technical_name,
                  version: String(service.version),
                  entitySet: current.name,
                }).toString()}`,
              )
            }
          >
            {t('sapSystems.explorer.createSource')}
          </Button>
        ) : undefined
      }
    >
      {service?.description && <p>{service.description}</p>}
      {detail.isPending && <p className="sx-muted">{t('sapSystems.explorer.readingMetadata')}</p>}
      {detail.error && <Alert tone="danger">{describeError(detail.error).message}</Alert>}
      {detail.data && (
        <div className="sx-stack">
          <p className="sx-row">
            <KindPill kind={detail.data.service.kind} />
            <span className="sx-muted">
              OData {detail.data.odata_version} · <code>{detail.data.service.service_path}</code>
            </span>
          </p>
          <DataTable
            caption={t('sapSystems.explorer.entitySets')}
            rows={sets}
            rowKey={(s) => s.name}
            onRowClick={(s) => {
              setSelected(s.name);
            }}
            columns={[
              {
                key: 'name',
                header: t('sapSystems.explorer.columns.entitySet'),
                render: (s) => (
                  <span>
                    <strong>{s.name}</strong>
                    {s.label && <span className="sx-muted"> · {s.label}</span>}
                  </span>
                ),
              },
              {
                key: 'role',
                header: t('sapSystems.explorer.columns.odpRole'),
                render: (s) => (s.odp_role ? t(`sapSystems.explorer.odpRoles.${s.odp_role}`) : '—'),
              },
              {
                key: 'delta',
                header: t('sapSystems.explorer.columns.delta'),
                render: (s) => <DeltaMark value={s.supports_delta} />,
              },
              {
                key: 'keys',
                header: t('sapSystems.explorer.columns.keys'),
                render: (s) => s.keys.join(', ') || '—',
              },
            ]}
          />
          {current && (
            <>
              <h3>{t('sapSystems.explorer.propertiesOf', { name: current.name })}</h3>
              <EntitySetDetail entitySet={current} />
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}

export function ServiceExplorer({ systemId }: { systemId: string }) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<ServiceKind | ''>('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<CatalogService | null>(null);
  const refresh = useRefreshCatalog(systemId);
  const catalog = useCatalog(systemId, {
    page,
    page_size: PAGE_SIZE,
    ...(search.trim() ? { search: search.trim() } : {}),
    ...(kind ? { type: [kind] } : {}),
  });
  const data = catalog.data;
  const error = catalog.error ?? refresh.error;

  const columns: Column<CatalogService>[] = [
    {
      key: 'service',
      header: t('sapSystems.explorer.columns.service'),
      render: (s) => (
        <span>
          <strong>{s.technical_name}</strong>
          {s.version > 1 && <span className="sx-muted"> v{s.version}</span>}
          <br />
          <span className="sx-muted">{s.description ?? s.title ?? '—'}</span>
        </span>
      ),
    },
    {
      key: 'kind',
      header: t('sapSystems.explorer.columns.kind'),
      render: (s) => <KindPill kind={s.kind} />,
    },
    {
      key: 'delta',
      header: t('sapSystems.explorer.columns.delta'),
      render: (s) => <DeltaMark value={s.supports_delta} />,
    },
    {
      key: 'odp',
      header: t('sapSystems.explorer.columns.entitySets'),
      render: (s) =>
        s.odp_entity_sets.length
          ? s.odp_entity_sets.join(', ')
          : s.entity_sets
            ? s.entity_sets.join(', ')
            : '—',
    },
  ];

  return (
    <div className="sx-stack">
      <FilterBar
        actions={
          <Button
            variant="secondary"
            onClick={() => {
              refresh.mutate();
            }}
            disabled={refresh.isPending}
          >
            {refresh.isPending
              ? t('sapSystems.explorer.refreshing')
              : t('sapSystems.explorer.refresh')}
          </Button>
        }
      >
        <TextField
          label={t('common.search')}
          type="search"
          value={search}
          placeholder={t('sapSystems.explorer.searchPlaceholder')}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <SelectField
          label={t('sapSystems.explorer.type')}
          placeholder={t('common.all')}
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as ServiceKind | '');
            setPage(1);
          }}
          options={SERVICE_KINDS.map((k) => ({
            value: k,
            label: t(`sapSystems.explorer.kinds.${k}`),
          }))}
        />
      </FilterBar>
      {data && (
        <p className="sx-muted" data-testid="catalog-summary">
          {t('sapSystems.explorer.counts', {
            odp: data.counts.odp ?? 0,
            cds: data.counts.cds ?? 0,
            other: data.counts.other ?? 0,
            unknown: data.counts.unknown ?? 0,
          })}{' '}
          · {t('sapSystems.explorer.fetchedAt', { date: formatDateTime(data.fetched_at) })}
          {data.from_cache && ` ${t('sapSystems.explorer.fromCache')}`}
        </p>
      )}
      {(data?.counts.unknown ?? 0) > 0 && (
        <Alert tone="info">{t('sapSystems.explorer.unknownHelp')}</Alert>
      )}
      {error && <Alert tone="danger">{describeError(error).message}</Alert>}
      <DataTable
        caption={t('sapSystems.detail.tabs.services')}
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(s) => `${s.technical_name}-${String(s.version)}`}
        loading={catalog.isPending}
        empty={
          catalog.isPending ? t('sapSystems.explorer.reading') : t('sapSystems.explorer.empty')
        }
        onRowClick={setOpen}
      />
      {data && data.total > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
      <ServiceDialog
        systemId={systemId}
        service={open}
        onClose={() => {
          setOpen(null);
        }}
      />
    </div>
  );
}
