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
