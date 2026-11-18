import createClient, { type Middleware } from 'openapi-fetch';
import type { components, paths } from './schema';

export type ProblemDetails = components['schemas']['ProblemDetails'];

/** Error de la API con el cuerpo Problem Details (RFC 9457) ya interpretado. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails | null;

  constructor(status: number, problem: ProblemDetails | null) {
    super(problem?.detail ?? problem?.title ?? `Error HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
  }
}

function readCookie(name: string): string | undefined {
  return document.cookie
    .split('; ')
    .find((c) => c.startsWith(`${name}=`))
    ?.split('=')[1];
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Envía la cabecera CSRF (cookie sx_csrf, iteración 1) en métodos que modifican datos. */
const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (UNSAFE_METHODS.has(request.method)) {
      const token = readCookie('sx_csrf');
      if (token) request.headers.set('X-CSRF-Token', decodeURIComponent(token));
    }
    return request;
  },
};

// URL absoluta: necesaria fuera del navegador (pruebas en Node/jsdom).
// `fetch` se resuelve en cada llamada para que los mocks de pruebas (MSW) puedan interceptarla.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'same-origin',
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrfMiddleware);

function isProblem(value: unknown): value is ProblemDetails {
  return typeof value === 'object' && value !== null && 'status' in value && 'code' in value;
}

/**
 * Desenvuelve la respuesta de openapi-fetch: devuelve `data` o lanza `ApiError`.
 * Pensado para usarse dentro de las queryFn / mutationFn de TanStack Query.
 */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data;
  const problem = isProblem(result.error) ? result.error : null;
  throw new ApiError(result.response.status, problem);
}
