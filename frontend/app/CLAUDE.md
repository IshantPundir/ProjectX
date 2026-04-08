@AGENTS.md

# ProjectX — Frontend (App)
## Claude Code Context (Frontend)

> Read the root `CLAUDE.md` first. This file contains frontend-specific rules that extend it.

---

## Next.js 16 Warning (AGENTS.md)

Per `AGENTS.md` in this directory: **this Next.js version has breaking changes from training-data Next.js**. Before writing any new route, layout, or API handler file, consult the installed docs at `node_modules/next/dist/docs/`. Don't rely on memorized App Router patterns — they may have changed.

---

## What This Surface Is

The Next.js app serves **two distinct user surfaces** within a single codebase:

1. **Dashboard** — Recruiter, Hiring Manager, Interviewer, Admin. Configure pipelines, review candidates, manage reports, run the kanban board, join live sessions as a human participant.
2. **Candidate Interview UI** — Candidate-facing. Branded, JWT-accessed (no login). Camera + mic required. 2×2 video grid. Pre-check flow → live session → completion screen.

Both surfaces must be designed as **enterprise products**, not consumer apps. Clients are Fortune 500 companies. The UI represents their brand to candidates.

---

## Tech Stack

### Currently Installed (Phase 1)

- **Framework:** Next.js 16.2.2 with App Router
- **Language:** TypeScript (strict mode — `"strict": true` in tsconfig)
- **Styling:** Tailwind CSS v4 (utility-first — no custom CSS unless strictly necessary)
- **Auth:** @supabase/ssr v0.10 (cookie-based SSR sessions) + @supabase/supabase-js
- **HTTP client:** `apiFetch` wrapper in `lib/api/client.ts` — typed fetch, all calls go to Nexus
- **State management:** Local `useState` + `useEffect` (no global state library yet)
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate + CloudFront (same container, different target)

### Currently Installed (Phase 2A)

- **Component library:** shadcn/ui v4.2.0 (`base-nova` preset, **Base UI** not Radix — see ecosystem note below)
- **Server state:** TanStack Query v5 (`@tanstack/react-query` + devtools, provider lives in `DashboardProviders` client boundary inside the server dashboard layout)
- **Forms:** React Hook Form + Zod (`@hookform/resolvers/zod`)
- **SSE client:** `@microsoft/fetch-event-source` (used for the JD status stream)
- **Toast:** `sonner` (mounted via `<Toaster />` in `DashboardProviders`)

**⚠️ shadcn v4 / Base UI ecosystem note:** shadcn v4 switched from Radix primitives to `@base-ui/react`. Writing custom components that extend shadcn primitives requires Base UI idioms:
- `TooltipTrigger` uses `render={<span>...</span>}` instead of Radix's `asChild`
- `TooltipProvider` uses `delay={150}` instead of Radix's `delayDuration`
- `Select`'s `onValueChange` types its value as `unknown` (Zod validation catches invalid shapes)
- `SelectTrigger` defaults to `w-fit` — add `w-full` explicitly when you need it to fill a grid column

Don't blindly copy Radix patterns from the internet; check the actual component source in `components/ui/` before adapting anything.

### Planned for Phase 2+

- **State management:** Zustand for client-side global state (deferred from Phase 2A)
- **Real-time / WebRTC:** LiveKit React SDK (`@livekit/components-react`) — Phase 3

---

## Directory Structure

### Current (Phase 1)

```
frontend/app/
├── app/                              ← Next.js App Router
│   ├── layout.tsx                    ← Root layout (Geist fonts, zinc-50 bg)
│   ├── globals.css                   ← Tailwind v4 import only
│   ├── (auth)/
│   │   ├── layout.tsx                ← Centered card container
│   │   ├── login/page.tsx            ← Email+password + JWT tenant_id check
│   │   └── invite/page.tsx           ← Invite acceptance + account setup
│   ├── onboarding/
│   │   ├── layout.tsx                ← Centered full-viewport (no sidebar)
│   │   └── page.tsx                  ← 2-step onboarding wizard
│   └── (dashboard)/
│       ├── layout.tsx                ← Server component: auth guard + /me check + sidebar shell
│       ├── SidebarNav.tsx            ← Client component: nav links + sign out
│       ├── page.tsx                  ← Dashboard home (placeholder cards)
│       ├── profile/page.tsx          ← User profile + role assignments
│       └── settings/
│           ├── team/page.tsx         ← Team management, invites, resend, revoke, deactivate
│           └── org-units/
│               ├── page.tsx          ← Org unit tree + create form
│               └── [unitId]/page.tsx ← Unit detail: members, roles, sub-units, delete
├── lib/
│   ├── api/client.ts                 ← apiFetch() utility — typed fetch wrapper
│   └── supabase/
│       ├── client.ts                 ← Browser Supabase client
│       └── server.ts                 ← Server Supabase client (cookies)
└── CLAUDE.md                         ← you are here
```

