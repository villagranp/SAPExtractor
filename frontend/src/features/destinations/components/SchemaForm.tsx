import { useTranslation } from 'react-i18next';
import { Checkbox, SelectField, TextAreaField, TextField } from '@/shared/ui';
import { properties, type FormValues, type JsonObjectSchema } from './schemaValues';

export type { FormValues, JsonObjectSchema } from './schemaValues';

/**
 * Formulario generado a partir del JSON Schema que publica cada tipo de destino. Cubre lo
 * que usan los plugins: texto, enteros, booleanos, listas cerradas (`enum`) y secretos
 * (`format: password`).
 */
const ENUM_LABELS: Record<string, string> = {
  '\t': 'enumLabels.tab',
  '\n': 'enumLabels.lf',
  '\r\n': 'enumLabels.crlf',
  none: 'enumLabels.none',
  sql: 'enumLabels.sql',
  entra_service_principal: 'enumLabels.entra_service_principal',
};

interface SchemaFormProps {
  schema: JsonObjectSchema;
  values: FormValues;
  onChange: (values: FormValues) => void;
  /** Errores por campo (sin el prefijo `config.`/`secret.`). */
  errors?: Record<string, string | undefined>;
  /** Campos que no se pueden editar (p. ej. identidad de la cuenta al editar). */
  readOnly?: string[];
}

export function SchemaForm({
  schema,
  values,
  onChange,
  errors = {},
  readOnly = [],
}: SchemaFormProps) {
  const { t } = useTranslation();
  const required = new Set(schema.required ?? []);
  const set = (key: string, value: string | boolean) => {
    onChange({ ...values, [key]: value });
  };

  return (
    <div className="sx-stack">
      {properties(schema).map(([key, prop]) => {
        const label = `${prop.title ?? key}${required.has(key) ? '' : ` ${t('destinations.form.optional')}`}`;
        const common = {
          label,
          hint: prop.description,
          error: errors[key],
          disabled: readOnly.includes(key),
        };
        const value = values[key];
        if (prop.type === 'boolean') {
          return (
            <Checkbox
              key={key}
              label={prop.title ?? key}
              description={prop.description}
              checked={Boolean(value)}
              disabled={readOnly.includes(key)}
              onChange={(e) => {
                set(key, e.target.checked);
              }}
            />
          );
        }
        const text = typeof value === 'string' ? value : '';
        if (prop.enum) {
          return (
            <SelectField
              key={key}
              {...common}
              value={text}
              {...(required.has(key) && !prop.default ? { placeholder: t('common.choose') } : {})}
              onChange={(e) => {
                set(key, e.target.value);
              }}
              options={prop.enum.map((option) => {
                const raw = String(option);
                const labelKey = ENUM_LABELS[raw];
                return { value: raw, label: labelKey ? t(`destinations.form.${labelKey}`) : raw };
              })}
            />
          );
        }
        if (prop.format === 'password' && key.endsWith('_pem')) {
          return (
            <TextAreaField
              key={key}
              {...common}
              mono
              rows={6}
              value={text}
              placeholder={prop.description}
              hint={t('destinations.form.pemHint')}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                set(key, e.target.value);
              }}
            />
          );
        }
        return (
          <TextField
            key={key}
            {...common}
            type={
              prop.format === 'password' ? 'password' : prop.type === 'integer' ? 'number' : 'text'
            }
            autoComplete={prop.format === 'password' ? 'new-password' : 'off'}
            value={text}
            {...(prop.maxLength ? { maxLength: prop.maxLength } : {})}
            {...(prop.minimum !== undefined ? { min: prop.minimum } : {})}
            {...(prop.maximum !== undefined ? { max: prop.maximum } : {})}
            onChange={(e) => {
              set(key, e.target.value);
            }}
          />
        );
      })}
    </div>
  );
}
