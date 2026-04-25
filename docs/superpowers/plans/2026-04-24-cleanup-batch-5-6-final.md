# Cleanup Batch 5+6 — Final-Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the original Batch 5 scope (component decomposition + a11y + design tokens from spec §9) folded together with the Tier-2 polish items accumulated across B1–B4 reviews. After this batch lands, the 2026-04-24 cleanup spec is fully done.

**Architecture:** A small shared primitive (`<DangerConfirmDialog>`) lands first to unblock the destructive-confirm migrations. The largest single migration is `app/(dashboard)/jobs/[jobId]/page.tsx` (1,658 LOC → <200 LOC) decomposed into a `components/dashboard/jd-panels/` directory tree with nested `components/` and `helpers/` subdirs and an `index.ts` re-exporting `JDReviewShell` only. Backend hardening adds a single new method (`AuthProvider.sign_out`) called from all four minted-token auth-failure branches in the login handler. All frontend cleanups preserve current behavior at the boundary; all backend changes preserve the provider-agnostic auth abstraction.

**Tech Stack:** Frontend — Next.js 16 (App Router), React 19, TypeScript strict, `react-hook-form`, `@hookform/resolvers/zod`, `zod`, `@tanstack/react-query` v5, `@microsoft/fetch-event-source`, Vitest + @testing-library/react + jsdom. Backend — FastAPI, Python 3.12, pydantic v2, `httpx`, pytest.

---

## Design notes

The design spec at `docs/superpowers/specs/2026-04-25-cleanup-batch-5-6-final-design.md` locks 6 conventions during brainstorming:

- **D5.1 — jd-panels boundaries.** Nested `components/` + `helpers/` subdirs. One component per file. `index.ts` re-exports `JDReviewShell` only. Pure helpers have no JSX.
- **D5.2 — `confirm()` → Dialog pattern.** Shared `<DangerConfirmDialog>` primitive. All 7 callsites use it (6 in C4 + 1 in C5.3 + MembersSection in C5.5).
- **D5.3 — `applyApiErrorToForm` strip-prefix.** `stripPrefixes?: string[]` opt, default `["body"]`. Greedy front-strip. No `locTransform` hook.
- **D5.4 — `AuthProvider.sign_out` shape.** `sign_out(tokens: SessionTokens)`. Idempotent. Called in all 4 minted-token auth-failure branches.
- **D5.5 — Onboarding error mapping.** Optional `onError?: (err, form) => void` prop on `CompanyProfileForm`. Onboarding's wrapper at lines 420-431 is REMOVED.
- **D5.6 — Composition test harness.** `tests/_utils/render.tsx` exports `renderWithProviders(ui)`. Used by C8.1, C8.2, C8.3.

Per the spec, C7 triggers the backend CLAUDE.md "Human Review Required For: Any change to `app/modules/auth/`" gate. C1 + C2 + C6 are split-review (load-bearing or behavioral); C3, C4, C5, C8 are combined review.

---

## File Structure

### Frontend — new files

| File | Role |
|---|---|
| `frontend/app/components/px/DangerConfirmDialog.tsx` | NEW — shared destructive-confirm dialog primitive |
| `frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx` | NEW — root shell for the JD review page |
| `frontend/app/components/dashboard/jd-panels/SectionsRail.tsx` | NEW — left navigation rail |
| `frontend/app/components/dashboard/jd-panels/SignalsCanvas.tsx` | NEW — signals tab body |
| `frontend/app/components/dashboard/jd-panels/SignalInspector.tsx` | NEW — right-side signal editor |
| `frontend/app/components/dashboard/jd-panels/FullJdCanvas.tsx` | NEW — JD source view |
| `frontend/app/components/dashboard/jd-panels/components/SignalRow.tsx` | NEW — signal row as `<button>` (a11y) |
| `frontend/app/components/dashboard/jd-panels/components/SignalGroup.tsx` | NEW — group container |
| `frontend/app/components/dashboard/jd-panels/components/CanvasHeader.tsx` | NEW — canvas header |
| `frontend/app/components/dashboard/jd-panels/components/TabStrip.tsx` | NEW — inner-tab strip with role="tablist" |
| `frontend/app/components/dashboard/jd-panels/components/Confidence.tsx` | NEW — confidence chip |
| `frontend/app/components/dashboard/jd-panels/components/SourceBadge.tsx` | NEW — source badge |
| `frontend/app/components/dashboard/jd-panels/components/SnippetHighlighted.tsx` | NEW — JD snippet with highlight |
| `frontend/app/components/dashboard/jd-panels/components/InspectorHint.tsx` | NEW — inspector hint card |
| `frontend/app/components/dashboard/jd-panels/components/InspectorTips.tsx` | NEW — keyboard tips |
| `frontend/app/components/dashboard/jd-panels/components/InspectorAction.tsx` | NEW — inspector action button |
| `frontend/app/components/dashboard/jd-panels/components/Kbd.tsx` | NEW — keyboard-key indicator |
| `frontend/app/components/dashboard/jd-panels/components/EmptyRow.tsx` | NEW — empty-state row |
| `frontend/app/components/dashboard/jd-panels/helpers/suggestQuestions.ts` | NEW — suggest-questions pure helper |
| `frontend/app/components/dashboard/jd-panels/helpers/groupSignals.ts` | NEW — signal-grouping helper |
| `frontend/app/components/dashboard/jd-panels/helpers/findSnippet.ts` | NEW — snippet-finding helper |
| `frontend/app/components/dashboard/jd-panels/helpers/needsReview.ts` | NEW — needs-review predicate |
| `frontend/app/components/dashboard/jd-panels/helpers/weightToConfidence.ts` | NEW — weight-to-confidence converter |
| `frontend/app/components/dashboard/jd-panels/index.ts` | NEW — re-exports `JDReviewShell` only |
| `frontend/app/tests/_utils/render.tsx` | NEW — `renderWithProviders` harness |
| `frontend/app/tests/components/members-section-cancel-path.test.tsx` | NEW — C8.1 |
| `frontend/app/tests/components/org-units-client-account-flow.test.tsx` | NEW — C8.2 |
| `frontend/app/tests/composition/company-profile-detail-no-nested-forms.test.tsx` | NEW — C8.3 |

### Frontend — modified files

| File | What changes |
|---|---|
| `frontend/app/components/px/index.ts` | Export `DangerConfirmDialog` |
| `frontend/app/lib/api/errors.ts` | Add `stripPrefixes?: string[]` opt to `applyApiErrorToForm` |
| `frontend/app/tests/api/apply-api-error-to-form.test.ts` | EXTEND — new cases for strip-prefix override |
| `frontend/app/lib/hooks/use-job-status-stream.ts` | `useState(true)` → `useState(false)` (C5.1) |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx` | Replace inline `useQuery` with `useTeamMembers`; replace inline `<Dialog>` with `<DangerConfirmDialog>` (C5.2 + C5.5) |
| `frontend/app/components/dashboard/company-profile-form.tsx` | Add optional `onError?: (err, form) => void` prop (C5.4) |
| `frontend/app/app/onboarding/page.tsx` | Remove submit wrapper at lines 420-431; pass `onError` to `<CompanyProfileForm>` (C5.4) |
| `frontend/app/app/(dashboard)/settings/team/page.tsx` | Replace bespoke `ConfirmDialog` with `<DangerConfirmDialog>` (C5.3) |
| `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx` | Swap `<div role="dialog">` for `px/Dialog`; tab strip → `role="tablist"` + `role="tab"` + `aria-selected`; tokens (C2 + C3) |
| `frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx` | Tokens (C3) |
| `frontend/app/components/dashboard/pipeline/StageInspectorPanel.tsx` | Tokens (C3) |
| `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx` | Tokens (C3) |
| `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx` | `confirm()` → `<DangerConfirmDialog>` at line 298 (C4) |
| `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx` | `confirm()` → `<DangerConfirmDialog>` at lines 383 + 1346 (C4) |
| `frontend/app/components/dashboard/question-bank/QuestionCard.tsx` | `confirm()` → `<DangerConfirmDialog>` at lines 125 + 142 (C4) |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx` | `confirm()` → `<DangerConfirmDialog>` at line 44 (C4) |
| `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` | DECOMPOSE — 1,658 LOC → <200 LOC; embedded `confirm()` at 1412 → `<DangerConfirmDialog>` (C1) |

### Backend — modified files

| File | What changes |
|---|---|
| `backend/nexus/app/modules/auth/admin/base.py` | Add `sign_out(tokens: SessionTokens) -> None` to `AuthProvider` protocol |
| `backend/nexus/app/modules/auth/admin/supabase.py` | Implement `SupabaseAuthProvider.sign_out` |
| `backend/nexus/app/modules/auth/router.py` | Add `_revoke_quietly` helper; call it in all 4 minted-token auth-failure branches |
| `backend/nexus/app/modules/auth/schemas.py` | `LoginRequest.password` gets `Field(min_length=1, max_length=128)` |
| `backend/nexus/tests/test_auth_login.py` | Extend with parametrized 4-branch sign_out tests + revocation-tolerance test + length-bound test |

---

## Pre-flight

### Task 0: Worktree, env, baseline gates

**Files:** none (setup only)

- [ ] **Step 1: Create the worktree from main**

```bash
cd /home/ishant/Projects/ProjectX
git worktree add .worktrees/cleanup-batch-5-6 -b cleanup/batch-5-6-final main
cd .worktrees/cleanup-batch-5-6
git status   # expect clean tree on cleanup/batch-5-6-final
```

- [ ] **Step 2: Copy gitignored env files into the worktree**

```bash
cp /home/ishant/Projects/ProjectX/frontend/app/.env.local frontend/app/.env.local
cp /home/ishant/Projects/ProjectX/backend/nexus/.env backend/nexus/.env
```

- [ ] **Step 3: Verify Supabase local stack is running**

```bash
docker ps | grep supabase_db_backend
# Expected: a running container named supabase_db_backend (or similar)
```

If not running, start it from the supabase project: `cd /home/ishant/Projects/ProjectX/backend/supabase && supabase start`.

- [ ] **Step 4: Bring up the backend redis service in this worktree**

```bash
cd backend/nexus
docker compose up -d redis
```

- [ ] **Step 5: Backend pytest baseline (498 passed / 4 deselected)**

```bash
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
```

Expected: `498 passed, 4 deselected`. If anything else, STOP — investigate before proceeding.

- [ ] **Step 6: Frontend baseline gates**

```bash
cd ../../frontend/app
npm install     # in case the worktree's node_modules is missing/stale
npx tsc --noEmit
npm run lint
npm run test
npm run build
```

Expected:
- `tsc`: 0 errors
- `lint`: 0 errors, 21 pre-existing warnings ok
- `npm run test`: `Test Files 24 passed, Tests 87 passed`
- `npm run build`: clean

If anything else, STOP.

- [ ] **Step 7: Confirm worktree state and record baseline**

```bash
cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-5-6
git status      # clean
git log --oneline -3
```

Worktree is ready. Proceed to Phase 1.

---

## Phase 1 — Foundation: `DangerConfirmDialog` primitive

This primitive blocks Wave B (C4 callsites, C5.3, C5.5) and the embedded `confirm()` at line 1412 of `jobs/[jobId]/page.tsx` (C1). Build it first.

### Task 1: Create `DangerConfirmDialog` and export it

**Files:**
- Create: `frontend/app/components/px/DangerConfirmDialog.tsx`
- Modify: `frontend/app/components/px/index.ts`
- Test: `frontend/app/tests/components/danger-confirm-dialog.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/danger-confirm-dialog.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DangerConfirmDialog } from '@/components/px'

describe('DangerConfirmDialog', () => {
  it('renders title, description, and labelled buttons when open', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText('Delete item')).toBeInTheDocument()
    expect(screen.getByText('Are you sure?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument()
  })

  it('does not render anything when closed', () => {
    render(
      <DangerConfirmDialog
        open={false}
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByText('Delete item')).not.toBeInTheDocument()
  })

  it('Cancel calls onClose, NOT onConfirm', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('Confirm calls onConfirm, NOT onClose (parent decides when to close)', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('disables both buttons while pending and shows pendingLabel on confirm', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    const confirmBtn = screen.getByRole('button', { name: 'Deleting…' })
    expect(confirmBtn).toBeDisabled()
  })

  it('falls back to "{confirmLabel}…" when no pendingLabel given', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Remove"
        pending
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Remove…' })).toBeInTheDocument()
  })

  it('renders ReactNode descriptions for interpolated content', () => {
    render(
      <DangerConfirmDialog
        open
        title="Remove role"
        description={<>Remove <strong>Hiring Manager</strong> from this user?</>}
        confirmLabel="Remove role"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText('Hiring Manager')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
cd frontend/app
npx vitest run tests/components/danger-confirm-dialog.test.tsx
```

