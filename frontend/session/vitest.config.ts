import path from 'path'
import { coverageConfigDefaults, defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'html'],
      thresholds: {
        lines: 80,
        statements: 80,
        // Enterprise gate: candidate-session paths get 100% branch coverage.
        // Root CLAUDE.md "Test Coverage Gates" — auth + RLS + candidate-session
        // are the four 100%-branch surfaces. The first three live in nexus;
        // the fourth lives here.
        '**/lib/api/candidate-session.ts': {
          branches: 100,
          functions: 100,
        },
        '**/app/interview/[token]/OtpStep.tsx': {
          branches: 100,
        },
        '**/components/interview/app/app.tsx': {
          branches: 100,
        },
      },
      include: [
        'app/**/*.{ts,tsx}',
        'components/**/*.{ts,tsx}',
        'lib/**/*.{ts,tsx}',
        'hooks/**/*.{ts,tsx}',
      ],
      exclude: [
        ...coverageConfigDefaults.exclude,
        '**/components/ui/**',
        '**/components/agents-ui/**',
        '**/components/ai-elements/**',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
})
