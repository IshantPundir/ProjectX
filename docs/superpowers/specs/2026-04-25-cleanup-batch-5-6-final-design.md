# Cleanup Batch 5+6 — Final-Batch Design

> **Date:** 2026-04-25
> **Branch (planned):** `cleanup/batch-5-6-final` (worktree at `.worktrees/cleanup-batch-5-6/`)
> **Parent spec:** `docs/superpowers/specs/2026-04-24-frontend-backend-cleanup-design.md` §9 (B5)
> **Status:** approved — proceeding to writing-plans

This document is the locked design for the combined Batch 5 + Batch 6 cleanup — the original B5 scope (component decomposition + a11y + design tokens) folded together with B1–B4 review-deferred polish items. After this batch lands, the 2026-04-24 cleanup spec is fully done.

---

## 1. Goals

- Land B5.1–B5.5 from the parent spec: decompose `jobs/[jobId]/page.tsx`, fix a11y on `TemplatePickerDialog` and `SignalRow`, sweep raw zinc/white refs to `px-*` design tokens.
- Fold in deferred polish: 6 remaining `confirm()` callsites, 3 small frontend cleanups, onboarding error mapping, backend login hardening, 3 test gaps, 1 nested-loc fix.
- Preserve the cleanup spec invariant: data model, RLS, auth contracts, and module boundaries unchanged. This batch is correctness, ergonomics, and accessibility — not architecture.

## 2. Non-Goals

These are **explicitly excluded** from this batch — flag and move on if encountered:

- Question-editor UI wiring (`+ Add question` onClick, inline question edit). Feature work; deserves its own scope.
- Pipeline SSE pub/sub migration (extending `app/core/pubsub.py` to the pipeline tab). Architectural extension; deserves a B2-style design.
- `CompanyProfileDetail` `form.watch` perf concern (`useWatch` in leaf subcomponents like `<CharCount>`). Perf, not correctness.
- Phase 3 work (LiveKit, candidate session, scheduler, analysis, reporting).
- CSP wiring (planned follow-up, requires nonce coordination across multiple SDKs).
- Dark mode (explicit non-goal per `frontend/app/CLAUDE.md`).
- The `proxy.ts` + `(dashboard)/layout.tsx` SSR `getSession()` calls (B3 left these intentionally).
- Other 13 pipeline files outside the 4 named in §6.3 that contain raw `zinc-*` classes — out of scope here; tracked for a future sweep.

## 3. Locked Decisions (brainstorming 2026-04-25)

| ID | Decision | Lock |
|---|---|---|
| D5.1 | jd-panels component boundaries | Nested `components/` + `helpers/` subfolders. One component per file (no exceptions for "small leaves"). Helpers are pure-function modules with no JSX. `index.ts` re-exports `JDReviewShell` only — internal layout free to refactor without touching the page. |
| D5.2 | `confirm()` → Dialog pattern | Shared `<DangerConfirmDialog>` primitive in `components/px/`. All 7 callsites (6 in C4 + 1 in C5.3 + MembersSection in C5.5) use it. MembersSection migrates from its inline B4 pattern in the same batch for codebase consistency. |
| D5.3 | `applyApiErrorToForm` nested-loc strategy | `stripPrefixes?: string[]` opt, default `["body"]` (preserves current behavior). Greedy front-strip — strips while the head matches any string in the list. No `locTransform` hook (YAGNI). |
| D5.4 | `AuthProvider.sign_out` shape | `sign_out(tokens: SessionTokens) -> None` on the `AuthProvider` protocol. Idempotent on already-revoked tokens. Idempotent in unconfigured environments. Called from BOTH 403 branches in the login handler (deactivated-user AND missing-tenant_id), not just the deactivated case. |
| D5.5 | Onboarding error mapping | `CompanyProfileForm` accepts an optional `onError?: (err, form) => void` prop. Onboarding wires it with `applyApiErrorToForm(err, form, { stripPrefixes: ["body", "metadata"] })` falling back to `toast.error`. Folded into C5 as item 4. |
| D5.6 | C8 composition test approach | Reusable harness `tests/_utils/render.tsx` exports `renderWithProviders(ui)` that wraps a fresh `QueryClient` (`retry: false`, `gcTime: 0`). C8.1, C8.2, C8.3 all use it. Network is mocked at `apiFetch` boundary, not deeper. |