Expected: import error — `DangerConfirmDialog` not exported.

- [ ] **Step 3: Implement the primitive**

Create `frontend/app/components/px/DangerConfirmDialog.tsx`:

```tsx
"use client";

import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./Dialog";

export interface DangerConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  pending?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

/**
 * Destructive-confirmation dialog. Stays open while `pending` is true so
 * the consumer can show in-flight state and keep the dialog open on
 * mutation error. Parent must explicitly call `onClose()` after a
 * successful mutation.
 *
 * Used for every "are you sure?" destructive action in the app.
 */
export function DangerConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pendingLabel,
  pending = false,
  onConfirm,
  onClose,
}: DangerConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-btn ghost sm"
            disabled={pending}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              void onConfirm();
            }}
            disabled={pending}
            className="px-btn destructive sm"
          >
            {pending ? (pendingLabel ?? `${confirmLabel}…`) : confirmLabel}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Export from `components/px/index.ts`**

Add this line in alphabetical position:

```ts
export { DangerConfirmDialog } from "./DangerConfirmDialog";
export type { DangerConfirmDialogProps } from "./DangerConfirmDialog";
```

- [ ] **Step 5: Run the test, verify it passes**

```bash
npx vitest run tests/components/danger-confirm-dialog.test.tsx
```

Expected: 7 passed.

- [ ] **Step 6: Run full vitest + tsc + lint**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: tsc 0 errors, lint 0 new errors, all tests pass (87 + 7 new = 94).

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/px/DangerConfirmDialog.tsx \
        frontend/app/components/px/index.ts \
        frontend/app/tests/components/danger-confirm-dialog.test.tsx
git commit -m "$(cat <<'EOF'
feat(px): add DangerConfirmDialog primitive

Shared destructive-confirm dialog: title + ReactNode description +
Cancel + destructive Confirm button + optional pending state.

Stays open while pending=true so the consumer can show in-flight state
and keep the dialog open on mutation error. Parent calls onClose() on
success.

Replaces 7 ad-hoc destructive-confirm patterns in upcoming commits:
- 6 confirm() callsites in pipeline + question-bank + org-units
- 1 bespoke ConfirmDialog in settings/team
- MembersSection's inline Dialog from B4
EOF
)"
```

---

## Phase 2 — Wave A (parallel-safe surgical fixes)

These tasks are independent of each other and the primitive (except where noted). They can be implemented in any order.

### Task 2: C5.1 — `useJobStatusStream.isStreaming` initial state

**Files:**
- Modify: `frontend/app/lib/hooks/use-job-status-stream.ts:58`
- Test: existing tests in `frontend/app/tests/` cover this hook indirectly; the manual smoke is the verification.

**Why:** Current `useState(true)` suppresses fallback polling for the entire initial-failure window. Initializing `false` lets polling kick in immediately on initial mount; `setIsStreaming(true)` at line 64 (already there, fires on each connection attempt) takes over once SSE is actually connecting.

- [ ] **Step 1: Apply the change**

```ts
// line 58 — was:
const [isStreaming, setIsStreaming] = useState(true)
// becomes:
const [isStreaming, setIsStreaming] = useState(false)
```

- [ ] **Step 2: Verify gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 3: Manual smoke (capture intent in commit, no automated test)**

Boot the dev server (`npm run dev`), visit `/jobs/<some-id>`. The page should load identically to before.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/lib/hooks/use-job-status-stream.ts
git commit -m "$(cat <<'EOF'
fix(hooks): isStreaming starts false to allow initial-failure polling

Initializing isStreaming=true suppressed React Query's fallback polling
for the entire window before SSE connected. On a slow network or a
backend that's down at mount time, the user saw stale data with no
indication anything was wrong.

Init false; the existing setIsStreaming(true) at line 64 still flips
on connection attempt, so the steady-state behaviour is unchanged.
EOF
)"
```

### Task 3: C6 — `applyApiErrorToForm` strip-prefix opt + tests

**Files:**
- Modify: `frontend/app/lib/api/errors.ts`
- Modify: `frontend/app/tests/api/apply-api-error-to-form.test.ts` (extend)

- [ ] **Step 1: Write the new failing tests**

Append to `frontend/app/tests/api/apply-api-error-to-form.test.ts` inside the existing `describe('applyApiErrorToForm', ...)` block:

```ts
  it('strips a custom prefix list (greedy) when stripPrefixes is provided', () => {
    const form = makeForm({ website: '' })
    const err = makeError([
      { loc: ['body', 'metadata', 'website'], msg: 'invalid url', type: 'value_error' },
    ])
    const result = applyApiErrorToForm(err, form, {
      stripPrefixes: ['body', 'metadata'],
    })
    expect(result).toBe(true)
    expect(form.formState.errors.website?.message).toBe('invalid url')
  })

  it('default behaviour (no stripPrefixes) only strips body — preserves dotted nested path', () => {
    const form = makeForm({ metadata: { website: '' } })
    const err = makeError([
      { loc: ['body', 'metadata', 'website'], msg: 'invalid url', type: 'value_error' },
    ])
    const result = applyApiErrorToForm(err, form)
    expect(result).toBe(true)
    expect(form.formState.errors.metadata?.website?.message).toBe('invalid url')
  })

  it('greedy strip — multiple consecutive matches are all removed', () => {
    const form = makeForm({ x: '' })
    const err = makeError([
      { loc: ['body', 'body', 'x'], msg: 'oops', type: 'value_error' },
    ])
    const result = applyApiErrorToForm(err, form, { stripPrefixes: ['body'] })
    expect(result).toBe(true)
    expect(form.formState.errors.x?.message).toBe('oops')
  })

  it('stops stripping at the first non-matching segment', () => {
    const form = makeForm({ x: '' })
    const err = makeError([
      { loc: ['body', 'x'], msg: 'oops', type: 'value_error' },
    ])
    const result = applyApiErrorToForm(err, form, {
      stripPrefixes: ['body', 'metadata'],
    })
    // body matches first prefix → stripped. Next segment is "x", not "metadata" → stop.
    expect(result).toBe(true)
    expect(form.formState.errors.x?.message).toBe('oops')
  })
```

If `makeForm` and `makeError` helpers don't already exist in the file, lift them inline by reading lines 1-53 of the existing test file and reusing the existing helpers as-is.

- [ ] **Step 2: Run tests, verify NEW tests fail and OLD tests still pass**

```bash
npx vitest run tests/api/apply-api-error-to-form.test.ts
```

Expected: 7 existing tests pass, 4 new tests fail (because `stripPrefixes` opt is not yet implemented).

- [ ] **Step 3: Implement `stripPrefixes` in `errors.ts`**

Replace `frontend/app/lib/api/errors.ts` contents with:

```ts
import type { FieldValues, Path, UseFormReturn } from 'react-hook-form'

import { ApiValidationError } from './client'

/**
 * Apply a thrown error to a react-hook-form instance.
 *
 * Returns `true` if at least one field-level error was set (caller
 * suppresses the toast). Returns `false` for any error shape that is
 * not an `ApiValidationError` — caller falls back to a generic toast
 * or form-level error.
 *
 * Loc handling:
 * - FastAPI prepends `"body"` to every `loc`. Default `stripPrefixes`
 *   is `["body"]` — that segment is dropped greedily from the front of
 *   each loc.
 * - Pass `stripPrefixes: ["body", "metadata"]` (or any other segments)
 *   when the backend nests the request body under a key the frontend
 *   form does NOT mirror. Stripping is greedy: consecutive segments
 *   that match any string in the list are all removed.
 * - The remaining segments are joined with `.` to produce an RHF path
 *   (e.g. `["profile", "about"]` → `"profile.about"`).
 * - If the resulting path is not a known field on the form, the error
 *   falls back to `opts.fallbackFieldKey` (if provided) or `root`.
 */
export function applyApiErrorToForm<T extends FieldValues>(
  err: unknown,
  form: UseFormReturn<T>,
  opts: { fallbackFieldKey?: Path<T>; stripPrefixes?: string[] } = {},
): boolean {
  if (!(err instanceof ApiValidationError)) return false

  const stripPrefixes = opts.stripPrefixes ?? ['body']
  const knownFieldKeys = collectFieldKeys(form.getValues())
  let mappedAny = false

  for (const entry of err.fieldErrors) {
    const path = locToPath(entry.loc, stripPrefixes)
    if (path && knownFieldKeys.has(path)) {
      form.setError(path as Path<T>, { message: entry.msg, type: 'server' })
      mappedAny = true
      continue
    }
    if (opts.fallbackFieldKey) {
      form.setError(opts.fallbackFieldKey, {
        message: entry.msg,
        type: 'server',
      })
      mappedAny = true
      continue
    }
    form.setError('root' as Path<T>, { message: entry.msg, type: 'server' })
    mappedAny = true
  }

  return mappedAny
}

/**
 * Greedily drop leading `stripPrefixes` segments, then join the rest
 * with `.`. Returns null for shapes we don't recognise (e.g. empty
 * after strip).
 */
function locToPath(
  loc: (string | number)[],
  stripPrefixes: string[],
): string | null {
  let stripped = loc
  while (
    stripped.length > 0 &&
    typeof stripped[0] === 'string' &&
    stripPrefixes.includes(stripped[0] as string)
  ) {
    stripped = stripped.slice(1)
  }
  if (stripped.length === 0) return null
  return stripped.map((seg) => String(seg)).join('.')
}

/**
 * Walk the form's current values to collect every valid dotted path.
 * Used to decide whether a server `loc` maps to a known field or
 * should fall through to the fallback slot.
 */
function collectFieldKeys(values: unknown, prefix = ''): Set<string> {
  const keys = new Set<string>()
  if (values === null || typeof values !== 'object' || Array.isArray(values)) {
    return keys
  }
  for (const [k, v] of Object.entries(values as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k
    keys.add(path)
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      for (const nested of collectFieldKeys(v, path)) keys.add(nested)
    }
  }
  return keys
}
```

- [ ] **Step 4: Run all tests in this file, verify all pass**

```bash
npx vitest run tests/api/apply-api-error-to-form.test.ts
```

Expected: 11 passed (7 existing + 4 new).

- [ ] **Step 5: Run full vitest + tsc**

```bash
npx tsc --noEmit
npm run test
```

Expected: clean. The 14 existing consumers of `applyApiErrorToForm` continue working because the default (`["body"]`) preserves prior behavior.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/errors.ts \
        frontend/app/tests/api/apply-api-error-to-form.test.ts
git commit -m "$(cat <<'EOF'
feat(api): applyApiErrorToForm gets stripPrefixes opt

FastAPI nests loc under "body" for request-body validation errors. When
the backend's Pydantic model has nested models (e.g. metadata.website)
but the frontend form is flat (just website), the existing loc-to-path
mapping produces a dotted path that doesn't match the form, falls
through to root, and the user gets a generic "form invalid" instead of
the field-level error.

stripPrefixes lets the caller drop additional segments greedily from
the front. Default ["body"] preserves all existing behavior; the 14
existing consumers are unchanged.

Used in C5.4 by onboarding to map ["body","metadata","website"] →
"website" against the flat CompanyProfileForm.
EOF
)"
```

### Task 4: C5.4 — `CompanyProfileForm` `onError` prop + onboarding wiring

**Files:**
- Modify: `frontend/app/components/dashboard/company-profile-form.tsx`
- Modify: `frontend/app/app/onboarding/page.tsx`
- Test: existing onboarding tests cover the form contract; add one new test for `onError` delegation.

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/components/company-profile-form-on-error.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { CompanyProfileForm } from '@/components/dashboard/company-profile-form'

const VALID_VALUES = {
  about:
    'We build distributed log processing for real-time analytics at petabyte scale.',
  industry: 'saas_enterprise_software',
  company_stage: 'series_a_b',
  hiring_bar:
    'Pragmatic engineers comfortable with ambiguity and operational ownership.',
}

function fillValid() {
  fireEvent.change(screen.getByLabelText(/What does your company actually build/), {
    target: { value: VALID_VALUES.about },
  })
  fireEvent.change(screen.getByLabelText(/What does a strong hire look like/), {
    target: { value: VALID_VALUES.hiring_bar },
  })
  // Industry and company_stage are Base UI Selects — set via form initialValue
  // to keep this test focused on onError plumbing, not Select interaction.
}

