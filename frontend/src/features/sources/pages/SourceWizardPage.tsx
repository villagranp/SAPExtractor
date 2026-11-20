import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm, useWatch, type Path } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DetailList,
  PageHeader,
  Stepper,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  useCreateSource,
  useInspectEntitySet,
  useSource,
  useUpdateSource,
  type DeltaSemantics,
  type Inspection,
  type SchemaColumn,
  type SourceDetail,
} from '../api/queries';
import { ServicePicker } from '../components/ServicePicker';
import { DeltaSummary, SourceTypeLabel } from '../components/SourcePills';
import { DeltaStep, FiltersStep, SchemaKeysStep } from '../components/WizardSteps';
import {
  EMPTY_FORM,
  STEP_FIELDS,
  formFieldOf,
  fromInspection,
  fromSource,
  sourceFormSchema,
  toCreateRequest,
  toUpdateRequest,
  type SourceForm,
  type StepId,
} from '../schemas/sourceForm';
import '../sources.css';

const NEW_STEPS: StepId[] = ['service', 'schema', 'delta', 'filters', 'general', 'summary'];
const EDIT_STEPS: StepId[] = ['schema', 'delta', 'filters', 'general', 'summary'];
const WATERMARK_EDM = new Set(['Edm.DateTime', 'Edm.DateTimeOffset', 'Edm.Date', 'Edm.Decimal']);

/** Lo que el asistente necesita del entity set: del `$metadata` (alta) o del origen (edición). */
interface EntityInfo {
  key: string;
  columns: SchemaColumn[];
  supportsDelta: boolean;
  watermarkCandidates: string[];
  deltaProcesses: Record<string, DeltaSemantics>;
  hint: Inspection['semantics_hint'];
}

function infoOf(inspection: Inspection, key: string): EntityInfo {
  return {
    key,
    columns: inspection.columns,
    supportsDelta: inspection.supports_delta,
    watermarkCandidates: inspection.watermark_candidates,
    deltaProcesses: inspection.delta_processes,
    hint: inspection.semantics_hint,
  };
}

function infoOfSource(source: SourceDetail): EntityInfo {
  return {
    key: source.id,
    columns: source.columns,
    supportsDelta: source.supports_delta,
    watermarkCandidates: source.columns
      .filter((c) => WATERMARK_EDM.has(c.edm_type) && c.name !== 'ODQ_ENTITYCNTR')
      .map((c) => c.name),
    deltaProcesses: {},
    hint: null,
  };
}

const inspectionKey = (f: SourceForm) =>
  `${f.sap_system_id}|${f.service_name};${String(f.service_version)}|${f.entity_set}|${f.source_type}`;

