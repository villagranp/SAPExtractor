import { describe, expect, it } from 'vitest';
import { dependencySchedule, weekdaySchedule } from '../test/fixtures';
import {
  ALL_DAYS,
  WORKDAYS,
  cronDays,
  cronToSimple,
  fromSchedules,
  newBlackout,
  newDependency,
  newSchedule,
  parseCronDays,
  scheduleIssue,
  simpleToCron,
  toPreviewRequest,
  toScheduleBody,
  weeklySegments,
  type ScheduleDraft,
} from './schedule';

const simple = (changes: Partial<ScheduleDraft>): ScheduleDraft => ({
  ...newSchedule(),
  ...changes,
});

describe('días de la semana en cron', () => {
  it('escribe rangos y listas con el domingo como 0', () => {
    expect(cronDays(ALL_DAYS)).toBe('*');
    expect(cronDays(WORKDAYS)).toBe('1-5');
    expect(cronDays([5, 6])).toBe('0,6');
    expect(cronDays([0, 2, 4])).toBe('1,3,5');
    expect(cronDays([0, 1, 2, 5])).toBe('1-3,6');
  });

  it('lee listas, rangos y el domingo escrito como 0 o como 7', () => {
    expect(parseCronDays('*')).toEqual([...ALL_DAYS]);
    expect(parseCronDays('1-5')).toEqual([...WORKDAYS]);
    expect(parseCronDays('0,6')).toEqual([5, 6]);
    expect(parseCronDays('7')).toEqual([6]);
    expect(parseCronDays('MON-FRI')).toBeNull();
    expect(parseCronDays('5-1')).toBeNull();
  });
});

describe('frases ↔ cron', () => {
  it('«cada 30 minutos en días laborables» es */30 * * * 1-5', () => {
    const draft = simple({ repeat: 'minutes', every: 30, days: [...WORKDAYS] });
    expect(simpleToCron(draft)).toBe('*/30 * * * 1-5');
    expect(toScheduleBody(draft)).toMatchObject({
      type: 'cron',
      cron_expression: '*/30 * * * 1-5',
      timezone: null,
      misfire_policy: 'skip',
      enabled: true,
    });
  });

  it('traduce horas, hora diaria y día del mes', () => {
    expect(simpleToCron(simple({ repeat: 'hours', every: 1 }))).toBe('0 * * * *');
    expect(simpleToCron(simple({ repeat: 'hours', every: 4, days: [5, 6] }))).toBe('0 */4 * * 0,6');
    expect(simpleToCron(simple({ repeat: 'daily', time: '06:05', days: [...WORKDAYS] }))).toBe(
      '5 6 * * 1-5',
    );
    expect(simpleToCron(simple({ repeat: 'monthly', time: '23:30', monthDay: 15 }))).toBe(
      '30 23 15 * *',
    );
  });

  it('reconoce lo que el modo sencillo sabe escribir', () => {
    expect(cronToSimple('*/30 * * * 1-5')).toMatchObject({
      repeat: 'minutes',
      every: 30,
      days: [...WORKDAYS],
    });
    expect(cronToSimple('0 */4 * * *')).toMatchObject({ repeat: 'hours', every: 4 });
    expect(cronToSimple('5 6 * * 1-5')).toMatchObject({ repeat: 'daily', time: '06:05' });
    expect(cronToSimple('30 23 15 * *')).toMatchObject({ repeat: 'monthly', monthDay: 15 });
  });

  it('deja como cron lo que las frases no cubren', () => {
    expect(cronToSimple('*/7 * * * *')).toBeNull();
    expect(cronToSimple('*/30 8-18 * * 1-5')).toBeNull();
    expect(cronToSimple('0 6 * 1 *')).toBeNull();
    expect(cronToSimple('0 6 1 * 1')).toBeNull();
    expect(cronToSimple('no es cron')).toBeNull();
  });
});

describe('validación de formato', () => {
  it('exige días, cinco campos de cron y ventanas completas', () => {
    expect(scheduleIssue(simple({ repeat: 'minutes', days: [] }))).toBe('days');
    expect(scheduleIssue(simple({ mode: 'cron', cron: '0 6 * *' }))).toBe('cron');
    expect(scheduleIssue(simple({ mode: 'interval', intervalMinutes: '0' }))).toBe('interval');
    expect(scheduleIssue(simple({ blackouts: [{ ...newBlackout('weekly'), days: [] }] }))).toBe(
      'blackoutDays',
    );
    expect(
      scheduleIssue(
        simple({ blackouts: [{ ...newBlackout('weekly'), start: '02:00', end: '02:00' }] }),
      ),
    ).toBe('blackoutHours');
    expect(scheduleIssue(simple({ blackouts: [newBlackout('range')] }))).toBe('blackoutRange');
    expect(scheduleIssue(simple({}))).toBeNull();
  });

  it('no pide vista previa de una programación incompleta', () => {
    expect(toPreviewRequest(simple({ mode: 'cron', cron: '' }))).toBeNull();
    expect(toPreviewRequest(simple({ repeat: 'daily', time: '06:00' }))).toMatchObject({
      type: 'cron',
      cron_expression: '0 6 * * *',
      count: 10,
    });
  });
});

