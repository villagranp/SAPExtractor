import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useDeltaState,
  useResetDelta,
  useResolveSubscription,
  type DeltaState,
} from '@/features/runs/api/queries';
import { DeltaStatusPill } from '@/features/runs/components/RunParts';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, Button, Card, ConfirmDialog, DetailList, Spinner } from '@/shared/ui';
import type { PipelineDetail } from '../api/queries';
import '@/features/runs/runs.css';

type Pending = 'reset' | 'closed' | 'left_open' | null;

function DeltaCards({
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
        {canReset && (resettable || state.sap_close_pending) && (
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

/**
 * Estado del delta del pipeline (plan 06 §3): token vigente, historial, reinicialización y
 * confirmación de la suscripción que queda abierta en SAP.
 */
export function DeltaTab({ pipeline, canReset }: { pipeline: PipelineDetail; canReset: boolean }) {
  const { t } = useTranslation();
  const delta = useDeltaState(pipeline.id);
  const reset = useResetDelta(pipeline.id);
  const resolve = useResolveSubscription(pipeline.id);
  const [pending, setPending] = useState<Pending>(null);
  const actionError = reset.error ?? resolve.error;

  if (delta.error) return <Alert tone="danger">{describeError(delta.error).message}</Alert>;
  if (!delta.data) return <Spinner label={t('common.loading')} />;

  const confirm = (reason: string) => {
    const done = {
      onSuccess: () => {
        setPending(null);
      },
    };
    if (pending === 'reset') reset.mutate(reason, done);
    if (pending === 'closed') resolve.mutate({ action: 'closed', reason: reason || null }, done);
    if (pending === 'left_open') resolve.mutate({ action: 'left_open', reason }, done);
  };

  return (
    <>
      <DeltaCards state={delta.data} canReset={canReset} onAction={setPending} />
      <ConfirmDialog
        key={pending ?? 'none'}
        open={pending !== null}
        title={pending ? t(`pipelines.confirm.${pending}.title`, { code: pipeline.code }) : ''}
        message={pending ? t(`pipelines.confirm.${pending}.message`) : ''}
        confirmLabel={pending ? t(`pipelines.confirm.${pending}.confirm`) : ''}
        cancelLabel={t('common.cancel')}
        danger={pending === 'reset'}
        busy={reset.isPending || resolve.isPending}
        reasonLabel={t('pipelines.confirm.reason')}
        reasonRequired={pending === 'reset' || pending === 'left_open'}
        {...(pending === 'reset'
          ? {
              confirmText: pipeline.code,
              confirmTextLabel: t('pipelines.confirm.typeCode', { code: pipeline.code }),
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
          setPending(null);
        }}
      />
    </>
  );
}
