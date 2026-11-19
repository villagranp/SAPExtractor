import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';
import { createQueryClient } from '@/app/queryClient';

interface Options {
  route?: string;
  /** Rutas adicionales (p. ej. destino de una redirección). */
  routes?: RouteObject[];
}

/** Renderiza un elemento con TanStack Query y router en memoria. */
export function renderWithProviders(ui: ReactElement, { route = '/', routes = [] }: Options = {}) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter([...routes, { path: '*', element: ui }], {
    initialEntries: [route],
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient };
}
