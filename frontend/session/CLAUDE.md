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

- Next.js 16 (16.2.x) App Router, React 19, TypeScript strict mode
- Tailwind v4 (in-house tokens duplicated from `frontend/app`)
- LiveKit (`livekit-client` + `@livekit/components-react` + `@livekit/components-styles`)
  — lazy-loaded inside `WizardShell.tsx` so pre-check steps do not pull the SDK
- **`@mediapipe/tasks-vision`** — client-side face/head-pose detection for the
  proctoring deterrent (model + WASM served from `public/mediapipe/`). Load-bearing.
- TanStack Query v5 for the candidate-session endpoints (8 — see API Client)
- React Hook Form + Zod for consent + OTP forms
- `sonner` for toasts; `motion` for transitions; `streamdown` for streamed text
- Vitest + Testing Library + jsdom for tests

**Forbidden dependencies** (pre-merge grep gate; CI gate when CI lands):
`@supabase/*`, `@dnd-kit/*`, `gsap`, `@dagrejs/dagre`, `cmdk`,
`embla-carousel-react`, `media-chrome`, `@microsoft/fetch-event-source`,
`ai`. Adding any of these requires a justification in the PR description.
(Currently-present deps NOT on the ban list but worth knowing: `@mediapipe/tasks-vision`,
`motion`, `radix-ui`, `streamdown`, `use-stick-to-bottom`, `lucide-react`.)

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
├── app-config.ts                ← Static branding/visualizer config (from LiveKit starter template): title, logo, accent, audio-visualizer tuning (default type 'aura')
├── app/
│   ├── layout.tsx               ← Root layout (fonts + px theme attrs + InterviewProviders; export const dynamic = "force-dynamic")
│   ├── page.tsx                 ← Friendly landing for accidental root visits
│   ├── not-found.tsx
│   ├── globals.css              ← Duplicated px tokens + shadcn → px mapping
│   ├── healthz/route.ts         ← Health probe
│   └── interview/[token]/
│       ├── page.tsx             ← Wizard host
│       ├── WizardShell.tsx      ← Lazy-loads the live session shell via next/dynamic
│       ├── WizardFrame.tsx / WizardStepper.tsx ← Wizard chrome
│       ├── WelcomeStep.tsx / ConsentStep.tsx / OtpStep.tsx / CameraMicStep.tsx
│       ├── sampleNoiseFloorDbfs.ts ← Mic noise-floor probe
│       └── error/page.tsx       ← Token error landing
├── components/
│   ├── px/                      ← Duplicated primitives (Button, Input, Toaster)
│   ├── ui/                      ← shadcn-style ui primitives (now minimal — button.tsx)
│   ├── agents-ui/               ← Aura/shader audio-visualizer enclave (aura, react-shader-toy, animated-background, agent-session-provider, start-audio-button) — NO control bar / transcript here
│   ├── interview/               ← Live session surface, split into app/, app/hooks/, lib/, session/ + the proctoring subsystem
│   │   └── proctoring/          ← Client proctoring (see "Candidate-side proctoring" below) incl. vision/ (MediaPipe)
│   └── DevtoolsShield.tsx       ← Devtools-open deterrent overlay
├── hooks/
│   ├── agents-ui/use-agent-audio-visualizer-aura.ts
│   ├── use-agent-state.ts
│   └── use-prefers-reduced-motion.ts
├── lib/
│   ├── env.ts                   ← Zod env validator
│   ├── utils.ts                 ← Duplicated cn helper
│   ├── api/
│   │   ├── candidate-session.ts ← 8 endpoints under /api/candidate-session/{token}/
│   │   ├── audio-hints.ts       ← toAudioCaptureOptions (snake_case → camelCase)
│   │   ├── client.ts
│   │   └── errors.ts            ← Duplicated error narrowing
│   └── hooks/
│       └── use-{candidate-session,consent,request-otp,verify-otp}.ts
├── public/
│   ├── projectx-logo.svg        ← Duplicated brand asset
│   └── mediapipe/               ← face_landmarker.task model + wasm/ (MediaPipe vision runtime for client proctoring)
└── tests/
    ├── setup.ts                 ← jsdom polyfills + getUserMedia mock
    ├── _utils/render.tsx        ← Test render harness with QueryClient
    ├── lib/{env.test.ts, api/candidate-session.test.ts}
    └── components/interview/    ← component tests
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

Eight endpoints, all under `/api/candidate-session/{token}/*`:

