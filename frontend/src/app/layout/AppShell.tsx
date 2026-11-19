import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet } from 'react-router';
import { useLogout, useMe } from '@/features/auth/api/queries';
import { useSystemInfo } from '@/features/system/api/queries';
import { Button, StatusPill } from '@/shared/ui';
import { visibleNavigation } from './navigation';
import './layout.css';

/** Layout PROVISIONAL: se sustituirá por el de la plantilla corporativa (iteración 1b). */
export function AppShell() {
  const { t } = useTranslation();
  const info = useSystemInfo();
  const me = useMe();
  const logout = useLogout();
  const sections = visibleNavigation(me.data?.permissions ?? []);

  return (
    <div className="sx-shell">
      <a className="sx-skip" href="#sx-main">
        {t('app.skipToContent')}
      </a>
      <aside className="sx-sidebar" aria-label={t('nav.menu')}>
        <Link to="/" className="sx-brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <span>
            <strong>{t('app.name')}</strong>
            <small>{t('app.tagline')}</small>
          </span>
        </Link>
        <nav>
          {sections.map((section, i) => (
            <div key={section.titleKey ?? i} className="sx-nav-section">
              {section.titleKey && <h2>{t(section.titleKey)}</h2>}
              <ul>
                {section.items.map((item) => (
                  <li key={item.to}>
                    {item.iteration === undefined ? (
                      <NavLink to={item.to} end={item.to === '/'} className="sx-nav-link">
                        {t(item.labelKey)}
                      </NavLink>
                    ) : (
                      <span className="sx-nav-link sx-nav-link--disabled" aria-disabled="true">
                        {t(item.labelKey)}
                        <small>{t('nav.soon', { it: item.iteration })}</small>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>
      <div className="sx-main-area">
        <header className="sx-topbar">
          {info.data && (
            <StatusPill tone={info.data.environment === 'prd' ? 'danger' : 'info'}>
              {info.data.environment.toUpperCase()}
            </StatusPill>
          )}
          {me.data && (
            <div className="sx-usermenu">
              <Link to="/profile" className="sx-usermenu__name">
                {me.data.display_name}
                <small>{me.data.username}</small>
              </Link>
              <Button
                variant="secondary"
                onClick={() => {
                  logout.mutate();
                }}
                disabled={logout.isPending}
              >
                {t('userMenu.logout')}
              </Button>
            </div>
          )}
        </header>
        <main id="sx-main" className="sx-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
