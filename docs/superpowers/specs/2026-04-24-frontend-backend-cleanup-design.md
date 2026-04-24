# Frontend ↔ Backend Production-Hardening Cleanup

**Date:** 2026-04-24
**Status:** Approved (design phase)
**Owner:** Ishant Pundir
**Scope:** End-to-end cleanup of issues identified in the 2026-04-24 code review of `frontend/app/` and its contract with `backend/nexus/`. Covers auth correctness, schema/contract alignment, SSE event hygiene, form-state migration, component decomposition, accessibility, and design-system consistency.

---

## 1. Goals

1. Eliminate every CRITICAL and HIGH severity finding from the 2026-04-24 review.
2. Eliminate every MEDIUM finding that is already a documented convention violation in the project's CLAUDE.md files (raw `useState` forms, raw data fetching, design-system bypass).
3. Bring the codebase to "production-grade" on the dimensions called out in `frontend/app/CLAUDE.md` and `backend/nexus/CLAUDE.md`: auth abstraction, accessibility, RHF+Zod forms, TanStack Query for server state, design-token discipline.
4. Land changes in 5 reviewable PR-sized batches; type-check, lint, and test must pass after each batch.

## 2. Non-Goals

- No new features. No Phase 3 work (LiveKit, candidate session, scheduler).
- No backend module that is currently a stub becomes non-stub. Stub routers stay stubs.
- No CSP wiring (already documented as a follow-up in `next.config.ts`).
- No dark mode (out of scope per `frontend/app/CLAUDE.md`).
- No DB rollback scripts for migrations (early-dev — user accepted data loss).

## 3. Decisions Locked

| ID | Decision | Pick |
|---|---|---|
| D1 | `apiFetch` AbortSignal threading | A — explicit `signal` option, threaded from every queryFn |
| D2 | When to emit `bank.question_updated` SSE | A — every successful question update, regardless of which fields changed |
| D3 | Invite acceptance Supabase auth flow | A — backend creates Supabase auth user via Admin API; frontend never calls `auth.signUp` |
| D4 | Global 401 handling | A — `QueryClient` `onError` in `DashboardProviders` redirects to `/login` and toasts |
| D5 | Token-fetch validation strategy | C — single `getUser()` validation on mount + `visibilitychange`; `getSession()` for token reads in between |
| D6 | Form/state migration scope | A — full sweep, all 6 raw-`useState` pages migrated to RHF+Zod (forms) and TanStack Query (data) |
| D7 | `jobs/[jobId]/page.tsx` decomposition | A — full extract: all 13 components → `components/dashboard/jd-panels/`, page becomes a slim shell |
| D8 | Pipeline-component design-token migration | A — sweep `PipelineFlowColumn`, `StageInspectorPanel`, `TemplatePickerDialog`, `StageConfigDrawer` to `px-*` tokens |

## 4. Sequenced Batches

The work is split into 5 batches. Each batch lands as one git commit (or one PR if the user prefers branch-per-batch). Each batch ends with a green run of: `npm run type-check`, `npm run lint`, `npm run test`, `npm run build`. Backend batches additionally run `pytest`.

```
Batch 1 → Batch 2 → Batch 3 → Batch 4 → Batch 5
```

Reasoning for ordering:
- **B1 first:** purely surgical fixes, no contract change, no schema change. De-risks the rest.
- **B2 second:** schema/contract alignment lands before B3/B4 use the new types; otherwise B3/B4 would have to ship type guards and remove them later.
- **B3 third:** auth changes are load-bearing. Doing them after schema cleanup means we touch a settled API surface, not a moving one.
- **B4 fourth:** form migrations consume the auth helpers from B3 (e.g. global 401 redirect is in place; `getFreshSupabaseToken` is the only token path).
- **B5 last:** component decomposition + a11y + design tokens. Mostly visual; safest to do once the data layer is stable.

---

## 5. Batch 1 — Critical Mechanical Fixes

