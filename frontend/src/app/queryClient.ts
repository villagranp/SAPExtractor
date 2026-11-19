import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import { authKeys } from '@/features/auth/api/queries';
import { ApiError } from '@/shared/api/client';

export function createQueryClient() {
  // Si la sesión caduca, cualquier 401 devuelve al login (RequireAuth observa /me).
  const onError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      queryClient.setQueryData(authKeys.me, null);
    }
  };
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        // Los errores 401 del propio login / MFA se muestran en el formulario.
        const key = mutation.options.mutationKey?.[0];
        if (key !== 'auth-step') onError(error);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // No reintentar errores del cliente (4xx): no se van a arreglar solos.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      },
    },
  });
  return queryClient;
}
