/**
 * Menú principal.
 * - `iteration`: iteración del ROADMAP en la que se habilita; mientras tanto se ve deshabilitada.
 * - `permission`: la opción solo aparece si el usuario tiene ese permiso (la API también lo exige).
 */
export interface NavItem {
  to: string;
  labelKey: string;
  iteration?: number;
  permission?: string;
}

export interface NavSection {
  titleKey?: string;
  items: NavItem[];
}

export const navigation: NavSection[] = [
  { items: [{ to: '/', labelKey: 'nav.home' }] },
  {
    titleKey: 'nav.sectionOperation',
    items: [
      { to: '/pipelines', labelKey: 'nav.pipelines', iteration: 6 },
      { to: '/runs', labelKey: 'nav.runs', iteration: 5 },
      { to: '/alerts', labelKey: 'nav.alerts', iteration: 8 },
    ],
  },
  {
    titleKey: 'nav.sectionData',
    items: [
      { to: '/sap-systems', labelKey: 'nav.sapSystems', permission: 'sap_connection:read' },
      { to: '/sources', labelKey: 'nav.sources', iteration: 3 },
      { to: '/destinations', labelKey: 'nav.destinations', iteration: 4 },
    ],
  },
  {
    titleKey: 'nav.sectionAdmin',
    items: [
      { to: '/admin/users', labelKey: 'nav.users', permission: 'user:read' },
      { to: '/admin/roles', labelKey: 'nav.roles', permission: 'role:read' },
      { to: '/admin/api-tokens', labelKey: 'nav.apiTokens', permission: 'api_token:manage' },
      { to: '/admin/audit', labelKey: 'nav.audit', permission: 'audit:read' },
      { to: '/admin/identity-provider', labelKey: 'nav.identityProvider', iteration: 7 },
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
