import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  FieldErrors,
  PageHeader,
  Spinner,
  TextField,
} from '@/shared/ui';
import {
  useCreateRole,
  useDeleteRole,
  usePermissions,
  useRole,
  useUpdateRole,
  type Role,
} from '../api/queries';
import { PermissionMatrix } from '../components/PermissionMatrix';

function RoleForm({ role }: { role: Role | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const permissions = usePermissions();
  const canManage = useCan('role:manage');
  const create = useCreateRole();
  const update = useUpdateRole(role?.id ?? '');
  const remove = useDeleteRole(role?.id ?? '');
  const [code, setCode] = useState(role?.code ?? '');
  const [name, setName] = useState(role?.name ?? '');
  const [description, setDescription] = useState(role?.description ?? '');
  const [selected, setSelected] = useState<string[]>(role?.permissions ?? []);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const readOnly = !canManage || (role?.is_system ?? false);
  const mutation = role ? update : create;
  const error = mutation.error ? describeError(mutation.error) : null;

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (role) {
      update.mutate({
        name: name.trim(),
        description: description.trim(),
        permissions: selected,
        version: role.version,
      });
    } else {
      create.mutate(
        {
          code: code.trim(),
          name: name.trim(),
          description: description.trim() || null,
          permissions: selected,
        },
        { onSuccess: (created) => void navigate(`/admin/roles/${created.id}`, { replace: true }) },
      );
    }
  };

  return (
    <form onSubmit={submit} noValidate className="sx-stack">
      {role?.is_system && <Alert tone="info">{t('roles.systemReadonly')}</Alert>}
      {error && <Alert tone="danger">{error.message}</Alert>}
      {update.isSuccess && <Alert tone="success">{t('common.saved')}</Alert>}
      <Card title={t('roles.editor.general')}>
        <TextField
          label={t('roles.fields.code')}
          hint={t('roles.editor.codeHint')}
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
          }}
          disabled={role !== null}
          required
        />
        <FieldErrors errors={error?.fields.code} />
        <TextField
          label={t('roles.fields.name')}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
          disabled={readOnly}
          required
        />
        <TextField
          label={t('roles.fields.description')}
          value={description}
          onChange={(e) => {
            setDescription(e.target.value);
          }}
          disabled={readOnly}
        />
      </Card>
      <Card title={t('roles.fields.permissions')}>
        <FieldErrors errors={error?.fields.permissions} />
        {permissions.data ? (
          <PermissionMatrix
            permissions={permissions.data}
            selected={selected}
            onChange={setSelected}
            disabled={readOnly}
          />
        ) : (
          <Spinner label={t('common.loading')} />
        )}
      </Card>
      {!readOnly && (
        <div className="sx-row">
          <Button
            type="submit"
            disabled={
              mutation.isPending || !name.trim() || (!role && !code.trim()) || selected.length === 0
            }
          >
            {t('common.save')}
          </Button>
          {role && (
            <Button
              variant="danger"
              onClick={() => {
                setConfirmDelete(true);
              }}
            >
              {t('common.delete')}
            </Button>
          )}
        </div>
      )}
      {role && (
        <ConfirmDialog
          open={confirmDelete}
          title={t('roles.editor.deleteTitle')}
          message={t('roles.editor.deleteConfirm', { name: role.name })}
          confirmLabel={t('common.delete')}
          cancelLabel={t('common.cancel')}
          danger
          busy={remove.isPending}
          error={remove.error && <Alert tone="danger">{describeError(remove.error).message}</Alert>}
          onClose={() => {
            remove.reset();
            setConfirmDelete(false);
          }}
          onConfirm={() => {
            remove.mutate(undefined, {
              onSuccess: () => void navigate('/admin/roles', { replace: true }),
            });
          }}
        />
      )}
    </form>
  );
}

export function RoleEditorPage() {
  const { t } = useTranslation();
  const { roleId } = useParams();
  const role = useRole(roleId);
  const isNew = roleId === undefined;

  return (
    <>
      <p>
        <Link to="/admin/roles">← {t('roles.title')}</Link>
      </p>
      <PageHeader
        title={isNew ? t('roles.editor.newTitle') : (role.data?.name ?? t('roles.title'))}
      />
      {!isNew && role.isPending && <Spinner label={t('common.loading')} />}
      {!isNew && role.error && <Alert tone="danger">{describeError(role.error).message}</Alert>}
      {isNew && <RoleForm role={null} />}
      {role.data && <RoleForm key={role.data.version} role={role.data} />}
    </>
  );
}