> **Status:** ✅ Completed 2026-04-24 on branch `cleanup/batch-1-mechanical-fixes` (worktree `.worktrees/cleanup-batch-1/`). 18 commits `f36aec9..70b19fc`. Final gates: tsc clean, lint 0 errors, 54/54 tests, build clean. Per-task spec + code-quality review passed; whole-batch reviewer recommended landing.
>
> **Deferred follow-ups** (surfaced by the final reviewer, out of scope for B1):
> - `useJobStatusStream.isStreaming` initializes to `true` before SSE connects, suppressing polling for the initial-failure window. Consider initializing to `false` and flipping to `true` only after `onopen` succeeds.
> - `jobsApi.delete` and `orgUnitsApi.assignRole`/`removeRole` declare `Promise<{status: string}>` returns but the backend likely returns 204 → with Task 2's `apiFetch` change, callers reading `.status` will get `undefined`. Either change return types to `void` or document the actual backend response shape.
> - Five remaining `window.confirm` callsites (QuestionCard, JobPipelineFunnel, UnifiedPipelineView:298, [jobId]/page:1412, MembersSection, pipeline-templates/page) — same Dialog conversion pattern Task 12 established.

### 5.1 Scope

| ID | Issue | File(s) |
|---|---|---|
| B1.1 | `useQuestionsStatusStream` lacks `MAX_TOTAL_RETRIES` cap and auth-error rethrow | `frontend/app/lib/hooks/use-questions-status-stream.ts` |
| B1.2 | `apiFetch` does not accept `signal`; queryFn AbortSignal is ignored | `frontend/app/lib/api/client.ts` + every hook in `frontend/app/lib/hooks/*.ts` |
| B1.3 | `apiFetch` calls `res.json()` on 204 No Content responses | `frontend/app/lib/api/client.ts` |
| B1.4 | `candidate-session.ts` spreads attacker-influenced JSON onto `Error` via `Object.assign(...)` | `frontend/app/lib/api/candidate-session.ts:80-83` |
| B1.5 | `use-confirm-signals` and `use-save-signals` don't invalidate `['jobs-list']` | `frontend/app/lib/hooks/use-confirm-signals.ts`, `frontend/app/lib/hooks/use-save-signals.ts` |
| B1.6 | `use-job` polling fights with SSE: both invalidate the same query concurrently | `frontend/app/lib/hooks/use-job.ts`, `frontend/app/lib/hooks/use-job-status-stream.ts` |
| B1.7 | `<Link><button>...</button></Link>` nested-interactive in jobs index | `frontend/app/app/(dashboard)/jobs/page.tsx:329-333` |
| B1.8 | `useSearchParams()` outside `<Suspense>` (no `loading.tsx` for `/jobs/[jobId]`) | `frontend/app/app/(dashboard)/jobs/[jobId]/loading.tsx` (new), `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` |
| B1.9 | `useWatch({ control })` with no `name` re-renders entire form on each keystroke | `frontend/app/app/(dashboard)/jobs/new/page.tsx:225` |
| B1.10 | Unmount fire-and-forget mutation risks data loss on hard page close | `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx:232-241` |
| B1.11 | Dead `currentStep === 'start'` branch | `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx:107` |
| B1.12 | `key={i}` on mapped array | `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx:839` |
| B1.13 | Dead `{canManage && !signal && null}` no-op | `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx:1370` |
| B1.14 | Empty-deps `useEffect` in questions SSE hook | `frontend/app/lib/hooks/use-questions-status-stream.ts:37-39` |
| B1.15 | Inline styles on `StatusPill` | `frontend/app/app/(dashboard)/jobs/page.tsx:37-38` |
| B1.16 | `orgUnitsApi.assignRole` and `removeRole` lack typed return generics | `frontend/app/lib/api/org-units.ts:77-93` |
| B1.17 | `candidate-session.ts` `API_BASE` defaults to `''` | `frontend/app/lib/api/candidate-session.ts:9` |
| B1.18 | `window.confirm()` for bulk delete | `frontend/app/app/(dashboard)/jobs/page.tsx:276` |
| B1.19 | Two callers of `/api/auth/me` with two different return types | `frontend/app/app/(dashboard)/layout.tsx`, `frontend/app/lib/api/org-units.ts:101-103` |

### 5.2 Architecture changes in B1

**`apiFetch` signature change:**
```ts
export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string; signal?: AbortSignal } = {},
): Promise<T>
```
Behavior: 204 responses return `undefined as T`. Caller is responsible for typing as `Promise<void>`.

