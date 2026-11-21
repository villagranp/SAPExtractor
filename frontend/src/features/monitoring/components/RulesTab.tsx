import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Checkbox,
  ConfirmDialog,
  DataTable,
  Dialog,
  SelectField,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  useDeleteRule,
  useRules,
  useRuleTypes,
  useSaveRule,
  type AlertType,
  type Rule,
  type RuleType,
  type Severity,
} from '../api/queries';
import { SeverityPill } from './MonitoringParts';

const SEVERITIES: Severity[] = ['critical', 'warning', 'info'];
type Scope = 'global' | 'pipeline' | 'tag';

interface Draft {
  id: string | null;
  name: string;
  type: AlertType;
  scope: Scope;
  scopeValue: string;
  params: Record<string, string>;
  severity: Severity;
  enabled: boolean;
}

function draftOf(rule: Rule | null, type: RuleType): Draft {
  return {
    id: rule?.id ?? null,
    name: rule?.name ?? '',
    type: rule?.type ?? type.type,
    scope: rule?.scope ?? 'global',
    scopeValue: rule?.scope_value ?? '',
    params: Object.fromEntries(
      type.params.map((p) => [
        p.name,
        String((rule?.params[p.name] as number | undefined) ?? p.default),
      ]),
    ),
    severity: rule?.severity ?? type.default_severity,
    enabled: rule?.enabled ?? true,
  };
}

