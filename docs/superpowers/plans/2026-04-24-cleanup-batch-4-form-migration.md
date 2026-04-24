# Cleanup Batch 4 — Form + State Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land section 8 (and the locked decisions in §8.5) of the 2026-04-24 cleanup spec — migrate the six Phase-1 pages that still hold raw `useState` form state and raw `fetch` data to the enterprise patterns the project committed to in Phase 2A: React Hook Form + Zod for forms, TanStack Query for server state in the dashboard, `ApiValidationError` + `applyApiErrorToForm` for per-field 422 mapping. Remove the last `supabase.auth.signInWithPassword` callsite by moving login behind the B3 `AuthProvider` abstraction.

**Architecture:** Two shared utilities land first (`ApiValidationError` subclass in `lib/api/client.ts`, `applyApiErrorToForm` in a new `lib/api/errors.ts`); every page below depends on them. A new backend endpoint `POST /api/auth/login` calls `AuthProvider.sign_in_with_password` — the same provider abstraction B3 introduced — so a future Cognito/Keycloak swap is a config change. Frontend API namespaces grow (`authApi.login` + `completeOnboarding` + `setWorkspaceMode`, new `teamApi`, extended `orgUnitsApi`). Dashboard data layers move to per-endpoint TanStack Query hooks (one hook per file, matching the existing 35+ `lib/hooks/*.ts` convention). Auth and onboarding pages use plain RHF + `await` in `onSubmit` — the `(auth)` and `/onboarding` route groups deliberately have no `QueryClient`, because a login 401 is a form-level error ("bad password"), not a session-expired error that should trigger the dashboard's global redirect. The org-unit detail page is migrated as a tree: page shell + five subcomponents, folding in MembersSection's `confirm()` → `Dialog` conversion.

**Tech Stack:** Backend — FastAPI, Python 3.12, pydantic v2, `httpx`, pytest. Frontend — Next.js 16 (App Router), React 19, TypeScript strict, `react-hook-form`, `@hookform/resolvers/zod`, `zod`, `@tanstack/react-query` v5, `sonner`, Vitest + @testing-library/react + jsdom.

---

## Design notes

The spec (§8.5) locked the following decisions during brainstorming; this plan implements them:

- **D4.1 — Login owned by backend.** New `POST /api/auth/login`. Old `supabase.auth.signInWithPassword` callsite removed.
- **D4.2 — 422 errors throw `ApiValidationError extends ApiError`** with required `fieldErrors`. Discriminated error hierarchy, not optional properties.
- **D4.3 — Schemas live in a sibling `schema.ts` next to each page**, not inline.
- **D4.4 — One hook per file** in `lib/hooks/`, matching the existing convention.
- **D4.5 — B4.6 scope is the full `[unitId]` tree** (page + CompanyProfileDetail + DivisionDetail + RegionDetail + TeamDetail + MembersSection), folding in the MembersSection `confirm()` → `Dialog` conversion.

Per §8.5.6, `(auth)` and `/onboarding` pages use plain RHF + `await` (not `useMutation`). The dashboard pages use `useMutation`. `applyApiErrorToForm` is error-shape-agnostic and works with either pattern.

Per §8.5.5, cluster C2 (backend login endpoint) touches `app/modules/auth/`, which triggers the backend CLAUDE.md "Human Review Required" rule. Standard subagent review applies to every other cluster.

---

## File Structure

### Backend

| File | Role | Status |
|---|---|---|
| `backend/nexus/app/modules/auth/schemas.py` | Add `LoginRequest` and `LoginResponse` | Modify |
| `backend/nexus/app/modules/auth/router.py` | Add `POST /api/auth/login` handler | Modify |
| `backend/nexus/app/middleware/auth.py` | Add `/api/auth/login` to `_PUBLIC_PREFIXES` | Modify |
| `backend/nexus/tests/test_auth_login.py` | NEW — endpoint tests (happy path, 401, 403 no tenant, 403 deactivated, 422) | Create |

### Frontend — shared

| File | Role | Status |
|---|---|---|
| `frontend/app/lib/api/client.ts` | Add `FastApiValidationError`, `ApiValidationError`; branch on 422 in `apiFetch` | Modify |
| `frontend/app/lib/api/errors.ts` | NEW — `applyApiErrorToForm` utility | Create |
| `frontend/app/lib/api/auth.ts` | Extend `authApi` with `login`, `completeOnboarding`, `setWorkspaceMode` | Modify |
| `frontend/app/lib/api/team.ts` | NEW — `teamApi` namespace | Create |
| `frontend/app/lib/api/org-units.ts` | Extend `orgUnitsApi` with `delete`, `removeMember` | Modify |

### Frontend — hooks (all new, one per file)

| File | Role |
|---|---|
| `frontend/app/lib/hooks/use-team-members.ts` | `useTeamMembers()` query |
| `frontend/app/lib/hooks/use-invite-team-member.ts` | `useInviteTeamMember()` mutation |
| `frontend/app/lib/hooks/use-resend-team-invite.ts` | `useResendTeamInvite()` mutation |
| `frontend/app/lib/hooks/use-revoke-team-invite.ts` | `useRevokeTeamInvite()` mutation |
| `frontend/app/lib/hooks/use-deactivate-user.ts` | `useDeactivateUser()` mutation |
| `frontend/app/lib/hooks/use-org-units.ts` | `useOrgUnits()` query |
| `frontend/app/lib/hooks/use-org-unit.ts` | `useOrgUnit(id)` query |
| `frontend/app/lib/hooks/use-org-unit-members.ts` | `useOrgUnitMembers(id)` query |
| `frontend/app/lib/hooks/use-roles.ts` | `useRoles()` query |
| `frontend/app/lib/hooks/use-create-org-unit.ts` | `useCreateOrgUnit()` mutation |
| `frontend/app/lib/hooks/use-update-org-unit.ts` | `useUpdateOrgUnit()` mutation |
| `frontend/app/lib/hooks/use-delete-org-unit.ts` | `useDeleteOrgUnit()` mutation |
| `frontend/app/lib/hooks/use-assign-role.ts` | `useAssignRole()` mutation |
| `frontend/app/lib/hooks/use-remove-role.ts` | `useRemoveRole()` mutation |

### Frontend — pages

| File | Role | Status |
|---|---|---|
| `frontend/app/app/(auth)/login/page.tsx` | Migrate to RHF + Zod + `authApi.login` | Modify |
| `frontend/app/app/(auth)/login/schema.ts` | NEW — `loginSchema` + `LoginFormValues` | Create |
| `frontend/app/app/(auth)/invite/page.tsx` | Migrate to RHF + Zod | Modify |
| `frontend/app/app/(auth)/invite/schema.ts` | NEW — `inviteSchema` + `InviteFormValues` | Create |
| `frontend/app/app/onboarding/page.tsx` | Migrate to `authApi` wrappers + `applyApiErrorToForm` | Modify |
| `frontend/app/app/(dashboard)/settings/team/page.tsx` | Migrate data + forms | Modify |
| `frontend/app/app/(dashboard)/settings/team/schema.ts` | NEW — `inviteTeamMemberSchema` | Create |
| `frontend/app/app/(dashboard)/settings/org-units/page.tsx` | Migrate data + forms | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/schema.ts` | NEW — `createOrgUnitSchema` | Create |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Migrate to hooks | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts` | NEW — shared schemas for detail forms | Create |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail.tsx` | Migrate to hooks + RHF | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/DivisionDetail.tsx` | Migrate to hooks + RHF | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/RegionDetail.tsx` | Migrate to hooks + RHF | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/TeamDetail.tsx` | Migrate to hooks + RHF | Modify |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx` | Migrate to hooks + RHF; fold `confirm()` → `Dialog` | Modify |

### Frontend — tests (all new)

| File | Role |
|---|---|
| `frontend/app/tests/api/api-validation-error.test.ts` | `ApiValidationError` shape and `apiFetch` 422 branching |
| `frontend/app/tests/api/apply-api-error-to-form.test.ts` | `applyApiErrorToForm` mapping cases |
| `frontend/app/tests/auth/login-page.test.tsx` | Login page calls `authApi.login`; zero `signInWithPassword` calls |
| `frontend/app/tests/auth/invite-page-form.test.tsx` | Invite page uses RHF validation; password confirm works |
| `frontend/app/tests/settings/team-invite-form.test.tsx` | Team invite form submits + maps 422 |
| `frontend/app/tests/settings/create-org-unit-form.test.tsx` | Org unit create form submits + validates |
| `frontend/app/tests/settings/members-section-dialog.test.tsx` | MembersSection uses Dialog, not `confirm()` |

---

## Pre-flight

- [ ] **P.1:** Confirm working tree is on branch `cleanup/batch-4-form-migration` at worktree `.worktrees/cleanup-batch-4`.
  ```bash
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4 status
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4 branch --show-current
  ```
  Expected: clean tree, branch = `cleanup/batch-4-form-migration`.

- [ ] **P.2:** Frontend baseline green. The `type-check` script does not exist in `package.json`; use `npx tsc --noEmit` directly.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  cp /home/ishant/Projects/ProjectX/frontend/app/.env.local ./.env.local 2>/dev/null || true
  npm install
  npx tsc --noEmit && npm run lint && npm run test && npm run build
  ```
  Expected: all green. 63/63 vitest. Lint 0 errors. `next build` succeeds.

- [ ] **P.3:** Backend baseline green. Same 4 deselects as B3.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose up -d postgres redis
  docker compose run --rm nexus pytest -x \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: 490 passed, 4 deselected.

- [ ] **P.4:** Alembic head is `0017_sq_updated_at_trigger`. B4 does not touch the database.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose run --rm nexus alembic current
  ```
  Expected: `0017_sq_updated_at_trigger (head)`.

---

## Phase 1 — Shared utilities (Cluster C1)

These two tasks unblock every page migration. Both land before any other code. Subagent review cadence: combined spec + quality pass per task (small, mechanical, but shared across 40+ consumers).

## Task 1: `ApiValidationError` subclass + `apiFetch` 422 branching

**Goal:** Preserve the raw FastAPI 422 `detail` array on the thrown error so `applyApiErrorToForm` can do per-field mapping. Add `ApiValidationError extends ApiError` with a required `fieldErrors` property. `ApiError` stays unchanged for all other statuses; `instanceof ApiError` still matches `ApiValidationError` (subclass), so every existing narrowing continues to work.

**Files:**
- Modify: `frontend/app/lib/api/client.ts`
- Create: `frontend/app/tests/api/api-validation-error.test.ts`

- [ ] **Step 1.1: Write failing test**

  File: `frontend/app/tests/api/api-validation-error.test.ts`
  ```ts
  import { describe, expect, it, vi } from 'vitest'

  import {
    ApiError,
    ApiValidationError,
    apiFetch,
  } from '@/lib/api/client'

  function mockFetch(status: number, body: unknown): void {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
  }

  describe('ApiValidationError', () => {
    it('is thrown on 422 responses with a detail array', async () => {
      mockFetch(422, {
        detail: [
          { loc: ['body', 'email'], msg: 'value is not a valid email', type: 'value_error.email' },
          { loc: ['body', 'password'], msg: 'ensure this value has at least 8 characters', type: 'value_error.any_str.min_length' },
        ],
      })

      await expect(apiFetch('/api/anything', { method: 'POST' })).rejects.toSatisfy((err) => {
        return (
          err instanceof ApiValidationError &&
          err instanceof ApiError &&
          err.status === 422 &&
          err.fieldErrors.length === 2 &&
          err.fieldErrors[0].loc[1] === 'email'
        )
      })
    })

    it('still throws ApiError (not ApiValidationError) on 422 with string detail', async () => {
      mockFetch(422, { detail: 'not an array' })

      await expect(apiFetch('/api/anything', { method: 'POST' })).rejects.toSatisfy((err) => {
        return err instanceof ApiError && !(err instanceof ApiValidationError)
      })
    })

    it('throws plain ApiError on non-422 failures', async () => {
      mockFetch(400, { detail: 'bad request' })

      await expect(apiFetch('/api/anything')).rejects.toSatisfy((err) => {
        return err instanceof ApiError && !(err instanceof ApiValidationError) && err.status === 400
      })
    })

    it('sets a human-readable message joining field errors', async () => {
      mockFetch(422, {
        detail: [
          { loc: ['body', 'email'], msg: 'invalid email', type: 'x' },
          { loc: ['body', 'password'], msg: 'too short', type: 'y' },
        ],
      })

      await expect(apiFetch('/api/anything')).rejects.toThrow(/invalid email, too short/)
    })
  })
  ```

- [ ] **Step 1.2: Run test to verify it fails**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/api/api-validation-error.test.ts
  ```
  Expected: FAIL — `ApiValidationError` is not exported.

- [ ] **Step 1.3: Implement `ApiValidationError` + modify `apiFetch`**

  Modify `frontend/app/lib/api/client.ts`. The file currently exports `ApiError` and `apiFetch`. Add the new exports and branch the 422 path. Final file content:
  ```ts
  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  /**
   * Error thrown by apiFetch when the backend returns a non-OK response.
   *
   * Carries the HTTP status code alongside the parsed detail message so
   * callers can branch on status (e.g. 404 => "not found, return null")
   * without resorting to fragile substring matching on err.message.
   *
   * Extends the built-in Error, so any existing `catch (err) { err.message }`
   * code continues to work unchanged.
   */
  export class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  /**
   * Shape of a single entry in FastAPI's 422 `detail` array.
   *
   * `loc` is an array like `["body", "email"]` or `["body", "profile", "about"]`.
   * `applyApiErrorToForm` strips the leading `"body"` and walks the rest to
   * produce a dotted react-hook-form path.
   */
  export interface FastApiValidationError {
    loc: (string | number)[];
    msg: string;
    type: string;
  }

  /**
   * Specialised error for FastAPI 422 responses carrying structured
   * field errors. `fieldErrors` is REQUIRED — if the raw detail isn't
   * an array, `apiFetch` throws a plain `ApiError` instead.
   *
   * Callers should narrow on `err instanceof ApiValidationError` to get
   * typed, non-null access to `fieldErrors`. The `instanceof ApiError`
   * narrowing still matches (subclass), so existing 401 / 403 / 4xx
   * handlers continue to work unchanged.
   */
  export class ApiValidationError extends ApiError {
    fieldErrors: FastApiValidationError[];
    constructor(message: string, fieldErrors: FastApiValidationError[]) {
      super(message, 422);
      this.name = "ApiValidationError";
      this.fieldErrors = fieldErrors;
    }
  }

  /**
   * Typed `fetch` wrapper for talking to Nexus.
   *
   * - Auto-injects `Authorization: Bearer <token>` when `token` is provided.
   * - Threads the optional `signal` into the underlying `fetch` so TanStack
   *   Query (or any caller) can cancel in-flight requests.
   * - Throws `ApiValidationError` on 422 responses whose `detail` is an
   *   array of `FastApiValidationError`. Throws plain `ApiError` on every
   *   other non-OK response (including 422 with a non-array detail).
   * - Returns `undefined` for 204 No Content responses. **Type the call as
   *   `apiFetch<void>('/api/...')` for endpoints that return 204** —
   *   otherwise the asserted `T` will silently be `undefined` at runtime
   *   and any property access on the result will throw.
   */
  export async function apiFetch<T>(
    path: string,
    options: RequestInit & { token?: string; signal?: AbortSignal } = {},
  ): Promise<T> {
    const { token, signal, ...fetchOptions } = options;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };

    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    const res = await fetch(`${API_URL}${path}`, {
      ...fetchOptions,
      headers,
      signal,
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = body.detail;
      if (res.status === 422 && Array.isArray(detail)) {
        const fieldErrors = detail as FastApiValidationError[];
        const message = fieldErrors.map((e) => e.msg).join(', ');
        throw new ApiValidationError(message, fieldErrors);
      }
      const message = Array.isArray(detail)
        ? detail.map((e: { msg?: string }) => e.msg ?? String(e)).join(', ')
        : typeof detail === 'string'
          ? detail
          : `API error: ${res.status}`;
      throw new ApiError(message, res.status);
    }

    if (res.status === 204) return undefined as T;

    return res.json();
  }
  ```

