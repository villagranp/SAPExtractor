import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan } from '@/features/auth/api/queries';
import {
  useDeltaState,
  useRequestRun,
  useResetDelta,
  useResolveSubscription,
  useRuns,
  type DeltaState,
} from '@/features/runs/api/queries';
import { DeltaStatusPill, RunsTable } from '@/features/runs/components/RunParts';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DetailList,
  PageHeader,
  Pagination,
  Spinner,
  Tabs,
} from '@/shared/ui';
import { useDeletePipeline, usePipeline, type PipelineDetail } from '../api/queries';
import '@/features/runs/runs.css';

type Tab = 'runs' | 'delta' | 'activity';
type Pending = 'reset' | 'closed' | 'left_open' | 'delete' | null;
const PAGE_SIZE = 20;

function RunsTab({ pipeline }: { pipeline: PipelineDetail }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const runs = useRuns({ pipeline_id: pipeline.id, page, page_size: PAGE_SIZE });
  return (
    <>
      {runs.error && <Alert tone="danger">{describeError(runs.error).message}</Alert>}
      <RunsTable
        runs={runs.data?.items ?? []}
        loading={runs.isPending}
        showPipeline={false}
        empty={t('pipelines.detail.noRuns')}
      />
      {runs.data && runs.data.total > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={runs.data.total}
          onPageChange={setPage}
          labels={{
            previous: t('common.previous'),
            next: t('common.next'),
            summary: t('common.pageSummary'),
          }}
        />
      )}
    </>
  );
}

