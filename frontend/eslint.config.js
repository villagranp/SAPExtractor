import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'playwright-report', 'src/shared/api/schema.d.ts'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      // Las pantallas solo pueden usar el design system propio (ver docs/plan/10-frontend-y-ux.md).
      'no-restricted-imports': [
        'error',
        { patterns: [{ group: ['**/template/**'], message: 'Use componentes de @/shared/ui' }] },
      ],
    },
  },
  {
    // Los iconos también pasan por el design system (ADR 0007).
    files: ['src/features/**/*.{ts,tsx}', 'src/app/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [{ name: '@tabler/icons-react', message: 'Importe los iconos desde @/shared/ui' }],
          patterns: [{ group: ['**/template/**'], message: 'Use componentes de @/shared/ui' }],
        },
      ],
    },
  },
);