- [ ] **Step 1.4: Run test to verify it passes**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/api/api-validation-error.test.ts
  ```
  Expected: 4/4 pass.

- [ ] **Step 1.5: Full test + type + lint check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint && npm run test
  ```
  Expected: tsc 0 errors, lint 0 errors, vitest 67/67 (63 pre-existing + 4 new).

- [ ] **Step 1.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/api/client.ts frontend/app/tests/api/api-validation-error.test.ts
  git commit -m "feat(api): add ApiValidationError subclass for 422 per-field mapping"
  ```

---

## Task 2: `applyApiErrorToForm` utility

**Goal:** A single, error-shape-agnostic helper that callers pass any error into. If it's an `ApiValidationError`, it walks `fieldErrors` and calls `form.setError` on each matched path. Returns `true` if it mapped at least one field (caller suppresses toast); `false` otherwise (caller falls back to a toast). Nested `loc` like `["body", "profile", "about"]` maps to `"profile.about"`. An unmapped field falls back to `opts.fallbackFieldKey` or — as a last resort — the RHF `root` error slot.

**Files:**
- Create: `frontend/app/lib/api/errors.ts`
- Create: `frontend/app/tests/api/apply-api-error-to-form.test.ts`

- [ ] **Step 2.1: Write failing tests**

  File: `frontend/app/tests/api/apply-api-error-to-form.test.ts`
  ```ts
  import { describe, expect, it } from 'vitest'
  import { renderHook } from '@testing-library/react'
  import { useForm } from 'react-hook-form'
  import { z } from 'zod'
  import { zodResolver } from '@hookform/resolvers/zod'

  import { ApiError, ApiValidationError } from '@/lib/api/client'
  import { applyApiErrorToForm } from '@/lib/api/errors'

  const schema = z.object({
    email: z.string().email(),
    password: z.string().min(8),
    profile: z.object({
      about: z.string().min(10),
    }),
  })

  type FormValues = z.infer<typeof schema>

  function renderForm() {
    return renderHook(() =>
      useForm<FormValues>({
        resolver: zodResolver(schema),
        defaultValues: {
          email: '',
          password: '',
          profile: { about: '' },
        },
      }),
    ).result
  }

  describe('applyApiErrorToForm', () => {
    it('returns false for non-ApiValidationError inputs', () => {
      const form = renderForm().current
      expect(applyApiErrorToForm(new Error('boom'), form)).toBe(false)
      expect(applyApiErrorToForm(new ApiError('401', 401), form)).toBe(false)
      expect(applyApiErrorToForm('string error', form)).toBe(false)
      expect(applyApiErrorToForm(undefined, form)).toBe(false)
    })

    it('maps a top-level body field', () => {
      const form = renderForm().current
      const err = new ApiValidationError('invalid email', [
        { loc: ['body', 'email'], msg: 'invalid email', type: 'x' },
      ])
      expect(applyApiErrorToForm(err, form)).toBe(true)
      expect(form.formState.errors.email?.message).toBe('invalid email')
    })

    it('maps multiple fields in one call', () => {
      const form = renderForm().current
      const err = new ApiValidationError('multi', [
        { loc: ['body', 'email'], msg: 'bad email', type: 'x' },
        { loc: ['body', 'password'], msg: 'too short', type: 'y' },
      ])
      expect(applyApiErrorToForm(err, form)).toBe(true)
      expect(form.formState.errors.email?.message).toBe('bad email')
      expect(form.formState.errors.password?.message).toBe('too short')
    })

    it('maps nested body fields with dotted paths', () => {
      const form = renderForm().current
      const err = new ApiValidationError('nested', [
        { loc: ['body', 'profile', 'about'], msg: 'too short', type: 'x' },
      ])
      expect(applyApiErrorToForm(err, form)).toBe(true)
      expect(form.formState.errors.profile?.about?.message).toBe('too short')
    })

    it('falls back to fallbackFieldKey when loc does not match a known field', () => {
      const form = renderForm().current
      const err = new ApiValidationError('unknown', [
        { loc: ['body', 'mystery_field'], msg: 'nope', type: 'x' },
      ])
      expect(
        applyApiErrorToForm(err, form, { fallbackFieldKey: 'email' }),
      ).toBe(true)
      expect(form.formState.errors.email?.message).toBe('nope')
    })

    it('falls back to root error when no fallback provided', () => {
      const form = renderForm().current
      const err = new ApiValidationError('unknown', [
        { loc: ['body', 'mystery_field'], msg: 'nope', type: 'x' },
      ])
      expect(applyApiErrorToForm(err, form)).toBe(true)
      expect(form.formState.errors.root?.message).toBe('nope')
    })

    it('returns true when at least one field maps (mixed match + miss)', () => {
      const form = renderForm().current
      const err = new ApiValidationError('mixed', [
        { loc: ['body', 'email'], msg: 'bad', type: 'x' },
        { loc: ['body', 'unknown'], msg: 'also bad', type: 'x' },
      ])
      expect(applyApiErrorToForm(err, form)).toBe(true)
      expect(form.formState.errors.email?.message).toBe('bad')
    })
  })
  ```

- [ ] **Step 2.2: Run test to verify it fails**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/api/apply-api-error-to-form.test.ts
  ```
  Expected: FAIL — `applyApiErrorToForm` is not exported.

