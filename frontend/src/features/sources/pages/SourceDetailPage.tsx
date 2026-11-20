import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan } from '@/features/auth/api/queries';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  DetailList,
  PageHeader,
  SelectField,
  StatusPill,
  Tabs,
} from '@/shared/ui';
import {
  useActivateSource,
  useDeleteSource,
  useDeprecateSource,
  usePreviewSource,
  useRefreshSchema,
  useSchemaDiff,
  useSchemaVersions,
  useSource,
  type SchemaDiff,
  type SourceDetail,
} from '../api/queries';
import {
  DeltaSummary,
  EnvironmentPill,
  SourceStatusPill,
  SourceTypeLabel,
} from '../components/SourcePills';
import { columnType, displayValue } from '../components/format';
import '../sources.css';

type TabId = 'general' | 'schema' | 'preview' | 'activity';

function GeneralTab({ source }: { source: SourceDetail }) {
  const { t } = useTranslation();
  return (
    <Card>
      <DetailList
        items={[
          {
            label: t('sources.fields.system'),
            value: (
              <span className="sx-row">
                <Link to={`/sap-systems/${source.sap_system.id}`}>{source.sap_system.code}</Link>
                <EnvironmentPill environment={source.sap_system.environment} />
              </span>
            ),
          },
          { label: t('sources.fields.type'), value: <SourceTypeLabel type={source.source_type} /> },
          {
            label: t('sources.fields.odp'),
            value: source.odp_name ? (
              <code>
                {source.odp_name} ({source.odp_context})
              </code>
            ) : (
              '—'
            ),
          },
          {
            label: t('sources.fields.service'),
            value: (
              <code>
                {source.service_path}/{source.entity_set}
              </code>
            ),
          },
          { label: t('sources.fields.odataVersion'), value: source.odata_version },
          {
            label: t('sources.fields.delta'),
            value: (
              <DeltaSummary
                method={source.delta_method}
                semantics={source.delta_semantics}
                watermarkField={source.watermark_field}
              />
            ),
          },
          ...(source.delta_method === 'WATERMARK'
            ? [
                {
                  label: t('sources.fields.watermarkKind'),
                  value: `${t(`sources.watermarkKinds.${source.watermark_kind ?? 'datetime'}`)} · ${
                    source.watermark_timezone ?? ''
                  } · ${t('sources.wizard.overlapShort', {
                    seconds: source.watermark_overlap_seconds ?? 0,
                  })}`,
                },
              ]
            : []),
          {
            label: t('sources.fields.keyFields'),
            value: <code>{source.key_fields.join(', ')}</code>,
          },
          {
            label: t('sources.fields.filters'),
            value: source.filter_expression ? (
              <code className="sx-sources__expression">{source.filter_expression}</code>
            ) : (
              t('sources.wizard.noFiltersShort')
            ),
          },
          { label: t('sources.fields.pageSize'), value: source.page_size },
          {
            label: t('sources.fields.previewEnabled'),
            value: source.preview_enabled ? t('common.yes') : t('common.no'),
          },
          { label: t('sources.fields.tags'), value: source.tags.join(', ') || '—' },
          ...(source.status_reason
            ? [{ label: t('sources.fields.statusReason'), value: source.status_reason }]
            : []),
          {
            label: t('sources.fields.updatedAt'),
            value: `${formatDateTime(source.updated_at)} · ${source.updated_by ?? '—'}`,
          },
        ]}
      />
    </Card>
  );
}

function DiffView({ diff }: { diff: SchemaDiff }) {
  const { t } = useTranslation();
  if (!diff.added.length && !diff.removed.length && !diff.changed.length) {
    return <p className="sx-muted">{t('sources.schema.noDifferences')}</p>;
  }
  return (
    <div className="sx-stack">
      <Alert tone={diff.breaking ? 'danger' : 'info'}>
        {diff.breaking ? t('sources.schema.breaking') : t('sources.schema.compatible')}
      </Alert>
      {diff.added.length > 0 && (
        <p>
          <strong>{t('sources.schema.added')}</strong>{' '}
          {diff.added.map((c) => (
            <code key={c.name}>{c.name} </code>
          ))}
        </p>
      )}
      {diff.removed.length > 0 && (
        <p>
          <strong>{t('sources.schema.removed')}</strong>{' '}
          {diff.removed.map((c) => (
            <code key={c.name}>{c.name} </code>
          ))}
        </p>
      )}
      {diff.changed.length > 0 && (
        <DataTable
          caption={t('sources.schema.changed')}
          rows={diff.changed}
          rowKey={(c) => c.name}
          columns={[
            {
              key: 'name',
              header: t('sources.wizard.column'),
              render: (c) => <code>{c.name}</code>,
            },
            {
              key: 'what',
              header: t('sources.schema.change'),
              render: (c) => (
                <span className="sx-diff">
                  {Object.keys(c.after)
                    .filter((k) => c.before[k] !== c.after[k])
                    .map((k) => (
                      <span key={k}>
                        {k}: <span className="sx-diff__before">{displayValue(c.before[k])}</span> →{' '}
                        <span className="sx-diff__after">{displayValue(c.after[k])}</span>
                      </span>
                    ))}
                </span>
              ),
            },
            {
              key: 'impact',
              header: t('sources.schema.impact'),
              render: (c) =>
                c.breaking ? (
                  <StatusPill tone="danger">{t('sources.schema.breakingShort')}</StatusPill>
                ) : (
                  <StatusPill tone="success">{t('sources.schema.compatibleShort')}</StatusPill>
                ),
            },
          ]}
        />
      )}
    </div>
  );
}

