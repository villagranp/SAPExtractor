import { useId, type ReactNode } from 'react';

/* Piezas de presentación: métricas, medidores y control segmentado. */

// ------------------------------------------------------------------ Metric
export interface MetricProps {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
}

/** Cifra destacada con su etiqueta (tarjetas del inicio y paneles). */
export function Metric({ label, value, unit, hint, children }: MetricProps) {
  return (
    <div className="sx-metric">
      <span className="sx-metric__label">{label}</span>
      <span className="sx-metric__value">
        {value}
        {unit !== undefined && <span className="sx-metric__unit">{unit}</span>}
      </span>
      {hint !== undefined && <span className="sx-metric__hint">{hint}</span>}
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ Meter
export type MeterState = 'on' | 'partial' | 'off';

export interface MeterProps {
  segments: { key: string; state: MeterState; title: string }[];
  label: string;
}

/** Progreso discreto en segmentos (p. ej. iteraciones completadas). */
export function Meter({ segments, label }: MeterProps) {
  return (
    <ol className="sx-meter" aria-label={label}>
      {segments.map((s) => (
        <li
          key={s.key}
          title={s.title}
          className={`sx-meter__segment sx-meter__segment--${s.state}`}
        >
          <span className="sx-visually-hidden">{s.title}</span>
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ SegmentedControl
export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

export interface SegmentedControlProps<T extends string> {
  label: string;
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
}

/** Elección única entre pocas opciones (grupo de radios con aspecto de teclas). */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: SegmentedControlProps<T>) {
  const name = useId();
  return (
    <fieldset className="sx-segmented">
      <legend>{label}</legend>
      <div className="sx-segmented__track">
        {options.map((o) => (
          <label key={o.value} className="sx-segmented__option">
            <input
              type="radio"
              name={name}
              value={o.value}
              checked={value === o.value}
              onChange={() => {
                onChange(o.value);
              }}
            />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