function Wizard({ existing }: { existing?: SourceDetail }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const editing = existing !== undefined;
  const steps = editing ? EDIT_STEPS : NEW_STEPS;
  const [index, setIndex] = useState(0);
  const [info, setInfo] = useState<EntityInfo | null>(existing ? infoOfSource(existing) : null);
  const [serverError, setServerError] = useState<string | null>(null);

  const form = useForm<SourceForm>({
    resolver: zodResolver(sourceFormSchema),
    defaultValues: existing
      ? fromSource(existing)
      : {
          ...EMPTY_FORM,
          sap_system_id: params.get('system') ?? '',
          service_name: params.get('service') ?? '',
          service_version: Number(params.get('version') ?? '1') || 1,
          entity_set: params.get('entitySet') ?? '',
        },
  });
  const inspect = useInspectEntitySet();
  const create = useCreateSource();
  const update = useUpdateSource(existing?.id ?? '');
  const step = steps[index] ?? 'summary';
  const busy = inspect.isPending || create.isPending || update.isPending;

  const goTo = (target: number) => {
    setServerError(null);
    setIndex(target);
  };

  const next = async () => {
    setServerError(null);
    const ok = await form.trigger([...STEP_FIELDS[step]] as Path<SourceForm>[]);
    if (!ok) return;
    if (step === 'service') {
      const values = form.getValues();
      const key = inspectionKey(values);
      if (info?.key !== key) {
        try {
          const inspection = await inspect.mutateAsync({
            sap_system_id: values.sap_system_id,
            service_name: values.service_name,
            service_version: values.service_version,
            entity_set: values.entity_set,
          });
          form.reset({ ...fromInspection(values, inspection), source_type: values.source_type });
          setInfo(infoOf(inspection, key));
        } catch (error) {
          setServerError(describeError(error).message);
          return;
        }
      }
    }
    goTo(index + 1);
  };

  const save = form.handleSubmit(async (values) => {
    if (!info) return;
    setServerError(null);
    try {
      const saved = existing
        ? await update.mutateAsync(toUpdateRequest(values, info.columns, existing.version))
        : await create.mutateAsync(toCreateRequest(values, info.columns));
      void navigate(`/sources/${saved.id}`, { state: { saved: true } });
    } catch (error) {
      const described = describeError(error);
      let firstStep: number | null = null;
      for (const [apiField, messages] of Object.entries(described.fields)) {
        const field = formFieldOf(apiField) as Path<SourceForm>;
        form.setError(field, { type: 'server', message: messages[0] ?? '' });
        const owner = steps.findIndex((s) =>
          (STEP_FIELDS[s] as readonly string[]).some(
            (f) => field === f || field.startsWith(`${f}.`),
          ),
        );
        if (owner >= 0) firstStep = firstStep === null ? owner : Math.min(firstStep, owner);
      }
      setServerError(described.message);
      if (firstStep !== null) setIndex(firstStep);
    }
  });

  const submitLabel = inspect.isPending
    ? t('sources.wizard.reading')
    : create.isPending || update.isPending
      ? t('sources.wizard.saving')
      : step === 'summary'
        ? editing
          ? t('sources.wizard.saveChanges')
          : t('sources.wizard.saveDraft')
        : step === 'service'
          ? t('sources.wizard.readSchema')
          : t('common.next');

  const values = useWatch({ control: form.control }) as SourceForm;
  const errors = form.formState.errors;
  const fieldError = (name: keyof SourceForm) => {
    const message = errors[name]?.message;
    return message ? t(`sources.errors.${message}`, { defaultValue: message }) : undefined;
  };

  return (
    <>
      <PageHeader
        title={
          editing
            ? t('sources.wizard.editTitle', { code: existing.code })
            : t('sources.wizard.title')
        }
        description={t('sources.wizard.description')}
        actions={
          <Link to={editing ? `/sources/${existing.id}` : '/sources'}>{t('common.cancel')}</Link>
        }
      />
      <Stepper
        label={t('sources.wizard.progress')}
        steps={steps.map((id) => ({ id, label: t(`sources.wizard.steps.${id}`) }))}
        current={index}
        onSelect={goTo}
      />
      <Card title={t(`sources.wizard.steps.${step}`)}>
        <form
          className="sx-stack"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (step === 'summary') void save();
            else void next();
          }}
        >
          {serverError && <Alert tone="danger">{serverError}</Alert>}

          {step === 'service' && <ServicePicker form={form} />}
          {step === 'schema' && info && <SchemaKeysStep form={form} columns={info.columns} />}
          {step === 'delta' && info && (
            <DeltaStep
              form={form}
              columns={info.columns}
              supportsDelta={info.supportsDelta}
              watermarkCandidates={info.watermarkCandidates}
              deltaProcesses={info.deltaProcesses}
              hint={info.hint}
            />
          )}
          {step === 'filters' && info && <FiltersStep form={form} columns={info.columns} />}
          {step === 'general' && (
            <div className="sx-stack">
              <TextField
                label={t('sources.fields.code')}
                hint={editing ? t('sources.wizard.codeFixed') : t('sources.wizard.codeHint')}
                disabled={editing}
                error={fieldError('code')}
                autoComplete="off"
                {...form.register('code')}
              />
              <TextField
                label={t('sources.fields.name')}
                error={fieldError('name')}
                {...form.register('name')}
              />
              <TextAreaField
                label={t('sources.fields.description')}
                error={fieldError('description')}
                rows={3}
                {...form.register('description')}
              />
              <TextField
                label={t('sources.fields.tags')}
                hint={t('sources.wizard.tagsHint')}
                error={fieldError('tags')}
                {...form.register('tags')}
              />
              <TextField
                label={t('sources.fields.pageSize')}
                hint={t('sources.wizard.pageSizeHint')}
                inputMode="numeric"
                error={fieldError('page_size')}
                {...form.register('page_size')}
              />
              <Checkbox
                label={t('sources.fields.previewEnabled')}
                description={t('sources.wizard.previewHint')}
                {...form.register('preview_enabled')}
              />
            </div>
          )}
          {step === 'summary' && info && (
            <div className="sx-stack">
              <DetailList
                items={[
                  {
                    label: t('sources.fields.code'),
                    value: <code>{values.code.toUpperCase()}</code>,
                  },
                  { label: t('sources.fields.name'), value: values.name },
                  {
                    label: t('sources.fields.type'),
                    value: <SourceTypeLabel type={values.source_type} />,
                  },
                  {
                    label: t('sources.fields.service'),
                    value: (
                      <code>
                        {values.service_name} / {values.entity_set}
                      </code>
                    ),
                  },
                  { label: t('sources.fields.keyFields'), value: values.key_fields.join(', ') },
                  {
                    label: t('sources.fields.delta'),
                    value: (
                      <DeltaSummary
                        method={values.delta_method}
                        semantics={values.delta_method === 'ODQ' ? values.delta_semantics : null}
                        watermarkField={values.watermark_field || null}
                      />
                    ),
                  },
                  {
                    label: t('sources.fields.filters'),
                    value: values.filters.length
                      ? values.filters
                          .map((f) => `${f.field} ${t(`sources.operators.${f.op}`)} ${f.value}`)
                          .join(` ${t('sources.wizard.and')} `)
                      : t('sources.wizard.noFiltersShort'),
                  },
                  { label: t('sources.fields.pageSize'), value: values.page_size },
                ]}
              />
              {!editing && <Alert tone="info">{t('sources.wizard.savesDraft')}</Alert>}
            </div>
          )}

          <div className="sx-row">
            {index > 0 && (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  goTo(index - 1);
                }}
              >
                {t('common.back')}
              </Button>
            )}
            <Button type="submit" disabled={busy}>
              {submitLabel}
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}

export function SourceWizardPage() {
  const { t } = useTranslation();
  const { sourceId } = useParams();
  const source = useSource(sourceId ?? '');
  if (!sourceId) return <Wizard />;
  if (source.error) return <Alert tone="danger">{describeError(source.error).message}</Alert>;
  if (!source.data) return <p className="sx-muted">{t('common.loading')}</p>;
  return <Wizard existing={source.data} />;
}