### Added in Phase 2A

```
├── app/
│   └── (dashboard)/
│       └── jobs/                         ← Job pipeline management (Phase 2A)
│           ├── page.tsx                  ← Jobs list
│           └── [jobId]/
│               └── review/page.tsx       ← Three-panel JD review (server component shell)
├── components/
│   ├── ui/                               ← shadcn primitives — auto-generated, do not edit
│   ├── dashboard/
│   │   ├── providers.tsx                 ← DashboardProviders client boundary (TanStack Query + Toaster)
│   │   ├── company-profile-form.tsx      ← Shared 4-field RHF+Zod form
│   │   └── jd-panels/
│   │       ├── SignalChip.tsx            ← Provenance-aware chip with inference tooltip
│   │       ├── OriginalJdPanel.tsx       ← Collapses to drawer below 3xl
│   │       ├── EnrichedJdPanel.tsx
│   │       ├── SignalsPanel.tsx
│   │       ├── LoadingSkeleton.tsx       ← Content-aware skeleton with SSE status pill
│   │       └── ErrorBanner.tsx          ← Retry button + sanitized error message
├── lib/
│   ├── api/
│   │   ├── client.ts                     ← existing apiFetch()
│   │   └── jobs.ts                       ← NEW typed API namespace for JD module
│   ├── auth/
│   │   └── tokens.ts                     ← getFreshSupabaseToken() — no in-memory cache layer
│   └── hooks/
│       ├── use-job.ts                    ← TanStack Query wrapper for GET /api/jobs/{id}
│       └── use-job-status-stream.ts      ← fetch-event-source SSE with query invalidation
```

### Planned Additions (Phase 3+)

```
├── app/
│   ├── (dashboard)/
│   │   ├── candidates/           ← Candidate cards, kanban board
│   │   ├── sessions/             ← Live session management
│   │   └── reports/              ← Evaluation report viewer
│   └── (interview)/              ← Route group — candidate interview surface
│       ├── layout.tsx            ← Minimal layout, no nav
│       └── [token]/              ← JWT-gated entry point
│           ├── pre-check/        ← Camera/mic test, identity confirm, OTP
│           ├── session/          ← Live interview (2×2 video grid)
│           └── complete/         ← Post-session completion screen
├── components/
│   ├── interview/                ← Candidate session components
│   ├── shared/                   ← Shared across both surfaces
│   └── copilot/                  ← AI Copilot panel components
├── stores/                       ← Zustand stores
├── types/                        ← Shared TypeScript types/interfaces
└── middleware.ts                 ← Route protection, auth checks
```

---

## Absolute Rules

### Never Call Supabase Directly from the Frontend
All data access goes through the FastAPI backend (Nexus). No direct Supabase client queries in page or component code.

```typescript
// CORRECT — go through Nexus
const jobs = await api.jobs.list()

// WRONG — bypasses FastAPI, bypasses RBAC, bypasses RLS context
const { data } = await supabase.from('jobs').select('*')
```

The Supabase client on the frontend is used **only** for Auth (session management, SSO redirects). Nothing else.

### TypeScript Strict Mode
- `"strict": true` in `tsconfig.json` — no exceptions.
- No `any` types. Use `unknown` + type narrowing if the shape is truly unknown.
- All API response types must be explicitly typed. Co-locate types with their API call in `lib/api/`.

### Component Placement Rules

