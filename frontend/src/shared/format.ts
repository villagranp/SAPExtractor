const dateTime = new Intl.DateTimeFormat('es', { dateStyle: 'short', timeStyle: 'medium' });
const dateOnly = new Intl.DateTimeFormat('es', { dateStyle: 'medium' });

/** Fecha y hora local legible; '—' si no hay valor. */
export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : '—';
}

export function formatDate(value: string | null | undefined): string {
  return value ? dateOnly.format(new Date(value)) : '—';
}
