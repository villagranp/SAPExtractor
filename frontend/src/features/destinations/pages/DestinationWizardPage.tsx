import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { describeError, type DescribedError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DetailList,
  PageHeader,
  SegmentedControl,
  SelectField,
  Spinner,
  Stepper,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  DESTINATION_TYPES,
  ENVIRONMENTS,
  useCreateDestination,
  useDestination,
  useDestinationTypeSchema,
  useTestDraftDestination,
  useUpdateDestination,
  type CheckReport,
  type DestinationDetail,
  type DestinationEnvironment,
  type DestinationType,
  type DestinationTypeSchema,
  type DestinationUpdateRequest,
} from '../api/queries';
import { CheckReportView } from '../components/CheckReportView';
import { SchemaForm } from '../components/SchemaForm';
import {
  initialValues,
  missingFields,
  toPayload,
  type FormValues,
  type JsonObjectSchema,
} from '../components/schemaValues';

type StepId = 'type' | 'general' | 'config' | 'credentials' | 'test' | 'summary';

interface General {
  code: string;
  name: string;
  description: string;
  environment: DestinationEnvironment;
  tags: string;
}

const NEW_GENERAL: General = { code: '', name: '', description: '', environment: 'DEV', tags: '' };

const STEP_FIELDS: Record<StepId, string[]> = {
  type: ['type'],
  general: ['code', 'name', 'description', 'environment', 'tags'],
  config: ['config'],
  credentials: ['secret'],
  test: [],
  summary: [],
};

function tagList(tags: string): string[] {
  return tags
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

function stepOfField(field: string, steps: StepId[]): StepId | undefined {
  const root = field.split('.')[0] ?? field;
  return steps.find((s) => STEP_FIELDS[s].includes(root));
}

/** Errores de la API con prefijo (`config.path`) → errores del formulario (`path`). */
function prefixed(error: DescribedError | null, prefix: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [field, messages] of Object.entries(error?.fields ?? {})) {
    if (field.startsWith(`${prefix}.`) && messages[0])
      out[field.slice(prefix.length + 1)] = messages[0];
  }
  return out;
}

interface WizardProps {
  existing?: DestinationDetail;
  initialType: DestinationType | null;
}

