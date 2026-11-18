import { useTranslation } from 'react-i18next';
import { Button, Card, StatusPill, TextField } from '@/shared/ui';
import './login.css';

/** Pantalla provisional: el login local y Entra ID se implementan en las iteraciones 1 y 7. */
export function LoginPage() {
  const { t } = useTranslation();
  return (
    <main className="sx-login">
      <Card title={t('login.title')} className="sx-login__card">
        <p>
          <StatusPill tone="info">{t('login.pending')}</StatusPill>
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
          }}
        >
          <fieldset disabled style={{ border: 0, padding: 0, margin: 0 }}>
            <TextField label={t('login.username')} name="username" autoComplete="username" />
            <TextField
              label={t('login.password')}
              name="password"
              type="password"
              autoComplete="current-password"
            />
            <Button type="submit" style={{ width: '100%', justifyContent: 'center' }}>
              {t('login.submit')}
            </Button>
          </fieldset>
        </form>
      </Card>
    </main>
  );
}