describe('CompanyProfileForm onError prop', () => {
  it('delegates thrown errors to onError when provided (no rethrow)', async () => {
    const error = new Error('boom')
    const onSubmit = vi.fn().mockRejectedValueOnce(error)
    const onError = vi.fn()

    // Provide initialValue so the form is valid on mount and submit fires.
    render(
      <CompanyProfileForm
        initialValue={VALID_VALUES}
        onSubmit={onSubmit}
        onError={onError}
        submitLabel="Save"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1))
    expect(onError.mock.calls[0][0]).toBe(error)
    // Second arg is the form instance — at minimum has setError.
    expect(typeof onError.mock.calls[0][1].setError).toBe('function')
  })

  it('rethrows when onError is not provided (preserves prior behaviour)', async () => {
    const error = new Error('boom')
    const onSubmit = vi.fn().mockRejectedValueOnce(error)

    render(
      <CompanyProfileForm
        initialValue={VALID_VALUES}
        onSubmit={onSubmit}
        submitLabel="Save"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    // Hard to assert on rethrow directly without a runtime crash; verify
    // form state instead — RHF.handleSubmit catches and surfaces via
    // formState.errors.root when the submit handler rejects with no
    // onError. (If a future RHF version logs the rethrow as an unhandled
    // rejection, this test still validates the contract: onError absent →
    // form does not call any user handler beyond onSubmit.)
    // Sufficient: onSubmit was called once, no other behaviour observed.
  })
})
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
npx vitest run tests/components/company-profile-form-on-error.test.tsx
```

Expected: fails because `CompanyProfileForm` does not yet accept `onError`.

- [ ] **Step 3: Add `onError` to `CompanyProfileForm`**

In `frontend/app/components/dashboard/company-profile-form.tsx`:

Replace the `Props` type (around line 62) with:

```tsx
type Props = {
  initialValue?: Partial<CompanyProfile>
  onSubmit: (value: CompanyProfile) => Promise<void>
  /**
   * If provided, errors thrown by `onSubmit` are passed here with the
   * form instance so the parent can call `applyApiErrorToForm(err, form)`.
   * If absent, errors propagate as in prior behaviour.
   */
  onError?: (err: unknown, form: UseFormReturn<CompanyProfile>) => void
  submitLabel?: string
}
```

Add an import for `UseFormReturn` at the top (alongside the existing `useForm` import):

```tsx
import { useForm, type UseFormReturn } from 'react-hook-form'
```

Replace the component body (around lines 68-83) so it uses the wrapper. Concretely, change the `<form>` element's `onSubmit` from:

```tsx
<form
  onSubmit={form.handleSubmit(onSubmit)}
  className="space-y-6 max-w-2xl"
>
```

to:

```tsx
<form
  onSubmit={form.handleSubmit(async (values) => {
    try {
      await onSubmit(values)
    } catch (err) {
      if (onError) {
        onError(err, form)
        return
      }
      throw err
    }
  })}
  className="space-y-6 max-w-2xl"
>
```

The component signature line also needs to accept the new prop:

```tsx
export function CompanyProfileForm({
  initialValue,
  onSubmit,
  onError,
  submitLabel = 'Save Company Profile',
}: Props) {
```

- [ ] **Step 4: Run the new test, verify it passes**

```bash
npx vitest run tests/components/company-profile-form-on-error.test.tsx
```

Expected: 2 passed.

- [ ] **Step 5: Wire `onError` in onboarding**

In `frontend/app/app/onboarding/page.tsx`:

Add imports at the top of the file (next to existing imports):

```tsx
import { applyApiErrorToForm } from '@/lib/api/errors'
```

Replace the wrapper at lines ~419-433 (the `<CompanyProfileForm onSubmit={...}>` block):

```tsx
              <CompanyProfileForm
                onSubmit={handleSubmitProfile}
                onError={(err, form) => {
                  if (
                    applyApiErrorToForm(err, form, {
                      stripPrefixes: ['body', 'metadata'],
                    })
                  ) {
                    return
                  }
                  setProfileError(
                    err instanceof Error
                      ? err.message
                      : 'Failed to save company profile',
                  )
                }}
                submitLabel="Finish Onboarding"
              />
```

The existing `setProfileError` state and the `<p>{profileError}</p>` block stay as the form-level fallback for non-422 errors.

- [ ] **Step 6: Run full gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean. tsc passes (the new `UseFormReturn` import and prop are typed correctly).

- [ ] **Step 7: Manual smoke**

```bash
npm run dev
```

Visit `/onboarding` step 2. Submit a profile that the backend will reject (e.g. about < 30 chars — though Zod blocks this client-side first; for a real 422, force a malformed value via DevTools or temporarily). The form should surface either the field-level error (when stripPrefixes maps successfully) or the form-level setProfileError fallback.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/components/dashboard/company-profile-form.tsx \
        frontend/app/app/onboarding/page.tsx \
        frontend/app/tests/components/company-profile-form-on-error.test.tsx
git commit -m "$(cat <<'EOF'
feat(onboarding): map 422 field errors via onError + applyApiErrorToForm

CompanyProfileForm gets an optional onError prop. When provided, the
form's submit wrapper catches and delegates instead of rethrowing — so
parents can surface field-level errors via applyApiErrorToForm without
needing direct access to the form's RHF instance.

Onboarding's lines 420-431 wrapper (try/setProfileError/rethrow) is
removed in favour of clean delegation. The form-level setProfileError
state stays as the non-422 fallback.

Other CompanyProfileForm consumers (e.g. CompanyProfileDetail in
[unitId]) handle their own try/catch at the page level and are
unaffected — onError defaults to undefined → rethrow is preserved.
EOF
)"
```

### Task 5: C5.2 — `MembersSection` uses real `useTeamMembers`

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx:51-56`

**Why:** The file inlines a `useQuery({queryKey: ['team', 'members'], queryFn: ...})` that's identical to the existing `useTeamMembers` hook from B4. Replace the inline copy.

- [ ] **Step 1: Apply the change**

In `MembersSection.tsx`, replace the imports block (around line 6-11):

```tsx
// Remove these imports if they become unused after this task — verify
// after the swap below:
//   - useQuery from @tanstack/react-query
//   - teamApi, type TeamMember from @/lib/api/team
//   - getFreshSupabaseToken from @/lib/auth/tokens
import { useTeamMembers } from "@/lib/hooks/use-team-members";
```

Replace lines 51-56 (the inline tenantUsersQuery):

```tsx
  const tenantUsersQuery = useTeamMembers();
```

Verify all the unused imports (`useQuery`, `teamApi`, `TeamMember`, `getFreshSupabaseToken`) are removed if no other line in the file references them. The `TeamMember` type may still be referenced in the `tenantUsers` filter expression — check and adjust:

```tsx
  const tenantUsers = useMemo(
    () =>
      (tenantUsersQuery.data ?? []).filter(
        (x) => x.source === "user" && x.is_active,
      ),
    [tenantUsersQuery.data],
  );
```

If TypeScript still infers correctly (the `useTeamMembers` hook returns the same `TeamMember[]` shape), no further change is needed. Otherwise, keep the `TeamMember` import.

- [ ] **Step 2: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean. Tests for MembersSection from B4 still pass — same query key, same staleTime.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/MembersSection.tsx
git commit -m "$(cat <<'EOF'
refactor(MembersSection): use existing useTeamMembers hook

The component inlined a useQuery that was structurally identical to the
useTeamMembers hook landed in B4 — same query key, same staleTime, same
queryFn pattern. Drop the duplicate.

No behavioral change.
EOF
)"
```

### Task 6: C2 — `TemplatePickerDialog` a11y migration

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx`

**Why:** Spec §9.3 (B5.2 + B5.4). Custom `<div role="dialog">` becomes `<Dialog>` from `components/px/Dialog` (which already has focus trap + ESC + scroll lock + focus restore). Tab strip's `aria-pressed` becomes proper `role="tablist"` + `role="tab"` + `aria-selected` semantics with matching `role="tabpanel"`.

- [ ] **Step 1: Read the current file end-to-end**

Open `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx`. Note:
- The `<div role="dialog">` wrapper at line 55 with hand-rolled escape handler / backdrop
- The two `aria-pressed` tab buttons at lines 78 and 86
- The two corresponding panel `<div>`s further down

- [ ] **Step 2: Write a manual a11y verification checklist (commit as inline comment for later removal)**

This is a manual smoke test. Capture in the implementation by visual verification rather than vitest, because focus-trap behaviour with portals + jsdom is fragile.

- [ ] **Step 3: Replace the wrapper with `<Dialog>`**

Imports — add at the top:

```tsx
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/px";
```

Replace the outer `<div role="dialog">` with:

```tsx
<Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
  <DialogContent widthClass="sm:max-w-2xl">
    {/* keep the existing dialog header and body markup, but drop the
        hand-rolled close button + backdrop click handler — DialogContent
        provides both. */}
    <DialogHeader>
      <DialogTitle>Pick a template</DialogTitle>
    </DialogHeader>
    {/* rest of the body unchanged except per Step 4 for the tab strip */}
  </DialogContent>
</Dialog>
```

Remove the hand-rolled escape handler `useEffect` (no longer needed — `DialogContent` handles ESC). Remove the manual focus-restoration `useEffect` (also handled by `DialogContent`). Remove the manual backdrop click handler.

- [ ] **Step 4: Convert the tab strip**

The two tab buttons at lines 78 and 86 become:

```tsx
<div role="tablist" aria-label="Template source" className={/* preserved classes */}>
  <button
    type="button"
    role="tab"
    id="tab-library"
    aria-selected={tab === 'library'}
    aria-controls="panel-library"
    tabIndex={tab === 'library' ? 0 : -1}
    onClick={() => setTab('library')}
    className={/* preserved classes — keep current visual styling */}
  >
    Library
  </button>
  <button
    type="button"
    role="tab"
    id="tab-starters"
    aria-selected={tab === 'starters'}
    aria-controls="panel-starters"
    tabIndex={tab === 'starters' ? 0 : -1}
    onClick={() => setTab('starters')}
    className={/* preserved classes */}
  >
    Starter packs
  </button>
</div>
```

Wrap the corresponding panel divs:

```tsx
{tab === 'library' && (
  <div role="tabpanel" id="panel-library" aria-labelledby="tab-library">
    {/* existing library content */}
  </div>
)}
{tab === 'starters' && (
  <div role="tabpanel" id="panel-starters" aria-labelledby="tab-starters">
    {/* existing starters content */}
  </div>
)}
```

Remove all `aria-pressed=` references from this file.

- [ ] **Step 5: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 6: Manual a11y smoke**

```bash
npm run dev
```

1. Open a job's pipeline tab (any pipeline route that exposes the template picker).
2. Click "Use template" or whatever opens `TemplatePickerDialog`.
3. Press Tab repeatedly — focus must cycle inside the dialog (focus trap).
4. Press Escape — dialog closes, focus returns to the opener button.
5. Click outside on the backdrop — dialog closes.
6. Inside the dialog, click the second tab — `aria-selected=true` moves; the first tab gets `tabIndex=-1`.
7. Inspect with browser devtools / axe-devtools — no violations on tab strip or dialog.

If any of these fails, fix before commit.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx
git commit -m "$(cat <<'EOF'
fix(pipeline/TemplatePickerDialog): use px/Dialog + WAI-ARIA tab semantics

Replaces hand-rolled <div role="dialog"> wrapper with px/Dialog, which
already provides focus trap, ESC handling, scroll lock, and focus
restoration. Drops the local useEffects that duplicated this logic.

Tab strip:
- aria-pressed (toggle-button semantics, wrong) → role="tab" +
  aria-selected (tab semantics, right)
- Wrapping container becomes role="tablist"
- Panels become role="tabpanel" with aria-labelledby pointing at the
  corresponding tab id
- tabIndex=0 on the active tab, -1 on inactive tabs (arrow-key roving
  is not implemented but the structure is correct for a future add)

Closes spec §9 B5.2 + B5.4.
EOF
)"
```

### Task 7: C3 — design-token sweep on `PipelineFlowColumn.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx`

**Why:** Spec §9.4 (B5.5). 6 raw zinc/white refs in this file → `var(--px-*)` style props.

- [ ] **Step 1: List the offending lines**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx
```

Expected: 6 matches.

- [ ] **Step 2: Apply replacements**

Per spec §9.4 visual hierarchy:
- `bg-white` → remove the class; add `style={{ background: 'var(--px-surface)' }}` to the same element.
- `border-zinc-200` → remove the class; add `style={{ borderColor: 'var(--px-hairline)' }}` to the same element.
- `border-zinc-100` → `borderColor: 'var(--px-hairline)'` (closest match — px palette has one hairline shade)
- `border-zinc-300` → `borderColor: 'var(--px-divider)'` (slightly darker — for sectional dividers)
- `text-zinc-400` → `color: 'var(--px-fg-4)'` (mutest)
- `text-zinc-500` → `color: 'var(--px-fg-3)'`
- `text-zinc-600` → `color: 'var(--px-fg-2)'`
- `text-zinc-700` → `color: 'var(--px-fg)'`

If an element already has a `style` prop, MERGE the new style — do not replace. Example:

```tsx
// before:
<div className="bg-white border-zinc-200 rounded-md">
// after:
<div
  className="rounded-md"
  style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
>
```

If a zinc shade has no clean px-token equivalent, leave the class AND add a `// TODO(design-review): no px-token equivalent for zinc-N` comment on the same line. Surface in PR description.

- [ ] **Step 3: Verify zero remaining matches**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx
```

Expected: 0 (or only TODO-annotated lines).

- [ ] **Step 4: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 5: Manual visual smoke**

```bash
npm run dev
```

Open the pipeline page (`/jobs/<id>/pipeline`). Compare side-by-side with main: layout, colors, hover states, dragging affordances must look identical.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/PipelineFlowColumn.tsx
git commit -m "$(cat <<'EOF'
chore(pipeline/PipelineFlowColumn): replace raw zinc/white classes with px-* tokens

Spec §9 B5.5 — 6 refs total in this file. Visual layer goes through
the px design tokens defined in app/globals.css instead of Tailwind's
default zinc palette. No visual change intended.
EOF
)"
```

### Task 8: C3 — design-token sweep on `StageInspectorPanel.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageInspectorPanel.tsx`

**Why:** Spec §9.4 (B5.5). 4 raw zinc/white refs in this file → `var(--px-*)` style props.

- [ ] **Step 1: List the offending lines**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/StageInspectorPanel.tsx
```

Expected: 4 matches.

- [ ] **Step 2: Apply replacements**

Replacement table (per spec §9.4):

| From | To |
|---|---|
| `bg-white` | `style={{ background: 'var(--px-surface)' }}` |
| `border-zinc-100` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-200` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-300` | `style={{ borderColor: 'var(--px-divider)' }}` |
| `text-zinc-400` | `style={{ color: 'var(--px-fg-4)' }}` |
| `text-zinc-500` | `style={{ color: 'var(--px-fg-3)' }}` |
| `text-zinc-600` | `style={{ color: 'var(--px-fg-2)' }}` |
| `text-zinc-700` | `style={{ color: 'var(--px-fg)' }}` |

If an element already has a `style` prop, MERGE; do not replace. If a zinc shade has no clean px-token equivalent, leave the class AND add `// TODO(design-review): no px-token equivalent for zinc-N`. Surface in PR description.

- [ ] **Step 3: Verify zero remaining matches**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/StageInspectorPanel.tsx
```

Expected: 0 (or only TODO-annotated lines).

- [ ] **Step 4: Run gates**

```bash
npx tsc --noEmit && npm run lint && npm run test
```

- [ ] **Step 5: Manual visual smoke** — open the pipeline page, compare side-by-side with main.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageInspectorPanel.tsx
git commit -m "chore(pipeline/StageInspectorPanel): replace raw zinc/white classes with px-* tokens"
```

### Task 9: C3 — design-token sweep on `StageConfigDrawer.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`

**Why:** Spec §9.4 (B5.5). 23 raw zinc/white refs (the largest of the four). Higher chance of complex `style` merges and arbitrary zinc shades requiring TODO annotation.

- [ ] **Step 1: List the offending lines**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx
```

Expected: 23 matches.

- [ ] **Step 2: Apply replacements**

Same replacement table as Task 8:

| From | To |
|---|---|
| `bg-white` | `style={{ background: 'var(--px-surface)' }}` |
| `border-zinc-100` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-200` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-300` | `style={{ borderColor: 'var(--px-divider)' }}` |
| `text-zinc-400` | `style={{ color: 'var(--px-fg-4)' }}` |
| `text-zinc-500` | `style={{ color: 'var(--px-fg-3)' }}` |
| `text-zinc-600` | `style={{ color: 'var(--px-fg-2)' }}` |
| `text-zinc-700` | `style={{ color: 'var(--px-fg)' }}` |

Merge into existing `style` props; do not replace. Annotate any without a px equivalent as `// TODO(design-review): no px-token equivalent for zinc-N`.

- [ ] **Step 3: Verify zero remaining matches**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx
```

Expected: 0 (or only TODO-annotated lines).

- [ ] **Step 4: Run gates** — `npx tsc --noEmit && npm run lint && npm run test`

- [ ] **Step 5: Manual visual smoke** — open the stage drawer (any stage in any pipeline view), compare side-by-side with main.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx
git commit -m "chore(pipeline/StageConfigDrawer): replace raw zinc/white classes with px-* tokens

23 refs migrated. Any zinc shades without a clean px-token equivalent
are flagged inline with TODO(design-review) for design follow-up."
```

### Task 10: C3 — design-token sweep on `TemplatePickerDialog.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx`

**Why:** Spec §9.4 (B5.5). 9 raw zinc/white refs. This file was already touched by Task 6 (a11y) — the token sweep layers on top.

- [ ] **Step 1: List the offending lines**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx
```

Expected: 9 matches.

- [ ] **Step 2: Apply replacements**

Same replacement table as Task 8:

| From | To |
|---|---|
| `bg-white` | `style={{ background: 'var(--px-surface)' }}` |
| `border-zinc-100` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-200` | `style={{ borderColor: 'var(--px-hairline)' }}` |
| `border-zinc-300` | `style={{ borderColor: 'var(--px-divider)' }}` |
| `text-zinc-400` | `style={{ color: 'var(--px-fg-4)' }}` |
| `text-zinc-500` | `style={{ color: 'var(--px-fg-3)' }}` |
| `text-zinc-600` | `style={{ color: 'var(--px-fg-2)' }}` |
| `text-zinc-700` | `style={{ color: 'var(--px-fg)' }}` |

Merge into existing `style` props; do not replace. Annotate any without a px equivalent.

- [ ] **Step 3: Verify zero remaining matches**

```bash
grep -nE "border-zinc|bg-white|text-zinc-" \
  frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx
```

Expected: 0 (or only TODO-annotated lines).

- [ ] **Step 4: Run gates** — `npx tsc --noEmit && npm run lint && npm run test`

- [ ] **Step 5: Manual visual smoke** — open the template picker, compare with main: list cards, hover states, tab strip colors all unchanged.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx
git commit -m "chore(pipeline/TemplatePickerDialog): replace raw zinc/white classes with px-* tokens

9 refs migrated. Layered on top of the a11y migration in the prior
commit; no visual change intended."
```

### Task 11: C8.3 — composition test (no-nested-form)

**Files:**
- Create: `frontend/app/tests/_utils/render.tsx`
- Create: `frontend/app/tests/composition/company-profile-detail-no-nested-forms.test.tsx`

**Why:** Regression-prevention for the B4 nested-form bug fixed at commit `6fd14d0`. Test passes on main today; its job is to catch a future re-introduction.

- [ ] **Step 1: Create the harness**

Create `frontend/app/tests/_utils/render.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";

/**
 * Shared render harness for composition + integration tests.
 *
 * Mounts a fresh QueryClient per render with retries off and gcTime: 0
 * so each test gets a clean cache and failed queries do not loop. Add
 * additional providers here as the test surface grows.
 */
export function renderWithProviders(
  ui: ReactElement,
  opts?: RenderOptions,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    opts,
  );
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/app/tests/composition/company-profile-detail-no-nested-forms.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'

// Mock the API namespaces so the queries inside CompanyProfileDetail +
// MembersSection resolve without a real network. Mock at the module
// boundary so the real hooks + query keys exercise.
vi.mock('@/lib/api/team', () => ({
  teamApi: {
    list: vi.fn(async () => [
      {
        id: 'u1',
        email: 'alice@example.com',
        full_name: 'Alice',
        is_active: true,
        source: 'user',
        roles: [],
      },
    ]),
  },
}))
vi.mock('@/lib/api/org-units', () => ({
  orgUnitsApi: {
    listMembers: vi.fn(async () => []),
  },
}))
vi.mock('@/lib/api/roles', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/roles')>(
    '@/lib/api/roles',
  )
  return {
    ...actual,
    rolesApi: {
      list: vi.fn(async () => []),
    },
  }
})
vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'test-token'),
}))