**SSE retry parity:** `use-questions-status-stream` mirrors the `use-job-status-stream` shape:
- Same `MAX_TOTAL_RETRIES = 20`.
- Same `RetryAbort` thrown on auth/4xx in `onerror`.
- Same `EventStreamContentType` content-type guard in `onopen`.

**Polling/SSE coordination:** `useJob` accepts an optional `isStreaming: boolean` and disables `refetchInterval` when streaming is active. `JDReviewShell` (or wherever the two hooks are colocated) wires it.

**Unmount mutation:** TanStack Query mutations actually survive component unmount on in-app SPA navigation — the existing `mutate()` call is fine for that case. The real risk is hard page close (tab close, browser close, hard nav to a different origin). Fix: add a `window.addEventListener('beforeunload', handler)` that fires only when `stagesRef.current` differs from the last-saved snapshot, returning a non-empty string to trigger the browser's "unsaved changes" prompt. Keep the existing unmount `mutate()` for the SPA case. Do **not** use `keepalive: true` here because the Supabase token is async-fetched and would not be available synchronously in `beforeunload`.

**Single `MeData` type:** Move to `frontend/app/lib/api/auth.ts` (new file) with `authApi.me()` and a single `MeResponse` type matching the backend response. `dashboard/layout.tsx` and any other callers use it.

### 5.3 Acceptance criteria for B1

- `npm run type-check`, `npm run lint`, `npm run test`, `npm run build` all pass.
- Manually verify: navigating to `/jobs/[jobId]` while signals_extracting shows status updates without a duplicate fetch in DevTools Network tab.
- Manually verify: bulk delete on `/jobs` opens a `Dialog`, not a browser confirm.
- Manually verify: confirming signals on a job immediately updates the `/pipeline` view (no 10s wait for staleTime).

---

## 6. Batch 2 — Schema + SSE Backend Alignment

### 6.1 Scope

| ID | Issue | File(s) |
|---|---|---|
| B2.1 | `JobPostingWithSnapshot` detail response missing `org_unit_name`, `created_by_email`, `updated_by_email`, `signal_count`, `needs_review_count` | `backend/nexus/app/modules/jd/schemas.py:192`, `backend/nexus/app/modules/jd/router.py` (get_job handler), frontend `lib/api/jobs.ts:85` |
| B2.2 | `POST /api/jobs/{id}/retry` returns a `JobPostingSummary` with all enrichment fields zero/null | `backend/nexus/app/modules/jd/router.py:506-531` |
| B2.3 | Backend `OrgUnitResponse.company_profile_completed_at` not in frontend `OrgUnit` type | `frontend/app/lib/api/org-units.ts:20` |
| B2.4 | SSE `bank.question_updated` event listened-for but never emitted | `backend/nexus/app/modules/question_bank/sse.py`, `backend/nexus/app/modules/question_bank/service.py` (update_question, regenerate_question, delete_question, reorder, confirm_bank, create_question) |

### 6.2 Backend changes in B2

**B2.1: `get_job` enrichment.** The handler currently returns `JobPostingWithSnapshot` directly. Refactor to share the enrichment helper used by `list_jobs` so both detail and list paths return the same shape. Pydantic schema `JobPostingWithSnapshot` extends `JobPostingSummary` so the new fields are inherited.

**B2.2: `retry` enrichment.** Same fix — the retry handler at line 506 must call the same enrichment path before returning.

**B2.4: SSE event emission.** Add a Redis pub/sub event publish in every question-bank mutation site:
- `service.update_question` → `bank.question_updated`
- `service.regenerate_question` → `bank.question_updated`
- `service.delete_question` → `bank.question_updated`
- `service.reorder_questions` → `bank.question_updated`
- `service.create_question` → `bank.question_updated`
- `service.confirm_bank` → already covered by `bank.status_changed`; emit both for safety

The event payload includes `bank_id`, `stage_id`, `question_id` (where applicable), `event_type` (the mutation kind for observability), and a `correlation_id`.

The SSE generator in `sse.py` subscribes to the same Redis channel and forwards events tagged with the matching `job_id`.

### 6.3 Frontend changes in B2

