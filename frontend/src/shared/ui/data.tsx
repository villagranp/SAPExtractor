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
  /**
   * En pantallas estrechas cada fila pasa a ficha: una línea por columna, con su cabecera
   * delante. Sin esto la tabla se desplaza en horizontal dentro de su tarjeta.
   */
  stacked?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  empty,
  onRowClick,
  stacked = false,
}: DataTableProps<T>) {
  const group = stacked ? { role: 'rowgroup' } : {};
  const rowRole = stacked ? { role: 'row' } : {};
  return (
    <div
      className={stacked ? 'sx-table-wrap sx-table-wrap--stacked' : 'sx-table-wrap'}
      aria-busy={loading}
      // En pantallas estrechas la tabla se desplaza en horizontal: con nombre, la región se
      // puede enfocar y mover con las flechas (WCAG 2.1.1).
      {...(caption ? { role: 'region', 'aria-label': caption, tabIndex: 0 } : {})}
    >
      {/* Apilada, la tabla deja de pintarse como tabla: los roles la mantienen para los lectores. */}
      <table className="sx-table" {...(stacked ? { role: 'table' } : {})}>
        {caption && <caption className="sx-visually-hidden">{caption}</caption>}
        <thead {...group}>
          <tr {...rowRole}>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{ width: c.width, textAlign: c.align ?? 'left' }}
                {...(stacked ? { role: 'columnheader' } : {})}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody {...group}>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              {...rowRole}
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
                <td
                  key={c.key}
                  style={{ textAlign: c.align ?? 'left' }}
                  {...(stacked
                    ? {
                        role: 'cell',
                        'data-label': typeof c.header === 'string' ? c.header : undefined,
                      }
                    : {})}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
          {!loading && rows.length === 0 && (
            <tr {...rowRole}>
              <td
                colSpan={columns.length}
                className="sx-table__empty"
                {...(stacked ? { role: 'cell' } : {})}
              >
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
