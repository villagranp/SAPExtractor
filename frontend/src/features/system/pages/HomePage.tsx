import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { useAuditIntegrity } from '@/features/audit/api/queries';
import { useCan } from '@/features/auth/api/queries';
import { useSapSystems } from '@/features/sap-systems/api/queries';
import { useUsers } from '@/features/users/api/queries';
import { formatDateTime } from '@/shared/format';
import {
  Button,
  Card,
  IconPlus,
  Meter,
  Metric,
  PageHeader,
  StatusPill,
  type MeterState,
  type StatusTone,
} from '@/shared/ui';
import { useReadiness, useSystemInfo, type HealthReport } from '../api/queries';
import './home.css';

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

/**
 * Estado de cada iteración del ROADMAP (docs/plan/ROADMAP.md); actualizarlo al cerrar una.
 * Fase 0 sigue en curso por el spike SAP; It4a está construida pero falta validar Snowflake.
 */
const ROADMAP: { key: string; state: MeterState }[] = [
  { key: 'it0', state: 'on' },
  { key: 'it1', state: 'on' },
  { key: 'it2', state: 'on' },
  { key: 'fase0', state: 'partial' },
  { key: 'it3', state: 'on' },
  { key: 'it4a', state: 'partial' },
  { key: 'it5', state: 'off' },
  { key: 'it6', state: 'off' },
  { key: 'it7', state: 'off' },
  { key: 'it8', state: 'off' },
  { key: 'it9', state: 'off' },
];

/** Fecha y hora que se refrescan cada minuto (cabecera del inicio). */
function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(new Date());
    }, 60_000);
    return () => {
      window.clearInterval(id);
    };
  }, []);
  return now;
}

const eyebrowFormat = new Intl.DateTimeFormat('es', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  hour: '2-digit',
  minute: '2-digit',
});

/** «viernes, 20 de noviembre» → «Viernes, 20 de noviembre» (en español el día va en minúscula). */
function sentenceCase(text: string): string {
  return text.charAt(0).toLocaleUpperCase('es') + text.slice(1);
}

