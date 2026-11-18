import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet } from 'react-router';
import { useSystemInfo } from '@/features/system/api/queries';
import { StatusPill } from '@/shared/ui';
import { navigation } from './navigation';
import './layout.css';

/** Layout PROVISIONAL: se sustituirá por el de la plantilla corporativa (iteración 1b). */
export function AppShell() {
  const { t } = useTranslation();
  const info = useSystemInfo();

  return (
    <div className="sx-shell">
      <a className="sx-skip" href="#sx-main">
        {t('app.skipToContent')}
      </a>
      <aside className="sx-sidebar" aria-label="Menú principal">
        <Link to="/" className="sx-brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <span>
            <strong>{t('app.name')}</strong>
            <small>{t('app.tagline')}</small>
          </span>
        </Link>
        <nav>
          {navigation.map((section, i) => (
            <div key={section.titleKey ?? i} className="sx-nav-section">
              {section.titleKey && <h2>{t(section.titleKey)}</h2>}
              <ul>
                {section.items.map((item) => (
                  <li key={item.to}>
                    {item.iteration === undefined ? (
                      <NavLink to={item.to} end className="sx-nav-link">
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
          <Link to="/login">{t('login.title')}</Link>
        </header>
        <main id="sx-main" className="sx-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
