import { z } from 'zod';
import type {
  EntraCredentialIn,
  EntraProvider,
  EntraTestRequest,
  EntraUpdateRequest,
  GroupMappingIn,
} from '../api/queries';

/**
 * Formulario de Entra ID (plan 02 §4). Los mensajes son claves de `identityProvider.errors.*`.
 * El secreto y la clave privada nunca llegan desde la API: vacíos = se conserva lo guardado.
 */
const GUID = /^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$/;

const guid = z.string().trim().regex(GUID, 'guid');

export const entraFormSchema = z.object({
  tenant_id: guid,
  client_id: guid,
  credential_type: z.enum(['client_secret', 'certificate']),
  client_secret: z.string().max(512, 'tooLong'),
  secret_expires_at: z.string(),
  certificate_pem: z.string(),
  private_key_pem: z.string(),
  private_key_passphrase: z.string().max(256, 'tooLong'),
  jit_provisioning: z.boolean(),
  require_group_membership: z.boolean(),
  default_role_id: z.string(),
  allowed_domains: z.string().max(2000, 'tooLong'),
});

export type EntraForm = z.infer<typeof entraFormSchema>;

export const mappingRowSchema = z.object({
  group_object_id: guid,
  group_name: z.string().trim().min(1, 'required').max(256, 'tooLong'),
  role_id: z.string().min(1, 'required'),
});

export const mappingsFormSchema = z.object({ mappings: z.array(mappingRowSchema).max(200) });
export type MappingsForm = z.infer<typeof mappingsFormSchema>;

export function fromProvider(provider: EntraProvider | undefined): EntraForm {
  const expires = provider?.credential?.expires_at;
  return {
    tenant_id: provider?.tenant_id ?? '',
    client_id: provider?.client_id ?? '',
    credential_type: provider?.credential?.credential_type ?? 'certificate',
    client_secret: '',
    secret_expires_at:
      provider?.credential?.credential_type === 'client_secret' && expires
        ? expires.slice(0, 10)
        : '',
    certificate_pem: '',
    private_key_pem: '',
    private_key_passphrase: '',
    jit_provisioning: provider?.jit_provisioning ?? false,
    require_group_membership: provider?.require_group_membership ?? true,
    default_role_id: provider?.default_role?.id ?? '',
    allowed_domains: (provider?.allowed_domains ?? []).join(', '),
  };
}

export function mappingsFromProvider(provider: EntraProvider | undefined): MappingsForm {
  return {
    mappings: (provider?.group_mappings ?? []).map((m) => ({
      group_object_id: m.group_object_id,
      group_name: m.group_name,
      role_id: m.role.id,
    })),
  };
}

export function splitDomains(value: string): string[] {
  return value
    .split(/[\s,;]+/)
    .map((d) => d.trim())
    .filter(Boolean);
}

/** Fecha del formulario (aaaa-mm-dd) como fin de ese día en UTC. */
function endOfDay(date: string): string | null {
  return date ? `${date}T23:59:59Z` : null;
}

/** Credencial a enviar: `null` si no hay nada nuevo y el tipo no cambia. */
export function credentialOf(
  form: EntraForm,
  provider: EntraProvider | undefined,
): EntraCredentialIn | null {
  const current = provider?.credential?.credential_type;
  if (form.credential_type === 'client_secret') {
    const secret = form.client_secret.trim();
    const expires = endOfDay(form.secret_expires_at);
    const expiresChanged = expires?.slice(0, 10) !== provider?.credential?.expires_at?.slice(0, 10);
    if (!secret && current === 'client_secret' && !expiresChanged) return null;
    return {
      credential_type: 'client_secret',
      client_secret: secret || null,
      secret_expires_at: expires,
    };
  }
  const hasCertificate = Boolean(form.certificate_pem.trim() || form.private_key_pem.trim());
  if (!hasCertificate && current === 'certificate') return null;
  return {
    credential_type: 'certificate',
    certificate_pem: form.certificate_pem.trim() || null,
    private_key_pem: form.private_key_pem.trim() || null,
    private_key_passphrase: form.private_key_passphrase || null,
  };
}

export function toUpdateRequest(
  form: EntraForm,
  provider: EntraProvider | undefined,
): EntraUpdateRequest {
  return {
    version: provider?.configured ? (provider.version ?? null) : null,
    tenant_id: form.tenant_id.trim(),
    client_id: form.client_id.trim(),
    credential: credentialOf(form, provider),
    jit_provisioning: form.jit_provisioning,
    require_group_membership: form.require_group_membership,
    default_role_id: form.default_role_id || null,
    allowed_domains: splitDomains(form.allowed_domains),
  };
}

export function toTestRequest(
  form: EntraForm,
  provider: EntraProvider | undefined,
): EntraTestRequest {
  return {
    tenant_id: form.tenant_id.trim(),
    client_id: form.client_id.trim(),
    credential: credentialOf(form, provider),
  };
}

export function toMappings(form: MappingsForm): GroupMappingIn[] {
  return form.mappings.map((m) => ({
    group_object_id: m.group_object_id.trim(),
    group_name: m.group_name.trim(),
    role_id: m.role_id,
  }));
}

/** Errores de la API (`credential.client_secret`, `mappings.0.role_id`) al campo del formulario. */
export function formFieldOf(apiField: string): string {
  return apiField.replace(/^credential\./, '');
}
