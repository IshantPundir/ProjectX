@AGENTS.md

# ProjectX — Frontend (App)
## Claude Code Context (Frontend)

> Read the root `CLAUDE.md` first. This file contains frontend-specific rules that extend it.

---

## Next.js 16 Warning (AGENTS.md)

Per `AGENTS.md` in this directory: **this Next.js version has breaking changes from training-data Next.js**. Before writing any new route, layout, or API handler file, consult the installed docs at `node_modules/next/dist/docs/`. Don't rely on memorized App Router patterns — they may have changed.

---

## What This Surface Is

This Next.js app serves the **recruiter dashboard ONLY**. It is the surface for Recruiters, Hiring Managers, Interviewers, Admins, and Observers. Authentication is Supabase email + password; data flows through Nexus (`apiFetch` + `getFreshSupabaseToken`).

**The candidate interview surface lives in a separate app** at `frontend/session/`. Do NOT add candidate-facing routes, LiveKit code, or `@/components/{ui,agents-ui,ai-elements}/` imports to this app. The pre-merge dependency-check grep (`grep livekit package.json`) is the gate; CI rule lands later.

---

## Tech Stack

### Currently Installed (Phase 1)

- **Framework:** Next.js 16.2.2 with App Router
- **Language:** TypeScript (strict mode — `"strict": true` in tsconfig)
- **Styling:** Tailwind CSS v4 (utility-first — no custom CSS unless strictly necessary)
- **Auth:** @supabase/ssr v0.10 (cookie-based SSR sessions) + @supabase/supabase-js
- **HTTP client:** `apiFetch` wrapper in `lib/api/client.ts` — typed fetch wrapping Nexus. Throws `ApiError extends Error` with an HTTP `status` field. Consumers narrow with `err instanceof ApiError && err.status === N`.
- **Token retrieval:** `getFreshSupabaseToken()` in `lib/auth/tokens.ts`. Use this in new hooks and mutations — do not call `supabase.auth.getSession()` inline.
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate + CloudFront (same container, different target)

### Currently Installed (Phase 2A)

- **Server state:** TanStack Query v5 (`@tanstack/react-query` + devtools, provider lives in `DashboardProviders` client boundary inside the server dashboard layout)
- **Forms:** React Hook Form + Zod (`@hookform/resolvers/zod`)
- **SSE client:** `@microsoft/fetch-event-source` — used by `use-job-status-stream` and `use-questions-status-stream`. Both hooks use a ref-mirroring pattern so stage/job selection doesn't churn the underlying connection; `useJobStatusStream` also caps total reconnect attempts via `MAX_TOTAL_RETRIES` to prevent runaway loops.
- **Toast:** `sonner` (mounted via `<Toaster />` in `DashboardProviders`)
- **Testing:** Vitest + @testing-library/react + jsdom. Run via `npm run test`.

### Currently Installed (Phase 2B+)

- **Client-side global state:** Zustand v5 (`zustand`). Used for editable JD signal state in `stores/job-edit.ts` (isDirty tracking, optimistic local edits before save). Add new stores under `stores/` only when state needs to live outside a single React tree — most state should still go in TanStack Query cache or co-located component state.

### Currently Installed (Phase 2C)

- **Drag & drop:** `@dnd-kit/core` + `@dnd-kit/sortable` + `@dnd-kit/modifiers` with `KeyboardSensor` wired for a11y. Used by `PipelineFlowColumn` for stage reordering.
- **Node-link canvas:** `@dagrejs/dagre` for layout (no `@xyflow/react` — the org-unit canvas in `components/dashboard/org-units/` uses a custom SVG renderer in `OrgGraphCanvas.tsx` + `OrgUnitEdge.tsx` + `edge-path.ts`, with its own pan/zoom hook `use-pan-zoom.ts` and direction toggle `use-direction-toggle.ts`).
- **Animation:** GSAP v3 + `@gsap/react` (used sparingly for transitions; avoid for any state-driven motion that would be cleaner with Tailwind transitions).

