import { useCallback, useState } from 'react';

/* Estado de paginación compartido por las tablas (DataTable, Pagination). */

/** Filas por página al abrir cualquier tabla. */
export const DEFAULT_PAGE_SIZE = 20;
/** Tamaños que se pueden elegir; el mayor no supera el máximo de la API (200). */
export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

/** Estado de página y tamaño; cambiar el tamaño vuelve a la primera página. */
export function usePageState(initialSize: number = DEFAULT_PAGE_SIZE) {
  const [page, setPage] = useState(1);
  const [pageSize, setSize] = useState(initialSize);
  const setPageSize = useCallback((size: number) => {
    setSize(size);
    setPage(1);
  }, []);
  return { page, setPage, pageSize, setPageSize };
}
