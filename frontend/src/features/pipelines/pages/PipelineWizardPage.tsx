import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useMemo, useState } from 'react';
import { useForm, useWatch, type FieldErrors, type Path } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import {
  useDestination,
  useDestinationTypes,
  type DestinationSummary,
} from '@/features/destinations/api/queries';
import { useSource, type SourceSummary } from '@/features/sources/api/queries';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Card, DetailList, PageHeader, Spinner, Stepper } from '@/shared/ui';
import {
  useConcurrencyGroups,
  useCreatePipeline,
  usePipeline,
  usePipelineReview,
  usePutSchedules,
  useSchedules,
  useUpdatePipeline,
  type PipelineDetail,
  type ScheduleResponse,
} from '../api/queries';
import { FindingsList } from '../components/PipelinePills';
import { RouteStrip, type RouteStop } from '../components/RouteStrip';
import { SchedulesEditor } from '../components/ScheduleBuilder';
import { mappingText, useScheduleText } from '../components/scheduleText';
import { DestinationStep, GeneralStep, LoadStep, SourceStep } from '../components/WizardSteps';
import {
  EMPTY_FORM,
  STEP_FIELDS,
  columnRows,
  extractionModesFor,
  formFieldOf,
  fromPipeline,
  pipelineFormSchema,
  suggestCode,
  suggestLoadMode,
  supportedModes,
  toCreateRequest,
  toUpdateRequest,
  type PipelineForm,
  type StepId,
} from '../schemas/pipelineForm';
import {
  fromSchedules,
  schedulesValid,
  toScheduleBody,
  type ScheduleDraft,
} from '../schemas/schedule';
import '../pipelines.css';

const DRAFT_STEPS: StepId[] = ['source', 'destination', 'load', 'schedule', 'general', 'summary'];
/** Activo o en pausa: origen y destino ya no cambian, así que esos pasos no aparecen. */
const LOCKED_STEPS: StepId[] = ['load', 'schedule', 'general', 'summary'];
/** Al editar, el propio pipeline no cuenta como «otro pipeline delta sobre el mismo origen». */
const SELF_FINDING = 'SX-PIPE-DELTA-SHARED';

const bodiesOf = (drafts: ScheduleDraft[]) => JSON.stringify(drafts.map(toScheduleBody));

function stepOfField(steps: StepId[], field: string): number {
  return steps.findIndex((step) =>
    (STEP_FIELDS[step] as readonly string[]).some((f) => field === f || field.startsWith(`${f}.`)),
  );
}

interface WizardProps {
  existing?: PipelineDetail;
  existingSchedules?: ScheduleResponse[];
}

