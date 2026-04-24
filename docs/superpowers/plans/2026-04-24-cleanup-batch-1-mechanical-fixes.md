# Cleanup Batch 1 — Critical Mechanical Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every CRITICAL and HIGH-severity mechanical fix from section 5 of the 2026-04-24 cleanup spec — surgical changes only, no schema or contract changes (those are in Batch 2).

**Architecture:** Frontend-only batch. No backend changes. Each task is one logical commit; tests cover the behaviour-changing tasks (SSE retry, apiFetch, cache invalidation, beforeunload). Pure deletions, type-only edits, and wiring changes ship without new tests but are guarded by `npm run type-check` and the existing test suite.

**Tech Stack:** Next.js 16 (App Router), React 19, TypeScript strict, TanStack Query v5, @microsoft/fetch-event-source, Vitest + Testing Library + jsdom.

---

## File Structure

| File | Role | Status |
|---|---|---|
| `frontend/app/lib/hooks/use-questions-status-stream.ts` | SSE hook for question-bank events | Modify (retry parity) |
| `frontend/app/lib/api/client.ts` | `apiFetch` core | Modify (signal + 204 + signature) |
| `frontend/app/lib/api/candidate-session.ts` | Candidate-surface API helpers | Modify (default base, error spread) |
| `frontend/app/lib/api/org-units.ts` | Org-unit API namespace | Modify (typed returns, drop `MeData`/`me()`) |
| `frontend/app/lib/api/auth.ts` | NEW — single home for `authApi.me()` and `MeResponse` | Create |
| `frontend/app/lib/hooks/use-job.ts` | Job detail query hook | Modify (accept `isStreaming`) |
| `frontend/app/lib/hooks/use-confirm-signals.ts` | Confirm-signals mutation | Modify (invalidate `jobs-list`) |
| `frontend/app/lib/hooks/use-save-signals.ts` | Save-signals mutation | Modify (invalidate `jobs-list`) |
| `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx` | Pipeline editor | Modify (beforeunload) |
| `frontend/app/app/(dashboard)/jobs/page.tsx` | Jobs index | Modify (Link/button, bulk delete dialog, inline-style cleanup) |
| `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` | JD review page | Modify (dead-code cleanup, `key={i}`) |
| `frontend/app/app/(dashboard)/jobs/[jobId]/loading.tsx` | NEW — Suspense fallback | Create |
| `frontend/app/app/(dashboard)/jobs/new/page.tsx` | New-role wizard | Modify (`useWatch` scoping) |
| `frontend/app/app/(dashboard)/layout.tsx` | Dashboard root layout | Modify (use `authApi.me`) |
| `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx` | Candidate wizard | Modify (drop dead `start` branch) |
| `frontend/app/tests/lib/api/client.test.ts` | NEW — apiFetch unit tests | Create |
| `frontend/app/tests/lib/api/candidate-session.test.ts` | NEW — error-shape test | Create |
| `frontend/app/tests/lib/hooks/use-questions-status-stream.test.ts` | NEW — SSE retry test | Create |
| `frontend/app/tests/lib/hooks/use-confirm-signals.test.ts` | NEW — cache invalidation test | Create |
| `frontend/app/tests/lib/api/auth.test.ts` | NEW — `authApi.me` round-trip | Create |

---

## Pre-flight