- [ ] **Step 2.3: Implement `applyApiErrorToForm`**

  File: `frontend/app/lib/api/errors.ts`
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
   * - FastAPI prepends `"body"` to every `loc`. We strip it.
   * - The remaining segments are joined with `.` to produce an RHF path
   *   (e.g. `["profile", "about"]` → `"profile.about"`).
   * - If the resulting path is not a known field on the form, the error
   *   falls back to `opts.fallbackFieldKey` (if provided) or `root`.
   */
  export function applyApiErrorToForm<T extends FieldValues>(
    err: unknown,
    form: UseFormReturn<T>,
    opts: { fallbackFieldKey?: Path<T> } = {},
  ): boolean {
    if (!(err instanceof ApiValidationError)) return false

    const knownFieldKeys = collectFieldKeys(form.getValues())
    let mappedAny = false

    for (const entry of err.fieldErrors) {
      const path = locToPath(entry.loc)
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
   * Drop the leading `"body"` segment (FastAPI always prepends it for
   * request-body validation errors), then join the rest with `.`.
   * Returns null for shapes we don't recognise (e.g. empty after strip).
   */
  function locToPath(loc: (string | number)[]): string | null {
    const stripped = loc[0] === 'body' ? loc.slice(1) : loc
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

- [ ] **Step 2.4: Run test to verify it passes**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/api/apply-api-error-to-form.test.ts
  ```
  Expected: 7/7 pass.

- [ ] **Step 2.5: Full type + lint + test**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint && npm run test
  ```
  Expected: tsc 0 errors, lint 0 errors, vitest 74/74.

- [ ] **Step 2.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/api/errors.ts frontend/app/tests/api/apply-api-error-to-form.test.ts
  git commit -m "feat(api): add applyApiErrorToForm for per-field 422 mapping"
  ```

---

## Phase 2 — Backend login endpoint (Cluster C2)

One endpoint, gated by the CLAUDE.md "human review required" rule (touches `app/modules/auth/`). Subagent review cadence: **split** — spec review and quality review as separate passes.

## Task 3: `LoginRequest` / `LoginResponse` schemas

**Goal:** Add the request/response Pydantic models for the new login endpoint, matching §8.5.1 exactly.

**Files:**
- Modify: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 3.1: Read current schemas to find insertion point**

  ```bash
  grep -n 'class ' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus/app/modules/auth/schemas.py
  ```
  Expected: list includes `AcceptInviteRequest`, `AcceptInviteResponse`, `VerifyInviteResponse`, `MeResponse`, `RoleAssignmentResponse`. Add the new classes below `AcceptInviteResponse`.

- [ ] **Step 3.2: Add schemas**

  Modify `backend/nexus/app/modules/auth/schemas.py`. After the existing `AcceptInviteResponse` class, append:
  ```python
  class LoginRequest(BaseModel):
      """Request body for POST /api/auth/login.

      `email` uses `EmailStr` so the 422 path catches malformed addresses
      before the handler touches the AuthProvider — no user enumeration
      surface for syntax errors.
      """

      email: EmailStr
      password: str


  class LoginResponse(BaseModel):
      """Response body for POST /api/auth/login.

      `redirect_to` is computed server-side from `users.onboarding_complete`
      so the frontend never has to decode the access_token to pick a
      post-login route.
      """

      access_token: str
      refresh_token: str
      expires_in: int
      redirect_to: str
  ```
  If `EmailStr` is not already imported at the top of the file, add it to the `from pydantic import ...` line.

- [ ] **Step 3.3: Quick smoke import**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose run --rm nexus python -c "from app.modules.auth.schemas import LoginRequest, LoginResponse; print(LoginRequest.model_json_schema()); print(LoginResponse.model_json_schema())"
  ```
  Expected: prints the two JSON schemas, no import errors.

- [ ] **Step 3.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add backend/nexus/app/modules/auth/schemas.py
  git commit -m "feat(auth): add LoginRequest/LoginResponse schemas"
  ```

---

## Task 4: `POST /api/auth/login` handler + public-prefix middleware + tests

**Goal:** Implement the handler per §8.5.1. Public endpoint — add `/api/auth/login` to `_PUBLIC_PREFIXES` in the auth middleware. Error matrix: 401 for bad credentials (generic message), 403 for missing tenant_id / deactivated account, 422 for Pydantic validation failures.

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py`
- Modify: `backend/nexus/app/middleware/auth.py`
- Create: `backend/nexus/tests/test_auth_login.py`

- [ ] **Step 4.1: Inspect current middleware public prefixes**

  ```bash
  grep -n '_PUBLIC_PREFIXES' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus/app/middleware/auth.py
  ```
  Expected: a tuple/list constant listing `/api/auth/verify-invite`, `/api/auth/accept-invite`, and similar public paths. Add `/api/auth/login` to it.

- [ ] **Step 4.2: Write failing tests**

  File: `backend/nexus/tests/test_auth_login.py`
  ```python
  """Integration tests for POST /api/auth/login.

  The AuthProvider is overridden via FastAPI's dependency_overrides so
  each test controls exactly what the provider returns — happy path,
  InvalidCredentialsError, UserNotFoundError, etc.
  """
  from __future__ import annotations

  import uuid
  from unittest.mock import AsyncMock

  import pytest
  from httpx import ASGITransport, AsyncClient

  from app.main import app
  from app.modules.auth.admin import get_auth_provider
  from app.modules.auth.admin.base import (
      InvalidCredentialsError,
      SessionTokens,
      UserNotFoundError,
  )


  def _override_provider(**methods: AsyncMock):
      fake = type("FakeProvider", (), methods)()
      app.dependency_overrides[get_auth_provider] = lambda: fake
      return fake


  @pytest.fixture(autouse=True)
  def _clear_overrides():
      yield
      app.dependency_overrides.clear()


  @pytest.mark.asyncio
  async def test_login_rejects_malformed_email() -> None:
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login", json={"email": "not-an-email", "password": "hunter2hunter2"}
          )
      assert resp.status_code == 422
      body = resp.json()
      assert isinstance(body["detail"], list)
      assert any(err["loc"][-1] == "email" for err in body["detail"])


  @pytest.mark.asyncio
  async def test_login_rejects_missing_fields() -> None:
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post("/api/auth/login", json={})
      assert resp.status_code == 422


  @pytest.mark.asyncio
  async def test_login_invalid_credentials_returns_401_generic_message() -> None:
      _override_provider(
          sign_in_with_password=AsyncMock(side_effect=InvalidCredentialsError("bad pw")),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": "user@example.com", "password": "wrongpass"},
          )
      assert resp.status_code == 401
      # No user-enumeration — message is generic, not "no such user" vs "bad password"
      assert resp.json()["detail"] == "Invalid email or password."


  @pytest.mark.asyncio
  async def test_login_user_not_found_returns_401_same_generic_message() -> None:
      _override_provider(
          sign_in_with_password=AsyncMock(side_effect=UserNotFoundError("no user")),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": "nobody@example.com", "password": "whatever"},
          )
      assert resp.status_code == 401
      assert resp.json()["detail"] == "Invalid email or password."


  @pytest.mark.asyncio
  async def test_login_happy_path_returns_tokens_and_redirect(
      seeded_active_user_factory,  # pytest fixture declared in tests/conftest.py
  ) -> None:
      """Happy path: valid login for a super admin with onboarding NOT complete.

      The factory fixture MUST:
        - create a Client row (onboarding_complete=False)
        - create a User row (is_active=True, with a tenant_id)
        - return (user_id, tenant_id, email, access_token_stub)

      If this fixture doesn't exist yet, copy the pattern from
      test_accept_invite.py (B3 test) which already does similar seeding.
      """
      user = await seeded_active_user_factory(
          is_active=True, is_super_admin=True, onboarding_complete=False
      )
      _override_provider(
          sign_in_with_password=AsyncMock(
              return_value=SessionTokens(
                  access_token=user.access_token_stub,
                  refresh_token="refresh-abc",
                  expires_in=3600,
              )
          ),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": user.email, "password": "correctpass"},
          )
      assert resp.status_code == 200
      body = resp.json()
      assert body["access_token"] == user.access_token_stub
      assert body["refresh_token"] == "refresh-abc"
      assert body["expires_in"] == 3600
      assert body["redirect_to"] == "/onboarding"


  @pytest.mark.asyncio
  async def test_login_completed_onboarding_redirects_to_root(
      seeded_active_user_factory,
  ) -> None:
      user = await seeded_active_user_factory(
          is_active=True, is_super_admin=True, onboarding_complete=True
      )
      _override_provider(
          sign_in_with_password=AsyncMock(
              return_value=SessionTokens(
                  access_token=user.access_token_stub,
                  refresh_token="r",
                  expires_in=3600,
              )
          ),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": user.email, "password": "x"},
          )
      assert resp.status_code == 200
      assert resp.json()["redirect_to"] == "/"


  @pytest.mark.asyncio
  async def test_login_deactivated_account_returns_403(
      seeded_active_user_factory,
  ) -> None:
      user = await seeded_active_user_factory(is_active=False)
      _override_provider(
          sign_in_with_password=AsyncMock(
              return_value=SessionTokens(
                  access_token=user.access_token_stub,
                  refresh_token="r",
                  expires_in=3600,
              )
          ),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": user.email, "password": "x"},
          )
      assert resp.status_code == 403
      assert "deactivated" in resp.json()["detail"].lower()


  @pytest.mark.asyncio
  async def test_login_missing_tenant_id_returns_403() -> None:
      """A token whose payload lacks tenant_id — e.g. a ProjectX-admin-only
      account. The handler rejects before installing a session.
      """
      # An access_token whose decoded payload has no tenant_id. We use the
      # existing verify_access_token path; the simplest way to exercise this
      # is to have the provider return a token for an email that doesn't
      # resolve to a user row in the DB. verify_access_token will decode
      # successfully but the user-lookup step raises.
      _override_provider(
          sign_in_with_password=AsyncMock(
              return_value=SessionTokens(
                  access_token="token.without.tenant",
                  refresh_token="r",
                  expires_in=3600,
              )
          ),
      )
      async with AsyncClient(
          transport=ASGITransport(app=app), base_url="http://test"
      ) as client:
          resp = await client.post(
              "/api/auth/login",
              json={"email": "admin@projectx.test", "password": "x"},
          )
      # Accept either 401 (provider-signalled) or 403 (handler-signalled)
      # depending on whether the token decodes cleanly. Document the exact
      # code the handler settles on once wired.
      assert resp.status_code in (401, 403)
  ```

  Note on the `seeded_active_user_factory` fixture: if it doesn't already exist in `tests/conftest.py`, create it alongside this test file or add it to `conftest.py`. The B3 test `test_accept_invite.py` contains analogous seeding code — mirror its shape. The factory should:
  - insert a `clients` row with the requested `onboarding_complete`
  - insert a `users` row with the requested `is_active` and `is_super_admin`
  - mint a minimal ES256-signed access_token whose payload includes `sub=user.auth_user_id`, `aud="authenticated"`, `tenant_id=<client.id>`, `iss=<supabase_url>/auth/v1`
  - return a simple namespace object with `user_id`, `tenant_id`, `email`, `access_token_stub`

  If copying from B3 turns out to be more work than expected, replace the factory-dependent tests with `monkeypatch`-based tests that stub `verify_access_token` directly — document the choice in the commit message.

- [ ] **Step 4.3: Run tests to verify they fail**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose run --rm nexus pytest tests/test_auth_login.py -v
  ```
  Expected: all tests FAIL with 404 (endpoint doesn't exist yet).

- [ ] **Step 4.4: Add `/api/auth/login` to `_PUBLIC_PREFIXES`**

  Modify `backend/nexus/app/middleware/auth.py`. Find the `_PUBLIC_PREFIXES` constant and add `"/api/auth/login"` in the same style as the existing entries.

- [ ] **Step 4.5: Implement the handler**

  Modify `backend/nexus/app/modules/auth/router.py`. At the top of the file (alongside the existing schema imports), add `LoginRequest` and `LoginResponse`:
  ```python
  from app.modules.auth.schemas import (
      AcceptInviteRequest,
      AcceptInviteResponse,
      LoginRequest,
      LoginResponse,
      MeResponse,
      RoleAssignmentResponse,
      VerifyInviteResponse,
  )
  ```

  Also add an import for `verify_access_token` at the top (inline-import inside the handler is fine too):
  ```python
  from app.modules.auth.service import verify_access_token
  ```

  Append the handler below the existing `accept_invite` endpoint and before `get_current_user`:
  ```python
  @router.post("/login", response_model=LoginResponse)
  async def login(
      data: LoginRequest,
      request: Request,
      db: AsyncSession = Depends(get_bypass_db),
  ) -> LoginResponse:
      """Backend-owned login.

      Moves the last `supabase.auth.signInWithPassword` call behind the
      provider-agnostic AuthProvider boundary so a future Cognito/Keycloak
      swap is a config change, not a code rewrite.

      Error contract:
      - 401 for invalid credentials / unknown user. Generic message,
        no user enumeration.
      - 403 for accounts missing tenant_id (ProjectX-admin-only).
      - 403 for deactivated accounts (users.is_active = false).
      - 422 for Pydantic validation failures (handled by FastAPI).

      Public endpoint — no bearer token required (see middleware
      _PUBLIC_PREFIXES).
      """
      from app.modules.auth.admin import (
          InvalidCredentialsError,
          UserNotFoundError,
          get_auth_provider,
      )

      provider = get_auth_provider()

      # Phase 1: sign in. Generic 401 on any credential/user-not-found path.
      try:
          tokens = await provider.sign_in_with_password(data.email, data.password)
      except (InvalidCredentialsError, UserNotFoundError):
          logger.info("auth.login.rejected", email=data.email, reason="invalid_credentials")
          raise HTTPException(status_code=401, detail="Invalid email or password.")

      # Phase 2: decode the access token to pull tenant_id. The provider
      # returned tokens but the app layer still has final veto — for
      # example, a ProjectX-admin-only account has no tenant and must
      # not land on the client dashboard.
      try:
          payload = await verify_access_token(tokens.access_token)
      except Exception:
          logger.error("auth.login.token_verify_failed", email=data.email)
          raise HTTPException(status_code=401, detail="Invalid email or password.")

      tenant_id = payload.get("tenant_id") or ""
      if not tenant_id:
          logger.info("auth.login.no_tenant", email=data.email)
          raise HTTPException(
              status_code=403,
              detail="This account does not have access to the client dashboard.",
          )

      # Phase 3: app user lookup. Reject deactivated accounts.
      user_row = await db.execute(
          select(User).where(User.email == data.email)
      )
      user = user_row.scalar_one_or_none()
      if user is None:
          # Auth provider succeeded but we have no app user row — treat
          # as a no-access condition, same 403 shape.
          logger.error("auth.login.no_app_user", email=data.email)
          raise HTTPException(
              status_code=403,
              detail="This account does not have access to the client dashboard.",
          )
      if not user.is_active:
          logger.info("auth.login.deactivated", user_id=str(user.id))
          raise HTTPException(
              status_code=403,
              detail="This account has been deactivated.",
          )

      # Phase 4: compute redirect_to. Super admins who haven't finished
      # onboarding land on /onboarding; everyone else lands on /.
      client_row = await db.execute(
          select(Client).where(Client.id == user.tenant_id)
      )
      client = client_row.scalar_one()
      is_super_admin = client.super_admin_id == user.id
      redirect_to = (
          "/onboarding"
          if is_super_admin and not client.onboarding_complete
          else "/"
      )

      logger.info(
          "auth.login.success",
          user_id=str(user.id),
          tenant_id=str(user.tenant_id),
          redirect_to=redirect_to,
      )

      return LoginResponse(
          access_token=tokens.access_token,
          refresh_token=tokens.refresh_token,
          expires_in=tokens.expires_in,
          redirect_to=redirect_to,
      )
  ```

- [ ] **Step 4.6: Run tests to verify they pass**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose run --rm nexus pytest tests/test_auth_login.py -v
  ```
  Expected: all tests pass. If `seeded_active_user_factory` isn't defined, the two happy-path and the deactivated tests will error — author the fixture at that point (see note in Step 4.2).

- [ ] **Step 4.7: Full backend suite green**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose run --rm nexus pytest -x \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: 490 + new login tests passed, 4 deselected.

- [ ] **Step 4.8: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add \
    backend/nexus/app/modules/auth/router.py \
    backend/nexus/app/middleware/auth.py \
    backend/nexus/tests/test_auth_login.py \
    backend/nexus/tests/conftest.py  # only if you added seeded_active_user_factory here
  git commit -m "feat(auth): add POST /api/auth/login via AuthProvider abstraction"
  ```

---

## Phase 3 — Frontend API namespaces (Cluster C3)

Typed wrappers for every endpoint B4 pages will call. Subagent review cadence: combined spec + quality (small, mechanical wrapper additions). Tasks 5/6/7 can run in parallel after Phase 2.

## Task 5: Extend `lib/api/auth.ts` — `login`, `completeOnboarding`, `setWorkspaceMode`

**Goal:** Add typed wrappers for the three auth-adjacent endpoints the B4 pages call. `authApi.login` is the frontend mirror of Task 4.

**Files:**
- Modify: `frontend/app/lib/api/auth.ts`

- [ ] **Step 5.1: Add `login`, `completeOnboarding`, `setWorkspaceMode` to `authApi`**

  Modify `frontend/app/lib/api/auth.ts`. Append the new exports and methods. Final content:
  ```ts
  import { apiFetch } from './client'

  export interface MeResponse {
    user_id: string
    email: string
    full_name: string | null
    tenant_id: string
    client_name: string
    is_super_admin: boolean
    onboarding_complete: boolean
    has_org_units: boolean
    workspace_mode: string
    assignments: {
      org_unit_id: string
      org_unit_name: string
      role_name: string
      permissions: string[]
    }[]
  }

  export interface AcceptInviteRequest {
    raw_token: string
    password: string
  }

  export interface AcceptInviteResponse {
    access_token: string
    refresh_token: string
    expires_in: number
    redirect_to: string
  }

  export interface LoginRequest {
    email: string
    password: string
  }

  export interface LoginResponse {
    access_token: string
    refresh_token: string
    expires_in: number
    redirect_to: string
  }

  export interface SetWorkspaceModeRequest {
    workspace_mode: 'enterprise' | 'agency'
  }

  export interface SetWorkspaceModeResponse {
    status: string
    workspace_mode: string
  }

  export const authApi = {
    me: (token: string, opts?: { signal?: AbortSignal }): Promise<MeResponse> =>
      apiFetch<MeResponse>('/api/auth/me', { token, signal: opts?.signal }),

    acceptInvite: (
      body: AcceptInviteRequest,
      opts?: { signal?: AbortSignal },
    ): Promise<AcceptInviteResponse> =>
      apiFetch<AcceptInviteResponse>('/api/auth/accept-invite', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: opts?.signal,
      }),

    login: (
      body: LoginRequest,
      opts?: { signal?: AbortSignal },
    ): Promise<LoginResponse> =>
      apiFetch<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: opts?.signal,
      }),

    completeOnboarding: (
      token: string,
      opts?: { signal?: AbortSignal },
    ): Promise<{ status: string }> =>
      apiFetch<{ status: string }>('/api/auth/onboarding/complete', {
        method: 'POST',
        token,
        signal: opts?.signal,
      }),

    setWorkspaceMode: (
      token: string,
      body: SetWorkspaceModeRequest,
      opts?: { signal?: AbortSignal },
    ): Promise<SetWorkspaceModeResponse> =>
      apiFetch<SetWorkspaceModeResponse>('/api/settings/workspace', {
        method: 'PATCH',
        token,
        body: JSON.stringify(body),
        signal: opts?.signal,
      }),
  }
  ```

- [ ] **Step 5.2: Type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

- [ ] **Step 5.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/api/auth.ts
  git commit -m "feat(api): add authApi.login, completeOnboarding, setWorkspaceMode"
  ```

---

## Task 6: Create `lib/api/team.ts` — `teamApi` namespace

**Goal:** Typed wrappers for the six team-settings endpoints currently called via raw `apiFetch` in `settings/team/page.tsx`.

**Files:**
- Create: `frontend/app/lib/api/team.ts`

- [ ] **Step 6.1: Inspect backend response shapes**

  ```bash
  grep -nA 20 '^async def ' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus/app/modules/settings/router.py | head -120
  ```
  Expected: shows list_members, invite_team_member, resend_invite, revoke_invite, deactivate_user signatures. Note that `resend_invite`, `revoke_invite`, and `deactivate_user` return 204 No Content — type their frontend wrappers as `Promise<void>`.

- [ ] **Step 6.2: Write the namespace**

  File: `frontend/app/lib/api/team.ts`
  ```ts
  import { apiFetch } from './client'

  export interface TeamMemberAssignment {
    org_unit_id: string
    org_unit_name: string
    role_name: string
  }

  /**
   * A row returned by GET /api/settings/team/members. Covers both users
   * (with role assignments) and outstanding invites (awaiting claim).
   * Callers partition on `source`.
   */
  export interface TeamMember {
    id: string
    email: string
    full_name: string | null
    is_active: boolean
    is_super_admin: boolean
    assignments: TeamMemberAssignment[]
    source: 'user' | 'invite'
    status: string
    created_at: string
  }

  export interface InviteTeamMemberRequest {
    email: string
  }

  export interface InviteTeamMemberResponse {
    invite_url: string
  }

  export const teamApi = {
    list: (token: string, opts?: { signal?: AbortSignal }): Promise<TeamMember[]> =>
      apiFetch<TeamMember[]>('/api/settings/team/members', {
        token,
        signal: opts?.signal,
      }),

    invite: (
      token: string,
      body: InviteTeamMemberRequest,
    ): Promise<InviteTeamMemberResponse> =>
      apiFetch<InviteTeamMemberResponse>('/api/settings/team/invite', {
        method: 'POST',
        token,
        body: JSON.stringify(body),
      }),

    resend: (token: string, inviteId: string): Promise<void> =>
      apiFetch<void>(`/api/settings/team/resend/${inviteId}`, {
        method: 'POST',
        token,
      }),

    revoke: (token: string, inviteId: string): Promise<void> =>
      apiFetch<void>(`/api/settings/team/revoke/${inviteId}`, {
        method: 'POST',
        token,
      }),

    deactivate: (token: string, userId: string): Promise<void> =>
      apiFetch<void>(`/api/settings/team/deactivate/${userId}`, {
        method: 'POST',
        token,
      }),
  }
  ```

  If the backend endpoints actually return JSON bodies (not 204), adjust the `Promise<void>` types at wrapping time. Step 6.1 determines this — don't guess.

- [ ] **Step 6.3: Type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

- [ ] **Step 6.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/api/team.ts
  git commit -m "feat(api): add teamApi namespace for settings/team endpoints"
  ```

---

## Task 7: Extend `lib/api/org-units.ts` — `delete`, `removeMember`

**Goal:** Add the two remaining endpoints that B4.6 subcomponents will call.

**Files:**
- Modify: `frontend/app/lib/api/org-units.ts`

- [ ] **Step 7.1: Add methods**

  Modify `frontend/app/lib/api/org-units.ts`. Inside the `orgUnitsApi` object, add `delete` and `removeMember` below the existing `update` method:
  ```ts
    delete: (token: string, unitId: string): Promise<void> =>
      apiFetch<void>(`/api/org-units/${unitId}`, {
        method: 'DELETE',
        token,
      }),

    removeMember: (
      token: string,
      unitId: string,
      userId: string,
    ): Promise<void> =>
      apiFetch<void>(`/api/org-units/${unitId}/members/${userId}`, {
        method: 'DELETE',
        token,
      }),
  ```

- [ ] **Step 7.2: Type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

- [ ] **Step 7.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/api/org-units.ts
  git commit -m "feat(api): add orgUnitsApi.delete and removeMember"
  ```

---

## Phase 4 — Team hooks (Cluster C5)

One-hook-per-file per §8.5's D4.4 decision. Tasks 8/9 run in parallel after Phase 3. Subagent review: combined spec + quality (mechanical).

## Task 8: `useTeamMembers` query hook

**Files:**
- Create: `frontend/app/lib/hooks/use-team-members.ts`

- [ ] **Step 8.1: Implement**

  File: `frontend/app/lib/hooks/use-team-members.ts`
  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { teamApi, type TeamMember } from '@/lib/api/team'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useTeamMembers() {
    return useQuery<TeamMember[]>({
      queryKey: ['team', 'members'],
      queryFn: async ({ signal }) => {
        const token = await getFreshSupabaseToken()
        return teamApi.list(token, { signal })
      },
      staleTime: 10_000,
    })
  }
  ```

- [ ] **Step 8.2: Type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

- [ ] **Step 8.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-team-members.ts
  git commit -m "feat(hooks): add useTeamMembers query hook"
  ```

