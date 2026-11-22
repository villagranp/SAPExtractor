import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { describeError } from '@/shared/api/errors';
import { Alert, Button, Checkbox, SegmentedControl, SelectField, TextField } from '@/shared/ui';
import { usePipelines, useSchedulePreview } from '../api/queries';
import {
  ALL_DAYS,
  HOUR_STEPS,
  MAX_BLACKOUTS,
  MAX_SCHEDULES,
  MINUTE_STEPS,
  WORKDAYS,
  cronToSimple,
  isDependency,
  newBlackout,
  newDependency,
  newSchedule,
  scheduleIssue,
  simpleToCron,
  toPreviewRequest,
  weeklySegments,
  type BlackoutDraft,
  type MisfirePolicy,
  type Repeat,
  type ScheduleDraft,
  type ScheduleMode,
} from '../schemas/schedule';
import {
  cronOf,
  fireFormatter,
  timezoneOptions,
  useScheduleText,
  weekdayLabels,
  weekdayNames,
} from './scheduleText';

/** Espera a que el usuario deje de escribir antes de pedir la vista previa. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    const id = window.setTimeout(() => {
      setSettled(value);
    }, delayMs);
    return () => {
      window.clearTimeout(id);
    };
    // `key` resume el contenido: el objeto cambia de identidad en cada render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, delayMs]);
  return settled;
}

// ------------------------------------------------------------------ días de la semana
function DaysPicker({
  legend,
  days,
  onChange,
  presets = false,
}: {
  legend: string;
  days: number[];
  onChange: (days: number[]) => void;
  presets?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const labels = weekdayLabels(i18n.resolvedLanguage ?? 'es');
  return (
    <fieldset className="sx-fieldset sx-pipelines__days">
      <legend>{legend}</legend>
      <div className="sx-pipelines__days-row">
        {ALL_DAYS.map((day) => (
          <Checkbox
            key={day}
            label={labels[day]}
            checked={days.includes(day)}
            onChange={(e) => {
              onChange(
                e.target.checked
                  ? [...days, day].sort((a, b) => a - b)
                  : days.filter((d) => d !== day),
              );
            }}
          />
        ))}
      </div>
      {presets && (
        <div className="sx-row">
          <Button
            variant="ghost"
            onClick={() => {
              onChange([...WORKDAYS]);
            }}
          >
            {t('pipelines.schedule.presetWorkdays')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              onChange([...ALL_DAYS]);
            }}
          >
            {t('pipelines.schedule.presetAllDays')}
          </Button>
        </div>
      )}
    </fieldset>
  );
}

// ------------------------------------------------------------------ banda semanal
const HOUR_MARKS = [0, 6, 12, 18, 24];

/** Semana de lunes a domingo con las horas excluidas marcadas. La lista de arriba dice lo mismo. */
function WeekBand({ blackouts }: { blackouts: BlackoutDraft[] }) {
  const { i18n } = useTranslation();
  const names = weekdayNames(i18n.resolvedLanguage ?? 'es', 'short');
  const segments = weeklySegments(blackouts);
  if (segments.length === 0) return null;
  return (
    <div className="sx-pipelines__band" aria-hidden="true">
      {ALL_DAYS.map((day) => (
        <div key={day} className="sx-pipelines__band-row">
          <span className="sx-pipelines__band-day">{names[day]}</span>
          <span className="sx-pipelines__band-track">
            {segments
              .filter((s) => s.day === day)
              .map((s) => (
                <span
                  key={`${String(s.from)}-${String(s.to)}`}
                  className="sx-pipelines__band-off"
                  style={{
                    left: `${String((s.from / 1440) * 100)}%`,
                    width: `${String(((s.to - s.from) / 1440) * 100)}%`,
                  }}
                />
              ))}
          </span>
        </div>
      ))}
      <div className="sx-pipelines__band-row">
        <span className="sx-pipelines__band-day" />
        <span className="sx-pipelines__band-hours">
          {HOUR_MARKS.map((hour) => (
            <span key={hour}>{String(hour).padStart(2, '0')}</span>
          ))}
        </span>
      </div>
    </div>
  );
}

/** Banda y pie: el pie sí se lee, la banda es solo un dibujo de la lista de ventanas. */
function WeekBandFigure({ blackouts }: { blackouts: BlackoutDraft[] }) {
  const { t } = useTranslation();
  if (weeklySegments(blackouts).length === 0) return null;
  return (
    <div>
      <WeekBand blackouts={blackouts} />
      <p className="sx-muted">{t('pipelines.schedule.bandCaption')}</p>
    </div>
  );
}

