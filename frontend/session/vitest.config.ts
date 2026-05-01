import path from 'path'
import { loadEnv } from 'vite'
import { coverageConfigDefaults, defineConfig } from 'vitest/config'

// Mirror Next.js: `.env.local` is the source of truth for local env values.
// Vitest does not auto-populate `process.env` from `.env.local`, so any
// module that reads `process.env.NEXT_PUBLIC_*` at top-level (e.g.
// `lib/env.ts`, which throws on missing) would crash before any test
// runs. Load `.env.local` (and `.env`) into `process.env` here.
const env = loadEnv('test', __dirname, '')
for (const [key, value] of Object.entries(env)) {
  if (process.env[key] === undefined) {
    process.env[key] = value
  }
}

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