### Component Library — In-House `px/` Primitives

The dashboard surface uses **no shadcn/ui** — the design system is a hand-rolled primitive library at `components/px/` built directly on `@base-ui-components/react`. The barrel export is `components/px/index.ts`.

| Primitive | File |
|---|---|
| `Button`, `ButtonVariant`, `ButtonSize` | `components/px/Button.tsx` |
| `Input`, `InputSize` | `components/px/Input.tsx` |
| `Textarea`, `Label` | `components/px/Textarea.tsx`, `components/px/Label.tsx` |
| `Select` family | `components/px/Select.tsx` |
| `Dialog` family + `DangerConfirmDialog` | `components/px/Dialog.tsx`, `components/px/DangerConfirmDialog.tsx` |
| `Alert`, `Badge`, `Skeleton`, `Separator` | `components/px/{Alert,Badge,Skeleton,Separator}.tsx` |
| `Tooltip` family | `components/px/Tooltip.tsx` |
| `Toaster` (sonner wrapper) | `components/px/Toaster.tsx` |

**Base UI ecosystem rules (still apply, just inside `px/` primitives):**
- `TooltipTrigger` uses `render={<span>...</span>}` instead of Radix's `asChild`
- `TooltipProvider` uses `delay={150}` instead of Radix's `delayDuration`
- `Select`'s `onValueChange` types its value as `unknown` (Zod validation catches invalid shapes)
- `SelectTrigger` defaults to `w-fit` — add `w-full` explicitly when you need it to fill a grid column

When you need a new primitive, add it under `components/px/` and export it from `index.ts` — never reach for an external shadcn snippet or copy a Radix pattern from the internet without checking the actual `@base-ui-components/react` API.

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

---

## Directory Structure

### Current

