import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  PREFERENCES_KEY,
  applyPreferences,
  readPreferences,
  resetPreferencesCache,
  usePreferences,
} from './preferences';

const root = document.documentElement;

afterEach(() => {
  window.localStorage.clear();
  resetPreferencesCache();
  root.removeAttribute('data-theme');
  root.removeAttribute('data-density');
});

describe('preferencias de apariencia', () => {
  it('por defecto sigue al sistema con densidad cómoda', () => {
    expect(readPreferences()).toEqual({ theme: 'system', density: 'comfortable' });
  });

  it('ignora valores guardados no válidos', () => {
    window.localStorage.setItem(PREFERENCES_KEY, '{"theme":"neon","density":"x"}');
    expect(readPreferences()).toEqual({ theme: 'system', density: 'comfortable' });
    window.localStorage.setItem(PREFERENCES_KEY, 'no es json');
    expect(readPreferences()).toEqual({ theme: 'system', density: 'comfortable' });
  });

  it('aplica los atributos en <html> y los quita al volver a los valores por defecto', () => {
    applyPreferences({ theme: 'dark', density: 'compact' });
    expect(root.dataset.theme).toBe('dark');
    expect(root.dataset.density).toBe('compact');
    applyPreferences({ theme: 'system', density: 'comfortable' });
    expect(root.hasAttribute('data-theme')).toBe(false);
    expect(root.hasAttribute('data-density')).toBe(false);
  });

  it('el hook guarda, aplica y notifica los cambios', () => {
    const { result } = renderHook(() => usePreferences());
    act(() => {
      result.current[1]({ theme: 'light' });
    });
    expect(result.current[0]).toEqual({ theme: 'light', density: 'comfortable' });
    expect(root.dataset.theme).toBe('light');
    expect(JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) ?? '{}')).toEqual({
      theme: 'light',
      density: 'comfortable',
    });
    act(() => {
      result.current[1]({ density: 'compact' });
    });
    expect(result.current[0]).toEqual({ theme: 'light', density: 'compact' });
  });
});
