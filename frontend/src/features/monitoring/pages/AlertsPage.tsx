import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useCan } from '@/features/auth/api/queries';
import { FilterBar, PageHeader, SelectField, Tabs } from '@/shared/ui';
import type { AlertStatus } from '../api/queries';
import { AlertsTable } from '../components/AlertsTable';
import { ChannelsTab } from '../components/ChannelsTab';
import { RulesTab } from '../components/RulesTab';
import '../monitoring.css';

type Tab = 'live' | 'history' | 'rules' | 'channels';
const STATUSES: AlertStatus[] = ['open', 'acknowledged', 'resolved'];

/** Alertas abiertas, historial, reglas y canales de aviso (plan 09 §3, plan 10 §3). */
export function AlertsPage() {
  const { t } = useTranslation();
  const canManage = useCan('alert:manage');
  const [tab, setTab] = useState<Tab>('live');
  const [status, setStatus] = useState<AlertStatus | ''>('');
  const tabs: Tab[] = canManage
    ? ['live', 'history', 'rules', 'channels']
    : ['live', 'history', 'rules'];

  return (
    <>
      <PageHeader
        title={t('monitoring.alerts.title')}
        description={t('monitoring.alerts.description')}
      />
      <Tabs
        label={t('monitoring.alerts.tabsLabel')}
        active={tab}
        onChange={(id) => {
          setTab(id as Tab);
        }}
        tabs={tabs.map((id) => ({ id, label: t(`monitoring.alerts.tabs.${id}`) }))}
      />
      {tab === 'live' && <AlertsTable live />}
      {tab === 'history' && (
        <>
          <FilterBar>
            <SelectField
              label={t('monitoring.alerts.status')}
              placeholder={t('common.all')}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as AlertStatus | '');
              }}
              options={STATUSES.map((s) => ({
                value: s,
                label: t(`monitoring.alertStatuses.${s}`),
              }))}
            />
          </FilterBar>
          <AlertsTable live={false} {...(status ? { status } : {})} />
        </>
      )}
      {tab === 'rules' && <RulesTab />}
      {tab === 'channels' && canManage && <ChannelsTab />}
    </>
  );
}
