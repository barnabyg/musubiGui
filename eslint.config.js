import js from '@eslint/js';
import ts from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';
export default ts.config(
  {
    ignores: [
      'node_modules/**',
      'dist/**',
      '.cache/**',
      'musubi_gui/web/**',
      '.venv/**',
      'test-results/**',
      '.worktrees/**',
    ],
  },
  js.configs.recommended,
  ...ts.configs.recommended,
  {
    files: ['frontend/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': hooks },
    rules: hooks.configs.recommended.rules,
  },
);
