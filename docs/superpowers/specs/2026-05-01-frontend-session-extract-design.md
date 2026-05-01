# Extract Candidate Interview UI into `frontend/session`

**Date:** 2026-05-01
**Status:** Design — pending implementation plan
**Owner:** ProjectX team
**Related:** `2026-05-01-drop-langfuse-modular-monolith-design.md` (Phase 3 — engine merged into Nexus, candidate UI shipped in `frontend/app`)

---

## Summary

Extract the candidate interview surface (`/interview/[token]`, LiveKit live session, audio visualizers, OTP + consent + camera/mic wizard) out of `frontend/app` into a new sibling Next.js 16 app at `frontend/session/` (port 3002). After the cut, `frontend/app` is recruiter-only; `frontend/session` is candidate-only; `frontend/admin` is unchanged.

Backend gains a new `candidate_session_base_url` setting so scheduler invite emails point at the new origin. No endpoint changes, no auth changes, no schema migrations.

This is a clean cutover in a single PR. No transition redirect window — pre-production posture, no live invites in flight.

---

## Motivation

### Today's posture
`frontend/app` serves both the recruiter dashboard (Supabase ES256 cookie auth) and the candidate interview surface (single-use HS256 JWT in URL path). Two completely different auth models share one Next.js process, one cookie jar, one bundle, one deploy pipeline, one error budget, and one `proxy.ts` middleware that branches on `path.startsWith("/interview")`.

### Why split

1. **Physical isolation of the highest-risk surface.** The candidate token is a credential-grade value. Today the only thing keeping recruiter session code from reading it (or vice versa) is code discipline in `proxy.ts` and the absence of accidental imports. After the split, the candidate origin literally cannot call recruiter APIs or read recruiter cookies — the boundary is enforced by the operating system, not by the codebase.
2. **Independent error budgets.** Root CLAUDE.md sets the candidate session API at 99.95% availability vs. 99.9% for the recruiter dashboard. A frontend regression in one surface cannot break in-flight interviews on the other.
3. **Tighter CSP per origin.** The candidate origin's CSP can allow only `NEXT_PUBLIC_API_URL` + LiveKit hosts. The recruiter origin loses LiveKit + WebRTC origins entirely.
4. **Bundle hygiene.** `livekit-client` + `@livekit/components-react` (~180 KB gzip) + `motion` + the entire shadcn `agents-ui` + `ai-elements` registries leave the recruiter bundle.
5. **`proxy.ts` simplification.** Loses its candidate detection branch entirely. The candidate origin's middleware does only one thing: set security headers.

### Why now

Pre-production. No live candidate invites in flight. The `frontend/admin` precedent already proves the pattern (sibling Next.js app, separate port, separate deploy). The candidate surface is well-isolated already (verified in the file map below) — the cost of the split is small and the cost of waiting (more recruiter code accruing accidental shared dependencies, more candidate code accruing recruiter-side coupling) grows.

---

## Architecture Overview

Three independent Next.js 16 apps:

| App | Port | Purpose | Auth | LiveKit |
|---|---|---|---|---|
| `frontend/app` | 3000 | Recruiter dashboard | Supabase ES256 cookies | none |
| `frontend/admin` | 3001 | ProjectX-operator console | Supabase (admin claim) | none |
| `frontend/session` | 3002 | Candidate interview | Single-use HS256 JWT in URL path | yes (lazy-loaded) |

`backend/nexus` adds a single new setting `candidate_session_base_url`. CORS allowlist extends to include port 3002. Scheduler invite/resend email link builders switch from `frontend_base_url` to `candidate_session_base_url`. Six existing candidate endpoints under `/api/candidate-session/{token}/*` are unchanged.

**No shared package.** UI primitives that both apps need (`Button`, `Input`, `Toaster`, `cn`, `errors.ts`, the shadcn → px CSS-variable token mapping in `globals.css`) are duplicated verbatim. Both surfaces are stable enough that drift is a non-issue, and a shared workspace package would re-couple the two surfaces' build and test cycles. A drift-discipline checklist in both CLAUDE.md files is the explicit cost.

---

## New App Scaffolding (`frontend/session`)

### File tree

