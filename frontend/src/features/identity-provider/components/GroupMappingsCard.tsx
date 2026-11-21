import { zodResolver } from '@hookform/resolvers/zod';
import { useFieldArray, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Card, IconPlus, SelectField, TextField } from '@/shared/ui';
import type { EntraProvider } from '../api/queries';
import { useSaveGroupMappings } from '../api/queries';
import {
  mappingsFormSchema,
  mappingsFromProvider,
  toMappings,
  type MappingsForm,
} from '../schemas/entraForm';

interface RoleOption {
  value: string;
  label: string;
}

/** Grupos de seguridad de Entra que conceden roles en cada inicio de sesión. */
export function GroupMappingsCard({
  provider,
  roles,
}: {
  provider: EntraProvider;
  roles: RoleOption[];
}) {
  const { t } = useTranslation();
  const save = useSaveGroupMappings();
  const form = useForm<MappingsForm>({
    resolver: zodResolver(mappingsFormSchema),
    defaultValues: mappingsFromProvider(provider),
  });
  const rows = useFieldArray({ control: form.control, name: 'mappings' });
  const serverError = save.error ? describeError(save.error) : null;
  const errorOf = (index: number, field: 'group_object_id' | 'group_name' | 'role_id') => {
    const local = form.formState.errors.mappings?.[index]?.[field]?.message;
    if (local) return t(`identityProvider.errors.${local}`, { defaultValue: local });
    return serverError?.fields[`mappings.${String(index)}.${field}`]?.[0];
  };

  const submit = form.handleSubmit((values) => {
    save.mutate(
      { version: provider.version ?? 0, mappings: toMappings(values) },
      {
        onSuccess: (saved) => {
          form.reset(mappingsFromProvider(saved));
        },
      },
    );
  });

  return (
    <Card title={t('identityProvider.groups.title')}>
      <form onSubmit={(e) => void submit(e)} noValidate className="sx-stack">
        <p className="sx-muted">{t('identityProvider.groups.intro')}</p>
        {serverError && <Alert tone="danger">{serverError.message}</Alert>}
        {save.isSuccess && !form.formState.isDirty && (
          <Alert tone="success">{t('identityProvider.groups.saved')}</Alert>
        )}
        {rows.fields.length === 0 && (
          <p className="sx-muted">{t('identityProvider.groups.empty')}</p>
        )}
        <ol className="sx-idp__mappings">
          {rows.fields.map((row, index) => (
            <li key={row.id} className="sx-idp__mapping">
              <TextField
                label={t('identityProvider.groups.objectId')}
                autoComplete="off"
                spellCheck={false}
                error={errorOf(index, 'group_object_id')}
                {...form.register(`mappings.${index}.group_object_id`)}
              />
              <TextField
                label={t('identityProvider.groups.name')}
                error={errorOf(index, 'group_name')}
                {...form.register(`mappings.${index}.group_name`)}
              />
              <SelectField
                label={t('identityProvider.groups.role')}
                options={roles}
                placeholder={t('identityProvider.groups.chooseRole')}
                error={errorOf(index, 'role_id')}
                {...form.register(`mappings.${index}.role_id`)}
              />
              <Button
                variant="secondary"
                aria-label={t('identityProvider.groups.removeRow', { index: index + 1 })}
                onClick={() => {
                  rows.remove(index);
                }}
              >
                {t('identityProvider.groups.remove')}
              </Button>
            </li>
          ))}
        </ol>
        <div className="sx-row">
          <Button
            variant="secondary"
            icon={<IconPlus size={18} aria-hidden />}
            onClick={() => {
              rows.append({ group_object_id: '', group_name: '', role_id: '' });
            }}
          >
            {t('identityProvider.groups.add')}
          </Button>
          <Button type="submit" disabled={save.isPending || !form.formState.isDirty}>
            {save.isPending
              ? t('identityProvider.groups.saving')
              : t('identityProvider.groups.save')}
          </Button>
        </div>
      </form>
    </Card>
  );
}
