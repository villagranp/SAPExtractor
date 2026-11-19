import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useRoles } from '@/features/roles/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  FieldErrors,
  OneTimeSecret,
  SelectField,
  TextField,
} from '@/shared/ui';
import { useCreateUser, type AuthSource, type UserDetail } from '../api/queries';

export function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const roles = useRoles();
  const create = useCreateUser();
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [authSource, setAuthSource] = useState<AuthSource>('local');
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [created, setCreated] = useState<{ user: UserDetail; password: string | null } | null>(
    null,
  );
  const error = create.error ? describeError(create.error) : null;

  const close = () => {
    setUsername('');
    setDisplayName('');
    setEmail('');
    setAuthSource('local');
    setRoleIds([]);
    setCreated(null);
    create.reset();
    onClose();
  };

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    create.mutate(
      {
        username: username.trim(),
        display_name: displayName.trim(),
        email: email.trim() || null,
        auth_source: authSource,
        role_ids: roleIds,
      },
      {
        onSuccess: (data) => {
          setCreated({ user: data.user, password: data.temporary_password });
        },
      },
    );
  };

  if (created) {
    return (
      <Dialog
        open={open}
        title={t('users.create.createdTitle')}
        onClose={close}
        footer={
          <>
            <Link to={`/admin/users/${created.user.id}`} className="sx-btn sx-btn--secondary">
              {t('users.create.openUser')}
            </Link>
            <Button onClick={close}>{t('common.close')}</Button>
          </>
        }
      >
        <p>{t('users.create.createdBody', { username: created.user.username })}</p>
        {created.password ? (
          <OneTimeSecret
            label={t('users.create.tempPassword')}
            value={created.password}
            warning={t('users.create.tempPasswordWarning')}
            copyLabel={t('common.copy')}
            copiedLabel={t('common.copied')}
          />
        ) : (
          <Alert tone="info">{t('users.create.entraHint')}</Alert>
        )}
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      title={t('users.create.title')}
      onClose={close}
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="sx-create-user"
            disabled={create.isPending || !username.trim() || !displayName.trim()}
          >
            {t('users.create.submit')}
          </Button>
        </>
      }
    >
      <form id="sx-create-user" onSubmit={submit} noValidate>
        {error && !Object.keys(error.fields).length && <Alert tone="danger">{error.message}</Alert>}
        <TextField
          label={t('users.fields.username')}
          hint={t('users.create.usernameHint')}
          value={username}
          onChange={(e) => {
            setUsername(e.target.value);
          }}
          autoComplete="off"
          required
        />
        <FieldErrors errors={error?.fields.username} />
        <TextField
          label={t('users.fields.displayName')}
          value={displayName}
          onChange={(e) => {
            setDisplayName(e.target.value);
          }}
          required
        />
        <TextField
          label={t('users.fields.email')}
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
          }}
        />
        <FieldErrors errors={error?.fields.email} />
        <SelectField
          label={t('users.fields.authSource')}
          value={authSource}
          onChange={(e) => {
            setAuthSource(e.target.value as AuthSource);
          }}
          options={[
            { value: 'local', label: t('users.authSources.local') },
            { value: 'entra', label: t('users.authSources.entra') },
          ]}
          hint={authSource === 'entra' ? t('users.create.entraHint') : t('users.create.localHint')}
        />
        <fieldset className="sx-fieldset">
          <legend>{t('users.fields.roles')}</legend>
          {(roles.data ?? []).map((role) => (
            <Checkbox
              key={role.id}
              label={role.name}
              description={role.description}
              checked={roleIds.includes(role.id)}
              onChange={(e) => {
                setRoleIds((ids) =>
                  e.target.checked ? [...ids, role.id] : ids.filter((id) => id !== role.id),
                );
              }}
            />
          ))}
        </fieldset>
      </form>
    </Dialog>
  );
}
