import type {
  BlackoutWindowBody,
  ScheduleBody,
  SchedulePreviewRequest,
  ScheduleResponse,
} from '../api/queries';

/**
 * Programación tal como la edita el usuario (plan 08 §6): frases sencillas, cron o un
 * intervalo fijo. La API solo entiende `cron` e `interval`; aquí se traduce en los dos sentidos.
 * `dependency` no lleva reloj: el pipeline se lanza cuando otro termina con éxito, y es la
 * única programación que puede tener.
 *
 * Días de la semana: 0 = lunes … 6 = domingo, igual que las ventanas de exclusión de la API.
 */
export type ScheduleMode = 'simple' | 'cron' | 'interval' | 'dependency';
export type Repeat = 'minutes' | 'hours' | 'daily' | 'monthly';
export type MisfirePolicy = ScheduleBody['misfire_policy'];

export const MINUTE_STEPS = [5, 10, 15, 20, 30] as const;
export const HOUR_STEPS = [1, 2, 3, 4, 6, 8, 12] as const;
export const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6] as const;
export const WORKDAYS = [0, 1, 2, 3, 4] as const;
export const MAX_SCHEDULES = 5;
export const MAX_BLACKOUTS = 20;
export const PREVIEW_COUNT = 10;

export interface BlackoutDraft {
  key: string;
  kind: 'weekly' | 'range';
  label: string;
  days: number[];
  /** Semanal: horas locales de la programación (HH:MM); el fin puede ser del día siguiente. */
  start: string;
  end: string;
  /** Entre fechas: valores de `<input type="datetime-local">`, en la zona del navegador. */
  startAt: string;
  endAt: string;
}

export interface ScheduleDraft {
  key: string;
  mode: ScheduleMode;
  repeat: Repeat;
  every: number;
  time: string;
  days: number[];
  monthDay: number;
  cron: string;
  intervalMinutes: string;
  /** Zona IANA; vacío = la de la plataforma. */
  timezone: string;
  misfire: MisfirePolicy;
  enabled: boolean;
  blackouts: BlackoutDraft[];
  /** Vigencia fijada por API: la pantalla no la edita, pero la conserva al guardar. */
  startAt: string | null;
  endAt: string | null;
  /** Modo `dependency`: pipeline anterior (vacío = sin elegir) y cómo nombrarlo. */
  upstreamId: string;
  upstreamCode: string;
}

let counter = 0;
const nextKey = (prefix: string) => `${prefix}-${String(++counter)}`;

/** «Cada 30 minutos, de lunes a viernes»: el caso más pedido como punto de partida. */
export function newSchedule(): ScheduleDraft {
  return {
    key: nextKey('schedule'),
    mode: 'simple',
    repeat: 'daily',
    every: 30,
    time: '06:00',
    days: [...ALL_DAYS],
    monthDay: 1,
    cron: '0 6 * * *',
    intervalMinutes: '60',
    timezone: '',
    misfire: 'skip',
    enabled: true,
    blackouts: [],
    startAt: null,
    endAt: null,
    upstreamId: '',
    upstreamCode: '',
  };
}

/** «Después de otro pipeline», con el pipeline anterior aún sin elegir. */
export function newDependency(): ScheduleDraft {
  return { ...newSchedule(), mode: 'dependency' };
}

export const isDependency = (drafts: ScheduleDraft[]) => drafts[0]?.mode === 'dependency';

export function newBlackout(kind: BlackoutDraft['kind']): BlackoutDraft {
  return {
    key: nextKey('blackout'),
    kind,
    label: '',
    days: kind === 'weekly' ? [...ALL_DAYS] : [],
    start: '22:00',
    end: '02:00',
    startAt: '',
    endAt: '',
  };
}

// ------------------------------------------------------------------ cron

