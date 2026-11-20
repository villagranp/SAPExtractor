import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  useCatalog,
  useEntitySets,
  useSapSystems,
  type CatalogService,
} from '@/features/sap-systems/api/queries';
import { describeError } from '@/shared/api/errors';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DataTable,
  SelectField,
  Spinner,
  TextField,
  type Column,
} from '@/shared/ui';
import {
  useDdlPreview,
  type DdlPreview,
  type DestinationDetail,
  type LoadMode,
} from '../api/queries';

type PreviewColumn = DdlPreview['columns'][number];

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Nombre de tabla sugerido a partir del entity set (`EntityOf0FI_GL_14` → `FI_GL_14`). */
function suggestTarget(entitySet: string): string {
  const base = entitySet.replace(/^EntityOf/, '').replace(/[^A-Za-z0-9_]/g, '_');
  return /^[0-9]/.test(base) ? base.replace(/^[0-9]+/, '') || `T_${base}` : base;
}

/**
 * Vista previa del DDL: elige un entity set de un sistema SAP y muestra la tabla (o los
 * archivos) que se crearían en este destino. No ejecuta nada en el destino.
 */
export function DdlPreviewPanel({
  destination,
  supportedModes,
}: {
  destination: DestinationDetail;
  supportedModes: LoadMode[];
}) {
  const { t } = useTranslation();
  const systems = useSapSystems({ page: 1, page_size: 100 });
  const [systemId, setSystemId] = useState('');
  const catalog = useCatalog(systemId, { page: 1, page_size: 100 });
  const [serviceKey, setServiceKey] = useState('');
  const services = systemId ? (catalog.data?.items ?? []) : [];
  const service: CatalogService | null =
    services.find((s) => `${s.technical_name}|${String(s.version)}` === serviceKey) ?? null;
  const entitySets = useEntitySets(systemId, service);
  const [entitySet, setEntitySet] = useState('');
  const [target, setTarget] = useState('');
  const [mode, setMode] = useState<LoadMode>(supportedModes[0] ?? 'append');
  const [technical, setTechnical] = useState(true);
  const preview = useDdlPreview(destination.id);

  const columns: Column<PreviewColumn>[] = [
    {
      key: 'source',
      header: t('destinations.ddl.source'),
      render: (c) => (
        <span>
          <code>{c.source}</code>
          {c.is_key && <strong> · {t('destinations.ddl.key')}</strong>}
          {c.label && (
            <>
              <br />
              <span className="sx-muted">{c.label}</span>
            </>
          )}
        </span>
      ),
    },
    {
      key: 'target',
      header: t('destinations.ddl.target'),
      render: (c) => (c.target ? <code>{c.target}</code> : t('destinations.ddl.excluded')),
    },
    {
      key: 'edm',
      header: t('destinations.ddl.edmType'),
      render: (c) => <code>{c.edm_type}</code>,
    },
    {
      key: 'arrow',
      header: t('destinations.ddl.arrowType'),
      render: (c) => <code>{c.arrow_type}</code>,
    },
  ];

  const ready = Boolean(systemId && service && entitySet && IDENTIFIER.test(target));
  const run = () => {
    if (!service) return;
    preview.mutate({
      source: {
        sap_system_id: systemId,
        service: service.technical_name,
        version: service.version,
        entity_set: entitySet,
      },
      target_name: target,
      load_mode: mode,
      schema_evolution: 'add_columns',
      delete_handling: 'soft',
      technical_columns: technical,
    });
  };

  return (
    <div className="sx-stack">
      <Card title={t('destinations.ddl.title')}>
        <div className="sx-stack">
          <p className="sx-muted" style={{ margin: 0 }}>
            {t('destinations.ddl.help')}
          </p>
          <div className="sx-two-columns">
            <SelectField
              label={t('destinations.ddl.system')}
              placeholder={t('common.choose')}
              value={systemId}
              onChange={(e) => {
                setSystemId(e.target.value);
                setServiceKey('');
                setEntitySet('');
              }}
              options={(systems.data?.items ?? []).map((s) => ({
                value: s.id,
                label: `${s.code} · ${s.name}`,
              }))}
            />
            <SelectField
              label={t('destinations.ddl.service')}
              placeholder={
                catalog.isFetching && systemId ? t('common.loading') : t('common.choose')
              }
              value={serviceKey}
              disabled={!systemId}
              onChange={(e) => {
                setServiceKey(e.target.value);
                setEntitySet('');
              }}
              options={services.map((s) => ({
                value: `${s.technical_name}|${String(s.version)}`,
                label: s.title ? `${s.technical_name} · ${s.title}` : s.technical_name,
              }))}
            />
            <SelectField
              label={t('destinations.ddl.entitySet')}
              placeholder={entitySets.isFetching ? t('common.loading') : t('common.choose')}
              value={entitySet}
              disabled={!service}
              onChange={(e) => {
                setEntitySet(e.target.value);
                if (!target) setTarget(suggestTarget(e.target.value));
              }}
              options={(entitySets.data?.entity_sets ?? [])
                .filter((es) => es.odp_role !== 'delta_links')
                .map((es) => ({
                  value: es.name,
                  label: es.label ? `${es.name} · ${es.label}` : es.name,
                }))}
            />
            <TextField
              label={t('destinations.ddl.targetName')}
              value={target}
              hint={t('destinations.ddl.targetHint')}
              error={
                target && !IDENTIFIER.test(target) ? t('destinations.ddl.targetInvalid') : undefined
              }
              maxLength={128}
              onChange={(e) => {
                setTarget(e.target.value);
              }}
            />
            <SelectField
              label={t('destinations.fields.loadMode')}
              value={mode}
              onChange={(e) => {
                setMode(e.target.value as LoadMode);
              }}
              options={supportedModes.map((m) => ({
                value: m,
                label: t(`destinations.loadModes.${m}`),
              }))}
            />
            <Checkbox
              label={t('destinations.ddl.technicalColumns')}
              description={t('destinations.ddl.technicalHelp')}
              checked={technical}
              onChange={(e) => {
                setTechnical(e.target.checked);
              }}
            />
          </div>
          <div>
            <Button onClick={run} disabled={!ready || preview.isPending}>
              {t('destinations.ddl.generate')}
            </Button>
          </div>
        </div>
      </Card>
      {preview.isPending && <Spinner label={t('common.loading')} />}
      {preview.error && <Alert tone="danger">{describeError(preview.error).message}</Alert>}
      {preview.data && (
        <>
          <Card title={t('destinations.ddl.statements')}>
            <pre className="sx-code-block" data-testid="ddl-statements">
              {preview.data.statements.join(';\n\n')}
            </pre>
          </Card>
          <DataTable
            caption={t('destinations.ddl.columns')}
            columns={columns}
            rows={preview.data.columns}
            rowKey={(c) => c.source}
            empty="—"
          />
        </>
      )}
    </div>
  );
}
