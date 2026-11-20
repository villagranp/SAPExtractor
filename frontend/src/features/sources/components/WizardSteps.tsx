import type { TFunction } from 'i18next';
import { useFieldArray, useWatch, type UseFormReturn } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Button,
  Checkbox,
  DataTable,
  SegmentedControl,
  SelectField,
  StatusPill,
  TextField,
} from '@/shared/ui';
import {
  DELTA_SEMANTICS,
  FILTER_OPERATORS,
  WATERMARK_KINDS,
  type DeltaSemantics,
  type SchemaColumn,
} from '../api/queries';
import { deltaMethodsFor, isOdp, type SourceForm } from '../schemas/sourceForm';
import { columnType } from './format';

interface StepProps {
  form: UseFormReturn<SourceForm>;
  columns: SchemaColumn[];
}

// ------------------------------------------------------------------ esquema y claves
export function SchemaKeysStep({ form, columns }: StepProps) {
  const { t } = useTranslation();
  const keys = useWatch({ control: form.control, name: 'key_fields' });
  const toggle = (name: string, checked: boolean) => {
    const next = checked ? [...keys, name] : keys.filter((k) => k !== name);
    // Mantiene el orden del $metadata.
    const ordered = columns.map((c) => c.name).filter((n) => next.includes(n));
    form.setValue('key_fields', ordered, { shouldValidate: form.formState.isSubmitted });
  };
  return (
    <div className="sx-stack">
      <p>{t('sources.wizard.keysIntro')}</p>
      {form.formState.errors.key_fields && <Alert tone="danger">{t('sources.errors.keys')}</Alert>}
      <DataTable
        caption={t('sources.wizard.columnsCaption')}
        rows={columns}
        rowKey={(c) => c.name}
        columns={[
          {
            key: 'key',
            header: t('sources.wizard.keyForDestination'),
            width: '120px',
            render: (c) => (
              <Checkbox
                label={<span className="sx-visually-hidden">{c.name}</span>}
                checked={keys.includes(c.name)}
                onChange={(e) => {
                  toggle(c.name, e.target.checked);
                }}
              />
            ),
          },
          {
            key: 'name',
            header: t('sources.wizard.column'),
            render: (c) => (
              <span>
                <code>{c.name}</code>
                {c.is_key && (
                  <>
                    {' '}
                    <StatusPill tone="info">{t('sources.wizard.sapKey')}</StatusPill>
                  </>
                )}
                <br />
                <span className="sx-muted">{c.label ?? '—'}</span>
              </span>
            ),
          },
          {
            key: 'type',
            header: t('sources.wizard.sapType'),
            render: (c) => <code>{columnType(c)}</code>,
          },
          {
            key: 'filterable',
            header: t('sources.wizard.filterable'),
            render: (c) => (c.filterable ? t('common.yes') : t('common.no')),
          },
        ]}
      />
    </div>
  );
}

// ------------------------------------------------------------------ delta
interface DeltaStepProps extends StepProps {
  supportsDelta: boolean;
  watermarkCandidates: string[];
  deltaProcesses: Record<string, DeltaSemantics>;
  hint: { delta_process: string; semantics: DeltaSemantics } | null;
}

let timezones: string[] | null = null;
function timezoneOptions(): string[] {
  timezones ??= ['UTC', ...Intl.supportedValuesOf('timeZone').filter((z) => z !== 'UTC')];
  return timezones;
}