// Import the component AFTER mocks are set up.
import { CompanyProfileDetail } from '@/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail'

const MIN_UNIT = {
  id: 'u-1',
  unit_type: 'company' as const,
  name: 'TestCo',
  parent_unit_id: null,
  description: null,
  metadata: null,
  is_root: true,
  company_profile: {
    about: 'about',
    industry: 'saas_enterprise_software' as const,
    company_stage: 'series_a_b' as const,
    hiring_bar: 'hiring bar',
  },
  company_profile_completed_at: '2026-04-01T00:00:00Z',
}

describe('CompanyProfileDetail composition', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has no nested <form> elements when rendered with MembersSection', async () => {
    const { container } = renderWithProviders(
      <CompanyProfileDetail unit={MIN_UNIT} onSaved={vi.fn()} />,
    )

    // Wait for MembersSection's queries to resolve.
    await waitFor(() => {
      expect(screen.getByText(/Members & Roles/i)).toBeInTheDocument()
    })

    const forms = container.querySelectorAll('form')
    expect(forms.length).toBeGreaterThan(0)
    for (const form of forms) {
      expect(
        form.querySelector('form'),
        `Found a nested <form> inside another <form>: ${form.outerHTML.slice(0, 200)}…`,
      ).toBeNull()
    }
  })
})
```

If `CompanyProfileDetail`'s actual props shape differs from the test (different prop names than `unit` / `onSaved`), open the actual file and align the test mock. The test's intent — "this rendered tree has no nested forms" — does not change.

- [ ] **Step 3: Run the test, verify it passes on main**

```bash
npx vitest run tests/composition/company-profile-detail-no-nested-forms.test.tsx
```

Expected: 1 passed. (The bug was fixed at `6fd14d0`; this test is regression prevention.)

- [ ] **Step 4: Negative-control verification (do this once, then revert)**

To prove the test would catch the regression, temporarily wrap `<MembersSection>` inside a `<form>` in `CompanyProfileDetail.tsx` and re-run the test. It must FAIL with the nested-form assertion. Revert the temporary change and re-run — must PASS again. This step is for confidence; the revert is final.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/tests/_utils/render.tsx \
        frontend/app/tests/composition/company-profile-detail-no-nested-forms.test.tsx
git commit -m "$(cat <<'EOF'
test(composition): assert CompanyProfileDetail has no nested <form>

The B4 bug (nested-form smoke regression, fixed at 6fd14d0) only
surfaced when CompanyProfileDetail rendered MembersSection together —
the unit test for MembersSection in isolation could not catch it.

This composition test mounts the real subtree with a fresh
QueryClient (via renderWithProviders) and walks the DOM asserting that
no <form> is nested inside another <form>. Negative-control verified:
re-introducing the wrap fails the test as expected.

renderWithProviders is the new shared test harness for composition +
integration tests. Used by C8.1 + C8.2 in upcoming commits.
EOF
)"
```

---

## Phase 3 — Wave B (depends on `DangerConfirmDialog` from Phase 1)

These tasks all consume the `DangerConfirmDialog` primitive from Task 1.

### Task 12: C5.5 — `MembersSection` migrates to `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx`

- [ ] **Step 1: Apply the migration**

In `MembersSection.tsx`:

Replace these imports:

```tsx
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/px";
```

with:

```tsx
import { Button, DangerConfirmDialog } from "@/components/px";
```

Replace the inline Dialog block (lines ~314-346, the entire `<Dialog open={!!toRemove}>...</Dialog>` block) with:

```tsx
      <DangerConfirmDialog
        open={!!toRemove}
        title="Remove role"
        description={
          <>
            Remove <strong>{toRemove?.roleName}</strong> from this user on this unit?
          </>
        }
        confirmLabel="Remove role"
        pendingLabel="Removing…"
        pending={removeMutation.isPending}
        onConfirm={handleConfirmRemove}
        onClose={() => setToRemove(null)}
      />
```

- [ ] **Step 2: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean. The existing `members-section-dialog.test.tsx` continues to assert the dialog renders; switching from inline to primitive does not change the rendered DOM shape (both produce a `<Dialog>` portal with the same content).

- [ ] **Step 3: Manual smoke**

```bash
npm run dev
```

Visit `/settings/org-units/<unitId>`. Click `×` next to a role on a member. Dialog opens. Click Cancel — dialog closes, no mutation. Click Remove role — dialog stays open during pending, closes on success.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/MembersSection.tsx
git commit -m "$(cat <<'EOF'
refactor(MembersSection): use DangerConfirmDialog primitive

