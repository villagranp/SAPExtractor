import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Button, StatusPill, TextField } from './components';

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
});
