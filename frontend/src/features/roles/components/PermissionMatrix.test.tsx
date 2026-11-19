import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { permissionCatalog } from '@/test/handlers';
import { PermissionMatrix } from './PermissionMatrix';

function Harness({ initial = [] as string[] }) {
  const [selected, setSelected] = useState(initial);
  return (
    <>
      <PermissionMatrix
        permissions={permissionCatalog}
        selected={selected}
        onChange={setSelected}
      />
      <output>{selected.join(',')}</output>
    </>
  );
}

describe('PermissionMatrix', () => {
  it('agrupa por módulo y permite marcar el módulo completo', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByText('Identidad')).toBeInTheDocument();
    expect(screen.getByText('Auditoría')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Seleccionar todo Identidad'));
    expect(screen.getByText('user:create,user:read', { selector: 'output' })).toBeInTheDocument();
    expect(screen.getByText('2 de 2 permisos')).toBeInTheDocument();

    await user.click(screen.getByLabelText(/^Ver usuarios/));
    expect(screen.getByText('user:create', { selector: 'output' })).toBeInTheDocument();
    expect(screen.getByLabelText('Seleccionar todo Identidad')).toHaveProperty(
      'indeterminate',
      true,
    );
  });
});
