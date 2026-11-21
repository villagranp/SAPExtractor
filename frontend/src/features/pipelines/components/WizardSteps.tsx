import { useState } from 'react';
import { useFieldArray, useWatch, type UseFormReturn } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useDestinations, type DestinationSummary } from '@/features/destinations/api/queries';
import {
  DestinationStatusPill,
  LastTestPill,
} from '@/features/destinations/components/DestinationPills';
import {
  FILTER_OPERATORS,
  useSources,
  type SourceDetail,
  type SourceSummary,
} from '@/features/sources/api/queries';
import { columnType } from '@/features/sources/components/format';
import {
  DeltaSummary,
  EnvironmentPill,
  SourceStatusPill,
} from '@/features/sources/components/SourcePills';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Checkbox,
  DataTable,
  EmptyState,
  SegmentedControl,
  SelectField,
  Spinner,
  StatusPill,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  DELETE_HANDLINGS,
  LOG_LEVELS,
  SCHEMA_EVOLUTIONS,
  type ConcurrencyGroup,
  type ExtractionMode,
  type LoadMode,
} from '../api/queries';
import { extractionModesFor, type PipelineForm } from '../schemas/pipelineForm';

type Form = UseFormReturn<PipelineForm>;
const CHOICES = 50;

function useFieldError(form: Form) {
  const { t } = useTranslation();
  return (name: keyof PipelineForm) => {
    const message = form.formState.errors[name]?.message;
    return message ? t(`pipelines.errors.${message}`, { defaultValue: message }) : undefined;
  };
}

