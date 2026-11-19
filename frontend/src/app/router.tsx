import type { ReactNode } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router';
import { ApiTokensPage } from '@/features/api-tokens/pages/ApiTokensPage';
import { AuditPage } from '@/features/audit/pages/AuditPage';
import { LoginPage } from '@/features/auth/pages/LoginPage';
import { ProfilePage } from '@/features/auth/pages/ProfilePage';
import { RoleEditorPage } from '@/features/roles/pages/RoleEditorPage';
import { RolesPage } from '@/features/roles/pages/RolesPage';
import { SapSystemDetailPage } from '@/features/sap-systems/pages/SapSystemDetailPage';
import { SapSystemsPage } from '@/features/sap-systems/pages/SapSystemsPage';
import { SapSystemWizardPage } from '@/features/sap-systems/pages/SapSystemWizardPage';
import { HomePage } from '@/features/system/pages/HomePage';
import { UserDetailPage } from '@/features/users/pages/UserDetailPage';
import { UsersPage } from '@/features/users/pages/UsersPage';
import { RequireAuth, RequirePermission } from './guards';
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

function guarded(path: string, permission: string, element: ReactNode): RouteObject {
  return {
    path,
    element: <RequirePermission permission={permission}>{element}</RequirePermission>,
  };
}

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <HomePage /> },
      { path: 'profile', element: <ProfilePage /> },
      guarded('sap-systems', 'sap_connection:read', <SapSystemsPage />),
      guarded('sap-systems/new', 'sap_connection:manage', <SapSystemWizardPage />),
      guarded('sap-systems/:systemId', 'sap_connection:read', <SapSystemDetailPage />),
      guarded('sap-systems/:systemId/edit', 'sap_connection:manage', <SapSystemWizardPage />),
      guarded('admin/users', 'user:read', <UsersPage />),
      guarded('admin/users/:userId', 'user:read', <UserDetailPage />),
      guarded('admin/roles', 'role:read', <RolesPage />),
      guarded('admin/roles/new', 'role:manage', <RoleEditorPage />),
      guarded('admin/roles/:roleId', 'role:read', <RoleEditorPage />),
      guarded('admin/api-tokens', 'api_token:manage', <ApiTokensPage />),
      guarded('admin/audit', 'audit:read', <AuditPage />),
      ...devRoutes,
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