| Component type | Location |
|---|---|
| shadcn/ui primitives | `components/ui/` (auto-generated — do not edit) |
| Dashboard composite components | `components/dashboard/` |
| Candidate session components | `components/interview/` |
| AI Copilot panel | `components/copilot/` |
| Shared across both surfaces | `components/shared/` |
| New dashboard page sections | Inside the relevant `app/(dashboard)/` route folder |
| New interview page sections | Inside the relevant `app/(interview)/` route folder |

Do not drop components at the root of `components/` without a subdirectory.

**Current state (Phase 2A):** `components/ui/` and `components/dashboard/` are live. `components/interview/`, `components/copilot/`, `components/shared/` are Phase 3+.

### Forms

- All forms must use React Hook Form + Zod (`@hookform/resolvers/zod`). No uncontrolled forms.
- Validation schemas defined in a co-located `schema.ts` file.
- API error messages are surfaced to the relevant field, not just a toast.

**Current state (Phase 2A):** React Hook Form + Zod are installed. Phase 1 pages still use raw `useState` — migrate them when touching those pages.

### Secrets
- **Never put API keys, secrets, or tokens in client-side code or environment variables prefixed with `NEXT_PUBLIC_`** unless that value is genuinely intended to be public (e.g., a LiveKit server URL).
- Sensitive operations (e.g., ATS credential storage) go through the backend — never touch the frontend.

---

## Two Surfaces — Design Constraints

### Dashboard Surface
- Enterprise SaaS aesthetic — clean, data-dense, professional.
- Sidebar navigation. Persistent across all dashboard routes.
- Real-time kanban board (candidate pipeline) updates via WebSocket or polling.
- Borderline candidates display a clear visual indicator and cannot be advanced/rejected without explicit action.
- The recruiter's daily action items dashboard must be the default landing view post-login.

### Candidate Interview Surface
- Minimal UI. Candidate should not be confused or distracted.
- Branded — company name, logo, and configured bot tone/name from the job setup.
- No navigation. No sidebar. Full-viewport video experience.
- **2×2 video grid layout:** candidate (camera on), AI bot tile (avatar/no camera), human participant tiles if present, empty slot.
- Session progress indicator: "Q3 of 9 · 11 min remaining" — always visible.
- Pre-check flow is blocking — camera test, mic test, identity confirm, OTP verification must all pass before the session begins.
- Camera and microphone are required throughout. If either is lost mid-session, surface a clear blocking error.

### AI Copilot Panel (`components/copilot/`)
- Renders automatically for any human (non-candidate) in a session — never toggled off.
- Shows: live transcript with speaker labels, real-time signal cards per exchange, bot's next planned probe (before it fires), question coverage tracker.
- This panel must be visually distinct from the main video grid — secondary panel, not overlaid.

---

## LiveKit Integration

- Use `@livekit/components-react` for all WebRTC session UI.
- Never implement raw WebRTC — use LiveKit abstractions.
- LiveKit token is provisioned by Nexus (`/api/sessions/{id}/token`). Never generate LiveKit tokens on the frontend.
- Recordings: LiveKit Egress writes to S3 — no frontend involvement. Recordings are accessed via pre-signed URLs from Nexus.

---

## State Management

### Current (Phase 1)
- All state is local `useState` + `useEffect` with manual fetch patterns
- Server-side data: `React.cache()` used for `/api/auth/me` in dashboard layout (deduplicates across render tree)
- Token fetched fresh from `supabase.auth.getSession()` before each API call — no cached auth state
- No global state library installed

### Current (Phase 2A)
- **Server state** (API data, cache, loading states): TanStack Query v5. No Zustand for this.
- **Form state**: React Hook Form + Zod. Not Zustand, not useState.
- `DashboardProviders` client boundary wraps the server dashboard layout and mounts `QueryClientProvider`, `<Toaster />`, and `ReactQueryDevtools` (dev only).
- Avoid prop drilling beyond 2 levels — co-locate state in the route segment or use TanStack Query cache.

### Target (Phase 3+)
- **Client-side global state** (UI state, session context, copilot buffer): Zustand (not yet installed).

---

## Auth Flow