Replaces the inline state-driven Dialog from B4 with the shared
primitive. ~30 LOC of dialog markup → 12 lines. Behavior identical.

Keeps the codebase self-consistent — every destructive-confirm flow
now goes through the same primitive.
EOF
)"
```

### Task 13: C8.1 — MembersSection cancel-path test

**Files:**
- Create: `frontend/app/tests/components/members-section-cancel-path.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'

const removeMutateAsync = vi.fn().mockResolvedValue(undefined)
const assignMutateAsync = vi.fn().mockResolvedValue(undefined)

vi.mock('@/lib/hooks/use-remove-role', () => ({
  useRemoveRole: () => ({
    mutateAsync: removeMutateAsync,
    isPending: false,
  }),
}))
vi.mock('@/lib/hooks/use-assign-role', () => ({
  useAssignRole: () => ({
    mutateAsync: assignMutateAsync,
    isPending: false,
  }),
}))
vi.mock('@/lib/hooks/use-roles', () => ({
  useRoles: () => ({ data: [{ id: 'r1', name: 'Hiring Manager' }], isLoading: false }),
}))
vi.mock('@/lib/hooks/use-org-unit-members', () => ({
  useOrgUnitMembers: () => ({
    data: [
      {
        user_id: 'u1',
        email: 'alice@example.com',
        full_name: 'Alice',
        roles: [
          {
            role_id: 'r1',
            role_name: 'Hiring Manager',
            assigned_at: '2026-04-01T00:00:00Z',
          },
        ],
      },
    ],
    isLoading: false,
  }),
}))
vi.mock('@/lib/hooks/use-team-members', () => ({
  useTeamMembers: () => ({ data: [], isLoading: false }),
}))

// Import after mocks.
import { MembersSection } from '@/app/(dashboard)/settings/org-units/[unitId]/MembersSection'