---

## Task 9: Team mutation hooks — invite, resend, revoke, deactivate

**Goal:** Four mutation hooks sharing the same shape. One commit because they're parallel mechanical creates. Each invalidates `['team', 'members']` on success.

**Files:**
- Create: `frontend/app/lib/hooks/use-invite-team-member.ts`
- Create: `frontend/app/lib/hooks/use-resend-team-invite.ts`
- Create: `frontend/app/lib/hooks/use-revoke-team-invite.ts`
- Create: `frontend/app/lib/hooks/use-deactivate-user.ts`

- [ ] **Step 9.1: Create `use-invite-team-member.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import {
    teamApi,
    type InviteTeamMemberRequest,
    type InviteTeamMemberResponse,
  } from '@/lib/api/team'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useInviteTeamMember() {
    const qc = useQueryClient()
    return useMutation<InviteTeamMemberResponse, Error, InviteTeamMemberRequest>({
      mutationFn: async (body) => {
        const token = await getFreshSupabaseToken()
        return teamApi.invite(token, body)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['team', 'members'] })
      },
    })
  }
  ```

- [ ] **Step 9.2: Create `use-resend-team-invite.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { teamApi } from '@/lib/api/team'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useResendTeamInvite() {
    const qc = useQueryClient()
    return useMutation<void, Error, string>({
      mutationFn: async (inviteId) => {
        const token = await getFreshSupabaseToken()
        return teamApi.resend(token, inviteId)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['team', 'members'] })
      },
    })
  }
  ```

- [ ] **Step 9.3: Create `use-revoke-team-invite.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { teamApi } from '@/lib/api/team'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useRevokeTeamInvite() {
    const qc = useQueryClient()
    return useMutation<void, Error, string>({
      mutationFn: async (inviteId) => {
        const token = await getFreshSupabaseToken()
        return teamApi.revoke(token, inviteId)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['team', 'members'] })
      },
    })
  }
  ```

- [ ] **Step 9.4: Create `use-deactivate-user.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { teamApi } from '@/lib/api/team'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useDeactivateUser() {
    const qc = useQueryClient()
    return useMutation<void, Error, string>({
      mutationFn: async (userId) => {
        const token = await getFreshSupabaseToken()
        return teamApi.deactivate(token, userId)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['team', 'members'] })
      },
    })
  }
  ```

- [ ] **Step 9.5: Type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint
  ```
  Expected: 0 errors on both.

- [ ] **Step 9.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-invite-team-member.ts \
          frontend/app/lib/hooks/use-resend-team-invite.ts \
          frontend/app/lib/hooks/use-revoke-team-invite.ts \
          frontend/app/lib/hooks/use-deactivate-user.ts
  git commit -m "feat(hooks): add team mutation hooks (invite/resend/revoke/deactivate)"
  ```

---

## Phase 5 — Org-units hooks (Cluster C6)

Nine hooks across four tasks. All invalidate `['org-units']` and — where relevant — the per-unit and members sub-keys. Tasks 10–13 run in parallel after Phase 3. Subagent review: combined per task.

## Task 10: Org-unit query hooks — `useOrgUnits`, `useOrgUnit`

**Files:**
- Create: `frontend/app/lib/hooks/use-org-units.ts`
- Create: `frontend/app/lib/hooks/use-org-unit.ts`

- [ ] **Step 10.1: Create `use-org-units.ts`**

  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useOrgUnits() {
    return useQuery<OrgUnit[]>({
      queryKey: ['org-units'],
      queryFn: async () => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.list(token)
      },
      staleTime: 10_000,
    })
  }
  ```

- [ ] **Step 10.2: Create `use-org-unit.ts`**

  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useOrgUnit(unitId: string) {
    return useQuery<OrgUnit>({
      queryKey: ['org-units', unitId],
      queryFn: async () => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.get(token, unitId)
      },
      enabled: !!unitId,
      staleTime: 10_000,
    })
  }
  ```

- [ ] **Step 10.3: Type-check and commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-org-units.ts frontend/app/lib/hooks/use-org-unit.ts
  git commit -m "feat(hooks): add useOrgUnits and useOrgUnit query hooks"
  ```

---

## Task 11: Member + role query hooks — `useOrgUnitMembers`, `useRoles`

**Files:**
- Create: `frontend/app/lib/hooks/use-org-unit-members.ts`
- Create: `frontend/app/lib/hooks/use-roles.ts`

- [ ] **Step 11.1: Create `use-org-unit-members.ts`**

  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { orgUnitsApi, type OrgUnitMember } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useOrgUnitMembers(unitId: string) {
    return useQuery<OrgUnitMember[]>({
      queryKey: ['org-units', unitId, 'members'],
      queryFn: async () => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.listMembers(token, unitId)
      },
      enabled: !!unitId,
      staleTime: 10_000,
    })
  }
  ```

- [ ] **Step 11.2: Create `use-roles.ts`**

  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { orgUnitsApi, type RoleOption } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useRoles() {
    return useQuery<RoleOption[]>({
      queryKey: ['roles'],
      queryFn: async () => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.listRoles(token)
      },
      // Roles are effectively static within a session
      staleTime: 5 * 60_000,
    })
  }
  ```

- [ ] **Step 11.3: Type-check and commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-org-unit-members.ts frontend/app/lib/hooks/use-roles.ts
  git commit -m "feat(hooks): add useOrgUnitMembers and useRoles query hooks"
  ```

---

## Task 12: Org-unit mutation hooks — `useCreateOrgUnit`, `useUpdateOrgUnit`, `useDeleteOrgUnit`

**Files:**
- Create: `frontend/app/lib/hooks/use-create-org-unit.ts`
- Create: `frontend/app/lib/hooks/use-update-org-unit.ts`
- Create: `frontend/app/lib/hooks/use-delete-org-unit.ts`

- [ ] **Step 12.1: Create `use-create-org-unit.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'
  import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

  export interface CreateOrgUnitInput {
    name: string
    unit_type: string
    parent_unit_id: string | null
    company_profile: CompanyProfile | null
    metadata?: Record<string, unknown> | null
  }

  export function useCreateOrgUnit() {
    const qc = useQueryClient()
    return useMutation<OrgUnit, Error, CreateOrgUnitInput>({
      mutationFn: async (body) => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.create(token, body)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['org-units'] })
      },
    })
  }
  ```

- [ ] **Step 12.2: Create `use-update-org-unit.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { orgUnitsApi, type OrgUnit, type OrgUnitMetadata } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'
  import type { CompanyProfile } from '@/components/dashboard/company-profile-form'

  export interface UpdateOrgUnitInput {
    unitId: string
    body: {
      name?: string
      company_profile?: CompanyProfile | null
      set_company_profile?: boolean
      metadata?: OrgUnitMetadata | null
      set_metadata?: boolean
    }
  }

  export function useUpdateOrgUnit() {
    const qc = useQueryClient()
    return useMutation<OrgUnit, Error, UpdateOrgUnitInput>({
      mutationFn: async ({ unitId, body }) => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.update(token, unitId, body)
      },
      onSuccess: (updated) => {
        void qc.invalidateQueries({ queryKey: ['org-units'] })
        void qc.invalidateQueries({ queryKey: ['org-units', updated.id] })
      },
    })
  }
  ```

- [ ] **Step 12.3: Create `use-delete-org-unit.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { orgUnitsApi } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export function useDeleteOrgUnit() {
    const qc = useQueryClient()
    return useMutation<void, Error, string>({
      mutationFn: async (unitId) => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.delete(token, unitId)
      },
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ['org-units'] })
      },
    })
  }
  ```

- [ ] **Step 12.4: Type-check and commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit
  ```
  Expected: 0 errors.

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-create-org-unit.ts \
          frontend/app/lib/hooks/use-update-org-unit.ts \
          frontend/app/lib/hooks/use-delete-org-unit.ts
  git commit -m "feat(hooks): add org-unit create/update/delete mutation hooks"
  ```

---

## Task 13: Role-assignment mutation hooks — `useAssignRole`, `useRemoveRole`

**Files:**
- Create: `frontend/app/lib/hooks/use-assign-role.ts`
- Create: `frontend/app/lib/hooks/use-remove-role.ts`

- [ ] **Step 13.1: Create `use-assign-role.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { orgUnitsApi } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export interface AssignRoleInput {
    unitId: string
    userId: string
    roleId: string
  }

  export function useAssignRole() {
    const qc = useQueryClient()
    return useMutation<{ status: string }, Error, AssignRoleInput>({
      mutationFn: async ({ unitId, userId, roleId }) => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.assignRole(token, unitId, {
          user_id: userId,
          role_id: roleId,
        })
      },
      onSuccess: (_data, { unitId }) => {
        void qc.invalidateQueries({ queryKey: ['org-units', unitId, 'members'] })
      },
    })
  }
  ```

- [ ] **Step 13.2: Create `use-remove-role.ts`**

  ```ts
  'use client'

  import { useMutation, useQueryClient } from '@tanstack/react-query'

  import { orgUnitsApi } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  export interface RemoveRoleInput {
    unitId: string
    userId: string
    roleId: string
  }

  export function useRemoveRole() {
    const qc = useQueryClient()
    return useMutation<{ status: string }, Error, RemoveRoleInput>({
      mutationFn: async ({ unitId, userId, roleId }) => {
        const token = await getFreshSupabaseToken()
        return orgUnitsApi.removeRole(token, unitId, userId, roleId)
      },
      onSuccess: (_data, { unitId }) => {
        void qc.invalidateQueries({ queryKey: ['org-units', unitId, 'members'] })
      },
    })
  }
  ```

- [ ] **Step 13.3: Type-check and commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint
  ```
  Expected: 0 errors.

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/lib/hooks/use-assign-role.ts frontend/app/lib/hooks/use-remove-role.ts
  git commit -m "feat(hooks): add role-assignment mutation hooks"
  ```

---

## Phase 6 — Auth + onboarding page migrations (Cluster C7)

Per §8.5.6: `(auth)` and `/onboarding` pages use plain RHF `handleSubmit` + `await` in `onSubmit` — **no `useMutation`** (no `QueryClient` in those route groups, and a login 401 must not trigger the dashboard's global `handleAuthError` redirect). Subagent review cadence: combined spec + quality per task.

## Task 14: B4.1 — Migrate `app/(auth)/login/page.tsx` to RHF + Zod + `authApi.login`

**Files:**
- Create: `frontend/app/app/(auth)/login/schema.ts`
- Modify: `frontend/app/app/(auth)/login/page.tsx`
- Create: `frontend/app/tests/auth/login-page.test.tsx`

- [ ] **Step 14.1: Create the schema**

  File: `frontend/app/app/(auth)/login/schema.ts`
  ```ts
  import { z } from 'zod'

  export const loginSchema = z.object({
    email: z
      .string()
      .min(1, 'Email is required')
      .email('Enter a valid email address'),
    password: z.string().min(1, 'Password is required'),
  })

  export type LoginFormValues = z.infer<typeof loginSchema>
  ```

- [ ] **Step 14.2: Write the failing test**

  File: `frontend/app/tests/auth/login-page.test.tsx`
  ```tsx
  import { describe, expect, it, vi, beforeEach } from 'vitest'
  import { render, screen, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  import LoginPage from '@/app/(auth)/login/page'

  const pushMock = vi.fn()
  const refreshMock = vi.fn()

  vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: pushMock, refresh: refreshMock }),
  }))

  const setSessionMock = vi.fn(async () => ({ error: null }))
  vi.mock('@/lib/supabase/client', () => ({
    createClient: () => ({
      auth: {
        setSession: setSessionMock,
        signInWithPassword: vi.fn(() => {
          throw new Error('signInWithPassword should never be called from the login page in B4')
        }),
      },
    }),
  }))

  const loginMock = vi.fn()
  vi.mock('@/lib/api/auth', () => ({
    authApi: { login: (body: unknown) => loginMock(body) },
  }))

  describe('LoginPage (B4)', () => {
    beforeEach(() => {
      pushMock.mockClear()
      refreshMock.mockClear()
      setSessionMock.mockClear()
      loginMock.mockReset()
    })

    it('submits credentials to authApi.login and installs the session', async () => {
      loginMock.mockResolvedValue({
        access_token: 'a.b.c',
        refresh_token: 'refresh',
        expires_in: 3600,
        redirect_to: '/',
      })

      render(<LoginPage />)
      await userEvent.type(screen.getByLabelText(/email/i), 'user@example.com')
      await userEvent.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
      await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

      await waitFor(() => {
        expect(loginMock).toHaveBeenCalledWith({
          email: 'user@example.com',
          password: 'hunter2hunter2',
        })
      })
      expect(setSessionMock).toHaveBeenCalledWith({
        access_token: 'a.b.c',
        refresh_token: 'refresh',
      })
      expect(pushMock).toHaveBeenCalledWith('/')
    })

    it('maps 401 to a form-level error without redirecting', async () => {
      const { ApiError } = await import('@/lib/api/client')
      loginMock.mockRejectedValue(
        new ApiError('Invalid email or password.', 401),
      )

      render(<LoginPage />)
      await userEvent.type(screen.getByLabelText(/email/i), 'bad@example.com')
      await userEvent.type(screen.getByLabelText(/^password$/i), 'nope')
      await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

      await waitFor(() => {
        expect(
          screen.getByText(/invalid email or password/i),
        ).toBeInTheDocument()
      })
      expect(pushMock).not.toHaveBeenCalled()
      expect(setSessionMock).not.toHaveBeenCalled()
    })

    it('does NOT reference supabase.auth.signInWithPassword', async () => {
      // Compile-time negative — imports checked via literal codebase grep
      // in the final-verification task. This test asserts the runtime path
      // never even touches signInWithPassword (the mock above would throw).
      loginMock.mockResolvedValue({
        access_token: 'a',
        refresh_token: 'r',
        expires_in: 10,
        redirect_to: '/',
      })

      render(<LoginPage />)
      await userEvent.type(screen.getByLabelText(/email/i), 'u@x.com')
      await userEvent.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
      await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
      await waitFor(() => expect(loginMock).toHaveBeenCalled())
    })
  })
  ```

- [ ] **Step 14.3: Run test to verify it fails**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/auth/login-page.test.tsx
  ```
  Expected: FAIL — the current `LoginPage` still calls `signInWithPassword`.

- [ ] **Step 14.4: Rewrite `page.tsx`**

  Replace `frontend/app/app/(auth)/login/page.tsx` with:
  ```tsx
  'use client'

  import { useState } from 'react'
  import { useRouter } from 'next/navigation'
  import { useForm } from 'react-hook-form'
  import { zodResolver } from '@hookform/resolvers/zod'

  import { authApi } from '@/lib/api/auth'
  import { ApiError } from '@/lib/api/client'
  import { applyApiErrorToForm } from '@/lib/api/errors'
  import { createClient } from '@/lib/supabase/client'

  import { loginSchema, type LoginFormValues } from './schema'

  function EyeIcon() {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    )
  }

  function EyeOffIcon() {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
        <line x1="1" y1="1" x2="23" y2="23" />
      </svg>
    )
  }

  export default function LoginPage() {
    const router = useRouter()
    const [showPassword, setShowPassword] = useState(false)

    const form = useForm<LoginFormValues>({
      resolver: zodResolver(loginSchema),
      defaultValues: { email: '', password: '' },
    })

    async function onSubmit(values: LoginFormValues) {
      try {
        const result = await authApi.login(values)

        const supabase = createClient()
        const { error: sessionError } = await supabase.auth.setSession({
          access_token: result.access_token,
          refresh_token: result.refresh_token,
        })
        if (sessionError) {
          form.setError('root', { message: sessionError.message })
          return
        }

        // Open-redirect guard: allow only same-origin relative paths.
        const safeRedirect =
          result.redirect_to.startsWith('/') &&
          !result.redirect_to.startsWith('//')
            ? result.redirect_to
            : '/'
        router.push(safeRedirect)
        router.refresh()
      } catch (err) {
        if (applyApiErrorToForm(err, form)) return
        if (err instanceof ApiError) {
          form.setError('root', { message: err.message })
          return
        }
        form.setError('root', {
          message: err instanceof Error ? err.message : 'An unexpected error occurred',
        })
      }
    }

    const rootError = form.formState.errors.root?.message

    return (
      <>
        <div className="mb-8 text-center">
          <div
            className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
            style={{ background: 'var(--px-accent)' }}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="white" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          </div>
          <h1
            className="px-serif m-0 text-[32px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            ProjectX
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
            Sign in to your recruiting dashboard
          </p>
        </div>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4 rounded-[12px] border p-7"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
            boxShadow: 'var(--px-shadow-sm)',
          }}
        >
          {rootError && (
            <p
              className="rounded-md border p-3 text-[13px]"
              style={{
                color: 'var(--px-danger)',
                background: 'var(--px-danger-bg)',
                borderColor: 'var(--px-danger-line)',
              }}
            >
              {rootError}
            </p>
          )}
          <div>
            <label htmlFor="login-email" className="px-label">Email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              className="px-input"
              placeholder="you@company.com"
              {...form.register('email')}
            />
            {form.formState.errors.email && (
              <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                {form.formState.errors.email.message}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="login-password" className="px-label">Password</label>
            <div className="relative">
              <input
                id="login-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                className="px-input pr-10"
                {...form.register('password')}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex cursor-pointer items-center px-3"
                style={{ color: 'var(--px-fg-4)' }}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOffIcon /> : <EyeIcon />}
              </button>
            </div>
            {form.formState.errors.password && (
              <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                {form.formState.errors.password.message}
              </p>
            )}
          </div>
          <button
            type="submit"
            disabled={form.formState.isSubmitting}
            className="px-btn primary lg w-full"
          >
            {form.formState.isSubmitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="mt-4 text-center text-[12.5px]" style={{ color: 'var(--px-fg-4)' }}>
          Don&apos;t have an account? Contact your{' '}
          <strong className="font-semibold" style={{ color: 'var(--px-fg-3)' }}>
            Company Admin
          </strong>{' '}
          for an invite.
        </p>
      </>
    )
  }
  ```

- [ ] **Step 14.5: Run tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/auth/login-page.test.tsx && npx tsc --noEmit && npm run lint
  ```
  Expected: 3/3 login tests pass. tsc and lint 0 errors.

