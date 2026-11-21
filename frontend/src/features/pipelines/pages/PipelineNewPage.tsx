import { useEffect, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import {
  useDestinations,
  useDestinationTypes,
  type LoadMode,
} from '@/features/destinations/api/queries';
import { useSources } from '@/features/sources/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  PageHeader,
  SelectField,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import { useCreatePipeline, type ExtractionMode } from '../api/queries';
import { loadModesFor, modesFor, preferredLoadMode } from '../rules';

interface PipelineForm {
  code: string;
  name: string;
  description: string;
  source_id: string;
  destination_id: string;
  extraction_mode: ExtractionMode | '';
  load_mode: LoadMode | '';
  target_name: string;
}

const EMPTY: PipelineForm = {
  code: '',
  name: '',
  description: '',
  source_id: '',
  destination_id: '',
  extraction_mode: '',
  load_mode: '',
  target_name: '',
};

const FIELDS = Object.keys(EMPTY) as (keyof PipelineForm)[];

/** Alta de pipeline en una sola pantalla. El asistente completo con programación llega en la It6. */
export function PipelineNewPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreatePipeline();
  const [serverError, setServerError] = useState<string | null>(null);
  const sources = useSources({ page: 1, page_size: 200 });
  const destinations = useDestinations({ page: 1, page_size: 200 });
  const types = useDestinationTypes();
  const form = useForm<PipelineForm>({ defaultValues: EMPTY });
  const values = useWatch({ control: form.control });
  const errors = form.formState.errors;

  const liveSources = (sources.data?.items ?? []).filter((s) => s.status !== 'deprecated');
  const liveDestinations = (destinations.data?.items ?? []).filter((d) => d.status !== 'inactive');
  const source = liveSources.find((s) => s.id === values.source_id);
  const destination = liveDestinations.find((d) => d.id === values.destination_id);
  const supported = types.data?.find((x) => x.type === destination?.type)?.supported_modes ?? [];
  const modes = modesFor(source);
  const loadModes = loadModesFor(values.extraction_mode ?? '', source, supported);
  const modesKey = modes.join();
  const loadModesKey = loadModes.join();

  // Al cambiar origen o destino se proponen el modo y la carga más habituales.
  const { setValue, getValues } = form;
  useEffect(() => {
    const allowed = modesKey ? (modesKey.split(',') as ExtractionMode[]) : [];
    const current = getValues('extraction_mode');
    if (!current || !allowed.includes(current)) setValue('extraction_mode', allowed[0] ?? '');
  }, [modesKey, getValues, setValue]);
  useEffect(() => {
    const allowed = loadModesKey ? (loadModesKey.split(',') as LoadMode[]) : [];
    const current = getValues('load_mode');
    if (!current || !allowed.includes(current)) setValue('load_mode', preferredLoadMode(allowed));
  }, [loadModesKey, getValues, setValue]);

  const required = { required: t('pipelines.form.required') };
  const fieldError = (name: keyof PipelineForm) => errors[name]?.message;

  const save = form.handleSubmit(async (v) => {
    setServerError(null);
    try {
      const saved = await create.mutateAsync({
        code: v.code.trim().toUpperCase(),
        name: v.name.trim(),
        description: v.description.trim() || null,
        source_id: v.source_id,
        destination_id: v.destination_id,
        extraction_mode: v.extraction_mode as ExtractionMode,
        load_options: {
          load_mode: v.load_mode as LoadMode,
          target_name: v.target_name.trim(),
          // Valores por defecto del plan 07 §2; el asistente de la It6 permitirá cambiarlos.
          create_if_missing: true,
          schema_evolution: 'add_columns',
          delete_handling: 'soft',
          technical_columns: true,
        },
      });
      void navigate(`/pipelines/${saved.id}`);
    } catch (error) {
      const described = describeError(error);
      let assigned = 0;
      for (const [apiField, messages] of Object.entries(described.fields)) {
        const field = apiField.replace(/^load_options\./, '') as keyof PipelineForm;
        if (FIELDS.includes(field)) {
          form.setError(field, { type: 'server', message: messages[0] ?? '' });
          assigned += 1;
        }
      }
      if (assigned === 0) setServerError(described.message);
    }
  });

  return (
    <>
      <PageHeader
        title={t('pipelines.form.title')}
        description={t('pipelines.form.description')}
        actions={<Link to="/pipelines">{t('common.cancel')}</Link>}
      />
      {serverError && <Alert tone="danger">{serverError}</Alert>}
      <form onSubmit={(e) => void save(e)} noValidate className="sx-stack">
        <Card title={t('pipelines.form.generalTitle')}>
          <div className="sx-two-columns">
            <TextField
              label={t('pipelines.fields.code')}
              hint={t('pipelines.form.codeHint')}
              error={fieldError('code')}
              {...form.register('code', required)}
            />
            <TextField
              label={t('pipelines.fields.name')}
              error={fieldError('name')}
              {...form.register('name', required)}
            />
          </div>
          <TextAreaField
            label={t('pipelines.fields.description')}
            rows={2}
            {...form.register('description')}
          />
        </Card>
        <Card title={t('pipelines.form.flowTitle')}>
          <div className="sx-two-columns">
            <SelectField
              label={t('pipelines.fields.source')}
              placeholder={t('common.choose')}
              error={fieldError('source_id')}
              options={liveSources.map((s) => ({
                value: s.id,
                label: t('pipelines.form.option', { code: s.code, name: s.name }),
              }))}
              {...form.register('source_id', required)}
            />
            <SelectField
              label={t('pipelines.fields.destination')}
              placeholder={t('common.choose')}
              error={fieldError('destination_id')}
              options={liveDestinations.map((d) => ({
                value: d.id,
                label: t('pipelines.form.option', { code: d.code, name: d.name }),
              }))}
              {...form.register('destination_id', required)}
            />
            <SelectField
              label={t('pipelines.fields.mode')}
              placeholder={t('common.choose')}
              hint={source ? t(`pipelines.form.modeHint.${source.delta_method}`) : undefined}
              error={fieldError('extraction_mode')}
              disabled={modes.length === 0}
              options={modes.map((m) => ({ value: m, label: t(`pipelines.modes.${m}`) }))}
              {...form.register('extraction_mode', required)}
            />
            <SelectField
              label={t('pipelines.fields.loadMode')}
              placeholder={t('common.choose')}
              hint={
                values.extraction_mode === 'DELTA' && source?.delta_semantics !== 'after_image'
                  ? t('pipelines.form.appendOnlyHint')
                  : undefined
              }
              error={fieldError('load_mode')}
              disabled={loadModes.length === 0}
              options={loadModes.map((m) => ({
                value: m,
                label: t(`destinations.loadModes.${m}`),
              }))}
              {...form.register('load_mode', required)}
            />
            <TextField
              label={t('pipelines.fields.target')}
              hint={t('pipelines.form.targetHint')}
              error={fieldError('target_name')}
              {...form.register('target_name', required)}
            />
          </div>
          {values.extraction_mode === 'DELTA' && (
            <Alert tone="info">{t('pipelines.form.deltaNotice')}</Alert>
          )}
        </Card>
        <div>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? t('pipelines.form.saving') : t('pipelines.form.save')}
          </Button>
        </div>
      </form>
    </>
  );
}
