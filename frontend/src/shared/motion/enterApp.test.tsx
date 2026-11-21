import { render } from '@testing-library/react';
import { enterApp, useAppShellReady } from './enterApp';

function Shell() {
  useAppShellReady();
  return <div>app</div>;
}

type StartViewTransition = (update: () => Promise<void>) => ViewTransition;

function mockViewTransitions() {
  let finish: () => void = () => undefined;
  const start = vi.fn<StartViewTransition>((update) => {
    const updateCallbackDone = update();
    const finished = updateCallbackDone.then(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    return { updateCallbackDone, finished } as unknown as ViewTransition;
  });
  Object.defineProperty(document, 'startViewTransition', { value: start, configurable: true });
  return {
    start,
    finish: () => {
      finish();
    },
  };
}

describe('enterApp', () => {
  afterEach(() => {
    Reflect.deleteProperty(document, 'startViewTransition');
    document.documentElement.removeAttribute('data-sx-transition');
  });

  it('sin view transitions aplica el cambio de inmediato', async () => {
    const commit = vi.fn();
    await enterApp(commit);
    expect(commit).toHaveBeenCalledOnce();
    expect(document.documentElement).not.toHaveAttribute('data-sx-transition');
  });

  it('espera a que la aplicación se monte y limpia el atributo al terminar', async () => {
    const { start, finish } = mockViewTransitions();
    let mounted = false;
    const done = enterApp(() => {
      render(<Shell />);
      mounted = true;
    }).then(() => mounted);

    expect(start).toHaveBeenCalledOnce();
    expect(document.documentElement).toHaveAttribute('data-sx-transition', 'enter-app');
    await expect(done).resolves.toBe(true);

    finish();
    await vi.waitFor(() => {
      expect(document.documentElement).not.toHaveAttribute('data-sx-transition');
    });
  });
});