- `lib/api/jobs.ts`: align `JobPostingWithSnapshot` and `JobPostingSummary` types with backend.
- `lib/api/org-units.ts`: add `company_profile_completed_at?: string | null` to `OrgUnit`.
- `lib/hooks/use-questions-status-stream.ts`: confirm the existing `bank.question_updated` listener still invalidates the right keys (`['bank', jobId, stageId]` and `['banks', jobId]`).

### 6.4 Migration / data

User accepted data deletion. The SSE additions are additive. The schema response changes are response-only (no DB migration). No migration needed.

### 6.5 Acceptance criteria for B2

- Backend tests pass (`pytest`). Existing tests for `list_jobs` shape extended to cover `get_job` and `retry`.
- New backend tests assert `bank.question_updated` fires on each of the 5 mutation paths.
- Frontend `npm run type-check` passes with the updated types — no `unknown`-narrowing required for the now-populated fields.
- Manual smoke: open the question-bank UI in two tabs, edit a question's text in tab A, see the list refresh in tab B without a page reload.

---

## 7. Batch 3 — Auth Hardening + Invite Flow

### 7.1 Scope

| ID | Issue | File(s) |
|---|---|---|
| B3.1 | `getFreshSupabaseToken` calls `getSession()` without prior validation | `frontend/app/lib/auth/tokens.ts` |
| B3.2 | `app/(dashboard)/profile/page.tsx` and `app/onboarding/page.tsx` use raw `getSession()` instead of `getFreshSupabaseToken` | both files |
| B3.3 | Invite flow calls `supabase.auth.signUp()` then `signInWithPassword()` (architecture violation + user enumeration oracle) | `frontend/app/app/(auth)/invite/page.tsx`, `backend/nexus/app/modules/auth/router.py` (new endpoint) |
| B3.4 | No global 401 handler; hooks retry 3× then surface errors to component | `frontend/app/components/dashboard/providers.tsx` |

### 7.2 D5 implementation: SessionGuard provider

A new client component at `frontend/app/components/dashboard/SessionGuard.tsx`:
- On mount: calls `supabase.auth.getUser()`. If null → redirect to `/login`.
- Listens to `document.visibilitychange`: when tab becomes visible after >5 minutes hidden → re-validate via `getUser()`.
- Listens to `supabase.auth.onAuthStateChange('SIGNED_OUT')` → redirect to `/login`.

`getFreshSupabaseToken` keeps its current `getSession()`-only implementation (fast). The invariant is: any time `getFreshSupabaseToken` returns a token, the session has been validated within the last visibility window OR the cookie is freshly refreshed.

`SessionGuard` is mounted inside `DashboardProviders`, which already wraps the dashboard tree.

### 7.3 D3 implementation: backend invite acceptance

Replace the current 2-call frontend flow with a single backend call:

**New backend endpoint:** `POST /api/auth/accept-invite`
- Body: `{ raw_token: string, password: string }`
- No auth required (public).
- Backend:
  1. Verify invite token (existing `verify_invite` logic).
  2. Call Supabase Admin API `auth.admin.create_user({ email, password, email_confirm: true })`. On "already exists" error → call `auth.admin.update_user_by_email({ email, password })` (idempotent password reset) — accepted edge case, not a security regression because the invite token itself proves possession.
  3. Use the returned `auth_user_id` to create the app `User` row (existing logic from `complete_invite`).
  4. Mint a fresh Supabase session via the SDK (or sign-in-with-password for the new user) and return `{ access_token, refresh_token, redirect_to }`.
- **Compensating actions, not a single transaction.** Supabase auth and the app DB are separate systems, so true atomicity isn't possible. Instead: if step 3 fails after step 2 succeeded, the handler calls `auth.admin.delete_user(auth_user_id)` to roll back. If step 4 fails after step 3 succeeded, the app user row is deleted and `auth.admin.delete_user` is called. All compensation paths log a structured warning so failures here are visible.

**Old endpoint:** `POST /api/auth/complete-invite` is **deleted**. No deprecation period — single frontend caller, hard switch.

**Frontend invite page:** `app/(auth)/invite/page.tsx`:
- No more `supabase.auth.signUp` or `signInWithPassword`.
- POSTs `{ raw_token, password }` to `/api/auth/accept-invite`.
- On success, calls `supabase.auth.setSession({ access_token, refresh_token })` to install the session cookie, then `router.push(safeRedirect)`.

