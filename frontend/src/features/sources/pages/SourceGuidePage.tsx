import { useTranslation } from 'react-i18next';
import { Card, PageHeader } from '@/shared/ui';
import '../sources.css';

const SECTIONS = ['extractors', 'cdsOdp', 'cdsOdata', 'user'] as const;

/** Guía para el equipo SAP: cómo publicar un origen para que la plataforma lo lea (plan 05 §5). */
export function SourceGuidePage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t('sources.guide.title')} description={t('sources.guide.description')} />
      <div className="sx-stack sx-sources__guide">
        {SECTIONS.map((id) => {
          const steps = t(`sources.guide.${id}.steps`, { returnObjects: true }) as string[];
          return (
            <Card key={id} title={t(`sources.guide.${id}.title`)}>
              <div className="sx-stack">
                <p>{t(`sources.guide.${id}.intro`)}</p>
                <ol>
                  {steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </div>
            </Card>
          );
        })}
        <Card title={t('sources.guide.naming.title')}>
          <p>{t('sources.guide.naming.text')}</p>
        </Card>
      </div>
    </>
  );
}
