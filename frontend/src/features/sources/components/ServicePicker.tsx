import { useState } from 'react';
import { useWatch, type UseFormReturn } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import {
  useCatalog,
  useEntitySets,
  useSapSystems,
  type CatalogService,
  type EntitySet,
  type ServiceKind,
} from '@/features/sap-systems/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  DataTable,
  FilterBar,
  Pagination,
  SegmentedControl,
  SelectField,
  StatusPill,
  TextField,
} from '@/shared/ui';
import { SOURCE_TYPES, type SourceType } from '../api/queries';
import type { SourceForm } from '../schemas/sourceForm';

const PAGE_SIZE = 10;
const KINDS_FOR: Record<SourceType, ServiceKind[]> = {
  ODP_EXTRACTOR: ['odp'],
  CDS_ODP: ['odp'],
  CDS_ODATA: ['cds', 'other', 'unknown'],
};

/** Entity sets que sirven para cada tipo: EntityOf… (extractor), FactsOf… (CDS) o normales. */
function fitsType(set: EntitySet, type: SourceType): boolean {
  if (type === 'ODP_EXTRACTOR') return set.odp_role === 'entity';
  if (type === 'CDS_ODP') return ['facts', 'attributes', 'texts'].includes(set.odp_role ?? '');
  return set.odp_role === null;
}

export function ServicePicker({ form }: { form: UseFormReturn<SourceForm> }) {
  const { t } = useTranslation();
  const { setValue, formState, control } = form;
  const [systemId, type, serviceName, serviceVersion, entitySet] = useWatch({
    control,
    name: ['sap_system_id', 'source_type', 'service_name', 'service_version', 'entity_set'],
  });
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);

  const systems = useSapSystems({ status: 'active', page: 1, page_size: 100 });
  const catalog = useCatalog(systemId, {
    page,
    page_size: PAGE_SIZE,
    type: KINDS_FOR[type],
    ...(search.trim() ? { search: search.trim() } : {}),
  });
  const selectedService: CatalogService | null = serviceName
    ? (catalog.data?.items.find(
        (s) => s.technical_name === serviceName && s.version === serviceVersion,
      ) ?? {
        technical_name: serviceName,
        version: serviceVersion,
        service_id: serviceName,
        title: null,
        description: null,
        service_path: null,
        entity_sets: null,
        odp_entity_sets: [],
        kind: 'unknown',
        classified_by: null,
        supports_delta: null,
      })
    : null;
  const sets = useEntitySets(systemId, selectedService);
  const candidates = (sets.data?.entity_sets ?? []).filter((s) => fitsType(s, type));

  const pick = (field: 'service_name' | 'entity_set', value: string) => {
    setValue(field, value, { shouldValidate: formState.isSubmitted });
  };
  const resetService = () => {
    pick('service_name', '');
    pick('entity_set', '');
    setValue('service_version', 1);
  };

  return (
    <div className="sx-stack">
      <SelectField
        label={t('sources.fields.system')}
        placeholder={t('sources.wizard.chooseSystem')}
        value={systemId}
        error={formState.errors.sap_system_id && t('sources.errors.required')}
        hint={t('sources.wizard.systemHint')}
        onChange={(e) => {
          setValue('sap_system_id', e.target.value, { shouldValidate: formState.isSubmitted });
          resetService();
          setPage(1);
        }}
        options={(systems.data?.items ?? []).map((s) => ({
          value: s.id,
          label: `${s.code} · ${s.name} (${s.environment})`,
        }))}
      />
      <SegmentedControl
        label={t('sources.fields.type')}
        value={type}
        options={SOURCE_TYPES.map((value) => ({ value, label: t(`sources.types.${value}`) }))}
        hint={t(`sources.typeHints.${type}`)}
        onChange={(value) => {
          setValue('source_type', value);
          resetService();
          setPage(1);
        }}
      />

      {systemId && (
        <>
          <FilterBar>
            <TextField
              label={t('sources.wizard.searchService')}
              type="search"
              value={search}
              placeholder={t('sources.wizard.searchServicePlaceholder')}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </FilterBar>
          {catalog.error && <Alert tone="danger">{describeError(catalog.error).message}</Alert>}
          <DataTable
            caption={t('sources.wizard.services')}
            loading={catalog.isPending}
            rows={catalog.data?.items ?? []}
            rowKey={(s) => `${s.technical_name};${String(s.version)}`}
            empty={t('sources.wizard.noServices')}
            columns={[
              {
                key: 'name',
                header: t('sources.wizard.service'),
                render: (s) => (
                  <span>
                    <code>{s.technical_name}</code>
                    {s.version > 1 && <span className="sx-muted"> v{s.version}</span>}
                    <br />
                    <span className="sx-muted">{s.description ?? s.title ?? '—'}</span>
                  </span>
                ),
              },
              {
                key: 'pick',
                header: <span className="sx-visually-hidden">{t('common.actions')}</span>,
                align: 'right',
                render: (s) => {
                  const chosen = s.technical_name === serviceName && s.version === serviceVersion;
                  return chosen ? (
                    <StatusPill tone="success">{t('sources.wizard.chosen')}</StatusPill>
                  ) : (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        pick('service_name', s.technical_name);
                        setValue('service_version', s.version);
                        pick('entity_set', '');
                      }}
                    >
                      {t('sources.wizard.choose')}
                    </Button>
                  );
                },
              },
            ]}
          />
          {catalog.data && catalog.data.total > PAGE_SIZE && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={catalog.data.total}
              onPageChange={setPage}
              labels={{
                previous: t('common.previous'),
                next: t('common.next'),
                summary: t('common.pageSummary'),
              }}
            />
          )}
          {formState.errors.service_name && (
            <Alert tone="danger">{t('sources.errors.serviceRequired')}</Alert>
          )}
        </>
      )}

      {selectedService && (
        <div className="sx-stack">
          {sets.isPending && <p className="sx-muted">{t('sources.wizard.readingMetadata')}</p>}
          {sets.error && <Alert tone="danger">{describeError(sets.error).message}</Alert>}
          {sets.data && candidates.length === 0 && (
            <Alert tone="warning">{t('sources.wizard.noEntitySets')}</Alert>
          )}
          {candidates.length > 0 && (
            <SelectField
              label={t('sources.fields.entitySet')}
              placeholder={t('sources.wizard.chooseEntitySet')}
              value={entitySet}
              error={formState.errors.entity_set && t('sources.errors.required')}
              onChange={(e) => {
                pick('entity_set', e.target.value);
              }}
              options={candidates.map((s) => ({
                value: s.name,
                label: `${s.name}${s.label ? ` · ${s.label}` : ''}${
                  s.supports_delta ? ` (${t('sources.wizard.withDelta')})` : ''
                }`,
              }))}
            />
          )}
        </div>
      )}
    </div>
  );
}