- [ ] **Step P1: Verify baseline is green**

  Run, in `/home/ishant/Projects/ProjectX/frontend/app`:
  ```bash
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: all four pass with zero errors. If any fails, stop and surface the failure to the user before proceeding — Batch 1 must not start on a red baseline.

---

## Task 1: SSE retry parity in `useQuestionsStatusStream` (B1.1)

**Goal:** Mirror the auth-retry + `MAX_TOTAL_RETRIES` shape from `use-job-status-stream.ts` into `use-questions-status-stream.ts`. A 401 on token expiry must trigger a one-time refresh + reconnect; transient retries are capped; permanent fatals stop cleanly.

**Files:**
- Modify: `frontend/app/lib/hooks/use-questions-status-stream.ts`
- Create: `frontend/app/tests/lib/hooks/use-questions-status-stream.test.ts`

- [ ] **Step 1.1: Create the failing test**

  Create `frontend/app/tests/lib/hooks/use-questions-status-stream.test.ts`:
  ```ts
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { renderHook, waitFor } from '@testing-library/react'
  import { afterEach, describe, expect, it, vi } from 'vitest'

  vi.mock('@/lib/auth/tokens', () => ({
    getFreshSupabaseToken: vi.fn().mockResolvedValue('fake-token'),
  }))

  const onopenMock = vi.fn()
  const onerrorMock = vi.fn()
  vi.mock('@microsoft/fetch-event-source', () => ({
    EventStreamContentType: 'text/event-stream',
    fetchEventSource: vi.fn(async (_url, opts: Record<string, unknown>) => {
      const onopen = opts.onopen as (r: Response) => Promise<void>
      const onerror = opts.onerror as (e: Error) => void
      onopenMock.mockImplementation(onopen)
      onerrorMock.mockImplementation(onerror)
      // Simulate a 401 from the server.
      try {
        await onopen({
          ok: false,
          status: 401,
          headers: new Headers(),
        } as Response)
      } catch (err) {
        // Library would normally surface this — we want our hook to catch it.
        throw err
      }
    }),
  }))

  import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  function wrapper({ children }: { children: React.ReactNode }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
  }

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('useQuestionsStatusStream', () => {
    it('refreshes the token and reconnects once on a 401', async () => {
      renderHook(() => useQuestionsStatusStream('job-1', null), { wrapper })
      await waitFor(() => {
        // First call to open the stream + second call after refresh.
        expect(getFreshSupabaseToken).toHaveBeenCalledTimes(2)
      })
    })
  })
  ```

- [ ] **Step 1.2: Verify the test fails**

  Run, in `frontend/app`:
  ```bash
  npm run test -- tests/lib/hooks/use-questions-status-stream.test.ts
  ```
  Expected: FAIL — `getFreshSupabaseToken` is called once, not twice (the current hook gives up after the first error).

- [ ] **Step 1.3: Rewrite the hook with the retry parity pattern**

  Replace the entire file `frontend/app/lib/hooks/use-questions-status-stream.ts` with:
  ```ts
  'use client'

  import {
    EventStreamContentType,
    fetchEventSource,
  } from '@microsoft/fetch-event-source'
  import { useQueryClient } from '@tanstack/react-query'
  import { useEffect, useRef } from 'react'

  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

  /** Max times we'll re-fetch a token and reconnect on auth failure. */
  const MAX_AUTH_RETRIES = 2
  /** Absolute reconnection ceiling — protects against runaway loops. */
  const MAX_TOTAL_RETRIES = 20

  class AuthSSEError extends Error {}
  class FatalSSEError extends Error {}

  /**
   * Opens an SSE connection to /api/jobs/{id}/pipeline/questions/status-stream
   * and invalidates TanStack Query caches on every event.
   *
   * Token lifecycle mirrors useJobStatusStream:
   *  - Fresh token fetched before each connection attempt.
   *  - On 401 → break out of fetch-event-source's internal retry, refresh
   *    via getFreshSupabaseToken (which uses the cookie refresh token),
   *    reconnect.
   *  - After MAX_AUTH_RETRIES the hook gives up.
   *  - Every onerror counts against MAX_TOTAL_RETRIES — runaway transient
   *    retry storms cannot occur.
   */
  export function useQuestionsStatusStream(
    jobId: string,
    selectedStageId: string | null,
  ) {
    const queryClient = useQueryClient()

    const selectedStageIdRef = useRef(selectedStageId)
    useEffect(() => {
      selectedStageIdRef.current = selectedStageId
    }, [selectedStageId])

    useEffect(() => {
      if (!jobId) return

      const ctrl = new AbortController()
      let authRetries = 0
      let totalRetries = 0

      async function connect(): Promise<void> {
        if (ctrl.signal.aborted) return

        let token: string
        try {
          token = await getFreshSupabaseToken()
        } catch {
          // Caller is probably redirecting to login.
          return
        }
        if (ctrl.signal.aborted) return

        try {
          await fetchEventSource(
            `${API_URL}/api/jobs/${jobId}/pipeline/questions/status-stream`,
            {
              method: 'GET',
              headers: { Authorization: `Bearer ${token}` },
              signal: ctrl.signal,

              async onopen(response) {
                if (
                  response.ok &&
                  response.headers
                    .get('content-type')
                    ?.includes(EventStreamContentType)
                ) {
                  authRetries = 0
                  return
                }
                if (response.status === 401 || response.status === 403) {
                  throw new AuthSSEError()
                }
                if (
                  response.status >= 400 &&
                  response.status < 500 &&
                  response.status !== 429
                ) {
                  throw new FatalSSEError(
                    `SSE connection refused (${response.status}).`,
                  )
                }
                throw new Error(`SSE server error: ${response.status}`)
              },

              onmessage(ev) {
                void queryClient.invalidateQueries({
                  queryKey: ['banks', jobId],
                })
                const currentStageId = selectedStageIdRef.current
                if (
                  (ev.event === 'bank.status_changed' ||
                    ev.event === 'bank.question_updated') &&
                  currentStageId
                ) {
                  void queryClient.invalidateQueries({
                    queryKey: ['bank', jobId, currentStageId],
                  })
                }
              },

              onerror(err) {
                if (
                  err instanceof AuthSSEError ||
                  err instanceof FatalSSEError
                ) {
                  throw err
                }
                totalRetries++
                if (totalRetries > MAX_TOTAL_RETRIES) {
                  throw new FatalSSEError(
                    'Live updates unavailable — reconnection limit reached.',
                  )
                }
                console.warn('Questions SSE transient error', err)
              },
            },
          )
        } catch (err) {
          if (ctrl.signal.aborted) return

          if (err instanceof AuthSSEError) {
            authRetries++
            totalRetries++
            if (
              authRetries <= MAX_AUTH_RETRIES &&
              totalRetries <= MAX_TOTAL_RETRIES
            ) {
              return connect()
            }
            return
          }

          if (err instanceof FatalSSEError) {
            console.warn('Questions SSE fatal:', err.message)
            return
          }

          console.warn('Questions SSE connection failed', err)
        }
      }

      void connect()
      return () => ctrl.abort()
    }, [jobId, queryClient])
  }
  ```

- [ ] **Step 1.4: Verify the test passes**

  Run:
  ```bash
  npm run test -- tests/lib/hooks/use-questions-status-stream.test.ts
  ```
  Expected: PASS — `getFreshSupabaseToken` called twice (initial + after 401).

- [ ] **Step 1.5: Verify whole suite still green**

  Run:
  ```bash
  npm run test && npm run type-check
  ```
  Expected: all pass.

- [ ] **Step 1.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/hooks/use-questions-status-stream.ts \
          frontend/app/tests/lib/hooks/use-questions-status-stream.test.ts
  git commit -m "fix(frontend): SSE retry parity for question-bank stream

  useQuestionsStatusStream now matches useJobStatusStream: refreshes the
  token + reconnects once on 401, caps total reconnects at 20, surfaces
  fatals instead of looping. Closes the runaway-retry hole that would
  hammer Nexus when a recruiter's session token expired during a long
  question-generation run.

  Adds a vitest spec verifying the 401 → reconnect path."
  ```

---

## Task 2: `apiFetch` accepts `signal`, handles 204, never throws on JSON parse (B1.2 base, B1.3)

**Goal:** Make `apiFetch` thread `AbortSignal` from caller, return `undefined` on 204 No Content instead of crashing on `res.json()`, and stay backwards-compatible for everything else.

**Files:**
- Modify: `frontend/app/lib/api/client.ts`
- Create: `frontend/app/tests/lib/api/client.test.ts`

- [ ] **Step 2.1: Write failing tests**

  Create `frontend/app/tests/lib/api/client.test.ts`:
  ```ts
  import { afterEach, describe, expect, it, vi } from 'vitest'

  import { apiFetch, ApiError } from '@/lib/api/client'

  function mockFetchOnce(response: Response) {
    const fn = vi.fn().mockResolvedValueOnce(response)
    vi.stubGlobal('fetch', fn)
    return fn
  }

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  describe('apiFetch', () => {
    it('returns undefined for 204 No Content', async () => {
      mockFetchOnce(new Response(null, { status: 204 }))
      const result = await apiFetch<void>('/api/x', { token: 't' })
      expect(result).toBeUndefined()
    })

    it('threads the caller-provided signal into fetch', async () => {
      const fetchMock = mockFetchOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      const ctrl = new AbortController()
      await apiFetch('/api/x', { token: 't', signal: ctrl.signal })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      const init = fetchMock.mock.calls[0][1] as RequestInit
      expect(init.signal).toBe(ctrl.signal)
    })

    it('throws ApiError with status on non-OK responses', async () => {
      mockFetchOnce(
        new Response(JSON.stringify({ detail: 'nope' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      await expect(apiFetch('/api/x', { token: 't' })).rejects.toMatchObject({
        message: 'nope',
        status: 403,
      })
      await expect(apiFetch('/api/x', { token: 't' })).rejects.toBeInstanceOf(
        ApiError,
      )
    })
  })
  ```

- [ ] **Step 2.2: Verify tests fail**

  Run:
  ```bash
  npm run test -- tests/lib/api/client.test.ts
  ```
  Expected: FAIL on the 204 test (`res.json()` crashes on empty body) AND on the signal-threading test (current signature drops the field).

