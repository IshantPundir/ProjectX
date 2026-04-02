@AGENTS.md

# ProjectX — Frontend (App)
## Claude Code Context (Frontend)

> Read the root `CLAUDE.md` first. This file contains frontend-specific rules that extend it.

---

## What This Surface Is

The Next.js app serves **two distinct user surfaces** within a single codebase:

1. **Dashboard** — Recruiter, Hiring Manager, Interviewer, Admin. Configure pipelines, review candidates, manage reports, run the kanban board, join live sessions as a human participant.
2. **Candidate Interview UI** — Candidate-facing. Branded, JWT-accessed (no login). Camera + mic required. 2×2 video grid. Pre-check flow → live session → completion screen.

Both surfaces must be designed as **enterprise products**, not consumer apps. Clients are Fortune 500 companies. The UI represents their brand to candidates.

---

## Tech Stack

- **Framework:** Next.js 14+ with App Router
- **Language:** TypeScript (strict mode — `"strict": true` in tsconfig)
- **Styling:** Tailwind CSS (utility-first — no custom CSS unless strictly necessary)
- **Component library:** shadcn/ui (primitives only — extend, don't override)
- **State management:** Zustand for client-side global state; React Query (TanStack Query) for server state and cache
- **Real-time / WebRTC:** LiveKit React SDK (`@livekit/components-react`)
- **Forms:** React Hook Form + Zod validation
- **HTTP client:** `fetch` or Axios — all calls go to Nexus (FastAPI backend). Never call Supabase directly from the frontend.
- **Auth:** Supabase Auth client for dashboard users. Candidate access is token-based (no auth library needed on that surface).
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate + CloudFront (same container, different target)

---

## Directory Structure

```
frontend/app/
├── app/                          ← Next.js App Router
│   ├── (dashboard)/              ← Route group — dashboard surface
│   │   ├── layout.tsx            ← Auth guard, sidebar, nav
│   │   ├── jobs/                 ← Job pipeline management
│   │   ├── candidates/           ← Candidate cards, kanban board
│   │   ├── sessions/             ← Live session management
│   │   ├── reports/              ← Evaluation report viewer
│   │   ├── settings/             ← Company settings, ATS config, team management
│   │   └── onboarding/           ← Company onboarding wizard (5-step)
│   ├── (interview)/              ← Route group — candidate interview surface
│   │   ├── layout.tsx            ← Minimal layout, no nav
│   │   ├── [token]/              ← JWT-gated entry point
│   │   │   ├── pre-check/        ← Camera/mic test, identity confirm, OTP
│   │   │   ├── session/          ← Live interview (2×2 video grid)
│   │   │   └── complete/         ← Post-session completion screen
│   ├── (auth)/                   ← Login, SSO callback, invite acceptance
│   ├── layout.tsx                ← Root layout
│   └── globals.css               ← Tailwind base only
├── components/
│   ├── ui/                       ← shadcn/ui primitives (auto-generated, don't edit)
│   ├── dashboard/                ← Dashboard-specific composite components
│   ├── interview/                ← Candidate session components
│   ├── shared/                   ← Shared across both surfaces
│   └── copilot/                  ← AI Copilot panel components
├── lib/
│   ├── api/                      ← Typed API client functions (all calls to Nexus)
│   ├── auth/                     ← Auth helpers, token management
│   ├── hooks/                    ← Custom React hooks
│   └── utils/                    ← Pure utility functions
├── stores/                       ← Zustand stores
├── types/                        ← Shared TypeScript types/interfaces
├── middleware.ts                 ← Route protection, auth checks
└── CLAUDE.md                     ← you are here
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

### Forms
- All forms use React Hook Form + Zod. No uncontrolled forms.
- Validation schemas defined in a co-located `schema.ts` file.
- API error messages are surfaced to the relevant field, not just a toast.

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

- **Server state** (API data, cache, loading states): TanStack Query. No Zustand for this.
- **Client-side global state** (UI state, session context, copilot buffer): Zustand.
- **Form state**: React Hook Form. Not Zustand, not useState.
- Avoid prop drilling beyond 2 levels — lift to Zustand or co-locate state in the route segment.

---

## Auth Flow

### Dashboard Users (Supabase Auth)
- SSO via Google Workspace or Microsoft (OAuth2).
- Magic link / work email as fallback.
- SAML SSO (Okta, Azure AD) available on Supabase Pro+.
- Auth guard in `app/(dashboard)/layout.tsx` — redirect to `/login` if no valid session.
- Role (`Recruiter`, `Hiring Manager`, `Interviewer`, `Observer`, `Company Admin`) is attached to the JWT claims and enforced at the backend. The frontend uses role to conditionally render UI elements — but **never as the sole access control**.

### Candidates (Token-Based — No Supabase Auth)
- Candidate enters via a JWT-signed scheduling link (72-hour expiry).
- OTP verification (configurable per JD) is the pre-session gate.
- No account creation. No password. No persistent session.
- Route: `app/(interview)/[token]/` — the token is in the URL path.
- Token is verified by Nexus on every API call from the candidate session.

---

## API Client (`lib/api/`)

- All API calls are typed. Response shapes defined in `types/`.
- Base URL from environment variable (`NEXT_PUBLIC_API_URL` — this URL is public by design).
- Auth header injected automatically from the current Supabase session token.
- Candidate session calls use the candidate JWT from the URL token — not Supabase.
- Error responses from Nexus follow a consistent shape — handle them uniformly in the API client layer, surface meaningful messages to the UI.

```typescript
// Example pattern — enforce this consistently
import { api } from '@/lib/api'

// Dashboard
const jobs = await api.jobs.list()
const report = await api.reports.get(sessionId)

// Candidate session
const session = await api.candidateSession.start(token)
```

---

## Tailwind Standards

- Use Tailwind utility classes. Do not write custom CSS unless a utility genuinely does not exist.
- Spacing: use the Tailwind spacing scale. Do not use arbitrary values (e.g., `mt-[17px]`) unless absolutely necessary for pixel-perfect requirements.
- Colours: use the design system tokens defined in `tailwind.config.ts`. Do not use raw colour values (e.g., `text-[#4A90E2]`).
- Dark mode: not in scope for MVP. Do not build dark mode variants.
- Responsive: dashboard is desktop-first (1280px minimum viewport target). Candidate interview UI must work on any device — candidates may join from mobile.

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
npm run test         # Vitest unit tests
```

CI will fail if `lint` or `type-check` have errors. Fix before pushing.

---

## Human Review Required For

- Any change to `middleware.ts` (route protection logic)
- Any change to auth token handling in `lib/auth/`
- Any component in `app/(interview)/` that touches the session state or pre-check flow
- Any change to the Borderline candidate display or advancement logic
- Any change to how candidate consent is captured and surfaced

---