---

## 4. Cluster Matrix

| Cluster | Tier | Scope | Review |
|---|---|---|---|
| C1 | T1 | Decompose `jobs/[jobId]/page.tsx` per D5.1. Folds in B5.3 (`SignalRow` `<button>`) and the embedded `confirm()` at line 1412 (uses `DangerConfirmDialog`). | Split |
| C2 | T1 | A11y: `TemplatePickerDialog` swaps custom `<div role="dialog">` for `px/Dialog` (focus trap, ESC, scroll lock, focus restore — all already implemented). Tab strip → `role="tablist"` + `role="tab"` + `aria-selected` + `role="tabpanel"`. | Split |
| C3 | T1 | Design-token sweep on 4 files only: `PipelineFlowColumn.tsx` (6 refs), `StageInspectorPanel.tsx` (4), `TemplatePickerDialog.tsx` (9), `StageConfigDrawer.tsx` (23). 42 refs total. Ambiguous shades flagged in PR for design review. | Combined |
| C4 | T1 | `DangerConfirmDialog` primitive + 6 `confirm()` callsites: pipeline-templates page, UnifiedPipelineView, JobPipelineFunnel ×2, QuestionCard ×2. (C1 owns the 7th callsite at line 1412.) | Combined |
| C5 | T1 | Small cleanups: (1) `useJobStatusStream.isStreaming` initial-state fix, (2) `MembersSection` uses real `useTeamMembers`, (3) `settings/team` bespoke `ConfirmDialog` → `DangerConfirmDialog`, (4) onboarding `onError` wiring, (5) MembersSection inline Dialog → `DangerConfirmDialog`. | Combined |
| C6 | T2 | `applyApiErrorToForm` gets `stripPrefixes` opt per D5.3. Default unchanged; existing 14 callers behave identically. | Split |
| C7 | T2 | Backend: add `sign_out` to `AuthProvider` protocol + Supabase impl. Login handler calls it in both 403 branches. `LoginRequest.password` gets `Field(min_length=1, max_length=128)`. CLAUDE.md auth-module gate triggers — split review. | Split |
| C8 | T2 | New `tests/_utils/render.tsx` harness. New tests: MembersSection cancel-path (C8.1), org-units `client_account` flow (C8.2), `<CompanyProfileDetail>` no-nested-form composition test (C8.3). C6 strip-prefix regression test colocated. | Combined |

Review cadence per `feedback_subagent_review_cadence`: combined for small mechanical clusters (C3, C4, C5, C8); split for behavioral-semantics or load-bearing clusters (C1 decomposition, C2 a11y, C6 error-mapping, C7 auth + CLAUDE.md gate).

---

## 5. Sequencing

```
Pre-flight
  └─ worktree, env copy, baseline tsc/lint/vitest/build green, baseline pytest 498 green

Wave A — parallel
  ├─ C4.Task 1: DangerConfirmDialog primitive in components/px/
  ├─ C5.1: useJobStatusStream isStreaming init false
  ├─ C5.2: MembersSection uses real useTeamMembers
  ├─ C5.4: onboarding onError wiring + CompanyProfileForm prop change
  ├─ C6:    applyApiErrorToForm stripPrefixes
  ├─ C2:    TemplatePickerDialog px/Dialog swap + tab strip a11y
  ├─ C3:    Design-token sweep (4 files)
  └─ C8.3:  composition regression test (CompanyProfileDetail today already has no nested form per commit 6fd14d0 — test passes on main)

Wave B — parallel (after primitive lands)
  ├─ C4.Tasks 2-7: 6 confirm() callsites use DangerConfirmDialog
  ├─ C5.3: settings/team ConfirmDialog → DangerConfirmDialog
  ├─ C5.5: MembersSection inline Dialog → DangerConfirmDialog
  └─ C1:   jd-panels decomposition (heavy lift; embeds line 1412 confirm fix)

Wave C — sprinkle alongside owners
  ├─ C8.1: MembersSection cancel-path (lands with C5.5)
  └─ C8.2: org-units client_account flow (independent, lands when convenient)

Wave D — last
  └─ C7: Backend sign_out + password length bound
```

