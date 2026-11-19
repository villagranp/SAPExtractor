import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { describeError, type DescribedError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DetailList,
  PageHeader,
  SelectField,
  Spinner,
  Stepper,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  ENVIRONMENTS,
  SYSTEM_TYPES,
  useCreateSapSystem,
  useSapSystem,
  useTestDraftConnection,
  useUpdateSapSystem,
  type SapEnvironment,
  type SapSystemDetail,
  type SapSystemTestRequest,
  type SapSystemUpdateRequest,
  type SystemType,
  type TestReport,
} from '../api/queries';
import { CredentialsFields } from '../components/CredentialsFields';
import {
  emptyCredentials,
  missingCredentialFields,
  toCredentialsIn,
  type CredentialsForm,
} from '../components/credentials';
import { TestReportView } from '../components/TestReportView';

type StepId = 'general' | 'connection' | 'credentials' | 'test' | 'summary';

interface WizardForm {
  code: string;
  name: string;
  description: string;
  environment: SapEnvironment;
  system_type: SystemType;
  tags: string;
  base_url: string;
  sap_client: string;
  language: string;
  tls_verify: boolean;
  ca_bundle_pem: string;
  remove_ca: boolean;
  proxy_url: string;
  timeout_seconds: string;
  max_concurrent_requests: string;
  credentials: CredentialsForm;
}

const NEW_FORM: WizardForm = {
  code: '',
  name: '',
  description: '',
  environment: 'DEV',
  system_type: 'S4HANA_ONPREM',
  tags: '',
  base_url: '',
  sap_client: '',
  language: 'ES',
  tls_verify: true,
  ca_bundle_pem: '',
  remove_ca: false,
  proxy_url: '',
  timeout_seconds: '60',
  max_concurrent_requests: '4',
  credentials: emptyCredentials,
};

const STEP_FIELDS: Record<StepId, string[]> = {
  general: ['code', 'name', 'description', 'environment', 'system_type', 'tags'],
  connection: [
    'base_url',
    'sap_client',
    'language',
    'tls_verify',
    'ca_bundle_pem',
    'proxy_url',
    'timeout_seconds',
    'max_concurrent_requests',
  ],
  credentials: ['credentials'],
  test: [],
  summary: [],
};

function fromSystem(s: SapSystemDetail): WizardForm {
  return {
    ...NEW_FORM,
    code: s.code,
    name: s.name,
    description: s.description ?? '',
    environment: s.environment,
    system_type: s.system_type,
    tags: s.tags.join(', '),
    base_url: s.base_url,
    sap_client: s.sap_client,
    language: s.language,
    tls_verify: s.tls_verify,
    proxy_url: s.proxy_url ?? '',
    timeout_seconds: String(s.timeout_seconds),
    max_concurrent_requests: String(s.max_concurrent_requests),
  };
}

