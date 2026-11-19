import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react';

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
