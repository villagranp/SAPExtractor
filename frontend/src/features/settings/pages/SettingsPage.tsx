import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Dialog,
  PageHeader,
  TextAreaField,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  useConcurrencyGroups,
  useDeleteGroup,
  useSaveGroup,
  type ConcurrencyGroup,
} from '../api/queries';

const CODE = /^[A-Za-z][A-Za-z0-9_]{1,29}$/;

/** Alta o edición de un grupo. Se monta al abrirlo, así cada apertura parte de cero. */
function GroupDialog({ group, onClose }: { group: ConcurrencyGroup | null; onClose: () => void }) {
  const { t } = useTranslation();
  const formId = useId();
  const save = useSaveGroup();
  const [code, setCode] = useState(group?.code ?? '');
  const [limit, setLimit] = useState(String(group?.max_parallel ?? 2));
  const [description, setDescription] = useState(group?.description ?? '');
  const described = save.error ? describeError(save.error) : null;
  const codeOk = CODE.test(code.trim());
  const parallel = Number(limit);
  const limitOk = /^\d+$/.test(limit) && parallel >= 1 && parallel <= 64;

  return (
    <Dialog
      open
      title={
        group ? t('settings.groups.editTitle', { code: group.code }) : t('settings.groups.newTitle')
      }
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={save.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form={formId} disabled={save.isPending || !codeOk || !limitOk}>
            {t('settings.groups.save')}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        className="sx-stack"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!codeOk || !limitOk) return;
          save.mutate(
            {
              code: code.trim().toUpperCase(),
              max_parallel: parallel,
              description: description.trim(),
            },
            { onSuccess: onClose },
          );
        }}
      >
        {described && <Alert tone="danger">{described.message}</Alert>}
        <TextField
          label={t('settings.groups.code')}
          hint={group ? t('settings.groups.codeFixed') : t('settings.groups.codeHint')}
          disabled={group !== null}
          autoComplete="off"
          spellCheck={false}
          value={code}
          error={
            described?.fields.code?.[0] ??
            (code && !codeOk ? t('settings.groups.codeError') : undefined)
          }
          onChange={(e) => {
            setCode(e.target.value);
          }}
        />
        <TextField
          label={t('settings.groups.limit')}
          hint={t('settings.groups.limitHint')}
          type="number"
          min={1}
          max={64}
          value={limit}
          error={
            described?.fields.max_parallel?.[0] ??
            (limitOk ? undefined : t('settings.groups.limitError'))
          }
          onChange={(e) => {
            setLimit(e.target.value);
          }}
        />
        <TextAreaField
          label={t('settings.groups.description')}
          rows={2}
          maxLength={500}
          value={description}
          onChange={(e) => {
            setDescription(e.target.value);
          }}
        />
      </form>
    </Dialog>
  );
}

type Editing = { group: ConcurrencyGroup | null } | null;

/** Ajustes de la plataforma (plan 10 §3). Por ahora, los grupos de concurrencia (plan 08 §3). */
export function SettingsPage() {
  const { t } = useTranslation();
  const groups = useConcurrencyGroups();
  const remove = useDeleteGroup();
  const canManage = useCan('pipeline:manage');
  const canSeeDashboard = useCan('dashboard:read');
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<ConcurrencyGroup | null>(null);

  const columns: Column<ConcurrencyGroup>[] = [
    {
      key: 'group',
      header: t('settings.groups.group'),
      render: (g) => (
        <span>
          <strong>{g.code}</strong>
          {g.description && (
            <>
              <br />
              <span className="sx-muted">{g.description}</span>
            </>
          )}
        </span>
      ),
    },
    {
      key: 'limit',
      header: t('settings.groups.limit'),
      render: (g) => t('settings.groups.atOnce', { count: g.max_parallel }),
    },
    {
      key: 'pipelines',
      header: t('settings.groups.pipelines'),
      render: (g) => g.pipelines,
    },
    { key: 'running', header: t('settings.groups.running'), render: (g) => g.running },
    ...(canManage
      ? [
          {
            key: 'actions',
            header: t('common.actions'),
            render: (g: ConcurrencyGroup) => (
              <span className="sx-row">
                <Button
                  variant="secondary"
                  aria-label={t('settings.groups.editOf', { code: g.code })}
                  onClick={() => {
                    setEditing({ group: g });
                  }}
                >
                  {t('settings.groups.edit')}
                </Button>
                <Button
                  variant="secondary"
                  aria-label={t('settings.groups.deleteOf', { code: g.code })}
                  onClick={() => {
                    setDeleting(g);
                  }}
                >
                  {t('settings.groups.delete')}
                </Button>
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader title={t('settings.title')} description={t('settings.description')} />
      <Card title={t('settings.groups.title')}>
        <div className="sx-stack">
          <p>{t('settings.groups.intro')}</p>
          {groups.error && <Alert tone="danger">{describeError(groups.error).message}</Alert>}
          <DataTable
            caption={t('settings.groups.title')}
            columns={columns}
            rows={groups.data ?? []}
            rowKey={(g) => g.code}
            loading={groups.isPending}
            empty={t('settings.groups.empty')}
            stacked
          />
          {canManage && (
            <div>
              <Button
                onClick={() => {
                  setEditing({ group: null });
                }}
              >
                {t('settings.groups.new')}
              </Button>
            </div>
          )}
          {canSeeDashboard && (
            <p className="sx-muted">
              {t('settings.groups.workersNote')}{' '}
              <Link to="/dashboard">{t('settings.groups.seeDashboard')}</Link>
            </p>
          )}
        </div>
      </Card>

      {editing && (
        <GroupDialog
          group={editing.group}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      <ConfirmDialog
        key={deleting?.code ?? 'none'}
        open={deleting !== null}
        title={deleting ? t('settings.groups.deleteTitle', { code: deleting.code }) : ''}
        message={t('settings.groups.deleteMessage')}
        confirmLabel={t('settings.groups.deleteConfirm')}
        cancelLabel={t('common.cancel')}
        danger
        busy={remove.isPending}
        error={
          remove.error ? (
            <Alert tone="danger">{describeError(remove.error).message}</Alert>
          ) : undefined
        }
        onConfirm={() => {
          if (!deleting) return;
          remove.mutate(deleting.code, {
            onSuccess: () => {
              setDeleting(null);
            },
          });
        }}
        onClose={() => {
          remove.reset();
          setDeleting(null);
        }}
      />
    </>
  );
}
