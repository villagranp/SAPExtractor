import { zodResolver } from '@hookform/resolvers/zod';
import { useState, type ReactNode } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useRoles } from '@/features/roles/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDate, formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  DetailList,
  PageHeader,
  SegmentedControl,
  SelectField,
  Spinner,
  StatusPill,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  useEntraProvider,
  useSaveEntra,
  useSetEntraEnabled,
  useTestEntra,
  type CredentialType,
  type EntraProvider,
} from '../api/queries';
import { EntraTestReportView } from '../components/EntraTestReportView';
import { GroupMappingsCard } from '../components/GroupMappingsCard';
import { RedirectUriField } from '../components/RedirectUriField';
import {
  entraFormSchema,
  formFieldOf,
  fromProvider,
  toTestRequest,
  toUpdateRequest,
  type EntraForm,
} from '../schemas/entraForm';
import '../identity-provider.css';

interface RoleOption {
  value: string;
  label: string;
}

function StatusCard({ provider }: { provider: EntraProvider }) {
  const { t } = useTranslation();
  const toggle = useSetEntraEnabled();
  const [confirm, setConfirm] = useState(false);
  const canEnable = provider.configured && provider.test_current;
  const enabling = !provider.enabled;

  return (
    <Card title={t('identityProvider.status.title')}>
      <div className="sx-stack">
        <p>
          {provider.enabled
            ? t('identityProvider.status.enabledText')
            : canEnable
              ? t('identityProvider.status.readyText')
              : t('identityProvider.status.pendingText')}
        </p>
        {toggle.error && <Alert tone="danger">{describeError(toggle.error).message}</Alert>}
        {provider.configured && (
          <div className="sx-row">
            <Button
              variant={enabling ? 'primary' : 'secondary'}
              disabled={enabling && !canEnable}
              onClick={() => {
                setConfirm(true);
              }}
            >
              {enabling
                ? t('identityProvider.status.enable')
                : t('identityProvider.status.disable')}
            </Button>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        title={
          enabling
            ? t('identityProvider.status.enableTitle')
            : t('identityProvider.status.disableTitle')
        }
        message={
          enabling
            ? t('identityProvider.status.enableConfirm')
            : t('identityProvider.status.disableConfirm')
        }
        confirmLabel={
          enabling ? t('identityProvider.status.enable') : t('identityProvider.status.disable')
        }
        cancelLabel={t('common.cancel')}
        danger={!enabling}
        busy={toggle.isPending}
        onClose={() => {
          setConfirm(false);
        }}
        onConfirm={() => {
          toggle.mutate(
            { enabled: enabling, version: provider.version ?? 0 },
            {
              onSettled: () => {
                setConfirm(false);
              },
            },
          );
        }}
      />
    </Card>
  );
}

function CredentialSummary({ provider }: { provider: EntraProvider }) {
  const { t } = useTranslation();
  const credential = provider.credential;
  if (!credential) return null;
  const items: { label: string; value: ReactNode }[] = [
    {
      label: t('identityProvider.credential.current'),
      value: t(`identityProvider.credential.types.${credential.credential_type}`),
    },
    {
      label: t('identityProvider.credential.expires'),
      value: credential.expires_at
        ? formatDate(credential.expires_at)
        : t('identityProvider.credential.noExpiry'),
    },
    {
      label: t('identityProvider.credential.updated'),
      value: formatDateTime(credential.updated_at),
    },
  ];
  if (credential.credential_type === 'certificate') {
    items.push(
      {
        label: t('identityProvider.credential.subject'),
        value: credential.certificate_subject ?? '—',
      },
      {
        label: t('identityProvider.credential.thumbprint'),
        value: <code className="sx-idp__mono">{credential.certificate_thumbprint}</code>,
      },
    );
  }
  return <DetailList items={items} />;
}

function SettingsForm({ provider, roles }: { provider: EntraProvider; roles: RoleOption[] }) {
  const { t } = useTranslation();
  const save = useSaveEntra();
  const test = useTestEntra();
  const form = useForm<EntraForm>({
    resolver: zodResolver(entraFormSchema),
    defaultValues: fromProvider(provider),
  });
  const credentialType = useWatch({ control: form.control, name: 'credential_type' });
  const stored = provider.credential?.credential_type;
  const keepsStored = stored === credentialType;
  const serverError = save.error ? describeError(save.error) : null;
  const testError = test.error ? describeError(test.error) : null;

  const fieldError = (name: keyof EntraForm) => {
    const local = form.formState.errors[name]?.message;
    if (local) return t(`identityProvider.errors.${local}`, { defaultValue: local });
    const fields = { ...testError?.fields, ...serverError?.fields };
    const key = Object.keys(fields).find((k) => formFieldOf(k) === name);
    return key ? fields[key]?.[0] : undefined;
  };

  const onSave = form.handleSubmit((values) => {
    test.reset();
    save.mutate(toUpdateRequest(values, provider), {
      // Vacía secreto y clave del formulario y muestra lo normalizado por la API.
      onSuccess: (saved) => {
        form.reset(fromProvider(saved));
      },
    });
  });
  const onTest = form.handleSubmit((values) => {
    save.reset();
    test.mutate(toTestRequest(values, provider));
  });

  const report = test.data ?? provider.last_test ?? null;
  const outdated = !test.data && Boolean(provider.last_test?.ok) && !provider.test_current;

  return (
    <form onSubmit={(e) => void onSave(e)} noValidate className="sx-stack">
      {serverError && <Alert tone="danger">{serverError.message}</Alert>}
      <Card title={t('identityProvider.app.title')}>
        <div className="sx-stack">
          <p className="sx-muted">{t('identityProvider.app.intro')}</p>
          <TextField
            label={t('identityProvider.app.tenantId')}
            hint={t('identityProvider.app.tenantHint')}
            autoComplete="off"
            spellCheck={false}
            error={fieldError('tenant_id')}
            {...form.register('tenant_id')}
          />
          <TextField
            label={t('identityProvider.app.clientId')}
            hint={t('identityProvider.app.clientHint')}
            autoComplete="off"
            spellCheck={false}
            error={fieldError('client_id')}
            {...form.register('client_id')}
          />
          <RedirectUriField value={provider.redirect_uri} />
        </div>
      </Card>

      <Card title={t('identityProvider.credential.title')}>
        <div className="sx-stack">
          <CredentialSummary provider={provider} />
          <SegmentedControl<CredentialType>
            label={t('identityProvider.credential.type')}
            value={credentialType}
            options={[
              { value: 'certificate', label: t('identityProvider.credential.types.certificate') },
              {
                value: 'client_secret',
                label: t('identityProvider.credential.types.client_secret'),
              },
            ]}
            onChange={(value) => {
              form.setValue('credential_type', value, { shouldDirty: true });
            }}
            hint={t(`identityProvider.credential.hints.${credentialType}`)}
          />
          {credentialType === 'client_secret' ? (
            <>
              <TextField
                type="password"
                autoComplete="new-password"
                label={t('identityProvider.credential.secret')}
                hint={
                  keepsStored
                    ? t('identityProvider.credential.keepSecret')
                    : t('identityProvider.credential.secretHint')
                }
                error={fieldError('client_secret')}
                {...form.register('client_secret')}
              />
              <TextField
                type="date"
                label={t('identityProvider.credential.secretExpires')}
                hint={t('identityProvider.credential.secretExpiresHint')}
                error={fieldError('secret_expires_at')}
                {...form.register('secret_expires_at')}
              />
            </>
          ) : (
            <>
              {keepsStored && (
                <p className="sx-muted">{t('identityProvider.credential.keepCertificate')}</p>
              )}
              <TextAreaField
                mono
                rows={6}
                spellCheck={false}
                label={t('identityProvider.credential.certificate')}
                hint={t('identityProvider.credential.certificateHint')}
                error={fieldError('certificate_pem')}
                {...form.register('certificate_pem')}
              />
              <TextAreaField
                mono
                rows={6}
                spellCheck={false}
                autoComplete="off"
                label={t('identityProvider.credential.privateKey')}
                error={fieldError('private_key_pem')}
                {...form.register('private_key_pem')}
              />
              <TextField
                type="password"
                autoComplete="new-password"
                label={t('identityProvider.credential.passphrase')}
                error={fieldError('private_key_passphrase')}
                {...form.register('private_key_passphrase')}
              />
            </>
          )}
        </div>
      </Card>

      <Card title={t('identityProvider.access.title')}>
        <div className="sx-stack">
          <Checkbox
            label={t('identityProvider.access.requireGroup')}
            description={t('identityProvider.access.requireGroupHint')}
            {...form.register('require_group_membership')}
          />
          <Checkbox
            label={t('identityProvider.access.jit')}
            description={t('identityProvider.access.jitHint')}
            {...form.register('jit_provisioning')}
          />
          <SelectField
            label={t('identityProvider.access.defaultRole')}
            hint={t('identityProvider.access.defaultRoleHint')}
            placeholder={t('identityProvider.access.noDefaultRole')}
            options={roles}
            error={fieldError('default_role_id')}
            {...form.register('default_role_id')}
          />
          <TextField
            label={t('identityProvider.access.domains')}
            hint={t('identityProvider.access.domainsHint')}
            autoComplete="off"
            error={fieldError('allowed_domains')}
            {...form.register('allowed_domains')}
          />
        </div>
      </Card>

      {save.isSuccess && <Alert tone="success">{t('identityProvider.saved')}</Alert>}
      <div className="sx-row">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? t('identityProvider.saving') : t('identityProvider.save')}
        </Button>
        <Button
          variant="secondary"
          disabled={test.isPending}
          onClick={() => {
            void onTest();
          }}
        >
          {test.isPending ? t('identityProvider.testing') : t('identityProvider.test')}
        </Button>
      </div>
      {testError && <Alert tone="danger">{testError.message}</Alert>}
      {test.isPending && <Spinner label={t('identityProvider.testing')} />}
      {report && !test.isPending && (
        <Card title={t('identityProvider.report.title')}>
          <EntraTestReportView report={report} outdated={outdated} />
        </Card>
      )}
    </form>
  );
}

/** Opciones de rol sin repetir, en el orden en que llegan. */
function optionsOf(roles: { id: string; name: string }[]): RoleOption[] {
  const seen = new Map<string, RoleOption>();
  for (const role of roles) {
    if (!seen.has(role.id)) seen.set(role.id, { value: role.id, label: role.name });
  }
  return [...seen.values()];
}

export function IdentityProviderPage() {
  const { t } = useTranslation();
  const provider = useEntraProvider();
  const roles = useRoles();
  const data = provider.data;
  // Los `select` se montan con su valor: sin la opción, el navegador lo deja vacío y al
  // guardar se perdería el rol. Se espera a la lista y se incluyen siempre los roles ya
  // configurados (por si quien configura no puede leer la lista de roles).
  const rolesReady = !roles.isPending;
  const roleOptions = optionsOf([
    ...(roles.data ?? []),
    ...(data?.default_role ? [data.default_role] : []),
    ...(data?.group_mappings ?? []).map((m) => m.role),
  ]);

  return (
    <>
      <PageHeader
        title={t('identityProvider.title')}
        description={t('identityProvider.description')}
        actions={
          <>
            {data && (
              <StatusPill tone={data.enabled ? 'success' : 'neutral'}>
                {data.enabled
                  ? t('identityProvider.status.enabled')
                  : data.configured
                    ? t('identityProvider.status.disabled')
                    : t('identityProvider.status.notConfigured')}
              </StatusPill>
            )}
            <Link to="/admin/identity-provider/guide">{t('identityProvider.guideLink')}</Link>
          </>
        }
      />
      {(provider.isPending || !rolesReady) && <Spinner label={t('common.loading')} />}
      {provider.error && <Alert tone="danger">{describeError(provider.error).message}</Alert>}
      {data && rolesReady && (
        <div className="sx-stack">
          {data.warnings.length > 0 && (
            <Alert tone="warning" title={t('identityProvider.warningsTitle')}>
              <ul className="sx-idp__details">
                {data.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Alert>
          )}
          <StatusCard provider={data} />
          <SettingsForm provider={data} roles={roleOptions} />
          {data.configured ? (
            <GroupMappingsCard provider={data} roles={roleOptions} />
          ) : (
            <Card title={t('identityProvider.groups.title')}>
              <p className="sx-muted">{t('identityProvider.groups.saveFirst')}</p>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