**Backend service:** new `app/modules/auth/admin_client.py` wraps the Supabase Admin SDK. The existing `_delete_auth_user` helper in `settings/service.py` is migrated here too — single source of truth for Supabase Admin API calls.

### 7.4 D4 implementation: global 401 handler

In `DashboardProviders`:
```ts
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err) => handleAuthError(err),
  }),
  mutationCache: new MutationCache({
    onError: (err) => handleAuthError(err),
  }),
})
```
`handleAuthError` (in `lib/auth/handle-error.ts`):
- If `err instanceof ApiError && err.status === 401` OR `err.message === 'No active Supabase session'`:
  - Sign out the Supabase client (clears cookies).
  - `toast.error('Session expired. Please log in again.')`.
  - `router.push('/login')`.
- Otherwise: no-op (let the component error boundary handle it).

The `router` reference comes via a `useRouter()` hook in `DashboardProviders` — the handler is closed over the router instance.

### 7.5 Migration / data

- `user_invites.status='pending'` rows generated under the old flow remain valid: the new endpoint reads the same table. No data migration.
- The `complete-invite` endpoint deletion is breaking — but no in-flight invites in production yet (early dev).

### 7.6 Acceptance criteria for B3

- Backend tests cover `/api/auth/accept-invite` happy path, expired-token, email mismatch, already-existing-auth-user.
- Frontend test: invite page submits to the new endpoint, never calls Supabase signup.
- Manual: log out mid-session in another tab → `SessionGuard` detects on next visibility change → redirect to `/login`.
- Manual: revoke a session via Supabase dashboard → next query invalidation triggers `handleAuthError` → redirect to `/login`.
- Codebase grep: zero references to `supabase.auth.signUp`, `signInWithPassword` in the frontend.

### 7.7 Human review required

Per CLAUDE.md, this batch must have explicit human review before merge:
- New `accept-invite` endpoint
- Deletion of `complete-invite` endpoint
- Changes to `lib/auth/tokens.ts`
- The Admin API wrapper

---

## 8. Batch 4 — Form + State Migration

### 8.1 Scope

| ID | Page | Migration |
|---|---|---|
| B4.1 | `app/(auth)/login/page.tsx` | useState → RHF + Zod |
| B4.2 | `app/(auth)/invite/page.tsx` | useState → RHF + Zod (after B3 lands) |
| B4.3 | `app/onboarding/page.tsx` | useState → RHF + Zod |
| B4.4 | `app/(dashboard)/settings/team/page.tsx` (478 LOC) | useEffect+fetch → TanStack Query; forms → RHF+Zod |
| B4.5 | `app/(dashboard)/settings/org-units/page.tsx` (720 LOC) | useEffect+fetch → TanStack Query; forms → RHF+Zod |
| B4.6 | `app/(dashboard)/settings/org-units/[unitId]/page.tsx` | useEffect+fetch → TanStack Query; forms → RHF+Zod |

### 8.2 Migration pattern

Each page follows the same shape:

1. **Schemas** colocated in `schema.ts` next to the page:
   ```
   app/(dashboard)/settings/team/schema.ts
   ```
   Exports Zod schemas with derived `z.infer<>` types.

2. **API namespaces** in `lib/api/`:
   - `lib/api/team.ts` (new) — `teamApi.list`, `teamApi.invite`, `teamApi.resend`, `teamApi.revoke`, `teamApi.deactivate`.
   - `lib/api/auth.ts` (already created in B1.19) — `authApi.login`, `authApi.acceptInvite` (B3), `authApi.completeOnboarding`.

3. **Hooks** in `lib/hooks/`:
   - `useTeamMembers()` (query)
   - `useInviteTeamMember()` (mutation, invalidates `['team', 'members']`)
   - `useResendInvite()` (mutation)
   - `useRevokeInvite()` (mutation)
   - `useDeactivateUser()` (mutation)
   - `useOrgUnits()`, `useOrgUnit(id)`, `useCreateOrgUnit()`, `useUpdateOrgUnit()`, `useDeleteOrgUnit()`

4. **Page** uses `useForm({ resolver: zodResolver(schema) })`, `<form onSubmit={form.handleSubmit(onSubmit)}>`, and surfaces FastAPI 422 errors per-field via `form.setError(field, { message })`.