/** Lista de días en la numeración de cron (0 = domingo), con rangos: lunes a viernes → `1-5`. */
export function cronDays(days: readonly number[]): string {
  const unique = [...new Set(days)].filter((d) => d >= 0 && d <= 6);
  if (unique.length === 0 || unique.length === 7) return '*';
  const cron = unique.map((d) => (d + 1) % 7).sort((a, b) => a - b);
  const parts: string[] = [];
  let start = cron[0] ?? 0;
  let previous = start;
  for (const day of [...cron.slice(1), Number.NaN]) {
    if (day === previous + 1) {
      previous = day;
      continue;
    }
    parts.push(start === previous ? String(start) : `${String(start)}-${String(previous)}`);
    start = day;
    previous = day;
  }
  return parts.join(',');
}

/** Campo «día de la semana» de cron → nuestros días (0 = lunes); `null` si no se entiende. */
export function parseCronDays(field: string): number[] | null {
  if (field === '*') return [...ALL_DAYS];
  const days = new Set<number>();
  for (const part of field.split(',')) {
    const match = /^([0-7])(?:-([0-7]))?$/.exec(part);
    if (!match) return null;
    const from = Number(match[1]);
    const to = match[2] === undefined ? from : Number(match[2]);
    if (to < from) return null;
    for (let day = from; day <= to; day += 1) days.add((day + 6) % 7);
  }
  return [...days].sort((a, b) => a - b);
}

function splitTime(time: string): [number, number] | null {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(time);
  return match ? [Number(match[1]), Number(match[2])] : null;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** Expresión cron de una programación en modo sencillo. */
export function simpleToCron(draft: ScheduleDraft): string {
  const days = cronDays(draft.days);
  const [hour, minute] = splitTime(draft.time) ?? [0, 0];
  switch (draft.repeat) {
    case 'minutes':
      return `*/${String(draft.every)} * * * ${days}`;
    case 'hours':
      return draft.every === 1 ? `0 * * * ${days}` : `0 */${String(draft.every)} * * ${days}`;
    case 'daily':
      return `${String(minute)} ${String(hour)} * * ${days}`;
    case 'monthly':
      return `${String(minute)} ${String(hour)} ${String(draft.monthDay)} * *`;
  }
}

type SimpleShape = Pick<ScheduleDraft, 'repeat' | 'every' | 'time' | 'days' | 'monthDay'>;

/** Reconoce las expresiones que el modo sencillo sabe escribir; el resto se edita como cron. */
export function cronToSimple(expression: string): SimpleShape | null {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute = '', hour = '', dayOfMonth = '', month = '', dayOfWeek = ''] = fields;
  if (month !== '*') return null;
  const base = { every: 30, time: '06:00', monthDay: 1 };

  if (dayOfMonth !== '*') {
    if (dayOfWeek !== '*' || !/^\d{1,2}$/.test(dayOfMonth)) return null;
    if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return null;
    const day = Number(dayOfMonth);
    if (day < 1 || day > 31 || Number(hour) > 23 || Number(minute) > 59) return null;
    return {
      ...base,
      repeat: 'monthly',
      time: `${pad(Number(hour))}:${pad(Number(minute))}`,
      days: [...ALL_DAYS],
      monthDay: day,
    };
  }

  const days = parseCronDays(dayOfWeek);
  if (!days || days.length === 0) return null;

  const everyMinutes = /^\*\/(\d{1,2})$/.exec(minute);
  if (everyMinutes && hour === '*') {
    const step = Number(everyMinutes[1]);
    if (!(MINUTE_STEPS as readonly number[]).includes(step)) return null;
    return { ...base, repeat: 'minutes', every: step, days };
  }
  if (minute === '0' && hour === '*') return { ...base, repeat: 'hours', every: 1, days };
  const everyHours = /^\*\/(\d{1,2})$/.exec(hour);
  if (minute === '0' && everyHours) {
    const step = Number(everyHours[1]);
    if (!(HOUR_STEPS as readonly number[]).includes(step)) return null;
    return { ...base, repeat: 'hours', every: step, days };
  }
  if (/^\d{1,2}$/.test(minute) && /^\d{1,2}$/.test(hour)) {
    if (Number(hour) > 23 || Number(minute) > 59) return null;
    return {
      ...base,
      repeat: 'daily',
      time: `${pad(Number(hour))}:${pad(Number(minute))}`,
      days,
    };
  }
  return null;
}