C7 is last because backend-rebuild TDD cycles are slow. Within C1, vitest after each extracted leaf to catch regressions early.

---

## 6. Cluster details

### 6.1 C1 — jd-panels decomposition

**Target structure:**
```
frontend/app/components/dashboard/jd-panels/
├── JDReviewShell.tsx              ← root, owns tab + draft signals state
├── SectionsRail.tsx               ← left nav, presentational
├── SignalsCanvas.tsx              ← signals body, owns selection callback up
├── SignalInspector.tsx            ← right-side editor, owns local edit state
├── FullJdCanvas.tsx               ← JD source view
├── components/
│   ├── SignalRow.tsx              ← <button>, keyboard-activated (B5.3)
│   ├── SignalGroup.tsx
│   ├── CanvasHeader.tsx
│   ├── TabStrip.tsx               ← uses role="tablist" (mirrors C2 pattern)
│   ├── Confidence.tsx
│   ├── SourceBadge.tsx
│   ├── SnippetHighlighted.tsx
│   ├── InspectorHint.tsx
│   ├── InspectorTips.tsx
│   ├── InspectorAction.tsx
│   ├── Kbd.tsx
│   └── EmptyRow.tsx
├── helpers/
│   ├── suggestQuestions.ts
│   ├── groupSignals.ts
│   ├── findSnippet.ts
│   ├── needsReview.ts
│   └── weightToConfidence.ts
└── index.ts                       ← re-exports JDReviewShell only
```

**Page becomes:**
```tsx
// frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx — target <200 LOC
"use client"
// imports: useParams, useSearchParams, useRouter, useEffect, useJob, useJobPipeline,
//          useJobStatusStream, useTriggerEnrich, JDReviewShell, LoadingSkeleton, ErrorBanner
export default function JobReviewPage() { /* unchanged loading/redirect logic */ }
```

Inside C1's commit sequence:
1. Extract pure helpers (no React, easiest tests)
2. Extract leaf components (presentational, no state)
3. Extract `SignalRow` AS `<button>` — this is B5.3
4. Extract `SignalInspector`, replace its inline `confirm()` at line 1412 with `<DangerConfirmDialog>`
5. Extract `SectionsRail`, `SignalsCanvas`, `FullJdCanvas`
6. Extract `JDReviewShell` (root) — page reduces to shell
7. Final `wc -l` sanity check; manual smoke

`SignalRow` becomes:
```tsx
<button type="button"
  className="px-signal-row"
  data-state={isSelected ? "selected" : undefined}
  onClick={onSelect}
  aria-current={isSelected ? "true" : undefined}>
  {/* same content; native keyboard activation, focus ring, :disabled for free */}
</button>
```

### 6.2 C2 — A11y on `TemplatePickerDialog`

**Current:** custom `<div role="dialog">` with hand-rolled escape handler. Tab strip uses `aria-pressed` on buttons.

**After:**
- Replace the dialog wrapper with `<Dialog open={...} onOpenChange={...}><DialogContent>...</DialogContent></Dialog>` from `components/px/Dialog.tsx`. Inherits focus trap (Tab cycles inside), ESC handler, scroll lock, focus restoration to opener. (`px/Dialog` already implements all of this — verified during self-review.)
- Tab strip: wrapping container becomes `<div role="tablist">`. Each tab button becomes `<button role="tab" aria-selected={isActive ? "true" : "false"} tabIndex={isActive ? 0 : -1}>`. Each panel container becomes `<div role="tabpanel">`. The `aria-pressed` attribute is removed (toggle-button semantics, not tab semantics).

**Verification:** keyboard-only navigation: Tab into dialog → focus trapped, ESC closes, focus returns to opener. Arrow keys move between tabs; Enter activates panel.

### 6.3 C3 — Design-token sweep

**Files in scope (4):**
| File | Refs |
|---|---|
| `PipelineFlowColumn.tsx` | 6 |
| `StageInspectorPanel.tsx` | 4 |
| `TemplatePickerDialog.tsx` | 9 |
| `StageConfigDrawer.tsx` | 23 |
| **Total** | **42** |