### 8.3 422 → field error mapping

A shared utility in `lib/api/errors.ts`:
```ts
export function applyApiErrorToForm(err: unknown, form: UseFormReturn): boolean
```
- Returns `true` if it mapped at least one field.
- For `ApiError` with status 422 and `detail: ValidationError[]` shape, walks `loc` to find the field name and calls `form.setError`.
- For all other error shapes, returns `false` (caller falls back to toast).

### 8.4 Acceptance criteria for B4

- `npm run type-check`, `npm run lint`, `npm run test`, `npm run build` all pass.
- All 6 pages have zero `useState` calls for form state and zero raw `fetch` for data.
- Existing tests for these pages updated; new tests for the form 422 → field mapping.
- Manual: submit each form with invalid data → field-level error message appears under the relevant input.

---

## 9. Batch 5 — Component Decomposition + A11y + Design Tokens

### 9.1 Scope

| ID | Issue | File(s) |
|---|---|---|
| B5.1 | `jobs/[jobId]/page.tsx` (1,659 LOC, 13 nested components) split | `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` + new files in `frontend/app/components/dashboard/jd-panels/` |
| B5.2 | `TemplatePickerDialog` missing focus trap | `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx` |
| B5.3 | `SignalRow` not keyboard accessible (`<div onClick>`) | extracted file from B5.1 |
| B5.4 | `aria-pressed` on tab buttons should be `role="tab"` + `aria-selected` | `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx:79,86` |
| B5.5 | Pipeline components use raw `zinc-*` instead of `px-*` tokens | `frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx`, `StageInspectorPanel.tsx`, `TemplatePickerDialog.tsx`, `StageConfigDrawer.tsx` |

### 9.2 D7 implementation: jobs/[jobId] decomposition

**Target structure:**
```
components/dashboard/jd-panels/
├── JDReviewShell.tsx           ← top-level layout
├── SignalsCanvas.tsx           ← signals display area
├── SignalInspector.tsx         ← right-side inspector panel
├── SectionsRail.tsx            ← left-side section navigation
├── FullJdCanvas.tsx            ← original JD view
├── SignalGroup.tsx             ← group container
├── SignalRow.tsx               ← individual signal row (now <button>, keyboard-accessible)
├── InspectorHint.tsx
├── InspectorTips.tsx
├── CanvasHeader.tsx
├── TabStrip.tsx
├── Confidence.tsx              ← confidence chip
├── SnippetHighlighted.tsx      ← highlighted JD snippet
└── helpers/
    └── suggestQuestions.ts     ← pure helper, no JSX
```

The page itself (`app/(dashboard)/jobs/[jobId]/page.tsx`) becomes a thin server-shell-or-client-shell that imports `JDReviewShell` from the components directory. Target: under 200 LOC.

Tests: existing tests in the page file move with their components if any; otherwise new vitest specs cover `SignalRow` keyboard activation and `JDReviewShell` integration.

### 9.3 A11y fixes (B5.2, B5.3, B5.4)

- `SignalRow`: convert `<div onClick>` → `<button type="button">` styled to look the same. Inherits keyboard activation, focus ring, and `:disabled` for free.
- `TemplatePickerDialog`: replace the custom `<div role="dialog">` with the `px/Dialog` primitive that already implements focus trap + escape handler. If `px/Dialog` doesn't exist yet, extend it to support this case (it's listed in `components/px/index.ts`).
- Tab strip in `TemplatePickerDialog`: change `aria-pressed` to `role="tab"` + `aria-selected`. Wrapping container gets `role="tablist"`. The corresponding panels get `role="tabpanel"`.

### 9.4 D8 implementation: design-token sweep

For each of `PipelineFlowColumn.tsx`, `StageInspectorPanel.tsx`, `TemplatePickerDialog.tsx`, `StageConfigDrawer.tsx`:

The `px-*` design tokens are defined as CSS custom properties in `app/globals.css` (e.g. `--px-surface`, `--px-hairline`, `--px-fg`). Existing px-system components use them via `style={{ background: 'var(--px-surface)' }}`. There are NO Tailwind utility-class equivalents (no `bg-px-surface`); only CSS variable references. The sweep:

