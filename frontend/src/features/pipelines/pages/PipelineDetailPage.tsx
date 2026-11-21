import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { AuditTrail } from '@/features/audit/components/AuditTrail';
import { useCan } from '@/features/auth/api/queries';
import { useRuns } from '@/features/runs/api/queries';
import { RunStatusPill } from '@/features/runs/components/RunParts';
import { IdleWorkersNotice, RunsTable } from '@/features/runs/components/RunsTable';
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
  StatusPill,
  Tabs,
} from '@/shared/ui';
import {
  usePipeline,
  usePipelineStatus,
  usePutSchedules,
  useSchedules,
  type CloneSchemaDiff,
  type PipelineDetail,
  type ScheduleResponse,
  type StatusAction,
} from '../api/queries';
import { DeltaTab } from '../components/DeltaTab';
import { ActivateDialog, CloneDialog, RunNowDialog } from '../components/PipelineDialogs';
import { TrialDialog } from '../components/TrialDialog';
import { FindingsList, PipelineStatusPill } from '../components/PipelinePills';
import { RouteStrip } from '../components/RouteStrip';
import { SchedulesEditor } from '../components/ScheduleBuilder';
import { cronOf, fireFormatter, mappingText, useScheduleText } from '../components/scheduleText';
import {
  fromSchedules,
  schedulesValid,
  toScheduleBody,
  type ScheduleDraft,
} from '../schemas/schedule';
import '../pipelines.css';

type Tab = 'summary' | 'runs' | 'delta' | 'schedule' | 'activity';
type Opened = 'run' | 'activate' | 'clone' | 'trial' | null;
const RUNS_PAGE_SIZE = 10;
/** Con el motivo confirmado, el cruce de entornos ya se explica en su propio aviso. */
const CROSS_ENVIRONMENT = 'SX-PIPE-CROSS-ENVIRONMENT';

interface DetailState {
  saved?: boolean;
  scheduleError?: string;
  clone?: { sourceCreated: boolean; schemaDiff: CloneSchemaDiff | null };
}

