import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Checkbox, Dialog, SelectField, TextField } from '@/shared/ui';
import {
  ENVIRONMENTS,
  useDuplicateSapSystem,
  useRotateCredentials,
  type SapEnvironment,
  type SapSystemDetail,
} from '../api/queries';
import { CredentialsFields } from './CredentialsFields';
import {
  emptyCredentials,
  missingCredentialFields,
  toCredentialsIn,
  type CredentialsForm,
} from './credentials';
import { TestReportView } from './TestReportView';

interface DialogProps {
  system: SapSystemDetail;
  open: boolean;
  onClose: () => void;
}

/** Sustituye la credencial; la API la prueba enseguida y devuelve el resultado. */
export function RotateCredentialsDialog({ system, open, onClose }: DialogProps) {
  const { t } = useTranslation();
  const rotate = useRotateCredentials(system.id);
  const [form, setForm] = useState<CredentialsForm>({
    ...emptyCredentials,
    auth_type: system.auth_type,
  });
  const error = rotate.error ? describeError(rotate.error) : null;
  const close = () => {
    rotate.reset();
    setForm({ ...emptyCredentials, auth_type: system.auth_type });
    onClose();
  };
  const result = rotate.data;
  return (
    <Dialog
      open={open}
      onClose={close}
      title={t('sapSystems.rotate.title', { code: system.code })}
      closeLabel={t('common.close')}
      footer={
        result ? (
          <Button onClick={close}>{t('common.close')}</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={rotate.isPending || missingCredentialFields(form).length > 0}
              onClick={() => {
                rotate.mutate({ credentials: toCredentialsIn(form), version: system.version });
              }}
            >
              {rotate.isPending ? t('sapSystems.rotate.working') : t('sapSystems.rotate.submit')}
            </Button>
          </>
        )
      }
    >
      {result?.test ? (
        <TestReportView report={result.test} />
      ) : (
        <>
          <p>{t('sapSystems.rotate.help')}</p>
          {error && <Alert tone="danger">{error.message}</Alert>}
          <CredentialsFields
            value={form}
            onChange={setForm}
            errors={Object.fromEntries(
              Object.entries(error?.fields ?? {}).map(([k, v]) => [k, v[0]]),
            )}
          />
        </>
      )}
    </Dialog>
  );
}

/** Copia la conexión (p. ej. de QAS a PRD) cambiando código, URL, mandante y credenciales. */
export function DuplicateDialog({ system, open, onClose }: DialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const duplicate = useDuplicateSapSystem(system.id);
  const [code, setCode] = useState(`${system.code}_COPIA`.slice(0, 30));
  const [name, setName] = useState(`${system.name} (copia)`);
  const [environment, setEnvironment] = useState<SapEnvironment>(system.environment);
  const [baseUrl, setBaseUrl] = useState(system.base_url);
  const [client, setClient] = useState(system.sap_client);
  const [keepCredentials, setKeepCredentials] = useState(true);
  const [credentials, setCredentials] = useState<CredentialsForm>({
    ...emptyCredentials,
    auth_type: system.auth_type,
  });
  const error = duplicate.error ? describeError(duplicate.error) : null;
  const field = (key: string) => error?.fields[key]?.[0];
  const incomplete = !keepCredentials && missingCredentialFields(credentials).length > 0;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={t('sapSystems.duplicate.title', { code: system.code })}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            disabled={duplicate.isPending || !code.trim() || !name.trim() || incomplete}
            onClick={() => {
              duplicate.mutate(
                {
                  code: code.trim().toUpperCase(),
                  name: name.trim(),
                  environment,
                  base_url: baseUrl.trim() || null,
                  sap_client: client.trim() || null,
                  credentials: keepCredentials ? null : toCredentialsIn(credentials),
                },
                { onSuccess: (r) => void navigate(`/sap-systems/${r.system.id}`) },
              );
            }}
          >
            {t('sapSystems.duplicate.submit')}
          </Button>
        </>
      }
    >
      <p>{t('sapSystems.duplicate.help')}</p>
      {error && !Object.keys(error.fields).length && <Alert tone="danger">{error.message}</Alert>}
      <div className="sx-two-columns">
        <TextField
          label={t('sapSystems.fields.code')}
          value={code}
          maxLength={30}
          onChange={(e) => {
            setCode(e.target.value);
          }}
          error={field('code')}
        />
        <TextField
          label={t('sapSystems.fields.name')}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
          error={field('name')}
        />
      </div>
      <div className="sx-two-columns">
        <SelectField
          label={t('sapSystems.fields.environment')}
          value={environment}
          onChange={(e) => {
            setEnvironment(e.target.value as SapEnvironment);
          }}
          options={ENVIRONMENTS.map((env) => ({
            value: env,
            label: t(`sapSystems.environments.${env}`),
          }))}
        />
        <TextField
          label={t('sapSystems.fields.client')}
          value={client}
          maxLength={3}
          onChange={(e) => {
            setClient(e.target.value);
          }}
          error={field('sap_client')}
        />
      </div>
      <TextField
        label={t('sapSystems.fields.baseUrl')}
        value={baseUrl}
        onChange={(e) => {
          setBaseUrl(e.target.value);
        }}
        error={field('base_url')}
      />
      <Checkbox
        label={t('sapSystems.duplicate.keepCredentials')}
        description={t('sapSystems.duplicate.keepCredentialsHelp')}
        checked={keepCredentials}
        onChange={(e) => {
          setKeepCredentials(e.target.checked);
        }}
      />
      {!keepCredentials && <CredentialsFields value={credentials} onChange={setCredentials} />}
    </Dialog>
  );
}
