import type { TFunction } from 'i18next';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ALL_DAYS, WORKDAYS, simpleToCron, type ScheduleDraft } from '../schemas/schedule';

/** 1 de enero de 2024 fue lunes: sirve de base para pedir a `Intl` el nombre de cada día. */
const MONDAY = Date.UTC(2024, 0, 1);

export function weekdayNames(language: string, width: 'long' | 'short'): string[] {
  const format = new Intl.DateTimeFormat(language, { weekday: width, timeZone: 'UTC' });
  return ALL_DAYS.map((day) => format.format(new Date(MONDAY + day * 86_400_000)));
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export function weekdayLabels(language: string): string[] {
  return weekdayNames(language, 'long').map(capitalize);
}

const sameDays = (a: readonly number[], b: readonly number[]) =>
  a.length === b.length && b.every((day) => a.includes(day));

/** «todos los días», «de lunes a viernes» o «en lunes, miércoles y sábado». */
export function daysText(days: readonly number[], t: TFunction, language: string): string {
  if (sameDays(days, ALL_DAYS)) return t('pipelines.schedule.days.all');
  if (sameDays(days, WORKDAYS)) return t('pipelines.schedule.days.workdays');
  const names = weekdayNames(language, 'long');
  const list = new Intl.ListFormat(language, { type: 'conjunction' }).format(
    [...days].sort((a, b) => a - b).map((day) => names[day] ?? ''),
  );
  return t('pipelines.schedule.days.some', { list });
}

/** La programación dicha en una frase: «Cada 30 minutos, de lunes a viernes». */
export function scheduleText(draft: ScheduleDraft, t: TFunction, language: string): string {
  if (draft.mode === 'cron') {
    return t('pipelines.schedule.text.cron', { expression: draft.cron.trim() });
  }
  if (draft.mode === 'interval') {
    return t('pipelines.schedule.text.interval', { count: Number(draft.intervalMinutes) || 0 });
  }
  const days = daysText(draft.days, t, language);
  switch (draft.repeat) {
    case 'minutes':
      return t('pipelines.schedule.text.minutes', { count: draft.every, days });
    case 'hours':
      return t('pipelines.schedule.text.hours', { count: draft.every, days });
    case 'daily':
      return t('pipelines.schedule.text.daily', { time: draft.time, days });
    case 'monthly':
      return t('pipelines.schedule.text.monthly', { time: draft.time, day: draft.monthDay });
  }
}

/** Expresión cron que se enviará, para mostrarla junto a la frase. */
export function cronOf(draft: ScheduleDraft): string | null {
  if (draft.mode === 'interval') return null;
  return draft.mode === 'cron' ? draft.cron.trim() : simpleToCron(draft);
}

export function useScheduleText() {
  const { t, i18n } = useTranslation();
  const language = i18n.resolvedLanguage ?? 'es';
  return useMemo(
    () => ({
      language,
      describe: (draft: ScheduleDraft) => scheduleText(draft, t, language),
    }),
    [t, language],
  );
}

/** Fecha de una ejecución en la zona de la programación: «lun, 23 nov, 06:00». */
export function fireFormatter(language: string, timeZone: string): Intl.DateTimeFormat {
  const options: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  };
  try {
    return new Intl.DateTimeFormat(language, { ...options, timeZone });
  } catch {
    // Zona que el navegador no conoce: se muestra en la del usuario.
    return new Intl.DateTimeFormat(language, options);
  }
}

let zones: string[] | null = null;
export function timezoneOptions(): string[] {
  zones ??= ['UTC', ...Intl.supportedValuesOf('timeZone').filter((zone) => zone !== 'UTC')];
  return zones;
}

/** «Todas, con el nombre de SAP» o «1 excluida y 2 renombradas». */
export function mappingText(
  excluded: number,
  renamed: number,
  t: TFunction,
  language: string,
): string {
  const parts = [
    ...(excluded > 0 ? [t('pipelines.mapping.excluded', { count: excluded })] : []),
    ...(renamed > 0 ? [t('pipelines.mapping.renamed', { count: renamed })] : []),
  ];
  if (parts.length === 0) return t('pipelines.mapping.all');
  return new Intl.ListFormat(language, { type: 'conjunction' }).format(parts);
}
