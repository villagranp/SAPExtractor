import type { ReactNode } from 'react';

/* Componentes de datos del design system (tabla, paginación, lista de detalle). */

// ------------------------------------------------------------------ DataTable
export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  width?: string;
  align?: 'left' | 'right' | 'center';
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  caption?: string;
  loading?: boolean;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  empty,
  onRowClick,
}: DataTableProps<T>) {
  return (
    <div className="sx-table-wrap" aria-busy={loading}>
      <table className="sx-table">
        {caption && <caption className="sx-visually-hidden">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" style={{ width: c.width, textAlign: c.align ?? 'left' }}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? 'sx-table__row--clickable' : undefined}
              onClick={
                onRowClick
                  ? () => {
                      onRowClick(row);
                    }
                  : undefined
              }
            >
              {columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align ?? 'left' }}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="sx-table__empty">
                {empty ?? '—'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ Pagination
export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  labels: { previous: string; next: string; summary: string };
}

/** `labels.summary` admite los marcadores {from}, {to} y {total}. */
export function Pagination({ page, pageSize, total, onPageChange, labels }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const summary = labels.summary
    .replace('{from}', String(from))
    .replace('{to}', String(to))
    .replace('{total}', String(total));
  return (
    <nav className="sx-pagination" aria-label={summary}>
      <span>{summary}</span>
      <div>
        <button
          type="button"
          className="sx-btn sx-btn--secondary"
          disabled={page <= 1}
          onClick={() => {
            onPageChange(page - 1);
          }}
        >
          {labels.previous}
        </button>
        <button
          type="button"
          className="sx-btn sx-btn--secondary"
          disabled={page >= pages}
          onClick={() => {
            onPageChange(page + 1);
          }}
        >
          {labels.next}
        </button>
      </div>
    </nav>
  );
}

// ------------------------------------------------------------------ DetailList
export interface DetailListProps {
  items: { label: ReactNode; value: ReactNode }[];
}

export function DetailList({ items }: DetailListProps) {
  return (
    <dl className="sx-dl">
      {items.map((item, i) => (
        <div key={i} className="sx-dl__row">
          <dt>{item.label}</dt>
          <dd>{item.value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}
