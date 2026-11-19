import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, FieldErrors, TextField } from '@/shared/ui';
import { useChangePassword } from '../api/queries';

export interface ChangePasswordFormProps {
  /** Cambio forzado tras el login con contraseña temporal: no pide la actual. */
  forced?: boolean;
  onDone?: () => void;
}

export function ChangePasswordForm({ forced = false, onDone }: ChangePasswordFormProps) {
  const { t } = useTranslation();
  const change = useChangePassword();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [mismatch, setMismatch] = useState(false);
  const error = change.error ? describeError(change.error) : null;

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (next !== confirm) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate(
      { current_password: forced ? null : current, new_password: next },
      {
        onSuccess: () => {
          setCurrent('');
          setNext('');
          setConfirm('');
          onDone?.();
        },
      },
    );
  };

  return (
    <form onSubmit={submit} noValidate>
      {error && !Object.keys(error.fields).length && <Alert tone="danger">{error.message}</Alert>}
      {change.isSuccess && !forced && <Alert tone="success">{t('password.changed')}</Alert>}
      {!forced && (
        <>
          <TextField
            label={t('password.current')}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => {
              setCurrent(e.target.value);
            }}
            required
          />
          <FieldErrors errors={error?.fields.current_password} />
        </>
      )}
      <TextField
        label={t('password.new')}
        type="password"
        autoComplete="new-password"
        hint={t('password.policyHint')}
        value={next}
        onChange={(e) => {
          setNext(e.target.value);
        }}
        required
      />
      <FieldErrors errors={error?.fields.new_password} />
      <TextField
        label={t('password.confirm')}
        type="password"
        autoComplete="new-password"
        value={confirm}
        onChange={(e) => {
          setConfirm(e.target.value);
        }}
        error={mismatch ? t('password.mismatch') : undefined}
        required
      />
      <Button type="submit" disabled={change.isPending || !next || !confirm}>
        {t('password.submit')}
      </Button>
    </form>
  );
}
