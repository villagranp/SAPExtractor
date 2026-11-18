/**
 * Menú principal. `iteration` indica en qué iteración del ROADMAP se habilita la opción;
 * mientras tanto se muestra deshabilitada con la etiqueta correspondiente.
 */
export interface NavItem {
  to: string;
  labelKey: string;
  iteration?: number;
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
      { to: '/sap-systems', labelKey: 'nav.sapSystems', iteration: 2 },
      { to: '/sources', labelKey: 'nav.sources', iteration: 3 },
      { to: '/destinations', labelKey: 'nav.destinations', iteration: 4 },
    ],
  },
  {
    titleKey: 'nav.sectionAdmin',
    items: [
      { to: '/admin/users', labelKey: 'nav.users', iteration: 1 },
      { to: '/admin/audit', labelKey: 'nav.audit', iteration: 1 },
      { to: '/admin/settings', labelKey: 'nav.settings', iteration: 1 },
    ],
  },
];