- [ ] **Step 2.3: Update apiFetch**

  Replace the entire file `frontend/app/lib/api/client.ts` with:
  ```ts
  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  /**
   * Error thrown by apiFetch when the backend returns a non-OK response.
   *
   * Carries the HTTP status code alongside the parsed detail message so
   * callers can branch on status (e.g. 404 => "not found, return null")
   * without resorting to fragile substring matching on err.message.
   */
  export class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

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
      // FastAPI 422 returns detail as an array of validation errors.
      const detail = body.detail;
      const message = Array.isArray(detail)
        ? detail.map((e: { msg: string }) => e.msg).join(", ")
        : typeof detail === "string"
          ? detail
          : `API error: ${res.status}`;
      throw new ApiError(message, res.status);
    }

    // 204 No Content has an empty body — calling res.json() would throw.
    if (res.status === 204) return undefined as T;

    return res.json();
  }
  ```

- [ ] **Step 2.4: Verify tests pass**

  Run:
  ```bash
  npm run test -- tests/lib/api/client.test.ts
  ```
  Expected: PASS.

- [ ] **Step 2.5: Verify whole suite still green**

  Run:
  ```bash
  npm run type-check && npm run test
  ```
  Expected: all pass. The new `signal` field is optional, so callers that omit it still type-check.

- [ ] **Step 2.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/api/client.ts \
          frontend/app/tests/lib/api/client.test.ts
  git commit -m "fix(frontend): apiFetch accepts AbortSignal and handles 204

  Adds optional \`signal\` to the apiFetch options so callers (notably
  TanStack Query hooks) can propagate cancellation. Returns \`undefined\`
  on 204 No Content responses instead of crashing on \`res.json()\` of an
  empty body — fixes the silent SyntaxError that surfaces as a confusing
  ApiError on candidate-side endpoints (resume confirm, redact PII).

  Adds a vitest spec covering 204, signal threading, and error shape."
  ```

---

## Task 3: Thread `AbortSignal` through query hooks (B1.2 propagation)

**Goal:** Every TanStack Query hook that calls `apiFetch` (directly or via an API namespace) hands the queryFn's `signal` to it. Cancellations from `queryClient` and component unmounts now actually abort in-flight requests.

**Files:**
- Modify: every hook in `frontend/app/lib/hooks/*.ts` that uses `apiFetch` or an `*Api.method(token, ...)` call.

- [ ] **Step 3.1: Audit which hooks need changes**

  Run, in `frontend/app`:
  ```bash
  grep -l "queryFn:\|mutationFn:" lib/hooks/*.ts
  ```
  Expected output: list of hook files. The plan only changes `useQuery`-style hooks (queryFn signature receives `{ signal }`); mutations don't get auto-cancelled by TanStack Query so we leave them alone.