```
frontend/app/
├── app/                                  ← Next.js App Router
│   ├── layout.tsx                        ← Root layout (Geist fonts, zinc-50 bg)
│   ├── globals.css                       ← Tailwind v4 import only + @theme tokens
│   ├── (auth)/
│   │   ├── layout.tsx                    ← Centered card container
│   │   ├── login/page.tsx                ← Email+password + JWT tenant_id check
│   │   └── invite/page.tsx               ← Invite acceptance + account setup
│   ├── onboarding/
│   │   ├── layout.tsx                    ← Centered full-viewport (no sidebar)
│   │   └── page.tsx                      ← 2-step onboarding wizard
│   ├── suspended/page.tsx                ← Tenant blocked / user revoked landing
│   ├── (dashboard)/
│   │   ├── layout.tsx                    ← Server component: auth guard + React.cache(getMe) + sidebar shell
│   │   ├── page.tsx                      ← Dashboard home
│   │   ├── profile/page.tsx              ← User profile + role assignments
│   │   ├── jobs/
│   │   │   ├── page.tsx                  ← Jobs list
│   │   │   ├── new/page.tsx              ← Create JD wizard
│   │   │   └── [jobId]/
│   │   │       ├── page.tsx              ← Three-panel JD review (signals + original + enriched)
│   │   │       ├── pipeline/page.tsx     ← Per-job pipeline editor
│   │   │       └── questions/page.tsx    ← Per-stage question bank UI
│   │   ├── candidates/
│   │   │   ├── page.tsx                  ← Kanban + list view (ClientCandidatesPage shell)
│   │   │   └── [candidateId]/page.tsx    ← Candidate detail (profile / assignments / sessions)
│   │   ├── pipeline/page.tsx             ← Tenant-wide pipeline templates browser
│   │   ├── questions/page.tsx            ← Tenant-wide question bank browser (placeholder)
│   │   ├── reports/page.tsx              ← Reports landing (Phase 3D — placeholder)
│   │   └── settings/
│   │       ├── team/page.tsx             ← Team management, invites, resend, revoke, deactivate
│       └── org-units/
│           ├── page.tsx              ← Org unit infinite-canvas tree + create
│           └── [unitId]/page.tsx     ← Unit detail: members, roles, sub-units, delete
├── components/
│   ├── px/                               ← In-house design-system primitives (Button, Input, Dialog, Tooltip, …)
│   └── dashboard/
│       ├── AppShell.tsx                  ← Sidebar nav + header
│       ├── SessionGuard.tsx              ← Client-side session presence check
│       ├── AccessDenied.tsx              ← RBAC-denial fallback
│       ├── providers.tsx                 ← DashboardProviders client boundary
│       ├── company-profile-form.tsx      ← Shared 4-field RHF+Zod form
│       ├── jd-panels/                    ← JDReviewShell, JDExtractingView, RawJdCanvas, EnrichedJdCanvas, SectionsRail, SignalsCanvas, SignalInspector, ErrorBanner, helpers/, components/
│       ├── pipeline/                     ← Pipeline editor: PipelineFlowColumn, StageInspectorPanel, StageConfigDrawer, TemplatePickerDialog, StarterPackBrowser, ActivationGate, StageParticipantsEditor, etc.
│       ├── question-bank/                ← AddQuestionDialog, AddCustomQuestionDialog, BankStatusBadge, QuestionCard, QuestionRefinePanel, …
│       ├── candidates/                   ← AddCandidateDialog, CandidateKanbanView/Card/Column, CandidateListView, ClientCandidatesPage, ResumeUploadField, SendInviteDialog, JdPicker, StageTransitionDropdown, SessionStatusBadge, StatusBadge
│       └── org-units/                    ← OrgGraph + OrgGraphCanvas + custom SVG edge/node + dagre layout hook + pan-zoom + direction-toggle
├── stores/
│   └── job-edit.ts                       ← Zustand: editable signal state with isDirty tracking
├── lib/
│   ├── api/                              ← Typed API namespaces: client, jobs, candidates, pipelines, question-banks, questions, scheduler, team, org-units, auth, errors
│   ├── auth/                             ← getFreshSupabaseToken, handle-error (global 401 sink)
│   ├── hooks/                            ← 50+ TanStack Query hooks (use-jobs, use-candidates, use-banks, use-pipeline-templates, use-job-status-stream, use-questions-status-stream, …)
│   ├── pipelines/                        ← Pipeline-specific helpers (e.g. classification, stage rules)
│   ├── supabase/{client,server}.ts       ← @supabase/ssr clients (cookies / browser)
│   └── utils.ts
├── tests/                                ← Vitest + Testing Library + jsdom
│   ├── setup.ts                          ← Stubs localStorage (private-mode resilient)
│   ├── _utils/render.tsx                 ← Test render helper
│   ├── api/, auth/, components/, lib/, settings/
└── proxy.ts                              ← Next.js middleware: validates Supabase session + decodes JWT for tenant_id, gates dashboard routes
```

Candidate interview surface lives at frontend/session/. See frontend/session/CLAUDE.md.

### Pending UI work (Phase 3D)

- **`components/copilot/`** — AI Copilot panel components don't exist yet. Required for any human-in-the-loop session.
- **`components/shared/`** — directory is reserved for cross-surface components; not yet populated.
- **Reports view** — `app/(dashboard)/reports/page.tsx` is a placeholder. Pairs with backend Phase 3D (`reporting` module is still a stub).

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
| In-house design-system primitives | `components/px/` (Button, Input, Dialog, Tooltip, …) |
| Dashboard composite components | `components/dashboard/<feature>/` |
| AI Copilot panel | `components/copilot/` (not yet built) |
| Cross-surface shared components | `components/shared/` (reserved, empty) |
| Page-local components | Inside the relevant `app/(dashboard)/<route>/` folder |

Do not drop components at the root of `components/` without a subdirectory.

When extending `components/px/`, add an export to `components/px/index.ts`. Always import from the barrel (`from "@/components/px"`), not the underlying file.

### Forms