function tagList(tags: string): string[] {
  return tags
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

/** Validación de formato en el navegador; las reglas de negocio las aplica la API. */
function validate(step: StepId, form: WizardForm, editing: boolean): Record<string, string> {
  const errors: Record<string, string> = {};
  if (step === 'general') {
    if (!editing && !/^[A-Za-z][A-Za-z0-9_]{2,29}$/.test(form.code.trim())) errors.code = 'code';
    if (!form.name.trim()) errors.name = 'required';
  }
  if (step === 'connection') {
    if (!/^https?:\/\/[^\s/]+/i.test(form.base_url.trim())) errors.base_url = 'url';
    if (!/^\d{3}$/.test(form.sap_client.trim())) errors.sap_client = 'client';
    if (!/^[A-Za-z]{1,2}$/.test(form.language.trim())) errors.language = 'language';
    const timeout = Number(form.timeout_seconds);
    if (!Number.isInteger(timeout) || timeout < 5 || timeout > 600)
      errors.timeout_seconds = 'timeout';
    const parallel = Number(form.max_concurrent_requests);
    if (!Number.isInteger(parallel) || parallel < 1 || parallel > 32) {
      errors.max_concurrent_requests = 'concurrency';
    }
  }
  if (step === 'credentials' && !editing) {
    for (const key of missingCredentialFields(form.credentials)) errors[key] = 'required';
  }
  return errors;
}

function connectionPayload(form: WizardForm) {
  return {
    base_url: form.base_url.trim(),
    sap_client: form.sap_client.trim(),
    language: form.language.trim().toUpperCase(),
    tls_verify: form.tls_verify,
    ca_bundle_pem: form.ca_bundle_pem.trim() || null,
    proxy_url: form.proxy_url.trim() || null,
    timeout_seconds: Number(form.timeout_seconds),
    max_concurrent_requests: Number(form.max_concurrent_requests),
  };
}

/** Cambios de la edición respecto del sistema guardado (PATCH parcial). */
function changes(form: WizardForm, s: SapSystemDetail): SapSystemUpdateRequest {
  const body: SapSystemUpdateRequest = { version: s.version };
  const conn = connectionPayload(form);
  if (form.name.trim() !== s.name) body.name = form.name.trim();
  if (form.description.trim() !== (s.description ?? '')) body.description = form.description.trim();
  if (form.environment !== s.environment) body.environment = form.environment;
  if (form.system_type !== s.system_type) body.system_type = form.system_type;
  if (tagList(form.tags).join(',') !== s.tags.join(',')) body.tags = tagList(form.tags);
  if (conn.base_url !== s.base_url) body.base_url = conn.base_url;
  if (conn.sap_client !== s.sap_client) body.sap_client = conn.sap_client;
  if (conn.language !== s.language) body.language = conn.language;
  if (conn.tls_verify !== s.tls_verify) body.tls_verify = conn.tls_verify;
  if ((conn.proxy_url ?? '') !== (s.proxy_url ?? '')) body.proxy_url = conn.proxy_url ?? '';
  if (conn.timeout_seconds !== s.timeout_seconds) body.timeout_seconds = conn.timeout_seconds;
  if (conn.max_concurrent_requests !== s.max_concurrent_requests) {
    body.max_concurrent_requests = conn.max_concurrent_requests;
  }
  if (form.remove_ca) body.ca_bundle_pem = '';
  else if (conn.ca_bundle_pem) body.ca_bundle_pem = conn.ca_bundle_pem;
  return body;
}

function stepOfField(field: string, steps: StepId[]): StepId | undefined {
  const root = field.split('.')[0] ?? field;
  return steps.find((s) => STEP_FIELDS[s].includes(root));
}

interface WizardProps {
  existing?: SapSystemDetail;
}

function Wizard({ existing }: WizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const editing = existing !== undefined;
  const steps: StepId[] = editing
    ? ['general', 'connection', 'test', 'summary']
    : ['general', 'connection', 'credentials', 'test', 'summary'];
  const [form, setForm] = useState<WizardForm>(existing ? fromSystem(existing) : NEW_FORM);
  const [index, setIndex] = useState(0);
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  const [report, setReport] = useState<TestReport | null>(null);
  const [activate, setActivate] = useState(true);
  const test = useTestDraftConnection();
  const create = useCreateSapSystem();
  const update = useUpdateSapSystem(existing?.id ?? '');
  const saving = create.isPending || update.isPending;
  const saveError: DescribedError | null = create.error
    ? describeError(create.error)
    : update.error
      ? describeError(update.error)
      : null;
  const testError = test.error ? describeError(test.error) : null;
  const step = steps[index] ?? 'general';

  const set =
    <K extends keyof WizardForm>(key: K) =>
    (value: WizardForm[K]) => {
      setForm((f) => ({ ...f, [key]: value }));
      setReport(null); // cualquier cambio invalida la prueba anterior
    };
  const text = (key: keyof WizardForm) => (e: { target: { value: string } }) => {
    set(key)(e.target.value);
  };

  const fieldError = (key: string): string | undefined => {
    const remote = saveError?.fields[key]?.[0] ?? testError?.fields[key]?.[0];
    const local = localErrors[key];
    return remote ?? (local ? t(`sapSystems.validation.${local}`) : undefined);
  };

  const goTo = (target: number) => {
    setLocalErrors({});
    setIndex(target);
  };
  const next = () => {
    const errors = validate(step, form, editing);
    setLocalErrors(errors);
    if (Object.keys(errors).length === 0) setIndex((i) => Math.min(i + 1, steps.length - 1));
  };

  const runTest = () => {
    const body: SapSystemTestRequest = {
      code: form.code.trim() || null,
      ...connectionPayload(form),
      ...(editing
        ? { reuse_credentials_from: existing.id, ca_bundle_pem: form.remove_ca ? '' : null }
        : { credentials: toCredentialsIn(form.credentials) }),
    };
    if (editing && !form.remove_ca && form.ca_bundle_pem.trim()) {
      body.ca_bundle_pem = form.ca_bundle_pem.trim();
    }
    test.mutate(body, { onSuccess: setReport });
  };

  const onSaveError = (error: unknown) => {
    const described = describeError(error);
    const target = Object.keys(described.fields)
      .map((f) => stepOfField(f, steps))
      .find(Boolean);
    if (target) setIndex(steps.indexOf(target));
  };

  const save = () => {
    if (editing) {
      update.mutate(changes(form, existing), {
        onSuccess: (s) => void navigate(`/sap-systems/${s.id}`),
        onError: onSaveError,
      });
      return;
    }
    create.mutate(
      {
        code: form.code.trim().toUpperCase(),
        name: form.name.trim(),
        description: form.description.trim() || null,
        environment: form.environment,
        system_type: form.system_type,
        tags: tagList(form.tags),
        ...connectionPayload(form),
        credentials: toCredentialsIn(form.credentials),
        run_test: true,
        activate: activate && report?.ok === true,
      },
      {
        onSuccess: (r) =>
          void navigate(`/sap-systems/${r.system.id}`, { state: { created: true } }),
        onError: onSaveError,
      },
    );
  };

  const stepContent: Record<StepId, ReactNode> = {
    general: (
      <div className="sx-stack">
        <div className="sx-two-columns">
          <TextField
            label={t('sapSystems.fields.code')}
            value={form.code}
            onChange={text('code')}
            disabled={editing}
            hint={t('sapSystems.hints.code')}
            error={fieldError('code')}
            maxLength={30}
          />
          <TextField
            label={t('sapSystems.fields.name')}
            value={form.name}
            onChange={text('name')}
            error={fieldError('name')}
            maxLength={200}
          />
        </div>
        <div className="sx-two-columns">
          <SelectField
            label={t('sapSystems.fields.environment')}
            value={form.environment}
            onChange={(e) => {
              set('environment')(e.target.value as SapEnvironment);
            }}
            options={ENVIRONMENTS.map((env) => ({
              value: env,
              label: t(`sapSystems.environments.${env}`),
            }))}
            error={fieldError('environment')}
          />
          <SelectField
            label={t('sapSystems.fields.systemType')}
            value={form.system_type}
            onChange={(e) => {
              set('system_type')(e.target.value as SystemType);
            }}
            options={SYSTEM_TYPES.map((st) => ({
              value: st,
              label: t(`sapSystems.systemTypes.${st}`),
            }))}
          />
        </div>
        <TextField
          label={t('sapSystems.fields.tags')}
          value={form.tags}
          onChange={text('tags')}
          hint={t('sapSystems.hints.tags')}
          error={fieldError('tags')}
        />
        <TextAreaField
          label={t('sapSystems.fields.description')}
          value={form.description}
          onChange={text('description')}
          rows={3}
        />
      </div>
    ),
    connection: (
      <div className="sx-stack">
        <TextField
          label={t('sapSystems.fields.baseUrl')}
          value={form.base_url}
          onChange={text('base_url')}
          placeholder="https://gateway.empresa.local:44300"
          hint={t('sapSystems.hints.baseUrl')}
          error={fieldError('base_url')}
        />
        <div className="sx-two-columns">
          <TextField
            label={t('sapSystems.fields.client')}
            value={form.sap_client}
            onChange={text('sap_client')}
            inputMode="numeric"
            maxLength={3}
            error={fieldError('sap_client')}
          />
          <TextField
            label={t('sapSystems.fields.language')}
            value={form.language}
            onChange={text('language')}
            maxLength={2}
            error={fieldError('language')}
          />
        </div>
        <Checkbox
          label={t('sapSystems.fields.tlsVerify')}
          description={t('sapSystems.hints.tlsVerify')}
          checked={form.tls_verify}
          onChange={(e) => {
            set('tls_verify')(e.target.checked);
          }}
        />
        {fieldError('tls_verify') && <Alert tone="danger">{fieldError('tls_verify')}</Alert>}
        {editing && existing.ca_bundle.length > 0 && (
          <Alert tone="info" title={t('sapSystems.wizard.currentCa')}>
            {existing.ca_bundle.map((c) => c.subject).join(' · ')}
            <Checkbox
              label={t('sapSystems.wizard.removeCa')}
              checked={form.remove_ca}
              onChange={(e) => {
                set('remove_ca')(e.target.checked);
              }}
            />
          </Alert>
        )}
        {!form.remove_ca && (
          <TextAreaField
            label={t('sapSystems.fields.caBundle')}
            mono
            rows={4}
            value={form.ca_bundle_pem}
            onChange={text('ca_bundle_pem')}
            placeholder="-----BEGIN CERTIFICATE-----"
            hint={editing ? t('sapSystems.wizard.caKeep') : t('sapSystems.hints.caBundle')}
            error={fieldError('ca_bundle_pem')}
          />
        )}
        <TextField
          label={t('sapSystems.fields.proxyUrl')}
          value={form.proxy_url}
          onChange={text('proxy_url')}
          placeholder="http://proxy.empresa.local:3128"
          error={fieldError('proxy_url')}
        />
        <div className="sx-two-columns">
          <TextField
            label={t('sapSystems.fields.timeout')}
            type="number"
            min={5}
            max={600}
            value={form.timeout_seconds}
            onChange={text('timeout_seconds')}
            error={fieldError('timeout_seconds')}
          />
          <TextField
            label={t('sapSystems.fields.concurrency')}
            type="number"
            min={1}
            max={32}
            value={form.max_concurrent_requests}
            onChange={text('max_concurrent_requests')}
            hint={t('sapSystems.hints.concurrency')}
            error={fieldError('max_concurrent_requests')}
          />
        </div>
      </div>
    ),
    credentials: (
      <CredentialsFields
        value={form.credentials}
        onChange={set('credentials')}
        errors={Object.fromEntries(
          Object.keys(localErrors).map((k) => [k, t('sapSystems.validation.required')]),
        )}
      />
    ),
    test: (
      <div className="sx-stack">
        <p className="sx-muted">{t('sapSystems.wizard.testHelp')}</p>
        {editing && <Alert tone="info">{t('sapSystems.wizard.testReusesCredentials')}</Alert>}
        <div>
          <Button onClick={runTest} disabled={test.isPending}>
            {test.isPending ? t('sapSystems.wizard.testing') : t('sapSystems.wizard.testButton')}
          </Button>
        </div>
        {test.isPending && <Spinner label={t('sapSystems.wizard.testing')} />}
        {testError && <Alert tone="danger">{testError.message}</Alert>}
        {report && <TestReportView report={report} />}
      </div>
    ),
    summary: (
      <div className="sx-stack">
        <DetailList
          items={[
            { label: t('sapSystems.fields.code'), value: form.code.toUpperCase() },
            { label: t('sapSystems.fields.name'), value: form.name },
            {
              label: t('sapSystems.fields.environment'),
              value: t(`sapSystems.environments.${form.environment}`),
            },
            {
              label: t('sapSystems.fields.systemType'),
              value: t(`sapSystems.systemTypes.${form.system_type}`),
            },
            { label: t('sapSystems.fields.baseUrl'), value: form.base_url },
            {
              label: t('sapSystems.fields.clientLanguage'),
              value: `${form.sap_client} / ${form.language.toUpperCase()}`,
            },
            {
              label: t('sapSystems.fields.tlsVerify'),
              value: form.tls_verify ? t('common.yes') : t('common.no'),
            },
            {
              label: t('sapSystems.fields.authType'),
              value: editing
                ? t(`sapSystems.authTypes.${existing.auth_type}`)
                : t(`sapSystems.authTypes.${form.credentials.auth_type}`),
            },
            {
              label: t('sapSystems.fields.lastTest'),
              value: report
                ? report.ok
                  ? t('sapSystems.test.ok')
                  : t('sapSystems.test.failed')
                : t('sapSystems.test.never'),
            },
          ]}
        />
        {editing && <p className="sx-muted">{t('sapSystems.wizard.credentialsElsewhere')}</p>}
        {!editing && (
          <Checkbox
            label={t('sapSystems.wizard.activate')}
            description={
              report?.ok
                ? t('sapSystems.wizard.activateHelp')
                : t('sapSystems.wizard.activateBlocked')
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
            ? t('sapSystems.wizard.titleEdit', { code: existing.code })
            : t('sapSystems.wizard.titleNew')
        }
        description={t('sapSystems.wizard.description')}
        actions={
          <Link to={editing ? `/sap-systems/${existing.id}` : '/sap-systems'}>
            {t('common.cancel')}
          </Link>
        }
      />
      <Stepper
        label={t('sapSystems.wizard.progress')}
        current={index}
        onSelect={goTo}
        steps={steps.map((id) => ({ id, label: t(`sapSystems.wizard.steps.${id}`) }))}
      />
      <Card title={t(`sapSystems.wizard.steps.${step}`)}>
        {saveError && Object.keys(saveError.fields).length > 0 && (
          <Alert tone="danger">{t('sapSystems.wizard.fixFields')}</Alert>
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
              {t('sapSystems.wizard.back')}
            </Button>
          )}
          {step !== 'summary' ? (
            <Button onClick={next}>{t('sapSystems.wizard.next')}</Button>
          ) : (
            <Button onClick={save} disabled={saving}>
              {saving ? t('sapSystems.wizard.saving') : t('sapSystems.wizard.save')}
            </Button>
          )}
        </div>
      </Card>
    </>
  );
}

export function SapSystemWizardPage() {
  const { t } = useTranslation();
  const { systemId = '' } = useParams();
  const system = useSapSystem(systemId);
  if (!systemId) return <Wizard />;
  if (system.isPending) return <Spinner label={t('common.loading')} />;
  if (system.error) return <Alert tone="danger">{describeError(system.error).message}</Alert>;
  return <Wizard existing={system.data} />;
}