```
frontend/session/
├── package.json                    ← Next 16 + LiveKit + TanStack + zod; NO Supabase
├── next.config.ts                  ← output: standalone, headers() with full security headers + CSP
├── tsconfig.json                   ← matches frontend/app exactly (strict: true, target ES2017, @/* path alias)
├── postcss.config.mjs              ← Tailwind v4
├── eslint.config.mjs               ← inherits eslint-config-next
├── vitest.config.ts                ← coverage thresholds: 80% global, 100% branch on three enumerated files
├── components.json                 ← shadcn registries: @agents-ui, @ai-elements
├── app-config.ts                   ← moved from frontend/app
├── Dockerfile                      ← matches frontend/admin pattern: node:22-alpine, multi-stage, non-root, port 3002
├── docker-compose.yml              ← port 3002, dev + prod profiles (mirrors frontend/app pattern)
├── .env.local.example              ← NEXT_PUBLIC_API_URL only
├── .gitignore, .dockerignore
├── README.md, CLAUDE.md, AGENTS.md
├── app/
│   ├── layout.tsx                  ← MERGED: recruiter root layout + interview layout (see "Layout merge" below)
│   ├── page.tsx                    ← "private interview link" landing copy
│   ├── not-found.tsx               ← same friendly copy
│   ├── globals.css                 ← duplicated px tokens + shadcn → px mapping
│   ├── healthz/route.ts            ← health probe for Railway/ECS
│   └── interview/[token]/
│       ├── page.tsx, WizardShell.tsx, ConsentStep.tsx, OtpStep.tsx, CameraMicStep.tsx
│       └── error/page.tsx
├── components/
│   ├── px/                         ← DUPLICATED: only Button, Input, Toaster (3 of 14)
│   ├── agents-ui/                  ← MOVED: 14 files + blocks/agent-session-view-01/**
│   ├── interview/                  ← MOVED: providers + app/** + app/hooks/**
│   ├── ui/                         ← MOVED: button, button-group, select, separator, toggle, tooltip
│   └── ai-elements/                ← MOVED: conversation, message, shimmer
├── hooks/agents-ui/                ← MOVED: 6 visualizer + control-bar hooks
├── lib/
│   ├── env.ts                      ← zod-validated env loader; throws at boot on invalid input
│   ├── utils.ts                    ← DUPLICATED: cn helper
│   ├── api/
│   │   ├── candidate-session.ts    ← MOVED: 6 endpoints
│   │   └── errors.ts               ← DUPLICATED
│   └── hooks/
│       └── use-{candidate-session,consent,request-otp,verify-otp}.ts  ← MOVED
├── public/
│   └── projectx-logo.svg           ← DUPLICATED (referenced by app-config.ts)
└── tests/
    ├── setup.ts                    ← DUPLICATED + getUserMedia polyfill added
    ├── _utils/render.tsx           ← DUPLICATED verbatim
    ├── lib/
    │   ├── env.test.ts             ← NEW: zod schema rejects invalid env
    │   └── api/candidate-session.test.ts  ← MOVED
    └── components/interview/
        ├── OtpStep.test.tsx        ← MOVED (consolidated under interview/)
        ├── app.test.tsx
        ├── CompletionScreen.test.tsx
        ├── ProgressBanner.test.tsx
        ├── ReconnectingOverlay.test.tsx
        └── use-session-outcome.test.tsx
```

### Layout merge (`app/layout.tsx`)

The new app's root layout merges two existing layouts:

1. From `frontend/app/app/layout.tsx`: the `<html>` shell with `next/font/google` Inter + Fraunces + JetBrains_Mono variables, `data-px-theme="warm-light"` and `data-px-density="comfortable"` attributes, the `<body>` with `font-sans` + `bg-background text-foreground`. The `data-px-*` attributes are load-bearing for the px primitives.
2. From `frontend/app/app/(interview)/layout.tsx`: the `<InterviewProviders>` wrapper (QueryClient + Toaster) and the `min-h-screen w-full` shell with `var(--px-bg)` / `var(--px-fg)` background.

The metadata block updates to `title: "ProjectX Interview"`, `description: "AI-led interview session"`. The candidate-surface `<html lang="en">` stays lang-en for now (i18n is out of scope).

### Security headers (next.config.ts `headers()`)

The new app extends the existing `frontend/app/next.config.ts` security pattern with a real CSP. CSP is feasible here because the candidate surface has no Supabase, no React Query devtools, and no inline-bootstrap-script complexity that blocked CSP in the recruiter app.