**Replacements:**
- `bg-white` → `style={{ background: 'var(--px-surface)' }}`
- `border-zinc-200` (or `border-zinc-100/300`) → `style={{ borderColor: 'var(--px-hairline)' }}`
- `text-zinc-500/600/700` → `style={{ color: 'var(--px-fg-3)' }}` / `var(--px-fg-2)` / `var(--px-fg)` per visual hierarchy. Consult adjacent components for the right level.
- Arbitrary zinc shades without a clean px equivalent → leave as-is, annotate with a `// TODO(design-review): no px-token equivalent for zinc-N` comment, surface in PR description.

**Verification:**
```bash
for f in PipelineFlowColumn.tsx StageInspectorPanel.tsx TemplatePickerDialog.tsx StageConfigDrawer.tsx; do
  grep -cE "border-zinc|bg-white|text-zinc-" "frontend/app/components/dashboard/pipeline/$f"
done
# All four expected to return 0, OR the only matches are explicitly annotated.
```

The 13 other pipeline files with zinc/white refs are explicitly out of scope (§2). They become a follow-up sweep.

### 6.4 C4 — `DangerConfirmDialog` primitive + 6 callsites

**Primitive shape (`components/px/DangerConfirmDialog.tsx`):**
```tsx
export interface DangerConfirmDialogProps {
  open: boolean
  title: string
  description: ReactNode  // ReactNode so callsites can interpolate <strong>
  confirmLabel: string
  pendingLabel?: string   // defaults to "${confirmLabel}…"
  pending?: boolean
  onConfirm: () => void | Promise<void>
  onClose: () => void
}

export function DangerConfirmDialog({
  open, title, description, confirmLabel, pendingLabel,
  pending = false, onConfirm, onClose,
}: DangerConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="px-btn ghost sm" disabled={pending}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm}
            className="px-btn danger sm" disabled={pending}>
            {pending ? (pendingLabel ?? `${confirmLabel}…`) : confirmLabel}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

Exported from `components/px/index.ts`.

**6 callsites converted:**
| # | File:line | Action |
|---|---|---|
| 1 | `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx:44` | Template delete |
| 2 | `components/dashboard/pipeline/UnifiedPipelineView.tsx:298` | Template reset |
| 3 | `components/dashboard/pipeline/JobPipelineFunnel.tsx:383` | Template reset |
| 4 | `components/dashboard/pipeline/JobPipelineFunnel.tsx:1346` | Stage delete |
| 5 | `components/dashboard/question-bank/QuestionCard.tsx:125` | Question delete confirmation |
| 6 | `components/dashboard/question-bank/QuestionCard.tsx:142` | Question delete |

Pattern (mirrors MembersSection's B4 shape, now distilled into the primitive):
```tsx
const [toDelete, setToDelete] = useState<X | null>(null)
const mutation = useDeleteX()
async function handleConfirm() {
  if (!toDelete) return
  try { await mutation.mutateAsync(toDelete); setToDelete(null) }
  catch (err) { toast.error(...) /* dialog stays open */ }
}
return <>
  <button onClick={() => setToDelete(item)}>Delete</button>
  <DangerConfirmDialog
    open={!!toDelete}
    title="Delete X"
    description={<>Delete <strong>{toDelete?.name}</strong>? This cannot be undone.</>}
    confirmLabel="Delete"
    pendingLabel="Deleting…"
    pending={mutation.isPending}
    onConfirm={handleConfirm}
    onClose={() => setToDelete(null)}
  />