- [ ] **Step 14.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(auth\)/login/ frontend/app/tests/auth/login-page.test.tsx
  git commit -m "refactor(login): migrate to RHF+Zod+authApi.login, remove signInWithPassword"
  ```

---

## Task 15: B4.2 — Migrate `app/(auth)/invite/page.tsx` to RHF + Zod

**Goal:** B3 already moved the submission behind `authApi.acceptInvite`; B4 migrates the local form state (`useState` for password fields, manual validation for match + min-length) to RHF + Zod.

**Files:**
- Create: `frontend/app/app/(auth)/invite/schema.ts`
- Modify: `frontend/app/app/(auth)/invite/page.tsx`
- Create: `frontend/app/tests/auth/invite-page-form.test.tsx`

- [ ] **Step 15.1: Create the schema**

  File: `frontend/app/app/(auth)/invite/schema.ts`
  ```ts
  import { z } from 'zod'

  export const inviteSchema = z
    .object({
      password: z
        .string()
        .min(8, 'Password must be at least 8 characters'),
      confirmPassword: z.string().min(1, 'Confirm your password'),
    })
    .refine((d) => d.password === d.confirmPassword, {
      path: ['confirmPassword'],
      message: 'Passwords do not match',
    })

  export type InviteFormValues = z.infer<typeof inviteSchema>
  ```

- [ ] **Step 15.2: Write failing test**

  File: `frontend/app/tests/auth/invite-page-form.test.tsx`
  ```tsx
  import { describe, expect, it, vi, beforeEach } from 'vitest'
  import { render, screen, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  import InvitePage from '@/app/(auth)/invite/page'

  const pushMock = vi.fn()
  const refreshMock = vi.fn()
  vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: pushMock, refresh: refreshMock }),
    useSearchParams: () => ({ get: (k: string) => (k === 'token' ? 'raw-token' : null) }),
  }))

  const acceptInviteMock = vi.fn()
  vi.mock('@/lib/api/auth', () => ({
    authApi: {
      acceptInvite: (body: unknown) => acceptInviteMock(body),
    },
  }))

  const apiFetchMock = vi.fn()
  vi.mock('@/lib/api/client', async () => {
    const actual = await vi.importActual<typeof import('@/lib/api/client')>('@/lib/api/client')
    return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) }
  })

  const setSessionMock = vi.fn(async () => ({ error: null }))
  vi.mock('@/lib/supabase/client', () => ({
    createClient: () => ({ auth: { setSession: setSessionMock } }),
  }))

  describe('InvitePage (B4)', () => {
    beforeEach(() => {
      pushMock.mockClear()
      refreshMock.mockClear()
      acceptInviteMock.mockReset()
      apiFetchMock.mockReset()
      apiFetchMock.mockResolvedValue({ email: 'user@example.com', client_name: 'Acme' })
      setSessionMock.mockClear()
    })

    it('shows a field-level error when passwords do not match', async () => {
      render(<InvitePage />)
      await screen.findByText(/acme/i)

      await userEvent.type(screen.getByLabelText(/^set password$/i), 'abcdefgh')
      await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'different')
      await userEvent.click(screen.getByRole('button', { name: /create account/i }))

      await waitFor(() => {
        expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument()
      })
      expect(acceptInviteMock).not.toHaveBeenCalled()
    })

    it('shows a field-level error when password is too short', async () => {
      render(<InvitePage />)
      await screen.findByText(/acme/i)

      await userEvent.type(screen.getByLabelText(/^set password$/i), 'short')
      await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'short')
      await userEvent.click(screen.getByRole('button', { name: /create account/i }))

      await waitFor(() => {
        expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument()
      })
      expect(acceptInviteMock).not.toHaveBeenCalled()
    })

    it('submits valid passwords to acceptInvite and installs session', async () => {
      acceptInviteMock.mockResolvedValue({
        access_token: 'a', refresh_token: 'r', expires_in: 3600, redirect_to: '/',
      })

      render(<InvitePage />)
      await screen.findByText(/acme/i)

      await userEvent.type(screen.getByLabelText(/^set password$/i), 'hunter2hunter2')
      await userEvent.type(screen.getByLabelText(/^confirm password$/i), 'hunter2hunter2')
      await userEvent.click(screen.getByRole('button', { name: /create account/i }))

      await waitFor(() => {
        expect(acceptInviteMock).toHaveBeenCalledWith({
          raw_token: 'raw-token',
          password: 'hunter2hunter2',
        })
      })
      expect(setSessionMock).toHaveBeenCalled()
      expect(pushMock).toHaveBeenCalledWith('/')
    })
  })
  ```

- [ ] **Step 15.3: Run test to verify failure**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/auth/invite-page-form.test.tsx
  ```
  Expected: FAIL — current page uses bespoke useState + manual password validation.

- [ ] **Step 15.4: Migrate the page**

  Replace the body of `frontend/app/app/(auth)/invite/page.tsx`'s `InviteContent` function with an RHF-driven form. Only the submission form changes; the "loading" / "invalid" branches are unchanged. Keep `useEffect` for the `verify-invite` GET (it's a one-shot, no QueryClient in (auth)). Integrate `applyApiErrorToForm` for backend 422s and fall back to `form.setError('root', ...)` for other errors. Show per-field messages under each input.

  Pattern (the rest of the file — `EyeIcon`/`EyeOffIcon`, interface `InviteDetails`, the "loading" and "invalid" branches, and the default export `InvitePage` — stays as-is). Replace the `InviteContent` component with the RHF version:
  ```tsx
  function InviteContent() {
    const searchParams = useSearchParams()
    const router = useRouter()
    const rawToken = searchParams.get('token') || ''

    const [state, setState] = useState<'loading' | 'invalid' | 'ready'>('loading')
    const [invite, setInvite] = useState<InviteDetails | null>(null)
    const [showPassword, setShowPassword] = useState(false)
    const [showConfirmPassword, setShowConfirmPassword] = useState(false)

    const form = useForm<InviteFormValues>({
      resolver: zodResolver(inviteSchema),
      defaultValues: { password: '', confirmPassword: '' },
      mode: 'onBlur',
    })

    useEffect(() => {
      if (!rawToken) {
        setState('invalid')
        return
      }
      apiFetch<InviteDetails>(
        `/api/auth/verify-invite?token=${encodeURIComponent(rawToken)}`,
      )
        .then((data) => {
          setInvite(data)
          setState('ready')
        })
        .catch(() => setState('invalid'))
    }, [rawToken])

    async function onSubmit(values: InviteFormValues) {
      try {
        const result = await authApi.acceptInvite({
          raw_token: rawToken,
          password: values.password,
        })

        const supabase = createClient()
        const { error: sessionError } = await supabase.auth.setSession({
          access_token: result.access_token,
          refresh_token: result.refresh_token,
        })
        if (sessionError) {
          form.setError('root', { message: sessionError.message })
          return
        }

        const safeRedirect =
          result.redirect_to?.startsWith('/') &&
          !result.redirect_to.startsWith('//')
            ? result.redirect_to
            : '/'
        router.push(safeRedirect)
        router.refresh()
      } catch (err) {
        if (applyApiErrorToForm(err, form, { fallbackFieldKey: 'password' })) return
        form.setError('root', {
          message: err instanceof Error ? err.message : 'Failed to create account',
        })
      }
    }

    // ... existing "loading" / "invalid" branches unchanged ...

    const rootError = form.formState.errors.root?.message

    return (
      <>
        {/* existing header JSX unchanged ... */}

        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4 rounded-[12px] border p-7"
          style={{ /* existing styles */ }}
        >
          {rootError && <p /* existing error chip styles */>{rootError}</p>}

          <div>
            <label className="px-label">Email</label>
            {/* existing locked-email display using invite!.email */}
          </div>

          <div>
            <label className="px-label" htmlFor="invite-password">Set password</label>
            <div className="relative">
              <input
                id="invite-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                className="px-input pr-10"
                placeholder="Enter a password"
                {...form.register('password')}
              />
              <button type="button" onClick={() => setShowPassword((v) => !v)} /* existing toggle */ />
            </div>
            {form.formState.errors.password && (
              <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                {form.formState.errors.password.message}
              </p>
            )}
            {!form.formState.errors.password && (
              <p className="px-hint">Minimum 8 characters</p>
            )}
          </div>

          <div>
            <label className="px-label" htmlFor="invite-confirm-password">Confirm password</label>
            <div className="relative">
              <input
                id="invite-confirm-password"
                type={showConfirmPassword ? 'text' : 'password'}
                autoComplete="new-password"
                className="px-input pr-10"
                {...form.register('confirmPassword')}
              />
              <button type="button" onClick={() => setShowConfirmPassword((v) => !v)} /* existing toggle */ />
            </div>
            {form.formState.errors.confirmPassword && (
              <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                {form.formState.errors.confirmPassword.message}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={form.formState.isSubmitting}
            className="px-btn primary lg w-full"
          >
            {form.formState.isSubmitting ? 'Creating account…' : 'Create account & continue →'}
          </button>
        </form>
      </>
    )
  }
  ```

  Add these imports at the top of the file (keeping the existing `Suspense`, `useEffect`, `useState`, `createClient`, `apiFetch`, `authApi`, `useSearchParams`, `useRouter` imports):
  ```tsx
  import { useForm } from 'react-hook-form'
  import { zodResolver } from '@hookform/resolvers/zod'

  import { applyApiErrorToForm } from '@/lib/api/errors'

  import { inviteSchema, type InviteFormValues } from './schema'
  ```

- [ ] **Step 15.5: Run tests + checks**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/auth/invite-page-form.test.tsx && npx tsc --noEmit && npm run lint
  ```
  Expected: 3/3 new tests pass. tsc and lint 0 errors.

- [ ] **Step 15.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(auth\)/invite/ frontend/app/tests/auth/invite-page-form.test.tsx
  git commit -m "refactor(invite): migrate form state to RHF+Zod with applyApiErrorToForm"
  ```

---

## Task 16: B4.3 — Migrate `app/onboarding/page.tsx` to `authApi` wrappers + `applyApiErrorToForm`

**Goal:** The onboarding page has two steps. Step 1 is button-click workspace-mode selection (no form fields — RHF would be ceremony). Step 2 delegates to the already-RHF-driven `CompanyProfileForm`. B4 migration is: route all API calls through the new `authApi` wrappers (`setWorkspaceMode`, `completeOnboarding`) and use the `orgUnitsApi.update` wrapper (already exists) for the company-profile save. Surface backend 422 errors via `applyApiErrorToForm` where a form is available; local error state remains for step-1 workspace-selection errors (no form to target). No QueryClient — the onboarding layout has none and none is introduced.

**Files:**
- Modify: `frontend/app/app/onboarding/page.tsx`

