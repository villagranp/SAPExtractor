import { useTranslation } from 'react-i18next';
import { SelectField, TextAreaField, TextField } from '@/shared/ui';
import { AUTH_TYPES, type AuthType } from '../api/queries';
import type { CredentialsForm } from './credentials';

export function CredentialsFields({
  value,
  onChange,
  errors = {},
}: {
  value: CredentialsForm;
  onChange: (value: CredentialsForm) => void;
  errors?: Record<string, string | undefined>;
}) {
  const { t } = useTranslation();
  const set = (key: keyof CredentialsForm) => (e: { target: { value: string } }) => {
    onChange({ ...value, [key]: e.target.value });
  };
  const err = (key: string) => errors[key] ?? errors[`credentials.${key}`];
  return (
    <div className="sx-stack">
      <SelectField
        label={t('sapSystems.fields.authType')}
        value={value.auth_type}
        onChange={(e) => {
          onChange({ ...value, auth_type: e.target.value as AuthType });
        }}
        options={AUTH_TYPES.map((a) => ({ value: a, label: t(`sapSystems.authTypes.${a}`) }))}
        hint={t(`sapSystems.credentials.help.${value.auth_type}`)}
      />
      {value.auth_type === 'basic' && (
        <div className="sx-two-columns">
          <TextField
            label={t('sapSystems.credentials.username')}
            value={value.username}
            onChange={set('username')}
            autoComplete="off"
            error={err('username')}
          />
          <TextField
            label={t('sapSystems.credentials.password')}
            type="password"
            value={value.password}
            onChange={set('password')}
            autoComplete="new-password"
            error={err('password')}
          />
        </div>
      )}
      {value.auth_type === 'x509' && (
        <>
          <TextAreaField
            label={t('sapSystems.credentials.certificate')}
            mono
            rows={5}
            value={value.certificate_pem}
            onChange={set('certificate_pem')}
            placeholder="-----BEGIN CERTIFICATE-----"
            error={err('certificate_pem')}
          />
          <TextAreaField
            label={t('sapSystems.credentials.privateKey')}
            mono
            rows={5}
            value={value.private_key_pem}
            onChange={set('private_key_pem')}
            placeholder="-----BEGIN PRIVATE KEY-----"
            autoComplete="off"
            error={err('private_key_pem')}
          />
          <TextField
            label={t('sapSystems.credentials.passphrase')}
            type="password"
            value={value.private_key_passphrase}
            onChange={set('private_key_passphrase')}
            autoComplete="new-password"
            error={err('private_key_passphrase')}
          />
        </>
      )}
      {value.auth_type === 'oauth2_client_credentials' && (
        <>
          <TextField
            label={t('sapSystems.credentials.tokenUrl')}
            value={value.token_url}
            onChange={set('token_url')}
            placeholder="https://<subdominio>.authentication.eu10.hana.ondemand.com/oauth/token"
            error={err('token_url')}
          />
          <div className="sx-two-columns">
            <TextField
              label={t('sapSystems.credentials.clientId')}
              value={value.client_id}
              onChange={set('client_id')}
              autoComplete="off"
              error={err('client_id')}
            />
            <TextField
              label={t('sapSystems.credentials.clientSecret')}
              type="password"
              value={value.client_secret}
              onChange={set('client_secret')}
              autoComplete="new-password"
              error={err('client_secret')}
            />
          </div>
          <TextField
            label={t('sapSystems.credentials.scope')}
            value={value.scope}
            onChange={set('scope')}
          />
        </>
      )}
      <p className="sx-muted">{t('sapSystems.credentials.secretNote')}</p>
    </div>
  );
}