function SchemaTab({ source, canManage }: { source: SourceDetail; canManage: boolean }) {
  const { t } = useTranslation();
  const versions = useSchemaVersions(source.id);
  const refresh = useRefreshSchema(source.id);
  const latest = source.schema.version;
  const [from, setFrom] = useState<number | null>(latest > 1 ? latest - 1 : null);
  const diff = useSchemaDiff(source.id, from, latest);
  const result = refresh.data;

  return (
    <div className="sx-stack">
      <Card title={t('sources.schema.title', { version: latest })}>
        <div className="sx-stack">
          <p className="sx-muted">
            {t('sources.schema.checkedAt', { when: formatDateTime(source.schema_checked_at) })}
          </p>
          {canManage && (
            <div>
              <Button
                variant="secondary"
                disabled={refresh.isPending}
                onClick={() => {
                  refresh.mutate();
                }}
              >
                {refresh.isPending ? t('sources.schema.checking') : t('sources.schema.check')}
              </Button>
            </div>
          )}
          {refresh.error && <Alert tone="danger">{describeError(refresh.error).message}</Alert>}
          {result && !result.changed && (
            <Alert tone="success">{t('sources.schema.unchanged')}</Alert>
          )}
          {result?.changed && (
            <>
              <Alert tone="warning">
                {t('sources.schema.newVersion', { version: result.diff.to_version })}
              </Alert>
              <DiffView diff={result.diff} />
            </>
          )}
          <DataTable
            caption={t('sources.wizard.columnsCaption')}
            rows={source.columns}
            rowKey={(c) => c.name}
            columns={[
              {
                key: 'name',
                header: t('sources.wizard.column'),
                render: (c) => (
                  <span>
                    <code>{c.name}</code>
                    {source.key_fields.includes(c.name) && (
                      <>
                        {' '}
                        <StatusPill tone="info">{t('sources.wizard.key')}</StatusPill>
                      </>
                    )}
                    <br />
                    <span className="sx-muted">{c.label ?? '—'}</span>
                  </span>
                ),
              },
              {
                key: 'type',
                header: t('sources.wizard.sapType'),
                render: (c) => <code>{columnType(c)}</code>,
              },
              {
                key: 'arrow',
                header: t('sources.schema.arrowType'),
                render: (c) => <code>{c.arrow_type}</code>,
              },
              {
                key: 'filterable',
                header: t('sources.wizard.filterable'),
                render: (c) => (c.filterable ? t('common.yes') : t('common.no')),
              },
            ]}
          />
        </div>
      </Card>
      {(versions.data?.length ?? 0) > 1 && (
        <Card title={t('sources.schema.history')}>
          <div className="sx-stack">
            <SelectField
              label={t('sources.schema.compareWith', { version: latest })}
              value={from === null ? '' : String(from)}
              onChange={(e) => {
                setFrom(e.target.value ? Number(e.target.value) : null);
              }}
              options={(versions.data ?? [])
                .filter((v) => v.version !== latest)
                .map((v) => ({
                  value: String(v.version),
                  label: t('sources.schema.versionOption', {
                    version: v.version,
                    when: formatDateTime(v.detected_at),
                  }),
                }))}
            />
            {diff.error && <Alert tone="danger">{describeError(diff.error).message}</Alert>}
            {diff.data && <DiffView diff={diff.data} />}
          </div>
        </Card>
      )}
    </div>
  );
}

function PreviewTab({ source }: { source: SourceDetail }) {
  const { t } = useTranslation();
  const canPreview = useCan('source:preview');
  const preview = usePreviewSource(source.id);
  const data = preview.data;
  if (!canPreview) return <Alert tone="info">{t('sources.preview.noPermission')}</Alert>;
  if (!source.preview_enabled) return <Alert tone="info">{t('sources.preview.disabled')}</Alert>;
  return (
    <Card>
      <div className="sx-stack sx-sources__preview">
        <p>{t('sources.preview.intro')}</p>
        <div>
          <Button
            disabled={preview.isPending}
            onClick={() => {
              preview.mutate();
            }}
          >
            {preview.isPending ? t('sources.preview.reading') : t('sources.preview.read')}
          </Button>
        </div>
        {preview.error && <Alert tone="danger">{describeError(preview.error).message}</Alert>}
        {data && (
          <>
            <p className="sx-muted">
              {t('sources.preview.summary', {
                rows: data.rows.length,
                ms: data.elapsed_ms,
              })}
              {data.count !== null && ` ${t('sources.preview.count', { count: data.count })}`}
              {data.truncated && ` ${t('sources.preview.truncated')}`}
            </p>
            <DataTable
              caption={t('sources.preview.caption', { code: source.code })}
              rows={data.rows.map((row, i) => ({ row, i }))}
              rowKey={(r) => String(r.i)}
              empty={t('sources.preview.empty')}
              columns={data.columns.map((name) => ({
                key: name,
                header: <code>{name}</code>,
                render: (r: { row: Record<string, unknown> }) => displayValue(r.row[name]),
              }))}
            />
          </>
        )}
      </div>
    </Card>
  );
}