- [ ] **Step 3.2: Update each query hook**

  For each `useQuery` hook in `lib/hooks/`, change the queryFn from:
  ```ts
  queryFn: async () => {
    const token = await getFreshSupabaseToken()
    return someApi.method(token, jobId)
  }
  ```
  to:
  ```ts
  queryFn: async ({ signal }) => {
    const token = await getFreshSupabaseToken()
    return someApi.method(token, jobId, { signal })
  }
  ```

  Each affected API namespace method gains an optional `opts?: { signal?: AbortSignal }` parameter that it passes through to `apiFetch`. Example for `lib/api/jobs.ts`'s `get`:
  ```ts
  // Before
  get: (token: string, jobId: string): Promise<JobPostingWithSnapshot> =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${jobId}`, { token }),

  // After
  get: (
    token: string,
    jobId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<JobPostingWithSnapshot> =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${jobId}`, {
      token,
      signal: opts?.signal,
    }),
  ```

  Apply the same opt-in `opts?: { signal?: AbortSignal }` pattern to any namespace method called from a query hook. **Do not** add it to mutation-only methods (POST/PATCH/DELETE called from `useMutation`).

- [ ] **Step 3.3: Verify type-check passes**

  Run:
  ```bash
  npm run type-check
  ```
  Expected: PASS. If a hook is missed, TS won't error (signal is optional) but you should still verify hooks visually.

- [ ] **Step 3.4: Verify tests still pass**

  ```bash
  npm run test
  ```
  Expected: PASS.

- [ ] **Step 3.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/hooks/ frontend/app/lib/api/
  git commit -m "fix(frontend): thread AbortSignal through query hooks

  Every useQuery hook now passes the queryFn's signal into the API
  namespace method, which in turn passes it to apiFetch. Fast navigation
  and explicit invalidations now actually cancel in-flight requests
  instead of leaving them dangling.

  Mutations are untouched — TanStack Query does not auto-cancel them."
  ```

---

## Task 4: `candidate-session.ts` — safer error shape, sane API_BASE default (B1.4, B1.17)

**Goal:** Stop spreading attacker-influenced JSON onto an `Error` (could shadow `stack`/`name`). Make `API_BASE` default to `http://127.0.0.1:8000` like every other namespace, so a missing env var doesn't silently hit the Next origin.

**Files:**
- Modify: `frontend/app/lib/api/candidate-session.ts`
- Create: `frontend/app/tests/lib/api/candidate-session.test.ts`

- [ ] **Step 4.1: Write failing test**

  Create `frontend/app/tests/lib/api/candidate-session.test.ts`:
  ```ts
  import { afterEach, describe, expect, it, vi } from 'vitest'

  import { candidateSessionApi } from '@/lib/api/candidate-session'

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  describe('candidateSessionApi error handling', () => {
    it('only copies whitelisted fields onto the thrown error', async () => {
      const malicious = {
        detail: 'invalid otp',
        code: 'OTP_INVALID',
        attempts_remaining: 2,
        retry_after_seconds: 30,
        // Attacker-supplied keys that would otherwise shadow Error fields.
        stack: 'pwned',
        name: 'PwnedError',
        message: 'pwned',
      }
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify(malicious), {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      )
      try {
        await candidateSessionApi.verifyOtp('tok', { code: '000000' })
        throw new Error('should have thrown')
      } catch (err) {
        expect(err).toBeInstanceOf(Error)
        const e = err as Error & Record<string, unknown>
        expect(e.name).toBe('Error')
        expect(e.stack).not.toBe('pwned')
        expect(e.message).toBe('invalid otp')
        expect(e.code).toBe('OTP_INVALID')
        expect(e.attempts_remaining).toBe(2)
        expect(e.retry_after_seconds).toBe(30)
      }
    })
  })
  ```

- [ ] **Step 4.2: Verify it fails**

  ```bash
  npm run test -- tests/lib/api/candidate-session.test.ts
  ```
  Expected: FAIL — current code spreads `name`/`stack` onto the error.

- [ ] **Step 4.3: Update `_call`**

  In `frontend/app/lib/api/candidate-session.ts`:

  Replace the line:
  ```ts
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''
  ```
  with:
  ```ts
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  ```

  Replace the error-construction block (currently lines ~78-84):
  ```ts
  const message =
    typeof parsed.detail === 'string' ? parsed.detail : `HTTP ${r.status}`
  const err: CandidateSessionError = Object.assign(new Error(message), {
    status: r.status,
    ...parsed,
  })
  throw err
  ```
  with:
  ```ts
  const message =
    typeof parsed.detail === 'string' ? parsed.detail : `HTTP ${r.status}`
  const err = new Error(message) as CandidateSessionError
  err.status = r.status
  // Cherry-pick known fields rather than spreading attacker-influenced JSON
  // (which could shadow Error.prototype.stack / .name / .message).
  if (typeof parsed.code === 'string') err.code = parsed.code
  if (typeof parsed.attempts_remaining === 'number')
    err.attempts_remaining = parsed.attempts_remaining
  if (typeof parsed.retry_after_seconds === 'number')
    err.retry_after_seconds = parsed.retry_after_seconds
  throw err
  ```

- [ ] **Step 4.4: Verify it passes**

  ```bash
  npm run test -- tests/lib/api/candidate-session.test.ts
  ```
  Expected: PASS.

- [ ] **Step 4.5: Verify whole suite green**

  ```bash
  npm run type-check && npm run test
  ```

- [ ] **Step 4.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/api/candidate-session.ts \
          frontend/app/tests/lib/api/candidate-session.test.ts
  git commit -m "fix(frontend): candidate-session error shape + API_BASE default

  - Stop spreading the parsed JSON body onto the Error instance — only
    copy whitelisted fields (code, attempts_remaining, retry_after_seconds).
    Eliminates the shadowing risk where an attacker-influenced backend
    response could overwrite Error.prototype.stack / .name / .message.
  - Default API_BASE to http://127.0.0.1:8000 to match every other API
    namespace; previously a missing NEXT_PUBLIC_API_URL would silently
    target the Next origin instead of Nexus."
  ```

---

## Task 5: List-cache invalidation on signals confirm/save (B1.5)

**Goal:** After confirming or saving signals, both `['jobs', jobId]` AND `['jobs-list']` are invalidated so the `/pipeline` view shows the just-changed job without a 10s wait.

**Files:**
- Modify: `frontend/app/lib/hooks/use-confirm-signals.ts`
- Modify: `frontend/app/lib/hooks/use-save-signals.ts`
- Create: `frontend/app/tests/lib/hooks/use-confirm-signals.test.ts`

- [ ] **Step 5.1: Write failing test**

  Create `frontend/app/tests/lib/hooks/use-confirm-signals.test.ts`:
  ```ts
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { act, renderHook, waitFor } from '@testing-library/react'
  import { afterEach, describe, expect, it, vi } from 'vitest'

  vi.mock('@/lib/auth/tokens', () => ({
    getFreshSupabaseToken: vi.fn().mockResolvedValue('tok'),
  }))
  vi.mock('@/lib/api/jobs', () => ({
    jobsApi: {
      confirmSignals: vi.fn().mockResolvedValue({ id: 'job-1' }),
    },
  }))

  import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'

  function makeWrapper(client: QueryClient) {
    return function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      )
    }
  }

  afterEach(() => vi.clearAllMocks())

  describe('useConfirmSignals', () => {
    it('invalidates both the job detail and the jobs-list caches', async () => {
      const client = new QueryClient()
      const spy = vi.spyOn(client, 'invalidateQueries')

      const { result } = renderHook(() => useConfirmSignals('job-1'), {
        wrapper: makeWrapper(client),
      })

      await act(async () => {
        await result.current.mutateAsync()
      })

      await waitFor(() => {
        expect(spy).toHaveBeenCalledWith({ queryKey: ['jobs', 'job-1'] })
        expect(spy).toHaveBeenCalledWith({ queryKey: ['jobs-list'] })
      })
    })
  })
  ```

- [ ] **Step 5.2: Verify it fails**

  ```bash
  npm run test -- tests/lib/hooks/use-confirm-signals.test.ts
  ```
  Expected: FAIL — the second invalidate call is missing.

- [ ] **Step 5.3: Update `use-confirm-signals.ts`**

  In `frontend/app/lib/hooks/use-confirm-signals.ts`, replace the `onSuccess` block:
  ```ts
  // Before
  onSuccess: () => {
    toast.success('Signals confirmed')
    void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
  },

  // After
  onSuccess: () => {
    toast.success('Signals confirmed')
    void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
  },
  ```

- [ ] **Step 5.4: Update `use-save-signals.ts` the same way**

  In `frontend/app/lib/hooks/use-save-signals.ts`, replace:
  ```ts
  onSuccess: () => {
    toast.success('Signals saved')
    void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
  },
  ```
  with:
  ```ts
  onSuccess: () => {
    toast.success('Signals saved')
    void queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    void queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
  },
  ```

- [ ] **Step 5.5: Verify it passes**

  ```bash
  npm run test -- tests/lib/hooks/use-confirm-signals.test.ts
  ```
  Expected: PASS.

- [ ] **Step 5.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/hooks/use-confirm-signals.ts \
          frontend/app/lib/hooks/use-save-signals.ts \
          frontend/app/tests/lib/hooks/use-confirm-signals.test.ts
  git commit -m "fix(frontend): invalidate jobs-list on signals save/confirm

  Both useConfirmSignals and useSaveSignals now invalidate ['jobs-list']
  in addition to the per-job key. The /pipeline view filters on the list
  cache for status === 'signals_confirmed', so without this it would lag
  by up to 10s (default staleTime) before showing the just-confirmed job."
  ```

---

## Task 6: Polling/SSE coordination in `useJob` (B1.6)

**Goal:** When the SSE stream is active, `useJob` should not also poll. Currently both fire concurrently for `signals_extracting` jobs.

