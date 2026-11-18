import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { createQueryClient } from '@/app/queryClient';

/** Renderiza un elemento con TanStack Query y router en memoria. */
export function renderWithProviders(ui: ReactElement, { route = '/' } = {}) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter([{ path: '*', element: ui }], { initialEntries: [route] });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
