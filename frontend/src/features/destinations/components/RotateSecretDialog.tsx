import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Dialog, Spinner } from '@/shared/ui';
import {
  useDestinationTypeSchema,
  useRotateDestinationSecret,
  type DestinationDetail,
} from '../api/queries';
import { CheckReportView } from './CheckReportView';
import { SchemaForm } from './SchemaForm';
import {
  initialValues,
  missingFields,
  secretSchemaFor,
  toPayload,
  type FormValues,
} from './schemaValues';

interface Props {
  destination: DestinationDetail;
  open: boolean;
  onClose: () => void;
}

/** Sustituye la credencial; la API prueba el destino enseguida y devuelve el resultado. */
export function RotateSecretDialog({ destination, open, onClose }: Props) {
  const { t } = useTranslation();
  const typeSchema = useDestinationTypeSchema(open ? destination.type : null);
  const rotate = useRotateDestinationSecret(destination.id);
  const schema = secretSchemaFor(
    destination.type,
    typeSchema.data?.secret_schema ?? {},
    destination.config,
  );
  const [values, setValues] = useState<FormValues | null>(null);
  const current = values ?? initialValues(schema);
  const error = rotate.error ? describeError(rotate.error) : null;
  const errors = Object.fromEntries(
    Object.entries(error?.fields ?? {})
      .filter(([k]) => k.startsWith('secret.'))
      .map(([k, v]) => [k.slice('secret.'.length), v[0]]),
  );
  const close = () => {
    rotate.reset();
    setValues(null);
    onClose();
  };
  const result = rotate.data;
  return (
    <Dialog
      open={open}
      onClose={close}
      title={t('destinations.rotate.title', { code: destination.code })}
      closeLabel={t('common.close')}
      footer={
        result ? (
          <Button onClick={close}>{t('common.close')}</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={
                rotate.isPending || !typeSchema.data || missingFields(schema, current).length > 0
              }
              onClick={() => {
                rotate.mutate({ secret: toPayload(schema, current), version: destination.version });
              }}
            >
              {rotate.isPending
                ? t('destinations.rotate.working')
                : t('destinations.rotate.submit')}
            </Button>
          </>
        )
      }
    >
      {result?.test ? (
        <CheckReportView report={result.test} />
      ) : typeSchema.isPending ? (
        <Spinner label={t('common.loading')} />
      ) : (
        <>
          {destination.type !== 'csv' && <p>{t(`destinations.rotate.help.${destination.type}`)}</p>}
          {error && <Alert tone="danger">{error.message}</Alert>}
          <SchemaForm schema={schema} values={current} onChange={setValues} errors={errors} />
        </>
      )}
    </Dialog>
  );
}