- All forms must use React Hook Form + Zod (`@hookform/resolvers/zod`). No uncontrolled forms.
- Validation schemas defined in a co-located `schema.ts` file.
- API error messages are surfaced to the relevant field, not just a toast.

**Current state (Phase 2A):** React Hook Form + Zod are installed. Phase 1 pages still use raw `useState` — migrate them when touching those pages.

### Secrets
- **Never put API keys, secrets, or tokens in client-side code or environment variables prefixed with `NEXT_PUBLIC_`** unless that value is genuinely intended to be public (e.g., the Nexus base URL).
- Sensitive operations (e.g., ATS credential storage) go through the backend — never touch the frontend.

---

## Dashboard Design Constraints

### Dashboard Surface
- Enterprise SaaS aesthetic — clean, data-dense, professional.
- Sidebar navigation. Persistent across all dashboard routes.
- Real-time kanban board (candidate pipeline) updates via WebSocket or polling.
- Borderline candidates display a clear visual indicator and cannot be advanced/rejected without explicit action.
- The recruiter's daily action items dashboard must be the default landing view post-login.

### AI Copilot Panel (`components/copilot/`)
- Renders automatically for any human (non-candidate) in a session — never toggled off.
- Shows: live transcript with speaker labels, real-time signal cards per exchange, bot's next planned probe (before it fires), question coverage tracker.
- This panel must be visually distinct from the main video grid — secondary panel, not overlaid.

---

## State Management

### Current
- **Server state** (API data, cache, loading states): TanStack Query v5. Default for anything that comes from the backend.
- **Form state**: React Hook Form + Zod. Not Zustand, not useState. Validation schemas live in a co-located `schema.ts` file when shared, or inline when single-use.
- **Client global state**: Zustand v5, used **only** when state genuinely needs to live outside a single React tree. Current footprint is intentionally small — `stores/job-edit.ts` (editable signal state with isDirty tracking) is the only store today. Do not reach for Zustand when TanStack Query or component-local state would do.
- `DashboardProviders` client boundary wraps the server dashboard layout and mounts `QueryClientProvider`, `<Toaster />`, and `ReactQueryDevtools` (dev only).
- Avoid prop drilling beyond 2 levels — co-locate state in the route segment or use TanStack Query cache.
- **Query key discipline**: list endpoints use distinct keys from their detail siblings. E.g. the jobs list uses `['jobs-list']` while `useJob(id)` uses `['jobs', id]`. Prefix matching on invalidate calls means the wrong key shape clobbers unrelated caches.

### Legacy (Phase 1 pages still pending migration)
Login, invite, and onboarding pages still use raw `useState` + `fetch`. Per convention: **migrate them when you touch them**, not as a standalone refactor.

---

## Auth Flow

### Dashboard Users (Supabase Auth)
- **MVP:** Email + password only (no OAuth, no magic link).
- OAuth (Google, Microsoft) and SAML SSO (Okta, Azure AD) are additive for later phases.
- Auth guard in `app/(dashboard)/layout.tsx` — **server component** that calls `supabase.auth.getUser()` and redirects to `/login` if no valid session.
- On login, the frontend manually decodes the JWT (via `atob()`) to check for `tenant_id`. Rejects ProjectX admin-only accounts from the client dashboard.
- `/api/auth/me` response (fetched server-side via `React.cache()`) drives the onboarding redirect: `is_super_admin && !onboarding_complete → /onboarding`.
- Roles are NOT in the JWT. They are fetched per-request from the database. The frontend uses `is_super_admin` and `assignments` from `/api/auth/me` for conditional UI rendering — **never as the sole access control**.

Candidates have no auth on this surface — they use frontend/session/.

---

## API Client (`lib/api/client.ts`)

