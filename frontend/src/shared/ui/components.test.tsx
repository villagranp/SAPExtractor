import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, StatusPill, TextField } from './components';
import { DataTable, Pagination } from './data';

describe('shared/ui', () => {
  it('Button es type=button por defecto y ejecuta onClick', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Guardar</Button>);
    const button = screen.getByRole('button', { name: 'Guardar' });
    expect(button).toHaveAttribute('type', 'button');
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('StatusPill expone el tono', () => {
    render(<StatusPill tone="danger">Fallo</StatusPill>);
    expect(screen.getByText('Fallo')).toHaveAttribute('data-tone', 'danger');
  });

  it('TextField asocia etiqueta y error accesible', () => {
    render(<TextField label="Usuario" error="Obligatorio" />);
    const input = screen.getByLabelText('Usuario');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Obligatorio');
  });

  it('DataTable muestra 20 filas al inicio y deja ver más', async () => {
    const rows = Array.from({ length: 45 }, (_, i) => ({ id: `fila-${String(i + 1)}` }));
    render(
      <DataTable
        caption="Filas"
        rows={rows}
        rowKey={(r) => r.id}
        columns={[{ key: 'id', header: 'Id', render: (r) => r.id }]}
      />,
    );
    expect(screen.getAllByRole('row')).toHaveLength(21); // cabecera + 20
    expect(screen.getByText('1–20 de 45')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(screen.getByText('fila-21')).toBeInTheDocument();
    expect(screen.queryByText('fila-1')).not.toBeInTheDocument();
    // Cambiar el tamaño vuelve a la primera página.
    await userEvent.selectOptions(screen.getByLabelText('Filas por página'), '50');
    expect(screen.getAllByRole('row')).toHaveLength(46);
    expect(screen.getByText('1–45 de 45')).toBeInTheDocument();
  });

  it('DataTable sin más de 20 filas no muestra paginación', () => {
    render(
      <DataTable
        rows={[{ id: 'a' }]}
        rowKey={(r) => r.id}
        columns={[{ key: 'id', header: 'Id', render: (r) => r.id }]}
      />,
    );
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });

  it('Pagination de servidor avisa del nuevo tamaño', async () => {
    const onPageSizeChange = vi.fn();
    render(
      <Pagination
        page={2}
        pageSize={20}
        total={130}
        onPageChange={vi.fn()}
        onPageSizeChange={onPageSizeChange}
      />,
    );
    expect(screen.getByText('21–40 de 130')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Filas por página'), '100');
    expect(onPageSizeChange).toHaveBeenCalledWith(100);
  });
});
