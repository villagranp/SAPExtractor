import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Checklist,
  ConfirmDialog,
  DataTable,
  DetailList,
  EmptyState,
  OneTimeSecret,
  PageHeader,
  Pagination,
  SelectField,
  Spinner,
  StatusPill,
  Stepper,
  Tabs,
  TextAreaField,
  TextField,
} from '@/shared/ui';

const rows = [
  { id: '1', name: 'ZFI_WS_GL_ITEMS_SRV', state: 'Activo' },
  { id: '2', name: 'ZSD_WS_SALES_ITEMS_SRV', state: 'Pausado' },
];

/** Catálogo visual del design system (solo en desarrollo). Sirve para validar la plantilla. */
export function UiKitPage() {
  const [tab, setTab] = useState('a');
  const [page, setPage] = useState(1);
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      <PageHeader
        title="UI kit"
        description="Componentes de @/shared/ui con los tokens actuales."
        actions={<Button variant="secondary">Acción</Button>}
      />
      <div className="sx-grid">
        <Card title="Botones">
          <div className="sx-row">
            <Button>Primario</Button>
            <Button variant="secondary">Secundario</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Peligro</Button>
            <Button disabled>Deshabilitado</Button>
          </div>
        </Card>
        <Card title="Estados">
          <div className="sx-row">
            <StatusPill tone="success">Éxito</StatusPill>
            <StatusPill tone="warning">Aviso</StatusPill>
            <StatusPill tone="danger">Fallo</StatusPill>
            <StatusPill tone="info">En curso</StatusPill>
            <StatusPill tone="neutral">Pausado</StatusPill>
          </div>
        </Card>
        <Card title="Formulario">
          <TextField label="Nombre" hint="Texto de ayuda" />
          <TextField label="Con error" error="Campo obligatorio" />
          <SelectField
            label="Selección"
            placeholder="Todos"
            options={[
              { value: 'a', label: 'Opción A' },
              { value: 'b', label: 'Opción B' },
            ]}
          />
          <Checkbox label="Casilla" description="Con descripción" />
        </Card>
        <Card title="Avisos y carga">
          <Alert tone="info" title="Información">
            Mensaje informativo.
          </Alert>
          <Alert tone="danger">Error al guardar.</Alert>
          <Spinner label="Cargando…" />
        </Card>
        <Card title="Detalle y secreto">
          <DetailList
            items={[
              { label: 'Sistema', value: 'ECC PRD' },
              { label: 'Cliente', value: '300' },
            ]}
          />
          <OneTimeSecret
            label="Token"
            value="sxp_ejemplo"
            warning="Solo se muestra una vez."
            copyLabel="Copiar"
            copiedLabel="Copiado"
          />
        </Card>
        <Card title="Asistente y comprobación">
          <Stepper
            label="Pasos"
            current={1}
            steps={[
              { id: 'a', label: 'Datos' },
              { id: 'b', label: 'Conexión' },
              { id: 'c', label: 'Resumen' },
            ]}
          />
          <Checklist
            label="Prueba"
            items={[
              { id: '1', title: 'Red', tone: 'success', statusLabel: 'Correcto', meta: '12 ms' },
              { id: '2', title: 'Autenticación', tone: 'danger', statusLabel: 'Error' },
            ]}
          />
          <TextAreaField label="PEM" mono rows={2} placeholder="-----BEGIN CERTIFICATE-----" />
        </Card>
        <Card title="Diálogo">
          <Button
            onClick={() => {
              setConfirm(true);
            }}
          >
            Abrir confirmación
          </Button>
        </Card>
      </div>
      <div style={{ marginTop: 'var(--sx-space-4)' }}>
        <Tabs
          label="Pestañas"
          active={tab}
          onChange={setTab}
          tabs={[
            { id: 'a', label: 'Datos' },
            { id: 'b', label: 'Actividad' },
          ]}
        />
        <DataTable
          caption="Tabla"
          columns={[
            { key: 'name', header: 'Servicio', render: (r) => <code>{r.name}</code> },
            { key: 'state', header: 'Estado', render: (r) => r.state },
          ]}
          rows={rows}
          rowKey={(r) => r.id}
        />
        <Pagination
          page={page}
          pageSize={2}
          total={6}
          onPageChange={setPage}
          labels={{ previous: 'Anterior', next: 'Siguiente', summary: '{from}–{to} de {total}' }}
        />
      </div>
      <div style={{ marginTop: 'var(--sx-space-4)' }}>
        <EmptyState title="Sin pipelines" action={<Button>Crear pipeline</Button>}>
          Todavía no hay datos.
        </EmptyState>
      </div>
      <ConfirmDialog
        open={confirm}
        title="Confirmar"
        message="¿Seguro?"
        confirmLabel="Confirmar"
        cancelLabel="Cancelar"
        reasonLabel="Motivo"
        onConfirm={() => {
          setConfirm(false);
        }}
        onClose={() => {
          setConfirm(false);
        }}
      />
    </>
  );
}
