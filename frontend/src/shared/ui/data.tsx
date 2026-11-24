import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { PAGE_SIZE_OPTIONS, usePageState } from './pagination';

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
  /**
   * Paginación en el servidor: `rows` ya es la página pedida. Sin ella, la tabla pagina en
   * el navegador: muestra 20 filas (`DEFAULT_PAGE_SIZE`) y deja elegir cuántas ver.
   */
  pagination?: PaginationProps;
}

export function DataTable<T>({ rows, pagination, ...rest }: DataTableProps<T>) {
  const local = usePageState();
  if (pagination) {
    return (
      <>
        <TableView rows={rows} {...rest} />
        <Pagination {...pagination} />
      </>
    );
  }
  const pages = Math.max(1, Math.ceil(rows.length / local.pageSize));
  // Si las filas cambian (un filtro), la página actual puede quedar fuera de rango.
  const page = Math.min(local.page, pages);
  const visible = rows.slice((page - 1) * local.pageSize, page * local.pageSize);
  return (
    <>
      <TableView rows={visible} {...rest} />
      <Pagination
        page={page}
        pageSize={local.pageSize}
        total={rows.length}
        onPageChange={local.setPage}
        onPageSizeChange={local.setPageSize}
      />
    </>
  );
}

function TableView<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  empty,
  onRowClick,
  stacked = false,
}: Omit<DataTableProps<T>, 'pagination'>) {
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
  /** Si se indica, se puede elegir cuántas filas ver por página. */
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: readonly number[];
  /** Textos propios; por defecto, los comunes de la aplicación. */
  labels?: Partial<{ previous: string; next: string; summary: string; pageSize: string }>;
}

/**
 * `labels.summary` admite los marcadores {from}, {to} y {total}. No se pinta si todo cabe
 * en una página del tamaño más pequeño: no hay nada que paginar ni que ampliar.
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  labels,
}: PaginationProps) {
  const { t } = useTranslation();
  const sizeId = useId();
  const smallest = Math.min(pageSize, ...(onPageSizeChange ? pageSizeOptions : []));
  if (total <= smallest) return null;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const summary = (labels?.summary ?? t('common.pageSummary'))
    .replace('{from}', String(from))
    .replace('{to}', String(to))
    .replace('{total}', String(total));
  return (
    <nav className="sx-pagination" aria-label={summary}>
      <span>{summary}</span>
      {onPageSizeChange && (
        <span className="sx-pagination__size">
          <label htmlFor={sizeId}>{labels?.pageSize ?? t('common.pageSize')}</label>
          <select
            id={sizeId}
            className="sx-input"
            value={pageSize}
            onChange={(e) => {
              onPageSizeChange(Number(e.target.value));
            }}
          >
            {pageSizeOptions.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </span>
      )}
      <div>
        <button
          type="button"
          className="sx-btn sx-btn--secondary"
          disabled={page <= 1}
          onClick={() => {
            onPageChange(page - 1);
          }}
        >
          {labels?.previous ?? t('common.previous')}
        </button>
        <button
          type="button"
          className="sx-btn sx-btn--secondary"
          disabled={page >= pages}
          onClick={() => {
            onPageChange(page + 1);
          }}
        >
          {labels?.next ?? t('common.next')}
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