function Detail({ source }: { source: SourceDetail }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const canManage = useCan('source:manage');
  const [tab, setTab] = useState<TabId>('general');
  const [dialog, setDialog] = useState<'deprecate' | 'delete' | null>(null);
  const activate = useActivateSource(source.id);
  const deprecate = useDeprecateSource(source.id);
  const remove = useDeleteSource(source.id);
  const justSaved = (location.state as { saved?: boolean } | null)?.saved === true;
  const actionError = activate.error ?? deprecate.error;

  const actions = canManage && (
    <>
      {source.status !== 'active' && (
        <Button
          disabled={activate.isPending}
          onClick={() => {
            activate.mutate();
          }}
        >
          {t('sources.actions.activate')}
        </Button>
      )}
      <Button variant="secondary" onClick={() => void navigate(`/sources/${source.id}/edit`)}>
        {t('sources.actions.edit')}
      </Button>
      {source.status === 'active' && (
        <Button
          variant="secondary"
          onClick={() => {
            setDialog('deprecate');
          }}
        >
          {t('sources.actions.deprecate')}
        </Button>
      )}
      <Button
        variant="danger"
        onClick={() => {
          setDialog('delete');
        }}
      >
        {t('sources.actions.delete')}
      </Button>
    </>
  );

  return (
    <>
      <PageHeader
        title={
          <span className="sx-row">
            <span>{source.code}</span>
            <SourceStatusPill status={source.status} />
          </span>
        }
        description={source.name}
        actions={actions}
      />
      {justSaved && source.status === 'draft' && (
        <Alert tone="success">{t('sources.savedDraft')}</Alert>
      )}
      {actionError && <Alert tone="danger">{describeError(actionError).message}</Alert>}
      <Tabs
        label={t('sources.tabs.label')}
        active={tab}
        onChange={(id) => {
          setTab(id as TabId);
        }}
        tabs={(['general', 'schema', 'preview', 'activity'] as const).map((id) => ({
          id,
          label: t(`sources.tabs.${id}`),
        }))}
      />
      <div className="sx-mt">
        {tab === 'general' && <GeneralTab source={source} />}
        {tab === 'schema' && <SchemaTab source={source} canManage={canManage} />}
        {tab === 'preview' && <PreviewTab source={source} />}
        {tab === 'activity' && <AuditTrail resourceId={source.id} />}
      </div>
      <ConfirmDialog
        open={dialog === 'deprecate'}
        title={t('sources.dialogs.deprecateTitle', { code: source.code })}
        message={t('sources.dialogs.deprecateMessage')}
        confirmLabel={t('sources.actions.deprecate')}
        cancelLabel={t('common.cancel')}
        reasonLabel={t('sources.dialogs.reason')}
        busy={deprecate.isPending}
        error={deprecate.error ? describeError(deprecate.error).message : undefined}
        onConfirm={(reason) => {
          deprecate.mutate(reason, {
            onSuccess: () => {
              setDialog(null);
            },
          });
        }}
        onClose={() => {
          setDialog(null);
        }}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        danger
        title={t('sources.dialogs.deleteTitle', { code: source.code })}
        message={t('sources.dialogs.deleteMessage')}
        confirmLabel={t('sources.actions.delete')}
        cancelLabel={t('common.cancel')}
        reasonLabel={t('sources.dialogs.reason')}
        reasonRequired
        confirmText={source.code}
        confirmTextLabel={t('sources.dialogs.typeCode', { code: source.code })}
        busy={remove.isPending}
        error={remove.error ? describeError(remove.error).message : undefined}
        onConfirm={(reason) => {
          remove.mutate(reason, {
            onSuccess: () => void navigate('/sources'),
          });
        }}
        onClose={() => {
          setDialog(null);
        }}
      />
    </>
  );
}

export function SourceDetailPage() {
  const { t } = useTranslation();
  const { sourceId = '' } = useParams();
  const source = useSource(sourceId);
  if (source.error) return <Alert tone="danger">{describeError(source.error).message}</Alert>;
  if (!source.data) return <p className="sx-muted">{t('common.loading')}</p>;
  return <Detail source={source.data} />;
}
