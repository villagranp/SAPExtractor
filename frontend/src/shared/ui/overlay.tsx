import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Button } from './components';

/* Diálogos modales sobre <dialog> nativo (foco, Escape y fondo inerte los gestiona el navegador). */

export interface DialogProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'md' | 'lg';
  closeLabel?: string;
}

export function Dialog({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
  closeLabel = 'Cerrar',
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      // jsdom no implementa showModal: se degrada a mostrarlo sin modal.
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    } else if (!open && dialog.open) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`sx-dialog sx-dialog--${size}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {open && (
        <>
          <header className="sx-dialog__header">
            <h2 id={titleId}>{title}</h2>
            <button
              type="button"
              className="sx-dialog__close"
              aria-label={closeLabel}
              onClick={onClose}
            >
              ×
            </button>
          </header>
          <div className="sx-dialog__body">{children}</div>
          {footer !== undefined && <footer className="sx-dialog__footer">{footer}</footer>}
        </>
      )}
    </dialog>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  message: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  danger?: boolean;
  busy?: boolean;
  /** Si se indica, se pide un motivo (obligatorio si `reasonRequired`). */
  reasonLabel?: string;
  reasonRequired?: boolean;
  error?: ReactNode;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  danger = false,
  busy = false,
  reasonLabel,
  reasonRequired = false,
  error,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [reason, setReason] = useState('');
  const reasonId = useId();
  const blocked = busy || (reasonRequired && reason.trim().length < 5);
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            disabled={blocked}
            onClick={() => {
              onConfirm(reason.trim());
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div>{message}</div>
      {reasonLabel && (
        <div className="sx-field" style={{ marginTop: 'var(--sx-space-4)' }}>
          <label htmlFor={reasonId}>{reasonLabel}</label>
          <textarea
            id={reasonId}
            className="sx-input"
            rows={3}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
        </div>
      )}
      {error}
    </Dialog>
  );
}