| Method | Path | Purpose |
|---|---|---|
| GET  | `/pre-check` | Initial state load (company, stage, consent text, OTP requirement, `proctoring_enabled`/`proctoring_outcome`) |
| POST | `/consent` | Capture consent + signature |
| POST | `/request-otp` | Send OTP via email/SMS |
| POST | `/verify-otp` | Verify the 6-digit code |
| POST | `/start` | Atomic-consume token, mint LiveKit creds, dispatch agent (returns a `proctoring: ProctoringConfig` block + `audio_processing_hints`) |
| POST | `/rejoin` | Mid-session reconnect (for active sessions) |
| GET  | `/state` | Minimal post-`/start` state snapshot (fallback poll, `useSessionStateFallback`); `SessionState` includes `'terminated'` |
| POST | `/proctoring/event` | Report a single proctoring violation; backend is authoritative on the soft-violation threshold + termination |

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
- Recordings use LiveKit **Auto Egress** writing to **Cloudflare R2** — no frontend
  involvement (this surface has zero recording/egress/storage code; the destination
  is a backend detail).

### Audio handling

`getUserMedia` constraints are determined at runtime from the
`audio_processing_hints` field returned by the `/start` response —
do NOT hard-code `noiseSuppression: true` or any other constraint.
Pass the hints straight into LiveKit's `AudioCaptureOptions` or
the `getUserMedia({ audio: … })` call. The
`lib/api/audio-hints.ts::toAudioCaptureOptions` helper does the
snake_case → camelCase rename.

**Contract** (server is source of truth, values are constant in production):

| Constraint | Value |
|---|---|
| `noiseSuppression` | **true** (no server-side NC; the browser does light NS) |
| `echoCancellation` | true (load-bearing for full-duplex barge-in) |
| `autoGainControl` | true |

There is no server-side noise cancellation (ai-coustics was removed; barge-in
is VAD-mode and the engine VAD is Silero). The helper still abstracts the server
contract for resilience — always read from the `/start` response, never hard-code
these values. See root CLAUDE.md → "Audio Path" and the spec
`docs/superpowers/specs/2026-06-04-self-hosted-audio-turn-taking-design.md`
(supersedes the audio path of `2026-05-06-audio-pipeline-design.md`).

`getUserMedia` (in `CameraMicStep.tsx`) is gated by the "Human Review
Required For: any change to OTP, consent, or camera/mic step flow"
rule.

---

## Candidate-side proctoring

The live session runs a **client-side proctoring deterrent** (spec
`docs/superpowers/specs/2026-05-21-candidate-session-proctoring-design.md`). It lives
under `components/interview/proctoring/` and is wired into the session via
`components/interview/app/view-controller.tsx`. The backend is authoritative — the
client only detects + reports; Nexus decides termination.

- **Controller** (`use-proctoring-controller.ts`) — classifies violations hard vs
  soft, flashes a border, POSTs each to `/api/candidate-session/{token}/proctoring/event`,
  and ends the LiveKit session via `ctx.end()` when told to.
- **Guards** (hooks): `use-devtools-guard`, `use-focus-guard`, `use-fullscreen-guard`,
  `use-keyboard-guard`, `use-visibility-guard`, `use-vision-guard`. Plus the top-level
  `components/DevtoolsShield.tsx`.
- **Vision** (`proctoring/vision/`: `face-landmarker.ts`, `head-pose.ts`, `gaze.ts`,
  `reading.ts`) — uses `@mediapipe/tasks-vision` against the candidate's LiveKit camera
  track to flag `multiple_faces`, `face_not_visible`, `looking_away_sustained`. This is a
  **coarse head-pose-only deterrent** (iris removed); accurate gaze is deferred to the
  server-side `vision` module. Model + WASM are served from `public/mediapipe/`.
- **UI**: `ProctoringGuard`, `FocusGraceOverlay`, `FullscreenGraceOverlay`, `ViolationBorder`,
  `VisionDebugOverlay`, with `nudge-kinds.ts` / `violation-kinds.ts`.
- The candidate is informed; proctoring is gated by the backend `proctoring_enabled` flag
  (from `/pre-check` + the `/start` `proctoring` block). `SessionState` gains `'terminated'`
  when policy ends the interview mid-session.

> Proctoring is a **deterrent + signal for human review**, never an auto-reject. It is the
> client half of a two-plane system; the server half (ONNX gaze analysis) lives in the
> backend `vision` module. Any change here falls under "Human Review Required For".

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
- Any change to `components/interview/proctoring/` (client proctoring detection/reporting)

---