export function DeltaStep({
  form,
  columns,
  supportsDelta,
  watermarkCandidates,
  deltaProcesses,
  hint,
}: DeltaStepProps) {
  const { t } = useTranslation();
  const { setValue, register, formState, control } = form;
  const [type, method, semantics, process] = useWatch({
    control,
    name: ['source_type', 'delta_method', 'delta_semantics', 'delta_process'],
  });
  const methods = deltaMethodsFor(type, supportsDelta);
  const error = (name: keyof SourceForm) => {
    const message = formState.errors[name]?.message;
    return message ? t(`sources.errors.${message}`, { defaultValue: message }) : undefined;
  };
  const mapped = deltaProcesses[process.trim().toUpperCase()];

  return (
    <div className="sx-stack">
      <SegmentedControl
        label={t('sources.fields.deltaMethod')}
        value={methods.includes(method) ? method : (methods[0] ?? 'NONE')}
        options={methods.map((value) => ({ value, label: t(`sources.deltaMethods.${value}`) }))}
        hint={t(`sources.deltaHints.${method}`)}
        onChange={(value) => {
          setValue('delta_method', value);
        }}
      />
      {isOdp(type) && !supportsDelta && (
        <Alert tone="info">{t('sources.wizard.noChangeTracking')}</Alert>
      )}

      {method === 'ODQ' && (
        <div className="sx-stack">
          {Object.keys(deltaProcesses).length > 0 && (
            <TextField
              label={t('sources.wizard.deltaProcess')}
              hint={t('sources.wizard.deltaProcessHint')}
              maxLength={10}
              {...register('delta_process', {
                onChange: (e: { target: { value: string } }) => {
                  const found = deltaProcesses[e.target.value.trim().toUpperCase()];
                  if (found) setValue('delta_semantics', found);
                },
              })}
            />
          )}
          {Object.keys(deltaProcesses).length > 0 && process.trim() && !mapped && (
            <Alert tone="warning">{t('sources.wizard.unknownProcess')}</Alert>
          )}
          {hint && (
            <Alert tone="info">
              {t('sources.wizard.processHint', {
                process: hint.delta_process,
                semantics: t(`sources.semantics.${hint.semantics}`),
              })}
            </Alert>
          )}
          <SelectField
            label={t('sources.fields.deltaSemantics')}
            value={semantics}
            hint={t(`sources.semanticsHints.${semantics}`)}
            onChange={(e) => {
              setValue('delta_semantics', e.target.value as DeltaSemantics);
            }}
            options={DELTA_SEMANTICS.map((value) => ({
              value,
              label: t(`sources.semantics.${value}`),
            }))}
          />
        </div>
      )}

      {method === 'WATERMARK' && (
        <div className="sx-grid">
          <SelectField
            label={t('sources.fields.watermarkField')}
            placeholder={t('sources.wizard.chooseField')}
            error={error('watermark_field')}
            options={watermarkCandidates.map((name) => {
              const column = columns.find((c) => c.name === name);
              return { value: name, label: column ? `${name} · ${columnType(column)}` : name };
            })}
            {...register('watermark_field')}
          />
          <SelectField
            label={t('sources.fields.watermarkKind')}
            options={WATERMARK_KINDS.map((value) => ({
              value,
              label: t(`sources.watermarkKinds.${value}`),
            }))}
            {...register('watermark_kind')}
          />
          <SelectField
            label={t('sources.fields.watermarkTimezone')}
            placeholder={t('sources.wizard.chooseTimezone')}
            hint={t('sources.wizard.timezoneHint')}
            error={error('watermark_timezone')}
            options={timezoneOptions().map((z) => ({ value: z, label: z }))}
            {...register('watermark_timezone')}
          />
          <TextField
            label={t('sources.fields.watermarkOverlap')}
            hint={t('sources.wizard.overlapHint')}
            inputMode="numeric"
            error={error('watermark_overlap_seconds')}
            {...register('watermark_overlap_seconds')}
          />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ filtros
export function FiltersStep({ form, columns }: StepProps) {
  const { t } = useTranslation();
  const { fields, append, remove } = useFieldArray({ control: form.control, name: 'filters' });
  const filterable = columns.filter(
    (c) => c.filterable && !['Edm.Binary', 'Edm.Time', 'Edm.TimeOfDay'].includes(c.edm_type),
  );
  const [type, rows] = useWatch({ control: form.control, name: ['source_type', 'filters'] });
  const errors = form.formState.errors.filters;

  return (
    <div className="sx-stack">
      <p>{t('sources.wizard.filtersIntro')}</p>
      {isOdp(type) && <Alert tone="warning">{t('sources.wizard.filtersOdpWarning')}</Alert>}
      {fields.length === 0 && <p className="sx-muted">{t('sources.wizard.noFilters')}</p>}
      {fields.map((item, index) => {
        const column = columns.find((c) => c.name === rows[index]?.field);
        const rowError = errors?.[index];
        return (
          <fieldset key={item.id} className="sx-sources__filter-row">
            <legend className="sx-visually-hidden">
              {t('sources.wizard.condition', { n: index + 1 })}
            </legend>
            <SelectField
              label={t('sources.wizard.field')}
              placeholder={t('sources.wizard.chooseField')}
              error={rowError?.field && t('sources.errors.required')}
              options={filterable.map((c) => ({
                value: c.name,
                label: c.label ? `${c.name} · ${c.label}` : c.name,
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
                hint={column ? valueHint(column, t) : undefined}
                error={
                  rowError?.value?.message &&
                  t(`sources.errors.${rowError.value.message}`, {
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
    </div>
  );
}

function valueHint(column: SchemaColumn, t: TFunction): string {
  if (column.edm_type === 'Edm.DateTime' && column.display_format === 'Date') {
    return t('sources.valueHints.date');
  }
  if (column.edm_type === 'Edm.DateTime') return t('sources.valueHints.datetime');
  if (column.edm_type === 'Edm.DateTimeOffset') return t('sources.valueHints.datetimeOffset');
  if (column.edm_type === 'Edm.Decimal') return t('sources.valueHints.decimal');
  if (column.edm_type.startsWith('Edm.Int')) return t('sources.valueHints.integer');
  if (column.max_length !== null) return t('sources.valueHints.textMax', { n: column.max_length });
  return t('sources.valueHints.text');
}
