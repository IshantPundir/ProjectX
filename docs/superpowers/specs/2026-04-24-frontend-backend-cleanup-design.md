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
| D2b | Event delivery model (revised during B2 planning 2026-04-24) | **Pub/sub (Redis) + polling backstop at 5s.** Fast path: `app/core/pubsub.py` → Redis → SSE subscribe. Correctness path: existing DB poll, interval bumped 500ms → 5s. Both feed the same SSE stream; client dedupes via query invalidation. Mutation sites publish post-commit (FastAPI `BackgroundTasks` in handlers; inline post-commit in Dramatiq actors). Rationale: enterprise-grade real-time from day 1; Redis is already a critical dependency (Dramatiq broker) so adds zero new availability domain; polling is correctness insurance so Redis outages degrade gracefully to 5s latency rather than silent data loss. |
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

## 6. Batch 2 — Schema + Event Delivery Alignment

> **Status:** ✅ Completed 2026-04-24 on branch `cleanup/batch-2-schema-events` (worktree `.worktrees/cleanup-batch-2/`). 22 commits `fb716a4..a678d81`, merged as `4f68638` (no-ff). Final gates: alembic at head 0017, 462/462 backend tests (+ 4 pre-existing failures unrelated to B2), tsc clean, lint 0 errors, 54/54 vitest, `next build` clean. Clustered subagent implementation + combined spec/quality reviews per task + final whole-batch review by opus reviewer (recommendation: APPROVE FOR MERGE).
>
> **Deferred follow-ups** (surfaced during smoke verification, out of scope for B2):
> - Question-editor UI is not wired on `/jobs/[jobId]/questions`: "+ Add question" button has no `onClick` handler; inline text editing for individual questions is missing. Backend endpoints (`create_question`, `update_question`, `delete_question`, `reorder_questions`) are fully functional and tested — just no UI surface yet. Blocks two-tab browser smoke of T11/T12/T13/T14. (T15 confirm + T16 regenerate DO have working UI paths.)
> - Pipeline-tab stage add/remove requires a manual refresh in other tabs. B2 wired pub/sub only for the question-bank SSE stream; the pipeline has no SSE. Extending the pub/sub + backstop pattern to the pipeline module is the natural next step — `app/pubsub.py` is designed to absorb it without changes.
> - Three B1 deferred items from section 5 also remain open (`useJobStatusStream.isStreaming` init, 204-response return types, five `window.confirm` callsites). Still out-of-scope; fine to fold into a later cleanup batch.

### 6.1 Scope

| ID | Issue | File(s) |
|---|---|---|
| B2.0 | No centralized pub/sub boundary; SSE is poll-only at 500ms; question mutations can't notify subscribers without a query round-trip | `backend/nexus/app/core/pubsub.py` (new), `backend/nexus/app/core/health.py` (extend) |
| B2.1 | `JobPostingWithSnapshot` detail response missing `org_unit_name`, `created_by_email`, `updated_by_email`, `signal_count`, `needs_review_count` (inline enrichment logic exists only in `list_jobs`) | `backend/nexus/app/modules/jd/schemas.py:168-220`, `backend/nexus/app/modules/jd/router.py:364-485` (list_jobs + get_job), `backend/nexus/app/modules/jd/service.py` (extract helper), frontend `lib/api/jobs.ts:59-104` |
| B2.2 | `POST /api/jobs/{id}/retry` returns a `JobPostingSummary` with all enrichment fields zero/null | `backend/nexus/app/modules/jd/router.py:506-531` |
| B2.3 | Backend `OrgUnitResponse.company_profile_completed_at` not in frontend `OrgUnit` type | `frontend/app/lib/api/org-units.ts:20-38` |
| B2.4 | SSE `bank.question_updated` event listened-for but never emitted; poll-only detection doesn't work reliably for UPDATE-in-place | `backend/nexus/app/modules/question_bank/sse.py`, `backend/nexus/app/modules/question_bank/service.py` (update/delete/reorder/confirm_bank), `backend/nexus/app/modules/question_bank/router.py:473` (create_question), `backend/nexus/app/modules/question_bank/actors.py:766` (regenerate_question) |
| B2.5 | `stage_questions.updated_at` has `server_default=NOW()` but no `onupdate` — column never refreshes on UPDATE, defeating any poll-based change detection | `backend/nexus/app/models.py:474-476`, new Alembic migration |

### 6.2 Architecture — pub/sub + polling backstop

