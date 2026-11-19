import { useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react';

/*
 * Design system propio. Las pantallas (features/*) importan SOLO desde '@/shared/ui'.
 * Al llegar la plantilla corporativa se reimplementan estos componentes con su estilo
 * manteniendo la misma API (ver docs/plan/10-frontend-y-ux.md).
 */

function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(' ');
}

// ------------------------------------------------------------------ Button
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
}

export function Button({
  variant = 'primary',
  icon,
  className,
  children,
  type,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type ?? 'button'}
      className={cx('sx-btn', `sx-btn--${variant}`, className)}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ Card
export interface CardProps {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, children, className }: CardProps) {
  return (
    <section className={cx('sx-card', className)}>
      {title !== undefined && <h2 className="sx-card__title">{title}</h2>}
      {children}
    </section>
  );
}

// ------------------------------------------------------------------ StatusPill
export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface StatusPillProps {
  tone: StatusTone;
  children: ReactNode;
}

export function StatusPill({ tone, children }: StatusPillProps) {
  return (
    <span className={cx('sx-pill', `sx-pill--${tone}`)} data-tone={tone}>
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ PageHeader
export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="sx-page-header">
      <div>
        <h1>{title}</h1>
        {description !== undefined && <p>{description}</p>}
      </div>
      {actions !== undefined && <div>{actions}</div>}
    </header>
  );
}

// ------------------------------------------------------------------ EmptyState
export interface EmptyStateProps {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div className="sx-empty">
      <h2>{title}</h2>
      {children}
      {action !== undefined && <div style={{ marginTop: 'var(--sx-space-4)' }}>{action}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ TextField
export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
}

export function TextField({ label, hint, error, className, ...rest }: TextFieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className="sx-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className={cx('sx-input', className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
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
