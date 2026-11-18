import { Button, Card, EmptyState, PageHeader, StatusPill, TextField } from '@/shared/ui';

/** Catálogo visual del design system (solo en desarrollo). Sirve para validar la plantilla. */
export function UiKitPage() {
  return (
    <>
      <PageHeader
        title="UI kit"
        description="Componentes de @/shared/ui con los tokens actuales."
        actions={<Button variant="secondary">Acción</Button>}
      />
      <div className="sx-grid">
        <Card title="Botones">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sx-space-2)' }}>
            <Button>Primario</Button>
            <Button variant="secondary">Secundario</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Peligro</Button>
            <Button disabled>Deshabilitado</Button>
          </div>
        </Card>
        <Card title="Estados">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--sx-space-2)' }}>
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
        </Card>
      </div>
      <div style={{ marginTop: 'var(--sx-space-4)' }}>
        <EmptyState title="Sin pipelines" action={<Button>Crear pipeline</Button>}>
          Todavía no hay datos.
        </EmptyState>
      </div>
    </>
  );
}