// ------------------------------------------------------------------ fechas de las ventanas

/** ISO con zona → valor de `datetime-local` en la zona del navegador. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return (
    `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** Valor de `datetime-local` (zona del navegador) → ISO en UTC; `null` si está vacío. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

// ------------------------------------------------------------------ validación

export type ScheduleIssue =
  | 'days'
  | 'time'
  | 'cron'
  | 'interval'
  | 'monthDay'
  | 'upstream'
  | 'blackoutDays'
  | 'blackoutHours'
  | 'blackoutRange';

/** Primer problema de formato de una programación; las reglas de cron las valida la API. */
export function scheduleIssue(draft: ScheduleDraft): ScheduleIssue | null {
  if (draft.mode === 'dependency') {
    if (!draft.upstreamId) return 'upstream';
  } else if (draft.mode === 'cron') {
    if (draft.cron.trim().split(/\s+/).length !== 5) return 'cron';
  } else if (draft.mode === 'interval') {
    const minutes = Number(draft.intervalMinutes);
    if (!/^\d+$/.test(draft.intervalMinutes) || minutes < 1 || minutes > 44_640) return 'interval';
  } else {
    if (draft.repeat !== 'monthly' && draft.days.length === 0) return 'days';
    if ((draft.repeat === 'daily' || draft.repeat === 'monthly') && !splitTime(draft.time)) {
      return 'time';
    }
    if (draft.repeat === 'monthly' && (draft.monthDay < 1 || draft.monthDay > 31)) {
      return 'monthDay';
    }
  }
  for (const window of draft.blackouts) {
    if (window.kind === 'weekly') {
      if (window.days.length === 0) return 'blackoutDays';
      if (!splitTime(window.start) || !splitTime(window.end) || window.start === window.end) {
        return 'blackoutHours';
      }
    } else {
      const start = fromLocalInput(window.startAt);
      const end = fromLocalInput(window.endAt);
      if (!start || !end || end <= start) return 'blackoutRange';
    }
  }
  return null;
}

/** ¿Todas las programaciones tienen un formato válido? (el asistente no avanza con errores) */
export function schedulesValid(drafts: ScheduleDraft[]): boolean {
  return drafts.every((draft) => scheduleIssue(draft) === null);
}

// ------------------------------------------------------------------ API

function blackoutBody(window: BlackoutDraft): BlackoutWindowBody {
  const label = window.label.trim() || null;
  if (window.kind === 'weekly') {
    return {
      kind: 'weekly',
      label,
      days: [...window.days].sort((a, b) => a - b),
      start: window.start,
      end: window.end,
    };
  }
  return {
    kind: 'range',
    label,
    start_at: fromLocalInput(window.startAt),
    end_at: fromLocalInput(window.endAt),
  };
}

export function toScheduleBody(draft: ScheduleDraft): ScheduleBody {
  if (draft.mode === 'dependency') {
    // Sin reloj: solo cuentan el pipeline anterior, las exclusiones y si está en marcha.
    return {
      type: 'dependency',
      upstream_pipeline_id: draft.upstreamId || null,
      timezone: draft.timezone || null,
      blackout_windows: draft.blackouts.map(blackoutBody),
      misfire_policy: 'skip',
      enabled: draft.enabled,
    };
  }
  const common = {
    timezone: draft.timezone || null,
    start_at: draft.startAt,
    end_at: draft.endAt,
    blackout_windows: draft.blackouts.map(blackoutBody),
    misfire_policy: draft.misfire,
    enabled: draft.enabled,
  };
  if (draft.mode === 'interval') {
    return { ...common, type: 'interval', interval_minutes: Number(draft.intervalMinutes) };
  }
  const cron =
    draft.mode === 'cron' ? draft.cron.trim().split(/\s+/).join(' ') : simpleToCron(draft);
  return { ...common, type: 'cron', cron_expression: cron };
}

