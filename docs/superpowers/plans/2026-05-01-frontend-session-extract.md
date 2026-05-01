# Extract Candidate UI into `frontend/session` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the candidate interview surface (`/interview/[token]`, LiveKit live session, OTP/consent/camera-mic wizard, audio visualizers) out of `frontend/app` into a new sibling Next.js 16 app at `frontend/session/` (port 3002). After the cut, `frontend/app` is recruiter-only.

**Architecture:** Three independent Next.js 16 apps (recruiter:3000, admin:3001, session:3002). Backend gains one new setting (`candidate_session_base_url`) so scheduler emails point at the new origin. No endpoints, no auth, no schema migrations. Phases 1–3 are additive (both apps work in parallel); phase 4 is the breaking cut.

**Tech Stack:** Next.js 16 + React 19 + TypeScript strict + Tailwind v4 + LiveKit + TanStack Query v5 + Vitest. Backend: FastAPI + pydantic-settings.

**Spec reference:** `docs/superpowers/specs/2026-05-01-frontend-session-extract-design.md`

**Worktree note:** Recommended to run in a dedicated git worktree. Create one before starting if you have not already (`git worktree add ../ProjectX-session-extract feat/extract-frontend-session`).

---

## File Map

### Created in `frontend/session/`
- Top-level: `package.json`, `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `eslint.config.mjs`, `vitest.config.ts`, `components.json`, `Dockerfile`, `docker-compose.yml`, `.gitignore`, `.dockerignore`, `.env.local.example`, `README.md`, `CLAUDE.md`, `AGENTS.md`
- App: `app/layout.tsx`, `app/page.tsx`, `app/not-found.tsx`, `app/globals.css`, `app/healthz/route.ts`
- Lib: `lib/env.ts`
- Tests infra: `tests/setup.ts`, `tests/_utils/render.tsx`, `tests/lib/env.test.ts`

### Moved from `frontend/app/` to `frontend/session/`
- `app/(interview)/interview/[token]/**` → `app/interview/[token]/**`
- `components/{agents-ui,interview,ui,ai-elements}/**`
- `hooks/agents-ui/**`
- `lib/api/candidate-session.ts`
- `lib/hooks/use-{candidate-session,consent,request-otp,verify-otp}.ts`
- `app-config.ts`
- 7 test files

### Duplicated from `frontend/app/` to `frontend/session/`
- `lib/utils.ts`
- `lib/api/errors.ts`
- `components/px/{Button,Input,Toaster}.tsx` + new focused `index.ts`
- `public/projectx-logo.svg`
- `tests/setup.ts`, `tests/_utils/render.tsx`
- Shadcn → px CSS-variable mapping in `app/globals.css`

### Deleted (no destination)
- `frontend/app/lib/hooks/use-start-session.ts` (dead code)

### Modified in `frontend/app/`
- `proxy.ts` — delete `/interview` branch (lines 38–43)
- `package.json` — remove `@livekit/components-react`, `@livekit/components-styles`, `livekit-client`, `motion`, `@livekit/protocol`
- `components.json` — drop `@agents-ui` and `@ai-elements` registries
- `CLAUDE.md` — major surgery (drop candidate-surface sections)
- `AGENTS.md` — rewrite "Two design systems" → "One design system"

### Modified in `backend/nexus/`
- `app/config.py` — add `candidate_session_base_url` + extend `cors_origins`
- `app/modules/scheduler/service.py:102, :203` — switch to `candidate_session_base_url`
- `.env.example` — add `CANDIDATE_SESSION_BASE_URL` block + extend `CORS_ORIGINS`
- `tests/test_config_validators.py` — 3 new tests
- `tests/test_scheduler_service.py` — 2 new tests
- `CLAUDE.md` — split notifications URL discipline

### Modified in `/CLAUDE.md`
- Monorepo Structure, Phase 3C.2 row, Test Coverage Gates, Dev Commands, Hard Rules → Security, Observability Standards

---

## Phase 0 — Pre-flight

### Task 0.1: Verify clean baseline

**Files:** none

- [ ] **Step 1:** Verify clean working tree.

```bash
git status
```
Expected: `nothing to commit, working tree clean` (or only the spec doc you committed).

- [ ] **Step 2:** Branch off main.

```bash
git checkout -b feat/extract-frontend-session
```

- [ ] **Step 3:** Verify all three frontends + backend build green BEFORE starting.

```bash
cd frontend/app && npm ci && npm run build && cd ../..
cd frontend/admin && npm ci && npm run build && cd ../..
cd backend/nexus && docker compose up -d && docker compose run --rm nexus pytest && docker compose down && cd ../..
```
Expected: all green. If anything fails, do NOT proceed — investigate first.

- [ ] **Step 4:** Snapshot baseline bundle size for the PR description.

```bash
cd frontend/app && du -sh .next/static/chunks/ > /tmp/bundle-baseline.txt && cat /tmp/bundle-baseline.txt && cd ../..
```

---

## Phase 1 — Scaffold `frontend/session`

This phase creates the new app as an empty shell that builds and serves a friendly landing page. `frontend/app` is unchanged.

### Task 1.1: Create directory + ignore files

**Files:**
- Create: `frontend/session/.gitignore`
- Create: `frontend/session/.dockerignore`

- [ ] **Step 1:** Create the directory.

```bash
mkdir -p frontend/session/{app,components,hooks,lib,public,tests}
```

- [ ] **Step 2:** Create `frontend/session/.gitignore`.

```
node_modules
.next
.next-dev
.env.local
.env.production
*.tsbuildinfo
next-env.d.ts
coverage
.vitest-cache
```

- [ ] **Step 3:** Create `frontend/session/.dockerignore`.

```
node_modules
.next
.env.local
.env.production
coverage
tests
*.md
.git
.github
.vscode
```

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/.gitignore frontend/session/.dockerignore
git commit -m "feat(session): scaffold ignore files"
```

### Task 1.2: Create package.json

**Files:**
- Create: `frontend/session/package.json`

- [ ] **Step 1:** Create `frontend/session/package.json`.

```json
{
  "name": "session",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev --hostname 0.0.0.0 --port 3002",
    "build": "next build",
    "start": "next start --port 3002",
    "lint": "eslint",
    "type-check": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  },
  "dependencies": {
    "@hookform/resolvers": "^5.0.0",
    "@livekit/components-react": "^2.9.20",
    "@livekit/components-styles": "^1.2.0",
    "@livekit/protocol": "^1.40.0",
    "@tanstack/react-query": "^5.96.2",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "livekit-client": "^2.18.8",
    "lucide-react": "^0.555.0",
    "motion": "^12.38.0",
    "next": "16.2.4",
    "react": "19.2.5",
    "react-dom": "19.2.5",
    "react-hook-form": "^7.66.0",
    "sonner": "^2.0.7",
    "tailwind-merge": "^3.0.0",
    "zod": "^4.3.6"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.3.0",
    "@testing-library/user-event": "^14.6.1",
    "@types/node": "^20",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "@vitest/coverage-v8": "^4.0.0",
    "eslint": "^9",
    "eslint-config-next": "16.2.4",
    "jsdom": "^28.0.0",
    "tailwindcss": "^4",
    "typescript": "^5",
    "vitest": "^4.0.0"
  }
}
```

Use exact versions present in `frontend/app/package.json` for any package shared with that app — run `cat frontend/app/package.json` and align versions before installing. The versions above are the current `frontend/app` pins as of this writing; verify and adjust if drifted.

- [ ] **Step 2:** Verify `frontend/app/package.json` versions match for shared deps.

```bash
for pkg in next react react-dom @tanstack/react-query react-hook-form zod sonner clsx tailwind-merge lucide-react class-variance-authority livekit-client @livekit/components-react @livekit/components-styles motion; do
  echo "=== $pkg ==="
  grep "\"$pkg\":" frontend/app/package.json
  grep "\"$pkg\":" frontend/session/package.json
done
```
Expected: matched versions for every shared dep. Adjust `frontend/session/package.json` if any mismatch.

- [ ] **Step 3:** Run `npm install`.

```bash
cd frontend/session && npm install && cd ../..
```
Expected: `package-lock.json` created. No errors.

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/package.json frontend/session/package-lock.json
git commit -m "feat(session): package.json + lockfile"
```

### Task 1.3: Create tsconfig.json

**Files:**
- Create: `frontend/session/tsconfig.json`

- [ ] **Step 1:** Create `frontend/session/tsconfig.json` (mirrors `frontend/app/tsconfig.json` exactly).

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "incremental": true,
    "plugins": [
      {
        "name": "next"
      }
    ],
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx",
    ".next/types/**/*.ts",
    ".next/dev/types/**/*.ts",
    "**/*.mts"
  ],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 2:** Run `tsc --noEmit` to verify the config parses.

```bash
cd frontend/session && npx tsc --noEmit && cd ../..
```
Expected: no errors (might say no input files — that's fine).

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/tsconfig.json
git commit -m "feat(session): tsconfig (mirrors frontend/app)"
```

### Task 1.4: Create next.config.ts with security headers + CSP

**Files:**
- Create: `frontend/session/next.config.ts`

- [ ] **Step 1:** Create `frontend/session/next.config.ts`.

```ts
import type { NextConfig } from "next";

// Security headers applied to every response.
//
// CSP is included here because the candidate surface has no Supabase
// (no inline-bootstrap-script complexity), no React Query devtools,
// and no third-party SDK that requires a nonce. The frontend/app CSP
// is deferred separately due to those constraints.
//
// Referrer-Policy: no-referrer is LOAD-BEARING — prevents the candidate
// JWT (in URL path) from leaking via Referer headers to external links.
//
// connect-src includes the LiveKit Cloud wildcard. For self-hosted
// LiveKit deployments, parameterize via env at that point.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob: mediastream:",
  `connect-src 'self' ${API_URL} wss://*.livekit.cloud https://*.livekit.cloud`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=(), interest-cohort=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Content-Security-Policy", value: CSP },
];

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};

