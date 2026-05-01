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
- Two-layer setup:
  - **Static headers** (HSTS, X-Frame-Options DENY, X-Content-Type-Options
    nosniff, Referrer-Policy: no-referrer, Permissions-Policy, COOP, CORP)
    live in `next.config.ts` `headers()`.
  - **Content-Security-Policy** lives in `proxy.ts` because it requires
    a per-request nonce (`'nonce-${nonce}' 'strict-dynamic'`) — Next.js
    emits inline bootstrap scripts that a static `script-src 'self'`
    would block. Per Next 16 CSP guide.
- Dev mode CSP includes `'unsafe-eval'` (React debugger) and
  `'unsafe-inline'` style fallback (Tailwind runtime). Production drops
  both — strict nonce only.
- Loosening any header requires a threat-model update in `docs/security/`.
- The root layout sets `export const dynamic = "force-dynamic"` so every
  route is dynamically rendered (required for nonce injection).

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
- The CSP in `proxy.ts` is the boundary contract. Adding a new
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

- Any change to `proxy.ts` (CSP nonce + connect-src origins)
- Any change to `next.config.ts` `headers()` (static security headers)
- Any change to `lib/api/candidate-session.ts` (sole API surface)
- Any new `Authorization` header sent from the candidate surface
- Any change that adds a third-party origin to CSP `connect-src`
- Any change to OTP, consent, or camera/mic step flow

---