function Wizard({ existing, initialType }: WizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const editing = existing !== undefined;
  const [type, setType] = useState<DestinationType | null>(existing?.type ?? initialType);
  const typeSchema = useDestinationTypeSchema(type);
  const info: DestinationTypeSchema | undefined = typeSchema.data;
  const needsSecret = info?.requires_secret ?? (type !== null && type !== 'csv');
  const steps: StepId[] = editing
    ? ['general', 'config', 'test', 'summary']
    : [
        ...(initialType ? [] : (['type'] as StepId[])),
        'general',
        'config',
        ...(needsSecret ? (['credentials'] as StepId[]) : []),
        'test',
        'summary',
      ];
  const [general, setGeneral] = useState<General>(
    existing
      ? {
          code: existing.code,
          name: existing.name,
          description: existing.description ?? '',
          environment: existing.environment,
          tags: existing.tags.join(', '),
        }
      : NEW_GENERAL,
  );
  const [config, setConfig] = useState<FormValues | null>(null);
  const [secret, setSecret] = useState<FormValues | null>(null);
  const [index, setIndex] = useState(0);
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  const [report, setReport] = useState<CheckReport | null>(null);
  const [activate, setActivate] = useState(true);
  const test = useTestDraftDestination();
  const create = useCreateDestination();
  const update = useUpdateDestination(existing?.id ?? '');
  const saving = create.isPending || update.isPending;
  const saveError: DescribedError | null = create.error
    ? describeError(create.error)
    : update.error
      ? describeError(update.error)
      : null;
  const testError = test.error ? describeError(test.error) : null;
  const step = steps[index] ?? 'general';

  const configSchema = (info?.config_schema ?? {}) as JsonObjectSchema;
  const secretSchema = (info?.secret_schema ?? {}) as JsonObjectSchema;
  // Los valores se inicializan cuando llega el esquema del tipo elegido.
  const configValues = config ?? (info ? initialValues(configSchema, existing?.config) : {});
  const secretValues = secret ?? (info ? initialValues(secretSchema) : {});

  const invalidateTest = () => {
    setReport(null); // cualquier cambio invalida la prueba anterior
  };
  const setField = (key: keyof General) => (e: { target: { value: string } }) => {
    setGeneral((g) => ({ ...g, [key]: e.target.value }));
    invalidateTest();
  };

  const remoteErrors = { ...saveError?.fields, ...testError?.fields };
  const fieldError = (key: string): string | undefined => {
    const remote = remoteErrors[key]?.[0];
    const local = localErrors[key];
    return remote ?? (local ? t(`destinations.validation.${local}`) : undefined);
  };
  const schemaErrors = (prefix: 'config' | 'secret') => {
    const local = Object.fromEntries(
      Object.entries(localErrors)
        .filter(([k]) => k.startsWith(`${prefix}.`))
        .map(([k, v]) => [k.slice(prefix.length + 1), t(`destinations.validation.${v}`)]),
    );
    return { ...local, ...prefixed(testError, prefix), ...prefixed(saveError, prefix) };
  };

  const validate = (current: StepId): Record<string, string> => {
    const errors: Record<string, string> = {};
    if (current === 'type' && !type) errors.type = 'required';
    if (current === 'general') {
      if (!editing && !/^[A-Za-z][A-Za-z0-9_]{2,29}$/.test(general.code.trim())) {
        errors.code = 'code';
      }
      if (!general.name.trim()) errors.name = 'required';
    }
    if (current === 'config') {
      for (const key of missingFields(configSchema, configValues))
        errors[`config.${key}`] = 'invalid';
    }
    if (current === 'credentials') {
      for (const key of missingFields(secretSchema, secretValues))
        errors[`secret.${key}`] = 'required';
    }
    return errors;
  };

  const goTo = (target: number) => {
    setLocalErrors({});
    setIndex(target);
  };
  const next = () => {
    const errors = validate(step);
    setLocalErrors(errors);
    if (Object.keys(errors).length === 0) setIndex((i) => Math.min(i + 1, steps.length - 1));
  };

  const configPayload = () => toPayload(configSchema, configValues);
  const secretPayload = () =>
    needsSecret && !editing ? toPayload(secretSchema, secretValues) : null;

  const runTest = () => {
    if (!type) return;
    test.mutate(
      {
        type,
        code: general.code.trim() || null,
        config: configPayload(),
        ...(editing ? { reuse_credentials_from: existing.id } : { secret: secretPayload() }),
      },
      { onSuccess: setReport },
    );
  };

  const onSaveError = (error: unknown) => {
    const described = describeError(error);
    const target = Object.keys(described.fields)
      .map((f) => stepOfField(f, steps))
      .find(Boolean);
    if (target) setIndex(steps.indexOf(target));
  };

  const changes = (d: DestinationDetail): DestinationUpdateRequest => {
    const body: DestinationUpdateRequest = { version: d.version };
    if (general.name.trim() !== d.name) body.name = general.name.trim();
    if (general.description.trim() !== (d.description ?? '')) {
      body.description = general.description.trim();
    }
    if (general.environment !== d.environment) body.environment = general.environment;
    if (tagList(general.tags).join(',') !== d.tags.join(',')) body.tags = tagList(general.tags);
    const cfg = configPayload();
    if (Object.keys(cfg).some((k) => cfg[k] !== d.config[k])) body.config = cfg;
    return body;
  };

  const save = () => {
    if (!type) return;
    if (editing) {
      update.mutate(changes(existing), {
        onSuccess: (d) => void navigate(`/destinations/${d.id}`),
        onError: onSaveError,
      });
      return;
    }
    create.mutate(
      {
        code: general.code.trim().toUpperCase(),
        name: general.name.trim(),
        description: general.description.trim() || null,
        type,
        environment: general.environment,
        tags: tagList(general.tags),
        config: configPayload(),
        secret: secretPayload(),
        run_test: true,
        activate: activate && report?.ok === true,
      },
      {
        onSuccess: (r) =>
          void navigate(`/destinations/${r.destination.id}`, { state: { created: true } }),
        onError: onSaveError,
      },
    );
  };

  const schemaLoading = type !== null && typeSchema.isPending;

  const stepContent: Record<StepId, ReactNode> = {
    type: (
      <div className="sx-stack">
        <p className="sx-muted">{t('destinations.wizard.typeHelp')}</p>
        <SegmentedControl
          label={t('destinations.fields.type')}
          value={type ?? 'snowflake'}
          onChange={(value) => {
            setType(value);
            setConfig(null);
            setSecret(null);
            invalidateTest();
          }}
          options={DESTINATION_TYPES.map((value) => ({
            value,
            label: t(`destinations.types.${value}.label`),
          }))}
        />
        {type && <p>{t(`destinations.types.${type}.description`)}</p>}
        {fieldError('type') && <Alert tone="danger">{fieldError('type')}</Alert>}
      </div>
    ),
    general: (
      <div className="sx-stack">
        <div className="sx-two-columns">
          <TextField
            label={t('destinations.fields.code')}
            value={general.code}
            onChange={setField('code')}
            disabled={editing}
            hint={t('destinations.hints.code')}
            error={fieldError('code')}
            maxLength={30}
          />
          <TextField
            label={t('destinations.fields.name')}
            value={general.name}
            onChange={setField('name')}
            error={fieldError('name')}
            maxLength={200}
          />
        </div>
        <SelectField
          label={t('destinations.fields.environment')}
          value={general.environment}
          onChange={setField('environment')}
          options={ENVIRONMENTS.map((env) => ({
            value: env,
            label: t(`destinations.environments.${env}`),
          }))}
          error={fieldError('environment')}
        />
        <TextField
          label={t('destinations.fields.tags')}
          value={general.tags}
          onChange={setField('tags')}
          hint={t('destinations.hints.tags')}
          error={fieldError('tags')}
        />
        <TextAreaField
          label={t('destinations.fields.description')}
          value={general.description}
          onChange={setField('description')}
          rows={3}
        />
      </div>
    ),
    config: schemaLoading ? (
      <Spinner label={t('common.loading')} />
    ) : (
      <div className="sx-stack">
        {type === 'csv' && (configSchema.properties?.root?.enum ?? []).length === 0 && (
          <Alert tone="warning">{t('destinations.wizard.noCsvRoots')}</Alert>
        )}
        <SchemaForm
          schema={configSchema}
          values={configValues}
          errors={schemaErrors('config')}
          onChange={(values) => {
            setConfig(values);
            invalidateTest();
          }}
        />
      </div>
    ),
    credentials: (
      <div className="sx-stack">
        {type && type !== 'csv' && (
          <Alert tone="info" title={t(`destinations.wizard.credentials.${type}.title`)}>
            {t(`destinations.wizard.credentials.${type}.help`)}
          </Alert>
        )}
        <SchemaForm
          schema={secretSchema}
          values={secretValues}
          errors={schemaErrors('secret')}
          onChange={(values) => {
            setSecret(values);
            invalidateTest();
          }}
        />
      </div>
    ),
    test: (
      <div className="sx-stack">
        <p className="sx-muted">{t(`destinations.wizard.testHelp.${type ?? 'csv'}`)}</p>
        {editing && needsSecret && (
          <Alert tone="info">{t('destinations.wizard.testReusesCredentials')}</Alert>
        )}
        <div>
          <Button onClick={runTest} disabled={test.isPending}>
            {test.isPending
              ? t('destinations.wizard.testing')
              : t('destinations.wizard.testButton')}
          </Button>
        </div>
        {test.isPending && <Spinner label={t('destinations.wizard.testing')} />}
        {testError && <Alert tone="danger">{testError.message}</Alert>}
        {report && <CheckReportView report={report} />}
      </div>
    ),
    summary: (
      <div className="sx-stack">
        <DetailList
          items={[
            {
              label: t('destinations.fields.type'),
              value: type ? t(`destinations.types.${type}.label`) : '—',
            },
            { label: t('destinations.fields.code'), value: general.code.toUpperCase() },
            { label: t('destinations.fields.name'), value: general.name },
            {
              label: t('destinations.fields.environment'),
              value: t(`destinations.environments.${general.environment}`),
            },
            ...Object.entries(configPayload()).map(([key, value]) => ({
              label: configSchema.properties?.[key]?.title ?? key,
              value:
                typeof value === 'boolean' ? t(value ? 'common.yes' : 'common.no') : String(value),
            })),
            {
              label: t('destinations.fields.lastTest'),
              value: report
                ? report.ok
                  ? t('destinations.test.ok')
                  : t('destinations.test.failed')
                : t('destinations.test.never'),
            },
          ]}
        />
        {editing && needsSecret && (
          <p className="sx-muted">{t('destinations.wizard.credentialsElsewhere')}</p>
        )}
        {!editing && (
          <Checkbox
            label={t('destinations.wizard.activate')}
            description={
              report?.ok
                ? t('destinations.wizard.activateHelp')
                : t('destinations.wizard.activateBlocked')
            }
            checked={activate && report?.ok === true}
            disabled={report?.ok !== true}
            onChange={(e) => {
              setActivate(e.target.checked);
            }}
          />
        )}
        {saveError && !Object.keys(saveError.fields).length && (
          <Alert tone="danger">{saveError.message}</Alert>
        )}
      </div>
    ),
  };

  return (
    <>
      <PageHeader
        title={
          editing
            ? t('destinations.wizard.titleEdit', { code: existing.code })
            : t('destinations.wizard.titleNew')
        }
        description={t('destinations.wizard.description')}
        actions={
          <Link to={editing ? `/destinations/${existing.id}` : '/destinations'}>
            {t('common.cancel')}
          </Link>
        }
      />
      <Stepper
        label={t('destinations.wizard.progress')}
        current={index}
        onSelect={goTo}
        steps={steps.map((id) => ({ id, label: t(`destinations.wizard.steps.${id}`) }))}
      />
      <Card title={t(`destinations.wizard.steps.${step}`)}>
        {saveError && Object.keys(saveError.fields).length > 0 && (
          <Alert tone="danger">{t('destinations.wizard.fixFields')}</Alert>
        )}
        {stepContent[step]}
        <div className="sx-row" style={{ marginTop: 'var(--sx-space-5)' }}>
          {index > 0 && (
            <Button
              variant="secondary"
              onClick={() => {
                goTo(index - 1);
              }}
            >
              {t('destinations.wizard.back')}
            </Button>
          )}
          {step !== 'summary' ? (
            <Button onClick={next} disabled={step === 'config' && schemaLoading}>
              {t('destinations.wizard.next')}
            </Button>
          ) : (
            <Button onClick={save} disabled={saving}>
              {saving ? t('destinations.wizard.saving') : t('destinations.wizard.save')}
            </Button>
          )}
        </div>
      </Card>
    </>
  );
}

function asType(value: string | null): DestinationType | null {
  return DESTINATION_TYPES.find((t) => t === value) ?? null;
}

export function DestinationWizardPage() {
  const { t } = useTranslation();
  const { destinationId = '' } = useParams();
  const [params] = useSearchParams();
  const destination = useDestination(destinationId);
  if (!destinationId) return <Wizard initialType={asType(params.get('type'))} />;
  if (destination.isPending) return <Spinner label={t('common.loading')} />;
  if (destination.error) {
    return <Alert tone="danger">{describeError(destination.error).message}</Alert>;
  }
  return <Wizard existing={destination.data} initialType={destination.data.type} />;
}