/** Petición de vista previa, o `null` si la programación aún no tiene un formato válido. */
export function toPreviewRequest(draft: ScheduleDraft): SchedulePreviewRequest | null {
  if (draft.mode === 'dependency' || scheduleIssue(draft) !== null) return null;
  const body = toScheduleBody(draft);
  return {
    type: body.type,
    cron_expression: body.cron_expression ?? null,
    interval_minutes: body.interval_minutes ?? null,
    timezone: body.timezone ?? null,
    blackout_windows: body.blackout_windows ?? [],
    misfire_policy: body.misfire_policy,
    enabled: true,
    count: PREVIEW_COUNT,
  };
}

function blackoutDraft(raw: Record<string, unknown>): BlackoutDraft {
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  const base = newBlackout(raw.kind === 'range' ? 'range' : 'weekly');
  if (base.kind === 'range') {
    return {
      ...base,
      label: text(raw.label),
      startAt: toLocalInput(text(raw.start_at)),
      endAt: toLocalInput(text(raw.end_at)),
    };
  }
  const days = Array.isArray(raw.days)
    ? raw.days.filter((d): d is number => typeof d === 'number')
    : [];
  return {
    ...base,
    label: text(raw.label),
    days,
    start: text(raw.start).slice(0, 5),
    end: text(raw.end).slice(0, 5),
  };
}

/** Programaciones guardadas → borradores editables. Las «solo manual» no se editan: se omiten. */
export function fromSchedules(schedules: ScheduleResponse[]): ScheduleDraft[] {
  return schedules
    .filter((s) => s.type !== 'manual_only')
    .map((s) => {
      const base: ScheduleDraft = {
        ...newSchedule(),
        timezone: s.timezone,
        misfire: s.misfire_policy,
        enabled: s.enabled,
        blackouts: s.blackout_windows.map(blackoutDraft),
        startAt: s.start_at,
        endAt: s.end_at,
      };
      if (s.type === 'dependency') {
        return {
          ...base,
          mode: 'dependency',
          upstreamId: s.upstream?.id ?? '',
          upstreamCode: s.upstream?.code ?? '',
        };
      }
      if (s.type === 'interval') {
        return { ...base, mode: 'interval', intervalMinutes: String(s.interval_minutes ?? 60) };
      }
      const cron = s.cron_expression ?? '';
      const simple = cronToSimple(cron);
      return simple
        ? { ...base, ...simple, mode: 'simple', cron }
        : { ...base, mode: 'cron', cron };
    });
}

// ------------------------------------------------------------------ banda semanal

export interface BandSegment {
  /** Día (0 = lunes). */
  day: number;
  /** Minutos desde medianoche. */
  from: number;
  to: number;
}

/** Tramos excluidos de cada día de la semana; una ventana que cruza medianoche ocupa dos días. */
export function weeklySegments(blackouts: BlackoutDraft[]): BandSegment[] {
  const segments: BandSegment[] = [];
  for (const window of blackouts) {
    if (window.kind !== 'weekly') continue;
    const start = splitTime(window.start);
    const end = splitTime(window.end);
    if (!start || !end) continue;
    const from = start[0] * 60 + start[1];
    const to = end[0] * 60 + end[1];
    if (from === to) continue;
    for (const day of window.days) {
      if (to > from) {
        segments.push({ day, from, to });
      } else {
        segments.push({ day, from, to: 1440 });
        if (to > 0) segments.push({ day: (day + 1) % 7, from: 0, to });
      }
    }
  }
  return segments;
}
