import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { EnvironmentPill } from '@/features/sources/components/SourcePills';
import type { ExtractionMode, LoadMode, PipelineStatus } from '../api/queries';

export interface RouteStop {
  code: string;
  name: string;
  /** Sistema SAP del origen o tipo del destino. */
  detail: ReactNode;
  environment: string;
}

export interface RouteStripProps {
  source: RouteStop | null;
  destination: RouteStop | null;
  extractionMode: ExtractionMode;
  loadMode: LoadMode;
  /** Con el pipeline activo, el conducto se ve en azul de aura (estado activo). */
  status?: PipelineStatus;
}

function Stop({ label, empty, stop }: { label: string; empty: string; stop: RouteStop | null }) {
  return (
    <div className={stop ? 'sx-pipelines__stop' : 'sx-pipelines__stop sx-pipelines__stop--empty'}>
      <span className="sx-pipelines__stop-label">{label}</span>
      {stop ? (
        <>
          <strong className="sx-pipelines__stop-code">{stop.code}</strong>
          <span className="sx-pipelines__stop-name">{stop.name}</span>
          <span className="sx-row">
            <EnvironmentPill environment={stop.environment} />
            <span className="sx-muted">{stop.detail}</span>
          </span>
        </>
      ) : (
        <span>{empty}</span>
      )}
    </div>
  );
}

/**
 * Recorrido del pipeline: un origen, un conducto y un destino (plan 08: un pipeline es siempre
 * un origen hacia un destino). Aparece en el asistente, donde se va completando, y en la
 * cabecera del detalle. El orden de lectura da el sentido; no hay flechas.
 */
export function RouteStrip({
  source,
  destination,
  extractionMode,
  loadMode,
  status,
}: RouteStripProps) {
  const { t } = useTranslation();
  return (
    <div
      className="sx-pipelines__route"
      role="group"
      aria-label={t('pipelines.route.label')}
      data-status={status}
    >
      <Stop
        label={t('pipelines.route.source')}
        empty={t('pipelines.route.noSource')}
        stop={source}
      />
      <p className="sx-pipelines__conduit">
        {/* Sin origen todavía no hay modo que anunciar: queda solo el conducto. */}
        {source && (
          <span>
            {t(`pipelines.extractionModes.${extractionMode}`)}
            <br />
            {t(`pipelines.loadModes.${loadMode}`)}
          </span>
        )}
      </p>
      <Stop
        label={t('pipelines.route.destination')}
        empty={t('pipelines.route.noDestination')}
        stop={destination}
      />
    </div>
  );
}
