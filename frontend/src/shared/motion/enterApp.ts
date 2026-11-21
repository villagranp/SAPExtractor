import { useLayoutEffect } from 'react';

/*
 * Paso del inicio de sesión a la aplicación con una view transition (estilos en
 * shared/theme/motion.css).
 *
 * En cuanto la sesión queda activa, el login se desmonta en un render síncrono, así que la
 * transición tiene que empezar antes: el navegador fotografía el login, `commit` escribe la
 * sesión, React monta la aplicación y `useAppShellReady` avisa de que ya está en el DOM. Sin
 * soporte del navegador, el cambio es inmediato.
 */

const ATTRIBUTE = 'data-sx-transition';
/** Si la aplicación no llega a montarse, la pantalla no se queda congelada más de esto. */
const SHELL_TIMEOUT_MS = 1500;

let signalShellReady: (() => void) | null = null;

export async function enterApp(commit: () => void): Promise<void> {
  if (!('startViewTransition' in document)) {
    commit();
    return;
  }
  const root = document.documentElement;
  root.setAttribute(ATTRIBUTE, 'enter-app');
  const transition = document.startViewTransition(async () => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ready = new Promise<void>((resolve) => {
      signalShellReady = resolve;
      timer = setTimeout(resolve, SHELL_TIMEOUT_MS);
    });
    commit();
    await ready;
    clearTimeout(timer);
    signalShellReady = null;
  });
  const cleanup = () => {
    root.removeAttribute(ATTRIBUTE);
  };
  transition.finished.then(cleanup, cleanup);
  // La animación sigue sola; quien llama solo espera a que la aplicación esté montada.
  await transition.updateCallbackDone;
}

/** Lo llama el layout de la aplicación: ya está en el DOM y la transición puede animarse. */
export function useAppShellReady(): void {
  useLayoutEffect(() => {
    signalShellReady?.();
  }, []);
}