- [ ] **Step 16.1: Rewrite the page**

  Replace `frontend/app/app/onboarding/page.tsx` with the version below. It preserves the existing two-step UI but routes submissions through typed API wrappers:
  ```tsx
  'use client'

  import { useEffect, useState } from 'react'
  import { useRouter } from 'next/navigation'

  import { authApi } from '@/lib/api/auth'
  import { orgUnitsApi, type OrgUnit } from '@/lib/api/org-units'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'
  import {
    CompanyProfileForm,
    type CompanyProfile,
  } from '@/components/dashboard/company-profile-form'

  type Step = 'workspace' | 'company-profile'
  type WorkspaceMode = 'enterprise' | 'agency'

  export default function OnboardingPage() {
    const router = useRouter()
    const [step, setStep] = useState<Step>('workspace')

    const [selectedMode, setSelectedMode] = useState<WorkspaceMode | null>(null)
    const [workspaceLoading, setWorkspaceLoading] = useState(false)
    const [workspaceError, setWorkspaceError] = useState('')

    const [rootUnitId, setRootUnitId] = useState('')
    const [profileError, setProfileError] = useState('')
    const [fetchingOrg, setFetchingOrg] = useState(false)

    async function getToken(): Promise<string | null> {
      try {
        return await getFreshSupabaseToken()
      } catch {
        router.push('/login')
        return null
      }
    }

    async function handleSelectWorkspace(mode: WorkspaceMode) {
      setSelectedMode(mode)
      setWorkspaceError('')
      setWorkspaceLoading(true)

      try {
        const token = await getToken()
        if (!token) return
        await authApi.setWorkspaceMode(token, { workspace_mode: mode })
        setStep('company-profile')
      } catch (err) {
        setWorkspaceError(
          err instanceof Error ? err.message : 'Failed to set workspace type',
        )
        setSelectedMode(null)
      } finally {
        setWorkspaceLoading(false)
      }
    }

    useEffect(() => {
      if (step !== 'company-profile') return
      let cancelled = false

      ;(async () => {
        setFetchingOrg(true)
        try {
          const token = await getToken()
          if (!token) return
          const units: OrgUnit[] = await orgUnitsApi.list(token)
          const root = units.find((u) => u.is_root)
          if (root && !cancelled) setRootUnitId(root.id)
        } catch {
          // Non-fatal — profile form can still submit without pre-filling
        } finally {
          if (!cancelled) setFetchingOrg(false)
        }
      })()

      return () => {
        cancelled = true
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [step])

    async function handleSubmitProfile(value: CompanyProfile) {
      setProfileError('')
      const token = await getToken()
      if (!token) return

      if (rootUnitId) {
        await orgUnitsApi.update(token, rootUnitId, {
          set_company_profile: true,
          company_profile: value,
        })
      }

      await authApi.completeOnboarding(token)

      router.push('/')
      router.refresh()
    }

    const stepIndex = step === 'workspace' ? 0 : 1

    return (
      <div className="w-full max-w-2xl">
        {/* Step indicator — unchanged from the previous implementation.
            Copy the <nav aria-label="Onboarding progress"> block verbatim
            from the prior page.tsx (zinc-500 / blue-600 pill, connecting
            divider, step labels). */}
        {/* ... existing step indicator JSX ... */}

        {step === 'workspace' && (
          /* ... existing step-1 UI — two cards, workspaceLoading spinner,
             workspaceError block, onClick={() => handleSelectWorkspace('enterprise')}
             / onClick={() => handleSelectWorkspace('agency')} — unchanged ... */
        )}

        {step === 'company-profile' && (
          /* ... existing step-2 UI — fetchingOrg spinner and the
             <CompanyProfileForm onSubmit={...} /> wrapper with profileError
             chip — unchanged except the submit handler is now the version
             below, which routes through authApi.completeOnboarding + the
             orgUnitsApi.update wrapper (already imported). */
          <CompanyProfileForm
            onSubmit={async (value: CompanyProfile) => {
              try {
                await handleSubmitProfile(value)
              } catch (err) {
                setProfileError(
                  err instanceof Error ? err.message : 'Failed to save company profile',
                )
                throw err
              }
            }}
            submitLabel="Finish Onboarding"
          />
        )}
      </div>
    )
  }
  ```

  The JSX blocks referenced by the comments (step indicator, step-1 cards, step-2 wrapper including the `fetchingOrg` spinner and the `profileError` chip) must be copied verbatim from the prior `onboarding/page.tsx` implementation — no visual regressions. Only the API-call sites change.

- [ ] **Step 16.2: Type + lint + build**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint && npm run build
  ```
  Expected: 0 errors. `next build` clean.

- [ ] **Step 16.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/onboarding/page.tsx
  git commit -m "refactor(onboarding): route API calls through authApi + orgUnitsApi wrappers"
  ```

---

## Phase 7 — Team settings page (Cluster C8)

One substantial task. Subagent review cadence: **split** (medium-size page, 475 LOC, many mutation sites).

## Task 17: B4.4 — Migrate `settings/team/page.tsx` to TanStack Query + RHF

