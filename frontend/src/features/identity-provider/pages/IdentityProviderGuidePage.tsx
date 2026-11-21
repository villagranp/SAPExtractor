import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Card, PageHeader } from '@/shared/ui';
import '../identity-provider.css';

const SECTIONS = ['register', 'credential', 'groups', 'permissions', 'activate'] as const;

/** Guía del *App registration* para IT y el administrador de la plataforma (plan 02 §4). */
export function IdentityProviderGuidePage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader
        title={t('identityProvider.guide.title')}
        description={t('identityProvider.guide.description')}
        actions={<Link to="/admin/identity-provider">{t('identityProvider.guide.back')}</Link>}
      />
      <div className="sx-stack sx-idp__guide">
        {SECTIONS.map((id) => {
          const steps = t(`identityProvider.guide.${id}.steps`, {
            returnObjects: true,
          }) as string[];
          return (
            <Card key={id} title={t(`identityProvider.guide.${id}.title`)}>
              <div className="sx-stack">
                <p>{t(`identityProvider.guide.${id}.intro`)}</p>
                <ol>
                  {steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
                {id === 'credential' && (
                  <>
                    <p>{t('identityProvider.guide.credential.commandIntro')}</p>
                    <pre className="sx-idp__command">
                      <code>{t('identityProvider.guide.credential.command')}</code>
                    </pre>
                  </>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