</>
```

### 6.5 C5 — Small cleanups (5 items)

1. **`useJobStatusStream.isStreaming` initial state** (`lib/hooks/use-job-status-stream.ts:58`):
   - Current: `useState(true)` — suppresses polling for the initial-failure window.
   - Fix: `useState(false)`; flip to `true` inside `onopen`; `setIsStreaming(true)` at line 64 already exists for the connection-attempt branch — verify it still fires correctly under the new initial state.

2. **`MembersSection` uses real `useTeamMembers`** (`MembersSection.tsx:52-56`):
   - Current: inline `useQuery({ queryKey: ['team', 'members'], queryFn: ... })`.
   - Fix: replace with `const tenantUsersQuery = useTeamMembers()` — the existing hook from `lib/hooks/use-team-members.ts`. Same query key, identical surface. Removes `getFreshSupabaseToken` + `teamApi` imports from this file.

3. **`settings/team/page.tsx` `ConfirmDialog` → `DangerConfirmDialog`**:
   - Delete the bespoke `ConfirmDialog` component at lines ~83-119.
   - Replace the callsite at line ~172 with `<DangerConfirmDialog>`.
   - Verify same UX: title + description + Cancel + destructive Confirm.

4. **Onboarding `onError` wiring** (`app/onboarding/page.tsx`):
   - `CompanyProfileForm` gets a new optional `onError?: (err, form) => void` prop. When provided, the form's internal submit catches and delegates to `onError(err, form)` instead of re-throwing.
   - Onboarding passes:
     ```tsx
     <CompanyProfileForm
       onSubmit={handleSubmitProfile}
       onError={(err, form) => {
         if (applyApiErrorToForm(err, form, { stripPrefixes: ["body", "metadata"] })) return
         toast.error(err instanceof Error ? err.message : "Failed to save profile")
       }}
     />
     ```
   - When `onError` is not provided, current behavior (re-throw to parent) is preserved — other callers (`[unitId]/CompanyProfileDetail`) unaffected.

5. **MembersSection inline Dialog → `DangerConfirmDialog`**:
   - Replace the inline `<Dialog open={!!toRemove}>...</Dialog>` block (lines 314-346) with a single `<DangerConfirmDialog>` callsite. Identical UX, less code.
   - C8.1 (cancel-path test) and C8.3 (composition test) cover the regression surface.

### 6.6 C6 — `applyApiErrorToForm` strip-prefix

**Change to `lib/api/errors.ts`:**

Add `stripPrefixes?: string[]` opt to the public signature. Default `["body"]` — preserves all existing 14 caller behaviors.

```ts
export function applyApiErrorToForm<T extends FieldValues>(
  err: unknown,
  form: UseFormReturn<T>,
  opts: { fallbackFieldKey?: Path<T>; stripPrefixes?: string[] } = {},
): boolean {
  if (!(err instanceof ApiValidationError)) return false
  const stripPrefixes = opts.stripPrefixes ?? ["body"]
  // ... rest unchanged ...
  for (const entry of err.fieldErrors) {
    const path = locToPath(entry.loc, stripPrefixes)
    // ...
  }
}

function locToPath(loc: (string | number)[], stripPrefixes: string[]): string | null {
  let stripped = loc
  while (
    stripped.length > 0 &&
    typeof stripped[0] === "string" &&
    stripPrefixes.includes(stripped[0] as string)
  ) {
    stripped = stripped.slice(1)
  }
  if (stripped.length === 0) return null
  return stripped.map((seg) => String(seg)).join(".")
}
```

**Tests in `tests/api/apply-api-error-to-form.test.ts` (new):**
- Default `["body"]`: `loc: ["body", "metadata", "website"]` → `"metadata.website"` (current behavior preserved)
- `stripPrefixes: ["body", "metadata"]`: same loc → `"website"`
- Multiple matching prefixes: `loc: ["body", "body", "x"]` → `"x"` (greedy)
- Mixed: `stripPrefixes: ["body", "metadata"]` with `loc: ["body", "x"]` → `"x"` (strips body, x is not metadata, stops)

### 6.7 C7 — Backend login hardening

**Protocol extension (`app/modules/auth/admin/base.py`):**
```python
class AuthProvider(Protocol):
    # ... existing methods ...

    async def sign_out(self, tokens: SessionTokens) -> None:
        """Revoke a previously-issued session.

        Idempotent on already-revoked tokens (404 → log + return).
        Idempotent in unconfigured environments (log + return).
        Other transport errors raise AuthProviderError.
        """
        ...
