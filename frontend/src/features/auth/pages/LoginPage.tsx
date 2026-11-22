import { useState, type SubmitEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useSearchParams } from 'react-router';
import { useSystemInfo } from '@/features/system/api/queries';
import { describeError } from '@/shared/api/errors';
import { useShake } from '@/shared/motion/useShake';
import { Alert, BrandMark, Button, Spinner, StatusPill, TextField } from '@/shared/ui';
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
import photoL from '../assets/login-main-1122.webp';
import photoS from '../assets/login-main-640.webp';
import './login.css';

const PHOTO_SIZES = '(min-width: 900px) 55vw, 100vw';

/** La API responde igual con contraseña errónea y con cuenta bloqueada (no revela cuál). */
const INVALID_CREDENTIALS = 'SX-AUTH-INVALID-CREDENTIALS';

function PasswordStep() {
  const { t } = useTranslation();
  const login = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // Rechazos seguidos: desde el segundo se avisa de que la cuenta puede bloquearse.
  const [rejections, setRejections] = useState(0);
  const { ref: credentialsRef, shake } = useShake<HTMLDivElement>();

  const submit = (e: SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    login.mutate(
      { username: username.trim(), password },
      {
        onError: (error) => {
          setPassword('');
          if (describeError(error).code !== INVALID_CREDENTIALS) return;
          setRejections((n) => n + 1);
          shake();
          // El botón se desactiva al vaciar la contraseña: el foco vuelve al campo a corregir.
          credentialsRef.current
            ?.querySelector<HTMLInputElement>('input[name="password"]')
            ?.focus();
        },
      },
    );
  };

  const error = login.error ? describeError(login.error) : null;
  const rejected = error?.code === INVALID_CREDENTIALS;
  const rejection = rejected
    ? rejections > 1
      ? `${t('login.invalidCredentials')} ${t('login.lockoutHint')}`
      : t('login.invalidCredentials')
    : undefined;

  return (
    <form onSubmit={submit} noValidate>
      {error && !rejected && <Alert tone="danger">{error.message}</Alert>}
      <div ref={credentialsRef}>
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
          error={rejection}
          required
        />
      </div>
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

/** Título y subtítulo de cada paso; el subtítulo dice qué hacer en ese paso. */
const STEPS: Record<Exclude<SessionStage, 'active'>, { title: string; lede: string }> = {
  mfa: { title: 'login.mfaTitle', lede: 'login.mfaHelp' },
  password_change: { title: 'login.passwordChangeTitle', lede: 'login.passwordChangeHelp' },
  mfa_setup: { title: 'login.mfaSetupTitle', lede: 'login.mfaSetupHelp' },
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

  const step = stage ? STEPS[stage] : null;
  const title = t(step?.title ?? 'login.title');
  const lede = t(step?.lede ?? 'login.lede');
  let body = <PasswordStep />;
  if (me.isPending) {
    body = <Spinner label={t('common.loading')} />;
  } else if (me.isError) {
    body = <Alert tone="danger">{t('login.apiUnavailable')}</Alert>;
  } else if (stage) {
    body =
      stage === 'mfa' ? (
        <MfaStep />
      ) : stage === 'password_change' ? (
        <ChangePasswordForm forced />
      ) : (
        <MfaSetup />
      );
  }

  return (
    <main className="sx-login">
      <div className="sx-login__frame">
        <section className="sx-login__side" aria-labelledby="sx-login-title">
          <p className="sx-login__brand">
            <BrandMark size={28} />
            <span>{t('app.name')}</span>
          </p>

          <div className="sx-login__form">
            <header className="sx-login__head">
              <h1 id="sx-login-title">{title}</h1>
              <p>{lede}</p>
            </header>
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
          </div>

          <footer className="sx-login__foot">
            <p>{t('login.help')}</p>
            <EnvironmentNote />
          </footer>
        </section>

        <LoginVisual />
      </div>
    </main>
  );
}

/** Entorno y versión antes de entrar: evita iniciar sesión en PRD creyendo estar en DEV. */
function EnvironmentNote() {
  const { t } = useTranslation();
  const info = useSystemInfo();
  if (!info.data) return null;
  const { environment, version } = info.data;
  return (
    <p className="sx-login__meta">
      <span className="sx-visually-hidden">{t('login.environment')}</span>
      <StatusPill
        tone={environment === 'prd' ? 'danger' : environment === 'qa' ? 'warning' : 'info'}
      >
        {environment.toUpperCase()}
      </StatusPill>
      <span>{t('login.version', { version })}</span>
    </p>
  );
}

/** Panel de imagen, decorativo (`aria-hidden`); la misma imagen sirve en los dos temas. */
function LoginVisual() {
  return (
    <div className="sx-login__visual" aria-hidden="true">
      <img
        className="sx-login__photo"
        src={photoL}
        srcSet={`${photoS} 640w, ${photoL} 1122w`}
        sizes={PHOTO_SIZES}
        alt=""
        decoding="async"
        fetchPriority="high"
      />
    </div>
  );
}