### Dashboard Users (Supabase Auth)
- **MVP:** Email + password only (no OAuth, no magic link).
- OAuth (Google, Microsoft) and SAML SSO (Okta, Azure AD) are additive for later phases.
- Auth guard in `app/(dashboard)/layout.tsx` — **server component** that calls `supabase.auth.getUser()` and redirects to `/login` if no valid session.
- On login, the frontend manually decodes the JWT (via `atob()`) to check for `tenant_id`. Rejects ProjectX admin-only accounts from the client dashboard.
- `/api/auth/me` response (fetched server-side via `React.cache()`) drives the onboarding redirect: `is_super_admin && !onboarding_complete → /onboarding`.
- Roles are NOT in the JWT. They are fetched per-request from the database. The frontend uses `is_super_admin` and `assignments` from `/api/auth/me` for conditional UI rendering — **never as the sole access control**.

### Candidates (Token-Based — No Supabase Auth) [Phase 2+]
- Candidate enters via a JWT-signed scheduling link (72-hour expiry).
- OTP verification (configurable per JD) is the pre-session gate.
- No account creation. No password. No persistent session.
- Route: `app/(interview)/[token]/` — the token is in the URL path.
- Token is verified by Nexus on every API call from the candidate session.

---

## API Client (`lib/api/client.ts`)

### Current Implementation
- Single `apiFetch<T>()` function — generic typed `fetch` wrapper
- Base URL from `NEXT_PUBLIC_API_URL` (defaults to `http://127.0.0.1:8000`)
- Token passed explicitly per call (not auto-injected)
- Handles FastAPI error shape: parses `{ detail: string }` from non-OK responses
- Response types defined inline per-page (no shared `types/` directory yet)

```typescript
// Current pattern — used throughout Phase 1
import { apiFetch } from '@/lib/api/client'

const me = await apiFetch<MeData>('/api/auth/me', { token })
const members = await apiFetch<TeamMember[]>('/api/settings/team/members', { token })
```

### Current (Phase 2A)
- `lib/api/jobs.ts` is the first typed API namespace — use it as the pattern for new modules.
- `lib/auth/tokens.ts` exports `getFreshSupabaseToken()` — call this instead of inline `getSession()` calls when building new hooks or API calls.
- Auth header still passed explicitly per call (not auto-injected); TanStack Query hooks handle this via `getFreshSupabaseToken()`.

### Target (Phase 3+)
- Expand to `api.candidates.*`, `api.sessions.*`, `api.reports.*` namespaces
- Shared `types/` directory for cross-module response types
- Candidate session calls use the candidate JWT from the URL token — not Supabase

---

## Tailwind Standards

- Use Tailwind utility classes. Do not write custom CSS unless a utility genuinely does not exist.
- Spacing: use the Tailwind spacing scale. Do not use arbitrary values (e.g., `mt-[17px]`) unless absolutely necessary for pixel-perfect requirements.
- Colours: use the design system tokens defined in `tailwind.config.ts`. Do not use raw colour values (e.g., `text-[#4A90E2]`).
- Dark mode: not in scope for MVP. Do not build dark mode variants.
- Responsive: dashboard is desktop-first (1280px minimum viewport target). Candidate interview UI must work on any device — candidates may join from mobile.

**Custom breakpoints:** `3xl: 1440px` added in `app/globals.css` via the `@theme` directive (Phase 2A — for the three-panel JD review layout). Tailwind v4 uses `--breakpoint-<name>` CSS variables inside `@theme`, NOT a `tailwind.config.ts` file (there isn't one).

---

## Accessibility

- All interactive elements must be keyboard-navigable.
- Use semantic HTML (`button`, `nav`, `main`, `section` etc.) — not `div` soup.
- ARIA labels on icon-only buttons and non-obvious interactive elements.
- Video grid elements must have appropriate labels for screen reader context.

---

## Dev Commands

```bash
npm run dev          # Start dev server (localhost:3000)
npm run build        # Production build (run before any PR)
npm run lint         # ESLint — must pass with zero errors
npm run type-check   # tsc --noEmit — must pass with zero errors
```

CI will fail if `lint` or `type-check` have errors. Fix before pushing.

Note: Vitest is not yet installed. Tests will be added in Phase 2+.

---

## Human Review Required For

- Any change to `middleware.ts` (route protection logic)
- Any change to auth token handling in `lib/auth/`
- Any component in `app/(interview)/` that touches the session state or pre-check flow
- Any change to the Borderline candidate display or advancement logic
- Any change to how candidate consent is captured and surfaced

---