- Replace `bg-white` → `style={{ background: 'var(--px-surface)' }}`.
- Replace `border-zinc-200` → `style={{ borderColor: 'var(--px-hairline)' }}`.
- Replace `text-zinc-500/600/700/etc.` → `style={{ color: 'var(--px-fg-3)' }}` / `var(--px-fg-2)` / `var(--px-fg)` per visual hierarchy (consult adjacent components for the right level).
- For arbitrary zinc shades not in the px palette: pick the nearest px token; if a true match doesn't exist, surface it as a NOTE in the PR for design review rather than guessing.

Visual verification: load each affected screen in the dev server, side-by-side compare against pre-change.

### 9.5 Acceptance criteria for B5

- `npm run type-check`, `npm run lint`, `npm run test`, `npm run build` all pass.
- `wc -l` on `app/(dashboard)/jobs/[jobId]/page.tsx` returns < 200.
- Codebase grep for `border-zinc`, `bg-white`, `text-zinc-` in `components/dashboard/pipeline/` returns zero matches (or the only matches are explicitly annotated as "no px-token equivalent — needs design review").
- Manual a11y: navigate `TemplatePickerDialog` and `SignalRow` entirely by keyboard; open dialog → focus trapped; Esc closes.
- Manual visual: pipeline page, JD review page, template picker render visually identical to pre-change.

---

## 10. Cross-Cutting Considerations

### 10.1 Testing

- Existing tests stay passing throughout. No batch may regress tests.
- New tests required for: B1 SSE retry cap, B2 SSE event emission, B3 invite acceptance, B4 422-to-field-error mapping, B5 SignalRow keyboard activation.
- Frontend tests use Vitest + Testing Library + jsdom (already installed).
- Backend tests use pytest (already configured in `backend/nexus/tests/`).

### 10.2 Observability

- B3's `accept-invite` endpoint logs the same correlation-id pattern as `complete-invite` did.
- B2's SSE event emission includes `correlation_id` in the payload so a question edit can be traced from the request → service → Redis pub → SSE consumer.

### 10.3 Backwards compatibility

- Early dev. The user accepted data deletion. Breaking changes are fine.
- Specifically: `complete-invite` endpoint deletion (B3), `JobPostingWithSnapshot` shape extension (B2 — additive, but old clients without B1.19 type updates will silently ignore the new fields).

### 10.4 Risk register

| Risk | Mitigation |
|---|---|
| B3 auth changes break login | Land B1+B2 first; B3 lands as its own PR with manual smoke + human review |
| B5 component decomposition breaks tests | Move tests with their components; vitest is fast — run after each extraction |
| B4 form migrations introduce regression | Each page tested manually before commit; existing test suite is the safety net |
| SSE Redis publishing failure | Wrap publish in try/except; failure logs but doesn't break the mutation |

---

## 11. Out of Scope — Explicit List

These were considered and excluded:

- **Adding CSP headers** — separate work, requires nonce wiring across multiple SDKs.
- **Migrating to React Compiler** — Next 16 supports it but no ROI for this cleanup.
- **Replacing custom `px/` design system with shadcn primitives** — `px/` is intentional per CLAUDE.md.
- **Moving Supabase auth out of `@supabase/ssr`** — would replace half the auth layer with no demonstrated need.
- **Adding bundle-size analysis or perf budget** — separate observability work.
- **Building a session-replay tool** — Phase 3.
- **Removing GSAP or `@xyflow/react`** — both have active uses (per CLAUDE.md).

---

## 12. Implementation Plan

After this design is approved, the writing-plans skill will produce one implementation plan per batch. Plans live at:

```
docs/superpowers/plans/2026-04-24-cleanup-batch-1-mechanical-fixes.md
docs/superpowers/plans/2026-04-24-cleanup-batch-2-schema-sse.md
docs/superpowers/plans/2026-04-24-cleanup-batch-3-auth-hardening.md
docs/superpowers/plans/2026-04-24-cleanup-batch-4-form-migration.md
docs/superpowers/plans/2026-04-24-cleanup-batch-5-decomposition-a11y.md
```

Each plan is executed in sequence. Per-batch acceptance criteria (sections 5.3, 6.5, 7.6, 8.4, 9.5) are the gate to move to the next batch.