### Implementation
- Single `apiFetch<T>()` function — generic typed `fetch` wrapper at `lib/api/client.ts`.
- Base URL from `NEXT_PUBLIC_API_URL` (defaults to `http://127.0.0.1:8000`).
- Token passed explicitly per call (not auto-injected). New hooks call `getFreshSupabaseToken()` from `lib/auth/tokens.ts` rather than inline `supabase.auth.getSession()`.
- Errors:
  - 422 → `ApiValidationError` with structured `fieldErrors[]` (FastAPI `loc` array → RHF path mapping in `lib/api/errors.ts::applyApiErrorToForm`).
  - Other non-OK → `ApiError` with `status` and `code` fields. Narrow with `err instanceof ApiError && err.status === N`.
  - Global 401 sink in `lib/auth/handle-error.ts` dedupes concurrent unauthorized responses, signs out, toasts, and redirects.

```typescript
// Canonical pattern
import { apiFetch } from '@/lib/api/client'

const me = await apiFetch<MeResponse>('/api/auth/me', { token })
const members = await apiFetch<TeamMember[]>('/api/settings/team/members', { token })
```

### Typed API namespaces (`lib/api/*`)

Each backend module has a co-located `lib/api/<module>.ts` file with response types and request helpers:

| Namespace | Covers |
|---|---|
| `auth.ts` | `/api/auth/me`, accept-invite, onboarding/complete |
| `team.ts` | `/api/settings/team/*` |
| `org-units.ts` | `/api/org-units/*` |
| `jobs.ts` | `/api/jobs/*` (signals, snapshots, status stream) |
| `pipelines.ts` | `/api/pipelines/*` + `/api/jobs/{id}/pipeline` |
| `question-banks.ts`, `questions.ts` | `/api/jobs/{id}/banks/*` and per-question CRUD |
| `candidates.ts` | `/api/candidates/*` + kanban |
| `scheduler.ts` | `/api/scheduler/*` (invite send/resend/revoke) |
| `client.ts`, `errors.ts` | Shared transport + error mapping |

When adding endpoints for a new backend module, create a new file under `lib/api/` and follow the existing pattern. Keep response types co-located with their fetcher; there is no central `types/` directory.

---

## Tailwind Standards

- Use Tailwind utility classes. Do not write custom CSS unless a utility genuinely does not exist.
- Spacing: use the Tailwind spacing scale. Do not use arbitrary values (e.g., `mt-[17px]`) unless absolutely necessary for pixel-perfect requirements.
- Colours: use the design system tokens defined in `app/globals.css` via the Tailwind v4 `@theme` directive. Do not use raw colour values (e.g., `text-[#4A90E2]`).
- Dark mode: not in scope for MVP. Do not build dark mode variants.
- Responsive: dashboard is desktop-first (1280px minimum viewport target). Candidate interview UI must work on any device — candidates may join from mobile.

