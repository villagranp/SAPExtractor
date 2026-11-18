import { createBrowserRouter, type RouteObject } from 'react-router';
import { LoginPage } from '@/features/auth/pages/LoginPage';
import { HomePage } from '@/features/system/pages/HomePage';
import { AppShell } from './layout/AppShell';
import { NotFoundPage } from './pages/NotFoundPage';

const devRoutes: RouteObject[] = import.meta.env.DEV
  ? [
      {
        path: 'ui-kit',
        lazy: async () => {
          const { UiKitPage } = await import('@/features/dev/UiKitPage');
          return { Component: UiKitPage };
        },
      },
    ]
  : [];

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      ...devRoutes,
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
