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
      // Coverage gates are FLOORS, set just below current actual so any
      // future drop fails the build. Aspirational targets (100% branch
      // on the three candidate-session files) are documented in
      // frontend/session/CLAUDE.md; ratchet these gates up as tests
      // are backfilled. Do NOT relax these without justification.
      thresholds: {
        lines: 17,
        statements: 17,
        '**/lib/api/candidate-session.ts': {
          branches: 39,
          functions: 23,
        },
        '**/app/interview/[token]/OtpStep.tsx': {
          branches: 26,
        },
        '**/components/interview/app/app.tsx': {
          branches: 0,
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
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
})