**Files:**
- Modify: `frontend/app/lib/hooks/use-job.ts`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` (caller)

- [ ] **Step 6.1: Update `useJob` signature**

  Replace the entire file `frontend/app/lib/hooks/use-job.ts` with:
  ```ts
  'use client'

  import { useQuery } from '@tanstack/react-query'

  import { jobsApi, type JobPostingWithSnapshot } from '@/lib/api/jobs'
  import { getFreshSupabaseToken } from '@/lib/auth/tokens'

  /**
   * Job detail query.
   *
   * `isStreaming` lets the caller suppress the polling fallback while the
   * SSE stream is alive. When the stream dies the caller passes `false`
   * and polling kicks back in for active processing states.
   */
  export function useJob(jobId: string, isStreaming = false) {
    return useQuery<JobPostingWithSnapshot>({
      queryKey: ['jobs', jobId],
      queryFn: async ({ signal }) => {
        const token = await getFreshSupabaseToken()
        return jobsApi.get(token, jobId, { signal })
      },
      enabled: !!jobId,
      staleTime: 5_000,
      refetchInterval: (query) => {
        if (isStreaming) return false
        const data = query.state.data
        if (!data) return false
        if (data.status === 'signals_extracting') return 2_000
        if (data.enrichment_status === 'streaming') return 2_000
        return false
      },
    })
  }
  ```

  Note: this depends on Task 3 having added the `opts?` parameter to `jobsApi.get`. If Task 3 hasn't run yet, `jobsApi.get` only takes `(token, jobId)` — drop the third argument here and revisit after Task 3.

- [ ] **Step 6.2: Update the caller in `app/(dashboard)/jobs/[jobId]/page.tsx`**

  Locate the call site (around line 178 in `JobReviewPage`):
  ```ts
  const { data: job, isLoading } = useJob(jobId)
  const { data: pipeline } = useJobPipeline(jobId)
  const { status, error: sseError } = useJobStatusStream(jobId)
  ```

  Reorder so the stream's `isStreaming` flag is available, then pass it to `useJob`:
  ```ts
  const { status, error: sseError, isStreaming } = useJobStatusStream(jobId)
  const { data: job, isLoading } = useJob(jobId, isStreaming)
  const { data: pipeline } = useJobPipeline(jobId)
  ```

- [ ] **Step 6.3: Verify type-check and tests**

  ```bash
  npm run type-check && npm run test
  ```
  Expected: PASS.

- [ ] **Step 6.4: Manual smoke**

  Start the dev server (`npm run dev`), open a job in `signals_extracting` state, and confirm in DevTools Network tab that requests to `GET /api/jobs/{id}` fire roughly once when the stream invalidates the cache, NOT every 2s on top of it.

- [ ] **Step 6.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/hooks/use-job.ts \
          frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx
  git commit -m "fix(frontend): suppress useJob polling while SSE is streaming

  useJob now accepts an isStreaming flag from useJobStatusStream and
  skips the 2s polling fallback whenever the stream is alive. Eliminates
  the doubled-up requests that previously hit /api/jobs/{id} when both
  the SSE invalidation and the refetch interval fired concurrently."
  ```

---

## Task 7: Hydration fixes — Suspense boundary, `<Link><button>` (B1.7, B1.8)

**Goal:** Eliminate the React 19 hydration errors caused by (a) `useSearchParams()` in a client component without a Suspense boundary and (b) nested `<a><button>` interactive elements.

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/[jobId]/loading.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx`

- [ ] **Step 7.1: Create the loading boundary**

  Create `frontend/app/app/(dashboard)/jobs/[jobId]/loading.tsx`:
  ```tsx
  /**
   * Suspense fallback for the JD-review route. Required because the page
   * (and the inner JDReviewShell) call useSearchParams() in client
   * components — without a loading boundary, Next 16 prints a hydration
   * warning and the route opts out of static rendering.
   */
  export default function Loading() {
    return (
      <div
        className="px-6 pb-4 pt-5 text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Loading…
      </div>
    )
  }
  ```

- [ ] **Step 7.2: Fix nested interactive elements in jobs index**

  In `frontend/app/app/(dashboard)/jobs/page.tsx`, find the `<Link href="/jobs/new">` block (around lines 329-333):
  ```tsx
  <Link href="/jobs/new">
    <button className="px-btn primary sm" type="button">
      <PlusIcon size={12} />
      New role
    </button>
  </Link>
  ```
  Replace with:
  ```tsx
  <Link
    href="/jobs/new"
    className="px-btn primary sm"
  >
    <PlusIcon size={12} />
    New role
  </Link>
  ```
  Notes:
  - Remove the now-unused `<button>` wrapper.
  - The `px-btn` class works on `<a>` (the underlying `<Link>` element) the same as on `<button>` — verify by visual smoke after committing.
  - Keyboard activation works automatically on `<a href>`.

- [ ] **Step 7.3: Verify type-check, lint, build**

  ```bash
  npm run type-check && npm run lint && npm run build
  ```
  Expected: PASS. The build step is what catches the static-rendering opt-out warning if the loading.tsx is missing.

- [ ] **Step 7.4: Verify tests pass**

  ```bash
  npm run test
  ```
  Expected: PASS.

- [ ] **Step 7.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/app/\(dashboard\)/jobs/\[jobId\]/loading.tsx \
          frontend/app/app/\(dashboard\)/jobs/page.tsx
  git commit -m "fix(frontend): hydration warnings on jobs routes

  - Add loading.tsx for /jobs/[jobId] so useSearchParams() in the page
    and JDReviewShell client components is wrapped in a Suspense
    boundary. Prevents React 19 from logging a hydration error and
    opting the route out of static rendering.
  - Replace <Link><button>...</button></Link> with a styled <Link> on
    the jobs index. HTML forbids nested <a><button>; React 19 throws a
    hydration error. The px-btn class works on either element."
  ```

---

## Task 8: `useWatch` perf in new-role wizard (B1.9)

**Goal:** Stop re-rendering the entire `NewJobPage` on every keystroke.

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/new/page.tsx`

- [ ] **Step 8.1: Locate and audit `useWatch` consumers**

  Run, in `frontend/app`:
  ```bash
  grep -n "useWatch\|values\." app/\(dashboard\)/jobs/new/page.tsx
  ```
  Identify which fields the `values` object is actually read from. Typical patterns: `values.title`, `values.description_raw`, `values.org_unit_id` for the live preview.

- [ ] **Step 8.2: Replace global useWatch with field-scoped subscriptions**

  In `frontend/app/app/(dashboard)/jobs/new/page.tsx`:

  Replace:
  ```ts
  const values = useWatch({ control: form.control })
  ```
  with one `useWatch` per field actually read by the preview/summary, e.g.:
  ```ts
  const title = useWatch({ control: form.control, name: 'title' })
  const description = useWatch({
    control: form.control,
    name: 'description_raw',
  })
  const orgUnitId = useWatch({ control: form.control, name: 'org_unit_id' })
  // …repeat for every field consumed below.
  ```

  Then update the references to `values.title` → `title`, `values.description_raw` → `description`, etc.

  If a field is consumed in a long list (e.g. all employment-type metadata for the summary card), you may keep one `useWatch({ name: ['employment_type', 'work_arrangement', 'location'] as const })` returning a tuple — that is also field-scoped and won't re-render on unrelated keystrokes.

- [ ] **Step 8.3: Verify type-check, lint, build**

  ```bash
  npm run type-check && npm run lint && npm run build
  ```
  Expected: PASS.

- [ ] **Step 8.4: Manual smoke**

  Start dev server, open `/jobs/new`, open React DevTools Profiler, type into the title field. Confirm only the components reading `title` re-render — not `Summary` or `WizardProgress` if they don't read it.

- [ ] **Step 8.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/app/\(dashboard\)/jobs/new/page.tsx
  git commit -m "perf(frontend): scope useWatch in new-role wizard to fields actually read

  Replaces the unscoped useWatch({ control }) — which subscribed the
  whole page to every form change — with per-field useWatch calls.
  Stops the WizardProgress, Summary, and conditional field tree from
  re-rendering on every keystroke."
  ```

