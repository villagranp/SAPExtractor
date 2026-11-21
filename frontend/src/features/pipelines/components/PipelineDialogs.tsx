import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { useDestinations } from '@/features/destinations/api/queries';
import { useSapSystems } from '@/features/sap-systems/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Checklist,
  Dialog,
  SelectField,
  Spinner,
  TextAreaField,
  TextField,
} from '@/shared/ui';
import {
  useActivatePipeline,
  useCheckPipeline,
  useClonePipeline,
  useRunPipeline,
  type PipelineDetail,
  type RunMode,
} from '../api/queries';
import { FindingsList } from './PipelinePills';

const MIN_REASON = 10;
const CHOICES = 100;

interface DialogBaseProps {
  pipeline: PipelineDetail;
  onClose: () => void;
}

// ------------------------------------------------------------------ comprobar y activar
/** Comprobación previa (plan 08 §5) y activación. Se monta al abrirlo: la comprobación arranca sola. */
export function ActivateDialog({ pipeline, onClose }: DialogBaseProps) {
  const { t } = useTranslation();
  const check = useCheckPipeline(pipeline.id);
  const activate = useActivatePipeline(pipeline.id);
  const [reason, setReason] = useState(pipeline.cross_environment_reason ?? '');
  const { mutate: runCheck } = check;
  const initialReason = pipeline.cross_environment_reason ?? '';

  useEffect(() => {
    runCheck(initialReason);
  }, [runCheck, initialReason]);

  const report = check.data;
  const failing = report?.checks.filter((c) => !c.ok) ?? [];
  const needsReason = pipeline.cross_environment;
  const reasonOk = reason.trim().length >= MIN_REASON;
  // Solo falta el motivo de los entornos cruzados: se puede activar en cuanto se escriba.
  const onlyReasonMissing =
    failing.length > 0 && failing.every((c) => c.code === 'cross_environment');
  const canActivate =
    !check.isPending &&
    !activate.isPending &&
    report !== undefined &&
    (report.ok || (onlyReasonMissing && reasonOk));

  return (
    <Dialog
      open
      size="lg"
      title={t('pipelines.activate.title', { code: pipeline.code })}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={activate.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="secondary"
            disabled={check.isPending || activate.isPending}
            onClick={() => {
              check.mutate(reason.trim());
            }}
          >
            {t('pipelines.activate.recheck')}
          </Button>
          <Button
            disabled={!canActivate}
            onClick={() => {
              activate.mutate(reason.trim(), { onSuccess: onClose });
            }}
          >
            {t('pipelines.activate.confirm')}
          </Button>
        </>
      }
    >
      <div className="sx-stack">
        <p>{t('pipelines.activate.intro')}</p>
        {check.error && <Alert tone="danger">{describeError(check.error).message}</Alert>}
        {activate.error && <Alert tone="danger">{describeError(activate.error).message}</Alert>}
        {check.isPending && <Spinner label={t('pipelines.activate.checking')} />}
        {report && (
          // Región enfocable: en pantallas bajas el diálogo se desplaza y no tiene otro control dentro.
          <div role="region" aria-label={t('pipelines.activate.checksLabel')} tabIndex={0}>
            <Checklist
              label={t('pipelines.activate.checksLabel')}
              items={report.checks.map((c) => ({
                id: c.code,
                title: t(`pipelines.checks.${c.code}`, { defaultValue: c.code }),
                tone: c.ok ? 'success' : 'danger',
                statusLabel: c.ok ? t('pipelines.activate.passed') : t('pipelines.activate.failed'),
                description: c.message,
              }))}
            />
            <FindingsList findings={report.findings.filter((f) => f.severity === 'warning')} />
          </div>
        )}
        {needsReason && (
          <TextAreaField
            label={t('pipelines.activate.reason')}
            hint={t('pipelines.activate.reasonHint', {
              source: pipeline.sap_system.environment,
              destination: pipeline.destination.environment,
            })}
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
        )}
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ ejecutar ahora
export function RunNowDialog({
  pipeline,
  canManage,
  onClose,
}: DialogBaseProps & { canManage: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const run = useRunPipeline(pipeline.id);
  const [mode, setMode] = useState<'' | RunMode>('');
  const [reason, setReason] = useState('');
  const incremental = pipeline.extraction_mode !== 'FULL';

  return (
    <Dialog
      open
      title={t('pipelines.runNow.title', { code: pipeline.code })}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={run.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            disabled={run.isPending}
            onClick={() => {
              run.mutate(
                { ...(mode ? { mode } : {}), reason: reason.trim() },
                {
                  onSuccess: (created) => {
                    void navigate(`/runs/${created.id}`);
                  },
                },
              );
            }}
          >
            {t('pipelines.runNow.confirm')}
          </Button>
        </>
      }
    >
      <div className="sx-stack">
        <p>{t('pipelines.runNow.intro')}</p>
        {run.error && <Alert tone="danger">{describeError(run.error).message}</Alert>}
        {incremental && canManage && (
          <SelectField
            label={t('pipelines.runNow.mode')}
            hint={mode === 'FULL' ? t('pipelines.runNow.fullHint') : undefined}
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as '' | RunMode);
            }}
            options={[
              {
                value: '',
                label: t('pipelines.runNow.usual', {
                  mode: t(`pipelines.extractionModes.${pipeline.extraction_mode}`),
                }),
              },
              { value: 'FULL', label: t('pipelines.runNow.full') },
            ]}
          />
        )}
        <TextAreaField
          label={t('pipelines.runNow.reason')}
          rows={2}
          maxLength={500}
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
          }}
        />
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ clonar
/** Copia el pipeline, si se quiere hacia otro sistema SAP y otro destino (DEV → QAS → PRD). */
export function CloneDialog({ pipeline, onClose }: DialogBaseProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const formId = useId();
  const clone = useClonePipeline(pipeline.id);
  const systems = useSapSystems({ page: 1, page_size: CHOICES, status: 'active' });
  const destinations = useDestinations({ page: 1, page_size: CHOICES });
  const [code, setCode] = useState(`${pipeline.code}_COPIA`.slice(0, 40));
  const [name, setName] = useState('');
  const [systemId, setSystemId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [sourceCode, setSourceCode] = useState('');
  const described = clone.error ? describeError(clone.error) : null;
  const codeOk = /^[A-Za-z][A-Za-z0-9_]{2,39}$/.test(code.trim());

  const submit = () => {
    if (!codeOk) return;
    clone.mutate(
      {
        code: code.trim().toUpperCase(),
        name: name.trim() || null,
        sap_system_id: systemId || null,
        destination_id: destinationId || null,
        source_code: systemId && sourceCode.trim() ? sourceCode.trim().toUpperCase() : null,
      },
      {
        onSuccess: (result) => {
          void navigate(`/pipelines/${result.pipeline.id}`, {
            state: {
              clone: { sourceCreated: result.source_created, schemaDiff: result.schema_diff },
            },
          });
        },
      },
    );
  };

  return (
    <Dialog
      open
      title={t('pipelines.clone.title', { code: pipeline.code })}
      onClose={onClose}
      closeLabel={t('common.close')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={clone.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form={formId} disabled={clone.isPending || !codeOk}>
            {t('pipelines.clone.confirm')}
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
          submit();
        }}
      >
        <p>{t('pipelines.clone.intro')}</p>
        {described && <Alert tone="danger">{described.message}</Alert>}
        <TextField
          label={t('pipelines.clone.code')}
          autoComplete="off"
          spellCheck={false}
          value={code}
          error={described?.fields.code?.[0] ?? (codeOk ? undefined : t('pipelines.errors.code'))}
          onChange={(e) => {
            setCode(e.target.value);
          }}
        />
        <TextField
          label={t('pipelines.clone.name')}
          hint={t('pipelines.clone.nameHint')}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
        />
        <SelectField
          label={t('pipelines.clone.system')}
          placeholder={t('pipelines.clone.sameSystem', { code: pipeline.sap_system.code })}
          hint={t('pipelines.clone.systemHint')}
          value={systemId}
          onChange={(e) => {
            setSystemId(e.target.value);
          }}
          options={(systems.data?.items ?? [])
            .filter((s) => s.id !== pipeline.sap_system.id)
            .map((s) => ({ value: s.id, label: `${s.code} (${s.environment})` }))}
        />
        {systemId && (
          <TextField
            label={t('pipelines.clone.sourceCode')}
            hint={t('pipelines.clone.sourceCodeHint')}
            autoComplete="off"
            spellCheck={false}
            value={sourceCode}
            error={described?.fields.source_code?.[0]}
            onChange={(e) => {
              setSourceCode(e.target.value);
            }}
          />
        )}
        <SelectField
          label={t('pipelines.clone.destination')}
          placeholder={t('pipelines.clone.sameDestination', { code: pipeline.destination.code })}
          value={destinationId}
          onChange={(e) => {
            setDestinationId(e.target.value);
          }}
          options={(destinations.data?.items ?? [])
            .filter((d) => d.id !== pipeline.destination.id)
            .map((d) => ({ value: d.id, label: `${d.code} (${d.environment})` }))}
        />
      </form>
    </Dialog>
  );
}
