import type { ReactNode } from 'react';
import { createBrowserRouter, type RouteObject } from 'react-router';
import { ApiTokensPage } from '@/features/api-tokens/pages/ApiTokensPage';
import { AuditPage } from '@/features/audit/pages/AuditPage';
import { DestinationDetailPage } from '@/features/destinations/pages/DestinationDetailPage';
import { DestinationsPage } from '@/features/destinations/pages/DestinationsPage';
import { DestinationWizardPage } from '@/features/destinations/pages/DestinationWizardPage';
import { LoginPage } from '@/features/auth/pages/LoginPage';
import { AlertsPage } from '@/features/monitoring/pages/AlertsPage';
import { DashboardPage } from '@/features/monitoring/pages/DashboardPage';
import { PipelineDetailPage } from '@/features/pipelines/pages/PipelineDetailPage';
import { PipelinesPage } from '@/features/pipelines/pages/PipelinesPage';
import { PipelineWizardPage } from '@/features/pipelines/pages/PipelineWizardPage';
import { RunDetailPage } from '@/features/runs/pages/RunDetailPage';
import { RunsPage } from '@/features/runs/pages/RunsPage';
import { ProfilePage } from '@/features/auth/pages/ProfilePage';
import { IdentityProviderGuidePage } from '@/features/identity-provider/pages/IdentityProviderGuidePage';
import { IdentityProviderPage } from '@/features/identity-provider/pages/IdentityProviderPage';
import { RoleEditorPage } from '@/features/roles/pages/RoleEditorPage';
import { RolesPage } from '@/features/roles/pages/RolesPage';
import { SapSystemDetailPage } from '@/features/sap-systems/pages/SapSystemDetailPage';
import { SapSystemsPage } from '@/features/sap-systems/pages/SapSystemsPage';
import { SapSystemWizardPage } from '@/features/sap-systems/pages/SapSystemWizardPage';
import { SettingsPage } from '@/features/settings/pages/SettingsPage';
import { SourceDetailPage } from '@/features/sources/pages/SourceDetailPage';
import { SourceGuidePage } from '@/features/sources/pages/SourceGuidePage';
import { SourcesPage } from '@/features/sources/pages/SourcesPage';
import { SourceWizardPage } from '@/features/sources/pages/SourceWizardPage';
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
      guarded('pipelines', 'pipeline:read', <PipelinesPage />),
      guarded('pipelines/new', 'pipeline:manage', <PipelineWizardPage />),
      guarded('pipelines/:pipelineId', 'pipeline:read', <PipelineDetailPage />),
      guarded('pipelines/:pipelineId/edit', 'pipeline:manage', <PipelineWizardPage />),
      guarded('runs', 'pipeline:read', <RunsPage />),
      guarded('runs/:runId', 'pipeline:read', <RunDetailPage />),
      guarded('sap-systems', 'sap_connection:read', <SapSystemsPage />),
      guarded('sap-systems/new', 'sap_connection:manage', <SapSystemWizardPage />),
      guarded('sap-systems/:systemId', 'sap_connection:read', <SapSystemDetailPage />),
      guarded('sap-systems/:systemId/edit', 'sap_connection:manage', <SapSystemWizardPage />),
      guarded('sources', 'source:read', <SourcesPage />),
      guarded('sources/new', 'source:manage', <SourceWizardPage />),
      guarded('sources/guide', 'source:read', <SourceGuidePage />),
      guarded('sources/:sourceId', 'source:read', <SourceDetailPage />),
      guarded('sources/:sourceId/edit', 'source:manage', <SourceWizardPage />),
      guarded('destinations', 'destination:read', <DestinationsPage />),
      guarded('destinations/new', 'destination:manage', <DestinationWizardPage />),
      guarded('destinations/:destinationId', 'destination:read', <DestinationDetailPage />),
      guarded('destinations/:destinationId/edit', 'destination:manage', <DestinationWizardPage />),
      guarded('dashboard', 'dashboard:read', <DashboardPage />),
      guarded('alerts', 'dashboard:read', <AlertsPage />),
      guarded('admin/users', 'user:read', <UsersPage />),
      guarded('admin/users/:userId', 'user:read', <UserDetailPage />),
      guarded('admin/roles', 'role:read', <RolesPage />),
      guarded('admin/roles/new', 'role:manage', <RoleEditorPage />),
      guarded('admin/roles/:roleId', 'role:read', <RoleEditorPage />),
      guarded('admin/api-tokens', 'api_token:manage', <ApiTokensPage />),
      guarded('admin/audit', 'audit:read', <AuditPage />),
      guarded('admin/settings', 'pipeline:read', <SettingsPage />),
      guarded('admin/identity-provider', 'idp:manage', <IdentityProviderPage />),
      guarded('admin/identity-provider/guide', 'idp:manage', <IdentityProviderGuidePage />),
      ...devRoutes,
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