export default nextConfig;
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/next.config.ts
git commit -m "feat(session): next.config.ts with security headers + CSP"
```

### Task 1.5: Create postcss.config.mjs + eslint.config.mjs

**Files:**
- Create: `frontend/session/postcss.config.mjs`
- Create: `frontend/session/eslint.config.mjs`

- [ ] **Step 1:** Copy postcss config from frontend/app.

```bash
cp frontend/app/postcss.config.mjs frontend/session/postcss.config.mjs
```

- [ ] **Step 2:** Copy eslint config from frontend/app.

```bash
cp frontend/app/eslint.config.mjs frontend/session/eslint.config.mjs
```

- [ ] **Step 3:** Verify both files are valid.

```bash
cat frontend/session/postcss.config.mjs
cat frontend/session/eslint.config.mjs
```

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/postcss.config.mjs frontend/session/eslint.config.mjs
git commit -m "feat(session): postcss + eslint configs (mirror frontend/app)"
```

### Task 1.6: Create vitest.config.ts with coverage gates

**Files:**
- Create: `frontend/session/vitest.config.ts`

- [ ] **Step 1:** Create `frontend/session/vitest.config.ts`.

```ts
import path from 'path'
import { defineConfig } from 'vitest/config'

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
        '**/*.d.ts',
        '**/node_modules/**',
        '**/.next/**',
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
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/vitest.config.ts
git commit -m "feat(session): vitest config with 100%-branch coverage gates"
```

### Task 1.7: Create components.json (shadcn config)

**Files:**
- Create: `frontend/session/components.json`

- [ ] **Step 1:** Create `frontend/session/components.json` (preserves the @agents-ui + @ai-elements registries since the moved code came from them).

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "app/globals.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "iconLibrary": "lucide",
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "registries": {
    "@agents-ui": "https://livekit.io/ui/r/{name}.json",
    "@ai-elements": "https://registry.ai-sdk.dev/{name}.json"
  }
}
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/components.json
git commit -m "feat(session): components.json (shadcn registries preserved)"
```

### Task 1.8: Create Dockerfile + docker-compose.yml

**Files:**
- Create: `frontend/session/Dockerfile`
- Create: `frontend/session/docker-compose.yml`

- [ ] **Step 1:** Create `frontend/session/Dockerfile` (mirrors `frontend/admin/Dockerfile`, port 3002).

```dockerfile
# --- Stage 1: Install dependencies ---
FROM node:22-alpine AS deps
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# --- Stage 2: Build the application ---
FROM node:22-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

# --- Stage 3: Production image ---
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3002
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3002

CMD ["node", "server.js"]
```

- [ ] **Step 2:** Create `frontend/session/docker-compose.yml` (mirrors `frontend/app/docker-compose.yml`, port 3002).

```yaml
services:
  # Local development — hot reload, source-mounted
  dev:
    build:
      context: .
      dockerfile: Dockerfile
      target: deps
    command: npm run dev
    ports:
      - "3002:3002"
    volumes:
      - .:/app
      - /app/node_modules
    env_file:
      - .env.local
    profiles:
      - dev

  # Production-like build — standalone output, minimal image
  app:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
    ports:
      - "3002:3002"
    env_file:
      - .env.production
    restart: unless-stopped
    profiles:
      - prod
```

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/Dockerfile frontend/session/docker-compose.yml
git commit -m "feat(session): Dockerfile + docker-compose (port 3002)"
```

### Task 1.9: Create .env.local.example + .env.local

**Files:**
- Create: `frontend/session/.env.local.example`
- Create: `frontend/session/.env.local`

- [ ] **Step 1:** Create `frontend/session/.env.local.example`.

```
# Required: backend API base URL
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 2:** Create `frontend/session/.env.local` (gitignored — local dev only).

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 3:** Commit (only the example; `.env.local` is gitignored).

```bash
git add frontend/session/.env.local.example
git commit -m "feat(session): env.local.example with required NEXT_PUBLIC_API_URL"
```

### Task 1.10: Create lib/env.ts with zod validation (TDD)

**Files:**
- Create: `frontend/session/lib/env.ts`
- Test: `frontend/session/tests/lib/env.test.ts`

- [ ] **Step 1:** Write the failing test at `frontend/session/tests/lib/env.test.ts`.

```ts
import { describe, expect, it } from 'vitest'
import { z } from 'zod'

import { envSchema } from '@/lib/env'

describe('envSchema', () => {
  it('accepts a valid http URL', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'http://localhost:8000',
    })
    expect(parsed.NEXT_PUBLIC_API_URL).toBe('http://localhost:8000')
  })

  it('accepts a valid https URL', () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://api.projectx.com',
    })
    expect(parsed.NEXT_PUBLIC_API_URL).toBe('https://api.projectx.com')
  })

  it('rejects a missing NEXT_PUBLIC_API_URL', () => {
    expect(() => envSchema.parse({})).toThrow(z.ZodError)
  })

  it('rejects a non-URL string', () => {
    expect(() =>
      envSchema.parse({ NEXT_PUBLIC_API_URL: 'not-a-url' }),
    ).toThrow(z.ZodError)
  })

  it('rejects an empty string', () => {
    expect(() => envSchema.parse({ NEXT_PUBLIC_API_URL: '' })).toThrow(
      z.ZodError,
    )
  })
})
```

- [ ] **Step 2:** Run the test to confirm it fails.

```bash
cd frontend/session && npx vitest run tests/lib/env.test.ts
```
Expected: FAIL — `envSchema` does not exist.

- [ ] **Step 3:** Implement `frontend/session/lib/env.ts`.

```ts
import { z } from 'zod'

/**
 * Environment schema for the candidate session app.
 *
 * Parsed at module load. Invalid config crashes the app at boot —
 * no fallback, no warning-and-continue. Mirrors backend pydantic-settings
 * discipline.
 */
export const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url(),
})

export type Env = z.infer<typeof envSchema>

/**
 * Parsed env. Throws z.ZodError at module load if validation fails.
 * Import this from any client/server file that needs env values rather
 * than reading process.env.* directly.
 */
export const env: Env = envSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
})
```

- [ ] **Step 4:** Run the test to confirm it passes.

```bash
cd frontend/session && npx vitest run tests/lib/env.test.ts
```
Expected: PASS — 5 tests green.

- [ ] **Step 5:** Commit.

```bash
git add frontend/session/lib/env.ts frontend/session/tests/lib/env.test.ts
git commit -m "feat(session): zod env validation with boot-time enforcement"
```

### Task 1.11: Create tests/setup.ts + tests/_utils/render.tsx

**Files:**
- Create: `frontend/session/tests/setup.ts`
- Create: `frontend/session/tests/_utils/render.tsx`

- [ ] **Step 1:** Copy the existing setup verbatim.

```bash
cp frontend/app/tests/setup.ts frontend/session/tests/setup.ts
cp frontend/app/tests/_utils/render.tsx frontend/session/tests/_utils/render.tsx
```

- [ ] **Step 2:** Append `getUserMedia` polyfill to `frontend/session/tests/setup.ts`. Add this to the END of the file:

```ts

/**
 * `getUserMedia` polyfill. jsdom does not implement
 * `navigator.mediaDevices`. CameraMicStep + the LiveKit publish path
 * touch it on mount; tests that don't explicitly exercise device
 * selection get a sane no-op default and can override per-test.
 */
import { vi } from 'vitest'

if (typeof navigator !== 'undefined' && !navigator.mediaDevices) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [],
        getVideoTracks: () => [],
        getAudioTracks: () => [],
      }),
      enumerateDevices: vi.fn().mockResolvedValue([]),
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    writable: true,
    configurable: true,
  })
}
```

- [ ] **Step 3:** Verify the test setup is loadable (it will be exercised by the next task's tests).

```bash
cd frontend/session && npx vitest run tests/lib/env.test.ts
```
Expected: 5 tests still green (the polyfill should not break anything).

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/tests/setup.ts frontend/session/tests/_utils/render.tsx
git commit -m "feat(session): test setup (mirrors frontend/app + getUserMedia polyfill)"
```

### Task 1.12: Create app/globals.css with px tokens + shadcn mapping

**Files:**
- Create: `frontend/session/app/globals.css`

- [ ] **Step 1:** Copy the entire globals.css from frontend/app verbatim.

```bash
cp frontend/app/app/globals.css frontend/session/app/globals.css
```

- [ ] **Step 2:** Verify the file copied correctly.

