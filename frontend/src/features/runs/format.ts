import { useTranslation } from 'react-i18next';
import type { RunMode } from './api/queries';

export function useModeLabel() {
  const { t } = useTranslation();
  return (mode: RunMode) => t(`runs.modes.${mode}`);
}

/** Cifras con el formato del idioma de la interfaz («2.500», «0,1 s»), o «—» sin dato. */
export function useNumbers() {
  const { i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? 'es';
  const integer = new Intl.NumberFormat(locale);
  const seconds = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  return {
    rows: (value: number | null | undefined) =>
      value === null || value === undefined ? '—' : integer.format(value),
    seconds: (ms: unknown) => (typeof ms === 'number' ? `${seconds.format(ms / 1000)} s` : '—'),
  };
}

/** Duración entre dos instantes: «45 s», «3 min 05 s» o «2 h 10 min»; '—' si aún no empezó. */
export function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!start) return '—';
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : now;
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${String(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${String(minutes)} min ${String(seconds % 60).padStart(2, '0')} s`;
  return `${String(Math.floor(minutes / 60))} h ${String(minutes % 60).padStart(2, '0')} min`;
}