**Design principle:** Build enterprise-grade event delivery from day 1. Pub/sub is the fast path; polling is the correctness backstop. Either alone is fragile — together, the system is self-degrading: if Redis drops an event, the poll catches it within 5s; if the poll misses (it won't with correct `updated_at`), pub/sub delivers in milliseconds.

#### B2.0: `app/core/pubsub.py` — the module boundary

This is the architecturally load-bearing piece. Whatever transport runs underneath (Redis today, possibly SNS/Cloud Pub/Sub at enterprise) stays invisible to callers.

**Public API:**
```python
async def publish(channel: str, event: str, payload: dict, *, correlation_id: str) -> None:
    """Best-effort. Never raises. Logs structlog warning + metric on failure.
    Fire this AFTER the DB transaction has committed."""

async def subscribe(*channels: str) -> AsyncIterator[Envelope]:
    """Yields envelopes. Auto-reconnects with exponential backoff on drop.
    Structured-logs each reconnect. Cancellable via standard asyncio cancellation."""
```

**Envelope shape (JSON-serialized with `orjson`):**
```python
class Envelope(TypedDict):
    event: str            # e.g. "bank.question_updated"
    payload: dict         # event-specific fields
    correlation_id: str   # end-to-end trace id
    emitted_at: str       # ISO-8601 with UTC offset
```

**Implementation notes:**
- Uses `redis.asyncio` with a **separate `Redis` client instance** from Dramatiq's broker. Pub/sub subscribe connections block while listening; sharing the Dramatiq pool would starve task workers.
- URL from `settings.REDIS_URL`. Connection pool sized for expected concurrent SSE connections (init at 10, max 100).
- `publish()` catches `redis.exceptions.RedisError` (any subclass), logs at WARN, emits a structlog event with `metric_name="pubsub.publish.failed"`. Never raises.
- `subscribe()` handles disconnects by retrying with exponential backoff (1s → 2s → 4s → 8s → 16s → 30s cap). Structured-logs every reconnect attempt (WARN) and success (INFO). Does NOT re-deliver events missed during the disconnect window — the polling backstop covers that gap.
- Cancellation: `subscribe()` is an async generator; standard `asyncio.CancelledError` propagates correctly, closes the pubsub connection on cleanup.
- `publish()` uses `asyncio.shield()` internally on the Redis send so a cancelled caller doesn't abort a half-sent publish.

**Health probe (B2.0 extends `app/core/health.py`):**
- Liveness: publish a `pubsub.health_check` event on channel `health:pubsub`, subscribe with 2s timeout, assert round-trip. Failure marks service unhealthy; Docker Compose / load balancer can recycle the pod.
- This is already how Dramatiq broker health is checked — same module.

#### B2.4: `bank.question_updated` event emission

**Critical ordering invariant:** Publish ALWAYS happens AFTER the DB commit. Publishing inside a service method fires before commit (because `get_tenant_db` commits on dependency cleanup, post-handler-return). Two enforcement patterns:

| Site type | Pattern |
|---|---|
| FastAPI handlers | `BackgroundTasks.add_task(pubsub.publish, ...)` — FastAPI runs background tasks after the response is sent, which is AFTER dependency cleanup (after commit) |
| Dramatiq actors | Call `await pubsub.publish(...)` inline after the `async with session.begin():` block exits |

Services **do not call publish directly.** They return an event descriptor (or the handler/actor constructs one from the service's return value), and the caller is responsible for enqueuing the publish.

**Mutation sites:**

| Site | File:line | Pattern |
|---|---|---|
| Create question | `router.create_question:473` | Handler adds BackgroundTask |
| Update question | `router` → `service.update_question:520` | Handler adds BackgroundTask |
| Delete question | `router` → `service.delete_question:595` | Handler adds BackgroundTask |
| Reorder questions | `router` → `service.reorder_questions:627` | Handler adds BackgroundTask |
| Confirm bank | `router` → `service.confirm_bank:675` | Handler adds BackgroundTask (emits `bank.status_changed`) |
| Regenerate question | `actors.regenerate_question:766` | Inline post-commit publish |

**Channel:** `job:{job_id}` — one subscription per SSE connection covers all banks of the job (banks span multiple stages within one job). Simpler than per-bank channels and scales the same.

**Event payload:**
```json
{
  "event": "bank.question_updated",
  "payload": {
    "job_id": "<uuid>",
    "bank_id": "<uuid>",
    "stage_id": "<uuid>",
    "question_id": "<uuid|null>",
    "mutation": "create|update|delete|reorder|regenerate"
  },
  "correlation_id": "<uuid>",
  "emitted_at": "2026-04-24T12:34:56.789+00:00"
}
```

`correlation_id` is read from the existing request-scoped helper (`jd/router.py:49` `_get_correlation_id` — mirrored in question_bank router) for handler sites; actors already receive it as an explicit parameter.

`confirm_bank` emits `bank.status_changed` with payload `{job_id, bank_id, stage_id, new_status}` on the same channel. This is additive to the poll's existing detection — both fire, client dedupes implicitly via query invalidation.

#### B2.4: SSE generator refactor (`question_bank/sse.py`)

The generator now feeds from two sources into one emit stream:

```python
async def sse_generator(job_id: UUID) -> AsyncIterator[str]:
    emit_queue: asyncio.Queue[Envelope] = asyncio.Queue()

    async def fast_path():
        async for envelope in pubsub.subscribe(f"job:{job_id}"):
            await emit_queue.put(envelope)

    async def backstop():
        async for envelope in poll_loop(job_id, interval=5):
            await emit_queue.put(envelope)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(fast_path())
        tg.create_task(backstop())
        while True:
            envelope = await emit_queue.get()
            yield _format(envelope)
```

- **Backstop interval: 5s** (up from 500ms). Fast path handles the low-latency case; the backstop is now correctness insurance, not the primary delivery mechanism. 5s is imperceptible degradation during Redis outages.
- **Poll detection** needs B2.5 (`stage_questions.updated_at` onupdate) to detect UPDATEs. Without it, poll can only catch INSERT/DELETE (via `bank.question_count`) and status changes. With it, poll watches `(bank.status, bank.question_count, max(stage_questions.updated_at) per bank)` — any change triggers `bank.question_updated`.
- **No server-side dedupe.** Both paths may emit the same logical event. Client-side: `use-questions-status-stream` calls `queryClient.invalidateQueries(...)` on each event; TanStack Query dedupes in-flight refetches. Net effect: two events → one refetch.
- **Cancellation:** `TaskGroup` guarantees both tasks are cancelled cleanly when the client disconnects. `pubsub.subscribe` honors cancellation; `poll_loop` checks the cancellation scope between iterations.

#### B2.5: `stage_questions.updated_at` auto-refresh

Two complementary fixes:

1. **ORM-level:** Add `onupdate=sql_text("NOW()")` to `StageQuestion.updated_at` column in `app/models.py:474-476`. Catches all ORM-driven UPDATEs.
2. **DB-level trigger (defense-in-depth):** Migration adds a `BEFORE UPDATE` trigger on `stage_questions` that sets `NEW.updated_at = NOW()`. Catches raw SQL paths and future codebase paths that bypass the ORM.

```sql
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER stage_questions_touch_updated_at
    BEFORE UPDATE ON stage_questions
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();
```

The trigger function is named generically (`touch_updated_at`) so other tables can adopt the same pattern later without a rename.

### 6.3 Backend changes in B2 — scoped

- **B2.0:** New `app/core/pubsub.py` (~150 LOC + tests). New health probe. Separate Redis client wired into `app/lifespan.py` (or equivalent startup hook).
- **B2.1:** Extract `enrich_job_summaries(jobs, db) -> list[JobPostingSummary]` into `jd/service.py`. Call from `list_jobs`, `get_job`, and `retry`. `_job_to_summary` keeps its current signature but the enrichment dict lookups move into the shared helper. `JobPostingWithSnapshot` already extends `JobPostingSummary`, so the `get_job` handler just needs to run the enrichment step before serializing.
- **B2.2:** `retry` handler calls `enrich_job_summaries([job], db)[0]` before returning.
- **B2.4:** Handlers take `BackgroundTasks: BackgroundTasks` as a parameter, enqueue publish after `await service.X(...)`. Actor adds inline publish post-commit. Services remain publish-free (single-responsibility boundary).
- **B2.5:** Migration + ORM column update.

### 6.4 Frontend changes in B2

- `lib/api/jobs.ts:59-104`: align `JobPostingSummary` / `JobPostingWithSnapshot` types with backend. No new unknown-narrowing after this.
- `lib/api/org-units.ts:20-38`: add `company_profile_completed_at?: string | null` to `OrgUnit`.
- `lib/hooks/use-questions-status-stream.ts:99-111`: no changes needed — the existing handler invalidates `['banks', jobId]` and `['bank', jobId, stageId]` on both `bank.status_changed` and `bank.question_updated`. Already correct.

### 6.5 Migration / data

- **Schema changes:** One Alembic migration for the `updated_at` trigger on `stage_questions` (B2.5). Additive. Zero data impact.
- **Response shape changes:** B2.1/B2.2/B2.3 are response-only Pydantic additions — no migration. Existing clients ignore new fields.
- **Redis:** `REDIS_URL` already required for Dramatiq. Pub/sub reuses the same instance, different client pool. No new infra for MVP.

### 6.6 Observability

- **Structured log events (no new metrics library):**
  - `metric_name="pubsub.publish.failed"` — incremented on each publish failure. Alert threshold: > 1% of publishes over 5min rolling window.
  - `metric_name="pubsub.subscribe.reconnected"` — incremented on each reconnect attempt. Alert on sustained >3/min per connection.
  - `metric_name="pubsub.publish.ok"` — baseline counter for ratio calculations.
- **Correlation ID end-to-end:** handler/actor → service → DB → publish → subscribe → SSE emit → client. Every log line on the path carries the same correlation_id. Searchable in Langfuse/structlog output.

### 6.7 Acceptance criteria for B2

- `npm run type-check`, `npm run lint`, `npm run test`, `npm run build` all pass on the frontend.
- `pytest` passes on the backend (including new tests below).
- **New backend tests:**
  - `pubsub.publish()` swallows `RedisError`, emits `pubsub.publish.failed` structlog event, returns without raising.
  - `pubsub.subscribe()` reconnects when the underlying connection is forcibly closed mid-iteration; resumes yielding envelopes.
  - Integration (with docker-compose Redis): each of the 5 mutation sites (`create_question` handler, `update_question` handler, `delete_question` handler, `reorder_questions` handler, `regenerate_question` actor) publishes a `bank.question_updated` envelope with matching `job_id`, `correlation_id`, and `mutation` values. Subscriber assertion within a 1s timeout.
  - `confirm_bank` handler publishes `bank.status_changed` (in addition to the poll-based emission remaining intact).
  - `enrich_job_summaries` helper called from `list_jobs`, `get_job`, and `retry` produces identical field sets for the same job.
  - Migration test: after upgrade, an UPDATE on `stage_questions` bumps `updated_at`.
- **Manual smoke tests:**
  - Open question-bank UI in two tabs, edit a question's text in tab A → tab B refreshes within ~100ms (fast path).
  - Kill Redis mid-session (docker-compose stop redis), edit a question → tab B refreshes within ~5s (backstop).
  - Restart Redis → subscribe reconnects, structured log shows reconnect event, fast path resumes.
  - Retry a failed job on `/jobs` → response includes populated `signal_count`, `needs_review_count`, `org_unit_name`, `created_by_email`, `updated_by_email` (not nulls/zeros).
  - Open a job detail page → same enrichment fields present.

### 6.8 Human review required

Per CLAUDE.md "module boundaries must be correct from day 1," these must have explicit human review before merge:
- `app/core/pubsub.py` — the module boundary itself
- The `BackgroundTasks` + actor post-commit publishing pattern (sets the convention for future events)
- The `stage_questions` migration and trigger

### 6.9 Risk register (B2-specific)

| Risk | Mitigation |
|---|---|
| Redis pub/sub drops events under network blip | Polling backstop at 5s is independent correctness path; no event loss possible in steady state |
| Pub/sub client starves Dramatiq broker | Separate `Redis` client instance (different pool); verified under load in tests |
| `BackgroundTasks` + response-first ordering: if server crashes between response send and task execution, event is lost | Backstop poll catches it within 5s; accepted tradeoff for simplicity |
| Correlation ID missing in an actor code path | Explicit param in all actors today; code review enforces; test asserts presence in payload |
| `onupdate` only fires for ORM UPDATEs | Postgres trigger catches raw-SQL paths too; migration is defense-in-depth |
| SSE TaskGroup deadlock on cancellation | `TaskGroup` cancels child tasks on exit; `asyncio.Queue` is cancellable; test covers client-disconnect cleanup |

---

## 7. Batch 3 — Auth Hardening + Invite Flow

> **Status:** ✅ Completed 2026-04-25 on branch `cleanup/batch-3-auth-hardening` (worktree `.worktrees/cleanup-batch-3/`). 19 feature commits `306dd73..c879c36` + 1 plan commit, merged as `3fd277f` (no-ff). Final gates: backend 490 passed (4 pre-existing deselects), tsc 0 errors, lint 0 errors, 63/63 vitest, `next build` clean. Per-cluster subagent implementation + spec/quality review per cluster; whole-batch human review per CLAUDE.md "Human Review Required" list. Manual browser smokes confirmed: invite flow end-to-end, deactivation → `AuthProvider.delete_user` (test user could not log in again — proves provider boundary hit Supabase Admin API), single-use invite enforcement.
>
> **Design deviation from spec:**
> - Spec's `app/modules/auth/admin_client.py` became a package `app/modules/auth/admin/` with `base.py` (protocol + types), `supabase.py` (concrete impl), `_factory.py` (singleton), `__init__.py` (public API). Maps cleanly to user's vendor-lock-in rule — adding Cognito/Keycloak is a new file + `settings.auth_provider = "cognito"`.
> - `lib/auth/tokens.ts` received a comment-only change documenting the SessionGuard invariant; hot-path `getSession()` read preserved. Architectural fix for B3.1 lives in SessionGuard (Task 13), not the token helper.
> - `AcceptInviteResponse` shape trimmed to `{access_token, refresh_token, expires_in, redirect_to}` — legacy `user_id`/`tenant_id`/`root_unit_id` fields dropped (frontend never used them).
>
> **Critical race bug caught + fixed in code review:** Initial handler compensation would have deleted a legitimately-created auth user on the 409 race-claim path. Fix: `auth_user_created_here: bool` flag threaded through `_safe_delete_auth_user`; compensation skipped with a `compensation_skipped` warning log when the auth user was reused via `find_user_by_email`. Regression-tested in `test_accept_invite.py::test_accept_invite_already_existing_auth_user_updates_password` (explicit `delete_user NOT called` assertion).
>
> **Deferred follow-ups** (out of scope for B3):
> - `frontend/app/app/(auth)/login/page.tsx` still uses `supabase.auth.signInWithPassword` directly — migrated during B4's form/state sweep.
> - `frontend/app/proxy.ts` + `frontend/app/app/(dashboard)/layout.tsx` use server-side `supabase.auth.getSession()` in the SSR flow — intentionally out of scope (B3 migrated only the two client-page callsites from B3.2).
> - `frontend/app/package.json` has no `type-check` script (CLAUDE.md documents one); `npx tsc --noEmit` works directly. Low-priority cleanup for a future batch.
> - Three B1 deferred follow-ups (sections 5's completion note) remain open. Unchanged.

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

> **Status:** ✅ Completed 2026-04-25 on branch `cleanup/batch-4-form-migration` (worktree `.worktrees/cleanup-batch-4/`). 23 commits `2850424..6fd14d0`, merged as `a237956` (no-ff). Final gates: backend 498 passed (4 pre-existing deselects), tsc 0 errors, lint 0 errors, 87/87 vitest, `next build` clean. Per-cluster subagent implementation + spec/quality review per cluster (split for C2 backend auth, C8 team page, C9 org-units index, Task 20 CompanyProfileDetail, Task 22 MembersSection); whole-batch final review by Opus reviewer (verdict: APPROVE WITH CONCERNS — 8 non-blocking polish follow-ups, no security/correctness blockers). Manual browser smokes confirmed: login (incl. backend-owned authApi.login), invite, onboarding, team-invite, org-unit-create, unit-detail Save changes, MembersSection assign + Dialog-based remove.
>
> **Bug caught during smoke (fixed pre-merge):**
> - Nested-form regression in the `[unitId]` detail tree — Tasks 20-21 wrapped each detail subcomponent's editable region in `<form onSubmit>` while also rendering MembersSection (which has its own `<form>`). Nested forms broke the assign-role submit. Fix at commit `6fd14d0`: unwrapped the outer form on all four detail subcomponents (CompanyProfileDetail, DivisionDetail, RegionDetail, TeamDetail), bound Save buttons to `onClick={form.handleSubmit(onSubmit)}`. Surfaced because `members-section-dialog.test.tsx` rendered MembersSection in isolation — composition test gap.
>
> **Deferred follow-ups** (surfaced during whole-batch review, out of scope for B4):
> - `applyApiErrorToForm` doesn't map nested 422 `loc: [body, metadata, *]` paths — falls through to `root` instead of the nested form field. None of the B4 forms hit this in practice; affects future deeply-nested forms.
> - `CompanyProfileDetail` uses 8 `form.watch()` calls causing full-component re-renders on each keystroke. Perf concern, not correctness. Use `useWatch` in leaf subcomponents (e.g. `<CharCount>`, `<CopilotSignalsCard>`).
> - Backend `/api/auth/login` deactivated-user branch returns tokens minted by the AuthProvider before the 403. Tokens are not installed client-side, but they exist in Supabase's token log. Hardening: call `provider.sign_out(access_token)` in the deactivated branch.
> - `LoginRequest.password` has no length bound. Could add `Field(min_length=1, max_length=128)` for crisper 422 surface.
> - `MembersSection` reimplements `useTeamMembers` inline (different staleTime). Could just call the hook.
> - `settings/team/page.tsx` uses a bespoke `ConfirmDialog` modal; harmonize with `px/Dialog` like MembersSection does.
> - Onboarding page is the odd one out — doesn't use `applyApiErrorToForm` for its mutation error path. Acceptable because step 2 delegates to `CompanyProfileForm` which owns its own RHF instance, but worth tracking.
> - Test gaps: MembersSection cancel-path test, org-units index `client_account` flow test, AND a composition-test that catches nested-form regressions (lesson from the bug above).
> - Three B1 deferred items (`useJobStatusStream.isStreaming` init, 204-response return types, the pipeline-templates `confirm()` callsite) remain open. Pre-existing; not affected by B4.

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

### 8.5 Locked decisions — brainstorming 2026-04-25

This subsection supersedes any earlier ambiguity in 8.1–8.4. Decisions here are binding for the B4 implementation plan.

| ID | Decision | Lock |
|---|---|---|
| D4.1 | Login flow ownership | **Backend-owned.** New `POST /api/auth/login` endpoint using the B3 `AuthProvider` abstraction. Frontend page calls `authApi.login()` → `setSession()` → navigate. Driven by the user's vendor-lock-in rule — future AWS Cognito migration must be a provider swap, not a code rewrite. |
| D4.2 | 422 error shape on the frontend | **`ApiValidationError extends ApiError` subclass** with required `fieldErrors: FastApiValidationError[]`. Thrown by `apiFetch` on 422 only. `ApiError` unchanged for all other statuses. Enterprise pattern: discriminated error hierarchy over optional-property soup. |
| D4.3 | Schema colocation | **Separate `schema.ts` next to each page.** `app/.../<page>/schema.ts` exports the Zod schema and the inferred form-values type. Schemas do not live inline in `page.tsx`. |
| D4.4 | Hook file organization | **One hook per file**, matching the existing `lib/hooks/` convention (35+ existing files). ~17 new files across team, org-units, and auth domains. |
| D4.5 | B4.6 scope | **Full org-unit detail tree.** B4.6 migrates `[unitId]/page.tsx` plus `CompanyProfileDetail`, `DivisionDetail`, `RegionDetail`, `TeamDetail`, and `MembersSection`. Folds in MembersSection's `confirm()` → `Dialog` conversion (one of the five B1 deferred callsites). The page's subcomponents carry most of the raw `useState` + `fetch` — migrating only the router file would be technically-complete but user-visibly unchanged. |

#### 8.5.1 Backend login endpoint (D4.1)

**Route:** `POST /api/auth/login` (public — added to `_PUBLIC_PREFIXES` in `app/middleware/auth.py`).

**Schemas** (`app/modules/auth/schemas.py`):
```python
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    redirect_to: str  # "/" or "/onboarding" — backend decides from users.onboarding_complete
```

**Error contract:**
| Status | Condition | Detail message |
|---|---|---|
| 401 | `InvalidCredentialsError` from `AuthProvider.sign_in_with_password` | `"Invalid email or password."` — no user enumeration |
| 401 | `UserNotFoundError` | Same generic message as 401 above |
| 403 | `tenant_id` claim missing from returned access_token | `"This account does not have access to the client dashboard."` |
| 403 | `users.is_active == false` | `"This account has been deactivated."` |
| 422 | Pydantic validation (empty email, malformed, missing password) | FastAPI default shape |

**Handler flow:**
1. Resolve `auth_provider = get_auth_provider()`.
2. Call `await auth_provider.sign_in_with_password(email, password)` → `SessionTokens`.
3. Decode the access_token (via existing `verify_access_token` — which already validates signature, audience, issuer, algorithm).
4. If `tenant_id` is missing → sign-out-side effect not needed (the provider returned tokens but we don't install them), return 403.
5. Look up the `users` row by `user_id`. If `is_active == false` → return 403.
6. Compute `redirect_to` — `/onboarding` when `users.is_super_admin and not users.onboarding_complete`, else `/`.
7. Return `LoginResponse`.

**Frontend** (`lib/api/auth.ts` extension):
```typescript
login: (
  body: { email: string; password: string },
  opts?: { signal?: AbortSignal },
): Promise<LoginResponse> =>
  apiFetch<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(body),
    signal: opts?.signal,
  }),
```

**Login page** (`app/(auth)/login/page.tsx`):
- `useForm<LoginFormValues>({ resolver: zodResolver(loginSchema) })`.
- On submit: `authApi.login({email, password})` → `supabase.auth.setSession(...)` → `router.push(safeRedirect)` (`redirect_to` must pass the existing `startsWith('/') && !startsWith('//')` open-redirect guard).
- `applyApiErrorToForm` for 422; `form.setError('root', ...)` for 401/403.

**Tests:**
- Backend: `tests/test_auth_login.py` — happy path, invalid password (401 generic), missing tenant_id (403), deactivated user (403), malformed request body (422), `AuthProvider` wired via dependency override.
- Frontend: `tests/auth/login-page.test.tsx` — valid submit calls `authApi.login` + `setSession`; 422 maps to `setError`; 401 surfaces as root error via toast + form-level message; zero references to `supabase.auth.signInWithPassword` in the page.

#### 8.5.2 ApiValidationError (D4.2)

`lib/api/client.ts` (modify):
```typescript
export interface FastApiValidationError {
  loc: (string | number)[]
  msg: string
  type: string
}

export class ApiValidationError extends ApiError {
  constructor(message: string, public fieldErrors: FastApiValidationError[]) {
    super(message, 422)
    this.name = 'ApiValidationError'
  }
}
```

`apiFetch` throws `ApiValidationError` when `res.status === 422 && Array.isArray(body.detail)`; throws `ApiError` on every other non-OK status. `instanceof ApiError` still matches `ApiValidationError` (subclass), so all 40+ existing narrowings continue working.

`lib/api/errors.ts` (new):
```typescript
export function applyApiErrorToForm<T extends FieldValues>(
  err: unknown,
  form: UseFormReturn<T>,
  opts?: { fallbackFieldKey?: Path<T> },
): boolean
```
- Narrows on `err instanceof ApiValidationError`. Returns `false` for any other error shape.
- For each entry in `fieldErrors`, strips a leading `"body"` segment from `loc` (FastAPI prepends it), then walks the remainder to produce a dotted path.
- If the dotted path matches a form field, calls `form.setError(path, { message: entry.msg, type: 'server' })`.
- If no field matches, falls back to `opts.fallbackFieldKey` (set on that field); if no fallback, sets `root` error.
- Returns `true` if at least one `setError` call fired — caller suppresses the toast.

`tests/api/apply-api-error-to-form.test.ts` (new) covers: non-ApiValidationError returns false; `loc: ["body", "email"]` maps to `email`; nested `loc: ["body", "company_profile", "about"]` maps to `company_profile.about`; unknown field falls back to `fallbackFieldKey`; unknown field with no fallback sets `root`.

#### 8.5.3 File layout summary

```
frontend/app/
├── lib/
│   ├── api/
│   │   ├── client.ts              (modify — add ApiValidationError + FastApiValidationError)
│   │   ├── errors.ts              (new — applyApiErrorToForm)
│   │   ├── auth.ts                (extend — add login, completeOnboarding, setWorkspaceMode)
│   │   ├── team.ts                (new — teamApi namespace)
│   │   └── org-units.ts           (extend — add delete, removeMember)
│   └── hooks/                     (~17 new use-* files, one per hook)
├── app/
│   ├── (auth)/{login,invite}/{page.tsx, schema.ts}
│   ├── onboarding/{page.tsx, schema.ts}
│   └── (dashboard)/settings/
│       ├── team/{page.tsx, schema.ts}
│       └── org-units/
│           ├── {page.tsx, schema.ts}
│           └── [unitId]/
│               ├── {page.tsx, schema.ts}
│               ├── CompanyProfileDetail.tsx   (migrate)
│               ├── DivisionDetail.tsx         (migrate)
│               ├── RegionDetail.tsx           (migrate)
│               ├── TeamDetail.tsx             (migrate)
│               └── MembersSection.tsx         (migrate + confirm → Dialog)
```

Backend:
```
backend/nexus/app/
├── modules/auth/
│   ├── schemas.py                 (modify — add LoginRequest, LoginResponse)
│   └── router.py                  (modify — add POST /api/auth/login)
├── middleware/auth.py             (modify — add /api/auth/login to _PUBLIC_PREFIXES)
└── tests/test_auth_login.py       (new)
```

#### 8.5.4 Cluster breakdown

Ordered to preserve the dependency chain (shared utilities → API namespaces → hooks → pages):

| # | Cluster | Scope |
|---|---|---|
| C1 | Shared utilities | `ApiValidationError` in `client.ts` + `applyApiErrorToForm` in `errors.ts` + tests. Unblocks every page migration below. |
| C2 | Backend login endpoint | `LoginRequest`/`LoginResponse` schemas + router handler + public-prefix middleware + test suite. |
| C3 | API namespaces | Extend `lib/api/auth.ts` (login, completeOnboarding, setWorkspaceMode); create `lib/api/team.ts`; extend `lib/api/org-units.ts` (delete, removeMember). |
| C4 | Hooks — auth + onboarding | `useLogin`, `useCompleteOnboarding`, `useSetWorkspaceMode`. |
| C5 | Hooks — team | `useTeamMembers`, `useInviteTeamMember`, `useResendInvite`, `useRevokeInvite`, `useDeactivateUser`. |
| C6 | Hooks — org-units | `useOrgUnits`, `useOrgUnit`, `useCreateOrgUnit`, `useUpdateOrgUnit`, `useDeleteOrgUnit`, `useOrgUnitMembers`, `useRoles`, `useAssignRole`, `useRemoveRole`. |
| C7 | Small pages | B4.1 login, B4.2 invite, B4.3 onboarding (each page + its `schema.ts` + migrated data layer via hooks from C4). |
| C8 | Settings — team | B4.4 team page (RHF+Zod invite form, TanStack Query members list, dialog-based deactivate/revoke). |
| C9 | Settings — org-units index | B4.5 `org-units/page.tsx` (the 721-LOC page). |
| C10 | Settings — org-units detail tree | B4.6 `[unitId]/page.tsx` + CompanyProfileDetail + DivisionDetail + RegionDetail + TeamDetail + MembersSection. Folds in the `confirm()` → `Dialog` conversion in MembersSection. |

C2 and C3 can run in parallel after C1. C4–C6 can parallelize after C3. C7–C10 are sequential (incremental complexity; C10 is the heaviest).

Subagent review cadence (per `feedback_subagent_review_cadence`): combined spec+quality review for C4/C5/C6/C7 (small and mechanical); split review (spec + quality as two passes) for C1 (shared utility touching 40+ consumers), C2 (backend auth endpoint), C8, C9, and C10.

#### 8.5.5 Human review required — updated

Section 7.7 flagged B3 for CLAUDE.md human review on auth changes. The B4 prompt initially said "no CLAUDE.md human-review gate" — that assumed option (b) (Supabase SDK wrapper). Decision D4.1 reopens the gate: **C2 (backend login endpoint) is a change to `app/modules/auth/`**, which the backend CLAUDE.md explicitly lists under "Human Review Required For." The whole-batch final reviewer remains the gate of record; C2 specifically must surface to the human reviewer with the auth-module changes highlighted.

Other B4 clusters touch only frontend files and do not trigger the CLAUDE.md gate. Standard subagent review applies.

#### 8.5.6 Submission pattern by route group

The `(auth)` route group and `/onboarding` have no `QueryClient` provider — TanStack Query is mounted only inside `DashboardProviders` via the dashboard layout. A 401 on the login page is a form-level error ("bad password"), not a session-expired error, so it must NOT flow through the global `handleAuthError` redirect logic that lives in `DashboardProviders`. Mixing them would redirect a user who mistyped their password to `/login` with a "Session expired" toast, which is wrong.

Resolution — two distinct patterns:

| Route group | Mutation pattern | Query pattern |
|---|---|---|
| `(auth)` — login, invite | Plain RHF `handleSubmit` + `await authApi.*` inside `onSubmit`. No `useMutation`. | N/A — auth pages don't fetch server state. Invite's `verify-invite` GET stays as the existing local `useEffect` or moves to a thin fetch-on-mount helper. |
| `/onboarding` | Plain RHF `handleSubmit` + `await`. No `useMutation`. | Existing `fetchRootUnit` useEffect stays (scope-limited). |
| `(dashboard)/settings/*` | `useMutation` with TanStack Query (already provider-wrapped). Global 401 handler is correct here — a 401 inside the dashboard genuinely means an expired session. | `useQuery`. |

`applyApiErrorToForm` is error-shape-agnostic — it works identically whether the caller used `useMutation` or plain `await`. The login page catches the thrown `ApiError`/`ApiValidationError`, passes it to `applyApiErrorToForm`, and if that returns `false`, manually calls `form.setError('root', { message })` with the user-facing error string (for 401/403, use the backend's `detail` verbatim).

This keeps the global 401 redirect logic scoped to the dashboard where it belongs, and avoids accidentally wiring up a second `QueryClient` in the (auth) tree just to satisfy a "use TanStack Query everywhere" rule that was never the spec's intent.

#### 8.5.7 Acceptance criteria — updated

Section 8.4 stands, with these additions:
- Backend: `tests/test_auth_login.py` passes; existing deselect list from B3 applies unchanged.
- Frontend: zero references to `supabase.auth.signInWithPassword` under `app/` and `components/` (grep-verified).
- Frontend: zero `confirm(` calls under `app/(dashboard)/settings/org-units/[unitId]/` (grep-verified — MembersSection is now Dialog-based).
- Browser smoke per page: invalid submit produces field-level error under the correct input; valid submit produces golden-path redirect/navigation.

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
- B2's SSE event emission includes `correlation_id` in the payload (mint at request, thread through handler → service → BackgroundTask → `pubsub.publish` → Redis → `pubsub.subscribe` → SSE consumer → browser). A question edit traces end-to-end through a single search term across structlog/Langfuse.
- B2 introduces three new metric-tagged structlog events: `pubsub.publish.ok`, `pubsub.publish.failed`, `pubsub.subscribe.reconnected`. These become the observability primitives every future pub/sub use site emits — the module boundary in `app/core/pubsub.py` is the enforcement point.

### 10.3 Backwards compatibility

- Early dev. The user accepted data deletion. Breaking changes are fine.
- Specifically: `complete-invite` endpoint deletion (B3), `JobPostingWithSnapshot` shape extension (B2 — additive, but old clients without B1.19 type updates will silently ignore the new fields).

### 10.4 Risk register

| Risk | Mitigation |
|---|---|
| B3 auth changes break login | Land B1+B2 first; B3 lands as its own PR with manual smoke + human review |
| B5 component decomposition breaks tests | Move tests with their components; vitest is fast — run after each extraction |
| B4 form migrations introduce regression | Each page tested manually before commit; existing test suite is the safety net |
| B2 pub/sub or polling introduces regression | See batch-local risk register (section 6.9) for the full pub/sub + backstop matrix |

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

Each plan is executed in sequence. Per-batch acceptance criteria (sections 5.3, 6.7, 7.6, 8.4, 9.5) are the gate to move to the next batch.