**Goal:** Replace local `useState` + manual `loadData()` refetch chain with `useTeamMembers` (query) + the four mutation hooks from Task 9. Replace the raw-`useState` invite form with RHF + `inviteTeamMemberSchema`. Existing `ConfirmDialog` stays — it's already a proper Dialog, not `window.confirm`. Route backend 422 on invite submission through `applyApiErrorToForm`.

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/team/schema.ts`
- Modify: `frontend/app/app/(dashboard)/settings/team/page.tsx`
- Create: `frontend/app/tests/settings/team-invite-form.test.tsx`

- [ ] **Step 17.1: Create the schema**

  File: `frontend/app/app/(dashboard)/settings/team/schema.ts`
  ```ts
  import { z } from 'zod'

  export const inviteTeamMemberSchema = z.object({
    email: z
      .string()
      .min(1, 'Email is required')
      .email('Enter a valid email address'),
  })

  export type InviteTeamMemberFormValues = z.infer<typeof inviteTeamMemberSchema>
  ```

- [ ] **Step 17.2: Write failing test**

  File: `frontend/app/tests/settings/team-invite-form.test.tsx`
  ```tsx
  import { describe, expect, it, vi, beforeEach } from 'vitest'
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { render, screen, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  import TeamPage from '@/app/(dashboard)/settings/team/page'
  import { ApiValidationError } from '@/lib/api/client'

  vi.mock('@/lib/auth/tokens', () => ({
    getFreshSupabaseToken: async () => 'stub-token',
  }))

  const listMock = vi.fn()
  const inviteMock = vi.fn()
  vi.mock('@/lib/api/team', () => ({
    teamApi: {
      list: () => listMock(),
      invite: (_t: string, body: unknown) => inviteMock(body),
      resend: async () => undefined,
      revoke: async () => undefined,
      deactivate: async () => undefined,
    },
  }))

  const meMock = vi.fn()
  vi.mock('@/lib/api/auth', () => ({
    authApi: { me: () => meMock() },
  }))

  function wrap(node: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
  }

  describe('TeamPage invite form', () => {
    beforeEach(() => {
      listMock.mockResolvedValue([])
      meMock.mockResolvedValue({
        user_id: 'u1', email: 'admin@x.com', full_name: 'A', tenant_id: 't1',
        client_name: 'Acme', is_super_admin: true, onboarding_complete: true,
        has_org_units: true, workspace_mode: 'enterprise', assignments: [],
      })
      inviteMock.mockReset()
    })

    it('shows a schema validation error on bad email', async () => {
      wrap(<TeamPage />)
      await screen.findByRole('heading', { name: /team & access/i })

      await userEvent.type(screen.getByLabelText(/email/i), 'not-an-email')
      await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

      await waitFor(() => {
        expect(screen.getByText(/valid email/i)).toBeInTheDocument()
      })
      expect(inviteMock).not.toHaveBeenCalled()
    })

    it('maps backend 422 field errors into the form', async () => {
      inviteMock.mockRejectedValue(
        new ApiValidationError('email taken', [
          { loc: ['body', 'email'], msg: 'email already taken', type: 'x' },
        ]),
      )

      wrap(<TeamPage />)
      await screen.findByRole('heading', { name: /team & access/i })

      await userEvent.type(screen.getByLabelText(/email/i), 'taken@x.com')
      await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

      await waitFor(() => {
        expect(screen.getByText(/email already taken/i)).toBeInTheDocument()
      })
    })

    it('submits a valid email and resets the form', async () => {
      inviteMock.mockResolvedValue({ invite_url: 'https://app/invite?token=abc' })

      wrap(<TeamPage />)
      await screen.findByRole('heading', { name: /team & access/i })

      await userEvent.type(screen.getByLabelText(/email/i), 'new@x.com')
      await userEvent.click(screen.getByRole('button', { name: /send invite/i }))

      await waitFor(() => {
        expect(inviteMock).toHaveBeenCalledWith({ email: 'new@x.com' })
      })
    })
  })
  ```

- [ ] **Step 17.3: Run test to verify failure**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/team-invite-form.test.tsx
  ```
  Expected: FAIL — current page uses raw `useState` + `useEffect` + `fetch`.

- [ ] **Step 17.4: Migrate the page**

  Replace `frontend/app/app/(dashboard)/settings/team/page.tsx`. Keep the existing `IconUsers`, `IconMail`, `SkeletonRow`, `TableSkeleton`, and `ConfirmDialog` components (no changes). Replace the `TeamPage` body:
  ```tsx
  'use client'

  import { useState } from 'react'
  import { useForm } from 'react-hook-form'
  import { zodResolver } from '@hookform/resolvers/zod'
  import { toast } from 'sonner'

  import { authApi, type MeResponse } from '@/lib/api/auth'
  import { useTeamMembers } from '@/lib/hooks/use-team-members'
  import { useInviteTeamMember } from '@/lib/hooks/use-invite-team-member'
  import { useResendTeamInvite } from '@/lib/hooks/use-resend-team-invite'
  import { useRevokeTeamInvite } from '@/lib/hooks/use-revoke-team-invite'
  import { useDeactivateUser } from '@/lib/hooks/use-deactivate-user'
  import { useQuery } from '@tanstack/react-query'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'
  import { applyApiErrorToForm } from '@/lib/api/errors'

  import {
    inviteTeamMemberSchema,
    type InviteTeamMemberFormValues,
  } from './schema'

  // (keep existing icon + skeleton + ConfirmDialog component code above)

  export default function TeamPage() {
    const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)

    const membersQuery = useTeamMembers()
    const meQuery = useQuery<MeResponse>({
      queryKey: ['me'],
      queryFn: async () => authApi.me(await getFreshSupabaseToken()),
      staleTime: 60_000,
    })

    const inviteMutation = useInviteTeamMember()
    const resendMutation = useResendTeamInvite()
    const revokeMutation = useRevokeTeamInvite()
    const deactivateMutation = useDeactivateUser()

    const form = useForm<InviteTeamMemberFormValues>({
      resolver: zodResolver(inviteTeamMemberSchema),
      defaultValues: { email: '' },
    })

    async function onInvite(values: InviteTeamMemberFormValues) {
      try {
        const result = await inviteMutation.mutateAsync({ email: values.email })
        form.reset()
        toast.success(
          result.invite_url ? `Invite sent! URL: ${result.invite_url}` : 'Invite sent!',
        )
      } catch (err) {
        if (applyApiErrorToForm(err, form, { fallbackFieldKey: 'email' })) return
        toast.error(err instanceof Error ? err.message : 'Failed to send invite')
      }
    }

    const me = meQuery.data ?? null
    const isSuperAdmin = me?.is_super_admin ?? false
    const members = membersQuery.data ?? []
    const users = members.filter((m) => m.source === 'user')
    const invites = members.filter((m) => m.source === 'invite')
    const loading = membersQuery.isLoading || meQuery.isLoading

    const statusColor: Record<string, string> = {
      active: 'bg-green-50 text-green-700',
      inactive: 'bg-zinc-100 text-zinc-500',
      pending: 'bg-amber-50 text-amber-700',
    }

    return (
      <>
        {confirmAction && (
          <ConfirmDialog
            action={confirmAction}
            onClose={() => setConfirmAction(null)}
          />
        )}
        <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-5">
          <h1
            className="px-serif m-0 mb-6 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Team & access
          </h1>

          {isSuperAdmin && (
            <form
              onSubmit={form.handleSubmit(onInvite)}
              className="mb-6 rounded-[10px] border p-5"
              style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
            >
              <h2
                className="mb-3 text-[11px] font-semibold uppercase"
                style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
              >
                Invite team member
              </h2>
              <div className="flex items-end gap-3">
                <div className="flex-1">
                  <label className="px-label" htmlFor="team-invite-email">Email</label>
                  <input
                    id="team-invite-email"
                    type="email"
                    className="px-input"
                    placeholder="colleague@company.com"
                    {...form.register('email')}
                  />
                  {form.formState.errors.email && (
                    <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
                      {form.formState.errors.email.message}
                    </p>
                  )}
                </div>
                <button
                  type="submit"
                  disabled={form.formState.isSubmitting}
                  className="px-btn primary sm"
                >
                  {form.formState.isSubmitting ? 'Sending…' : 'Send invite'}
                </button>
              </div>
              <p className="px-hint">
                Roles and org unit assignments can be configured after the user joins.
              </p>
            </form>
          )}

          {loading ? (
            <>
              <div className="h-4 w-28 bg-zinc-100 rounded animate-pulse mb-3" />
              <TableSkeleton cols={isSuperAdmin ? 5 : 4} rows={3} />
              <div className="h-4 w-36 bg-zinc-100 rounded animate-pulse mb-3 mt-6" />
              <TableSkeleton cols={isSuperAdmin ? 3 : 2} rows={2} />
            </>
          ) : (
            <>
              {/* Members table — unchanged JSX from the previous
                  implementation, except replace onClick handlers:
                  deactivate: deactivateMutation.mutateAsync(m.id)
                  (wrap in setConfirmAction as before). */}

              {/* Pending invites table — unchanged JSX except:
                  resend: resendMutation.mutateAsync(m.id)
                  revoke: revokeMutation.mutateAsync(m.id) */}
            </>
          )}
        </div>
      </>
    )
  }
  ```

  Copy the members and pending-invites tables verbatim from the prior page.tsx. The only change inside those tables is the action handlers: `handleResend(m.id)` becomes `resendMutation.mutateAsync(m.id)`, `handleRevoke(m.id)` becomes `revokeMutation.mutateAsync(m.id)`, `handleDeactivate(m.id)` becomes `deactivateMutation.mutateAsync(m.id)` (all still wrapped in `setConfirmAction` for the destructive ones). Wrap each `.mutateAsync(...)` in a try/catch that surfaces errors via `toast.error(err.message)`; `ApiError` narrowing is fine since the mutation already fired.

  Also keep the `ConfirmAction` interface and `ConfirmDialog` component exactly as they are.

- [ ] **Step 17.5: Run tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/team-invite-form.test.tsx && npx tsc --noEmit && npm run lint && npm run build
  ```
  Expected: 3/3 tests pass. tsc, lint, build clean.

- [ ] **Step 17.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/team/ frontend/app/tests/settings/team-invite-form.test.tsx
  git commit -m "refactor(settings/team): migrate to TanStack Query + RHF+Zod"
  ```

---

## Phase 8 — Org-units index page (Cluster C9)

**Split** subagent review. Largest single page in B4 (721 LOC).

## Task 18: B4.5 — Migrate `settings/org-units/page.tsx` to TanStack Query + RHF

**Goal:** Replace the local `loadData()` Promise.all + useState chain with `useOrgUnits`, `useJobsList` (existing in `lib/hooks/`), and `useQuery<MeResponse>` for `/me`. Migrate the "create unit" form to RHF + `createOrgUnitSchema`. The client-account flow that pops the `CompanyProfileDialog` is unchanged structurally — only the submission calls `useCreateOrgUnit().mutateAsync(...)` instead of `orgUnitsApi.create` directly.

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/org-units/schema.ts`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/page.tsx`
- Create: `frontend/app/tests/settings/create-org-unit-form.test.tsx`

- [ ] **Step 18.1: Create the schema**

  File: `frontend/app/app/(dashboard)/settings/org-units/schema.ts`
  ```ts
  import { z } from 'zod'

  export const createOrgUnitSchema = z.object({
    name: z
      .string()
      .min(1, 'Unit name is required')
      .max(100, 'Keep names under 100 characters'),
    unit_type: z.enum(['division', 'client_account', 'region', 'team']),
    parent_unit_id: z.string().optional().default(''),
  })

  export type CreateOrgUnitFormValues = z.infer<typeof createOrgUnitSchema>
  ```

- [ ] **Step 18.2: Write failing test**

  File: `frontend/app/tests/settings/create-org-unit-form.test.tsx`
  ```tsx
  import { describe, expect, it, vi, beforeEach } from 'vitest'
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { render, screen, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  import OrgUnitsPage from '@/app/(dashboard)/settings/org-units/page'

  vi.mock('@/lib/auth/tokens', () => ({
    getFreshSupabaseToken: async () => 'stub-token',
  }))

  const listUnitsMock = vi.fn()
  const createMock = vi.fn()
  vi.mock('@/lib/api/org-units', async () => {
    const actual = await vi.importActual<typeof import('@/lib/api/org-units')>(
      '@/lib/api/org-units',
    )
    return {
      ...actual,
      orgUnitsApi: {
        ...actual.orgUnitsApi,
        list: () => listUnitsMock(),
        create: (_t: string, body: unknown) => createMock(body),
      },
    }
  })
  vi.mock('@/lib/api/jobs', () => ({
    jobsApi: { list: async () => [] },
  }))
  vi.mock('@/lib/api/auth', () => ({
    authApi: {
      me: async () => ({
        user_id: 'u', email: 'a@x.com', full_name: null, tenant_id: 't',
        client_name: 'Acme', is_super_admin: true, onboarding_complete: true,
        has_org_units: true, workspace_mode: 'enterprise', assignments: [],
      }),
    },
  }))

  function wrap(node: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
  }

  describe('OrgUnitsPage create form', () => {
    beforeEach(() => {
      listUnitsMock.mockResolvedValue([
        {
          id: 'root-1', client_id: 't', parent_unit_id: null, name: 'Acme',
          unit_type: 'company', member_count: 0, created_at: '2026-01-01T00:00:00Z',
          created_by: null, created_by_email: null, deletable_by: null,
          deletable_by_email: null, admin_delete_disabled: false,
          is_accessible: true, admin_emails: [], is_root: true,
          company_profile: null, company_profile_completed_at: null, metadata: null,
        },
      ])
      createMock.mockReset()
    })

    it('blocks submit when name is empty', async () => {
      wrap(<OrgUnitsPage />)
      await screen.findByText(/org structure/i)

      await userEvent.click(screen.getByRole('button', { name: /new unit/i }))
      await userEvent.click(screen.getByRole('button', { name: /^create unit$/i }))

      await waitFor(() => {
        expect(screen.getByText(/unit name is required/i)).toBeInTheDocument()
      })
      expect(createMock).not.toHaveBeenCalled()
    })

    it('submits a division with a valid name', async () => {
      createMock.mockResolvedValue({
        id: 'new-1', client_id: 't', parent_unit_id: null, name: 'Eng',
        unit_type: 'division', member_count: 0, created_at: '2026-01-01T00:00:00Z',
        created_by: null, created_by_email: null, deletable_by: null,
        deletable_by_email: null, admin_delete_disabled: false,
        is_accessible: true, admin_emails: [], is_root: false,
        company_profile: null, company_profile_completed_at: null, metadata: null,
      })

      wrap(<OrgUnitsPage />)
      await screen.findByText(/org structure/i)

      await userEvent.click(screen.getByRole('button', { name: /new unit/i }))
      await userEvent.type(screen.getByLabelText(/name/i), 'Eng')
      await userEvent.click(screen.getByRole('button', { name: /^create unit$/i }))

      await waitFor(() => {
        expect(createMock).toHaveBeenCalledWith({
          name: 'Eng',
          unit_type: 'division',
          parent_unit_id: null,
          company_profile: null,
        })
      })
    })
  })
  ```

- [ ] **Step 18.3: Run test to verify failure**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/create-org-unit-form.test.tsx
  ```
  Expected: FAIL.

- [ ] **Step 18.4: Migrate the page**

  The page is 721 LOC and has two detail subsections (`SelectedDetail`, `UnitMetrics`, `UnitAccess`) that do not touch server state — leave them unchanged. The changes concentrate in the top-level `OrgUnitsPage` function: replace the `load` effect + local state with queries; replace the create-form `useState` with RHF. Pseudo-patch:

  1. Remove the four state vars: `units`, `jobs`, `me`, `loading`, `error`. Replace with:
     ```tsx
     const unitsQuery = useOrgUnits()
     const meQuery = useQuery<MeResponse>({
       queryKey: ['me'],
       queryFn: async () => authApi.me(await getFreshSupabaseToken()),
       staleTime: 60_000,
     })
     const jobsQuery = useQuery<JobPostingSummary[]>({
       queryKey: ['jobs-list'],
       queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
       staleTime: 10_000,
     })
     const units = unitsQuery.data ?? []
     const jobs = jobsQuery.data ?? []
     const me = meQuery.data ?? null
     const loading = unitsQuery.isLoading || meQuery.isLoading
     const [error, setError] = useState('')
     ```

  2. Remove `load()` + its `useEffect`. The queries fire automatically.

  3. Replace the create-form `useState` (`createName`, `createType`, `createParent`, `creating`) with RHF:
     ```tsx
     const createForm = useForm<CreateOrgUnitFormValues>({
       resolver: zodResolver(createOrgUnitSchema),
       defaultValues: { name: '', unit_type: 'division', parent_unit_id: '' },
     })
     const createMutation = useCreateOrgUnit()
     ```

  4. Replace `doCreate(companyProfile)` and `handleCreateSubmit` with:
     ```tsx
     async function doCreate(companyProfile: CompanyProfile | null) {
       const values = createForm.getValues()
       try {
         const newUnit = await createMutation.mutateAsync({
           name: values.name.trim(),
           unit_type: values.unit_type,
           parent_unit_id: values.parent_unit_id || null,
           company_profile: companyProfile,
         })
         createForm.reset({ name: '', unit_type: 'division', parent_unit_id: '' })
         setShowCreate(false)
         setShowProfileDialog(false)
         router.push(`/settings/org-units/${newUnit.id}`)
       } catch (err) {
         if (applyApiErrorToForm(err, createForm)) throw err
         setError(err instanceof Error ? err.message : 'Failed to create unit')
         throw err
       }
     }

     const onCreateSubmit = createForm.handleSubmit(async () => {
       if (createForm.getValues('unit_type') === 'client_account') {
         setError('')
         setShowProfileDialog(true)
         return
       }
       try {
         await doCreate(null)
       } catch {
         // error already surfaced via setError or form.setError
       }
     })
     ```

  5. Replace `createType`, `createParent`, `createName` usages in the JSX with `form.register(...)` on each input:
     ```tsx
     <input id="create-name" type="text" className="px-input"
            placeholder="e.g., Engineering" {...createForm.register('name')} />
     {createForm.formState.errors.name && (
       <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
         {createForm.formState.errors.name.message}
       </p>
     )}
     ```
     Type select:
     ```tsx
     <select id="create-type" className="px-input" {...createForm.register('unit_type')}>
       {createableTypes.map((t) => (
         <option key={t.value} value={t.value}>{t.label}</option>
       ))}
     </select>
     ```
     Parent select uses `register('parent_unit_id')`.

     Swap `<form onSubmit={handleCreateSubmit}>` → `<form onSubmit={onCreateSubmit}>`.

     Submit button disabled logic:
     ```tsx
     disabled={createMutation.isPending || !createForm.watch('name').trim()}
     ```

  6. The `CompanyProfileDialog` trigger and `handleClientAccountProfileSubmit` call `doCreate(profile)` — unchanged call shape.

  Import additions at the top:
  ```tsx
  import { useForm } from 'react-hook-form'
  import { zodResolver } from '@hookform/resolvers/zod'
  import { useQuery } from '@tanstack/react-query'

  import { useOrgUnits } from '@/lib/hooks/use-org-units'
  import { useCreateOrgUnit } from '@/lib/hooks/use-create-org-unit'
  import { applyApiErrorToForm } from '@/lib/api/errors'

  import {
    createOrgUnitSchema,
    type CreateOrgUnitFormValues,
  } from './schema'
  ```

  The `openRolesByUnit`, `rolledOpenRoles`, `graphNodes`, `selectOptions`, `createableTypes`, and the `SelectedDetail` / `UnitMetrics` / `UnitAccess` components stay exactly as they are.

- [ ] **Step 18.5: Run tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/create-org-unit-form.test.tsx && npx tsc --noEmit && npm run lint && npm run build
  ```
  Expected: 2/2 tests pass. tsc, lint, build clean.

- [ ] **Step 18.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/org-units/page.tsx \
          frontend/app/app/\(dashboard\)/settings/org-units/schema.ts \
          frontend/app/tests/settings/create-org-unit-form.test.tsx
  git commit -m "refactor(settings/org-units): migrate index page to TanStack Query + RHF"
  ```

---

## Phase 9 — Org-units detail tree (Cluster C10)

Per §8.5.D4.5: six files, not one. Split subagent review per task for the larger subcomponents; combined for small ones. The tree:

```
[unitId]/page.tsx           (Task 19 — router shell)
├── shared.tsx              (unchanged — pure helpers)
├── CompanyProfileDetail    (Task 20 — split review)
├── DivisionDetail          (Task 21 — combined review)
├── RegionDetail            (Task 21 — combined review)
├── TeamDetail              (Task 21 — combined review)
└── MembersSection          (Task 22 — split review, folds in confirm → Dialog)
```

Task 21 bundles the three "detail" subcomponents because they share the same migration pattern. Task 22 is split because the Dialog conversion is a security/UX-facing change.

## Task 19: Migrate `[unitId]/page.tsx` router shell

**Goal:** Replace local `useState` + `useEffect` + `Promise.all` with `useOrgUnit`, `useOrgUnits`, `useQuery<JobPostingSummary[]>` for jobs. Keep the ancestry-walk + open-roles rollup logic. Pass data to the `*Detail` subcomponents via props; `handleSaved` becomes a no-op wrapper that just fires `toast.success` — cache invalidation is handled by the subcomponent's mutation hook.

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts` (empty placeholder for shared detail-form schemas added in Tasks 20–22)
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`

- [ ] **Step 19.1: Create a placeholder schema file**

  File: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts`
  ```ts
  /**
   * Shared Zod schemas for the org-unit detail page's subcomponent forms.
   * Each subcomponent (CompanyProfileDetail, DivisionDetail, RegionDetail,
   * TeamDetail, MembersSection) owns its own schema below.
   */
  import { z } from 'zod'

  export const unitNameSchema = z.object({
    name: z
      .string()
      .min(1, 'Name is required')
      .max(100, 'Keep names under 100 characters'),
  })
  export type UnitNameFormValues = z.infer<typeof unitNameSchema>
  ```

- [ ] **Step 19.2: Rewrite `[unitId]/page.tsx`**

  Replace `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`:
  ```tsx
  'use client'

  import { useCallback, useMemo } from 'react'
  import { useParams, useRouter } from 'next/navigation'
  import { useQuery, useQueryClient } from '@tanstack/react-query'
  import { toast } from 'sonner'

  import { getFreshSupabaseToken } from '@/lib/auth/tokens'
  import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
  import { useOrgUnit } from '@/lib/hooks/use-org-unit'
  import { useOrgUnits } from '@/lib/hooks/use-org-units'
  import type { OrgUnit } from '@/lib/api/org-units'

  import { CompanyProfileDetail } from './CompanyProfileDetail'
  import { DivisionDetail } from './DivisionDetail'
  import { RegionDetail } from './RegionDetail'
  import { TeamDetail } from './TeamDetail'

  export default function OrgUnitDetailPage() {
    const params = useParams<{ unitId: string }>()
    const router = useRouter()
    const qc = useQueryClient()
    const unitId = params.unitId

    const unitQuery = useOrgUnit(unitId)
    const allUnitsQuery = useOrgUnits()
    const jobsQuery = useQuery<JobPostingSummary[]>({
      queryKey: ['jobs-list'],
      queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
      staleTime: 10_000,
    })

    const unit = unitQuery.data ?? null
    const allUnits = allUnitsQuery.data ?? []
    const jobs = jobsQuery.data ?? []
    const loading = unitQuery.isLoading || allUnitsQuery.isLoading
    const error = unitQuery.error?.message || allUnitsQuery.error?.message || ''

    const parentPath = useMemo(() => {
      if (!unit) return ''
      const byId = new Map(allUnits.map((u) => [u.id, u]))
      const chain: string[] = []
      let cur = unit.parent_unit_id ? byId.get(unit.parent_unit_id) : null
      while (cur) {
        chain.unshift(cur.name)
        cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : null
      }
      return chain.join(' · ')
    }, [unit, allUnits])

    const subUnits = useMemo(() => {
      if (!unit) return []
      return allUnits.filter((u) => u.parent_unit_id === unit.id)
    }, [unit, allUnits])

    const { openRolesCount, openRolesByChildId } = useMemo(() => {
      const raw: Record<string, number> = {}
      for (const j of jobs) {
        if (j.status === 'draft') continue
        raw[j.org_unit_id] = (raw[j.org_unit_id] ?? 0) + 1
      }
      const childrenOf: Record<string, string[]> = {}
      for (const u of allUnits) {
        if (u.parent_unit_id) (childrenOf[u.parent_unit_id] ||= []).push(u.id)
      }
      const rolled = (id: string): number => {
        let total = raw[id] ?? 0
        for (const cid of childrenOf[id] ?? []) total += rolled(cid)
        return total
      }
      const byChild: Record<string, number> = {}
      if (unit) {
        for (const c of childrenOf[unit.id] ?? []) byChild[c] = rolled(c)
      }
      return {
        openRolesCount: unit ? rolled(unit.id) : 0,
        openRolesByChildId: byChild,
      }
    }, [jobs, allUnits, unit])

    /**
     * Subcomponent mutations drive cache invalidation through their hooks;
     * this callback is purely for the success toast and the local
     * `allUnits` list to reflect the updated row without a flicker. The
     * hook's invalidate triggers a refetch anyway — the optimistic update
     * below is a UX polish, not a correctness requirement.
     */
    const handleSaved = useCallback(
      (updated: OrgUnit) => {
        qc.setQueryData<OrgUnit[]>(['org-units'], (prev) =>
          prev ? prev.map((u) => (u.id === updated.id ? updated : u)) : prev,
        )
        qc.setQueryData<OrgUnit>(['org-units', updated.id], updated)
      },
      [qc],
    )

    const onBack = () => router.push('/settings/org-units')

    if (loading) {
      return (
        <div className="mx-auto max-w-[1200px] px-8 pt-6 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Loading unit…
        </div>
      )
    }

    if (error || !unit) {
      return (
        <div className="mx-auto max-w-[1200px] px-8 pt-6">
          <div
            className="rounded-md border p-4 text-sm"
            style={{
              color: 'var(--px-danger)',
              background: 'var(--px-danger-bg)',
              borderColor: 'var(--px-danger-line)',
            }}
          >
            {error || 'Unit not found'}
          </div>
        </div>
      )
    }

    if (unit.unit_type === 'company' || unit.unit_type === 'client_account') {
      return (
        <div className="mx-auto max-w-[1200px]">
          <CompanyProfileDetail
            unit={unit}
            subUnits={subUnits}
            onBack={onBack}
            onSaved={(u) => {
              handleSaved(u)
              toast.success('Changes saved')
            }}
            openRolesCount={openRolesCount}
          />
        </div>
      )
    }
    if (unit.unit_type === 'region') {
      return (
        <div className="mx-auto max-w-[1200px]">
          <RegionDetail
            unit={unit}
            parentPath={parentPath}
            subUnits={subUnits}
            onBack={onBack}
            onSaved={handleSaved}
            openRolesCount={openRolesCount}
          />
        </div>
      )
    }
    if (unit.unit_type === 'division') {
      return (
        <div className="mx-auto max-w-[1200px]">
          <DivisionDetail
            unit={unit}
            parentPath={parentPath}
            subUnits={subUnits}
            onBack={onBack}
            onSaved={handleSaved}
            openRolesCount={openRolesCount}
            openRolesByChildId={openRolesByChildId}
          />
        </div>
      )
    }
    return (
      <div className="mx-auto max-w-[1200px]">
        <TeamDetail
          unit={unit}
          parentPath={parentPath}
          onBack={onBack}
          onSaved={handleSaved}
          openRolesCount={openRolesCount}
        />
      </div>
    )
  }
  ```

- [ ] **Step 19.3: Type + lint**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint
  ```
  Expected: 0 errors (may flag unused imports inside the subcomponents if they still use the pre-B4 patterns — that's fine, Tasks 20–22 will clean them up).

- [ ] **Step 19.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/page.tsx \
          frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/schema.ts
  git commit -m "refactor(org-units/detail): migrate router shell to TanStack Query"
  ```

---

## Task 20: Migrate `CompanyProfileDetail.tsx`

**Goal:** Replace the subcomponent's local `useState` save path with `useUpdateOrgUnit` from Task 12. Any local name edit form migrates to RHF + `unitNameSchema`. CompanyProfileForm is already RHF+Zod internally (no change). Errors from the update mutation surface via `applyApiErrorToForm`.

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail.tsx`

- [ ] **Step 20.1: Read the current implementation to understand the form structure**

  ```bash
  wc -l /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/CompanyProfileDetail.tsx
  grep -n 'useState\|useEffect\|fetch\|apiFetch\|orgUnitsApi' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/CompanyProfileDetail.tsx
  ```
  Expected: identifies the raw useState slots for the name-edit input, any `saving` spinner flag, and the direct `orgUnitsApi.update` call that needs to move to `useUpdateOrgUnit().mutateAsync`.

- [ ] **Step 20.2: Apply the migration**

  Pattern: for each form-state useState (`nameDraft`, `savingName`, etc.), swap to RHF with `unitNameSchema`. For each direct API call (`await orgUnitsApi.update(token, unit.id, {...})`), swap to:
  ```tsx
  const updateMutation = useUpdateOrgUnit()
  // ...
  try {
    const updated = await updateMutation.mutateAsync({ unitId: unit.id, body: {...} })
    onSaved(updated)
  } catch (err) {
    if (applyApiErrorToForm(err, form)) return
    toast.error(err instanceof Error ? err.message : 'Failed to save')
  }
  ```

  The CompanyProfileForm submission already exists — inside its `onSubmit` prop, call the mutation. Existing `onSaved(updated)` prop contract is unchanged.

  Add imports:
  ```tsx
  import { useForm } from 'react-hook-form'
  import { zodResolver } from '@hookform/resolvers/zod'
  import { toast } from 'sonner'

  import { useUpdateOrgUnit } from '@/lib/hooks/use-update-org-unit'
  import { applyApiErrorToForm } from '@/lib/api/errors'
  import { unitNameSchema, type UnitNameFormValues } from './schema'
  ```

- [ ] **Step 20.3: Type + lint + build**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint
  ```
  Expected: 0 errors.

- [ ] **Step 20.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/CompanyProfileDetail.tsx
  git commit -m "refactor(org-units/detail): migrate CompanyProfileDetail to useUpdateOrgUnit + RHF"
  ```

---

## Task 21: Migrate `DivisionDetail.tsx`, `RegionDetail.tsx`, `TeamDetail.tsx`

**Goal:** Three subcomponents, same migration pattern as Task 20. One task, one commit (parallel mechanical).

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/DivisionDetail.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/RegionDetail.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/TeamDetail.tsx`

- [ ] **Step 21.1: Apply the same pattern from Task 20 to each file**

  For each of the three `*Detail.tsx` files:
  1. Swap local `useState` for name / metadata-edit drafts to RHF with the appropriate schema (reuse `unitNameSchema` from `./schema.ts` or inline a small Zod schema if the subcomponent needs more fields).
  2. Replace direct `orgUnitsApi.update` calls with `useUpdateOrgUnit().mutateAsync({unitId: unit.id, body: {...}})`.
  3. Surface backend errors through `applyApiErrorToForm(err, form)` + `toast.error` fallback.
  4. Preserve `onSaved(updated)` prop behavior — the hook's `onSuccess` invalidates cache; `onSaved` is just the page-level toast-or-setQueryData hook.

- [ ] **Step 21.2: Type + lint**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint
  ```
  Expected: 0 errors.

- [ ] **Step 21.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/DivisionDetail.tsx \
          frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/RegionDetail.tsx \
          frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/TeamDetail.tsx
  git commit -m "refactor(org-units/detail): migrate Division/Region/Team subcomponents to hooks+RHF"
  ```

---

## Task 22: Migrate `MembersSection.tsx` + fold in `confirm()` → `Dialog`

**Goal:** Replace `useState`-driven member list with `useOrgUnitMembers(unitId)`. Replace the assign-role dropdown-form with RHF + a small schema. Replace the bare `if (!confirm(\`Remove ${roleName}...\`))` on line 102 with the project's `Dialog` primitive (same pattern as `ConfirmDialog` in settings/team/page.tsx — copy its shape, or use `components/px/Dialog` which is already used elsewhere in the app). The `useAssignRole` and `useRemoveRole` mutations from Task 13 drive the actual API calls.

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx`
- Create: `frontend/app/tests/settings/members-section-dialog.test.tsx`

- [ ] **Step 22.1: Write failing test**

  File: `frontend/app/tests/settings/members-section-dialog.test.tsx`
  ```tsx
  import { describe, expect, it, vi } from 'vitest'
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { render, screen, waitFor } from '@testing-library/react'
  import userEvent from '@testing-library/user-event'

  import { MembersSection } from '@/app/(dashboard)/settings/org-units/[unitId]/MembersSection'

  vi.mock('@/lib/auth/tokens', () => ({
    getFreshSupabaseToken: async () => 'stub-token',
  }))

  const listMembersMock = vi.fn(async () => [
    {
      user_id: 'u1', email: 'member@x.com', full_name: 'Member',
      roles: [{ role_id: 'r1', role_name: 'Recruiter', assigned_at: '2026-01-01' }],
    },
  ])
  const listRolesMock = vi.fn(async () => [
    { id: 'r1', name: 'Recruiter', description: '', permissions: [], is_system: true },
  ])
  const removeRoleMock = vi.fn(async () => ({ status: 'ok' }))

  vi.mock('@/lib/api/org-units', async () => {
    const actual = await vi.importActual<typeof import('@/lib/api/org-units')>(
      '@/lib/api/org-units',
    )
    return {
      ...actual,
      orgUnitsApi: {
        ...actual.orgUnitsApi,
        listMembers: () => listMembersMock(),
        listRoles: () => listRolesMock(),
        removeRole: (_t: string, _u: string, _uid: string, _rid: string) => removeRoleMock(),
      },
    }
  })

  function wrap(node: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
  }

  describe('MembersSection (B4)', () => {
    it('shows a Dialog (not window.confirm) before removing a role', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
      wrap(<MembersSection unitId="u1" />)

      await screen.findByText(/member@x.com/i)
      await userEvent.click(screen.getByRole('button', { name: /remove/i }))

      expect(confirmSpy).not.toHaveBeenCalled()
      await screen.findByRole('dialog')
      confirmSpy.mockRestore()
    })

    it('removes the role after confirming the Dialog', async () => {
      wrap(<MembersSection unitId="u1" />)

      await screen.findByText(/member@x.com/i)
      await userEvent.click(screen.getByRole('button', { name: /remove/i }))
      await userEvent.click(
        await screen.findByRole('button', { name: /^confirm$|^remove role$/i }),
      )

      await waitFor(() => {
        expect(removeRoleMock).toHaveBeenCalled()
      })
    })
  })
  ```

  `MembersSection` is currently a default export or named export — check the current file (`grep -n '^export' MembersSection.tsx`) and adjust the import in the test accordingly. If `MembersSection` is imported by `DivisionDetail` / `TeamDetail` as a named child, keep that shape.

- [ ] **Step 22.2: Run test to verify failure**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/members-section-dialog.test.tsx
  ```
  Expected: FAIL — current code uses `confirm()`; no `<dialog>` role appears.