describe('programaciones guardadas', () => {
  it('convierte una programación cron en frases y conserva sus ventanas', () => {
    const [draft] = fromSchedules([weekdaySchedule]);
    expect(draft).toMatchObject({
      mode: 'simple',
      repeat: 'minutes',
      every: 30,
      days: [...WORKDAYS],
      timezone: 'America/Guatemala',
    });
    expect(draft?.blackouts[0]).toMatchObject({
      kind: 'weekly',
      label: 'Copia de seguridad',
      start: '22:00',
      end: '02:00',
    });
    // Ida y vuelta: lo que se guarda es lo que se leyó.
    expect(draft && toScheduleBody(draft)).toMatchObject({
      type: 'cron',
      cron_expression: '*/30 * * * 1-5',
      timezone: 'America/Guatemala',
      blackout_windows: [
        { kind: 'weekly', label: 'Copia de seguridad', days: [0, 1, 2, 3, 4], start: '22:00' },
      ],
    });
  });

  it('mantiene los intervalos fijos y omite las «solo manual»', () => {
    const drafts = fromSchedules([
      { ...weekdaySchedule, type: 'interval', cron_expression: null, interval_minutes: 45 },
      { ...weekdaySchedule, type: 'manual_only', cron_expression: null },
      { ...weekdaySchedule, cron_expression: '*/30 8-18 * * 1-5' },
    ]);
    expect(drafts.map((d) => d.mode)).toEqual(['interval', 'cron']);
    expect(drafts[0] && toScheduleBody(drafts[0])).toMatchObject({
      type: 'interval',
      interval_minutes: 45,
    });
  });
});

describe('después de otro pipeline', () => {
  it('pide el pipeline anterior y se envía sin reloj', () => {
    expect(scheduleIssue(newDependency())).toBe('upstream');
    const chosen: ScheduleDraft = {
      ...newDependency(),
      upstreamId: 'p-1',
      upstreamCode: 'GL_A_SNOWFLAKE',
      enabled: false,
    };
    expect(scheduleIssue(chosen)).toBeNull();
    expect(toPreviewRequest(chosen)).toBeNull();
    expect(toScheduleBody(chosen)).toEqual({
      type: 'dependency',
      upstream_pipeline_id: 'p-1',
      timezone: null,
      blackout_windows: [],
      misfire_policy: 'skip',
      enabled: false,
    });
    // Las ventanas de exclusión siguen valiendo, y se validan igual.
    const weekly = { ...newBlackout('weekly'), days: [] };
    expect(scheduleIssue({ ...chosen, blackouts: [weekly] })).toBe('blackoutDays');
  });

  it('lee una dependencia guardada con su pipeline anterior', () => {
    const [draft] = fromSchedules([dependencySchedule]);
    expect(draft).toMatchObject({
      mode: 'dependency',
      upstreamId: dependencySchedule.upstream?.id,
      upstreamCode: 'GL_A_SNOWFLAKE',
    });
    expect(draft && toScheduleBody(draft)).toMatchObject({
      type: 'dependency',
      upstream_pipeline_id: dependencySchedule.upstream?.id,
      timezone: 'America/Guatemala',
    });
  });
});

describe('banda semanal', () => {
  it('parte en dos días la ventana que cruza la medianoche', () => {
    const window = { ...newBlackout('weekly'), days: [4], start: '22:00', end: '02:00' };
    expect(weeklySegments([window])).toEqual([
      { day: 4, from: 1320, to: 1440 },
      { day: 5, from: 0, to: 120 },
    ]);
    // El domingo continúa en el lunes.
    expect(weeklySegments([{ ...window, days: [6] }])[1]).toEqual({ day: 0, from: 0, to: 120 });
    expect(weeklySegments([{ ...window, start: '08:00', end: '09:30' }])).toEqual([
      { day: 4, from: 480, to: 570 },
    ]);
  });
});