```ts
// next.config.ts (sketch — full diff in implementation plan)
const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },  // load-bearing: prevents JWT-in-URL leak
  { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=(), interest-cohort=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "script-src 'self'",
      "style-src 'self' 'unsafe-inline'",  // Tailwind runtime-injected styles
      "img-src 'self' data: blob:",
      "media-src 'self' blob: mediastream:",
      // NEXT_PUBLIC_API_URL must be added at build time via the Docker ARG.
      // wss://*.livekit.cloud covers the LiveKit Cloud project (per backend/nexus/livekit.toml).
      `connect-src 'self' ${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"} wss://*.livekit.cloud https://*.livekit.cloud`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
      "upgrade-insecure-requests",
    ].join("; "),
  },
];
```

`Referrer-Policy: no-referrer` is the load-bearing one — it prevents the candidate JWT (in URL path) from leaking via `Referer` headers when the page navigates externally.

If a future scenario requires self-hosted LiveKit, the `wss://*.livekit.cloud` entries are parameterized via env at that point. Hardcoded for now.

### Env validation

`lib/env.ts` parses `process.env` through a zod schema at module load. Required vars: `NEXT_PUBLIC_API_URL` (URL). Invalid config crashes the app at boot — no fallback, no warning-and-continue. Discipline mirrors backend `pydantic-settings`.

### Sentry — deferred

Sentry is NOT installed in `frontend/app` today (despite CLAUDE.md aspirational mentions). Adding it in this PR is out of scope. The CLAUDE.md updates document the requirement so when Sentry is wired (a separate PR that touches all three frontend apps), the candidate-session DSN MUST scrub `/interview/[^/]+` from URL/breadcrumb/stack data and drop events with raw JWT pattern matches. Until then, the candidate surface logs nothing to a third party — `console.*` calls in production are flagged by ESLint `no-console` (warn).

### Dependencies

Runtime: `next` 16.2.4, `react` 19.2.5, `react-dom` 19.2.5, `livekit-client`, `@livekit/components-react`, `@livekit/components-styles`, `@livekit/protocol`, `@tanstack/react-query`, `react-hook-form`, `@hookform/resolvers`, `zod`, `sonner`, `clsx`, `tailwind-merge`, `lucide-react`, `class-variance-authority`, `motion`.

Dev: `vitest`, `@vitest/coverage-v8`, `jsdom`, `@testing-library/{react,jest-dom,user-event}`, `tailwindcss` v4, `@tailwindcss/postcss`, `eslint`, `eslint-config-next`, `typescript`, `@types/*`.

**Forbidden dependencies** (documented in CLAUDE.md; CI-enforced when CI lands): `@supabase/*`, `@dnd-kit/*`, `gsap`, `@dagrejs/dagre`, `cmdk`, `embla-carousel-react`, `media-chrome`, `@microsoft/fetch-event-source`, `ai`.

### Dockerfile