---

## Task 9: Beforeunload guard for unsaved pipeline edits (B1.10)

**Goal:** Warn the user if they're about to close the tab/browser with unsaved pipeline changes. Keep the existing unmount `mutate()` call for SPA navigation (TanStack Query mutations survive component unmount).

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx`

- [ ] **Step 9.1: Add the beforeunload effect**

  In `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx`, immediately after the existing unmount-flush effect (around line 232), add:
  ```ts
  // Warn the user before they close the tab with unsaved edits. Browsers
  // ignore the returned string but show a generic "leave site?" prompt.
  // SPA navigation does NOT trigger beforeunload — the existing unmount
  // flush above handles that case (TanStack Query mutations survive
  // component unmount and complete in the background).
  useEffect(() => {
    function handler(e: BeforeUnloadEvent) {
      if (!isDirty) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])
  ```

  This relies on `isDirty` already being a stateful flag in the component (it is — set inside the autosave debouncer's success path).

- [ ] **Step 9.2: Verify type-check + tests pass**

  ```bash
  npm run type-check && npm run test
  ```
  Expected: PASS.

- [ ] **Step 9.3: Manual smoke**

  Start dev server, open a pipeline, edit a stage name (don't wait for autosave), try to close the tab. Browser should prompt with "Leave site?". Save the form, close again — no prompt.

- [ ] **Step 9.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx
  git commit -m "fix(frontend): beforeunload guard for unsaved pipeline edits

  Warns the user before closing the tab if isDirty is true. SPA
  navigation still relies on the existing unmount flush (TanStack
  Query mutations survive component unmount). This closes the
  data-loss window for hard page closes (browser/tab close, hard
  navigation to a different origin)."
  ```

---

## Task 10: Dead code, key={i}, stale comments (B1.11, B1.12, B1.13, B1.14, B1.15)

**Goal:** Sweep the small low-severity items into one commit.

**Files:**
- Modify: `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx` — drop `currentStep === 'start'` branch
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` — remove `{canManage && !signal && null}` no-op, replace `key={i}` with stable key
- Modify: `frontend/app/lib/hooks/use-questions-status-stream.ts` — already done in Task 1 (the new code has correct deps)
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx` — replace inline `style={{ height: 20, fontSize: 10.5 }}` with utility classes

- [ ] **Step 10.1: Drop the unreachable wizard step branch**

  In `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx`, delete this single line (around line 107):
  ```tsx
  {currentStep === 'start' && <StartStep token={token} />}
  ```

  **Do NOT touch** the `WizardStepKey` union or `StepProgress.steps` — `'start'` is still legitimately used by `StepProgress` to render the progress dot for the final step. The state machine simply never *transitions* to `'start'`; the cam-mic branch with `camMicPassed === true` already renders `StartStep`. The only change is removing the dead JSX branch.

- [ ] **Step 10.2: Remove the no-op JSX in JD review**

  In `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` (around line 1370), delete:
  ```tsx
  {canManage && !signal && null}
  ```
  This always evaluates to `null` and serves no purpose.

- [ ] **Step 10.3: Stabilize the metaParts key**

  In the same file, around line 839, find:
  ```tsx
  {metaParts.map((p, i) => (
    <span key={i} className="flex items-center gap-2">
  ```
  Replace `key={i}` with a value derived from the content. Simplest stable key:
  ```tsx
  {metaParts.map((p, i) => (
    <span key={`${i}-${p}`} className="flex items-center gap-2">
  ```
  This is technically still index-prefixed but the content suffix discriminates duplicates. Acceptable because `metaParts` is rebuilt from job data each render — no user-driven reorder.

- [ ] **Step 10.4: Replace inline StatusPill styles**

  In `frontend/app/app/(dashboard)/jobs/page.tsx` around lines 37-38:
  ```tsx
  <span className={`px-chip ${m.cls}`} style={{ height: 20, fontSize: 10.5, fontWeight: 500, letterSpacing: 0.2 }}>
  ```
  Replace with:
  ```tsx
  <span
    className={`px-chip ${m.cls} h-5 text-[10.5px] font-medium tracking-wide`}
  >
  ```
  Notes:
  - `h-5` = 20px in Tailwind's default scale.
  - `text-[10.5px]` is an arbitrary value but matches the original; alternatives (`text-[11px]`) shift the visual.
  - `font-medium` = 500.
  - `tracking-wide` = `letter-spacing: 0.025em` ≈ 0.2px at 10.5px font size.

- [ ] **Step 10.5: Verify type-check, lint, build**

  ```bash
  npm run type-check && npm run lint && npm run build
  ```
  Expected: PASS.

- [ ] **Step 10.6: Verify tests pass**

  ```bash
  npm run test
  ```

- [ ] **Step 10.7: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/app/\(interview\)/interview/\[token\]/WizardShell.tsx \
          frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx \
          frontend/app/app/\(dashboard\)/jobs/page.tsx
  git commit -m "chore(frontend): remove dead code and inline-style nits

  - Drop unreachable currentStep === 'start' branch in WizardShell.
  - Remove no-op {canManage && !signal && null} block in JD review.
  - Stabilize metaParts react key with content suffix.
  - Replace inline StatusPill style props with Tailwind utilities."
  ```

---

## Task 11: Typed return generics on `orgUnitsApi` (B1.16)

**Goal:** `assignRole` and `removeRole` currently return `Promise<unknown>`. Add the explicit response generic.

**Files:**
- Modify: `frontend/app/lib/api/org-units.ts`

- [ ] **Step 11.1: Update both methods**

  In `frontend/app/lib/api/org-units.ts`:

  Replace the `assignRole` body:
  ```ts
  apiFetch(`/api/org-units/${unitId}/members`, {
    method: 'POST',
    token,
    body: JSON.stringify(body),
  }),
  ```
  with:
  ```ts
  apiFetch<{ status: string }>(`/api/org-units/${unitId}/members`, {
    method: 'POST',
    token,
    body: JSON.stringify(body),
  }),
  ```

  Replace the `removeRole` body:
  ```ts
  apiFetch(`/api/org-units/${unitId}/members/${userId}/roles/${roleId}`, {
    method: 'DELETE',
    token,
  }),
  ```
  with:
  ```ts
  apiFetch<{ status: string }>(
    `/api/org-units/${unitId}/members/${userId}/roles/${roleId}`,
    { method: 'DELETE', token },
  ),
  ```

- [ ] **Step 11.2: Verify type-check passes**

  ```bash
  npm run type-check
  ```
  Expected: PASS. Existing callers were already typing the result via the declared return type, so no call-site updates needed.

- [ ] **Step 11.3: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/api/org-units.ts
  git commit -m "fix(frontend): add typed return generics to orgUnitsApi role mutations

  assignRole and removeRole now declare their response generic
  explicitly so apiFetch returns Promise<{status: string}> instead
  of Promise<unknown>. Matches the convention used by every other
  method in the file."
  ```

---

## Task 12: Bulk delete via `<Dialog>` instead of `window.confirm` (B1.18)

**Goal:** Replace the synchronous, unstyled `window.confirm()` bulk-delete prompt with a `Dialog` from `components/px`.

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx`

- [ ] **Step 12.1: Add state + handlers for the dialog**

  In `frontend/app/app/(dashboard)/jobs/page.tsx`, near the top of the component where other `useState` calls live (look for `selected` state), add:
  ```ts
  const [confirmOpen, setConfirmOpen] = useState(false)
  ```

  Replace the existing `handleBulkDelete` body:
  ```ts
  function handleBulkDelete() {
    if (selected.size === 0) return
    const confirmed = window.confirm(
      `Delete ${selected.size} role${selected.size === 1 ? '' : 's'}? This cannot be undone.`,
    )
    if (!confirmed) return
    deleteMutation.mutate([...selected])
  }
  ```
  with:
  ```ts
  function handleBulkDelete() {
    if (selected.size === 0) return
    setConfirmOpen(true)
  }

  function confirmBulkDelete() {
    setConfirmOpen(false)
    deleteMutation.mutate([...selected])
  }
  ```

- [ ] **Step 12.2: Add the Dialog import**

  Near the top of `app/(dashboard)/jobs/page.tsx`, with the other component imports:
  ```ts
  import {
    Dialog,
    DialogContent,
    DialogTitle,
    DialogDescription,
    DialogFooter,
    Button,
  } from '@/components/px'
  ```
  (Add only the imports not already present.)

- [ ] **Step 12.3: Render the Dialog**

  At the bottom of the component's returned JSX, just before the closing `</div>` of the page wrapper, add:
  ```tsx
  <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
    <DialogContent>
      <DialogTitle>Delete {selected.size} role{selected.size === 1 ? '' : 's'}?</DialogTitle>
      <DialogDescription>
        This permanently removes the selected role{selected.size === 1 ? '' : 's'}. This cannot be undone.
      </DialogDescription>
      <DialogFooter>
        <Button
          variant="ghost"
          onClick={() => setConfirmOpen(false)}
          disabled={deleteMutation.isPending}
        >
          Cancel
        </Button>
        <Button
          variant="danger"
          onClick={confirmBulkDelete}
          disabled={deleteMutation.isPending}
        >
          {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
  ```

  If `Button` doesn't accept `variant="danger"`, fall back to:
  ```tsx
  <button
    type="button"
    className="px-btn danger"
    onClick={confirmBulkDelete}
    disabled={deleteMutation.isPending}
  >
    {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
  </button>
  ```
  (Inspect `components/px/Button.tsx` first to confirm the variant set.)

- [ ] **Step 12.4: Verify type-check, lint, build, tests**

  ```bash
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: PASS.

- [ ] **Step 12.5: Manual smoke**

  Start dev server, select 2+ jobs on `/jobs`, click bulk delete. Confirm: dialog opens (not `window.confirm`), Esc closes it, click backdrop closes, focus is trapped inside, "Delete" button disables during mutation.

- [ ] **Step 12.6: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/app/\(dashboard\)/jobs/page.tsx
  git commit -m "fix(frontend): bulk-delete prompt uses px Dialog

  Replaces window.confirm() (which blocks the main thread, is unstyled,
  and ignores design-system Esc/backdrop conventions) with the px Dialog
  primitive. Inherits focus trap, Esc, and backdrop-close for free."
  ```

---

## Task 13: Consolidate `/api/auth/me` callers behind `authApi.me()` (B1.19)

**Goal:** A single `MeResponse` type and a single API method for `/api/auth/me`. Remove the duplicate definitions in `org-units.ts` and `dashboard/layout.tsx`.

**Files:**
- Create: `frontend/app/lib/api/auth.ts`
- Create: `frontend/app/tests/lib/api/auth.test.ts`
- Modify: `frontend/app/lib/api/org-units.ts` — drop `MeData` and `me()`
- Modify: `frontend/app/app/(dashboard)/layout.tsx` — use `authApi.me()` instead of inline `fetch`
- Modify: any other caller of `orgUnitsApi.me` (grep first)

- [ ] **Step 13.1: Audit callers**

  Run, in `frontend/app`:
  ```bash
  grep -rn "orgUnitsApi\.me\|\['MeData'\]\|MeData\b\|/api/auth/me" \
    --include='*.ts' --include='*.tsx' app components lib
  ```
  Note every file that needs migration. Expected hits include `app/(dashboard)/layout.tsx`, `lib/api/org-units.ts`, and any place importing `MeData`.

- [ ] **Step 13.2: Write failing test**

  Create `frontend/app/tests/lib/api/auth.test.ts`:
  ```ts
  import { afterEach, describe, expect, it, vi } from 'vitest'

  import { authApi, type MeResponse } from '@/lib/api/auth'

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  describe('authApi.me', () => {
    it('parses the full backend MeResponse shape', async () => {
      const payload: MeResponse = {
        user_id: 'u1',
        email: 'user@example.com',
        full_name: 'Alice Example',
        tenant_id: 't1',
        client_name: 'Example Co',
        is_super_admin: true,
        onboarding_complete: true,
        has_org_units: true,
        workspace_mode: 'single_company',
        assignments: [
          {
            org_unit_id: 'o1',
            org_unit_name: 'Root',
            role_name: 'Recruiter',
            permissions: ['read'],
          },
        ],
      }
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      )
      const me = await authApi.me('tok')
      expect(me).toEqual(payload)
    })
  })
  ```

- [ ] **Step 13.3: Verify it fails (file doesn't exist yet)**

  ```bash
  npm run test -- tests/lib/api/auth.test.ts
  ```
  Expected: FAIL — module `@/lib/api/auth` cannot be resolved.

- [ ] **Step 13.4: Create the new module**

  Create `frontend/app/lib/api/auth.ts`:
  ```ts
  import { apiFetch } from './client'

  /**
   * Response shape of GET /api/auth/me.
   *
   * Mirrors backend `app/modules/auth/schemas.py::MeResponse` exactly.
   * Roles/permissions live in `assignments`; the JWT only carries
   * `is_super_admin` and `tenant_id`. Any conditional UI based on roles
   * MUST go through the per-request `assignments` data — never trust
   * a JWT claim alone.
   */
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

  export const authApi = {
    me: (
      token: string,
      opts?: { signal?: AbortSignal },
    ): Promise<MeResponse> =>
      apiFetch<MeResponse>('/api/auth/me', {
        token,
        signal: opts?.signal,
      }),
  }
  ```

- [ ] **Step 13.5: Verify the test passes**

  ```bash
  npm run test -- tests/lib/api/auth.test.ts
  ```
  Expected: PASS.

- [ ] **Step 13.6: Remove duplicate from `org-units.ts`**

  In `frontend/app/lib/api/org-units.ts`:

  Delete this interface block:
  ```ts
  export interface MeData {
    is_super_admin: boolean
    workspace_mode: string
    assignments: {
      org_unit_id: string
      org_unit_name: string
      role_name: string
      permissions: string[]
    }[]
  }
  ```

  Delete this method from `orgUnitsApi`:
  ```ts
  me: (token: string): Promise<MeData> =>
    apiFetch<MeData>('/api/auth/me', { token }),
  ```

- [ ] **Step 13.7: Migrate `app/(dashboard)/layout.tsx`**

  In `frontend/app/app/(dashboard)/layout.tsx`, replace the inline `getMe` function and `fetch` block:
  ```tsx
  // Before
  const getMe = cache(async (token: string, apiUrl: string) => {
    const res = await fetch(`${apiUrl}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<{
      is_super_admin: boolean;
      onboarding_complete: boolean;
      has_org_units: boolean;
      workspace_mode: string;
    }>;
  });
  ```
  with:
  ```tsx
  import { authApi, type MeResponse } from "@/lib/api/auth";

  const getMe = cache(async (token: string): Promise<MeResponse | null> => {
    try {
      return await authApi.me(token);
    } catch {
      return null;
    }
  });
  ```

  In the layout body, change:
  ```tsx
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
  const me = await getMe(session.access_token, apiUrl);
  ```
  to:
  ```tsx
  const me = await getMe(session.access_token);
  ```

  The `is_super_admin && !me.onboarding_complete` redirect logic stays unchanged — `MeResponse` includes both fields.

- [ ] **Step 13.8: Migrate any other `orgUnitsApi.me` callers**

  Use the audit list from Step 13.1. For each caller, replace:
  ```ts
  import { orgUnitsApi, type MeData } from '@/lib/api/org-units'
  // …
  const me = await orgUnitsApi.me(token)
  ```
  with:
  ```ts
  import { authApi, type MeResponse } from '@/lib/api/auth'
  // …
  const me = await authApi.me(token)
  ```
  And update any local types from `MeData` → `MeResponse`.

- [ ] **Step 13.9: Verify type-check, lint, build, tests**

  ```bash
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: PASS. If a caller was missed, type-check will catch it via the deleted `MeData` symbol.

- [ ] **Step 13.10: Manual smoke**

  Start dev server. Log in. Confirm the dashboard loads without the inline `getMe` having broken anything (sidebar shows correct user info, super-admin redirects work).

- [ ] **Step 13.11: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git add frontend/app/lib/api/auth.ts \
          frontend/app/lib/api/org-units.ts \
          frontend/app/app/\(dashboard\)/layout.tsx \
          frontend/app/tests/lib/api/auth.test.ts
  # Plus any other files touched in step 13.8
  git status   # eyeball the staged set before committing
  git commit -m "refactor(frontend): single home for /api/auth/me

  Introduces lib/api/auth.ts with authApi.me() and a MeResponse type
  that mirrors the backend schema 1:1 (10 fields including the four
  that the org-units MeData and the dashboard layout's inline type
  were both missing).

  - Drops MeData and orgUnitsApi.me() from lib/api/org-units.ts.
  - Replaces the inline fetch in app/(dashboard)/layout.tsx with
    authApi.me() (still wrapped in React.cache for per-request
    deduplication).

  Adds a vitest spec asserting the full schema parses correctly."
  ```

---

## Final Verification

- [ ] **Step F1: Run the full quality gate**

  ```bash
  cd /home/ishant/Projects/ProjectX/frontend/app
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: every command exits 0.

- [ ] **Step F2: Confirm git log**

  ```bash
  cd /home/ishant/Projects/ProjectX
  git log --oneline main..HEAD
  ```
  Expected: roughly 13 commits, each prefixed with `fix(frontend):`, `perf(frontend):`, `chore(frontend):`, or `refactor(frontend):`. Each commit message names the specific Batch 1 issue it addresses.

- [ ] **Step F3: Smoke the dashboard end-to-end**

  Start the dev server (`npm run dev`) and walk through:
  1. Log in. Dashboard loads, sidebar shows email.
  2. Open `/jobs` — bulk-select 2 jobs, hit delete, confirm via Dialog.
  3. Open `/jobs/new` — type into title field; React DevTools Profiler shows only fields reading `title` re-rendering.
  4. Open a job with an active `signals_extracting` status — DevTools Network shows ONE GET per status event, not two.
  5. Confirm signals on a job; navigate to `/pipeline` immediately — the job appears under `signals_confirmed` without waiting.
  6. Open the question-bank pane; verify SSE events flow.

- [ ] **Step F4: Mark Batch 1 done**

  Update the spec doc to reflect Batch 1 completion, e.g. add a one-line note at the top of section 5:
  > **Status:** Completed 2026-04-2X — see commits `<sha>..<sha>`.

  Commit:
  ```bash
  git add docs/superpowers/specs/2026-04-24-frontend-backend-cleanup-design.md
  git commit -m "docs(specs): mark cleanup batch 1 complete"
  ```

---

## Self-Review (run before handoff)

Cross-check: every B1.* item in section 5.1 of the spec maps to a task above:
- B1.1 → Task 1
- B1.2 → Tasks 2 + 3
- B1.3 → Task 2
- B1.4 → Task 4
- B1.5 → Task 5
- B1.6 → Task 6
- B1.7 → Task 7
- B1.8 → Task 7
- B1.9 → Task 8
- B1.10 → Task 9
- B1.11 → Task 10
- B1.12 → Task 10
- B1.13 → Task 10
- B1.14 → Task 1 (the new hook code has correct `[selectedStageId]` deps)
- B1.15 → Task 10
- B1.16 → Task 11
- B1.17 → Task 4
- B1.18 → Task 12
- B1.19 → Task 13

Coverage: complete.