function PlatformCard() {
  const { t } = useTranslation();
  const ready = useReadiness();
  const apiState: CheckState = ready.isLoading ? 'loading' : ready.isError ? 'fail' : 'ok';
  const rows = [
    { name: 'api', title: t('home.api'), state: apiState, detail: null },
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
  const label = (state: CheckState) =>
    state === 'loading'
      ? t('home.checking')
      : state === 'ok'
        ? t('home.ok')
        : state === 'fail'
          ? t('home.fail')
          : '—';
  const allOk = rows.every((r) => r.state === 'ok');
  const overall = ready.isLoading ? t('home.checking') : allOk ? t('home.allOk') : t('home.review');

  return (
    <Card className="sx-home__card">
      <Metric label={t('home.platform')} value={overall} />
      <ul className="sx-home__checks">
        {rows.map((row) => (
          <li key={row.name}>
            <span>{row.title}</span>
            <StatusPill tone={tone(row.state)}>{label(row.state)}</StatusPill>
            {row.detail && <small className="sx-muted">{row.detail}</small>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SapSystemsCard() {
  const { t } = useTranslation();
  const systems = useSapSystems({ page: 1, page_size: 200 });
  const items = systems.data?.items ?? [];
  const count = (status: string) => items.filter((s) => s.status === status).length;
  const failing = items.filter((s) => s.last_test_ok === false || s.test_outdated).length;
  return (
    <Card className="sx-home__card">
      <Metric
        label={t('home.sapSystems')}
        value={systems.data ? systems.data.total : '—'}
        hint={
          systems.data?.total === 0
            ? t('home.sapNone')
            : failing > 0
              ? t('home.sapFailing', { count: failing })
              : t('home.sapHealthy')
        }
      >
        <div className="sx-row">
          <StatusPill tone="success">{t('home.sapActive', { count: count('active') })}</StatusPill>
          <StatusPill tone="info">{t('home.sapDraft', { count: count('draft') })}</StatusPill>
          <StatusPill tone="neutral">
            {t('home.sapInactive', { count: count('inactive') })}
          </StatusPill>
        </div>
      </Metric>
      <Link to="/sap-systems" className="sx-home__more">
        {t('home.seeSapSystems')}
      </Link>
    </Card>
  );
}

function UsersCard() {
  const { t } = useTranslation();
  const users = useUsers({ page: 1, page_size: 1, status: 'active' });
  return (
    <Card className="sx-home__card">
      <Metric
        label={t('home.activeUsers')}
        value={users.data ? users.data.total : '—'}
        hint={t('home.activeUsersHint')}
      />
      <Link to="/admin/users" className="sx-home__more">
        {t('home.seeUsers')}
      </Link>
    </Card>
  );
}

function AuditCard() {
  const { t } = useTranslation();
  const integrity = useAuditIntegrity();
  const last = integrity.data?.last_verification ?? null;
  return (
    <Card className="sx-home__card">
      <Metric
        label={t('home.audit')}
        value={last ? (last.ok ? t('home.auditOk') : t('home.auditBroken')) : t('home.auditNever')}
        hint={
          last
            ? t('home.auditChecked', {
                count: last.checked,
                date: formatDateTime(last.verified_at),
              })
            : t('home.auditNeverHint')
        }
      />
      <Link to="/admin/audit" className="sx-home__more">
        {t('home.seeAudit')}
      </Link>
    </Card>
  );
}

export function HomePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const info = useSystemInfo();
  const ready = useReadiness();
  const now = useNow();
  const canSap = useCan('sap_connection:read');
  const canCreateSap = useCan('sap_connection:manage');
  const canUsers = useCan('user:read');
  const canAudit = useCan('audit:read');
  const done = ROADMAP.filter((r) => r.state === 'on').length;

  return (
    <>
      {/* El entorno ya está en la cabecera: aquí solo la fecha. */}
      <PageHeader
        eyebrow={sentenceCase(eyebrowFormat.format(now))}
        title={t('home.title')}
        description={t('home.description')}
        actions={
          canCreateSap && (
            <Button
              icon={<IconPlus size={18} aria-hidden="true" />}
              onClick={() => void navigate('/sap-systems/new')}
            >
              {t('home.newSapSystem')}
            </Button>
          )
        }
      />
      {ready.isError && (
        <p role="alert">
          <StatusPill tone="danger">{t('home.unreachable')}</StatusPill>
        </p>
      )}
      <div className="sx-home__grid">
        <PlatformCard />
        {canSap && <SapSystemsCard />}
        {canUsers && <UsersCard />}
        {canAudit && <AuditCard />}
      </div>
      <div className="sx-home__grid sx-home__grid--wide">
        <Card className="sx-home__card">
          <Metric
            label={t('home.roadmap')}
            value={done}
            unit={t('home.roadmapUnit', { total: ROADMAP.length })}
            hint={t('home.roadmapNext')}
          >
            <Meter
              label={t('home.roadmap')}
              segments={ROADMAP.map((r) => ({
                key: r.key,
                state: r.state,
                title: `${t(`home.iterations.${r.key}`)} · ${t(`home.iterationStates.${r.state}`)}`,
              }))}
            />
          </Metric>
        </Card>
        <Card className="sx-home__card">
          <Metric label={t('home.version')} value={info.data?.version ?? '—'}>
            <dl className="sx-dl">
              <dt>{t('home.environment')}</dt>
              <dd>{info.data?.environment ?? '—'}</dd>
              <dt>{t('home.commit')}</dt>
              <dd>
                <code>{info.data?.build_commit ?? '—'}</code>
              </dd>
            </dl>
          </Metric>
        </Card>
      </div>
    </>
  );
}
