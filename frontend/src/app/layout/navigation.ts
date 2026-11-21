import {
  IconArrowsShuffle,
  IconBell,
  IconLayoutDashboard,
  IconChecklist,
  IconDatabaseExport,
  IconDatabaseImport,
  IconHome,
  IconId,
  IconKey,
  IconPlayerPlay,
  IconServer2,
  IconShieldLock,
  IconUsers,
  type IconComponent,
} from '@/shared/ui';

/**
 * Menú principal.
 * - `iteration`: iteración del ROADMAP en la que se habilita; mientras tanto se ve deshabilitada.
 * - `permission`: la opción solo aparece si el usuario tiene ese permiso (la API también lo exige).
 */
export interface NavItem {
  to: string;
  labelKey: string;
  icon: IconComponent;
  iteration?: number;
  permission?: string;
}

export interface NavSection {
  titleKey?: string;
  items: NavItem[];
}

export const navigation: NavSection[] = [
  { items: [{ to: '/', labelKey: 'nav.home', icon: IconHome }] },
  {
    titleKey: 'nav.sectionOperation',
    items: [
      {
        to: '/dashboard',
        labelKey: 'nav.dashboard',
        icon: IconLayoutDashboard,
        permission: 'dashboard:read',
      },
      {
        to: '/pipelines',
        labelKey: 'nav.pipelines',
        icon: IconArrowsShuffle,
        permission: 'pipeline:read',
      },
      { to: '/runs', labelKey: 'nav.runs', icon: IconPlayerPlay, permission: 'pipeline:read' },
      { to: '/alerts', labelKey: 'nav.alerts', icon: IconBell, permission: 'dashboard:read' },
    ],
  },
  {
    titleKey: 'nav.sectionData',
    items: [
      {
        to: '/sap-systems',
        labelKey: 'nav.sapSystems',
        icon: IconServer2,
        permission: 'sap_connection:read',
      },
      {
        to: '/sources',
        labelKey: 'nav.sources',
        icon: IconDatabaseImport,
        permission: 'source:read',
      },
      {
        to: '/destinations',
        labelKey: 'nav.destinations',
        icon: IconDatabaseExport,
        permission: 'destination:read',
      },
    ],
  },
  {
    titleKey: 'nav.sectionAdmin',
    items: [
      { to: '/admin/users', labelKey: 'nav.users', icon: IconUsers, permission: 'user:read' },
      { to: '/admin/roles', labelKey: 'nav.roles', icon: IconShieldLock, permission: 'role:read' },
      {
        to: '/admin/api-tokens',
        labelKey: 'nav.apiTokens',
        icon: IconKey,
        permission: 'api_token:manage',
      },
      { to: '/admin/audit', labelKey: 'nav.audit', icon: IconChecklist, permission: 'audit:read' },
      {
        to: '/admin/identity-provider',
        labelKey: 'nav.identityProvider',
        icon: IconId,
        permission: 'idp:manage',
      },
    ],
  },
];

/** Secciones visibles para un conjunto de permisos (sin secciones vacías). */
export function visibleNavigation(permissions: readonly string[]): NavSection[] {
  return navigation
    .map((section) => ({
      ...section,
      items: section.items.filter(
        (item) => item.permission === undefined || permissions.includes(item.permission),
      ),
    }))
    .filter((section) => section.items.length > 0);
}

/** Elemento del menú que corresponde a una ruta (el de prefijo más largo) y su sección. */
export function locate(
  pathname: string,
  sections: NavSection[] = navigation,
): { section: NavSection; item: NavItem } | null {
  let best: { section: NavSection; item: NavItem } | null = null;
  for (const section of sections) {
    for (const item of section.items) {
      const matches =
        item.to === '/'
          ? pathname === '/'
          : pathname === item.to || pathname.startsWith(`${item.to}/`);
      if (matches && (!best || item.to.length > best.item.to.length)) best = { section, item };
    }
  }
  return best;
}
