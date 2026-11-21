import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { formatDateTime } from '@/shared/format';
import { Alert, Button, Checklist, Dialog, Spinner, type StatusTone } from '@/shared/ui';
import { useTestPipeline, type PipelineDetail, type PipelineTest } from '../api/queries';

type Step = PipelineTest['steps'][number];

/** Un borrado fallido del destino de prueba avisa, pero no invalida la prueba. */
function stepTone(step: Step): StatusTone {
  if (step.status === 'succeeded') return 'success';
  if (step.status === 'skipped') return 'neutral';
  return step.step === 'cleanup' ? 'warning' : 'danger';
}

/** Resultado paso a paso de «Probar pipeline» (plan 08 §6). */
export function TrialResult({ result }: { result: PipelineTest }) {
  const { t } = useTranslation();
  return (
    // Región enfocable: en pantallas bajas el diálogo se desplaza y el resultado no tiene controles.
    <div
      className="sx-stack"
      role="region"
      aria-label={t('pipelines.trial.resultLabel')}
      tabIndex={0}
    >
      <Alert tone={result.ok ? 'success' : 'danger'}>
        {result.ok
          ? t('pipelines.trial.passed', { read: result.rows_read, loaded: result.rows_loaded })
          : t('pipelines.trial.failed')}
      </Alert>
      <Checklist
        label={t('pipelines.trial.stepsLabel')}
        items={result.steps.map((step) => ({
          id: step.step,
          title: t(`pipelines.trial.steps.${step.step}`),
          tone: stepTone(step),
          statusLabel: t(`pipelines.trial.status.${step.status}`),
          description: step.message,
          meta: step.error_code ?? undefined,
        }))}
      />
    </div>
  );
}

/** Qué se sabe de la última prueba antes de lanzar otra. */
function LastTrial({ pipeline }: { pipeline: PipelineDetail }) {
  const { t } = useTranslation();
  if (!pipeline.last_test_at) return null;
  const when = formatDateTime(pipeline.last_test_at);
  if (pipeline.test_outdated) {
    return <Alert tone="warning">{t('pipelines.trial.lastOutdated', { when })}</Alert>;
  }
  return (
    <p className="sx-muted">
      {t(pipeline.last_test_ok ? 'pipelines.trial.lastPassed' : 'pipelines.trial.lastFailed', {
        when,
      })}
    </p>
  );
}

/** «Probar pipeline»: muestra de SAP hacia un destino de prueba, sin tocar nada real. */
export function TrialDialog({
  pipeline,
  onClose,
}: {
  pipeline: PipelineDetail;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const trial = useTestPipeline(pipeline.id);

  return (
    <Dialog
      open
      size="lg"
      title={t('pipelines.trial.title', { code: pipeline.code })}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={trial.isPending}>
            {t('common.close')}
          </Button>
          <Button
            disabled={trial.isPending}
            onClick={() => {
              trial.mutate();
            }}
          >
            {trial.data ? t('pipelines.trial.again') : t('pipelines.trial.run')}
          </Button>
        </>
      }
    >
      <div className="sx-stack">
        <p>{t('pipelines.trial.intro')}</p>
        {!trial.data && !trial.isPending && <LastTrial pipeline={pipeline} />}
        {trial.error && <Alert tone="danger">{describeError(trial.error).message}</Alert>}
        {trial.isPending && <Spinner label={t('pipelines.trial.running')} />}
        {trial.data && <TrialResult result={trial.data} />}
      </div>
    </Dialog>
  );
}
