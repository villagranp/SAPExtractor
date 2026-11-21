import { fireEvent, render, screen } from '@testing-library/react';
import { useShake } from './useShake';

function Target() {
  const { ref, shake } = useShake<HTMLDivElement>();
  return (
    <>
      <div ref={ref} data-testid="target" />
      <button type="button" onClick={shake}>
        Sacudir
      </button>
    </>
  );
}

describe('useShake', () => {
  it('sacude al pedirlo y suelta la clase al terminar la animación', () => {
    render(<Target />);
    const target = screen.getByTestId('target');
    fireEvent.click(screen.getByRole('button', { name: 'Sacudir' }));
    expect(target).toHaveClass('sx-shaking');
    fireEvent.animationEnd(target);
    expect(target).not.toHaveClass('sx-shaking');
  });

  it('ignora animaciones que terminan en los hijos', () => {
    render(<Target />);
    const target = screen.getByTestId('target');
    const child = document.createElement('span');
    target.append(child);
    fireEvent.click(screen.getByRole('button', { name: 'Sacudir' }));
    fireEvent.animationEnd(child);
    expect(target).toHaveClass('sx-shaking');
  });
});
