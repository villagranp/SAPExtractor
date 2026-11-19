import { useState, type ReactNode } from 'react';
import type { StatusTone } from './components';

/* Avisos, cargas, pestañas y secretos de un solo uso. */

export interface AlertProps {
  tone: StatusTone;
  title?: ReactNode;
  children?: ReactNode;
}

export function Alert({ tone, title, children }: AlertProps) {
  return (
    <div className={`sx-alert sx-alert--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      {title && <strong>{title}</strong>}
      {children && <div>{children}</div>}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="sx-spinner" role="status" aria-live="polite">
      <span className="sx-spinner__dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export interface TabItem {
  id: string;
  label: ReactNode;
}

export interface TabsProps {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  label: string;
}

export function Tabs({ tabs, active, onChange, label }: TabsProps) {
  return (
    <div className="sx-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          className="sx-tabs__tab"
          onClick={() => {
            onChange(tab.id);
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export interface OneTimeSecretProps {
  label: string;
  value: string;
  warning: ReactNode;
  copyLabel: string;
  copiedLabel: string;
}

/** Muestra un secreto que no se podrá volver a consultar (contraseña temporal, token API). */
export function OneTimeSecret({
  label,
  value,
  warning,
  copyLabel,
  copiedLabel,
}: OneTimeSecretProps) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="sx-secret">
      <span className="sx-secret__label">{label}</span>
      <div className="sx-secret__row">
        <code data-testid="one-time-secret">{value}</code>
        <button
          type="button"
          className="sx-btn sx-btn--secondary"
          onClick={() => {
            // Sin HTTPS (fuera de localhost) el navegador no expone la API del portapapeles.
            const clipboard = navigator.clipboard as Clipboard | undefined;
            void clipboard?.writeText(value).then(() => {
              setCopied(true);
            });
          }}
        >
          {copied ? copiedLabel : copyLabel}
        </button>
      </div>
      <p className="sx-field__hint">{warning}</p>
    </div>
  );
}

export interface ChecklistItem {
  id: string;
  title: ReactNode;
  tone: StatusTone;
  statusLabel: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
}

/** Resultado paso a paso de una comprobación (prueba de conexión, validaciones). */
export function Checklist({ items, label }: { items: ChecklistItem[]; label: string }) {
  return (
    <ol className="sx-checklist" aria-label={label}>
      {items.map((item) => (
        <li key={item.id} className="sx-checklist__item" data-status={item.tone}>
          <span className={`sx-pill sx-pill--${item.tone}`}>{item.statusLabel}</span>
          <div>
            <strong>{item.title}</strong>
            {item.description && <div>{item.description}</div>}
          </div>
          {item.meta && <small>{item.meta}</small>}
        </li>
      ))}
    </ol>
  );
}