// ------------------------------------------------------------------ resumen
function SummaryTab({ pipeline: p }: { pipeline: PipelineDetail }) {
  const { t, i18n } = useTranslation();
  const mapping = Object.values(p.load.column_mapping);
  const excluded = mapping.filter((target) => target === null).length;
  const renamed = mapping.length - excluded;
  const list = new Intl.ListFormat(i18n.resolvedLanguage ?? 'es', { type: 'conjunction' });
  return (
    <div className="sx-two-columns" style={{ alignItems: 'start' }}>
      <Card title={t('pipelines.detail.loadTitle')}>
        <DetailList
          items={[
            { label: t('pipelines.fields.name'), value: p.name },
            { label: t('pipelines.fields.description'), value: p.description },
            {
              label: t('pipelines.fields.extractionMode'),
              value: t(`pipelines.extractionModes.${p.extraction_mode}`),
            },
            {
              label: t('pipelines.fields.loadMode'),
              value: t(`pipelines.loadModes.${p.load.load_mode}`),
            },
            { label: t('pipelines.fields.targetName'), value: <code>{p.load.target_name}</code> },
            {
              label: t('pipelines.fields.schemaEvolution'),
              value: t(`pipelines.schemaEvolutions.${p.load.schema_evolution}`),
            },
            {
              label: t('pipelines.fields.deleteHandling'),
              value: t(`pipelines.deleteHandlings.${p.load.delete_handling}`),
            },
            {
              label: t('pipelines.fields.columns'),
              value: mappingText(excluded, renamed, t, i18n.resolvedLanguage ?? 'es'),
            },
            {
              label: t('pipelines.fields.filters'),
              value:
                p.filters === null
                  ? t('pipelines.wizard.sourceFiltersShort')
                  : p.filters.length === 0
                    ? t('pipelines.detail.noFilters')
                    : list.format(
                        p.filters.map(
                          (f) => `${f.field} ${t(`sources.operators.${f.op}`)} ${String(f.value)}`,
                        ),
                      ),
            },
            { label: t('pipelines.fields.tags'), value: p.tags.join(', ') || null },
          ]}
        />
      </Card>
      <Card title={t('pipelines.detail.executionTitle')}>
        <DetailList
          items={[
            {
              label: t('pipelines.fields.priority'),
              value: t(`pipelines.priorities.${p.priority}`),
            },
            {
              label: t('pipelines.fields.retries'),
              value: t('pipelines.wizard.retriesSummary', {
                count: p.retry_policy.max_attempts,
                timeout: p.timeout_minutes,
              }),
            },
            { label: t('pipelines.fields.workerPool'), value: <code>{p.worker_pool}</code> },
            { label: t('pipelines.fields.concurrencyGroup'), value: p.concurrency_group },
            {
              label: t('pipelines.fields.logLevel'),
              value: t(`pipelines.logLevels.${p.log_level}`),
            },
            {
              label: t('pipelines.fields.nextFire'),
              value: p.status === 'active' ? formatDateTime(p.next_fire_at) : null,
            },
            {
              label: t('pipelines.fields.lastRun'),
              value: p.last_run ? (
                <span className="sx-row">
                  <RunStatusPill status={p.last_run.status} />
                  <Link to={`/runs/${p.last_run.id}`}>
                    {formatDateTime(p.last_run.finished_at ?? p.last_run.created_at)}
                  </Link>
                </span>
              ) : (
                t('pipelines.neverRun')
              ),
            },
            { label: t('pipelines.detail.activated'), value: formatDateTime(p.activated_at) },
            {
              label: t('pipelines.detail.updated'),
              value: t('pipelines.detail.updatedBy', {
                date: formatDateTime(p.updated_at),
                user: p.updated_by ?? '—',
              }),
            },
          ]}
        />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ ejecuciones
function RunsTab({ pipeline }: { pipeline: PipelineDetail }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const runs = useRuns({ pipeline: pipeline.id, page, page_size: RUNS_PAGE_SIZE });
  return (
    <>
      {runs.error && <Alert tone="danger">{describeError(runs.error).message}</Alert>}
      <IdleWorkersNotice queued={pipeline.live_run?.status === 'queued'} />
      <RunsTable
        runs={runs.data?.items ?? []}
        loading={runs.isPending}
        caption={t('pipelines.detail.tabs.runs')}
        empty={t('pipelines.detail.noRuns')}
        showPipeline={false}
      />
      {runs.data && runs.data.total > RUNS_PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={RUNS_PAGE_SIZE}
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

// ------------------------------------------------------------------ programación
function ScheduleRow({ schedule, draft }: { schedule: ScheduleResponse; draft: ScheduleDraft }) {
  const { t } = useTranslation();
  const { describe, language } = useScheduleText();
  const cron = cronOf(draft);
  const format = fireFormatter(language, schedule.timezone);
  const inZone = (value: string | null) => (value ? format.format(new Date(value)) : null);
  return (
    <li className="sx-pipelines__schedule-row">
      <div>
        <strong>{describe(draft)}</strong>
        {cron && (
          <div className="sx-muted">
            <code>{cron}</code>
          </div>
        )}
        <div className="sx-muted">
          {t('pipelines.schedule.previewZone', { zone: schedule.timezone })}
        </div>
        {draft.blackouts.length > 0 && (
          <div className="sx-muted">
            {t('pipelines.schedule.blackoutCount', { count: draft.blackouts.length })}
          </div>
        )}
      </div>
      <StatusPill tone={schedule.enabled ? 'success' : 'neutral'}>
        {schedule.enabled ? t('pipelines.schedule.on') : t('pipelines.schedule.off')}
      </StatusPill>
      <DetailList
        items={[
          { label: t('pipelines.fields.nextFire'), value: inZone(schedule.next_fire_at) },
          { label: t('pipelines.schedule.lastFire'), value: inZone(schedule.last_fire_at) },
          ...(schedule.last_skip_at && schedule.last_skip_reason
            ? [
                {
                  label: t('pipelines.schedule.lastSkip'),
                  value: t(`pipelines.schedule.skipReasons.${schedule.last_skip_reason}`, {
                    date: inZone(schedule.last_skip_at),
                  }),
                },
              ]
            : []),
        ]}
      />
    </li>
  );
}

function ScheduleTab({ pipeline, canManage }: { pipeline: PipelineDetail; canManage: boolean }) {
  const { t } = useTranslation();
  const schedules = useSchedules(pipeline.id);
  const save = usePutSchedules();
  const [drafts, setDrafts] = useState<ScheduleDraft[] | null>(null);

  if (schedules.isPending) return <Spinner label={t('common.loading')} />;
  if (schedules.error) {
    return <Alert tone="danger">{describeError(schedules.error).message}</Alert>;
  }
  const stored = schedules.data.filter((s) => s.type !== 'manual_only');
  const storedDrafts = fromSchedules(stored);

  if (drafts !== null) {
    return (
      <Card title={t('pipelines.detail.editSchedule')}>
        <div className="sx-stack">
          {save.error && <Alert tone="danger">{describeError(save.error).message}</Alert>}
          <SchedulesEditor drafts={drafts} onChange={setDrafts} />
          <div className="sx-row">
            <Button
              variant="secondary"
              disabled={save.isPending}
              onClick={() => {
                save.reset();
                setDrafts(null);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              disabled={save.isPending || !schedulesValid(drafts)}
              onClick={() => {
                save.mutate(
                  { id: pipeline.id, schedules: drafts.map(toScheduleBody) },
                  {
                    onSuccess: () => {
                      setDrafts(null);
                    },
                  },
                );
              }}
            >
              {t('pipelines.detail.saveSchedule')}
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  return (
    <Card title={t('pipelines.detail.tabs.schedule')}>
      <div className="sx-stack">
        {pipeline.status !== 'active' && stored.length > 0 && (
          <Alert tone="info">{t(`pipelines.detail.scheduleIdle.${pipeline.status}`)}</Alert>
        )}
        {stored.length === 0 ? (
          <p>{t('pipelines.detail.manualOnly')}</p>
        ) : (
          <ul className="sx-pipelines__schedule-list">
            {stored.map((schedule, i) => {
              const draft = storedDrafts[i];
              return draft ? (
                <ScheduleRow key={schedule.id} schedule={schedule} draft={draft} />
              ) : null;
            })}
          </ul>
        )}
        {canManage && (
          <div>
            <Button
              variant="secondary"
              onClick={() => {
                setDrafts(storedDrafts);
              }}
            >
              {t('pipelines.detail.editSchedule')}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ aviso tras clonar
function CloneNotice({ clone }: { clone: NonNullable<DetailState['clone']> }) {
  const { t, i18n } = useTranslation();
  const diff = clone.schemaDiff;
  const list = new Intl.ListFormat(i18n.resolvedLanguage ?? 'es', { type: 'conjunction' });
  const names = (columns: { name: string }[]) => list.format(columns.map((c) => c.name));
  const changed = diff ? diff.added.length + diff.removed.length + diff.changed.length : 0;
  return (
    <Alert tone={diff?.breaking ? 'warning' : 'info'} title={t('pipelines.clone.doneTitle')}>
      <p>{t(clone.sourceCreated ? 'pipelines.clone.sourceCreated' : 'pipelines.clone.done')}</p>
      {diff && changed === 0 && <p>{t('pipelines.clone.sameSchema')}</p>}
      {diff && changed > 0 && (
        <ul className="sx-pipelines__notice-list">
          {diff.added.length > 0 && (
            <li>{t('pipelines.clone.added', { columns: names(diff.added) })}</li>
          )}
          {diff.removed.length > 0 && (
            <li>{t('pipelines.clone.removed', { columns: names(diff.removed) })}</li>
          )}
          {diff.changed.length > 0 && (
            <li>{t('pipelines.clone.changed', { columns: names(diff.changed) })}</li>
          )}
        </ul>
      )}
    </Alert>
  );
}

// ------------------------------------------------------------------ página
interface PendingAction {
  action: StatusAction;
  danger: boolean;
}

export function PipelineDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as DetailState;
  const { pipelineId = '' } = useParams();
  const pipeline = usePipeline(pipelineId);
  const status = usePipelineStatus(pipelineId);
  const canManage = useCan('pipeline:manage');
  const canRun = useCan('pipeline:run');
  const canPause = useCan('pipeline:pause');
  const canReset = useCan('pipeline:reset_delta');
  const [tab, setTab] = useState<Tab>(state.scheduleError ? 'schedule' : 'summary');
  const [opened, setOpened] = useState<Opened>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);

  if (pipeline.isPending) return <Spinner label={t('common.loading')} />;
  if (pipeline.error) return <Alert tone="danger">{describeError(pipeline.error).message}</Alert>;
  const p = pipeline.data;
  const blocked = p.findings.some((f) => f.severity === 'error');
  // El estado de delta solo existe en los pipelines incrementales.
  const tabs: Tab[] =
    p.extraction_mode === 'FULL'
      ? ['summary', 'runs', 'schedule', 'activity']
      : ['summary', 'runs', 'delta', 'schedule', 'activity'];
  const close = () => {
    setOpened(null);
  };

  const confirm = (reason: string) => {
    if (!pending) return;
    status.mutate(
      { action: pending.action, ...(reason ? { reason } : {}) },
      {
        onSuccess: () => {
          setPending(null);
          if (pending.action === 'delete') void navigate('/pipelines');
        },
      },
    );
  };

  return (
    <>
      <PageHeader
        title={
          <span className="sx-row">
            {p.code} <PipelineStatusPill status={p.status} />
            {p.live_run && <RunStatusPill status={p.live_run.status} />}
          </span>
        }
        description={p.name}
        actions={
          <div className="sx-row">
            <Link to="/pipelines">{t('pipelines.detail.back')}</Link>
            {canRun && p.status === 'active' && (
              <Button
                disabled={p.live_run !== null}
                title={p.live_run ? t('pipelines.detail.alreadyLive') : undefined}
                onClick={() => {
                  setOpened('run');
                }}
              >
                {t('pipelines.detail.actions.run')}
              </Button>
            )}
            {canManage && (
              <Button
                variant="secondary"
                onClick={() => {
                  setOpened('trial');
                }}
              >
                {t('pipelines.detail.actions.test')}
              </Button>
            )}
            {canManage && p.status === 'draft' && (
              <Button
                disabled={blocked}
                title={blocked ? t('pipelines.detail.activateBlocked') : undefined}
                onClick={() => {
                  setOpened('activate');
                }}
              >
                {t('pipelines.detail.actions.activate')}
              </Button>
            )}
            {canPause && p.status === 'active' && (
              <Button
                variant="secondary"
                onClick={() => {
                  setPending({ action: 'pause', danger: false });
                }}
              >
                {t('pipelines.detail.actions.pause')}
              </Button>
            )}
            {canPause && p.status === 'paused' && (
              <Button
                disabled={status.isPending}
                onClick={() => {
                  status.mutate({ action: 'resume' });
                }}
              >
                {t('pipelines.detail.actions.resume')}
              </Button>
            )}
            {canManage && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => void navigate(`/pipelines/${p.id}/edit`)}
                >
                  {t('pipelines.detail.actions.edit')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setOpened('clone');
                  }}
                >
                  {t('pipelines.detail.actions.clone')}
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    setPending({ action: 'delete', danger: true });
                  }}
                >
                  {t('pipelines.detail.actions.delete')}
                </Button>
              </>
            )}
          </div>
        }
      />
      <RouteStrip
        status={p.status}
        extractionMode={p.extraction_mode}
        loadMode={p.load_mode}
        source={{
          code: p.source.code,
          name: p.source.name,
          detail: p.sap_system.code,
          environment: p.sap_system.environment,
        }}
        destination={{
          code: p.destination.code,
          name: p.destination.name,
          detail: t(`destinations.types.${p.destination.type}.label`),
          environment: p.destination.environment,
        }}
      />
      {state.saved && <Alert tone="success">{t('pipelines.detail.saved')}</Alert>}
      {state.scheduleError && (
        <Alert tone="danger" title={t('pipelines.detail.scheduleNotSaved')}>
          {state.scheduleError}
        </Alert>
      )}
      {state.clone && <CloneNotice clone={state.clone} />}
      {status.error && pending === null && (
        <Alert tone="danger">{describeError(status.error).message}</Alert>
      )}
      {p.status === 'draft' && <Alert tone="info">{t('pipelines.detail.draftBanner')}</Alert>}
      {p.status === 'paused' && (
        <Alert tone="warning" title={t('pipelines.detail.pausedBanner')}>
          {p.status_reason}
        </Alert>
      )}
      {p.cross_environment && p.cross_environment_reason && (
        <Alert
          tone="warning"
          title={t('pipelines.detail.crossEnvironment', {
            source: p.sap_system.environment,
            destination: p.destination.environment,
          })}
        >
          {t('pipelines.detail.crossEnvironmentReason', { reason: p.cross_environment_reason })}
        </Alert>
      )}
      <FindingsList
        findings={p.findings.filter(
          (f) => f.code !== CROSS_ENVIRONMENT || !p.cross_environment_reason,
        )}
      />
      <Tabs
        label={t('pipelines.detail.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={tabs.map((id) => ({
          id,
          label: t(`pipelines.detail.tabs.${id}`),
        }))}
      />
      {tab === 'summary' && <SummaryTab pipeline={p} />}
      {tab === 'runs' && <RunsTab pipeline={p} />}
      {tab === 'delta' && <DeltaTab pipeline={p} canReset={canReset} />}
      {tab === 'schedule' && <ScheduleTab pipeline={p} canManage={canManage} />}
      {tab === 'activity' && <AuditTrail resourceId={p.id} />}

      {opened === 'run' && <RunNowDialog pipeline={p} canManage={canManage} onClose={close} />}
      {opened === 'activate' && <ActivateDialog pipeline={p} onClose={close} />}
      {opened === 'clone' && <CloneDialog pipeline={p} onClose={close} />}
      {opened === 'trial' && <TrialDialog pipeline={p} onClose={close} />}
      <ConfirmDialog
        key={pending?.action ?? 'none'}
        open={pending !== null}
        title={pending ? t(`pipelines.confirm.${pending.action}.title`, { code: p.code }) : ''}
        message={
          pending ? (
            <>
              <p>{t(`pipelines.confirm.${pending.action}.message`)}</p>
              {pending.action === 'pause' && p.extraction_mode === 'DELTA' && (
                <Alert tone="warning">{t('pipelines.confirm.pause.delta')}</Alert>
              )}
            </>
          ) : (
            ''
          )
        }
        confirmLabel={pending ? t(`pipelines.detail.actions.${pending.action}`) : ''}
        cancelLabel={t('common.cancel')}
        danger={pending?.danger ?? false}
        busy={status.isPending}
        reasonLabel={t('pipelines.confirm.reason')}
        {...(pending?.action === 'delete'
          ? {
              confirmText: p.code,
              confirmTextLabel: t('pipelines.confirm.typeCode', { code: p.code }),
            }
          : {})}
        error={
          status.error && pending !== null ? (
            <Alert tone="danger">{describeError(status.error).message}</Alert>
          ) : undefined
        }
        onConfirm={confirm}
        onClose={() => {
          status.reset();
          setPending(null);
        }}
      />
    </>
  );
}
