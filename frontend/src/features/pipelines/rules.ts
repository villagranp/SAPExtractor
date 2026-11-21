import type { LoadMode } from '@/features/destinations/api/queries';
import type { SourceSummary } from '@/features/sources/api/queries';
import type { ExtractionMode } from './api/queries';

/** Modos que admite el origen (plan 05: delta ODQ, marca de agua o solo carga completa). */
export function modesFor(source: SourceSummary | undefined): ExtractionMode[] {
  if (!source) return [];
  if (source.delta_method === 'ODQ') return ['DELTA', 'FULL'];
  if (source.delta_method === 'WATERMARK') return ['WATERMARK', 'FULL'];
  return ['FULL'];
}

/**
 * Modos de carga seguros (plan 06 §3.4): sin «upsert» en un delta que no entrega imágenes
 * posteriores y sin «reemplazar» fuera de la carga completa. La API aplica las mismas reglas.
 */
export function loadModesFor(
  mode: ExtractionMode | '',
  source: SourceSummary | undefined,
  supported: readonly LoadMode[],
): LoadMode[] {
  if (!mode || !source) return [];
  return supported.filter((m) => {
    if (m === 'replace') return mode === 'FULL';
    if (m === 'upsert' && mode === 'DELTA') return source.delta_semantics === 'after_image';
    return true;
  });
}

/** Carga propuesta: la más habitual entre las permitidas. */
export function preferredLoadMode(allowed: readonly LoadMode[]): LoadMode | '' {
  const order: LoadMode[] = ['upsert', 'replace', 'append'];
  return order.find((m) => allowed.includes(m)) ?? '';
}
