import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useSearchParams } from 'react-router';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Card, Spinner, TextField } from '@/shared/ui';
import {
  safeNext,
  useLogin,
  useLogout,
  useMe,
  useVerifyMfa,
  type SessionStage,
} from '../api/queries';
import { ChangePasswordForm } from '../components/ChangePasswordForm';
import { MfaSetup } from '../components/MfaSetup';
import './login.css';

function PasswordStep() {
  const { t } = useTranslation();
  const login = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    login.mutate(
      { username: username.trim(), password },
      {
        onError: () => {
          setPassword('');
        },
      },
    );
  };

  return (
    <form onSubmit={submit} noValidate>
      {login.error && <Alert tone="danger">{describeError(login.error).message}</Alert>}
      <TextField
        label={t('login.username')}
        name="username"
        autoComplete="username"
        value={username}
        onChange={(e) => {
          setUsername(e.target.value);
        }}
        autoFocus
        required
      />
      <TextField
        label={t('login.password')}
        name="password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(e) => {
          setPassword(e.target.value);
        }}
        required
      />
      <Button
        type="submit"
        disabled={login.isPending || !username || !password}
        style={{ width: '100%', justifyContent: 'center' }}
      >
        {login.isPending ? t('login.signingIn') : t('login.submit')}
      </Button>
    </form>
  );
}

function MfaStep() {
  const { t } = useTranslation();
  const verify = useVerifyMfa();
  const [code, setCode] = useState('');
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        verify.mutate(code, {
          onError: () => {
            setCode('');
          },
        });
      }}
      noValidate
    >
      <p>{t('login.mfaHelp')}</p>
      {verify.error && <Alert tone="danger">{describeError(verify.error).message}</Alert>}
      <TextField
        label={t('mfa.code')}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={code}
        onChange={(e) => {
          setCode(e.target.value.replace(/\D/g, ''));
        }}
        autoFocus
      />
      <Button
        type="submit"
        disabled={code.length !== 6 || verify.isPending}
        style={{ width: '100%', justifyContent: 'center' }}
      >
        {t('login.verify')}
      </Button>
    </form>
  );
}

const STEP_TITLES: Record<Exclude<SessionStage, 'active'>, string> = {
  mfa: 'login.mfaTitle',
  password_change: 'login.passwordChangeTitle',
  mfa_setup: 'login.mfaSetupTitle',
};

/** Inicio de sesión por pasos: contraseña → MFA → cambio obligatorio → alta de MFA. */
export function LoginPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const me = useMe();
  const logout = useLogout();
  const next = safeNext(params.get('next'));
  const stage = me.data?.stage;

  if (stage === 'active') return <Navigate to={next} replace />;

  let title = t('login.title');
  let body = <PasswordStep />;
  if (me.isPending) {
    body = <Spinner label={t('common.loading')} />;
  } else if (me.isError) {
    body = <Alert tone="danger">{t('login.apiUnavailable')}</Alert>;
  } else if (stage) {
    title = t(STEP_TITLES[stage]);
    body =
      stage === 'mfa' ? (
        <MfaStep />
      ) : stage === 'password_change' ? (
        <>
          <p>{t('login.passwordChangeHelp')}</p>
          <ChangePasswordForm forced />
        </>
      ) : (
        <>
          <p>{t('login.mfaSetupHelp')}</p>
          <MfaSetup />
        </>
      );
  }

  return (
    <main className="sx-login">
      <Card title={title} className="sx-login__card">
        {body}
        {stage && (
          <p className="sx-login__switch">
            <Button
              variant="ghost"
              onClick={() => {
                logout.mutate();
              }}
              disabled={logout.isPending}
            >
              {t('login.useAnotherAccount')}
            </Button>
          </p>
        )}
      </Card>
    </main>
  );
}