**Custom breakpoints:** `3xl: 1440px` added in `app/globals.css` via the `@theme` directive (Phase 2A — for the three-panel JD review layout). Tailwind v4 uses `--breakpoint-<name>` CSS variables inside `@theme`, NOT a `tailwind.config.ts` file (there isn't one).

---

## Accessibility

- All interactive elements must be keyboard-navigable.
- Use semantic HTML (`button`, `nav`, `main`, `section` etc.) — not `div` soup.
- ARIA labels on icon-only buttons and non-obvious interactive elements.
- Video grid elements must have appropriate labels for screen reader context.
- **Dialogs and drawers must move focus on open.** Use a `ref` + `useEffect(() => { if (open) ref.current?.focus() }, [open])` pattern. Both `StageConfigDrawer` (focuses the name input) and `TemplatePickerDialog` (focuses the close button — templates load async so focusing a card would race) follow this. WCAG 2.4.3.
- **Drag-and-drop needs a keyboard alternative.** `@dnd-kit` components wire `KeyboardSensor` with `sortableKeyboardCoordinates`.

---

## Security

> Cross-cutting standards (rate limiting, supply chain, secrets rotation, logging/PII, audit, code review, incident response) are defined **once in the root `CLAUDE.md` → Enterprise Operating Standards**. The rules below are frontend-specific implementation details on top of those.

- **Security headers** are set in `next.config.ts` `headers()`: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Content-Security-Policy** is a planned follow-up (nonce-based) and tracked as a hard requirement before GA. When wired, the CSP must forbid `unsafe-eval`, allow `https://api.projectx.com` explicitly, and use a per-request nonce on inline scripts. No `unsafe-inline` for scripts.
- **Post-auth redirects must be allowlisted.** Any `router.push(urlFromBackend)` where the URL is controlled by a mutation response must validate that the value starts with `/` (and does not start with `//`) before navigating. Without this, a compromised or MITM'd response creates an open redirect. The invite completion flow in `app/(auth)/invite/page.tsx` does this; any new post-auth redirect must follow suit.
- **No `dangerouslySetInnerHTML`** for backend-returned strings. Render as text content with `whitespace-pre-wrap` instead.
- **No `localStorage` for auth tokens.** Only non-sensitive UI preferences live in localStorage (e.g. `pipeline-inspector-tab`).
- **No direct Supabase data calls from the frontend** (`supabase.from(...).select(...)`). The only Supabase client usage is auth/session management. All data goes through `apiFetch` to Nexus.

### Browser-Side PII & Telemetry

- **No raw PII in browser logs or Sentry events.** Forbidden in `console.*`, in Sentry breadcrumbs, and in any third-party analytics: candidate emails, resume contents, full JWT bearer values, OTP codes, transcripts.
- Sentry's `beforeSend` strips request bodies and headers by default; do not whitelist `Authorization` or `Cookie` headers back in.
- Analytics on the **dashboard surface** is allowlisted to a single endpoint (Sentry), not arbitrary third parties. Adding a new telemetry destination requires a threat-model update.

### Form & Input Hygiene

- Server is the source of truth for validation. Client Zod schemas are for UX speed, not security.
- 422 responses surface field errors via `applyApiErrorToForm` — never silently swallow.
- File uploads (resumes) go to S3 via pre-signed URL from Nexus. Never POST file bodies through the dashboard origin.

---

## Production Operating Rules

- **Lockfile is authoritative.** `package-lock.json` is committed; `npm ci` is the only install command CI runs. Mismatched lockfile blocks merge.
- **`npm audit --omit=dev`** runs on every PR. Critical CVE blocks merge; high CVE blocks merge unless waived in the PR description with a CVE exception note.
- **Bundle budget per route**: dashboard pages target < 250 KB gzipped first-load JS.
- **Performance targets** (Lighthouse on production build): LCP < 2.5s on dashboard; TTI < 3.5s; CLS < 0.1. Regressions block merge.
- **Error boundaries** are required around every top-level route segment. The boundary surfaces a recovery action, never a silent blank screen.
- **Hydration discipline**: no `Date.now()`, `Math.random()`, or `window` access during render. Use `useEffect` for client-only side effects.
- **Suspense + loading.tsx** for every async route segment. No raw `<Spinner>` placeholder pages.

---

## Dev Commands

```bash
npm run dev          # Start dev server (localhost:3000)
npm run build        # Production build (run before any PR)
npm run lint         # ESLint — must pass with zero errors
npm run type-check   # tsc --noEmit — must pass with zero errors
npm run test         # Vitest — must pass with zero failures
```

CI will fail if `lint`, `type-check`, or `test` have errors. Fix before pushing.

The Vitest suite (~30 files under `tests/`) covers API client error mapping, form error mapping, key components (BankStatusBadge, OrgGraph, OrgUnitNode, QuestionCard, SendInviteDialog, OtpStep, DangerConfirmDialog, UnitTypeStyle, EdgePath, UseDirectionToggle, UseDagreLayout, UsePanZoom, SignalsPanelWrapper, etc.). Composition tests (parent + child rendered together, mocking at the API boundary) are the convention — verify negative-control by reintroducing the bug. Run `npm run test` to execute.

---

## Human Review Required For

- Any change to `middleware.ts` (route protection logic)
- Any change to auth token handling in `lib/auth/`
- Any change to the Borderline candidate display or advancement logic

---