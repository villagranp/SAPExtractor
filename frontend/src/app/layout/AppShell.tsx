import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet, useLocation } from 'react-router';
import { useLogout, useMe } from '@/features/auth/api/queries';
import { useSystemInfo } from '@/features/system/api/queries';
import { usePreferences } from '@/shared/theme/preferences';
import { IconLogout, IconMoon, IconSun, StatusPill } from '@/shared/ui';
import { locate, visibleNavigation } from './navigation';
import './layout.css';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters =
    parts.length > 1 ? `${parts[0]?.[0] ?? ''}${parts.at(-1)?.[0] ?? ''}` : name.slice(0, 2);
  return letters.toUpperCase();
}

/** ¿Se ve ahora el tema grafito? (preferencia explícita o, si sigue al sistema, el del SO). */
function isDarkNow(theme: 'system' | 'light' | 'dark'): boolean {
  if (theme !== 'system') return theme === 'dark';
  // matchMedia puede no existir (navegadores antiguos, entorno de pruebas).
  return typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false;
}

/** Layout de la aplicación: carril de teclas a la izquierda y cabecera con la ruta actual. */
export function AppShell() {
  const { t } = useTranslation();
  const info = useSystemInfo();
  const me = useMe();
  const logout = useLogout();
  const location = useLocation();
  const [prefs, setPrefs] = usePreferences();
  const sections = visibleNavigation(me.data?.permissions ?? []);
  const current = locate(location.pathname, sections);
  const dark = isDarkNow(prefs.theme);
  const environment = info.data?.environment;

  return (
    <div className="sx-shell">
      <a className="sx-skip" href="#sx-main">
        {t('app.skipToContent')}
      </a>
      <aside className="sx-rail" aria-label={t('nav.menu')}>
        <Link to="/" className="sx-rail__brand">
          <span aria-hidden="true">SX</span>
          <span className="sx-visually-hidden">{t('app.homeLink')}</span>
        </Link>
        <nav className="sx-rail__nav">
          {sections.map((section, i) => (
            <ul
              key={section.titleKey ?? i}
              className="sx-rail__group"
              aria-label={section.titleKey ? t(section.titleKey) : undefined}
            >
              {section.items.map((item) => {
                const Icon = item.icon;
                const label = t(item.labelKey);
                return (
                  <li key={item.to}>
                    {item.iteration === undefined ? (
                      <NavLink to={item.to} end={item.to === '/'} className="sx-rail__key">
                        <Icon size={20} stroke={1.75} aria-hidden="true" />
                        <span>{label}</span>
                      </NavLink>
                    ) : (
                      <span
                        className="sx-rail__key sx-rail__key--soon"
                        aria-disabled="true"
                        title={t('nav.soon', { it: item.iteration })}
                      >
                        <Icon size={20} stroke={1.75} aria-hidden="true" />
                        <span>{label}</span>
                        <small className="sx-rail__badge">
                          {t('nav.soonShort', { it: item.iteration })}
                        </small>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}
        </nav>
        {me.data && (
          <Link to="/profile" className="sx-rail__avatar">
            <span aria-hidden="true">{initials(me.data.display_name)}</span>
            <span className="sx-visually-hidden">
              {t('userMenu.profileOf', { name: me.data.display_name })}
            </span>
          </Link>
        )}
      </aside>

      <div className="sx-main-area">
        <header className="sx-topbar">
          <nav aria-label={t('nav.breadcrumb')} className="sx-crumbs">
            <ol>
              <li>
                <Link to="/">{t('app.name')}</Link>
              </li>
              {current?.section.titleKey && <li>{t(current.section.titleKey)}</li>}
              {current && current.item.to !== '/' && (
                <li>
                  <Link
                    to={current.item.to}
                    aria-current={location.pathname === current.item.to ? 'page' : undefined}
                  >
                    {t(current.item.labelKey)}
                  </Link>
                </li>
              )}
            </ol>
          </nav>
          <div className="sx-topbar__actions">
            {environment && (
              <StatusPill
                tone={environment === 'prd' ? 'danger' : environment === 'qa' ? 'warning' : 'info'}
              >
                {environment.toUpperCase()}
              </StatusPill>
            )}
            <button
              type="button"
              className="sx-iconkey"
              aria-label={dark ? t('userMenu.lightTheme') : t('userMenu.darkTheme')}
              title={dark ? t('userMenu.lightTheme') : t('userMenu.darkTheme')}
              onClick={() => {
                setPrefs({ theme: dark ? 'light' : 'dark' });
              }}
            >
              {dark ? (
                <IconSun size={18} aria-hidden="true" />
              ) : (
                <IconMoon size={18} aria-hidden="true" />
              )}
            </button>
            {me.data && (
              <>
                <Link to="/profile" className="sx-usermenu__name">
                  {me.data.display_name}
                  <small>{me.data.username}</small>
                </Link>
                <button
                  type="button"
                  className="sx-iconkey"
                  aria-label={t('userMenu.logout')}
                  title={t('userMenu.logout')}
                  onClick={() => {
                    logout.mutate();
                  }}
                  disabled={logout.isPending}
                >
                  <IconLogout size={18} aria-hidden="true" />
                </button>
              </>
            )}
          </div>
        </header>
        <main id="sx-main" className="sx-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