function Wizard({ existing, existingSchedules }: WizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { describe, language } = useScheduleText();
  const editing = existing !== undefined;
  const locked = editing && existing.status !== 'draft';
  const steps = locked ? LOCKED_STEPS : DRAFT_STEPS;
  const [index, setIndex] = useState(0);
  const [serverError, setServerError] = useState<string | null>(null);
  const [schedules, setSchedules] = useState<ScheduleDraft[]>(() =>
    fromSchedules(existingSchedules ?? []),
  );
  const [savedSchedules] = useState(() => bodiesOf(schedules));

  const form = useForm<PipelineForm>({
    resolver: zodResolver(pipelineFormSchema),
    defaultValues: existing
      ? fromPipeline(existing)
      : {
          ...EMPTY_FORM,
          source_id: params.get('source') ?? '',
          destination_id: params.get('destination') ?? '',
        },
  });
  const values = useWatch({ control: form.control }) as PipelineForm;
  const source = useSource(values.source_id);
  const destination = useDestination(values.destination_id);
  const types = useDestinationTypes();
  const groups = useConcurrencyGroups();
  const create = useCreatePipeline();
  const update = useUpdatePipeline(existing?.id ?? '');
  const putSchedules = usePutSchedules();
  const supported = useMemo(
    () => supportedModes(types.data, destination.data?.type),
    [types.data, destination.data?.type],
  );
  const step = steps[index] ?? 'summary';
  const busy = create.isPending || update.isPending || putSchedules.isPending;
  const sourceData = source.data;

  // El mapeo de columnas sigue al esquema del origen elegido.
  useEffect(() => {
    if (!sourceData) return;
    const rows = form.getValues('columns');
    const aligned =
      rows.length === sourceData.columns.length &&
      rows.every((row, i) => row.name === sourceData.columns[i]?.name);
    if (aligned) return;
    const mapping = existing?.source.id === sourceData.id ? existing.load.column_mapping : {};
    form.setValue('columns', columnRows(sourceData.columns, mapping));
  }, [sourceData, existing, form]);

  // Con origen o destino preelegidos por la URL, los modos parten de lo que ambos admiten.
  useEffect(() => {
    if (locked || !sourceData) return;
    const allowed = extractionModesFor(sourceData);
    const extraction = form.getValues('extraction_mode');
    const next = allowed.includes(extraction) ? extraction : (allowed[0] ?? 'FULL');
    if (next !== extraction) form.setValue('extraction_mode', next);
    if (supported.length > 0 && !supported.includes(form.getValues('load_mode'))) {
      form.setValue('load_mode', suggestLoadMode(next, sourceData, supported));
    }
  }, [locked, sourceData, supported, form]);

  const pickSource = (picked: SourceSummary) => {
    const extraction = extractionModesFor(picked)[0] ?? 'FULL';
    form.setValue('source_id', picked.id, { shouldValidate: true, shouldDirty: true });
    form.setValue('extraction_mode', extraction);
    if (supported.length > 0) {
      form.setValue('load_mode', suggestLoadMode(extraction, picked, supported));
    }
    form.setValue('own_filters', false);
    form.setValue('filters', []);
  };
  const pickDestination = (picked: DestinationSummary) => {
    form.setValue('destination_id', picked.id, { shouldValidate: true, shouldDirty: true });
    const modes = supportedModes(types.data, picked.type);
    if (modes.length > 0) {
      form.setValue(
        'load_mode',
        suggestLoadMode(form.getValues('extraction_mode'), sourceData, modes),
      );
    }
  };

  const review = usePipelineReview(
    values.source_id && values.destination_id
      ? {
          source_id: values.source_id,
          destination_id: values.destination_id,
          extraction_mode: values.extraction_mode,
          load_mode: values.load_mode,
        }
      : null,
  );
  const ownDelta = existing?.findings.some((f) => f.code === SELF_FINDING) ?? false;
  const findings = (review.data ?? []).filter(
    (f) =>
      f.code !== SELF_FINDING || !editing || existing.source.id !== values.source_id || ownDelta,
  );

  const goTo = (target: number) => {
    setServerError(null);
    setIndex(target);
  };

  const next = async () => {
    setServerError(null);
    const ok = await form.trigger([...STEP_FIELDS[step]] as Path<PipelineForm>[]);
    if (!ok) return;
    if (step === 'schedule' && !schedulesValid(schedules)) {
      setServerError(t('pipelines.wizard.scheduleInvalid'));
      return;
    }
    if (steps[index + 1] === 'general' && !editing && sourceData && destination.data) {
      if (!form.getValues('code')) {
        form.setValue('code', suggestCode(sourceData.code, destination.data.code));
      }
      if (!form.getValues('name')) {
        form.setValue(
          'name',
          t('pipelines.wizard.suggestedName', {
            source: sourceData.name,
            destination: destination.data.name,
          }),
        );
      }
    }
    goTo(index + 1);
  };

  const showInvalid = (errors: FieldErrors<PipelineForm>) => {
    const owners = Object.keys(errors)
      .map((field) => stepOfField(steps, field))
      .filter((owner) => owner >= 0);
    if (owners.length > 0) setIndex(Math.min(...owners));
  };

  const save = form.handleSubmit(async (form_) => {
    if (!sourceData) return;
    setServerError(null);
    let saved: PipelineDetail;
    try {
      saved = existing
        ? await update.mutateAsync(toUpdateRequest(form_, sourceData.columns, existing))
        : await create.mutateAsync(toCreateRequest(form_, sourceData.columns));
    } catch (error) {
      const described = describeError(error);
      let firstStep: number | null = null;
      for (const [apiField, messages] of Object.entries(described.fields)) {
        const field = formFieldOf(apiField);
        form.setError(field as Path<PipelineForm>, { type: 'server', message: messages[0] ?? '' });
        const owner = stepOfField(steps, field);
        if (owner >= 0) firstStep = firstStep === null ? owner : Math.min(firstStep, owner);
      }
      setServerError(described.message);
      if (firstStep !== null) setIndex(firstStep);
      return;
    }
    if (bodiesOf(schedules) !== savedSchedules) {
      try {
        await putSchedules.mutateAsync({ id: saved.id, schedules: schedules.map(toScheduleBody) });
      } catch (error) {
        // El pipeline ya está guardado: la ficha explica qué pasó con la programación.
        void navigate(`/pipelines/${saved.id}`, {
          state: { scheduleError: describeError(error).message },
        });
        return;
      }
    }
    void navigate(`/pipelines/${saved.id}`, { state: { saved: true } });
  }, showInvalid);

  const sourceStop: RouteStop | null = sourceData
    ? {
        code: sourceData.code,
        name: sourceData.name,
        detail: sourceData.sap_system.code,
        environment: sourceData.sap_system.environment,
      }
    : null;
  const destinationStop: RouteStop | null = destination.data
    ? {
        code: destination.data.code,
        name: destination.data.name,
        detail: t(`destinations.types.${destination.data.type}.label`),
        environment: destination.data.environment,
      }
    : null;

  const excluded = values.columns.filter((c) => !c.include).length;
  const renamed = values.columns.filter((c) => c.include && c.target.trim()).length;
  const submitLabel = busy
    ? t('pipelines.wizard.saving')
    : step !== 'summary'
      ? t('common.next')
      : editing
        ? t('pipelines.wizard.saveChanges')
        : t('pipelines.wizard.saveDraft');

  return (
    <>
      <PageHeader
        title={
          editing
            ? t('pipelines.wizard.editTitle', { code: existing.code })
            : t('pipelines.wizard.title')
        }
        description={t('pipelines.wizard.description')}
        actions={
          <Link to={editing ? `/pipelines/${existing.id}` : '/pipelines'}>
            {t('common.cancel')}
          </Link>
        }
      />
      <Stepper
        label={t('pipelines.wizard.progress')}
        steps={steps.map((id) => ({ id, label: t(`pipelines.wizard.steps.${id}`) }))}
        current={index}
        onSelect={goTo}
      />
      <RouteStrip
        source={sourceStop}
        destination={destinationStop}
        extractionMode={values.extraction_mode}
        loadMode={values.load_mode}
      />
      <FindingsList findings={findings} />
      <Card title={t(`pipelines.wizard.stepTitles.${step}`)}>
        <form
          className="sx-stack sx-pipelines__fit"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (step === 'summary') void save();
            else void next();
          }}
        >
          {serverError && <Alert tone="danger">{serverError}</Alert>}

          {step === 'source' && <SourceStep form={form} onPick={pickSource} />}
          {step === 'destination' && <DestinationStep form={form} onPick={pickDestination} />}
          {step === 'load' && (
            <LoadStep form={form} source={sourceData} supported={supported} modeLocked={locked} />
          )}
          {step === 'schedule' && <SchedulesEditor drafts={schedules} onChange={setSchedules} />}
          {step === 'general' && (
            <GeneralStep form={form} editing={editing} groups={groups.data ?? []} />
          )}
          {step === 'summary' && (
            <div className="sx-stack">
              <DetailList
                items={[
                  {
                    label: t('pipelines.fields.code'),
                    value: <code>{values.code.toUpperCase()}</code>,
                  },
                  { label: t('pipelines.fields.name'), value: values.name },
                  {
                    label: t('pipelines.fields.extractionMode'),
                    value: t(`pipelines.extractionModes.${values.extraction_mode}`),
                  },
                  {
                    label: t('pipelines.fields.loadMode'),
                    value: t(`pipelines.loadModes.${values.load_mode}`),
                  },
                  {
                    label: t('pipelines.fields.targetName'),
                    value: (
                      <code>
                        {values.target_name.trim() ||
                          (sourceData ? `${sourceData.sap_system.code}_${sourceData.code}` : '')}
                      </code>
                    ),
                  },
                  {
                    label: t('pipelines.fields.columns'),
                    value: mappingText(excluded, renamed, t, language),
                  },
                  {
                    label: t('pipelines.fields.filters'),
                    value: values.own_filters
                      ? t('pipelines.wizard.ownFiltersCount', { count: values.filters.length })
                      : t('pipelines.wizard.sourceFiltersShort'),
                  },
                  {
                    label: t('pipelines.fields.schedule'),
                    value:
                      schedules.length === 0 ? (
                        t('pipelines.schedule.manualOnly')
                      ) : (
                        <ul className="sx-pipelines__plain-list">
                          {schedules.map((draft) => (
                            <li key={draft.key}>
                              {describe(draft)}
                              <br />
                              <span className="sx-muted">
                                {draft.timezone
                                  ? t('pipelines.schedule.previewZone', { zone: draft.timezone })
                                  : t('pipelines.schedule.platformZoneShort')}
                                {draft.blackouts.length > 0 &&
                                  `, ${t('pipelines.schedule.blackoutCount', {
                                    count: draft.blackouts.length,
                                  })}`}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ),
                  },
                  {
                    label: t('pipelines.fields.priority'),
                    value: t(`pipelines.priorities.${values.priority}`),
                  },
                  {
                    label: t('pipelines.fields.retries'),
                    value: t('pipelines.wizard.retriesSummary', {
                      count: Number(values.max_attempts) || 0,
                      timeout: values.timeout_minutes,
                    }),
                  },
                ]}
              />
              {!editing && <Alert tone="info">{t('pipelines.wizard.savesDraft')}</Alert>}
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

export function PipelineWizardPage() {
  const { t } = useTranslation();
  const { pipelineId } = useParams();
  const pipeline = usePipeline(pipelineId ?? '');
  const schedules = useSchedules(pipelineId ?? '');
  if (!pipelineId) return <Wizard />;
  const error = pipeline.error ?? schedules.error;
  if (error) return <Alert tone="danger">{describeError(error).message}</Alert>;
  if (!pipeline.data || !schedules.data) return <Spinner label={t('common.loading')} />;
  return (
    <Wizard key={pipeline.data.id} existing={pipeline.data} existingSchedules={schedules.data} />
  );
}