// ------------------------------------------------------------------ ventanas de exclusión
function BlackoutEditor({
  blackouts,
  onChange,
}: {
  blackouts: BlackoutDraft[];
  onChange: (blackouts: BlackoutDraft[]) => void;
}) {
  const { t } = useTranslation();
  const patch = (key: string, changes: Partial<BlackoutDraft>) => {
    onChange(blackouts.map((w) => (w.key === key ? { ...w, ...changes } : w)));
  };
  const full = blackouts.length >= MAX_BLACKOUTS;
  return (
    <div className="sx-stack">
      <div>
        <h3 className="sx-pipelines__subtitle">{t('pipelines.schedule.blackouts')}</h3>
        <p className="sx-muted">{t('pipelines.schedule.blackoutsHint')}</p>
      </div>
      {blackouts.map((window, index) => (
        <fieldset key={window.key} className="sx-pipelines__blackout">
          <legend className="sx-visually-hidden">
            {t('pipelines.schedule.blackoutN', { n: index + 1 })}
          </legend>
          <TextField
            label={t('pipelines.schedule.blackoutLabel')}
            placeholder={t(`pipelines.schedule.blackoutPlaceholder.${window.kind}`)}
            maxLength={100}
            value={window.label}
            onChange={(e) => {
              patch(window.key, { label: e.target.value });
            }}
          />
          {window.kind === 'weekly' ? (
            <>
              <TextField
                label={t('pipelines.schedule.from')}
                type="time"
                value={window.start}
                onChange={(e) => {
                  patch(window.key, { start: e.target.value });
                }}
              />
              <TextField
                label={t('pipelines.schedule.to')}
                type="time"
                hint={window.end < window.start ? t('pipelines.schedule.nextDay') : undefined}
                value={window.end}
                onChange={(e) => {
                  patch(window.key, { end: e.target.value });
                }}
              />
            </>
          ) : (
            <>
              <TextField
                label={t('pipelines.schedule.from')}
                type="datetime-local"
                value={window.startAt}
                onChange={(e) => {
                  patch(window.key, { startAt: e.target.value });
                }}
              />
              <TextField
                label={t('pipelines.schedule.to')}
                type="datetime-local"
                hint={t('pipelines.schedule.browserZone')}
                value={window.endAt}
                onChange={(e) => {
                  patch(window.key, { endAt: e.target.value });
                }}
              />
            </>
          )}
          <Button
            variant="secondary"
            onClick={() => {
              onChange(blackouts.filter((w) => w.key !== window.key));
            }}
          >
            {t('pipelines.schedule.removeBlackout')}
          </Button>
          {window.kind === 'weekly' && (
            <div className="sx-pipelines__blackout-days">
              <DaysPicker
                legend={t('pipelines.schedule.blackoutDays')}
                days={window.days}
                onChange={(days) => {
                  patch(window.key, { days });
                }}
              />
            </div>
          )}
        </fieldset>
      ))}
      <WeekBandFigure blackouts={blackouts} />
      <div className="sx-row">
        <Button
          variant="secondary"
          disabled={full}
          onClick={() => {
            onChange([...blackouts, newBlackout('weekly')]);
          }}
        >
          {t('pipelines.schedule.addWeekly')}
        </Button>
        <Button
          variant="secondary"
          disabled={full}
          onClick={() => {
            onChange([...blackouts, newBlackout('range')]);
          }}
        >
          {t('pipelines.schedule.addRange')}
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ vista previa
function Preview({ draft }: { draft: ScheduleDraft }) {
  const { t } = useTranslation();
  const { language, describe } = useScheduleText();
  const issue = scheduleIssue(draft);
  const request = useDebounced(toPreviewRequest(draft), 350);
  const preview = useSchedulePreview(request);
  const cron = cronOf(draft);
  const zone = preview.data?.timezone ?? draft.timezone;
  const format = fireFormatter(language, zone || 'UTC');

  return (
    <aside className="sx-pipelines__preview" aria-label={t('pipelines.schedule.previewTitle')}>
      <h3 className="sx-pipelines__subtitle">{t('pipelines.schedule.previewTitle')}</h3>
      <p className="sx-pipelines__sentence">{describe(draft)}</p>
      {cron && draft.mode === 'simple' && (
        <p className="sx-muted">
          {t('pipelines.schedule.asCron')} <code>{cron}</code>
        </p>
      )}
      {issue ? (
        <p className="sx-field__error" role="alert">
          {t(`pipelines.schedule.issues.${issue}`)}
        </p>
      ) : preview.error ? (
        <p className="sx-field__error" role="alert">
          {describeError(preview.error).message}
        </p>
      ) : preview.data ? (
        preview.data.fires.length > 0 ? (
          <>
            <ol className="sx-pipelines__fires">
              {preview.data.fires.map((fire) => (
                <li key={fire}>
                  <time dateTime={fire}>{format.format(new Date(fire))}</time>
                </li>
              ))}
            </ol>
            <p className="sx-muted">{t('pipelines.schedule.previewZone', { zone })}</p>
          </>
        ) : (
          <p className="sx-muted">{t('pipelines.schedule.previewEmpty')}</p>
        )
      ) : (
        <p className="sx-muted">{t('pipelines.schedule.previewLoading')}</p>
      )}
    </aside>
  );
}

// ------------------------------------------------------------------ después de otro pipeline
/** Pipelines que caben en el desplegable; con más, se elige por código en la API. */
const UPSTREAM_PAGE_SIZE = 200;
const DEPENDENCY_RULES = ['any', 'busy', 'off'] as const;

interface DependencyBuilderProps extends ScheduleBuilderProps {
  /** Pipeline que se está editando: no puede ir después de sí mismo. */
  pipelineId?: string | undefined;
}

/**
 * «Después de otro pipeline» (plan 08 §6): un solo pipeline anterior, sin reloj. Las ventanas
 * de exclusión siguen valiendo; lo demás de una programación no aplica.
 */
function DependencyBuilder({ draft, onChange, pipelineId }: DependencyBuilderProps) {
  const { t } = useTranslation();
  const { describe } = useScheduleText();
  const pipelines = usePipelines({ page: 1, page_size: UPSTREAM_PAGE_SIZE });
  const set = (changes: Partial<ScheduleDraft>) => {
    onChange({ ...draft, ...changes });
  };
  const others = (pipelines.data?.items ?? []).filter((p) => p.id !== pipelineId);
  const options = others.map((p) => ({ value: p.id, label: `${p.code}, ${p.name}` }));
  // El anterior ya guardado sigue en la lista aunque no quepa en la primera página.
  if (draft.upstreamId && !options.some((o) => o.value === draft.upstreamId)) {
    options.unshift({ value: draft.upstreamId, label: draft.upstreamCode || draft.upstreamId });
  }
  const weekly = draft.blackouts.some((window) => window.kind === 'weekly');
  // Sin elegir pipeline la frase ya lo dice; aquí solo los fallos de las ventanas.
  const issue = scheduleIssue(draft);
  const windowIssue = issue !== null && issue !== 'upstream' ? issue : null;

  return (
    <div className="sx-pipelines__schedule">
      <div className="sx-stack sx-pipelines__schedule-controls">
        {pipelines.error && <Alert tone="danger">{describeError(pipelines.error).message}</Alert>}
        {pipelines.data && options.length === 0 ? (
          <Alert tone="info">{t('pipelines.schedule.upstreamNone')}</Alert>
        ) : (
          <SelectField
            label={t('pipelines.schedule.upstream')}
            placeholder={t('pipelines.schedule.upstreamPlaceholder')}
            hint={t('pipelines.schedule.upstreamHint')}
            value={draft.upstreamId}
            disabled={pipelines.isPending}
            onChange={(e) => {
              const chosen = others.find((p) => p.id === e.target.value);
              set({ upstreamId: e.target.value, upstreamCode: chosen?.code ?? '' });
            }}
            options={options}
          />
        )}
        <Checkbox
          label={t('pipelines.schedule.dependencyEnabled')}
          description={t('pipelines.schedule.enabledHint')}
          checked={draft.enabled}
          onChange={(e) => {
            set({ enabled: e.target.checked });
          }}
        />
      </div>
      <aside className="sx-pipelines__preview" aria-label={t('pipelines.schedule.dependencyTitle')}>
        <h3 className="sx-pipelines__subtitle">{t('pipelines.schedule.dependencyTitle')}</h3>
        <p className="sx-pipelines__sentence">{describe(draft)}</p>
        {windowIssue && (
          <p className="sx-field__error" role="alert">
            {t(`pipelines.schedule.issues.${windowIssue}`)}
          </p>
        )}
        <ul className="sx-pipelines__rules">
          {DEPENDENCY_RULES.map((rule) => (
            <li key={rule}>{t(`pipelines.schedule.dependencyRules.${rule}`)}</li>
          ))}
        </ul>
      </aside>
      <div className="sx-pipelines__schedule-blackouts sx-stack">
        <BlackoutEditor
          blackouts={draft.blackouts}
          onChange={(blackouts) => {
            set({ blackouts });
          }}
        />
        {weekly && (
          <SelectField
            label={t('pipelines.schedule.timezone')}
            placeholder={t('pipelines.schedule.platformZone')}
            hint={t('pipelines.schedule.dependencyZoneHint')}
            value={draft.timezone}
            onChange={(e) => {
              set({ timezone: e.target.value });
            }}
            options={timezoneOptions().map((zone) => ({ value: zone, label: zone }))}
          />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ una programación
type ClockMode = Exclude<ScheduleMode, 'dependency'>;

export interface ScheduleBuilderProps {
  draft: ScheduleDraft;
  onChange: (draft: ScheduleDraft) => void;
}

/** Constructor de una programación (plan 10 §4.3): frases, cron, zona y ventanas de exclusión. */
export function ScheduleBuilder({ draft, onChange }: ScheduleBuilderProps) {
  const { t } = useTranslation();
  const set = (changes: Partial<ScheduleDraft>) => {
    onChange({ ...draft, ...changes });
  };
  const modes: ClockMode[] =
    draft.mode === 'interval' ? ['simple', 'cron', 'interval'] : ['simple', 'cron'];
  const mode: ClockMode = draft.mode === 'dependency' ? 'simple' : draft.mode;

  const changeMode = (next: ClockMode) => {
    if (next === 'cron' && draft.mode === 'simple') {
      // Parte de lo que ya estaba elegido en frases.
      set({ mode: next, cron: simpleToCron(draft) });
    } else if (next === 'simple' && draft.mode === 'cron') {
      const simple = cronToSimple(draft.cron);
      set(simple ? { mode: next, ...simple } : { mode: next });
    } else {
      set({ mode: next });
    }
  };

  return (
    <div className="sx-pipelines__schedule">
      <div className="sx-stack sx-pipelines__schedule-controls">
        <SegmentedControl
          label={t('pipelines.schedule.mode')}
          value={mode}
          options={modes.map((value) => ({
            value,
            label: t(`pipelines.schedule.modes.${value}`),
          }))}
          hint={t(`pipelines.schedule.modeHints.${mode}`)}
          onChange={changeMode}
        />

        {draft.mode === 'simple' && (
          <>
            <div className="sx-pipelines__pair">
              <SelectField
                label={t('pipelines.schedule.repeat')}
                value={draft.repeat}
                onChange={(e) => {
                  const repeat = e.target.value as Repeat;
                  set({
                    repeat,
                    every: repeat === 'minutes' ? 30 : repeat === 'hours' ? 1 : draft.every,
                  });
                }}
                options={(['minutes', 'hours', 'daily', 'monthly'] as const).map((value) => ({
                  value,
                  label: t(`pipelines.schedule.repeats.${value}`),
                }))}
              />
              {draft.repeat === 'minutes' && (
                <SelectField
                  label={t('pipelines.schedule.every')}
                  value={String(draft.every)}
                  onChange={(e) => {
                    set({ every: Number(e.target.value) });
                  }}
                  options={MINUTE_STEPS.map((n) => ({
                    value: String(n),
                    label: t('pipelines.schedule.minutes', { count: n }),
                  }))}
                />
              )}
              {draft.repeat === 'hours' && (
                <SelectField
                  label={t('pipelines.schedule.every')}
                  value={String(draft.every)}
                  onChange={(e) => {
                    set({ every: Number(e.target.value) });
                  }}
                  options={HOUR_STEPS.map((n) => ({
                    value: String(n),
                    label: t('pipelines.schedule.hours', { count: n }),
                  }))}
                />
              )}
              {draft.repeat === 'monthly' && (
                <TextField
                  label={t('pipelines.schedule.monthDay')}
                  hint={draft.monthDay > 28 ? t('pipelines.schedule.monthDayHint') : undefined}
                  type="number"
                  min={1}
                  max={31}
                  value={draft.monthDay}
                  onChange={(e) => {
                    set({ monthDay: Number(e.target.value) });
                  }}
                />
              )}
              {(draft.repeat === 'daily' || draft.repeat === 'monthly') && (
                <TextField
                  label={t('pipelines.schedule.time')}
                  type="time"
                  value={draft.time}
                  onChange={(e) => {
                    set({ time: e.target.value });
                  }}
                />
              )}
            </div>
            {draft.repeat !== 'monthly' && (
              <DaysPicker
                legend={t('pipelines.schedule.daysLegend')}
                days={draft.days}
                presets
                onChange={(days) => {
                  set({ days });
                }}
              />
            )}
          </>
        )}

        {draft.mode === 'cron' && (
          <TextField
            label={t('pipelines.schedule.cron')}
            hint={t('pipelines.schedule.cronHint')}
            className="sx-input--mono"
            autoComplete="off"
            spellCheck={false}
            value={draft.cron}
            onChange={(e) => {
              set({ cron: e.target.value });
            }}
          />
        )}

        {draft.mode === 'interval' && (
          <TextField
            label={t('pipelines.schedule.interval')}
            hint={t('pipelines.schedule.intervalHint')}
            inputMode="numeric"
            value={draft.intervalMinutes}
            onChange={(e) => {
              set({ intervalMinutes: e.target.value });
            }}
          />
        )}

        <div className="sx-pipelines__pair">
          <SelectField
            label={t('pipelines.schedule.timezone')}
            placeholder={t('pipelines.schedule.platformZone')}
            hint={t('pipelines.schedule.timezoneHint')}
            value={draft.timezone}
            onChange={(e) => {
              set({ timezone: e.target.value });
            }}
            options={timezoneOptions().map((zone) => ({ value: zone, label: zone }))}
          />
          <SelectField
            label={t('pipelines.schedule.misfire')}
            hint={t(`pipelines.schedule.misfireHints.${draft.misfire}`)}
            value={draft.misfire}
            onChange={(e) => {
              set({ misfire: e.target.value as MisfirePolicy });
            }}
            options={(['skip', 'run_once'] as const).map((value) => ({
              value,
              label: t(`pipelines.schedule.misfires.${value}`),
            }))}
          />
        </div>
        <Checkbox
          label={t('pipelines.schedule.enabled')}
          description={t('pipelines.schedule.enabledHint')}
          checked={draft.enabled}
          onChange={(e) => {
            set({ enabled: e.target.checked });
          }}
        />
      </div>
      <Preview draft={draft} />
      <div className="sx-pipelines__schedule-blackouts">
        <BlackoutEditor
          blackouts={draft.blackouts}
          onChange={(blackouts) => {
            set({ blackouts });
          }}
        />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ todas las programaciones
export interface SchedulesEditorProps {
  drafts: ScheduleDraft[];
  onChange: (drafts: ScheduleDraft[]) => void;
  /** Pipeline que se edita (sin él, es uno nuevo): se excluye al elegir el pipeline anterior. */
  pipelineId?: string | undefined;
}

type Launch = 'scheduled' | 'after' | 'manual';
const LAUNCHES: Launch[] = ['scheduled', 'after', 'manual'];

/**
 * Sin programaciones el pipeline solo se lanza a mano; con reloj admite hasta cinco; después
 * de otro pipeline, solo esa.
 */
export function SchedulesEditor({ drafts, onChange, pipelineId }: SchedulesEditorProps) {
  const { t } = useTranslation();
  const dependency = isDependency(drafts) ? drafts[0] : undefined;
  const launch: Launch = dependency ? 'after' : drafts.length > 0 ? 'scheduled' : 'manual';
  const scheduled = launch === 'scheduled';
  return (
    <div className="sx-stack">
      <SegmentedControl
        label={t('pipelines.schedule.launch')}
        value={launch}
        options={LAUNCHES.map((value) => ({
          value,
          label: t(`pipelines.schedule.launches.${value}`),
        }))}
        hint={t(`pipelines.schedule.launchHints.${launch}`)}
        onChange={(value) => {
          if (value === launch) return;
          if (value === 'manual') onChange([]);
          else onChange([value === 'after' ? newDependency() : newSchedule()]);
        }}
      />
      {dependency && (
        <section className="sx-pipelines__schedule-card">
          <DependencyBuilder
            draft={dependency}
            pipelineId={pipelineId}
            onChange={(next) => {
              onChange([next]);
            }}
          />
        </section>
      )}
      {(dependency ? [] : drafts).map((draft, index) => (
        <section key={draft.key} className="sx-pipelines__schedule-card">
          {drafts.length > 1 && (
            <header className="sx-row sx-pipelines__schedule-head">
              <h3 className="sx-pipelines__subtitle">
                {t('pipelines.schedule.scheduleN', { n: index + 1 })}
              </h3>
              <Button
                variant="secondary"
                onClick={() => {
                  onChange(drafts.filter((d) => d.key !== draft.key));
                }}
              >
                {t('pipelines.schedule.remove')}
              </Button>
            </header>
          )}
          <ScheduleBuilder
            draft={draft}
            onChange={(next) => {
              onChange(drafts.map((d) => (d.key === next.key ? next : d)));
            }}
          />
        </section>
      ))}
      {scheduled && (
        <div>
          <Button
            variant="secondary"
            disabled={drafts.length >= MAX_SCHEDULES}
            onClick={() => {
              onChange([...drafts, newSchedule()]);
            }}
          >
            {t('pipelines.schedule.add')}
          </Button>
        </div>
      )}
    </div>
  );
}