Matches the `frontend/admin/Dockerfile` pattern verbatim, just with `PORT=3002` and `EXPOSE 3002`:
- Base image: `node:22-alpine` (unpinned digest, matching the precedent — pinning all three Dockerfiles' digests is a follow-up improvement, not blocking this PR)
- Multi-stage: `deps` → `builder` → `runner`
- Non-root user: `nextjs:nodejs` (UID/GID 1001)
- `output: "standalone"` minimal image
- Build arg: `NEXT_PUBLIC_API_URL` baked at build time (required for the CSP `connect-src` to be correct in the production bundle)

---

## File Migration Manifest

### MOVE (delete from `frontend/app`, create at `frontend/session`)

Routes:
- `app/(interview)/layout.tsx` → merged into `frontend/session/app/layout.tsx`
- `app/(interview)/interview/[token]/{page,WizardShell,ConsentStep,OtpStep,CameraMicStep}.tsx` → `frontend/session/app/interview/[token]/`
- `app/(interview)/interview/[token]/error/page.tsx` → same path

Component trees (verified zero recruiter usage):
- `components/agents-ui/**` (14 files + `blocks/agent-session-view-01/**`)
- `components/interview/**` (8 files + `app/hooks/**`)
- `components/ui/**` (button, button-group, select, separator, toggle, tooltip)
- `components/ai-elements/**` (conversation, message, shimmer)

Hooks tree:
- `hooks/agents-ui/**` (6 files)

Lib (single-purpose candidate files):
- `lib/api/candidate-session.ts`
- `lib/hooks/use-candidate-session.ts`
- `lib/hooks/use-consent.ts`
- `lib/hooks/use-request-otp.ts`
- `lib/hooks/use-verify-otp.ts`

Top-level:
- `app-config.ts`

Tests:
- `tests/lib/api/candidate-session.test.ts`
- `tests/components/OtpStep.test.tsx` → `tests/components/interview/OtpStep.test.tsx` (consolidated)
- `tests/components/interview/{app,CompletionScreen,ProgressBanner,ReconnectingOverlay,use-session-outcome}.test.tsx`

### DUPLICATE (keep in `frontend/app`, copy verbatim into `frontend/session`)

| File | Reason kept identical |
|---|---|
| `lib/utils.ts` | `cn` helper — never diverges (used 22 times in candidate code) |
| `lib/api/errors.ts` | Error narrowing shape used by hooks in both apps |
| `components/px/Button.tsx` | Visual + a11y identity must match across surfaces |
| `components/px/Input.tsx` | Same |
| `components/px/Toaster.tsx` | Same (sonner config) |
| Shadcn → px token mapping in `app/globals.css` | Visual consistency |
| `public/projectx-logo.svg` | Referenced by `app-config.ts` (default `logo` field) |
| `tests/setup.ts` | Polyfills (`Storage`, `matchMedia`, `ResizeObserver`, `IntersectionObserver`) |
| `tests/_utils/render.tsx` | QueryClient harness for composition tests |

`frontend/session/components/px/index.ts` re-exports only the 3 duplicated primitives — keeps the duplicated surface honest.

### DELETE (no destination)

- `lib/hooks/use-start-session.ts` — verified zero callers project-wide. Dead code orphaned during a prior refactor. Delete; do not move.

### Path aliases

Both apps declare the same `@/*` path alias in `tsconfig.json`. After moves, zero source-level import rewrites needed — moves are mechanical filesystem renames preserved as `git mv`.

---

## Backend Changes (`backend/nexus`)

Three files touched, plus tests. No endpoints, no auth, no migrations.

### `app/config.py`

Add the new setting (after line 285):

```python
# Candidate session base URL — used to build interview invite links in
# scheduler emails. Kept SEPARATE from frontend_base_url so the two
# surfaces (recruiter dashboard vs. candidate session app) can be deployed
# at different origins. Every environment must set CANDIDATE_SESSION_BASE_URL
# explicitly.
candidate_session_base_url: str = "http://localhost:3002"

@field_validator("candidate_session_base_url")
@classmethod
def _strip_candidate_session_trailing_slash(cls, v: str) -> str:
    return v.rstrip("/")
```

Extend the CORS allowlist (line 179):

```python
cors_origins: list[str] = [
    "http://localhost:3000", "http://localhost:3001", "http://localhost:3002",
    "http://127.0.0.1:3000", "http://127.0.0.1:3001", "http://127.0.0.1:3002",
]
```

### `app/modules/scheduler/service.py`

Lines 102 and 203 — both currently `f"{settings.frontend_base_url}/interview/{token_str}"` — change to:

```python
invite_url=f"{settings.candidate_session_base_url}/interview/{token_str}",
```

`revoke_invite` builds no link — no change.

### `.env.example`

Update line 169 (CORS_ORIGINS) and add a new block after line 176:

```
CORS_ORIGINS=["http://localhost:3000","http://localhost:3001","http://localhost:3002","http://127.0.0.1:3000","http://127.0.0.1:3001","http://127.0.0.1:3002"]

# Recruiter dashboard origin — used to build /invite?token=... links in
# admin/settings emails. Must be set explicitly per environment.
FRONTEND_BASE_URL=http://localhost:3000

# Candidate session origin — used to build /interview/{token} links in
# scheduler invite/resend emails. Must point at the frontend/session app,
# NOT frontend/app.
CANDIDATE_SESSION_BASE_URL=http://localhost:3002
```

### Tests

Extend `tests/test_config_validators.py`:
- `test_candidate_session_base_url_strips_trailing_slash`
- `test_candidate_session_base_url_default_is_localhost_3002`
- `test_cors_origins_default_includes_3002`

Extend `tests/test_scheduler_service.py`:
- `test_send_invite_uses_candidate_session_base_url` — patches settings with distinct host values for `frontend_base_url` vs `candidate_session_base_url`, captures the rendered email body, asserts the link uses the candidate setting and does NOT contain the recruiter setting.
- `test_resend_invite_uses_candidate_session_base_url` — same.

The two settings must hold distinct hostnames in the test, otherwise a regression that wires the wrong one would silently pass.

### Anti-regression grep

```bash
grep -rn "frontend_base_url.*interview\|frontend_base_url.*candidate-session" backend/nexus/app/
```

Must return zero matches. Documented as a pre-merge grep guard in `backend/nexus/CLAUDE.md`; CI gate when CI lands.

---

## `frontend/app` Cleanup (Post-Move)

### proxy.ts — delete the `/interview` branch

Remove lines 38–43:

```diff
-  // Candidate interview pages are always public — candidates are NOT
-  // Supabase users. Their JWT lives in the URL path and is verified
-  // server-side by Nexus on every /api/candidate-session/{token}/* call.
-  if (path.startsWith("/interview")) {
-    return supabaseResponse;
-  }
-
```

### package.json — remove

- `@livekit/components-react`
- `@livekit/components-styles`
- `livekit-client`
- `motion`
- `@livekit/protocol` (if present)

Run `npm install` to regenerate `package-lock.json`.

### components.json — drop registries

```diff
   "registries": {
-    "@agents-ui": "https://livekit.io/ui/r/{name}.json",
-    "@ai-elements": "https://registry.ai-sdk.dev/{name}.json"
   }
```

If the `registries` object becomes empty, delete the key.

### Files to delete

- `app/(interview)/` whole tree
- `components/agents-ui/`, `components/interview/`, `components/ui/`, `components/ai-elements/`
- `hooks/agents-ui/` AND the `hooks/` parent dir (verified: `hooks/` only contains `agents-ui/`)
- `lib/hooks/use-{candidate-session,consent,request-otp,verify-otp,start-session}.ts`
- `lib/api/candidate-session.ts`
- `app-config.ts`
- The 7 moved test files
- `tests/components/interview/` directory if empty after moves

### Verification grep gauntlet

All must return zero matches:

```bash
grep -rn "components/agents-ui\|components/ai-elements\|components/interview" \
  frontend/app/app frontend/app/components frontend/app/lib frontend/app/hooks frontend/app/tests \
  --include="*.ts" --include="*.tsx"

grep -rn "from ['\"]@livekit\|from ['\"]livekit-client\|from ['\"]motion" \
  frontend/app/app frontend/app/components frontend/app/lib frontend/app/hooks

grep -rn "from ['\"]@/lib/api/candidate-session\|from ['\"]@/lib/hooks/use-candidate-session\|from ['\"]@/lib/hooks/use-consent\|from ['\"]@/lib/hooks/use-request-otp\|from ['\"]@/lib/hooks/use-verify-otp\|from ['\"]@/lib/hooks/use-start-session" \
  frontend/app

grep -rn "/interview" frontend/app/proxy.ts frontend/app/app frontend/app/lib

grep -rn "from ['\"]@/app-config" frontend/app
```

### Build / test gate

```bash
cd frontend/app
npm ci && npm run lint && npm run type-check && npm run test && npm run build
```

All green; bundle size reduced (recorded in PR description).

### Stays in `frontend/app`

- All 14 `components/px/` primitives (recruiter is the canonical version)
- `lib/utils.ts`, `lib/api/errors.ts`, `lib/api/client.ts`
- `app/globals.css` shadcn → px mapping (harmless without consumers; removal is a follow-up)
- `tests/setup.ts`, `tests/_utils/render.tsx`

---

## Test Infrastructure (`frontend/session`)

### `vitest.config.ts`

Coverage thresholds enforce the enterprise gate:

```ts
coverage: {
  provider: 'v8',
  reporter: ['text', 'lcov', 'html'],
  thresholds: {
    lines: 80,
    statements: 80,
    '**/lib/api/candidate-session.ts': { branches: 100, functions: 100 },
    '**/app/interview/[token]/OtpStep.tsx': { branches: 100 },
    '**/components/interview/app/app.tsx': { branches: 100 },
  },
  include: ['app/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}', 'lib/**/*.{ts,tsx}', 'hooks/**/*.{ts,tsx}'],
  exclude: [
    '**/*.d.ts', '**/node_modules/**', '**/.next/**',
    '**/components/ui/**',          // shadcn-generated
    '**/components/agents-ui/**',   // shadcn-generated (LiveKit)
    '**/components/ai-elements/**', // shadcn-generated (Vercel AI SDK)
  ],
}
```

The 100%-branch threshold per file makes any drop in candidate-path test coverage cause `npm run test` to exit non-zero (CI gate when CI lands).

### `tests/setup.ts`

Copied verbatim plus a `getUserMedia` polyfill for `CameraMicStep` tests:

```ts
if (typeof navigator !== 'undefined' && !navigator.mediaDevices) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [], getVideoTracks: () => [], getAudioTracks: () => [],
      }),
      enumerateDevices: vi.fn().mockResolvedValue([]),
      addEventListener: () => {}, removeEventListener: () => {},
    },
    writable: true, configurable: true,
  })
}
```

### `tests/_utils/render.tsx`

Copied verbatim — already minimal (just `QueryClientProvider` with retries off and `gcTime: 0`).

### Mocks

The moved tests already mock `@livekit/components-react`, `livekit-client`, and `agents-ui` providers inline. They keep these mocks unchanged after the move — the new app installs the same SDKs.

### CI — deferred

No `.github/workflows/` exists in the repo today. CI integration of `frontend/session` is documented in the root + new-app CLAUDE.md as a requirement for whoever wires CI later. The same docs enumerate the dependency-check rule that should fail the build if:
- `frontend/app/package.json` lists any `livekit-*` package, OR
- `frontend/session/package.json` lists any `@supabase/*` package.

Until CI exists, these are pre-merge manual greps documented in the build sequence's verification gates.

### New tests added with the move

- `tests/lib/env.test.ts` — verifies the zod env schema rejects missing/invalid input.

(A test for `next.config.ts` `headers()` is deferred — Next 16 doesn't expose the headers config in a unit-testable form without spinning up the dev server. Header coverage falls to the manual smoke gate in Phase 6 + a curl assertion in Phase 1.)

---

## Documentation Updates

### Root `/CLAUDE.md`

- Add `frontend/session/` row to Monorepo Structure.
- Update Phase 3C.2 row to note the 2026-05-01 extract.
- Add `frontend/session/lib/api/candidate-session.ts`, `frontend/session/lib/env.ts`, and `frontend/session/next.config.ts headers()` to Test Coverage Gates.
- Add a `frontend/session` block to Dev Commands.
- Add a hard rule under Security: `frontend/session` MUST NOT depend on `@supabase/*`; `frontend/app` MUST NOT depend on `livekit-*` or import from `components/{agents-ui,ai-elements}/`. Manual grep gate today; CI-enforced when CI lands.
- Add a deferred-work note: when Sentry is wired across all three frontend apps, the candidate-session DSN's `beforeSend` MUST scrub `/interview/[^/]+` from URL/breadcrumb/stack data and drop events with raw JWT pattern matches.
- Add a deferred-work note: when CI lands, the matrix MUST include `frontend/session` and the dependency-check rule above.

### `frontend/app/CLAUDE.md`

Major surgery (per Section 5 + 7 of the brainstorming dialog). Drop:
- "What This Surface Is" mention of the candidate UI
- "Currently Installed (Phase 3C.2)" LiveKit block
- Component Library "Candidate-surface exception" callout
- Directory rows for `app/(interview)/`, `components/{interview,ui,agents-ui,ai-elements}/`, `hooks/agents-ui/`
- "Live interview UI (Phase 3C.2 — shipped)" subsection
- Component Placement Rules row for the candidate surface
- "Two Surfaces → Candidate Interview Surface" subsection
- "Auth Flow → Candidates" subsection
- API Client `candidate-session.ts` row
- "State Management → Live interview state"
- LiveKit Integration section
- Human Review bullets referencing the candidate surface

Add:
- "Surface Boundary" header rewritten: this app is recruiter-only; candidate UI lives at `frontend/session/`.
- "Code shared by duplication" subsection enumerating the 6 duplicated files + drift discipline rule.

### `frontend/app/AGENTS.md`

Replace the "Two design systems" block with a "One design system" block. Next.js 16 warning block stays.

### `backend/nexus/CLAUDE.md`

Rewrite "Notifications Abstraction → Invite / confirmation link URLs" to split `frontend_base_url` (recruiter emails) from `candidate_session_base_url` (candidate emails). Document the anti-regression grep as a pre-merge gate (CI gate when CI lands). Add the candidate JWT logging discipline ("never log raw token; use `jti_prefix` only").

### NEW `frontend/session/CLAUDE.md`

Full new file modeled on the existing CLAUDE.md style. Sections: What This Surface Is, Tech Stack, Absolute Rules (token handling, no Supabase, security headers in next.config.ts always on, env validation, no analytics until Sentry+scrubbers wired, two-app drift discipline), Directory Structure, Candidate JWT Rules, API Client, LiveKit Integration, Tailwind Standards, Accessibility, Security, Production Operating Rules, Dev Commands, Human Review Required For.

The forbidden-deps list is enumerated explicitly. Adding `@supabase/*` or any of the recruiter-only packages to `frontend/session/package.json` is a CLAUDE.md-documented merge-blocker (and a CI gate when CI lands).

### NEW `frontend/session/AGENTS.md`

Three short blocks: Next.js 16 warning, "One purpose" (don't add recruiter features), "Token discipline" (never log, never store, never analytics).

---

## Build Sequence

Six phases. Phases 1–3 are additive; phase 4 is the breaking cut. Each phase has a verification gate.

### Phase 0 — Pre-flight
Clean working tree, branch off `main`, baseline build snapshot.

### Phase 1 — Scaffold `frontend/session` (additive)
Create the new app with empty shell. Build, test, healthz reachable, security headers verified via `curl -I http://localhost:3002/`. `frontend/app` unchanged.

### Phase 2 — Backend changes (additive)
Add `candidate_session_base_url`, extend CORS, update scheduler.py, add tests. Old `frontend_base_url` paths still exist (will be removed in phase 4 cleanup of frontend/app).

### Phase 3 — Move + duplicate (additive)
`git mv` candidate code from `frontend/app` to `frontend/session`. Duplicate the 6 shared primitives + `public/projectx-logo.svg`. Both apps now have the candidate code; frontend/app still works.

Manual smoke: hit a real `/interview/{token}` URL in `frontend/session` end-to-end (pre-check → OTP → camera/mic → LiveKit join). This is the gate before phase 4.

### Phase 4 — Cleanup `frontend/app` (breaking cut)
Delete moved files + dead code (`use-start-session.ts`); strip `proxy.ts` `/interview` branch; remove deps from `package.json`; drop registries from `components.json`. Run grep gauntlet (every command returns zero matches). Build + test green. Bundle size delta recorded.

### Phase 5 — Documentation
Update the four CLAUDE.md files + AGENTS.md per § Documentation Updates. Verify no candidate references remain in `frontend/app` docs. Document the deferred Sentry + CI requirements in root CLAUDE.md so the gaps are explicit.

### Phase 6 — Final verification
Run all four services together. End-to-end manual smoke: recruiter sends invite → email link is `localhost:3002/interview/<jti>` → candidate completes session → CompletionScreen renders. Lighthouse on candidate pre-check, coverage report excerpt in PR description, manual run of the dependency-check greps from § Documentation Updates.

### Deploy ordering (production)

1. Set `CANDIDATE_SESSION_BASE_URL` in backend env.
2. Deploy backend.
3. Deploy `frontend/session`.
4. Smoke test against staging.
5. Deploy `frontend/app` (drops `/interview/*`).
6. Optional 30-day Cloudflare redirect from `app.projectx.com/interview/*` → `session.projectx.com/interview/*` for any in-flight email.

---

## Out of Scope

- **Sentry installation.** No Sentry in the codebase today (despite CLAUDE.md aspirational mentions). Wiring Sentry across all three frontend apps is a separate PR. The CLAUDE.md updates here document what the candidate-session DSN MUST do (PII scrubbing) when that work happens.
- **CI infrastructure.** No `.github/workflows/` exists. Adding `frontend/session` to a CI matrix is impossible until the matrix exists. The CLAUDE.md updates here enumerate the requirement and the dependency-check rule for whoever wires CI later.
- **Dockerfile digest pinning.** All three Dockerfiles use unpinned `node:22-alpine`. Pinning is an enterprise improvement that touches all three apps consistently — separate PR.
- **E2E tests (Playwright).** Candidate flow has no E2E coverage today; adding it is its own project.
- **Visual regression testing.**
- **Shared workspace package for px primitives.** Duplication is the deliberate choice; revisit only if drift becomes a real problem.
- **Removing the shadcn → px token mapping from `frontend/app/app/globals.css`.** Harmless without consumers; removing it intersects with the px palette block — safer to leave as a follow-up.
- **Schema migrations.** None required.
- **Threat-model rewrite.** Trust boundary unchanged (single-use HS256 in URL, atomic consume). One-line note in the existing entry is appropriate; no STRIDE pass needed.
- **Ceipal / ATS work.**
- **Phase 3D scoring/reporting work.**

---

## Risks & Mitigations

### Risk: production candidate emails point at the wrong origin
Mitigation: deploy ordering step 1 sets `CANDIDATE_SESSION_BASE_URL` before backend deploys. Phase 6 step 2 smoke test verifies the rendered link in staging before production cutover. Anti-regression grep blocks any new `frontend_base_url + /interview` pairing.

### Risk: a duplicated primitive drifts between apps without anyone noticing
Mitigation: drift discipline checklist in both CLAUDE.md files. Code review gate. The duplicated set is intentionally small (3 px components + 2 lib files + token mapping) so accidental drift is visible.

### Risk: a recruiter-side dependency creeps back into `frontend/session` (or vice versa)
Mitigation: forbidden-deps lists enumerated in both CLAUDE.md files. Pre-merge `grep livekit frontend/app/package.json` and `grep @supabase frontend/session/package.json` are documented gates (in the build-sequence verification table and in both CLAUDE.md files). CI rule when CI lands.

### Risk: candidate JWT logged or sent to Sentry (when Sentry lands)
Mitigation: Sentry is OUT OF SCOPE for this PR. The new `frontend/session/CLAUDE.md` documents the requirement that when Sentry is wired, the candidate-session DSN's `beforeSend` MUST scrub `/interview/[^/]+` from URL/breadcrumb/stack and drop events with raw JWT pattern matches. Backend `CLAUDE.md` already adds the `jti_prefix=<first 8>` discipline rule for backend logs.

### Risk: `next.config.ts` security headers loosened during a debugging session and not restored
Mitigation: Phase 1 verification gate is a `curl -I` assertion that every header is set. Phase 6 final smoke re-runs the same check. Human Review Required for any change to `next.config.ts headers()` (documented in `frontend/session/CLAUDE.md`).

### Risk: bundle size of `frontend/session` is larger than expected
Mitigation: bundle budget in CLAUDE.md (< 180 KB pre-LiveKit; LiveKit-bearing routes exempt but lazy-loaded). `@next/bundle-analyzer` wired but off by default for periodic audits.

### Risk: phase 4 cleanup deletes something that turns out to still be referenced
Mitigation: phases 1–3 are additive — both apps work in parallel after phase 3. Phase 4 starts from a known-good state. The verification grep gauntlet catches stragglers before the build gate. Rollback is a single-commit `git revert`.

---

## Verification Gates Summary

| Gate | What must hold | Phase |
|---|---|---|
| Baseline build | All three frontends + backend green on `main` | 0 |
| Session app boots | `npm run build && npm run test && curl /healthz` green | 1 |
| Security headers set | `curl -I http://localhost:3002/` shows every header from § Security headers | 1 |
| Backend tests green | `pytest -k "config or scheduler"` | 2 |
| No `frontend_base_url + /interview` | grep returns zero matches | 2 |
| Session app coverage | 100% branch on `candidate-session.ts`, OtpStep, app.tsx | 3 |
| Manual end-to-end smoke | Real LiveKit room joined from `frontend/session` | 3 |
| Cleanup grep gauntlet | All five greps return zero matches | 4 |
| frontend/app build green | `lint && type-check && test && build` | 4 |
| Bundle size delta | Measurable reduction recorded in PR description | 4 |
| Docs free of candidate refs | `grep -ri "livekit\|interview\|wizardshell\|agents-ui\|ai-elements\|candidate-session"` in `frontend/app/CLAUDE.md` returns 0 (other than one-line pointer to `frontend/session/`) | 5 |
| Manual dep-check greps | `grep livekit frontend/app/package.json` and `grep @supabase frontend/session/package.json` both return 0 | 5 |
| Final smoke | Recruiter → invite → candidate → CompletionScreen | 6 |

---

## References

- Conversation log of brainstorming session (2026-05-01)
- `frontend/app/CLAUDE.md` — current recruiter-app rules
- `backend/nexus/CLAUDE.md` — current backend rules
- `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` — Phase 3 (engine merge into Nexus, candidate UI shipped to `frontend/app`)
- `frontend/admin/` — sibling app pattern reference