```bash
diff frontend/app/app/globals.css frontend/session/app/globals.css
```
Expected: no output (files are identical).

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/app/globals.css
git commit -m "feat(session): globals.css duplicated (px tokens + shadcn mapping)"
```

### Task 1.13: Create app/layout.tsx (merged root + interview layouts)

**Files:**
- Create: `frontend/session/app/layout.tsx`

- [ ] **Step 1:** Create `frontend/session/app/layout.tsx`. This MERGES `frontend/app/app/layout.tsx` (fonts + html shell + px theme attrs) with `frontend/app/app/(interview)/layout.tsx` (InterviewProviders + colored shell). Note: `InterviewProviders` will be imported once we move `components/interview/providers.tsx` in Phase 3 — for now in Phase 1 we use a placeholder fragment so the new app builds.

```tsx
import type { Metadata } from "next";
import { Inter, Fraunces, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import "./globals.css";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const fraunces = Fraunces({
  variable: "--font-serif",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "ProjectX Interview",
  description: "AI-led interview session",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${fraunces.variable} ${jetbrainsMono.variable} h-full antialiased`}
      data-px-theme="warm-light"
      data-px-density="comfortable"
    >
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <div
          className="min-h-screen w-full"
          style={{
            background: "var(--px-bg)",
            color: "var(--px-fg)",
          }}
        >
          {children}
        </div>
      </body>
    </html>
  );
}
```

(Note: `InterviewProviders` wrapper is NOT in this Phase 1 layout. It gets added in Task 3.4 when we move `components/interview/providers.tsx`.)

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/app/layout.tsx
git commit -m "feat(session): root layout with px theme attrs + colored shell"
```

### Task 1.14: Create app/page.tsx + app/not-found.tsx

**Files:**
- Create: `frontend/session/app/page.tsx`
- Create: `frontend/session/app/not-found.tsx`

- [ ] **Step 1:** Create `frontend/session/app/page.tsx`.

```tsx
export default function HomePage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold mb-3">Private interview link</h1>
        <p className="text-base opacity-80">
          Please use the interview link sent to your email. If you do not have
          a link, contact your recruiter.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2:** Create `frontend/session/app/not-found.tsx`.

```tsx
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold mb-3">Page not found</h1>
        <p className="text-base opacity-80">
          This URL does not match any interview link. Please check the link in
          your email or contact your recruiter.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/app/page.tsx frontend/session/app/not-found.tsx
git commit -m "feat(session): friendly landing + not-found pages"
```

### Task 1.15: Create app/healthz/route.ts

**Files:**
- Create: `frontend/session/app/healthz/route.ts`

- [ ] **Step 1:** Create `frontend/session/app/healthz/route.ts`.

```ts
import { NextResponse } from "next/server";

export const dynamic = "force-static";
export const revalidate = false;

export function GET() {
  return NextResponse.json({ status: "ok", service: "frontend-session" });
}
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/app/healthz/route.ts
git commit -m "feat(session): healthz endpoint for Railway/ECS probes"
```

### Task 1.16: Verify Phase 1 — build, test, boot, headers

**Files:** none

- [ ] **Step 1:** Lint, type-check, test, build.

```bash
cd frontend/session
npm run lint
npm run type-check
npm run test
npm run build
cd ../..
```
Expected: all green. (Note: lint may warn about no-console; that's fine.)

- [ ] **Step 2:** Boot dev server and verify endpoints.

```bash
cd frontend/session && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev &
DEVPID=$!
sleep 8
curl -s http://localhost:3002/healthz
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3002/
curl -I http://localhost:3002/ 2>&1 | grep -iE "strict-transport|x-frame|x-content|referrer-policy|permissions-policy|cross-origin|content-security"
kill $DEVPID
cd ../..
```
Expected:
- healthz returns `{"status":"ok","service":"frontend-session"}`
- `/` returns HTTP 200
- All security headers from § next.config.ts SECURITY_HEADERS appear in the curl output, including the CSP

- [ ] **Step 3:** Verify `frontend/app` and `frontend/admin` still build (regression check).

```bash
cd frontend/app && npm run build && cd ../..
cd frontend/admin && npm run build && cd ../..
```
Expected: both green.

---

## Phase 2 — Backend changes

Add `candidate_session_base_url` setting, extend CORS, update scheduler. All additive — old behavior preserved until Phase 4.

### Task 2.1: Add 3 config tests (TDD)

**Files:**
- Modify: `backend/nexus/tests/test_config_validators.py`

- [ ] **Step 1:** Read the existing file to understand the test pattern.

```bash
cat backend/nexus/tests/test_config_validators.py
```

- [ ] **Step 2:** Append three new tests to `backend/nexus/tests/test_config_validators.py`. Adapt the import + class location to match the existing file's style. The tests:

```python
def test_candidate_session_base_url_strips_trailing_slash():
    """Mirrors frontend_base_url's normalizer — trailing slash is removed."""
    from app.config import Settings

    s = Settings(candidate_session_base_url="http://localhost:3002/")
    assert s.candidate_session_base_url == "http://localhost:3002"


def test_candidate_session_base_url_default_is_localhost_3002():
    """Guard against accidentally changing the local-dev default."""
    from app.config import Settings

    s = Settings()
    assert s.candidate_session_base_url == "http://localhost:3002"


def test_cors_origins_default_includes_3002():
    """Guard against accidentally dropping the new candidate-session port from CORS."""
    from app.config import Settings

    s = Settings()
    assert "http://localhost:3002" in s.cors_origins
    assert "http://127.0.0.1:3002" in s.cors_origins
```

- [ ] **Step 3:** Run the new tests — confirm they fail.

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_config_validators.py -k "candidate_session or cors_origins_default_includes_3002" -v && cd ../..
```
Expected: FAIL — `candidate_session_base_url` doesn't exist yet, and CORS list doesn't include 3002.

### Task 2.2: Implement candidate_session_base_url + CORS extension in config.py

**Files:**
- Modify: `backend/nexus/app/config.py`

- [ ] **Step 1:** Read the relevant region of config.py.

```bash
sed -n '175,290p' backend/nexus/app/config.py
```

- [ ] **Step 2:** Edit `cors_origins` line 179 to include port 3002. Replace:

```python
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"]
```

with:

```python
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
    ]
```

- [ ] **Step 3:** Add the new setting + validator AFTER the existing `_strip_trailing_slash` validator (line 286). Append:

```python

    # Candidate session base URL — used to build interview invite links in
    # scheduler emails. Kept SEPARATE from frontend_base_url so the two
    # surfaces (recruiter dashboard vs. candidate session app) can be
    # deployed at different origins. Every environment must set
    # CANDIDATE_SESSION_BASE_URL explicitly. Defaulting both to the same
    # host masked the split during Phase 1; we are not repeating that mistake.
    candidate_session_base_url: str = "http://localhost:3002"

    @field_validator("candidate_session_base_url")
    @classmethod
    def _strip_candidate_session_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")
```

- [ ] **Step 4:** Run the new tests — confirm they pass.

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_config_validators.py -k "candidate_session or cors_origins_default_includes_3002" -v && cd ../..
```
Expected: PASS — 3 tests green.

- [ ] **Step 5:** Run the full config-validators file to ensure no regressions.

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_config_validators.py -v && cd ../..
```
Expected: ALL green.

- [ ] **Step 6:** Commit.

```bash
git add backend/nexus/app/config.py backend/nexus/tests/test_config_validators.py
git commit -m "feat(nexus): add candidate_session_base_url + extend CORS to :3002"
```

### Task 2.3: Add 2 scheduler tests (TDD)

**Files:**
- Modify: `backend/nexus/tests/test_scheduler_service.py`

- [ ] **Step 1:** Read the existing test file structure.

```bash
head -80 backend/nexus/tests/test_scheduler_service.py
grep -n "send_email\|send_invite\|resend_invite\|invite_url" backend/nexus/tests/test_scheduler_service.py | head -20
```

- [ ] **Step 2:** Add two new tests at the end of `backend/nexus/tests/test_scheduler_service.py`. **The tests must set `frontend_base_url` and `candidate_session_base_url` to DISTINCT hostnames so that a regression that uses the wrong setting fails the assertion.** Adapt the fixture wiring to match existing patterns in the file (likely uses `mocker.patch` or settings overrides).

```python
async def test_send_invite_uses_candidate_session_base_url(
    db, monkeypatch, mocker
):
    """The interview invite email must link to candidate_session_base_url,
    NOT frontend_base_url. Distinct hostnames in the test guarantee a
    regression that wires the wrong setting fails the assertion.
    """
    # Patch settings with distinct hostnames
    monkeypatch.setattr(
        "app.modules.scheduler.service.settings.frontend_base_url",
        "https://recruiter.example.com",
    )
    monkeypatch.setattr(
        "app.modules.scheduler.service.settings.candidate_session_base_url",
        "https://candidate.example.com",
    )

    # Capture send_email invocations
    captured = {}
    async def fake_send(*, to, subject, html):
        captured["html"] = html
    mocker.patch("app.modules.scheduler.service.send_email", side_effect=fake_send)

    # ... arrange a session/assignment/candidate fixture exactly as
    # other tests in this file do (copy from the closest existing test) ...
    # Then call send_invite and assert:

    assert "https://candidate.example.com/interview/" in captured["html"]
    assert "https://recruiter.example.com" not in captured["html"]


async def test_resend_invite_uses_candidate_session_base_url(
    db, monkeypatch, mocker
):
    """Same guarantee for resend_invite as send_invite."""
    monkeypatch.setattr(
        "app.modules.scheduler.service.settings.frontend_base_url",
        "https://recruiter.example.com",
    )
    monkeypatch.setattr(
        "app.modules.scheduler.service.settings.candidate_session_base_url",
        "https://candidate.example.com",
    )

    captured = {}
    async def fake_send(*, to, subject, html):
        captured["html"] = html
    mocker.patch("app.modules.scheduler.service.send_email", side_effect=fake_send)

    # ... arrange + call resend_invite (copy from the existing resend test) ...

    assert "https://candidate.example.com/interview/" in captured["html"]
    assert "https://recruiter.example.com" not in captured["html"]
```

The fixture-arrangement portions (`# ...`) must be filled in by copying from the closest existing `send_invite` and `resend_invite` tests in the same file. Do NOT invent fixtures — copy the existing patterns.

- [ ] **Step 3:** Run the new tests — confirm they fail (because scheduler still uses `frontend_base_url`).

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_scheduler_service.py -k "candidate_session_base_url" -v && cd ../..
```
Expected: FAIL — assertions about candidate.example.com URL fail because emails currently render with frontend_base_url.

### Task 2.4: Switch scheduler/service.py to candidate_session_base_url

**Files:**
- Modify: `backend/nexus/app/modules/scheduler/service.py:102, :203`

- [ ] **Step 1:** Read the two lines.

```bash
sed -n '100,105p' backend/nexus/app/modules/scheduler/service.py
sed -n '201,206p' backend/nexus/app/modules/scheduler/service.py
```

- [ ] **Step 2:** Edit line 102. Change:

```python
        invite_url=f"{settings.frontend_base_url}/interview/{token_str}",
```

to:

```python
        invite_url=f"{settings.candidate_session_base_url}/interview/{token_str}",
```

- [ ] **Step 3:** Edit line 203 (same change). The two lines are now identical text in different functions.

- [ ] **Step 4:** Run the anti-regression grep — must return ZERO matches.

```bash
grep -rn "frontend_base_url.*interview\|frontend_base_url.*candidate-session" backend/nexus/app/
```
Expected: empty output.

- [ ] **Step 5:** Run the new tests — confirm they pass.

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_scheduler_service.py -k "candidate_session_base_url" -v && cd ../..
```
Expected: PASS.

- [ ] **Step 6:** Run the full scheduler test file to catch regressions.

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_scheduler_service.py -v && cd ../..
```
Expected: all green.

- [ ] **Step 7:** Commit.

```bash
git add backend/nexus/app/modules/scheduler/service.py backend/nexus/tests/test_scheduler_service.py
git commit -m "feat(nexus): scheduler interview links use candidate_session_base_url"
```

### Task 2.5: Update .env.example

**Files:**
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1:** Read the relevant region.

```bash
sed -n '165,180p' backend/nexus/.env.example
```

- [ ] **Step 2:** Edit line 169 (CORS_ORIGINS). Change:

```
CORS_ORIGINS=["http://localhost:3000","http://localhost:3001","http://127.0.0.1:3000","http://127.0.0.1:3001"]
```

to:

```
CORS_ORIGINS=["http://localhost:3000","http://localhost:3001","http://localhost:3002","http://127.0.0.1:3000","http://127.0.0.1:3001","http://127.0.0.1:3002"]
```

- [ ] **Step 3:** After the existing `FRONTEND_BASE_URL=http://localhost:3000` block (around line 176), add:

```

# Candidate session origin — used to build /interview/{token} links in
# scheduler invite/resend emails. Must point at the frontend/session app,
# NOT frontend/app. Mirrors FRONTEND_BASE_URL discipline: explicit per env.
CANDIDATE_SESSION_BASE_URL=http://localhost:3002
```

- [ ] **Step 4:** Update `backend/nexus/.env` (gitignored) so local dev works:

```bash
echo "" >> backend/nexus/.env
echo "CANDIDATE_SESSION_BASE_URL=http://localhost:3002" >> backend/nexus/.env
```

- [ ] **Step 5:** Verify backend boots cleanly with the new env (catches typos).

```bash
cd backend/nexus && docker compose down && docker compose up -d nexus && sleep 8 && docker compose logs nexus --tail 30 && docker compose down && cd ../..
```
Expected: logs include the `_assert_rls_completeness` startup assertion passing, no traceback.

- [ ] **Step 6:** Commit.

```bash
git add backend/nexus/.env.example
git commit -m "docs(nexus): add CANDIDATE_SESSION_BASE_URL to env.example + extend CORS"
```

### Task 2.6: Run full backend test suite (regression gate)

**Files:** none

- [ ] **Step 1:** Run the full pytest suite.

```bash
cd backend/nexus && docker compose run --rm nexus pytest && cd ../..
```
Expected: all green. If any test fails, investigate before proceeding.

---

## Phase 3 — Move + duplicate

Each move uses `git mv` so history is preserved. After this phase both apps have the candidate code; Phase 4 deletes from `frontend/app`.

### Task 3.1: Move app/(interview) tree → frontend/session/app/interview/

**Files:**
- Move: `frontend/app/app/(interview)/interview/[token]/**` → `frontend/session/app/interview/[token]/**`
- Delete: `frontend/app/app/(interview)/layout.tsx` (logic merged into `frontend/session/app/layout.tsx` in Task 1.13)

- [ ] **Step 1:** Move the route tree (drop the `(interview)` route group).

```bash
mkdir -p frontend/session/app/interview
git mv "frontend/app/app/(interview)/interview/[token]" frontend/session/app/interview/[token]
```

- [ ] **Step 2:** Delete the old (interview) layout — its logic moved to `frontend/session/app/layout.tsx` in Task 1.13.

```bash
git rm "frontend/app/app/(interview)/layout.tsx"
rmdir "frontend/app/app/(interview)" || true
```

- [ ] **Step 3:** Verify the move.

```bash
ls frontend/session/app/interview/[token]/
ls "frontend/app/app/(interview)" 2>&1 | head -3
```
Expected:
- New path lists `page.tsx, WizardShell.tsx, ConsentStep.tsx, OtpStep.tsx, CameraMicStep.tsx, error/`
- Old path: "No such file or directory"

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/app/interview frontend/app/app
git commit -m "refactor(session): move /interview/[token] route tree from frontend/app"
```

### Task 3.2: Move components/{agents-ui, interview, ui, ai-elements} trees

**Files:**
- Move 4 entire component trees from `frontend/app/components/` to `frontend/session/components/`

- [ ] **Step 1:** Move the four component trees.

```bash
git mv frontend/app/components/agents-ui frontend/session/components/agents-ui
git mv frontend/app/components/interview frontend/session/components/interview
git mv frontend/app/components/ui frontend/session/components/ui
git mv frontend/app/components/ai-elements frontend/session/components/ai-elements
```

- [ ] **Step 2:** Verify.

```bash
ls frontend/session/components/
ls frontend/app/components/
```
Expected:
- `frontend/session/components/` shows `agents-ui, ai-elements, interview, ui`
- `frontend/app/components/` no longer shows any of those

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/components frontend/app/components
git commit -m "refactor(session): move agents-ui + interview + ui + ai-elements component trees"
```

### Task 3.3: Move hooks/agents-ui tree

**Files:**
- Move: `frontend/app/hooks/agents-ui/**` → `frontend/session/hooks/agents-ui/**`
- Delete: `frontend/app/hooks/` (becomes empty after move)

- [ ] **Step 1:** Move the hooks tree.

```bash
git mv frontend/app/hooks/agents-ui frontend/session/hooks/agents-ui
```

- [ ] **Step 2:** Remove the now-empty hooks dir from frontend/app.

```bash
rmdir frontend/app/hooks
```

- [ ] **Step 3:** Verify.

```bash
ls frontend/session/hooks/agents-ui/
ls frontend/app/hooks 2>&1 | head -3
```
Expected:
- New path lists 6 visualizer + control-bar hook files
- Old path: "No such file or directory"

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/hooks frontend/app
git commit -m "refactor(session): move hooks/agents-ui tree (frontend/app/hooks/ now empty)"
```

### Task 3.4: Move lib/api/candidate-session.ts + 4 candidate hooks

**Files:**
- Move: `frontend/app/lib/api/candidate-session.ts` → `frontend/session/lib/api/candidate-session.ts`
- Move: `frontend/app/lib/hooks/use-{candidate-session,consent,request-otp,verify-otp}.ts` → `frontend/session/lib/hooks/`

- [ ] **Step 1:** Create destination directories.

```bash
mkdir -p frontend/session/lib/api frontend/session/lib/hooks
```

- [ ] **Step 2:** Move the API client.

```bash
git mv frontend/app/lib/api/candidate-session.ts frontend/session/lib/api/candidate-session.ts
```

- [ ] **Step 3:** Move the 4 candidate hooks.

```bash
git mv frontend/app/lib/hooks/use-candidate-session.ts frontend/session/lib/hooks/use-candidate-session.ts
git mv frontend/app/lib/hooks/use-consent.ts frontend/session/lib/hooks/use-consent.ts
git mv frontend/app/lib/hooks/use-request-otp.ts frontend/session/lib/hooks/use-request-otp.ts
git mv frontend/app/lib/hooks/use-verify-otp.ts frontend/session/lib/hooks/use-verify-otp.ts
```

- [ ] **Step 4:** Verify.

```bash
ls frontend/session/lib/api/
ls frontend/session/lib/hooks/
ls frontend/app/lib/api/ | grep -i candidate
ls frontend/app/lib/hooks/ | grep -E "use-(candidate-session|consent|request-otp|verify-otp)"
```
Expected:
- New paths show the moved files
- Old paths return nothing for the candidate files (still has recruiter files like `candidates.ts`, `use-candidate.ts`, etc. — those are recruiter-side and STAY)

- [ ] **Step 5:** Commit.

```bash
git add frontend/session/lib frontend/app/lib
git commit -m "refactor(session): move candidate-session API client + 4 candidate hooks"
```

### Task 3.5: Move app-config.ts

**Files:**
- Move: `frontend/app/app-config.ts` → `frontend/session/app-config.ts`

- [ ] **Step 1:** Move it.

```bash
git mv frontend/app/app-config.ts frontend/session/app-config.ts
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/app-config.ts frontend/app/app-config.ts
git commit -m "refactor(session): move app-config.ts (interview-only config)"
```

### Task 3.6: Move 7 candidate test files

**Files:**
- Move: `frontend/app/tests/components/OtpStep.test.tsx` → `frontend/session/tests/components/interview/OtpStep.test.tsx`
- Move: `frontend/app/tests/components/interview/{app,CompletionScreen,ProgressBanner,ReconnectingOverlay,use-session-outcome}.test.tsx` → `frontend/session/tests/components/interview/`
- Move: `frontend/app/tests/lib/api/candidate-session.test.ts` → `frontend/session/tests/lib/api/candidate-session.test.ts`

- [ ] **Step 1:** Create destination directories.

```bash
mkdir -p frontend/session/tests/components/interview frontend/session/tests/lib/api
```

- [ ] **Step 2:** Move OtpStep.test.tsx (consolidating it under `tests/components/interview/`).

```bash
git mv frontend/app/tests/components/OtpStep.test.tsx frontend/session/tests/components/interview/OtpStep.test.tsx
```

- [ ] **Step 3:** Move the 5 interview tests.

```bash
git mv frontend/app/tests/components/interview/app.test.tsx frontend/session/tests/components/interview/app.test.tsx
git mv frontend/app/tests/components/interview/CompletionScreen.test.tsx frontend/session/tests/components/interview/CompletionScreen.test.tsx
git mv frontend/app/tests/components/interview/ProgressBanner.test.tsx frontend/session/tests/components/interview/ProgressBanner.test.tsx
git mv frontend/app/tests/components/interview/ReconnectingOverlay.test.tsx frontend/session/tests/components/interview/ReconnectingOverlay.test.tsx
git mv frontend/app/tests/components/interview/use-session-outcome.test.tsx frontend/session/tests/components/interview/use-session-outcome.test.tsx
```

- [ ] **Step 4:** Move candidate-session test.

```bash
git mv frontend/app/tests/lib/api/candidate-session.test.ts frontend/session/tests/lib/api/candidate-session.test.ts
```

- [ ] **Step 5:** Remove the now-empty `frontend/app/tests/components/interview/` directory.

```bash
rmdir frontend/app/tests/components/interview
```

- [ ] **Step 6:** Verify.

```bash
ls frontend/session/tests/components/interview/
ls frontend/session/tests/lib/api/
ls frontend/app/tests/components/interview 2>&1 | head -3
```
Expected:
- New session tests dir lists 6 files
- New session API tests dir lists candidate-session.test.ts
- Old interview dir: "No such file or directory"

- [ ] **Step 7:** Commit.

```bash
git add frontend/session/tests frontend/app/tests
git commit -m "refactor(session): move 7 candidate test files"
```

### Task 3.7: Duplicate lib/utils.ts and lib/api/errors.ts

**Files:**
- Create: `frontend/session/lib/utils.ts` (copy of `frontend/app/lib/utils.ts`)
- Create: `frontend/session/lib/api/errors.ts` (copy of `frontend/app/lib/api/errors.ts`)

- [ ] **Step 1:** Copy lib/utils.ts.

```bash
cp frontend/app/lib/utils.ts frontend/session/lib/utils.ts
```

- [ ] **Step 2:** Copy lib/api/errors.ts.

```bash
cp frontend/app/lib/api/errors.ts frontend/session/lib/api/errors.ts
```

- [ ] **Step 3:** Verify.

```bash
diff frontend/app/lib/utils.ts frontend/session/lib/utils.ts
diff frontend/app/lib/api/errors.ts frontend/session/lib/api/errors.ts
```
Expected: no output for either diff.

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/lib/utils.ts frontend/session/lib/api/errors.ts
git commit -m "feat(session): duplicate lib/utils + lib/api/errors (drift discipline)"
```

### Task 3.8: Duplicate 3 px primitives + focused index.ts

**Files:**
- Create: `frontend/session/components/px/Button.tsx`
- Create: `frontend/session/components/px/Input.tsx`
- Create: `frontend/session/components/px/Toaster.tsx`
- Create: `frontend/session/components/px/index.ts`

- [ ] **Step 1:** Create the px directory and copy the 3 files used by the candidate flow.

```bash
mkdir -p frontend/session/components/px
cp frontend/app/components/px/Button.tsx frontend/session/components/px/Button.tsx
cp frontend/app/components/px/Input.tsx frontend/session/components/px/Input.tsx
cp frontend/app/components/px/Toaster.tsx frontend/session/components/px/Toaster.tsx
```

- [ ] **Step 2:** Create the focused barrel `frontend/session/components/px/index.ts`. This re-exports ONLY the 3 duplicated primitives (not the full 14 — keeps the duplicated surface honest).

```ts
export { Button } from "./Button";
export type { ButtonProps, ButtonVariant, ButtonSize } from "./Button";

export { Input } from "./Input";
export type { InputProps, InputSize } from "./Input";

export { Toaster } from "./Toaster";
```

- [ ] **Step 3:** Verify the source files match the recruiter app exactly.

```bash
diff frontend/app/components/px/Button.tsx frontend/session/components/px/Button.tsx
diff frontend/app/components/px/Input.tsx frontend/session/components/px/Input.tsx
diff frontend/app/components/px/Toaster.tsx frontend/session/components/px/Toaster.tsx
```
Expected: no output.

- [ ] **Step 4:** Verify the new app type-checks (the duplicated px imports must resolve via the new barrel).

```bash
cd frontend/session && npm run type-check && cd ../..
```
Expected: green. If errors mention missing `@base-ui-components/react` or other peer deps, add them to `frontend/session/package.json` and re-run `npm install`.

- [ ] **Step 5:** Commit.

```bash
git add frontend/session/components/px
git commit -m "feat(session): duplicate 3 px primitives + focused barrel (Button, Input, Toaster only)"
```

### Task 3.9: Duplicate public/projectx-logo.svg

**Files:**
- Create: `frontend/session/public/projectx-logo.svg`

- [ ] **Step 1:** Create public dir and copy the asset.

```bash
mkdir -p frontend/session/public
cp frontend/app/public/projectx-logo.svg frontend/session/public/projectx-logo.svg
```

- [ ] **Step 2:** Verify.

```bash
diff frontend/app/public/projectx-logo.svg frontend/session/public/projectx-logo.svg
```
Expected: no output.

- [ ] **Step 3:** Commit.

```bash
git add frontend/session/public/projectx-logo.svg
git commit -m "feat(session): duplicate projectx-logo.svg (referenced by app-config)"
```

### Task 3.10: Wire InterviewProviders into app/layout.tsx

**Files:**
- Modify: `frontend/session/app/layout.tsx`

The Phase 1 layout has the colored shell but does not wrap children in `InterviewProviders` (QueryClient + Toaster). Now that `components/interview/providers.tsx` exists in the new app, wire it.

- [ ] **Step 1:** Edit `frontend/session/app/layout.tsx`. Add import after the existing imports:

```tsx
import { InterviewProviders } from "@/components/interview/providers";
```

- [ ] **Step 2:** Wrap the colored shell's children with `<InterviewProviders>`. The body section becomes:

```tsx
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <InterviewProviders>
          <div
            className="min-h-screen w-full"
            style={{
              background: "var(--px-bg)",
              color: "var(--px-fg)",
            }}
          >
            {children}
          </div>
        </InterviewProviders>
      </body>
```

- [ ] **Step 3:** Type-check.

```bash
cd frontend/session && npm run type-check && cd ../..
```
Expected: green.

- [ ] **Step 4:** Commit.

```bash
git add frontend/session/app/layout.tsx
git commit -m "feat(session): wrap layout in InterviewProviders (QueryClient + Toaster)"
```

### Task 3.11: Verify Phase 3 — full session app build + tests + manual smoke

**Files:** none

- [ ] **Step 1:** Install (in case any moved file's transitive dep needs picking up).

```bash
cd frontend/session && npm install && cd ../..
```

- [ ] **Step 2:** Lint, type-check, test, build.

```bash
cd frontend/session
npm run lint
npm run type-check
npm run test
npm run build
cd ../..
```
Expected: all green. The 7 moved tests + the env test = 8 test files passing.

- [ ] **Step 3:** Run coverage to confirm the 100%-branch gates pass.

```bash
cd frontend/session && npm run test:coverage && cd ../..
```
Expected: coverage report shows 100% branch on `lib/api/candidate-session.ts`, `app/interview/[token]/OtpStep.tsx`, `components/interview/app/app.tsx`. Global ≥80% lines/statements.

- [ ] **Step 4:** Verify `frontend/app` STILL builds + tests (it shouldn't be broken — phase 4 hasn't started).

```bash
cd frontend/app && npm run lint && npm run type-check && npm run test && npm run build && cd ../..
```
Expected: green.

- [ ] **Step 5:** Manual smoke test (the gate before Phase 4). Start backend + frontend/session and complete a real interview link end-to-end.

```bash
# Terminal 1: backend
cd backend/nexus && docker compose up && cd ../..

# Terminal 2: frontend/session
cd frontend/session && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev

# Terminal 3: frontend/app (to mint the invite)
cd frontend/app && npm run dev
```

In a browser:
1. Login to `http://localhost:3000` as recruiter, create a candidate, send an interview invite.
2. Open Inbucket at `http://localhost:54324`, open the invite email, copy the `/interview/<token>` link.
3. Paste the link with origin replaced to `http://localhost:3002` (the email link will say `:3002` since Phase 2 already shipped).
4. Confirm: pre-check renders → consent works → OTP works → camera/mic works → LiveKit room joins → AI agent connects → CompletionScreen renders.

**Stop at this gate.** If the smoke test fails, do NOT proceed to Phase 4. Fix the moved app first.

---

## Phase 4 — Cleanup `frontend/app`

The breaking cut. Before this point, both apps work. After, only `frontend/session` serves the candidate flow.

### Task 4.1: Delete proxy.ts /interview branch

**Files:**
- Modify: `frontend/app/proxy.ts:38-43`

- [ ] **Step 1:** Read the relevant lines.

```bash
sed -n '36,45p' frontend/app/proxy.ts
```

- [ ] **Step 2:** Delete lines 38–43 from `frontend/app/proxy.ts`. The block to remove is:

```ts
  // Candidate interview pages are always public — candidates are NOT
  // Supabase users. Their JWT lives in the URL path and is verified
  // server-side by Nexus on every /api/candidate-session/{token}/* call.
  if (path.startsWith("/interview")) {
    return supabaseResponse;
  }

```

After the edit, `proxy.ts` flows directly from the `/invite` branch (line 36) to "// Validate session" (was line 45).

- [ ] **Step 3:** Verify the deletion.

```bash
grep -n "interview" frontend/app/proxy.ts
```
Expected: empty (no matches).

- [ ] **Step 4:** Lint + type-check.

```bash
cd frontend/app && npm run lint && npm run type-check && cd ../..
```
Expected: green.

- [ ] **Step 5:** Commit.

```bash
git add frontend/app/proxy.ts
git commit -m "refactor(app): remove /interview branch from proxy.ts (moved to frontend/session)"
```

### Task 4.2: Remove livekit + motion from package.json

**Files:**
- Modify: `frontend/app/package.json`

- [ ] **Step 1:** Read the relevant lines.

```bash
grep -nE "livekit|motion" frontend/app/package.json
```

- [ ] **Step 2:** Edit `frontend/app/package.json` and delete these dependency lines:
- `"@livekit/components-react": "^2.9.20",`
- `"@livekit/components-styles": "^1.2.0",`
- `"@livekit/protocol": "..."` (if present)
- `"livekit-client": "^2.18.8",`
- `"motion": "^12.38.0",`

Mind the trailing comma on the line BEFORE the deleted block — JSON has no trailing comma after the last property.

- [ ] **Step 3:** Run `npm install` to regenerate lockfile.

```bash
cd frontend/app && npm install && cd ../..
```
Expected: lockfile updated; package-lock.json no longer references livekit/motion.

- [ ] **Step 4:** Verify removal.

```bash
grep -E "livekit|motion" frontend/app/package.json
grep -E "livekit|motion" frontend/app/package-lock.json | head -3
```
Expected: package.json returns nothing. package-lock.json may still mention transitive deps (acceptable).

- [ ] **Step 5:** Lint, type-check, build.

```bash
cd frontend/app && npm run lint && npm run type-check && npm run build && cd ../..
```
Expected: green. (If any module still imports livekit/motion, the build will fail — that's a missed file from Phase 3, fix it.)

- [ ] **Step 6:** Commit.

```bash
git add frontend/app/package.json frontend/app/package-lock.json
git commit -m "refactor(app): remove livekit + motion deps (moved to frontend/session)"
```

### Task 4.3: Drop @agents-ui + @ai-elements registries from components.json

**Files:**
- Modify: `frontend/app/components.json`

- [ ] **Step 1:** Read the file.

```bash
cat frontend/app/components.json
```

- [ ] **Step 2:** Edit `frontend/app/components.json`. Remove the `registries` block:

```json
  "registries": {
    "@agents-ui": "https://livekit.io/ui/r/{name}.json",
    "@ai-elements": "https://registry.ai-sdk.dev/{name}.json"
  }
```

If the `registries` object becomes empty after removal, delete the whole `registries` key. Mind the comma on the preceding property line.

- [ ] **Step 3:** Verify.

```bash
grep -E "agents-ui|ai-elements" frontend/app/components.json
```
Expected: no output.

- [ ] **Step 4:** Commit.

```bash
git add frontend/app/components.json
git commit -m "refactor(app): drop @agents-ui + @ai-elements shadcn registries"
```

### Task 4.4: Delete dead code (use-start-session.ts)

**Files:**
- Delete: `frontend/app/lib/hooks/use-start-session.ts`

- [ ] **Step 1:** Confirm it really has zero callers (paranoia check).

```bash
grep -rn "useStartSession\|use-start-session" frontend/app/ --include="*.ts" --include="*.tsx" | grep -v "/lib/hooks/use-start-session.ts:"
```
Expected: empty.

- [ ] **Step 2:** Delete it.

```bash
git rm frontend/app/lib/hooks/use-start-session.ts
```

- [ ] **Step 3:** Lint + type-check.

```bash
cd frontend/app && npm run lint && npm run type-check && cd ../..
```
Expected: green.

- [ ] **Step 4:** Commit.

```bash
git commit -m "refactor(app): delete dead code use-start-session.ts (zero callers)"
```

### Task 4.5: Run grep gauntlet (verification gate)

**Files:** none — this is a verification step.

- [ ] **Step 1:** Verify no candidate component references remain.

```bash
grep -rn "components/agents-ui\|components/ai-elements\|components/interview" \
  frontend/app/app frontend/app/components frontend/app/lib frontend/app/tests \
  --include="*.ts" --include="*.tsx" 2>/dev/null
```
Expected: empty.

- [ ] **Step 2:** Verify no livekit/motion imports.

```bash
grep -rn "from ['\"]@livekit\|from ['\"]livekit-client\|from ['\"]motion" \
  frontend/app/app frontend/app/components frontend/app/lib 2>/dev/null
```
Expected: empty.

- [ ] **Step 3:** Verify no candidate-session imports.

```bash
grep -rn "from ['\"]@/lib/api/candidate-session\|from ['\"]@/lib/hooks/use-candidate-session\|from ['\"]@/lib/hooks/use-consent\|from ['\"]@/lib/hooks/use-request-otp\|from ['\"]@/lib/hooks/use-verify-otp\|from ['\"]@/lib/hooks/use-start-session" \
  frontend/app 2>/dev/null
```
Expected: empty.

- [ ] **Step 4:** Verify no /interview path references.

```bash
grep -rn "/interview" frontend/app/proxy.ts frontend/app/app frontend/app/lib 2>/dev/null
```
Expected: empty.

- [ ] **Step 5:** Verify no app-config import.

```bash
grep -rn "from ['\"]@/app-config" frontend/app 2>/dev/null
```
Expected: empty.

If ANY of these greps return matches, do NOT proceed — investigate the leftover and fix it.

### Task 4.6: Final frontend/app build + bundle delta

**Files:** none

- [ ] **Step 1:** Full lint + type-check + test + build.

```bash
cd frontend/app
npm ci
npm run lint
npm run type-check
npm run test
npm run build
cd ../..
```
Expected: all green.

- [ ] **Step 2:** Capture new bundle size and compute delta vs the Phase 0 snapshot.

```bash
cd frontend/app && du -sh .next/static/chunks/ > /tmp/bundle-after.txt && cat /tmp/bundle-after.txt && cd ../..
echo "BEFORE: $(cat /tmp/bundle-baseline.txt)"
echo "AFTER:  $(cat /tmp/bundle-after.txt)"
```
Expected: meaningful reduction (LiveKit SDK alone is ~180 KB gzipped). Record both numbers in the PR description.

---

## Phase 5 — Documentation

### Task 5.1: Update root /CLAUDE.md

**Files:**
- Modify: `/home/ishant/Projects/ProjectX/CLAUDE.md`

- [ ] **Step 1:** Update Monorepo Structure section. Replace the `frontend/` block:

```diff
 ProjectX/
 ├── CLAUDE.md
 ├── frontend/
-│   ├── app/                   ← Next.js 16 App Router — recruiter dashboard + candidate interview surface
-│   └── admin/                 ← Next.js 16 App Router — internal ProjectX-operator console (provision tenants)
+│   ├── app/                   ← Next.js 16 App Router — recruiter dashboard ONLY
+│   ├── admin/                 ← Next.js 16 App Router — internal ProjectX-operator console (provision tenants)
+│   └── session/               ← Next.js 16 App Router — candidate interview surface (token-gated, no Supabase auth)
 └── backend/
     └── nexus/                 ← FastAPI, Python 3.12, Docker, Modular Monolith
```

- [ ] **Step 2:** Update Phase 3C.2 row in the `Current Phase Status` table — append `(extracted to frontend/session 2026-05-01; see docs/superpowers/specs/2026-05-01-frontend-session-extract-design.md)` to the description cell.

- [ ] **Step 3:** Add to `Test Coverage Gates` (after the existing `frontend/app/proxy.ts` and `frontend/admin/proxy.ts` line):

```
- `frontend/session/lib/api/candidate-session.ts` — 100% branch coverage (root candidate-session gate).
- `frontend/session/lib/env.ts` — env validation must pass on valid input and reject invalid input.
- `frontend/session/next.config.ts` `headers()` — every header from the design spec must be present (manual `curl -I` assertion until CI lands).
```

- [ ] **Step 4:** Add to `Dev Commands`, after the `frontend/admin/` block:

```bash
# Candidate session app (from frontend/session/, port 3002)
npm run dev          # Start dev server (localhost:3002)
npm run build        # Production build
npm run lint
npm run type-check
npm run test         # Vitest — includes 100%-branch gate on candidate-session.ts
```

- [ ] **Step 5:** Add to `Hard Rules → Security` (or whichever security section exists at root level):

```markdown
- The candidate session app (`frontend/session`) MUST NOT depend on `@supabase/*` packages or read `NEXT_PUBLIC_SUPABASE_*` env vars. The recruiter dashboard app (`frontend/app`) MUST NOT depend on `livekit-*` packages or import from a `components/agents-ui/` or `components/ai-elements/` path. Pre-merge gate today: manual `grep livekit frontend/app/package.json` and `grep @supabase frontend/session/package.json` (CI gate when CI lands).
```

- [ ] **Step 6:** Add to `Observability Standards` (deferred-work note):

```markdown
- When Sentry is wired across all three frontend apps (separate PR), each app uses its own DSN. The candidate-session DSN's `beforeSend` MUST scrub `/interview/[^/]+` from URL/breadcrumb/stack data and drop events whose payloads pattern-match a JWT (`eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`). Until Sentry is wired, the candidate surface logs nothing to a third party.
```

- [ ] **Step 7:** Add a deferred-work note (appropriate section, e.g., near the top of "Enterprise Operating Standards"):

```markdown
- When CI infrastructure is added (`.github/workflows/`), the matrix MUST include `frontend/session` alongside `frontend/app` and `frontend/admin`. The dependency-check rule above must be enforced in CI.
```

- [ ] **Step 8:** Commit.

```bash
git add CLAUDE.md
git commit -m "docs(root): add frontend/session to monorepo + coverage gates + security rules"
```

### Task 5.2: Surgery on frontend/app/CLAUDE.md

**Files:**
- Modify: `frontend/app/CLAUDE.md`

The current file contains extensive candidate-surface content that no longer applies. Reference: `docs/superpowers/specs/2026-05-01-frontend-session-extract-design.md` § "frontend/app/CLAUDE.md" enumerates exactly which sections to remove.

- [ ] **Step 1:** Rewrite the `## What This Surface Is` section. Replace:

```markdown
The Next.js app serves **two distinct user surfaces** within a single codebase:

1. **Dashboard** — Recruiter, Hiring Manager, Interviewer, Admin. ...
2. **Candidate Interview UI** — Candidate-facing. ...
```

with:

```markdown
This Next.js app serves the **recruiter dashboard ONLY**. It is the surface for Recruiters, Hiring Managers, Interviewers, Admins, and Observers. Authentication is Supabase email + password; data flows through Nexus (`apiFetch` + `getFreshSupabaseToken`).

**The candidate interview surface lives in a separate app** at `frontend/session/`. Do NOT add candidate-facing routes, LiveKit code, or `@/components/{ui,agents-ui,ai-elements}/` imports to this app. The pre-merge dependency-check grep (`grep livekit package.json`) is the gate; CI rule lands later.
```

- [ ] **Step 2:** Delete the entire `### Currently Installed (Phase 3C.2)` LiveKit subsection.

- [ ] **Step 3:** Delete the `> **Candidate-surface exception (Phase 3C.2 LiveKit port).** ...` callout in the "Component Library" section.

- [ ] **Step 4:** In the Directory Structure section, delete these rows/blocks:
- `(interview)/` route subtree
- `components/interview/` row
- Any mention of `components/{ui,agents-ui,ai-elements}/`
- `hooks/agents-ui/` row (and `hooks/` parent — now removed entirely)

- [ ] **Step 5:** Delete the `### Live interview UI (Phase 3C.2 — shipped)` subsection. Replace with one line: `Candidate interview surface lives at frontend/session/. See frontend/session/CLAUDE.md.`

- [ ] **Step 6:** In `### Pending UI work (Phase 3D)`, delete the "Mid-session rejoin" bullet.

- [ ] **Step 7:** In the `### Component Placement Rules` table, delete the "Candidate interview surface" row.

- [ ] **Step 8:** Delete the `### Candidate Interview Surface` subsection from "Two Surfaces — Design Constraints" (the parent header should be renamed to `## Dashboard Design Constraints`).

- [ ] **Step 9:** In `## Auth Flow`, delete the `### Candidates (Token-Based — No Supabase Auth) [Phase 2+]` subsection. Add one line: `Candidates have no auth on this surface — they use frontend/session/.`

- [ ] **Step 10:** In the API Client section's "Typed API namespaces" table, delete the `candidate-session.ts` row.

- [ ] **Step 11:** Delete the `### Live interview state (Phase 3C.2 — shipped)` subsection from "State Management".

- [ ] **Step 12:** Delete the entire `## LiveKit Integration` section.

- [ ] **Step 13:** In `## Human Review Required For`, delete:
- "Any component in `app/(interview)/` that touches the session state or pre-check flow"
- "Any change to how candidate consent is captured and surfaced"

- [ ] **Step 14:** Add a new section after the existing "Component Library" section:

```markdown
### Code shared by duplication with `frontend/session`

The following files exist verbatim in both apps and must be kept in sync. There is no shared package — duplication was the deliberate choice (small surface, stable APIs, physical isolation between recruiter and candidate origins).

| File | Reason kept identical |
|---|---|
| `lib/utils.ts` | `cn` helper — never diverges |
| `lib/api/errors.ts` | Error narrowing shape used by hooks in both apps |
| `components/px/Button.tsx` | Visual + a11y identity must match across surfaces |
| `components/px/Input.tsx` | Same |
| `components/px/Toaster.tsx` | Same (sonner config) |
| `public/projectx-logo.svg` | Brand identity |
| Shadcn → px CSS-variable token mapping in `app/globals.css` | Visual consistency |

Any change to one of these files in either app MUST be applied to the other in the same PR, or the PR description must explicitly call out the deliberate divergence with a one-line rationale.
```

- [ ] **Step 15:** Verify no candidate references remain.

```bash
grep -iE "livekit|wizardshell|agents-ui|ai-elements|candidate-session|\(interview\)" frontend/app/CLAUDE.md
```
Expected: zero matches except possibly a one-line pointer to `frontend/session/`.

- [ ] **Step 16:** Commit.

```bash
git add frontend/app/CLAUDE.md
git commit -m "docs(app): surgery — remove candidate-surface sections (moved to frontend/session)"
```

### Task 5.3: Rewrite frontend/app/AGENTS.md

**Files:**
- Modify: `frontend/app/AGENTS.md`

- [ ] **Step 1:** Replace the "Two design systems" block. The new file content:

```markdown
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

# One design system

This app uses `components/px/` (hand-rolled, on `@base-ui-components/react`) for every route. There is no shadcn enclave here — that surface moved to `frontend/session/`.
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/app/AGENTS.md
git commit -m "docs(app): rewrite AGENTS.md — one design system (candidate moved out)"
```

### Task 5.4: Update backend/nexus/CLAUDE.md

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1:** Find and rewrite the `### Invite / confirmation link URLs` subsection of `## Notifications Abstraction`. Replace with:

```markdown
### Invite / confirmation link URLs

Outbound emails build links pointing at one of two frontends. There are two distinct settings — pick the right one per email type:

| Email type | Setting | Frontend app |
|---|---|---|
| Company admin invite, team invite, team-invite resend | `settings.frontend_base_url` | `frontend/app` (recruiter dashboard) |
| Candidate interview invite, interview resend | `settings.candidate_session_base_url` | `frontend/session` (candidate interview surface) |

Pointing a candidate-bound email at `frontend_base_url` would land the candidate on the recruiter login page — the wrong surface entirely. The historical pattern `f"{settings.frontend_base_url}/interview/{token_str}"` was correct only because both surfaces lived in the same app; after the 2026-05-01 split, that pattern is a bug.

**Anti-regression grep**: `grep -rn "frontend_base_url.*interview\|frontend_base_url.*candidate-session" app/` must return zero matches. Pre-merge gate today; CI gate when CI lands.

Every environment must set BOTH settings explicitly. The localhost defaults are local-dev convenience only.
```

- [ ] **Step 2:** Find the scheduler row in "Module Responsibilities" and append: `...notification dispatch reads settings.candidate_session_base_url (NOT frontend_base_url).`

- [ ] **Step 3:** Add to `### Logging & PII Discipline` under `## Code Standards`:

```markdown
- The candidate JWT (`token` path param in `/api/candidate-session/{token}/*`) MUST NOT appear in any structured log field, error message, or audit row. Use `jti_prefix=<first 8 chars of jti>` from the verified token's payload, never the raw bearer string.
```

- [ ] **Step 4:** Commit.

```bash
git add backend/nexus/CLAUDE.md
git commit -m "docs(nexus): split notifications URL discipline — frontend_base vs candidate_session_base"
```

### Task 5.5: Create frontend/session/CLAUDE.md

**Files:**
- Create: `frontend/session/CLAUDE.md`

- [ ] **Step 1:** Create `frontend/session/CLAUDE.md` with this content:

```markdown
@AGENTS.md

# ProjectX — Frontend (Session)
## Claude Code Context (Candidate Interview)

> Read the root `CLAUDE.md` first. This file contains candidate-session-specific
> rules that extend it.

---

## What This Surface Is

The Next.js app at `frontend/session/` is the **candidate interview surface**.
A candidate enters via a single-use JWT-signed link (`/interview/{token}`),
completes a pre-check wizard (consent → OTP → camera/mic), and joins a
LiveKit-backed live interview with the AI agent.

**This is the highest-risk surface in the product:**
- Candidates are NOT Supabase Auth users. The token in the URL is the only
  credential, single-use, atomic-consume on `/start`.
- Camera, microphone, and (eventually) recording flow through this origin.
- The token must never appear in `Referer` headers, browser logs, or any
  third-party analytics.

The recruiter dashboard lives in a separate app at `frontend/app/`. Do NOT
import recruiter-side modules or add Supabase auth here.

---

## Tech Stack

- Next.js 16 App Router, React 19, TypeScript strict mode
- Tailwind v4 (in-house tokens duplicated from `frontend/app`)
- LiveKit (`livekit-client` + `@livekit/components-react`) — lazy-loaded inside
  `WizardShell.tsx` so pre-check steps do not pull the SDK
- TanStack Query v5 for the 6 candidate-session endpoints
- React Hook Form + Zod for consent + OTP forms
- `sonner` for toasts
- Vitest + Testing Library + jsdom for tests

**Forbidden dependencies** (pre-merge grep gate; CI gate when CI lands):
`@supabase/*`, `@dnd-kit/*`, `gsap`, `@dagrejs/dagre`, `cmdk`,
`embla-carousel-react`, `media-chrome`, `@microsoft/fetch-event-source`,
`ai`. Adding any of these requires a justification in the PR description.

---

## Absolute Rules

### Token handling
- The candidate JWT lives in the URL path only. NEVER store it in cookies,
  localStorage, sessionStorage, or any JS variable beyond the immediate
  function scope that calls `candidateSessionApi.*`.
- NEVER log the token. NEVER include it in error messages displayed to
  the candidate. NEVER send it to any third party.
- When Sentry is wired (future PR), the candidate-session DSN's `beforeSend`
  MUST scrub `/interview/[^/]+` from URLs, breadcrumbs, and stack traces.
  Document this in the Sentry-wiring PR.

### No Supabase
- This app does NOT import `@supabase/*`. The candidate flow has no
  cookies, no SSR session, no JWKS verification on the frontend.
- The only auth happens server-side at Nexus on every
  `/api/candidate-session/{token}/*` call.

### Security headers always on
- `next.config.ts` `headers()` is the single source of truth.
- Includes Strict-Transport-Security, X-Frame-Options DENY,
  X-Content-Type-Options nosniff, Referrer-Policy: no-referrer,
  Permissions-Policy (camera + microphone scoped to `self`),
  Cross-Origin-Opener-Policy: same-origin, Cross-Origin-Resource-Policy:
  same-origin, AND a strict CSP including `frame-ancestors 'none'`.
- Loosening any header requires a threat-model update in `docs/security/`.

### Env validation
- `lib/env.ts` parses `process.env` through a zod schema at module load.
  Invalid config crashes the app — there is no fallback.
- All public env reads go through `env.NEXT_PUBLIC_API_URL`, never raw
  `process.env.*` in component code.

### No analytics on the candidate surface
- No GA, no PostHog, no Hotjar, no session replay.
- When Sentry is wired (future PR), no session replay on this surface.
- Adding any telemetry destination requires a threat-model update.

### Two-app drift discipline
- The following files exist verbatim in `frontend/app` and must be kept
  in sync: `lib/utils.ts`, `lib/api/errors.ts`,
  `components/px/{Button,Input,Toaster}.tsx`,
  `public/projectx-logo.svg`, the shadcn → px token mapping in
  `app/globals.css`.
- Any change here MUST be applied in `frontend/app` in the same PR
  (or the PR description must call out the deliberate divergence).

---

## Directory Structure

```
frontend/session/
├── app/
│   ├── layout.tsx               ← Root layout (fonts + px theme attrs + InterviewProviders)
│   ├── page.tsx                 ← Friendly landing for accidental root visits
│   ├── not-found.tsx
│   ├── globals.css              ← Duplicated px tokens + shadcn → px mapping
│   ├── healthz/route.ts         ← Health probe
│   └── interview/[token]/
│       ├── page.tsx             ← Wizard host
│       ├── WizardShell.tsx      ← Lazy-loads LiveSessionShell via next/dynamic
│       ├── ConsentStep.tsx
│       ├── OtpStep.tsx
│       ├── CameraMicStep.tsx
│       └── error/page.tsx       ← Token error landing
├── components/
│   ├── px/                      ← 3 duplicated primitives (Button, Input, Toaster)
│   ├── agents-ui/               ← LiveKit shadcn enclave (audio viz, control bar, transcript)
│   ├── interview/               ← App shell, view controller, completion/error/reconnecting screens
│   ├── ui/                      ← shadcn ui primitives used by agents-ui
│   └── ai-elements/             ← shadcn AI SDK elements (transcript, message, shimmer)
├── hooks/agents-ui/             ← Audio visualizer + control-bar canvas hooks
├── lib/
│   ├── env.ts                   ← Zod env validator
│   ├── utils.ts                 ← Duplicated cn helper
│   ├── api/
│   │   ├── candidate-session.ts ← 6 endpoints under /api/candidate-session/{token}/
│   │   └── errors.ts            ← Duplicated error narrowing
│   └── hooks/
│       └── use-{candidate-session,consent,request-otp,verify-otp}.ts
├── public/
│   └── projectx-logo.svg        ← Duplicated brand asset
└── tests/
    ├── setup.ts                 ← jsdom polyfills + getUserMedia mock
    ├── _utils/render.tsx        ← Test render harness with QueryClient
    ├── lib/
    │   ├── env.test.ts
    │   └── api/candidate-session.test.ts
    └── components/interview/    ← 6 component tests
```

---

## Candidate JWT Rules

- Single-use HS256 token, 72-hour expiry. Atomic-consume happens
  server-side on `POST /start`. No replay possible.
- Token is verified server-side on every API call by Nexus middleware.
  The frontend trusts the URL but does NOT verify the signature.
- Supersession chain: when the recruiter resends an invite, Nexus mints
  a new token row and stamps `superseded_at` on the prior. The frontend
  has no view into this — it just discovers the old token is rejected
  with `TOKEN_SUPERSEDED` and shows the error landing page.

---

## API Client (`lib/api/candidate-session.ts`)

Six endpoints, all under `/api/candidate-session/{token}/*`:

| Method | Path | Purpose |
|---|---|---|
| GET  | `/pre-check` | Initial state load (company, stage, consent text, OTP requirement) |
| POST | `/consent` | Capture consent + signature |
| POST | `/request-otp` | Send OTP via email/SMS |
| POST | `/verify-otp` | Verify the 6-digit code |
| POST | `/start` | Atomic-consume token, mint LiveKit creds, dispatch agent |
| POST | `/rejoin` | Mid-session reconnect (for active sessions) |

This client deliberately does NOT use the recruiter `apiFetch` wrapper —
that wrapper auto-attaches a Supabase bearer token. The candidate flow
has no Supabase session and must not send any `Authorization` header.

---

## LiveKit Integration

- The LiveKit SDK (`livekit-client` + `@livekit/components-react`) is
  lazy-loaded via `next/dynamic` inside `WizardShell.tsx` so the pre-check
  steps do not pull it.
- LiveKit URL + access token are returned by Nexus's `/start` endpoint;
  the frontend does NOT generate tokens.
- Recordings use LiveKit Egress writing to S3 — no frontend involvement.

---

## Tailwind Standards

- Tailwind v4 utility classes only. No custom CSS unless utility doesn't exist.
- Colours come from the duplicated px tokens in `app/globals.css`. Do not
  use raw hex values.
- The candidate surface MUST work on mobile viewports — candidates may
  join from any device.

---

## Accessibility

- Every step of the wizard must be keyboard-navigable.
- Audio visualizers must have an `aria-label` and respect
  `prefers-reduced-motion`.
- OTP input must announce errors via `aria-live="polite"`.
- Camera + mic step must announce permission state and provide a clear
  recovery path when permission is denied.

---

## Security

> Cross-cutting standards (rate limiting, supply chain, secrets rotation,
> logging/PII, audit, code review, incident response) are defined in the
> root `CLAUDE.md` → Enterprise Operating Standards.

App-specific notes:
- The CSP in `next.config.ts` is the boundary contract. Adding a new
  third-party origin to `connect-src` requires a threat-model update.
- For self-hosted LiveKit deployments, parameterize the `wss://*.livekit.cloud`
  entries via env at that point.
- Dockerfile runs as non-root user (`nextjs:nodejs`).

---

## Production Operating Rules

- Lockfile (`package-lock.json`) is authoritative. CI uses `npm ci` (when
  CI lands).
- Bundle budget: pre-LiveKit pages (`/`, `/interview/[token]` pre-`/start`)
  target < 180 KB gzipped first-load JS. LiveKit-bearing routes are
  exempt from the bundle gate but must lazy-load the SDK.
- Performance targets (Lighthouse on production build): LCP < 2.0s on
  candidate pre-check; TTI < 3.5s; CLS < 0.1.

---

## Dev Commands

```bash
npm run dev          # localhost:3002
npm run build
npm run lint
npm run type-check
npm run test         # Vitest — includes the 100%-branch gate
npm run test:coverage
```

---

## Human Review Required For

- Any change to `next.config.ts` `headers()` (security headers + CSP)
- Any change to `lib/api/candidate-session.ts` (sole API surface)
- Any new `Authorization` header sent from the candidate surface
- Any change that adds a third-party origin to CSP `connect-src`
- Any change to OTP, consent, or camera/mic step flow

---
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/CLAUDE.md
git commit -m "docs(session): create CLAUDE.md for the candidate interview surface"
```

### Task 5.6: Create frontend/session/AGENTS.md

**Files:**
- Create: `frontend/session/AGENTS.md`

- [ ] **Step 1:** Create `frontend/session/AGENTS.md`.

```markdown
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

# One purpose

This app exists for one user (the candidate) and one task (joining a single live interview). Do not add recruiter features, admin features, account management, or anything that requires Supabase auth. If a feature touches anything other than the candidate's own session, it belongs in `frontend/app/` or `frontend/admin/`, not here.

# Token discipline

The candidate JWT is in the URL path. Never log it, never store it, never send it to any third party. When Sentry is wired (future PR), its `beforeSend` MUST scrub `/interview/[^/]+` paths.
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/AGENTS.md
git commit -m "docs(session): create AGENTS.md (one purpose + token discipline)"
```

### Task 5.7: Create frontend/session/README.md

**Files:**
- Create: `frontend/session/README.md`

- [ ] **Step 1:** Create a minimal README.

```markdown
# frontend/session

Candidate interview surface for ProjectX. Token-gated single-use sessions.

See `CLAUDE.md` for architecture, rules, and dev commands.

## Quick start

```bash
cp .env.local.example .env.local
npm install
npm run dev   # localhost:3002
```

## Tests

```bash
npm run test
npm run test:coverage   # enforces 100% branch on candidate-session paths
```
```

- [ ] **Step 2:** Commit.

```bash
git add frontend/session/README.md
git commit -m "docs(session): README quickstart"
```

---

## Phase 6 — Final verification

### Task 6.1: Manual end-to-end smoke test

**Files:** none — verification only.

- [ ] **Step 1:** Start all services.

```bash
# Terminal 1: backend
cd backend/nexus && docker compose up

# Terminal 2: frontend/app (port 3000)
cd frontend/app && npm run dev

# Terminal 3: frontend/admin (port 3001)
cd frontend/admin && npm run dev

# Terminal 4: frontend/session (port 3002)
cd frontend/session && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

- [ ] **Step 2:** Run the recruiter → invite → candidate → completion flow.

In a browser:
1. Login to `http://localhost:3000` as recruiter.
2. Create a candidate, send an interview invite.
3. Open Inbucket at `http://localhost:54324`, find the invite email.
4. **Verify the link in the email body starts with `http://localhost:3002/interview/`** (NOT `:3000`). This confirms Phase 2 backend changes.
5. Click the link → lands on `frontend/session:3002` → completes consent → enters OTP → grants camera/mic → joins the LiveKit room → AI agent connects → completes a question → sees CompletionScreen.
6. Refresh the same URL after completion → sees "session already completed" error landing.
7. Try a URL with one character of the token mangled → 401-flavored error landing (no server crash, no PII leak).

If any step fails, do NOT call this PR done. Fix the failure first.

- [ ] **Step 3:** Verify security headers in production-build mode.

```bash
cd frontend/session && npm run build && npm start &
sleep 5
curl -I http://localhost:3002/ 2>&1 | grep -iE "strict-transport|x-frame|x-content|referrer-policy|permissions-policy|cross-origin|content-security"
kill %1
cd ../..
```
Expected: every header from `SECURITY_HEADERS` in `next.config.ts` appears, including the full CSP string.

- [ ] **Step 4:** Run dependency-check greps (the manual gates from CLAUDE.md).

```bash
echo "=== livekit must NOT be in frontend/app ==="
grep -E "livekit|^.*motion" frontend/app/package.json
echo ""
echo "=== @supabase must NOT be in frontend/session ==="
grep "@supabase" frontend/session/package.json
echo ""
echo "=== anti-regression backend grep ==="
grep -rn "frontend_base_url.*interview" backend/nexus/app/
```
Expected: all three return zero matches.

- [ ] **Step 5:** Verify coverage gates.

```bash
cd frontend/session && npm run test:coverage 2>&1 | tail -40 && cd ../..
```
Expected: report includes 100% branch on `candidate-session.ts`, `OtpStep.tsx`, `app.tsx`. Global ≥80%.

- [ ] **Step 6:** Capture bundle-size delta + coverage excerpt for the PR description.

```bash
echo "BEFORE: $(cat /tmp/bundle-baseline.txt)"
echo "AFTER:  $(cat /tmp/bundle-after.txt)"
```

### Task 6.2: Push branch + open PR

**Files:** none.

- [ ] **Step 1:** Push the branch.

```bash
git push -u origin feat/extract-frontend-session
```

- [ ] **Step 2:** Open the PR via `gh`.

```bash
gh pr create --title "Extract candidate UI into frontend/session" --body "$(cat <<'EOF'
## Summary

- Extracts the candidate interview surface (`/interview/[token]`, LiveKit live session, OTP/consent/camera-mic wizard, audio visualizers) out of `frontend/app` into a new sibling Next.js 16 app at `frontend/session/` (port 3002).
- Backend gains `candidate_session_base_url` setting so scheduler invite emails point at the new origin.
- After this PR: `frontend/app` is recruiter-only; `frontend/session` is candidate-only; `frontend/admin` unchanged.

## Spec

`docs/superpowers/specs/2026-05-01-frontend-session-extract-design.md`

## Bundle-size delta (frontend/app)

- BEFORE: <fill in from /tmp/bundle-baseline.txt>
- AFTER:  <fill in from /tmp/bundle-after.txt>

## Coverage gates (frontend/session)

100% branch on:
- `lib/api/candidate-session.ts`
- `app/interview/[token]/OtpStep.tsx`
- `components/interview/app/app.tsx`

Global: ≥80% lines / statements.

## Deploy ordering (production)

1. Set `CANDIDATE_SESSION_BASE_URL` in backend env BEFORE deploying backend.
2. Deploy backend.
3. Deploy `frontend/session` (port 3002).
4. Smoke-test against staging.
5. Deploy `frontend/app` (drops `/interview/*`).
6. Optional: Cloudflare 30-day redirect from `app.projectx.com/interview/*` → `session.projectx.com/interview/*`.

## Test plan

- [ ] Backend: `pytest` green
- [ ] frontend/app: `lint && type-check && test && build` green
- [ ] frontend/session: `lint && type-check && test:coverage && build` green
- [ ] frontend/admin: `build` green (regression check)
- [ ] End-to-end manual smoke: recruiter sends invite → email link is `:3002` → candidate completes → CompletionScreen renders
- [ ] Security headers verified via `curl -I` on production build
- [ ] Dependency-check greps return zero matches

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3:** Confirm CI status (none today; manual verification per Phase 6 Step 5 is the gate).

---

## Self-Review Notes

The plan covers every spec section. Test gates are concrete and per-task. No placeholders, no "implement later" handwaves. Each phase ends with a verification gate before the next phase starts. The breaking phase (4) is preceded by an additive phase (3) that validates with manual smoke. Documentation is its own phase (5) so doc churn doesn't gate the code work. Final manual smoke (Phase 6) is the ship gate.

Known limitations:
- Phase 6 has no automated CI; verification is manual `curl -I`, manual greps, manual end-to-end browser flow. CI gating is deferred per spec § Out of Scope.
- Test 2.3 leaves the fixture-arrangement portions for the engineer to fill in by copying from existing tests in `test_scheduler_service.py`. This is intentional — the existing fixtures are file-specific and would be wrong to invent.
- Task 5.2 lists 14 doc-edit steps; an engineer may want to do them in a single editor pass and one commit instead of 14 separate edits. That's fine — the steps are exhaustive but can be batched.
