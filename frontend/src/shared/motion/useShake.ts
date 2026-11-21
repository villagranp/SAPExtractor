import { useCallback, useEffect, useRef } from 'react';

const SHAKING = 'sx-shaking';

/**
 * Sacudida horizontal breve para un rechazo, como unas credenciales incorrectas. Fotogramas y
 * duración en shared/theme/motion.css; con movimiento reducido no hay desplazamiento.
 */
export function useShake<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const done = (event: AnimationEvent) => {
      if (event.target === el) el.classList.remove(SHAKING);
    };
    el.addEventListener('animationend', done);
    el.addEventListener('animationcancel', done);
    return () => {
      el.removeEventListener('animationend', done);
      el.removeEventListener('animationcancel', done);
    };
  }, []);

  const shake = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.classList.remove(SHAKING);
    // Forzar el layout reinicia la animación si llega otro rechazo a mitad de la sacudida.
    el.getBoundingClientRect();
    el.classList.add(SHAKING);
  }, []);

  return { ref, shake };
}
