import type { AuthType, CredentialsIn } from '../api/queries';

/** Estado del formulario de credenciales (todos los tipos; se envía solo el elegido). */
export interface CredentialsForm {
  auth_type: AuthType;
  username: string;
  password: string;
  certificate_pem: string;
  private_key_pem: string;
  private_key_passphrase: string;
  token_url: string;
  client_id: string;
  client_secret: string;
  scope: string;
}

export const emptyCredentials: CredentialsForm = {
  auth_type: 'basic',
  username: '',
  password: '',
  certificate_pem: '',
  private_key_pem: '',
  private_key_passphrase: '',
  token_url: '',
  client_id: '',
  client_secret: '',
  scope: '',
};

/** Campos obligatorios vacíos del tipo elegido (validación de formato en el navegador). */
export function missingCredentialFields(form: CredentialsForm): string[] {
  const required: Record<AuthType, (keyof CredentialsForm)[]> = {
    basic: ['username', 'password'],
    x509: ['certificate_pem', 'private_key_pem'],
    oauth2_client_credentials: ['token_url', 'client_id', 'client_secret'],
  };
  return required[form.auth_type].filter((key) => !form[key].trim());
}

export function toCredentialsIn(form: CredentialsForm): CredentialsIn {
  switch (form.auth_type) {
    case 'basic':
      return { auth_type: 'basic', username: form.username.trim(), password: form.password };
    case 'x509':
      return {
        auth_type: 'x509',
        certificate_pem: form.certificate_pem.trim(),
        private_key_pem: form.private_key_pem.trim(),
        private_key_passphrase: form.private_key_passphrase || null,
      };
    case 'oauth2_client_credentials':
      return {
        auth_type: 'oauth2_client_credentials',
        token_url: form.token_url.trim(),
        client_id: form.client_id.trim(),
        client_secret: form.client_secret,
        scope: form.scope.trim() || null,
      };
  }
}
