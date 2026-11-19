import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';

/* Controles de formulario adicionales del design system. */

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  options: SelectOption[];
  /** Texto de la opción vacía (p. ej. "Todos"). Si se omite, no hay opción vacía. */
  placeholder?: string;
  hint?: string | undefined;
  error?: string | undefined;
}

export function SelectField({
  label,
  options,
  placeholder,
  hint,
  error,
  className,
  ...rest
}: SelectFieldProps) {
  const id = useId();
  return (
    <div className="sx-field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className={['sx-input', className].filter(Boolean).join(' ')}
        aria-invalid={error ? true : undefined}
        {...rest}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && !error && <span className="sx-field__hint">{hint}</span>}
      {error && (
        <span className="sx-field__error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> {
  label: ReactNode;
  description?: ReactNode;
  indeterminate?: boolean;
}

export function Checkbox({ label, description, indeterminate, ...rest }: CheckboxProps) {
  const id = useId();
  return (
    <div className="sx-check">
      <input
        id={id}
        type="checkbox"
        ref={(el) => {
          if (el) el.indeterminate = indeterminate ?? false;
        }}
        {...rest}
      />
      <label htmlFor={id}>
        <span>{label}</span>
        {description && <small>{description}</small>}
      </label>
    </div>
  );
}

export interface FilterBarProps {
  children: ReactNode;
  actions?: ReactNode;
}

/** Fila de filtros encima de un listado. */
export function FilterBar({ children, actions }: FilterBarProps) {
  return (
    <div className="sx-filterbar">
      <div className="sx-filterbar__fields">{children}</div>
      {actions && <div className="sx-filterbar__actions">{actions}</div>}
    </div>
  );
}

export interface FieldErrorsProps {
  errors: string[] | undefined;
}

/** Lista de errores de un campo devueltos por la API (Problem Details → errors). */
export function FieldErrors({ errors }: FieldErrorsProps) {
  if (!errors?.length) return null;
  return (
    <ul className="sx-field__errors" role="alert">
      {errors.map((e) => (
        <li key={e}>{e}</li>
      ))}
    </ul>
  );
}

export interface TextAreaFieldProps extends Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  'id'
> {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  /** Texto de ancho fijo (PEM, JSON, código). */
  mono?: boolean;
}

export function TextAreaField({
  label,
  hint,
  error,
  mono = false,
  className,
  rows = 4,
  ...rest
}: TextAreaFieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className="sx-field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        rows={rows}
        className={['sx-input', mono && 'sx-input--mono', className].filter(Boolean).join(' ')}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        spellCheck={mono ? false : undefined}
        {...rest}
      />
      {hint && !error && (
        <span id={`${id}-hint`} className="sx-field__hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className="sx-field__error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export interface StepperStep {
  id: string;
  label: ReactNode;
}

export interface StepperProps {
  steps: StepperStep[];
  /** Índice del paso actual (0..n-1). */
  current: number;
  label: string;
  /** Permite volver a un paso anterior pulsándolo. */
  onSelect?: (index: number) => void;
}

/** Progreso de un asistente paso a paso (plan 10 §4.1). */
export function Stepper({ steps, current, label, onSelect }: StepperProps) {
  return (
    <ol className="sx-stepper" aria-label={label}>
      {steps.map((step, index) => {
        const state = index < current ? 'done' : index === current ? 'current' : 'todo';
        const content = (
          <>
            <span className="sx-stepper__index" aria-hidden="true">
              {state === 'done' ? '✓' : index + 1}
            </span>
            <span>{step.label}</span>
          </>
        );
        return (
          <li
            key={step.id}
            className={`sx-stepper__step sx-stepper__step--${state}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            {onSelect && state === 'done' ? (
              <button
                type="button"
                className="sx-stepper__button"
                onClick={() => {
                  onSelect(index);
                }}
              >
                {content}
              </button>
            ) : (
              content
            )}
          </li>
        );
      })}
    </ol>
  );
}
