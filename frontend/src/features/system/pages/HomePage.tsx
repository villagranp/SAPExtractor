import { useTranslation } from 'react-i18next';
import { Card, PageHeader, StatusPill, type StatusTone } from '@/shared/ui';
import { useReadiness, useSystemInfo, type HealthReport } from '../api/queries';

type CheckState = 'ok' | 'fail' | 'loading' | 'unknown';

function tone(state: CheckState): StatusTone {
  switch (state) {
    case 'ok':
      return 'success';
    case 'fail':
      return 'danger';
    case 'loading':
      return 'info';
    default:
      return 'neutral';
  }
}

function checkState(report: HealthReport | undefined, name: string, loading: boolean): CheckState {
  if (loading) return 'loading';
  const check = report?.checks[name];
  return check ? check.status : 'unknown';
}

export function HomePage() {
  const { t } = useTranslation();
  const info = useSystemInfo();
  const ready = useReadiness();

  const apiState: CheckState = ready.isLoading ? 'loading' : ready.isError ? 'fail' : 'ok';
  const label = (state: CheckState) =>
    state === 'loading'
      ? t('home.checking')
      : state === 'ok'
        ? t('home.ok')
        : state === 'fail'
          ? t('home.fail')
          : '—';

  const rows: {
    name: string;
    title: string;
    state: CheckState;
    detail?: string | null | undefined;
  }[] = [
    { name: 'api', title: t('home.api'), state: apiState },
    {
      name: 'database',
      title: t('home.database'),
      state: checkState(ready.data, 'database', ready.isLoading),
      detail: ready.data?.checks.database?.detail,
    },
    {
      name: 'migrations',
      title: t('home.migrations'),
      state: checkState(ready.data, 'migrations', ready.isLoading),
      detail: ready.data?.checks.migrations?.detail,
    },
  ];

  return (
    <>
      <PageHeader title={t('home.title')} description={t('home.description')} />
      {ready.isError && (
        <p role="alert">
          <StatusPill tone="danger">{t('home.unreachable')}</StatusPill>
        </p>
      )}
      <div className="sx-grid">
        {rows.map((row) => (
          <Card key={row.name} title={row.title}>
            <StatusPill tone={tone(row.state)}>{label(row.state)}</StatusPill>
            {row.detail && <p className="sx-field__hint">{row.detail}</p>}
          </Card>
        ))}
        <Card title={t('home.version')}>
          <dl className="sx-dl">
            <dt>{t('home.version')}</dt>
            <dd>{info.data?.version ?? '—'}</dd>
            <dt>{t('home.environment')}</dt>
            <dd>{info.data?.environment ?? '—'}</dd>
            <dt>{t('home.commit')}</dt>
            <dd>
              <code>{info.data?.build_commit ?? '—'}</code>
            </dd>
          </dl>
        </Card>
      </div>
      <Card title={t('home.nextSteps')} className="sx-mt">
        <p>{t('home.nextStepsBody')}</p>
      </Card>
    </>
  );
}
