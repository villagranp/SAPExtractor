import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, TextField } from '@/shared/ui';

/** URI de redirección calculada por la API: solo lectura, para copiarla al registro de Entra. */
export function RedirectUriField({ value }: { value: string }) {
  const { t } = useTranslation();
  const hintId = useId();
  const [copied, setCopied] = useState(false);
  return (
    <div className="sx-idp__redirect">
      <div className="sx-idp__copy-row">
        <TextField
          label={t('identityProvider.app.redirectUri')}
          value={value}
          readOnly
          spellCheck={false}
          className="sx-idp__mono"
          aria-describedby={hintId}
          onFocus={(e) => {
            e.currentTarget.select();
          }}
        />
        <Button
          variant="secondary"
          onClick={() => {
            // Sin HTTPS (fuera de localhost) el navegador no expone la API del portapapeles.
            const clipboard = navigator.clipboard as Clipboard | undefined;
            void clipboard?.writeText(value).then(() => {
              setCopied(true);
            });
          }}
        >
          {copied ? t('common.copied') : t('common.copy')}
        </Button>
      </div>
      <p id={hintId} className="sx-field__hint">
        {t('identityProvider.app.redirectHint')}
      </p>
    </div>
  );
}