- [ ] **Step 22.3: Migrate the component**

  Replace `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx`:

  1. Replace internal `useState` + `useEffect(loadMembers)` with `useOrgUnitMembers(unitId)` from Task 11.
  2. Replace roles `useEffect(loadRoles)` with `useRoles()` from Task 11.
  3. Replace the bare `confirm(\`Remove ${roleName}...\`)` with a state-driven `Dialog`:
     ```tsx
     const [toRemove, setToRemove] = useState<{ userId: string; roleId: string; roleName: string } | null>(null)
     const removeRoleMutation = useRemoveRole()

     async function handleConfirmRemove() {
       if (!toRemove) return
       try {
         await removeRoleMutation.mutateAsync({
           unitId,
           userId: toRemove.userId,
           roleId: toRemove.roleId,
         })
       } catch (err) {
         toast.error(err instanceof Error ? err.message : 'Failed to remove role')
       } finally {
         setToRemove(null)
       }
     }

     return (
       <>
         {/* ... existing members table JSX, but the "Remove" button:  */}
         <button
           type="button"
           onClick={() => setToRemove({ userId: m.user_id, roleId: role.role_id, roleName: role.role_name })}
         >
           Remove
         </button>

         <Dialog open={!!toRemove} onOpenChange={(open) => { if (!open) setToRemove(null) }}>
           <DialogContent>
             <DialogHeader>
               <DialogTitle>Remove role</DialogTitle>
               <DialogDescription>
                 Remove <strong>{toRemove?.roleName}</strong> from this user on this unit?
               </DialogDescription>
             </DialogHeader>
             <div className="mt-4 flex justify-end gap-2">
               <button
                 type="button"
                 onClick={() => setToRemove(null)}
                 className="px-btn ghost sm"
               >
                 Cancel
               </button>
               <button
                 type="button"
                 onClick={handleConfirmRemove}
                 disabled={removeRoleMutation.isPending}
                 className="px-btn danger sm"
               >
                 {removeRoleMutation.isPending ? 'Removing…' : 'Remove role'}
               </button>
             </div>
           </DialogContent>
         </Dialog>
       </>
     )
     ```
  4. Migrate any assign-role dropdown form to RHF if it was previously controlled by local useState. `useAssignRole` from Task 13 handles the mutation.

  Imports:
  ```tsx
  import { useState } from 'react'
  import { toast } from 'sonner'
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
  } from '@/components/px'

  import { useOrgUnitMembers } from '@/lib/hooks/use-org-unit-members'
  import { useRoles } from '@/lib/hooks/use-roles'
  import { useAssignRole } from '@/lib/hooks/use-assign-role'
  import { useRemoveRole } from '@/lib/hooks/use-remove-role'
  ```

- [ ] **Step 22.4: Run tests + final checks**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx vitest run tests/settings/members-section-dialog.test.tsx && npx tsc --noEmit && npm run lint && npm run build
  ```
  Expected: 2/2 tests pass. tsc, lint, build clean.

- [ ] **Step 22.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/MembersSection.tsx \
          frontend/app/tests/settings/members-section-dialog.test.tsx
  git commit -m "refactor(org-units/detail): migrate MembersSection to hooks; confirm→Dialog"
  ```

---

## Phase 10 — Final verification

## Task 23: Grep verifications

**Goal:** Assert §8.5.7's codebase-level acceptance criteria.

- [ ] **Step 23.1: Zero `signInWithPassword` references in frontend app code**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  grep -rn 'signInWithPassword' app/ components/ lib/ 2>/dev/null
  ```
  Expected: no output. If anything shows up, migrate it before continuing.

- [ ] **Step 23.2: Zero `confirm(` calls under `[unitId]/`**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  grep -rn 'confirm(' 'app/(dashboard)/settings/org-units/[unitId]/' 2>/dev/null \
    | grep -v 'confirmPassword\|Confirm password\|onConfirm\|setConfirm\|/confirm'
  ```
  Expected: no output.

- [ ] **Step 23.3: Zero raw `useState` for form state on the six B4 pages**

  ```bash
  for f in \
    'app/(auth)/login/page.tsx' \
    'app/(auth)/invite/page.tsx' \
    'app/onboarding/page.tsx' \
    'app/(dashboard)/settings/team/page.tsx' \
    'app/(dashboard)/settings/org-units/page.tsx' \
    'app/(dashboard)/settings/org-units/[unitId]/page.tsx'; do
    echo "--- $f ---"
    grep -n 'useState' "/home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app/$f" || echo '(no useState)'
  done
  ```
  Expected: remaining `useState` usages are only for UI-local state (dialog open/close flags, show-password toggles, selected row IDs). No `useState` holds form values or server data. If anything else appears, go back and migrate it.

## Task 24: Whole-batch final gates

- [ ] **Step 24.1: Frontend full suite**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/frontend/app
  npx tsc --noEmit && npm run lint && npm run test && npm run build
  ```
  Expected: tsc 0 errors, lint 0 errors, vitest all pass (63 baseline + 4 error-utility + 3 login + 3 invite + 3 team + 2 org-unit create + 2 members-section = 80 total), `next build` clean.

- [ ] **Step 24.2: Backend full suite**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4/backend/nexus
  docker compose up -d postgres redis
  docker compose run --rm nexus pytest -x \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
    --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
    --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips
  ```
  Expected: 490 baseline + new login tests (~7) passed, 4 deselected.

- [ ] **Step 24.3: Browser smoke — golden path per page**

  Using a local dev server (`npm run dev` on port 3000 + backend up on port 8000):
  - `/login` — enter valid credentials, reach `/` (or `/onboarding` for fresh super admin).
  - `/login` — enter a known-bad password, see the inline root error, no redirect.
  - `/invite?token=...` — enter valid matching passwords, reach redirect; then enter mismatched passwords on a second attempt and see the field-level error.
  - `/onboarding` — click workspace type, fill company profile, complete flow to `/`.
  - `/settings/team` — submit a malformed email, see field error; submit a valid email, see invite appear in pending table.
  - `/settings/org-units` — open create form, submit empty name → error; submit with valid name → new unit appears.
  - `/settings/org-units/{unitId}` — open a company unit, edit name, save; see toast; refresh and confirm persistence.
  - `/settings/org-units/{unitId}` — in MembersSection, click Remove → Dialog appears (not browser confirm); confirm → role is removed and the members list refreshes.

- [ ] **Step 24.4: Mark spec §8.5 complete**

  Optional documentation update — modify the spec's §8.5 block to add a **Status: Completed** line referencing the final merge commit. This is a post-merge step that happens on `main`, not on the batch branch.

---

## Post-plan checklist

Before merging the batch:

- Every task above is committed on `cleanup/batch-4-form-migration`.
- All baselines pass (P.2, P.3 + Task 24).
- Subagent review per cluster per `feedback_subagent_review_cadence`:
  - Combined: C1 (Tasks 1–2), C3 (Tasks 5–7), C5 (Tasks 8–9), C6 (Tasks 10–13), C7 (Tasks 14–16), Task 21 (the three parallel-detail subcomponents).
  - Split: C2 (Tasks 3–4) — backend auth module gate + CLAUDE.md human review, C8 (Task 17), C9 (Task 18), Task 20, Task 22.
- Final whole-batch reviewer (Opus) runs over the merged delta and produces an APPROVE / REQUEST CHANGES summary before the `--no-ff` merge to `main`.
- Worktree cleanup:
  ```bash
  git worktree remove /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-4
  ```
  Only after the batch is merged.

---