```

**Supabase impl (`app/modules/auth/admin/supabase.py`):**
```python
async def sign_out(self, tokens: SessionTokens) -> None:
    if _missing_config():
        logger.warning(
            "auth.admin.sign_out.skipped",
            reason="supabase_url or service_role_key not configured",
        )
        return
    url = f"{settings.supabase_url}/auth/v1/logout"
    headers = {**_anon_headers(), "Authorization": f"Bearer {tokens.access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, headers=headers)
    if resp.status_code in (200, 204, 404):
        if resp.status_code == 404:
            logger.info("auth.admin.sign_out.already_revoked")
        else:
            logger.info("auth.admin.sign_out.ok")
        return
    logger.error(
        "auth.admin.sign_out.failed",
        status=resp.status_code,
        body=_safe_json(resp),
    )
    raise AuthProviderError(f"Supabase sign_out failed ({resp.status_code})")
```

**Login handler change (`app/modules/auth/router.py`):**
```python
tokens = await auth_provider.sign_in_with_password(req.email, req.password)
payload = verify_access_token(tokens.access_token)

if not payload.tenant_id:
    await auth_provider.sign_out(tokens)
    raise HTTPException(403, "This account does not have access to the client dashboard.")

user = await _load_user(db, payload.user_id)
if not user.is_active:
    await auth_provider.sign_out(tokens)
    raise HTTPException(403, "This account has been deactivated.")
```

If `sign_out` itself raises, the 403 still fires — log the failure, do not propagate the revocation error to the client. (The original auth failure is the user-facing error.) This is implemented via `try/except AuthProviderError: logger.warning(...)` around the `sign_out` call.

**Schema bound (`app/modules/auth/schemas.py`):**
```python
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
```

**Tests (`tests/test_auth_login.py`):**
- Add: deactivated-user branch invokes `provider.sign_out`. Use a mock `AuthProvider` via dependency override; assert `sign_out` was called with the tokens returned by `sign_in_with_password`.
- Add: missing-tenant_id branch invokes `provider.sign_out`. Same mock assertion.
- Add: `sign_out` raises `AuthProviderError` → handler still returns 403 (revocation failure does not unbreak the auth failure).
- Update existing `test_login_rejects_missing_fields` to cover password-over-128-chars (422).

### 6.8 C8 — Test gaps + `renderWithProviders` harness

**New harness (`tests/_utils/render.tsx`):**
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, type RenderOptions } from "@testing-library/react"
import type { ReactElement } from "react"

export function renderWithProviders(ui: ReactElement, opts?: RenderOptions) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    opts,
  )
}
```

`retry: false` so failed queries don't retry forever in jsdom. `gcTime: 0` so each test gets a fresh cache.

**C8.1 — MembersSection cancel-path** (`tests/components/members-section-cancel-path.test.tsx`):
Render `<MembersSection unitId="..." />` with mocked `apiFetch`. Click `×` on a role chip → `<DangerConfirmDialog>` opens → click Cancel → assert `removeMutation` was NOT called and dialog is closed.

**C8.2 — org-units `client_account` flow** (`tests/components/org-units-client-account-flow.test.tsx`):
Render the org-units index page. Set workspace mode to `agency` (mock). Trigger create-org-unit form → choose `client_account` type → assert `<CompanyProfileDialog>` opens → submit → assert `doCreate(profile)` is called with the profile.

**C8.3 — composition: no nested forms** (`tests/composition/company-profile-detail-no-nested-forms.test.tsx`):
```tsx
it("CompanyProfileDetail with MembersSection has no nested <form> elements", async () => {
  const { container } = renderWithProviders(<CompanyProfileDetail unitId="u1" {...minimalProps} />)
  await waitFor(() => expect(screen.getByText(/Members & Roles/)).toBeInTheDocument())
  for (const form of container.querySelectorAll("form")) {
    expect(form.querySelector("form")).toBeNull()
  }
})
```

This test is expected to PASS on main as of commit `6fd14d0` (the nested-form bug was fixed pre-merge). Its purpose is regression prevention: if a future change re-introduces the wrap, this test fails immediately.

---

## 7. File deltas

**New files (frontend):**
```
components/px/DangerConfirmDialog.tsx
components/dashboard/jd-panels/JDReviewShell.tsx
components/dashboard/jd-panels/SectionsRail.tsx
components/dashboard/jd-panels/SignalsCanvas.tsx
components/dashboard/jd-panels/SignalInspector.tsx
components/dashboard/jd-panels/FullJdCanvas.tsx
components/dashboard/jd-panels/components/{SignalRow,SignalGroup,CanvasHeader,TabStrip,Confidence,SourceBadge,SnippetHighlighted,InspectorHint,InspectorTips,InspectorAction,Kbd,EmptyRow}.tsx
components/dashboard/jd-panels/helpers/{suggestQuestions,groupSignals,findSnippet,needsReview,weightToConfidence}.ts
components/dashboard/jd-panels/index.ts
tests/_utils/render.tsx
tests/components/members-section-cancel-path.test.tsx
tests/components/org-units-client-account-flow.test.tsx
tests/composition/company-profile-detail-no-nested-forms.test.tsx
tests/api/apply-api-error-to-form.test.ts        (new tests added to existing file)
```

**Modified (frontend):**
```
app/(dashboard)/jobs/[jobId]/page.tsx                              (1658 → <200 LOC)
components/dashboard/pipeline/TemplatePickerDialog.tsx             (a11y + tokens)
components/dashboard/pipeline/PipelineFlowColumn.tsx               (tokens)
components/dashboard/pipeline/StageInspectorPanel.tsx              (tokens)
components/dashboard/pipeline/StageConfigDrawer.tsx                (tokens)
components/dashboard/pipeline/UnifiedPipelineView.tsx              (confirm→DangerConfirmDialog)
components/dashboard/pipeline/JobPipelineFunnel.tsx                (×2 confirm→DangerConfirmDialog)
components/dashboard/question-bank/QuestionCard.tsx                (×2 confirm→DangerConfirmDialog)
app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx (confirm→DangerConfirmDialog)
app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx     (real useTeamMembers; DangerConfirmDialog)
app/(dashboard)/settings/team/page.tsx                             (bespoke ConfirmDialog → DangerConfirmDialog)
app/onboarding/page.tsx                                            (onError wiring)
components/dashboard/company-profile-form.tsx                      (onError prop)
lib/api/errors.ts                                                  (stripPrefixes opt)
lib/hooks/use-job-status-stream.ts                                 (isStreaming init false)
components/px/index.ts                                             (export DangerConfirmDialog)
```

**Modified (backend):**
```
app/modules/auth/admin/base.py             (sign_out method on protocol)
app/modules/auth/admin/supabase.py         (sign_out impl)
app/modules/auth/router.py                 (call sign_out in both 403 branches; revocation-failure tolerant)
app/modules/auth/schemas.py                (LoginRequest.password Field bound)
tests/test_auth_login.py                   (extend for sign_out branches + length test)
```

---

## 8. Verification gates

| Cluster | Gate |
|---|---|
| Pre-flight | tsc 0 errors, lint 0 errors (21 pre-existing warnings ok), 87/87 vitest, `next build` clean, backend pytest 498 passed (4 deselect flags from B3/B4 still apply) |
| C1 | `wc -l app/(dashboard)/jobs/[jobId]/page.tsx` < 200; vitest green; manual JD review smoke (load page, switch tabs, edit signal, confirm, re-enrich) |
| C2 | Keyboard-only smoke on TemplatePickerDialog (Tab traps, ESC closes, focus restores); arrow-keys move tabs |
| C3 | `for f in <4 files>; do grep -cE ...; done` returns 0 per file (or annotated TODOs only); pixel-diff side-by-side smoke on pipeline + JD review + template picker |
| C4 | Each Dialog manually exercised: Cancel dismisses, Confirm fires mutation, dialog stays open on error; `grep -rn "confirm(" components/dashboard/pipeline/ components/dashboard/question-bank/ app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/` returns 0 matches |
| C5 | (1) Kill backend mid-stream, watch SSE retry once, polling kicks in immediately; (2) MembersSection still loads users; (3,5) team + MembersSection danger-confirms work; (4) onboarding submit with bad website surfaces field-level error |
| C6 | New tests pass; existing 14 callers verified by full vitest suite |
| C7 | Backend pytest passes including new tests; manual login flows: (a) valid user 200 + tokens, (b) deactivated user 403 + Supabase token-log shows revoked, (c) over-128-char password 422, (d) Supabase outage during sign_out → still returns 403 (logged warning) |
| C8 | Composition test fails on temporary nested-form re-introduction (negative-control verification before commit); cancel test fails if cancel button calls mutation; client_account test fails if dialog flow regresses |

**Whole-batch final gates:**
- Frontend: tsc 0 errors, lint 0 errors, all vitest green (existing 87 + new ~5 = ~92), `next build` clean
- Backend: pytest 498+new pass with same 4 deselect flags
- Manual smokes: JD review, pipeline page, template picker, login (valid + invalid + deactivated), onboarding, team-page deactivate, org-units member-remove, question-card delete

---

## 9. Risk register

| Risk | Mitigation |
|---|---|
| C1 decomposition breaks signals editing flow | Move tests with their components if they exist; vitest after each leaf extraction; manual smoke before commit; `key={snapshot.version}` remount preserved on JDReviewShell |
| `DangerConfirmDialog` is wrong abstraction | All 7 callsites have identical shape; if a future need diverges (typed-confirmation, multi-step), grow the primitive or accept the bypass |
| C7 `sign_out` failure leaks tokens silently | Failure is logged (`auth.admin.sign_out.failed`) at error level; the 403 still fires for the user; observability has the signal for ops |
| `sign_out` adds latency to 403 path | Acceptable — that path is rare and already failing; revocation is a single HTTP call with 10s timeout |
| `stripPrefixes` change breaks existing 14 hook callers | Default `["body"]` unchanged; existing tests cover identical path; new tests verify default + override separately |
| `onError` prop breaks other `CompanyProfileForm` consumers | Optional; default behavior (re-throw) preserved; `[unitId]/CompanyProfileDetail` already maps errors at page level — unaffected |
| C8.3 composition test pattern doesn't generalize to JDReviewShell | `renderWithProviders` is sized for the common case; if JDReviewShell needs additional provider context, extend the harness rather than duplicating |

---

## 10. CLAUDE.md gate notes

C7 touches `app/modules/auth/{router.py, admin/base.py, admin/supabase.py, schemas.py}` — all under the **"Human Review Required For: Any change to `app/modules/auth/`"** gate per `backend/nexus/CLAUDE.md`.

Whole-batch reviewer must specifically scrutinize:
- `sign_out` boundary on the `AuthProvider` protocol — does it port cleanly to Cognito (`RevokeToken` takes refresh token but `SessionTokens` includes both) and Keycloak (`/logout` endpoint)?
- The two login-handler 403 branches — is revocation failure truly tolerant (logged, not propagated)? Is the order correct (verify access, then sign_out, then raise)?
- `LoginRequest.password` 128-char bound — is this a DoS surface today (no, since FastAPI's request size limits cap before this), and is 128 chars a reasonable user-facing limit (yes — well above common password-manager output, well below DoS thresholds)?

C1 touches `app/(dashboard)/jobs/[jobId]/page.tsx` which is NOT under any auth/middleware gate. Split review applied for size + behavioral coverage of signal-editing flow.

C2 + C6 split review for behavioral semantics (a11y compliance, error-mapping correctness).

---

## 11. Out of scope (recap from §2)

- Question-editor UI wiring
- Pipeline SSE pub/sub migration
- `CompanyProfileDetail` `form.watch` perf
- Phase 3 work (LiveKit, candidate session, scheduler, analysis, reporting)
- CSP wiring
- Dark mode
- The `proxy.ts` + `(dashboard)/layout.tsx` SSR `getSession()` calls
- 13 other pipeline files with raw `zinc-*` classes (future sweep)

If during execution you find yourself wanting to wire any of these — STOP, flag in the report, move on.

---

## 12. Implementation Plan

After this design is approved, the writing-plans skill will produce `docs/superpowers/plans/2026-04-24-cleanup-batch-5-6-final.md` per the cluster + sequencing structure here. Estimated 25-35 commits across 8 clusters; C1 is the only heavy lift, the rest mechanical or surgical.

Execution via `superpowers:subagent-driven-development` with per-cluster review cadence per §4 matrix; whole-batch Opus reviewer before merge to main.