function RuleDialog({
  draft,
  types,
  onClose,
}: {
  draft: Draft;
  types: RuleType[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const save = useSaveRule();
  const [value, setValue] = useState(draft);
  const type = types.find((x) => x.type === value.type) ?? types[0];
  const fields = save.error ? describeError(save.error).fields : {};
  const field = (name: string) => fields[name]?.[0];
  if (!type) return null;

  const changeType = (next: AlertType) => {
    const spec = types.find((x) => x.type === next);
    if (!spec) return;
    setValue({
      ...draftOf(null, spec),
      id: value.id,
      name: value.name,
      enabled: value.enabled,
      scope: spec.pipeline_scope ? value.scope : 'global',
      scopeValue: spec.pipeline_scope ? value.scopeValue : '',
    });
  };

  const submit = () => {
    save.mutate(
      {
        id: value.id,
        body: {
          name: value.name,
          type: value.type,
          scope: value.scope,
          scope_value: value.scope === 'global' ? null : value.scopeValue,
          params: Object.fromEntries(
            Object.entries(value.params).map(([k, v]) => [k, Number(v.replace(',', '.'))]),
          ),
          severity: value.severity,
          enabled: value.enabled,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog
      open
      title={value.id ? t('monitoring.rules.editTitle') : t('monitoring.rules.newTitle')}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            {t('monitoring.rules.save')}
          </Button>
        </>
      }
    >
      <div className="sx-stack">
        {save.error && Object.keys(fields).length === 0 && (
          <Alert tone="danger">{describeError(save.error).message}</Alert>
        )}
        <TextField
          label={t('monitoring.rules.name')}
          value={value.name}
          error={field('name')}
          onChange={(e) => {
            setValue({ ...value, name: e.target.value });
          }}
        />
        <SelectField
          label={t('monitoring.rules.type')}
          value={value.type}
          hint={t(`monitoring.ruleTypes.${value.type}.description`)}
          onChange={(e) => {
            changeType(e.target.value as AlertType);
          }}
          options={types.map((x) => ({
            value: x.type,
            label: t(`monitoring.ruleTypes.${x.type}.label`),
          }))}
        />
        {type.pipeline_scope && (
          <div className="sx-two-columns">
            <SelectField
              label={t('monitoring.rules.scope')}
              value={value.scope}
              error={field('scope')}
              onChange={(e) => {
                setValue({ ...value, scope: e.target.value as Scope });
              }}
              options={(['global', 'tag', 'pipeline'] as const).map((s) => ({
                value: s,
                label: t(`monitoring.scopes.${s}`),
              }))}
            />
            {value.scope !== 'global' && (
              <TextField
                label={
                  value.scope === 'tag'
                    ? t('monitoring.rules.tag')
                    : t('monitoring.rules.pipelineCode')
                }
                value={value.scopeValue}
                error={field('scope_value')}
                onChange={(e) => {
                  setValue({ ...value, scopeValue: e.target.value });
                }}
              />
            )}
          </div>
        )}
        {type.params.length > 0 && (
          <div className="sx-two-columns">
            {type.params.map((p) => (
              <TextField
                key={p.name}
                label={t(`monitoring.params.${p.name}`)}
                inputMode="decimal"
                hint={t('monitoring.rules.range', { min: p.minimum, max: p.maximum })}
                value={value.params[p.name] ?? ''}
                error={field(`params.${p.name}`)}
                onChange={(e) => {
                  setValue({ ...value, params: { ...value.params, [p.name]: e.target.value } });
                }}
              />
            ))}
          </div>
        )}
        <SelectField
          label={t('monitoring.alerts.severity')}
          value={value.severity}
          onChange={(e) => {
            setValue({ ...value, severity: e.target.value as Severity });
          }}
          options={SEVERITIES.map((s) => ({ value: s, label: t(`monitoring.severities.${s}`) }))}
        />
        <Checkbox
          label={t('monitoring.rules.enabled')}
          checked={value.enabled}
          onChange={(e) => {
            setValue({ ...value, enabled: e.target.checked });
          }}
        />
      </div>
    </Dialog>
  );
}

export function RulesTab() {
  const { t } = useTranslation();
  const canManage = useCan('alert:manage');
  const rules = useRules();
  const types = useRuleTypes();
  const remove = useDeleteRule();
  const [editing, setEditing] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const typeList = types.data ?? [];

  const open = (rule: Rule | null) => {
    const spec = typeList.find((x) => x.type === (rule?.type ?? 'freshness')) ?? typeList[0];
    if (spec) setEditing(draftOf(rule, spec));
  };

  const columns: Column<Rule>[] = [
    {
      key: 'name',
      header: t('monitoring.rules.name'),
      render: (r) => (
        <span className="sx-stack" style={{ gap: 'var(--sx-space-1)' }}>
          <strong>{r.name}</strong>
          <span className="sx-muted">{t(`monitoring.ruleTypes.${r.type}.label`)}</span>
        </span>
      ),
    },
    {
      key: 'scope',
      header: t('monitoring.rules.scope'),
      render: (r) =>
        r.scope === 'global'
          ? t('monitoring.scopes.global')
          : `${t(`monitoring.scopes.${r.scope}`)}: ${r.scope_value ?? ''}`,
    },
    {
      key: 'params',
      header: t('monitoring.rules.params'),
      render: (r) =>
        Object.entries(r.params)
          .map(([k, v]) => `${t(`monitoring.params.${k}`)}: ${String(v)}`)
          .join(', ') || '—',
    },
    {
      key: 'severity',
      header: t('monitoring.alerts.severity'),
      render: (r) => <SeverityPill severity={r.severity} />,
    },
    {
      key: 'enabled',
      header: t('monitoring.rules.enabled'),
      render: (r) => (r.enabled ? t('common.yes') : t('common.no')),
    },
    ...(canManage
      ? [
          {
            key: 'actions',
            header: t('common.actions'),
            render: (r: Rule) => (
              <span className="sx-row">
                <Button
                  variant="secondary"
                  onClick={() => {
                    open(r);
                  }}
                >
                  {t('monitoring.rules.edit')}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setDeleting(r);
                  }}
                >
                  {t('common.delete')}
                </Button>
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="sx-stack">
      {canManage && (
        <div>
          <Button
            onClick={() => {
              open(null);
            }}
            disabled={typeList.length === 0}
          >
            {t('monitoring.rules.new')}
          </Button>
        </div>
      )}
      {rules.error && <Alert tone="danger">{describeError(rules.error).message}</Alert>}
      <DataTable
        caption={t('monitoring.alerts.tabs.rules')}
        columns={columns}
        rows={rules.data ?? []}
        rowKey={(r) => r.id}
        loading={rules.isPending}
        empty={t('monitoring.rules.empty')}
      />
      {editing && (
        <RuleDialog
          draft={editing}
          types={typeList}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      <ConfirmDialog
        key={deleting?.id ?? 'none'}
        open={deleting !== null}
        title={t('monitoring.rules.deleteTitle', { name: deleting?.name ?? '' })}
        message={t('monitoring.rules.deleteMessage')}
        confirmLabel={t('common.delete')}
        cancelLabel={t('common.cancel')}
        danger
        busy={remove.isPending}
        onConfirm={() => {
          if (deleting)
            remove.mutate(deleting.id, {
              onSuccess: () => {
                setDeleting(null);
              },
            });
        }}
        onClose={() => {
          setDeleting(null);
        }}
      />
    </div>
  );
}
