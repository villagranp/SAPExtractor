import { useCallback, useSyncExternalStore } from 'react';

/*
 * Preferencias de apariencia del usuario en este navegador: tema y densidad.
 * Se aplican como atributos de <html> (`data-theme`, `data-density`). `public/theme-init.js`
 * repite la lectura antes del primer pintado para que la página no parpadee al cargar.
 */

export type ThemePreference = 'system' | 'light' | 'dark';
export type DensityPreference = 'comfortable' | 'compact';

export interface Preferences {
  theme: ThemePreference;
  density: DensityPreference;
}

export const PREFERENCES_KEY = 'sx-preferences';
const DEFAULTS: Preferences = { theme: 'system', density: 'comfortable' };
const listeners = new Set<() => void>();
let current: Preferences | null = null;

function sanitize(value: unknown): Preferences {
  const raw = (typeof value === 'object' && value !== null ? value : {}) as Partial<Preferences>;
  return {
    theme: raw.theme === 'light' || raw.theme === 'dark' ? raw.theme : 'system',
    density: raw.density === 'compact' ? 'compact' : 'comfortable',
  };
}

export function readPreferences(): Preferences {
  try {
    const stored = window.localStorage.getItem(PREFERENCES_KEY);
    return stored ? sanitize(JSON.parse(stored)) : DEFAULTS;
  } catch {
    return DEFAULTS; // almacenamiento bloqueado o JSON corrupto
  }
}

export function applyPreferences(prefs: Preferences, root: HTMLElement = document.documentElement) {
  if (prefs.theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', prefs.theme);
  if (prefs.density === 'compact') root.setAttribute('data-density', 'compact');
  else root.removeAttribute('data-density');
}

function snapshot(): Preferences {
  current ??= readPreferences();
  return current;
}

export function savePreferences(next: Preferences) {
  current = sanitize(next);
  try {
    window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(current));
  } catch {
    // sin almacenamiento: la preferencia dura hasta recargar
  }
  applyPreferences(current);
  listeners.forEach((listener) => {
    listener();
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Preferencias actuales y una función para cambiar una o varias. */
export function usePreferences(): [Preferences, (change: Partial<Preferences>) => void] {
  const prefs = useSyncExternalStore(subscribe, snapshot, () => DEFAULTS);
  const update = useCallback((change: Partial<Preferences>) => {
    savePreferences({ ...snapshot(), ...change });
  }, []);
  return [prefs, update];
}

/** Para pruebas: olvida la caché en memoria. */
export function resetPreferencesCache() {
  current = null;
}
