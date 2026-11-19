import { useTranslation } from 'react-i18next';
import { Checkbox } from '@/shared/ui';
import { groupByModule, type Permission } from '../api/queries';

export interface PermissionMatrixProps {
  permissions: Permission[];
  selected: string[];
  onChange: (selected: string[]) => void;
  disabled?: boolean;
}

/** Casillas de permisos agrupadas por módulo, con selección de módulo completo. */
export function PermissionMatrix({
  permissions,
  selected,
  onChange,
  disabled = false,
}: PermissionMatrixProps) {
  const { t } = useTranslation();
  const set = new Set(selected);
  const toggle = (codes: string[], on: boolean) => {
    const next = new Set(set);
    for (const code of codes) {
      if (on) next.add(code);
      else next.delete(code);
    }
    onChange([...next].sort());
  };

  return (
    <div className="sx-matrix">
      {groupByModule(permissions).map((group) => {
        const codes = group.items.map((p) => p.code);
        const count = codes.filter((c) => set.has(c)).length;
        return (
          <fieldset key={group.module} className="sx-fieldset sx-matrix__group">
            <legend>
              <Checkbox
                label={<strong>{group.name}</strong>}
                description={t('roles.selectedCount', { count, total: codes.length })}
                checked={count === codes.length}
                indeterminate={count > 0 && count < codes.length}
                disabled={disabled}
                onChange={(e) => {
                  toggle(codes, e.target.checked);
                }}
                aria-label={t('roles.selectModule', { module: group.name })}
              />
            </legend>
            {group.items.map((p) => (
              <Checkbox
                key={p.code}
                label={p.description}
                description={<code>{p.code}</code>}
                checked={set.has(p.code)}
                disabled={disabled}
                onChange={(e) => {
                  toggle([p.code], e.target.checked);
                }}
              />
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}