// ------------------------------------------------------------------ origen
export function SourceStep({ form, onPick }: { form: Form; onPick: (s: SourceSummary) => void }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const sources = useSources({
    page: 1,
    page_size: CHOICES,
    ...(q.trim() ? { q: q.trim() } : {}),
  });
  const selected = useWatch({ control: form.control, name: 'source_id' });
  const error = useFieldError(form)('source_id');
  const items = (sources.data?.items ?? []).filter((s) => s.status !== 'deprecated');

  if (sources.data?.total === 0 && !q.trim()) {
    return (
      <EmptyState
        title={t('pipelines.wizard.noSourcesTitle')}
        action={<Link to="/sources/new">{t('pipelines.wizard.createSource')}</Link>}
      >
        <p>{t('pipelines.wizard.noSources')}</p>
      </EmptyState>
    );
  }
  return (
    <div className="sx-stack">
      <p>{t('pipelines.wizard.sourceIntro')}</p>
      <TextField
        label={t('pipelines.wizard.searchSource')}
        type="search"
        placeholder={t('pipelines.wizard.searchSourcePlaceholder')}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
        }}
      />
      {sources.error && <Alert tone="danger">{describeError(sources.error).message}</Alert>}
      {error && <Alert tone="danger">{error}</Alert>}
      {sources.isPending ? (
        <Spinner label={t('common.loading')} />
      ) : (
        <fieldset className="sx-pipelines__choices">
          <legend className="sx-visually-hidden">{t('pipelines.wizard.sources')}</legend>
          {items.map((source) => (
            <label key={source.id} className="sx-pipelines__choice">
              <input
                type="radio"
                name="pipeline-source"
                checked={selected === source.id}
                onChange={() => {
                  onPick(source);
                }}
              />
              <span className="sx-pipelines__choice-main">
                <strong>{source.code}</strong>
                <span className="sx-muted">{source.name}</span>
              </span>
              <span className="sx-row">
                <EnvironmentPill environment={source.sap_system.environment} />
                <span className="sx-muted">{source.sap_system.code}</span>
              </span>
              <DeltaSummary method={source.delta_method} semantics={source.delta_semantics} />
              <SourceStatusPill status={source.status} />
            </label>
          ))}
          {items.length === 0 && <p className="sx-muted">{t('pipelines.wizard.noMatches')}</p>}
        </fieldset>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ destino
export function DestinationStep({
  form,
  onPick,
}: {
  form: Form;
  onPick: (d: DestinationSummary) => void;
}) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const destinations = useDestinations({
    page: 1,
    page_size: CHOICES,
    ...(q.trim() ? { q: q.trim() } : {}),
  });
  const selected = useWatch({ control: form.control, name: 'destination_id' });
  const error = useFieldError(form)('destination_id');
  const items = destinations.data?.items ?? [];

  if (destinations.data?.total === 0 && !q.trim()) {
    return (
      <EmptyState
        title={t('pipelines.wizard.noDestinationsTitle')}
        action={<Link to="/destinations/new">{t('pipelines.wizard.createDestination')}</Link>}
      >
        <p>{t('pipelines.wizard.noDestinations')}</p>
      </EmptyState>
    );
  }
  return (
    <div className="sx-stack">
      <p>{t('pipelines.wizard.destinationIntro')}</p>
      <TextField
        label={t('pipelines.wizard.searchDestination')}
        type="search"
        placeholder={t('pipelines.wizard.searchDestinationPlaceholder')}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
        }}
      />
      {destinations.error && (
        <Alert tone="danger">{describeError(destinations.error).message}</Alert>
      )}
      {error && <Alert tone="danger">{error}</Alert>}
      {destinations.isPending ? (
        <Spinner label={t('common.loading')} />
      ) : (
        <fieldset className="sx-pipelines__choices">
          <legend className="sx-visually-hidden">{t('pipelines.wizard.destinations')}</legend>
          {items.map((destination) => (
            <label key={destination.id} className="sx-pipelines__choice">
              <input
                type="radio"
                name="pipeline-destination"
                checked={selected === destination.id}
                onChange={() => {
                  onPick(destination);
                }}
              />
              <span className="sx-pipelines__choice-main">
                <strong>{destination.code}</strong>
                <span className="sx-muted">{destination.name}</span>
              </span>
              <span className="sx-row">
                <EnvironmentPill environment={destination.environment} />
                <span className="sx-muted">
                  {t(`destinations.types.${destination.type}.label`)}
                </span>
              </span>
              <LastTestPill destination={destination} />
              <DestinationStatusPill status={destination.status} />
            </label>
          ))}
          {items.length === 0 && <p className="sx-muted">{t('pipelines.wizard.noMatches')}</p>}
        </fieldset>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ carga
function ColumnMapping({ form, source }: { form: Form; source: SourceDetail }) {
  const { t } = useTranslation();
  const rows = source.columns.map((column, index) => ({ column, index }));
  const error = useFieldError(form)('columns');
  return (
    <div className="sx-stack sx-pipelines__fit">
      <div>
        <h3 className="sx-pipelines__subtitle">{t('pipelines.wizard.mappingTitle')}</h3>
        <p className="sx-muted">{t('pipelines.wizard.mappingHint')}</p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="sx-pipelines__mapping">
        <DataTable
          caption={t('pipelines.wizard.mappingTitle')}
          rows={rows}
          rowKey={(r) => r.column.name}
          columns={[
            {
              key: 'include',
              header: t('pipelines.wizard.include'),
              width: '110px',
              render: ({ column, index }) =>
                column.is_key ? (
                  // Una columna clave no se puede excluir: se muestra fija, sin casilla.
                  <StatusPill tone="info">{t('pipelines.wizard.keyColumn')}</StatusPill>
                ) : (
                  <Checkbox
                    label={
                      <span className="sx-visually-hidden">
                        {t('pipelines.wizard.includeColumn', { column: column.name })}
                      </span>
                    }
                    {...form.register(`columns.${index}.include`)}
                  />
                ),
            },
            {
              key: 'column',
              header: t('pipelines.wizard.sapColumn'),
              render: ({ column }) => (
                <span>
                  <code>{column.name}</code>
                  <br />
                  <span className="sx-muted">{column.label ?? '—'}</span>
                </span>
              ),
            },
            {
              key: 'type',
              header: t('pipelines.wizard.sapType'),
              render: ({ column }) => <code>{columnType(column)}</code>,
            },
            {
              key: 'target',
              header: t('pipelines.wizard.targetColumn'),
              render: ({ column, index }) => (
                <input
                  className="sx-input"
                  aria-label={t('pipelines.wizard.targetColumnOf', { column: column.name })}
                  placeholder={column.name.replace(/[^A-Za-z0-9_]+/g, '_').toUpperCase()}
                  maxLength={128}
                  autoComplete="off"
                  spellCheck={false}
                  {...form.register(`columns.${index}.target`)}
                />
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}

function FilterRows({ form, source }: { form: Form; source: SourceDetail }) {
  const { t } = useTranslation();
  const { fields, append, remove } = useFieldArray({ control: form.control, name: 'filters' });
  const [own, rows] = useWatch({ control: form.control, name: ['own_filters', 'filters'] });
  const errors = form.formState.errors.filters;
  const filterable = source.columns.filter(
    (c) => c.filterable && !['Edm.Binary', 'Edm.Time', 'Edm.TimeOfDay'].includes(c.edm_type),
  );
  const inherited = source.default_filters
    .map((f) => `${f.field} ${t(`sources.operators.${f.op}`)} ${String(f.value)}`)
    .join(` ${t('sources.wizard.and')} `);

  return (
    <div className="sx-stack">
      <div>
        <h3 className="sx-pipelines__subtitle">{t('pipelines.wizard.filtersTitle')}</h3>
        <p className="sx-muted">
          {inherited
            ? t('pipelines.wizard.sourceFilters', { filters: inherited })
            : t('pipelines.wizard.noSourceFilters')}
        </p>
      </div>
      <Checkbox
        label={t('pipelines.wizard.ownFilters')}
        description={t('pipelines.wizard.ownFiltersHint')}
        {...form.register('own_filters')}
      />
      {own && (
        <>
          {fields.map((item, index) => {
            const column = source.columns.find((c) => c.name === rows[index]?.field);
            const rowError = errors?.[index];
            return (
              <fieldset key={item.id} className="sx-pipelines__filter-row">
                <legend className="sx-visually-hidden">
                  {t('sources.wizard.condition', { n: index + 1 })}
                </legend>
                <SelectField
                  label={t('sources.wizard.field')}
                  placeholder={t('sources.wizard.chooseField')}
                  error={rowError?.field && t('pipelines.errors.required')}
                  options={filterable.map((c) => ({
                    value: c.name,
                    label: c.label ? `${c.name} (${c.label})` : c.name,
                  }))}
                  {...form.register(`filters.${index}.field`)}
                />
                <SelectField
                  label={t('sources.wizard.operator')}
                  options={FILTER_OPERATORS.map((op) => ({
                    value: op,
                    label: t(`sources.operators.${op}`),
                  }))}
                  {...form.register(`filters.${index}.op`)}
                />
                {column?.edm_type === 'Edm.Boolean' ? (
                  <SelectField
                    label={t('sources.wizard.value')}
                    options={[
                      { value: 'true', label: t('common.yes') },
                      { value: 'false', label: t('common.no') },
                    ]}
                    {...form.register(`filters.${index}.value`)}
                  />
                ) : (
                  <TextField
                    label={t('sources.wizard.value')}
                    error={
                      rowError?.value?.message &&
                      t(`pipelines.errors.${rowError.value.message}`, {
                        defaultValue: rowError.value.message,
                      })
                    }
                    {...form.register(`filters.${index}.value`)}
                  />
                )}
                <Button
                  variant="secondary"
                  onClick={() => {
                    remove(index);
                  }}
                >
                  {t('sources.wizard.removeCondition')}
                </Button>
              </fieldset>
            );
          })}
          {fields.length === 0 && <p className="sx-muted">{t('pipelines.wizard.noOwnFilters')}</p>}
          <div>
            <Button
              variant="secondary"
              disabled={fields.length >= 20 || filterable.length === 0}
              onClick={() => {
                append({ field: filterable[0]?.name ?? '', op: 'eq', value: '' });
              }}
            >
              {t('sources.wizard.addCondition')}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

export function LoadStep({
  form,
  source,
  supported,
  modeLocked,
}: {
  form: Form;
  source: SourceDetail | undefined;
  supported: LoadMode[];
  /** En un pipeline activo o en pausa el modo de extracción ya no cambia. */
  modeLocked: boolean;
}) {
  const { t } = useTranslation();
  const error = useFieldError(form);
  const [extraction, loadMode, columns] = useWatch({
    control: form.control,
    name: ['extraction_mode', 'load_mode', 'columns'],
  });
  if (!source || columns.length !== source.columns.length) {
    return <Spinner label={t('pipelines.wizard.readingSource')} />;
  }
  const extractionModes: ExtractionMode[] = modeLocked ? [extraction] : extractionModesFor(source);
  const loadModes = supported.includes(loadMode) ? supported : [...supported, loadMode];

  return (
    <div className="sx-stack sx-pipelines__fit">
      <SegmentedControl
        label={t('pipelines.fields.extractionMode')}
        value={extraction}
        options={extractionModes.map((value) => ({
          value,
          label: t(`pipelines.extractionModes.${value}`),
        }))}
        hint={
          modeLocked
            ? t('pipelines.wizard.modeLocked')
            : t(`pipelines.extractionHints.${extraction}`)
        }
        onChange={(value) => {
          form.setValue('extraction_mode', value, { shouldDirty: true });
        }}
      />
      <SegmentedControl
        label={t('pipelines.fields.loadMode')}
        value={loadMode}
        options={loadModes.map((value) => ({ value, label: t(`pipelines.loadModes.${value}`) }))}
        hint={t(`pipelines.loadHints.${loadMode}`)}
        onChange={(value) => {
          form.setValue('load_mode', value, { shouldDirty: true });
        }}
      />
      <div className="sx-pipelines__pair">
        <TextField
          label={t('pipelines.fields.targetName')}
          hint={t('pipelines.wizard.targetNameHint', {
            name: `${source.sap_system.code}_${source.code}`,
          })}
          autoComplete="off"
          spellCheck={false}
          error={error('target_name')}
          {...form.register('target_name')}
        />
        <SelectField
          label={t('pipelines.fields.schemaEvolution')}
          hint={t('pipelines.wizard.schemaEvolutionHint')}
          options={SCHEMA_EVOLUTIONS.map((value) => ({
            value,
            label: t(`pipelines.schemaEvolutions.${value}`),
          }))}
          {...form.register('schema_evolution')}
        />
        <SelectField
          label={t('pipelines.fields.deleteHandling')}
          hint={t('pipelines.wizard.deleteHandlingHint')}
          options={[
            { value: 'auto', label: t('pipelines.deleteHandlings.auto') },
            ...DELETE_HANDLINGS.map((value) => ({
              value,
              label: t(`pipelines.deleteHandlings.${value}`),
            })),
          ]}
          {...form.register('delete_handling')}
        />
      </div>
      <div className="sx-pipelines__pair">
        <Checkbox
          label={t('pipelines.fields.createIfMissing')}
          description={t('pipelines.wizard.createIfMissingHint')}
          {...form.register('create_if_missing')}
        />
        <Checkbox
          label={t('pipelines.fields.technicalColumns')}
          description={t('pipelines.wizard.technicalColumnsHint')}
          {...form.register('technical_columns')}
        />
      </div>
      <ColumnMapping form={form} source={source} />
      <FilterRows form={form} source={source} />
    </div>
  );
}

// ------------------------------------------------------------------ nombre y ejecución
export function GeneralStep({
  form,
  editing,
  groups,
}: {
  form: Form;
  editing: boolean;
  groups: ConcurrencyGroup[];
}) {
  const { t } = useTranslation();
  const error = useFieldError(form);
  return (
    <div className="sx-stack">
      <div className="sx-pipelines__pair">
        <TextField
          label={t('pipelines.fields.code')}
          hint={editing ? t('pipelines.wizard.codeFixed') : t('pipelines.wizard.codeHint')}
          disabled={editing}
          autoComplete="off"
          spellCheck={false}
          error={error('code')}
          {...form.register('code')}
        />
        <TextField
          label={t('pipelines.fields.name')}
          error={error('name')}
          {...form.register('name')}
        />
      </div>
      <TextAreaField
        label={t('pipelines.fields.description')}
        rows={2}
        error={error('description')}
        {...form.register('description')}
      />
      <TextField
        label={t('pipelines.fields.tags')}
        hint={t('pipelines.wizard.tagsHint')}
        error={error('tags')}
        {...form.register('tags')}
      />
      <div className="sx-pipelines__pair">
        <SelectField
          label={t('pipelines.fields.priority')}
          hint={t('pipelines.wizard.priorityHint')}
          options={(['1', '2', '3', '4', '5'] as const).map((value) => ({
            value,
            label: t(`pipelines.priorities.${value}`),
          }))}
          {...form.register('priority')}
        />
        <TextField
          label={t('pipelines.fields.timeout')}
          hint={t('pipelines.wizard.timeoutHint')}
          inputMode="numeric"
          error={error('timeout_minutes')}
          {...form.register('timeout_minutes')}
        />
        <TextField
          label={t('pipelines.fields.maxAttempts')}
          hint={t('pipelines.wizard.maxAttemptsHint')}
          inputMode="numeric"
          error={error('max_attempts')}
          {...form.register('max_attempts')}
        />
        <SelectField
          label={t('pipelines.fields.logLevel')}
          options={LOG_LEVELS.map((value) => ({ value, label: t(`pipelines.logLevels.${value}`) }))}
          {...form.register('log_level')}
        />
        <TextField
          label={t('pipelines.fields.workerPool')}
          hint={t('pipelines.wizard.workerPoolHint')}
          placeholder="default"
          autoComplete="off"
          spellCheck={false}
          error={error('worker_pool')}
          {...form.register('worker_pool')}
        />
        {groups.length > 0 && (
          <SelectField
            label={t('pipelines.fields.concurrencyGroup')}
            placeholder={t('pipelines.wizard.noGroup')}
            hint={t('pipelines.wizard.groupHint')}
            error={error('concurrency_group')}
            options={groups.map((g) => ({
              value: g.code,
              label: t('pipelines.wizard.groupOption', { code: g.code, count: g.max_parallel }),
            }))}
            {...form.register('concurrency_group')}
          />
        )}
      </div>
    </div>
  );
}
