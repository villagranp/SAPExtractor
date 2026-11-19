import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, TextField } from '@/shared/ui';
import { useEnableMfa, useStartMfaSetup } from '../api/queries';
import './mfa.css';

export interface MfaSetupProps {
  onDone?: () => void;
}

/** Alta del segundo factor: QR + clave manual + confirmación con un primer código. */
export function MfaSetup({ onDone }: MfaSetupProps) {
  const { t } = useTranslation();
  const start = useStartMfaSetup();
  const enable = useEnableMfa();
  const [code, setCode] = useState('');
  const enrollment = start.data;
  const error = start.error ?? enable.error;

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    enable.mutate(code.trim(), { onSuccess: () => onDone?.() });
  };

  if (!enrollment) {
    return (
      <div className="sx-stack">
        <p>{t('mfa.intro')}</p>
        {error && <Alert tone="danger">{describeError(error).message}</Alert>}
        <div>
          <Button
            onClick={() => {
              start.mutate();
            }}
            disabled={start.isPending}
          >
            {t('mfa.setupStart')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="sx-stack" noValidate>
      <ol className="sx-steps">
        <li>{t('mfa.scanQr')}</li>
        <li>{t('mfa.enterCode')}</li>
      </ol>
      <img
        src={enrollment.qr_svg_data_uri}
        alt={t('mfa.qrAlt')}
        width={200}
        height={200}
        className="sx-qr"
      />
      <p className="sx-muted">
        {t('mfa.manualKey')} <code data-testid="mfa-secret">{enrollment.secret}</code>
      </p>
      {error && <Alert tone="danger">{describeError(error).message}</Alert>}
      <TextField
        label={t('mfa.code')}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={code}
        onChange={(e) => {
          setCode(e.target.value.replace(/\D/g, ''));
        }}
      />
      <div>
        <Button type="submit" disabled={code.length !== 6 || enable.isPending}>
          {t('mfa.enable')}
        </Button>
      </div>
    </form>
  );
}