function DeltaTab({
  state,
  canReset,
  onAction,
}: {
  state: DeltaState;
  canReset: boolean;
  onAction: (action: Pending) => void;
}) {
  const { t } = useTranslation();
  const resettable = state.status === 'INITIALIZED' || state.status === 'BROKEN';
  return (
    <div className="sx-two-columns" style={{ alignItems: 'start' }}>
      <Card title={t('pipelines.detail.deltaTitle')}>
        {state.sap_close_pending && (
          <Alert tone="warning" title={t('pipelines.detail.closePendingTitle')}>
            {t('pipelines.detail.closePending')}
          </Alert>
        )}
        {state.requires_reinit && (
          <Alert tone="warning">{t('pipelines.detail.filtersChanged')}</Alert>
        )}
        <DetailList
          items={[
            {
              label: t('pipelines.fields.deltaStatus'),
              value: <DeltaStatusPill status={state.status} />,
            },
            { label: t('runs.fields.generation'), value: String(state.generation) },
            {
              label: t('pipelines.fields.currentToken'),
              value: state.current_token ? (
                <span className="sx-run__token">{state.current_token}</span>
              ) : null,
            },
            ...(typeof state.watermark_value?.end === 'string'
              ? [{ label: t('pipelines.fields.watermark'), value: state.watermark_value.end }]
              : []),
            {
              label: t('pipelines.fields.initializedAt'),
              value: formatDateTime(state.initialized_at),
            },
            { label: t('pipelines.fields.lastDelta'), value: formatDateTime(state.last_delta_at) },
          ]}
        />
        {canReset && (
          <div className="sx-row" style={{ marginTop: 'var(--sx-space-4)' }}>
            {resettable && (
              <Button
                variant="danger"
                onClick={() => {
                  onAction('reset');
                }}
              >
                {t('pipelines.detail.actions.reset')}
              </Button>
            )}
            {state.sap_close_pending && (
              <>
                <Button
                  onClick={() => {
                    onAction('closed');
                  }}
                >
                  {t('pipelines.detail.actions.closed')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    onAction('left_open');
                  }}
                >
                  {t('pipelines.detail.actions.leftOpen')}
                </Button>
              </>
            )}
          </div>
        )}
      </Card>
      <Card title={t('pipelines.detail.historyTitle')}>
        {state.history.length === 0 ? (
          <p className="sx-muted">{t('pipelines.detail.noHistory')}</p>
        ) : (
          <ol className="sx-stack" style={{ margin: 0, paddingInlineStart: 'var(--sx-space-4)' }}>
            {state.history.map((h) => (
              <li key={`${h.at}-${h.event}`}>
                <strong>{t(`pipelines.deltaEvents.${h.event}`, { defaultValue: h.event })}</strong>
                <br />
                <span className="sx-muted">
                  {t('pipelines.detail.historyLine', {
                    when: formatDateTime(h.at),
                    who: h.actor ?? '—',
                    generation: h.generation,
                  })}
                </span>
                {h.reason && <p style={{ margin: 0 }}>{h.reason}</p>}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

export function PipelineDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pipelineId = '' } = useParams();
  const pipeline = usePipeline(pipelineId);
  const isDelta = pipeline.data?.extraction_mode !== 'FULL';
  const delta = useDeltaState(pipelineId, pipeline.data !== undefined && isDelta);
  const runNow = useRequestRun(pipelineId);
  const reset = useResetDelta(pipelineId);
  const resolve = useResolveSubscription(pipelineId);
  const remove = useDeletePipeline(pipelineId);
  const canRun = useCan('pipeline:run');
  const canReset = useCan('pipeline:reset_delta');
  const canManage = useCan('pipeline:manage');
  const [tab, setTab] = useState<Tab>('runs');
  const [pending, setPending] = useState<Pending>(null);

  if (pipeline.isPending) return <Spinner label={t('common.loading')} />;
  if (pipeline.error) return <Alert tone="danger">{describeError(pipeline.error).message}</Alert>;
  const p = pipeline.data;
  const tabs: Tab[] = isDelta ? ['runs', 'delta', 'activity'] : ['runs', 'activity'];
  const busy = reset.isPending || resolve.isPending || remove.isPending;
  const actionError = reset.error ?? resolve.error ?? remove.error;

  const confirm = (reason: string) => {
    const done = {
      onSuccess: () => {
        setPending(null);
      },
    };
    if (pending === 'reset') reset.mutate(reason, done);
    if (pending === 'closed') resolve.mutate({ action: 'closed', reason: reason || null }, done);
    if (pending === 'left_open') resolve.mutate({ action: 'left_open', reason }, done);
    if (pending === 'delete') {
      remove.mutate(reason, { onSuccess: () => void navigate('/pipelines') });
    }
  };

  return (
    <>
      <PageHeader
        eyebrow={t(`pipelines.modes.${p.extraction_mode}`)}
        title={p.code}
        description={t('pipelines.detail.flow', {
          source: p.source.code,
          destination: p.destination.code,
          target: String(p.load_options.target_name),
        })}
        actions={
          <div className="sx-row">
            <Link to="/pipelines">{t('pipelines.detail.back')}</Link>
            {canManage && (
              <Button
                variant="secondary"
                onClick={() => {
                  setPending('delete');
                }}
              >
                {t('pipelines.detail.actions.delete')}
              </Button>
            )}
            {canRun && (
              <Button
                disabled={runNow.isPending}
                onClick={() => {
                  runNow.mutate(undefined, {
                    onSuccess: (run) => void navigate(`/runs/${run.id}`),
                  });
                }}
              >
                {runNow.isPending ? t('pipelines.detail.queueing') : t('pipelines.detail.runNow')}
              </Button>
            )}
          </div>
        }
      />
      {runNow.error && <Alert tone="danger">{describeError(runNow.error).message}</Alert>}
      <Card title={t('pipelines.detail.configTitle')}>
        <DetailList
          items={[
            { label: t('pipelines.fields.name'), value: p.name },
            { label: t('pipelines.fields.description'), value: p.description },
            {
              label: t('pipelines.fields.source'),
              value: <Link to={`/sources/${p.source.id}`}>{p.source.code}</Link>,
            },
            {
              label: t('pipelines.fields.destination'),
              value: <Link to={`/destinations/${p.destination.id}`}>{p.destination.code}</Link>,
            },
            {
              label: t('pipelines.fields.loadMode'),
              value: t(`destinations.loadModes.${String(p.load_options.load_mode)}`),
            },
            {
              label: t('pipelines.fields.target'),
              value: <code>{String(p.load_options.target_name)}</code>,
            },
            {
              label: t('pipelines.fields.attempts'),
              value: t('pipelines.detail.attemptsValue', {
                attempts: p.max_attempts,
                minutes: p.timeout_minutes,
              }),
            },
          ]}
        />
      </Card>
      <Tabs
        label={t('pipelines.detail.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={tabs.map((id) => ({ id, label: t(`pipelines.detail.tabs.${id}`) }))}
      />
      {tab === 'runs' && <RunsTab pipeline={p} />}
      {tab === 'delta' &&
        (delta.data ? (
          <DeltaTab state={delta.data} canReset={canReset} onAction={setPending} />
        ) : delta.error ? (
          <Alert tone="danger">{describeError(delta.error).message}</Alert>
        ) : (
          <Spinner label={t('common.loading')} />
        ))}
      {tab === 'activity' && <AuditTrail resourceId={p.id} />}

      <ConfirmDialog
        key={pending ?? 'none'}
        open={pending !== null}
        title={pending ? t(`pipelines.confirm.${pending}.title`, { code: p.code }) : ''}
        message={pending ? t(`pipelines.confirm.${pending}.message`) : ''}
        confirmLabel={pending ? t(`pipelines.confirm.${pending}.confirm`) : ''}
        cancelLabel={t('common.cancel')}
        danger={pending === 'reset' || pending === 'delete'}
        busy={busy}
        {...(pending !== null ? { reasonLabel: t('pipelines.confirm.reason') } : {})}
        reasonRequired={pending === 'reset' || pending === 'left_open'}
        {...(pending === 'reset' || pending === 'delete'
          ? {
              confirmText: p.code,
              confirmTextLabel: t('pipelines.confirm.typeCode', { code: p.code }),
            }
          : {})}
        error={
          actionError ? (
            <Alert tone="danger">{describeError(actionError).message}</Alert>
          ) : undefined
        }
        onConfirm={confirm}
        onClose={() => {
          reset.reset();
          resolve.reset();
          remove.reset();
          setPending(null);
        }}
      />
    </>
  );
}