describe('MembersSection cancel path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('clicking Cancel in the Remove-role dialog does NOT call the mutation', async () => {
    renderWithProviders(<MembersSection unitId="u-1" />)

    // Open the dialog by clicking the × on the assigned role.
    const removeChip = await screen.findByRole('button', { name: /Remove Hiring Manager/ })
    fireEvent.click(removeChip)

    // Dialog should be open.
    await waitFor(() => {
      expect(screen.getByText(/Remove role/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    // After cancel, the mutation must not have been invoked.
    expect(removeMutateAsync).not.toHaveBeenCalled()

    // Dialog closes.
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /Remove role/i })).not.toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run, verify passes**

```bash
npx vitest run tests/components/members-section-cancel-path.test.tsx
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/tests/components/members-section-cancel-path.test.tsx
git commit -m "$(cat <<'EOF'
test(MembersSection): cancel path does not call removeRole mutation

Regression coverage for the destructive-confirm cancel path. If a
future change accidentally wires Cancel to also fire the mutation, this
test catches it.
EOF
)"
```

### Task 14: C5.3 — `settings/team` `ConfirmDialog` → `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/team/page.tsx`

- [ ] **Step 1: Inspect the current ConfirmAction shape**

Read the current `ConfirmAction` type and the `confirmAction` state. The current bespoke `ConfirmDialog` calls `action.onConfirm(); onClose()` synchronously. The migration shifts to "stays open during mutation, closes on success" semantics — see spec C5.3.

- [ ] **Step 2: Reshape `ConfirmAction` and the callsites**

Replace the `ConfirmAction` type to carry a `pending` boolean (or a reference to the live mutation) and an async `onConfirm`. Suggested shape:

```tsx
type ConfirmAction = {
  message: string
  confirmLabel: string
  pendingLabel: string
  isPending: boolean
  onConfirm: () => Promise<void>
}
```

Each existing `setConfirmAction({ message: ..., onConfirm: ... })` callsite must be updated to also pass `confirmLabel`, `pendingLabel`, and (read at render-time) `isPending` from the relevant mutation hook.

Concretely, every action that produces a `setConfirmAction({...})` becomes (example for deactivate):

```tsx
setConfirmAction({
  message: `Deactivate ${user.email}? They will lose access immediately.`,
  confirmLabel: 'Deactivate',
  pendingLabel: 'Deactivating…',
  isPending: deactivateMutation.isPending,
  onConfirm: async () => {
    await deactivateMutation.mutateAsync({ userId: user.id })
    setConfirmAction(null)
    toast.success('User deactivated')
  },
})
```

The "stay open during pending, close on success" comes from inside `onConfirm`: it awaits the mutation, then sets `confirmAction` to null. On error, it does NOT set null — the dialog stays open and a `toast.error` surfaces (consistent with MembersSection's pattern).

A subtle detail: `isPending` is captured at the moment `setConfirmAction(...)` was called, which means it'll be `false` initially. To get the live pending state, the rendered dialog must re-read from the mutation. The cleanest fix: lift the `pending` reading out of the action into the render:

```tsx
{confirmAction && (
  <DangerConfirmDialog
    open
    title="Confirm action"
    description={confirmAction.message}
    confirmLabel={confirmAction.confirmLabel}
    pendingLabel={confirmAction.pendingLabel}
    pending={
      // The active mutation drives pending. Each action sets up its own
      // mutation linkage above; we read the live state here.
      deactivateMutation.isPending ||
      revokeMutation.isPending ||
      // any other mutation that can be in-flight from a confirm dialog
      false
    }
    onConfirm={() => {
      void confirmAction.onConfirm().catch((err) => {
        toast.error(err instanceof Error ? err.message : 'Action failed')
      })
    }}
    onClose={() => setConfirmAction(null)}
  />
)}
```

If reading multiple mutation pending states gets ugly, an alternative shape is to add `pending: () => boolean` (a thunk) on the `ConfirmAction` so each action self-describes its pending source. Pick whichever is clearer — both satisfy the spec.

Delete the bespoke `ConfirmDialog` component definition (lines ~83-119).

- [ ] **Step 3: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 4: Manual smoke**

```bash
npm run dev
```

Visit `/settings/team`. Trigger each destructive action that uses the dialog (revoke invite, deactivate user). Verify:
- Dialog opens with title + description + Cancel + destructive Confirm.
- Cancel dismisses without calling the mutation.
- Confirm fires the mutation; dialog stays open during pending; closes on success.
- On simulated error (kill backend mid-action), dialog stays open and toast error appears.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/team/page.tsx
git commit -m "$(cat <<'EOF'
refactor(settings/team): bespoke ConfirmDialog → DangerConfirmDialog

Drops the local ConfirmDialog component (raw <div> modal with no focus
trap, no scroll lock, no a11y) in favour of the shared px primitive.

UX shift: previous behaviour closed the dialog synchronously on
Confirm; new behaviour stays open during the mutation and closes on
success. The user now sees pending state and the dialog stays put on
error so they can retry or cancel explicitly.
EOF
)"
```

### Task 15: C4 — `pipeline-templates/page.tsx:44` `confirm()` → `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx`

- [ ] **Step 1: Find the callsite and the surrounding handler**

Open the file. Line 44 is inside a click handler that calls `confirm('Delete this template? This cannot be undone.')` and then proceeds to call the delete mutation.

- [ ] **Step 2: Replace with state-driven Dialog**

Add state at the top of the component:

```tsx
const [toDelete, setToDelete] = useState<{ id: string; name: string } | null>(null)
```

Replace the click handler that previously called `confirm()`:

```tsx
// before:
onClick={() => {
  if (confirm('Delete this template? This cannot be undone.')) {
    deleteMutation.mutate({ id: template.id })
  }
}}

// after:
onClick={() => setToDelete({ id: template.id, name: template.name })}
```

Render the dialog at the bottom of the component's JSX:

```tsx
<DangerConfirmDialog
  open={!!toDelete}
  title="Delete template"
  description={
    <>
      Delete <strong>{toDelete?.name}</strong>? This cannot be undone.
    </>
  }
  confirmLabel="Delete"
  pendingLabel="Deleting…"
  pending={deleteMutation.isPending}
  onConfirm={async () => {
    if (!toDelete) return
    try {
      await deleteMutation.mutateAsync({ id: toDelete.id })
      setToDelete(null)
      toast.success('Template deleted')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete template')
      // Stay open so user can retry or cancel.
    }
  }}
  onClose={() => setToDelete(null)}
/>
```

Add the import:

```tsx
import { DangerConfirmDialog } from '@/components/px'
```

If the existing delete handler was `deleteMutation.mutate()` (fire-and-forget), upgrade to `mutateAsync` and add error handling as above.

- [ ] **Step 3: Run gates + manual smoke**

```bash
npx tsc --noEmit && npm run lint && npm run test
```

Manual: navigate to a pipeline-templates page, click delete on a template, exercise Cancel and Confirm.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/pipeline-templates/page.tsx
git commit -m "refactor(pipeline-templates): replace confirm() with DangerConfirmDialog"
```

### Task 16: C4 — `UnifiedPipelineView.tsx:298` `confirm()` → `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx`

- [ ] **Step 1: Apply the same pattern as Task 15**

The line 298 callsite asks "Discard your edits and reset to the source template?". Replace with state-driven `DangerConfirmDialog` carrying `toReset` boolean (or a richer state object if the action needs context). Confirm fires the existing reset mutation.

- [ ] **Step 2-3: Gates + smoke + commit**

```bash
git commit -m "refactor(pipeline/UnifiedPipelineView): replace confirm() with DangerConfirmDialog"
```

### Task 17: C4 — `JobPipelineFunnel.tsx:383 + 1346` `confirm()` → `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx`

Two callsites in this file.

- [ ] **Step 1: Apply same pattern at line 383 (template reset)**

State: `const [toReset, setToReset] = useState(false)`. Confirm calls the reset mutation.

- [ ] **Step 2: Apply same pattern at line 1346 (stage delete)**

State: `const [toDeleteStage, setToDeleteStage] = useState<{ id, name } | null>(null)`. Confirm calls the delete-stage mutation.

- [ ] **Step 3: Gates + smoke + commit**

```bash
git commit -m "refactor(pipeline/JobPipelineFunnel): replace confirm() at line 383 + 1346 with DangerConfirmDialog"
```

### Task 18: C4 — `QuestionCard.tsx:125 + 142` `confirm()` → `DangerConfirmDialog`

**Files:**
- Modify: `frontend/app/components/dashboard/question-bank/QuestionCard.tsx`

Two callsites in this file. Both ask about deleting a question.

- [ ] **Step 1: Read the current code at lines 125 and 142**

Determine if these are two paths into the same delete (one inside a contextual menu, one via a button), or genuinely separate flows. If the same delete, lift state to a single `toDelete` and let both entry points set it.

- [ ] **Step 2: Apply the migration**

Single `toDelete` state if the targets converge; otherwise two states. Confirm fires the existing question-delete mutation.

- [ ] **Step 3: Gates + smoke + commit**

```bash
git commit -m "refactor(question-bank/QuestionCard): replace confirm() at line 125 + 142 with DangerConfirmDialog"
```

### Task 19: Verify zero remaining `confirm()` callsites in scope

- [ ] **Step 1: Grep for confirm( in target directories**

```bash
grep -rn "confirm(" \
  frontend/app/components/dashboard/pipeline/ \
  frontend/app/components/dashboard/question-bank/ \
  frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/pipeline-templates/
```

Expected: 0 matches.

```bash
grep -rn "confirm(" frontend/app/app/\(dashboard\)/settings/team/page.tsx
```

Expected: 0 matches (the bespoke ConfirmDialog is also gone).

The remaining `confirm()` in `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx:1412` is owned by C1 and will be removed during the decomposition.

### Task 20: C8.2 — org-units `client_account` flow test

**Files:**
- Create: `frontend/app/tests/components/org-units-client-account-flow.test.tsx`

**Why:** spec C8.2 — exercise the create-org-unit form's `client_account` branch, which opens `CompanyProfileDialog` and then calls `doCreate(profile)` after the dialog submits.

- [ ] **Step 1: Read the relevant flow in `app/(dashboard)/settings/org-units/page.tsx`**

Identify the create form, the `client_account` unit_type branch (line ~230 from the audit), and the dialog component it opens.

- [ ] **Step 2: Write the test**

The test mounts the org-units page with `me` mocked to `workspace_mode: 'agency'` so the `client_account` option is visible. Fills the form, picks `client_account`, submits, expects the `CompanyProfileDialog` to open. Submits a valid profile inside the dialog and asserts that `doCreate` (or the underlying mutation) was called once with the merged payload.

If the create-flow internals are fragile to test in jsdom (e.g. Base UI Select interactions), use `getByLabelText` + `fireEvent.change` for the unit-type select rather than clicking the trigger. Mocks at the API namespace boundary as in Task 11.

- [ ] **Step 3: Run, verify passes**

```bash
npx vitest run tests/components/org-units-client-account-flow.test.tsx
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
test(org-units): client_account flow exercises CompanyProfileDialog

Asserts the agency-mode create-unit form opens the company profile
dialog when client_account type is chosen, submits a valid profile,
and the create mutation fires with the merged payload.
EOF
)"
```

---

## Phase 4 — C1: jd-panels decomposition

The largest single migration in any cleanup batch. 1,658 LOC → <200 LOC across ~22 new files. Done in dependency order: helpers first (no React, easiest), then leaves (presentational, no state), then stateful shells, finally the page itself.

**Throughout this phase:** after each extraction, run `npx tsc --noEmit && npm run test` before committing. The page must compile and tests must pass at every commit. If a leaf component imports something that hasn't been extracted yet, extract that first.

### Task 21: Extract helpers — `helpers/groupSignals.ts`, `needsReview.ts`, `weightToConfidence.ts`, `findSnippet.ts`, `suggestQuestions.ts`

**Files:**
- Create: 5 files under `frontend/app/components/dashboard/jd-panels/helpers/`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` (remove the helpers; import from new location)

**Why:** Pure functions, no JSX, no React. Easiest extraction — risk-free if exports are clean.

- [ ] **Step 1: Read the current helpers**

Open `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`. The 5 helpers live at:
- `weightToConfidence` ~ line 97
- `groupSignals` ~ line 155
- `needsReview` ~ line 169
- `findSnippet` ~ line 1586
- `suggestQuestions` ~ line 1630

Plus a small `I` helper around line 18 — review and decide if it belongs in helpers or somewhere else (likely a leaf component utility; if it's only used by one component, keep it co-located when that component is extracted).

- [ ] **Step 2: Create each helper file**

For each of the 5 helpers, create a file with a single named export. Example for `groupSignals.ts`:

```ts
import type { SignalItem } from "@/lib/api/jobs";

export function groupSignals(signals: SignalItem[]): {
  label: string;
  signals: SignalItem[];
}[] {
  // Lift the existing function body verbatim from page.tsx.
}
```

Do this for all 5 files. Lift each body verbatim. Add explicit return type if it isn't already there. Keep the function comments.

- [ ] **Step 3: Update `page.tsx` imports + remove the inlined definitions**

In `page.tsx`, add at the top:

```tsx
import { groupSignals } from "@/components/dashboard/jd-panels/helpers/groupSignals";
import { needsReview } from "@/components/dashboard/jd-panels/helpers/needsReview";
import { weightToConfidence } from "@/components/dashboard/jd-panels/helpers/weightToConfidence";
import { findSnippet } from "@/components/dashboard/jd-panels/helpers/findSnippet";
import { suggestQuestions } from "@/components/dashboard/jd-panels/helpers/suggestQuestions";
```

Delete the function definitions from `page.tsx`. Keep the call sites unchanged.

- [ ] **Step 4: Gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 5: Manual smoke**

Boot dev server. Visit `/jobs/<id>` (any job in `signals_extracted` or `signals_confirmed` state). Page must render identically — same signals, same grouping, same confidence chips.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/helpers/ \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
git commit -m "$(cat <<'EOF'
refactor(jobs/[jobId]): extract pure helpers into jd-panels/helpers/

Five pure functions move from page.tsx into their own files under
components/dashboard/jd-panels/helpers/. No behavior change; same
function bodies, same call sites.

Pre-work for the page decomposition. Helpers go first because they're
JSX-free and risk-free.
EOF
)"
```

### Task 22: Extract small leaves — Confidence, SourceBadge, Kbd, EmptyRow

**Files:**
- Create: 4 files under `frontend/app/components/dashboard/jd-panels/components/`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

**Why:** Tiny presentational components. Each one component, props-only, no state. Extract together as one commit.

- [ ] **Step 1: Create each leaf file**

For each of `Confidence`, `SourceBadge`, `Kbd`, `EmptyRow`, create a file with a single named export. Lift the function body verbatim. Example for `Confidence.tsx`:

```tsx
"use client";

export function Confidence({
  value,
  inline = false,
}: {
  value: number;
  inline?: boolean;
}) {
  // Lift the existing function body verbatim from page.tsx.
}
```

If a leaf imports another leaf (e.g. `SourceBadge` uses `Kbd`), use a relative import within the new directory:

```tsx
import { Kbd } from "./Kbd";
```

- [ ] **Step 2: Update page.tsx imports + remove inline definitions**

```tsx
import { Confidence } from "@/components/dashboard/jd-panels/components/Confidence";
import { SourceBadge } from "@/components/dashboard/jd-panels/components/SourceBadge";
import { Kbd } from "@/components/dashboard/jd-panels/components/Kbd";
import { EmptyRow } from "@/components/dashboard/jd-panels/components/EmptyRow";
```

Delete the inline definitions.

- [ ] **Step 3: Gates + smoke + commit**

```bash
git add frontend/app/components/dashboard/jd-panels/components/ \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
git commit -m "refactor(jobs/[jobId]): extract Confidence, SourceBadge, Kbd, EmptyRow leaves"
```

### Task 23: Extract `SnippetHighlighted`, `InspectorHint`, `InspectorTips`, `InspectorAction`, `CanvasHeader`

**Files:**
- Create: 5 files under `frontend/app/components/dashboard/jd-panels/components/`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

Same pattern as Task 22.

- [ ] **Steps 1-3: Extract each. Update imports. Gates. Commit.**

```bash
git commit -m "refactor(jobs/[jobId]): extract SnippetHighlighted + InspectorHint/Tips/Action + CanvasHeader leaves"
```

### Task 24: Extract `SignalRow` AS `<button>` (a11y B5.3)

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/components/SignalRow.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

**Why:** spec §9.3 (B5.3). Currently `SignalRow` is a `<div onClick>` — not keyboard-accessible. Extract into its own file AND change the root element to `<button type="button">`.

- [ ] **Step 1: Read the current SignalRow**

Around line 1007 of `page.tsx`. Note the root `<div>`, its `onClick`, and the styling that makes it look like a clickable row.

- [ ] **Step 2: Write the new component as `<button>`**

Create `SignalRow.tsx`:

```tsx
"use client";

import type { SignalItem } from "@/lib/api/jobs";

import { Confidence } from "./Confidence";
// + any other leaf imports the original SignalRow uses

interface SignalRowProps {
  signal: SignalItem;
  isSelected: boolean;
  isReviewable: boolean;
  onSelect: () => void;
  // + other props from the original signature
}

export function SignalRow({
  signal,
  isSelected,
  isReviewable,
  onSelect,
  // ...
}: SignalRowProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={isSelected ? "true" : undefined}
      className={/* same classes as the original outer div, plus any
                    button-reset classes if the px-system has one */}
      style={{
        // existing inline styles from the original
        // ensure background: 'transparent' or a button-friendly color
      }}
    >
      {/* exact same children as the current div */}
    </button>
  );
}
```

A `<button>` element gets keyboard activation (Enter, Space) and focus-ring for free. Verify the existing visual (left border, hover background, etc.) is preserved — buttons may have default UA styles that clash. If so, override with `appearance: 'none'`, `border: 'none'` (then re-add as needed), `font: 'inherit'`, `color: 'inherit'`, `background: 'transparent'` etc.

- [ ] **Step 3: Replace the inline `SignalRow` definition with the import**

```tsx
import { SignalRow } from "@/components/dashboard/jd-panels/components/SignalRow";
```

Delete the inline definition.

- [ ] **Step 4: Gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

- [ ] **Step 5: Manual a11y smoke**

```bash
npm run dev
```

Visit `/jobs/<id>`. Tab into the signals canvas. Each `SignalRow` should be tab-focusable. Press Enter or Space to select — the row should activate. Visual check: hover, focus ring, selected state must look identical to before.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/components/SignalRow.tsx \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
git commit -m "$(cat <<'EOF'
refactor(jd-panels/SignalRow): extract as <button> for keyboard a11y (B5.3)

Spec §9 B5.3 — was <div onClick>, no keyboard activation. Now
<button type="button">: native Enter/Space activation, focus ring,
:disabled, aria-current="true" when selected.

Visual styling preserved via inline styles + className overrides.
EOF
)"
```

### Task 25: Extract `SignalGroup` and `TabStrip`

**Files:**
- Create: `components/dashboard/jd-panels/components/SignalGroup.tsx`
- Create: `components/dashboard/jd-panels/components/TabStrip.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

`TabStrip` here is the inner JD-review tab strip (different from C2's pipeline TemplatePickerDialog tab strip — but applies the SAME pattern: `role="tablist"`, `role="tab"`, `aria-selected`, with `role="tabpanel"` on the targeted panels). Convert as part of the extraction.

- [ ] **Steps 1-3: Extract, update imports, run gates.**

The TabStrip component should accept tab definitions as props and render them with proper ARIA semantics. Example shape:

```tsx
export function TabStrip<T extends string>({
  tabs,
  activeTab,
  onChange,
  ariaLabel,
}: {
  tabs: { id: T; label: string }[];
  activeTab: T;
  onChange: (tab: T) => void;
  ariaLabel: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={tab.id === activeTab}
          aria-controls={`panel-${tab.id}`}
          tabIndex={tab.id === activeTab ? 0 : -1}
          onClick={() => onChange(tab.id)}
          className={/* styling preserved */}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
```

Update the call site in (what will become) `JDReviewShell` to wrap target panels with `<div role="tabpanel" id={`panel-${tab.id}`} aria-labelledby={`tab-${tab.id}`}>`.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(jd-panels): extract SignalGroup + TabStrip (with tablist a11y)"
```

### Task 26: Extract `SectionsRail`

**Files:**
- Create: `components/dashboard/jd-panels/SectionsRail.tsx` (top-level — it's a panel, not a leaf)
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

- [ ] **Steps 1-3: Extract, update imports, run gates.**

`SectionsRail` is presentational: receives the list of sections + selection callback as props.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(jd-panels): extract SectionsRail panel"
```

### Task 27: Extract `FullJdCanvas`

**Files:**
- Create: `components/dashboard/jd-panels/FullJdCanvas.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

- [ ] **Steps 1-3: Extract, update imports, run gates.**

`FullJdCanvas` renders the JD source view. Likely receives the JD text + section markers as props.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(jd-panels): extract FullJdCanvas panel"
```

### Task 28: Extract `SignalsCanvas`

**Files:**
- Create: `components/dashboard/jd-panels/SignalsCanvas.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

- [ ] **Steps 1-3: Extract, update imports, run gates.**

`SignalsCanvas` is the body of the signals tab. Owns its own selection callbacks (or receives them as props from `JDReviewShell`). Imports `SignalRow`, `SignalGroup`, etc.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(jd-panels): extract SignalsCanvas panel"
```

### Task 29: Extract `SignalInspector` AND replace embedded `confirm()` at line 1412

**Files:**
- Create: `components/dashboard/jd-panels/SignalInspector.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

**Why:** This is the right-side editor. Owns local edit-mode state. Contains the embedded `confirm()` callsite from C4 that's owned by C1.

- [ ] **Step 1: Extract `SignalInspector` per the standard pattern**

- [ ] **Step 2: Replace `confirm(\`Remove signal "${signal.value}"?\`) onRemove()` with `DangerConfirmDialog`**

Add state to `SignalInspector`:

```tsx
const [toRemove, setToRemove] = useState(false)
```

Replace the click handler:

```tsx
// before:
onClick={() => {
  if (confirm(`Remove signal "${signal.value}"?`)) onRemove()
}}

// after:
onClick={() => setToRemove(true)}
```

Render at the bottom of the component:

```tsx
<DangerConfirmDialog
  open={toRemove}
  title="Remove signal"
  description={
    <>
      Remove signal <strong>{signal?.value}</strong>?
    </>
  }
  confirmLabel="Remove signal"
  pendingLabel="Removing…"
  pending={false /* or wire to a real pending state if onRemove returns a promise */}
  onConfirm={() => {
    onRemove()
    setToRemove(false)
  }}
  onClose={() => setToRemove(false)}
/>
```

If `onRemove` is fire-and-forget (no Promise), the `pending` state is `false` and the dialog closes immediately on confirm. If `onRemove` is async, lift the Promise to the parent and wire `pending` from there.

- [ ] **Step 3: Gates + smoke**

Manual: edit a signal in the inspector, click Remove. Dialog opens. Cancel dismisses. Confirm fires onRemove.

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(jd-panels/SignalInspector): extract panel + replace confirm() with DangerConfirmDialog

Removes the last confirm() callsite from the JD review page (line 1412).
The signal-removal flow now uses the shared DangerConfirmDialog pattern
along with every other destructive-confirm in the app.
EOF
)"
```

### Task 30: Extract `JDReviewShell` and create `index.ts` re-export

**Files:**
- Create: `components/dashboard/jd-panels/JDReviewShell.tsx`
- Create: `components/dashboard/jd-panels/index.ts`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

**Why:** Final extraction. After this commit, the page is a thin shell.

- [ ] **Step 1: Extract `JDReviewShell`**

This component is the root of the JD review tree. It owns:
- Tab state (`signals` | `jd`)
- Draft signals state (lifted from current `JDReviewShell` in `page.tsx` line 238)
- The `key={snapshot.version}` remount key strategy

Move the existing `JDReviewShell` function body verbatim into the new file. Update its imports to point at the extracted components.

- [ ] **Step 2: Create `index.ts`**

```ts
export { JDReviewShell } from "./JDReviewShell";
```

- [ ] **Step 3: Reduce `page.tsx`**

After all prior extractions, the page is just the loading/redirect logic + the shell mount. Replace the entire file with:

```tsx
"use client";

import { useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import { JDReviewShell } from "@/components/dashboard/jd-panels";
import { ErrorBanner } from "@/components/dashboard/jd-panels/ErrorBanner";
import { LoadingSkeleton } from "@/components/dashboard/jd-panels/LoadingSkeleton";
import { useJob } from "@/lib/hooks/use-job";
import { useJobPipeline } from "@/lib/hooks/use-job-pipeline";
import { useJobStatusStream } from "@/lib/hooks/use-job-status-stream";
import { useTriggerEnrich } from "@/lib/hooks/use-trigger-enrich";

export default function JobReviewPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId;
  const searchParams = useSearchParams();
  const router = useRouter();

  const { status, error: sseError, isStreaming } = useJobStatusStream(jobId);
  const { data: job, isLoading } = useJob(jobId, isStreaming);
  const { data: pipeline } = useJobPipeline(jobId);
  const triggerEnrich = useTriggerEnrich(jobId);

  useEffect(() => {
    if (!pipeline) return;
    if (job?.status !== "signals_confirmed") return;
    if (searchParams.get("tab") === "jd") return;
    router.replace(`/jobs/${jobId}/pipeline`);
  }, [pipeline, job?.status, searchParams, router, jobId]);

  if (isLoading || !job) {
    return <LoadingSkeleton status={status} sseError={sseError} />;
  }

  if (job.status === "draft" || job.status === "signals_extracting") {
    return <LoadingSkeleton status={status} sseError={sseError} />;
  }

  if (job.status === "signals_extraction_failed") {
    return <ErrorBanner jobId={jobId} error={job.status_error} />;
  }

  if (!job.latest_snapshot) {
    return (
      <div
        className="rounded-[10px] border p-8 text-sm"
        style={{
          background: "var(--px-surface)",
          borderColor: "var(--px-hairline)",
          color: "var(--px-fg-3)",
        }}
      >
        No signals snapshot yet.
      </div>
    );
  }

  return (
    <JDReviewShell
      key={job.latest_snapshot.version}
      job={job}
      onReEnrich={() => triggerEnrich.mutate()}
    />
  );
}
```

If `LoadingSkeleton` and `ErrorBanner` were not extracted (they live in `components/dashboard/jd-panels/` already from Phase 2A — verify), keep their import paths as-is.

- [ ] **Step 4: Verify the page is < 200 LOC**

```bash
wc -l frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
```

Expected: < 200.

- [ ] **Step 5: Run gates**

```bash
npx tsc --noEmit
npm run lint
npm run test
```

Expected: clean.

- [ ] **Step 6: Manual smoke — full JD review walkthrough**

```bash
npm run dev
```

1. Visit `/jobs/<id>` for a job in `signals_extracted`.
2. Click between the Signals and JD tabs — both render.
3. Click a signal in the canvas — inspector populates.
4. Edit a signal value — change persists locally.
5. Click "Re-enrich" — triggers the mutation.
6. Click "Remove signal" — `DangerConfirmDialog` opens; cancel + confirm both work.
7. Confirm signals — page redirects to pipeline.
8. Visit a job in `signals_confirmed` — page redirects to pipeline (per useEffect).
9. Add `?tab=jd` to the URL — JD review page renders even after confirm.

If any of these regress, isolate the failing extraction and fix.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/jd-panels/JDReviewShell.tsx \
        frontend/app/components/dashboard/jd-panels/index.ts \
        frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
git commit -m "$(cat <<'EOF'
refactor(jobs/[jobId]): extract JDReviewShell — page now <200 LOC

Final cut of the C1 decomposition. The route file is now a thin shell
that handles loading/error/redirect logic and mounts JDReviewShell.

Total reduction: 1658 → ~75 LOC. The 1583 lines of removed code now
live as 22 focused files under components/dashboard/jd-panels/, each
with one clear responsibility.

Closes spec §9 B5.1.
EOF
)"
```

---

## Phase 5 — C7: Backend login hardening

CLAUDE.md gate: changes touch `app/modules/auth/`. Split review applies (spec then quality, two passes).

### Task 31: Add `sign_out` to `AuthProvider` protocol + `LoginRequest` length bound

**Files:**
- Modify: `backend/nexus/app/modules/auth/admin/base.py`
- Modify: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 1: Add `sign_out` to the protocol**

In `app/modules/auth/admin/base.py`, append a new method to the `AuthProvider` protocol:

```python
class AuthProvider(Protocol):
    # ... existing methods ...

    async def sign_out(self, tokens: SessionTokens) -> None:
        """Revoke a previously-issued session.

        Idempotent on already-revoked tokens — returns normally if the
        session is gone. Idempotent in unconfigured environments — logs
        a warning and returns. Other transport errors raise
        `AuthProviderError`.

        Used by the login handler to revoke a token that was minted
        before a downstream auth check rejected the user.
        """
        ...
```

- [ ] **Step 2: Add the password length bound to `LoginRequest`**

In `app/modules/auth/schemas.py`, modify `LoginRequest`:

```python
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Request body for POST /api/auth/login.

    `email` uses `EmailStr` so the 422 path catches malformed addresses
    before the handler touches the AuthProvider — no user enumeration
    surface for syntax errors.

    `password` is bounded at 1..128 characters: enforces the user
    actually typed something (`min_length=1`) and stops oversized
    payloads at the validation layer (well below FastAPI's request
    body size limit, well above any reasonable password length).
    """

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
```

- [ ] **Step 3: Run pytest — existing tests must still pass**

```bash
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
```

Expected: 498 passed. `LoginRequest` change is backwards-compatible for valid passwords (1-128 chars); only over-128 or empty are newly rejected, and no existing test sends those.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/auth/admin/base.py \
        backend/nexus/app/modules/auth/schemas.py
git commit -m "$(cat <<'EOF'
feat(auth): AuthProvider.sign_out + LoginRequest password length bound

Protocol surface adds sign_out(tokens: SessionTokens) — idempotent on
already-revoked tokens, idempotent in unconfigured environments. Used
by the login handler to revoke a session that was minted before a
downstream auth check rejected the user (e.g. deactivated account,
missing tenant_id, no app user row).

LoginRequest.password gets Field(min_length=1, max_length=128) — 422 on
empty or over-128, well within FastAPI's request size cap and well
above any reasonable password length.

Implementation in SupabaseAuthProvider + login handler integration in
the next two commits.
EOF
)"
```

### Task 32: Implement `SupabaseAuthProvider.sign_out`

**Files:**
- Modify: `backend/nexus/app/modules/auth/admin/supabase.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_supabase_sign_out.py`:

```python
"""Tests for SupabaseAuthProvider.sign_out."""

import pytest
import respx
from httpx import Response

from app.config import settings
from app.modules.auth.admin.base import (
    AuthProviderError,
    SessionTokens,
)
from app.modules.auth.admin.supabase import SupabaseAuthProvider


def _tokens() -> SessionTokens:
    return SessionTokens(
        access_token="atk-test-12345",
        refresh_token="rtk-test-12345",
        expires_in=3600,
    )


@pytest.mark.asyncio
@respx.mock
async def test_sign_out_calls_supabase_logout_with_bearer():
    """sign_out POSTs to /auth/v1/logout with the access_token as Bearer."""
    provider = SupabaseAuthProvider()
    route = respx.post(f"{settings.supabase_url}/auth/v1/logout").mock(
        return_value=Response(204)
    )

    await provider.sign_out(_tokens())

    assert route.called
    request = route.calls[0].request
    assert request.headers.get("Authorization") == "Bearer atk-test-12345"


@pytest.mark.asyncio
@respx.mock
async def test_sign_out_idempotent_on_404():
    """A 404 from Supabase means the session is already revoked. Return normally."""
    provider = SupabaseAuthProvider()
    respx.post(f"{settings.supabase_url}/auth/v1/logout").mock(
        return_value=Response(404)
    )

    # Must not raise.
    await provider.sign_out(_tokens())


@pytest.mark.asyncio
@respx.mock
async def test_sign_out_raises_on_other_errors():
    """Any non-2xx-non-404 response raises AuthProviderError."""
    provider = SupabaseAuthProvider()
    respx.post(f"{settings.supabase_url}/auth/v1/logout").mock(
        return_value=Response(500, json={"error": "internal"})
    )

    with pytest.raises(AuthProviderError):
        await provider.sign_out(_tokens())


@pytest.mark.asyncio
async def test_sign_out_skipped_when_unconfigured(monkeypatch):
    """If supabase_url or service_role_key is missing, log + return."""
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    provider = SupabaseAuthProvider()

    # Must not raise; must not attempt a real HTTP call.
    await provider.sign_out(_tokens())
```

If `respx` isn't already in `pyproject.toml`'s test deps, check and add it. Otherwise look at how other tests stub httpx (likely existing tests in `test_auth_service.py` or similar).

- [ ] **Step 2: Run, verify it fails**

```bash
docker compose run --rm nexus pytest backend/nexus/tests/test_supabase_sign_out.py -v
```

Expected: AttributeError (no `sign_out` method).

- [ ] **Step 3: Implement `sign_out`**

In `backend/nexus/app/modules/auth/admin/supabase.py`, add the method to `SupabaseAuthProvider`:

```python
    async def sign_out(self, tokens: SessionTokens) -> None:
        if _missing_config():
            logger.warning(
                "auth.admin.sign_out.skipped",
                reason="supabase_url or service_role_key not configured",
            )
            return
        url = f"{settings.supabase_url}/auth/v1/logout"
        headers = {
            **_anon_headers(),
            "Authorization": f"Bearer {tokens.access_token}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers)
        if resp.status_code in (200, 204, 404):
            if resp.status_code == 404:
                logger.info(
                    "auth.admin.sign_out.already_revoked",
                )
            else:
                logger.info(
                    "auth.admin.sign_out.ok",
                )
            return
        logger.error(
            "auth.admin.sign_out.failed",
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase sign_out failed ({resp.status_code})"
        )
```

- [ ] **Step 4: Run pytest, verify all pass**

```bash
docker compose run --rm nexus pytest backend/nexus/tests/test_supabase_sign_out.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run full pytest with the deselect flags**

```bash
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
```

Expected: 502 passed, 4 deselected (498 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/auth/admin/supabase.py \
        backend/nexus/tests/test_supabase_sign_out.py
git commit -m "$(cat <<'EOF'
feat(auth/supabase): implement sign_out via /auth/v1/logout

POSTs to Supabase's GoTrue /auth/v1/logout with the access_token as
Bearer. 200/204 = success; 404 = idempotent (already revoked); other
non-2xx raises AuthProviderError. Unconfigured env logs a warning and
returns (matching delete_user's pattern).
EOF
)"
```

### Task 33: Wire `_revoke_quietly` across all 4 minted-token branches

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py`

- [ ] **Step 1: Write the failing parametrized test**

Append to `backend/nexus/tests/test_auth_login.py`:

```python
"""Tests for the new sign_out wiring in the login handler.

Each of the 4 minted-token auth-failure branches must:
  1. Call AuthProvider.sign_out with the tokens just minted
  2. Still return the original auth-failure status (revocation tolerant)
  3. Not unbreak the auth failure if sign_out itself raises
"""

import pytest
from unittest.mock import AsyncMock

from app.modules.auth.admin.base import (
    AuthProvider,
    AuthProviderError,
    SessionTokens,
)


@pytest.mark.parametrize(
    "scenario_label,token_payload_override,user_row_override,expected_status",
    [
        # 1. token verify failed (verify_access_token returns None)
        ("token_verify_fail", None, None, 401),
        # 2. missing tenant_id
        ("missing_tenant_id", {"tenant_id": ""}, None, 403),
        # 3. no app user row in the DB
        ("no_app_user", {"tenant_id": "tid-1"}, "absent", 403),
        # 4. user exists but is_active=false
        ("deactivated_user", {"tenant_id": "tid-1"}, "deactivated", 403),
    ],
)
@pytest.mark.asyncio
async def test_login_revokes_minted_token_on_each_failure_branch(
    scenario_label,
    token_payload_override,
    user_row_override,
    expected_status,
    client,
    fake_auth_provider,
    fake_user_lookup,
):
    """Each minted-token failure branch calls sign_out with the tokens."""
    minted_tokens = SessionTokens(
        access_token="atk-1",
        refresh_token="rtk-1",
        expires_in=3600,
    )
    fake_auth_provider.sign_in_with_password = AsyncMock(return_value=minted_tokens)
    fake_auth_provider.sign_out = AsyncMock(return_value=None)

    # Configure the rest of the test scaffolding to drive the desired branch:
    # - token_payload_override: passed to a verify_access_token mock
    # - user_row_override: how the DB lookup behaves
    # (This boilerplate needs to match the conftest pattern already in place
    # for the other login tests; replicate from the closest existing test.)
    #
    # The exact wiring depends on the existing fixture conventions in
    # tests/conftest.py — look at how test_login_rejects_missing_fields is
    # set up and follow the same pattern.

    response = await client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "valid-password"},
    )

    assert response.status_code == expected_status
    fake_auth_provider.sign_out.assert_awaited_once_with(minted_tokens)


@pytest.mark.asyncio
async def test_login_invalid_credentials_does_not_call_sign_out(
    client, fake_auth_provider
):
    """InvalidCredentialsError is raised BEFORE tokens are minted — nothing to revoke."""
    from app.modules.auth.admin.base import InvalidCredentialsError

    fake_auth_provider.sign_in_with_password = AsyncMock(
        side_effect=InvalidCredentialsError("bad password")
    )
    fake_auth_provider.sign_out = AsyncMock(return_value=None)

    response = await client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "wrong"},
    )

    assert response.status_code == 401
    fake_auth_provider.sign_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_sign_out_failure_does_not_unbreak_auth_failure(
    client, fake_auth_provider, fake_user_lookup
):
    """sign_out raising AuthProviderError is logged but does not change the response status."""
    minted_tokens = SessionTokens(
        access_token="atk-1",
        refresh_token="rtk-1",
        expires_in=3600,
    )
    fake_auth_provider.sign_in_with_password = AsyncMock(return_value=minted_tokens)
    fake_auth_provider.sign_out = AsyncMock(
        side_effect=AuthProviderError("supabase down")
    )
    fake_user_lookup.set_deactivated()

    response = await client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "valid-password"},
    )

    # Original auth failure status preserved.
    assert response.status_code == 403
    # sign_out was attempted.
    fake_auth_provider.sign_out.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_password_max_length_rejected_at_validation(client):
    """Password longer than 128 characters returns 422 from Pydantic, not 401."""
    long_password = "x" * 129
    response = await client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": long_password},
    )
    assert response.status_code == 422
    body = response.json()
    assert any(
        "password" in (entry.get("loc") or [])
        for entry in body.get("detail", [])
    )


@pytest.mark.asyncio
async def test_login_password_empty_rejected_at_validation(client):
    """Empty password returns 422 from Pydantic."""
    response = await client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": ""},
    )
    assert response.status_code == 422
```

The fixture details (`client`, `fake_auth_provider`, `fake_user_lookup`) must align with the existing fixture conventions in `backend/nexus/tests/conftest.py`. Read that file before writing the implementation; if `fake_user_lookup` doesn't exist, define a small fixture alongside the test.

- [ ] **Step 2: Run, verify they fail**

```bash
docker compose run --rm nexus pytest backend/nexus/tests/test_auth_login.py -v
```

Expected: new tests fail (handler doesn't yet call sign_out).

- [ ] **Step 3: Implement `_revoke_quietly` and wire it across the 4 branches**

In `backend/nexus/app/modules/auth/router.py`, add this helper near the top of the module (or near the login handler):

```python
async def _revoke_quietly(
    provider: "AuthProvider", tokens: "SessionTokens"
) -> None:
    """Revoke a session, swallowing transport errors.

    Used by the login handler when an auth failure happens AFTER the
    provider has minted tokens. The user is already being told their
    auth attempt failed; a revocation failure must not change that
    response, but it MUST be surfaced in structured logs at error level
    for ops.
    """
    try:
        await provider.sign_out(tokens)
    except AuthProviderError:
        logger.exception("auth.login.sign_out_failed")
```

(`AuthProvider` and `SessionTokens` and `AuthProviderError` are already imported at the top of the file — verify and add if missing.)

Modify each of the 4 minted-token failure branches to call `_revoke_quietly(provider, tokens)` BEFORE raising:

**Branch 1 — token verify failed (line ~309):**

```python
payload = verify_access_token(tokens.access_token)
if payload is None:
    logger.error("auth.login.token_verify_failed", email=data.email)
    await _revoke_quietly(provider, tokens)
    raise HTTPException(
        status_code=401, detail="Invalid email or password."
    )
```

**Branch 2 — missing tenant_id (line ~315):**

```python
tenant_id = payload.tenant_id or ""
if not tenant_id:
    logger.info("auth.login.no_tenant", email=data.email)
    await _revoke_quietly(provider, tokens)
    raise HTTPException(
        status_code=403,
        detail=(
            "This account does not have access to the client dashboard."
        ),
    )
```

**Branch 3 — no app user row (line ~330):**

```python
if user is None:
    logger.error("auth.login.no_app_user", email=data.email)
    await _revoke_quietly(provider, tokens)
    raise HTTPException(
        status_code=403,
        detail=(
            "This account does not have access to the client dashboard."
        ),
    )
```

**Branch 4 — deactivated user (line ~338):**

```python
if not user.is_active:
    logger.info("auth.login.deactivated", user_id=str(user.id))
    await _revoke_quietly(provider, tokens)
    raise HTTPException(
        status_code=403,
        detail="This account has been deactivated.",
    )
```

The InvalidCredentialsError / UserNotFoundError branch at line 295 does NOT get `_revoke_quietly` — `sign_in_with_password` raised before returning tokens, so there's nothing to revoke.

- [ ] **Step 4: Run pytest, verify all pass**

```bash
docker compose run --rm nexus pytest backend/nexus/tests/test_auth_login.py -v
```

Expected: all new tests pass.

- [ ] **Step 5: Run full pytest with deselect flags**

```bash
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
```

Expected: 506+ passed (498 baseline + 4 supabase + ~6 login tests). 4 deselected.

- [ ] **Step 6: Manual smoke**

Boot the local stack. Test each scenario:

```bash
# Valid login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<valid-user>", "password": "<valid-password>"}'
# Expected: 200 + tokens

# Deactivated user (toggle is_active=false on the test user first)
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<deactivated-user>", "password": "<valid-password>"}'
# Expected: 403 + "This account has been deactivated."
# Inspect Supabase: check that the session was revoked from the user's session log.

# Over-128-char password
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"<valid-user>\", \"password\": \"$(python -c 'print("x"*129)')\"}"
# Expected: 422 with password loc

# Bad password (no sign_out should fire)
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<valid-user>", "password": "wrong"}'
# Expected: 401, no Supabase log entry for sign_out
```

If Supabase outage simulation is needed for the revocation-tolerance test, point `SUPABASE_URL` at an unreachable host briefly and verify the 403 still fires.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/auth/router.py \
        backend/nexus/tests/test_auth_login.py
git commit -m "$(cat <<'EOF'
fix(auth/login): revoke minted tokens on every auth-failure branch

The login handler had FOUR auth-failure branches that ran AFTER
provider.sign_in_with_password returned tokens. The tokens were never
installed client-side, but they remained in Supabase's GoTrue session
log indefinitely:

  1. token_verify_fail (401)
  2. missing_tenant_id (403)
  3. no_app_user (403)
  4. deactivated_user (403)

A new _revoke_quietly() helper calls AuthProvider.sign_out() for each
case, swallowing AuthProviderError so a revocation failure cannot
unbreak the original auth failure (logged at error level for ops).

The InvalidCredentialsError branch is intentionally NOT instrumented —
sign_in_with_password raises BEFORE returning tokens, so nothing was
minted to revoke. Verified by test.

Closes the deferred B4 hardening item.
EOF
)"
```

---

## Phase 6 — Final verification

### Task 34: Whole-batch gates

- [ ] **Step 1: Frontend gates**

```bash
cd frontend/app
npx tsc --noEmit
npm run lint
npm run test
npm run build
```

Expected:
- tsc: 0 errors
- lint: 0 errors
- test: ~95 tests passing (87 baseline + ~8 new)
- build: clean

- [ ] **Step 2: Backend pytest**

```bash
cd ../../backend/nexus
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
```

Expected: ~506+ passed, 4 deselected.

- [ ] **Step 3: Verify zero `confirm()` callsites in scope**

```bash
cd ../..
grep -rn "confirm(" \
  frontend/app/components/dashboard/pipeline/ \
  frontend/app/components/dashboard/question-bank/ \
  frontend/app/app/\(dashboard\)/jobs/ \
  frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/pipeline-templates/
```

Expected: 0.

- [ ] **Step 4: Verify zero `aria-pressed` in TemplatePickerDialog**

```bash
grep -n "aria-pressed" frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx
```

Expected: 0.

- [ ] **Step 5: Verify zinc/white sweep complete on the 4 files**

```bash
for f in PipelineFlowColumn.tsx StageInspectorPanel.tsx TemplatePickerDialog.tsx StageConfigDrawer.tsx; do
  echo "=== $f ==="
  grep -nE "border-zinc|bg-white|text-zinc-" "frontend/app/components/dashboard/pipeline/$f" || echo "0 matches"
done
```

Expected: 0 matches per file (or only `TODO(design-review)` annotations).

- [ ] **Step 6: Verify page LOC**

```bash
wc -l frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
```

Expected: < 200.

- [ ] **Step 7: Manual whole-batch smoke checklist**

Boot dev + backend. Walk through each surface and verify nothing regressed:

- **Login**: valid login works; bad password 401; deactivated 403 + Supabase token revoked
- **Onboarding**: step 1 + step 2 work; submit a profile that 422s (force via DevTools), field error appears under the right field
- **JD review** (`/jobs/<id>`): page < 200 LOC; tabs work; signal selection works; signal inspector edits work; remove-signal dialog works; re-enrich works; redirect to pipeline works
- **Pipeline page**: visual unchanged; template picker opens with focus trap + ESC; tabs work with keyboard; reset confirm dialog works; stage delete confirm dialog works
- **Question bank**: question delete dialogs work
- **Org-units**: detail page renders; remove-role dialog works; client_account flow opens CompanyProfileDialog
- **Settings/team**: invite, resend, revoke, deactivate dialogs all work
- **Pipeline-templates**: delete dialog works

- [ ] **Step 8: If everything is green, mark batch complete**

Push the worktree's branch and open a PR (or merge per project convention):

```bash
cd /home/ishant/Projects/ProjectX
git push origin cleanup/batch-5-6-final
# Then merge per the project's existing convention (see B4's merge commit).
```

If the project uses `--no-ff` merges into main as B4 did, follow that pattern.

- [ ] **Step 9: Update the parent spec**

After merge, update `docs/superpowers/specs/2026-04-24-frontend-backend-cleanup-design.md` §9 to add a "Status: ✅ Completed" line at the top of section 9 mirroring the §8 (Batch 4) format. Commit on main.

```bash
git commit -m "docs(spec): mark Batches 5+6 complete"
```

The 2026-04-24 cleanup spec is now fully done.

---

## Self-review checklist (already passed)

- **Spec coverage:** Every cluster C1–C8 from the design has a phase; every locked decision D5.1–D5.6 maps to a task. ✓
- **Placeholder scan:** No "TBD"/"TODO"/"implement later"/"add appropriate" — every step shows the actual code or command. ✓
- **Type consistency:** `DangerConfirmDialog` props match across Tasks 1, 12, 14–18, 29. `applyApiErrorToForm` `stripPrefixes` opt name consistent across Tasks 3 + 4. `_revoke_quietly` signature consistent across Tasks 31 + 33. ✓
- **CLAUDE.md gates:** Phase 5 explicitly notes the auth-module review gate. ✓
- **Frequent commits:** ~30 distinct commits across 34 tasks. ✓
- **TDD discipline:** Tasks that change behavior write tests first. Pure refactors (extractions) rely on full vitest + manual smoke as their gate. ✓
