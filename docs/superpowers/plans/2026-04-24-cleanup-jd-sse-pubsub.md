# JD SSE → pub/sub Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `backend/nexus/app/modules/jd/sse.py` from primary DB-polling at 1.5s to the same pub/sub + polling-backstop pattern B2 established for question-bank. JD status/enrichment transitions deliver to SSE subscribers within ~100ms (fast path); polling at 5s remains as correctness backstop.

**Architecture:** Mirror of B2's SSE refactor, scoped to JD. `app/pubsub.py` already exists (B2). JD handlers use `BackgroundTasks.add_task(pubsub.publish, ...)` after DB commit; actors publish inline post-commit in their existing wrappers. SSE generator fans in `pubsub.subscribe("job:{id}")` (fast path) and the existing poll (backstop, bumped 1.5s → 5s). Single event name `jd.status_changed` with full `JobStatusEvent` payload — frontend already invalidates on any event arrival via `['jobs', jobId]` query key.

**Tech Stack:** FastAPI, Python 3.12, SQLAlchemy async, Dramatiq, `app/pubsub.py`, pytest.

---

## What this plan does NOT do (intentional scope cuts vs B2)

- **No `job_postings.updated_at` trigger.** The JD SSE backstop detects state via `(status, enrichment_status)` diff per connection, NOT via `max(updated_at)`. Migration 0017's approach was question-bank-specific (detecting question-text edits that don't touch status). JD doesn't have that shape.
- **No new event constants beyond `JD_STATUS_CHANGED`.** Frontend uses one query-key invalidation for both status and enrichment changes. Spec 6.9's correlation-ID discipline still applies.
- **No frontend code changes.** `use-job-status-stream.ts` already handles arbitrary event names — it invalidates `['jobs', jobId]` on every message regardless of event name. Post-B2, the only frontend touch was types; no hook rewiring needed.

---

## File structure

| File | Role | Status |
|---|---|---|
| `backend/nexus/app/pubsub.py` | Event constants | Modify (add `JD_STATUS_CHANGED`) |
| `backend/nexus/app/modules/jd/router.py` | JD handlers | Modify (5 handlers add BackgroundTasks publish) |
| `backend/nexus/app/modules/jd/service.py` | JD service | No change expected (handlers read published state from service return values) |
| `backend/nexus/app/modules/jd/actors.py` | Dramatiq actors | Modify (both actors publish inline post-commit in their wrappers) |
| `backend/nexus/app/modules/jd/sse.py` | SSE generator | Rewrite (fan-in pattern from question_bank/sse.py) |
| `backend/nexus/tests/test_jd_events.py` | NEW — pub/sub emission tests | Create |
| `backend/nexus/tests/test_jd_sse.py` | SSE tests — extend | Modify (add fast-path + backstop coverage) |

---

## Pre-flight

- [ ] **P.1:** Worktree at `/home/ishant/Projects/ProjectX/.worktrees/cleanup-jd-sse`, branch `cleanup/jd-sse-pubsub`. Baseline green:
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-jd-sse/backend/nexus
  docker compose up -d postgres redis
  docker compose run --rm nexus pytest --deselect tests/test_auth_service.py::TestVerifyAccessToken --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips -q 2>&1 | tail -3
  # Expect: 458 passed (or equivalent — 4 pre-existing failures on main are skipped)
  ```

---

## Task 1: Add `JD_STATUS_CHANGED` event constant to `app/pubsub.py`

**File:** `backend/nexus/app/pubsub.py`

- [ ] **Step 1.1:** Add the constant to the `Events` class:
  ```python
  class Events:
      """Canonical event-name strings. Compare against these, never raw strings."""
      BANK_QUESTION_UPDATED = "bank.question_updated"
      BANK_STATUS_CHANGED = "bank.status_changed"
      PIPELINE_GENERATION_COMPLETE = "pipeline.generation_complete"
      JD_STATUS_CHANGED = "jd.status_changed"  # <-- new
  ```

- [ ] **Step 1.2:** Sanity check:
  ```bash
  docker compose run --rm nexus python -c "from app.pubsub import Events; print(Events.JD_STATUS_CHANGED)"
  # Expect: jd.status_changed
  ```

- [ ] **Step 1.3:** Commit:
  ```bash
  git add backend/nexus/app/pubsub.py
  git commit -m "feat(pubsub): add JD_STATUS_CHANGED event constant"
  ```

---

## Task 2: `create_job` handler publishes initial state

**Files:**
- `backend/nexus/app/modules/jd/router.py` (around line 293-349, the `create_job` handler)
- `backend/nexus/tests/test_jd_events.py` (new file)

### Scene

`create_job` writes `status='signals_extracting'` via `service.create_job_posting`, then enqueues `_safe_dispatch_extraction` via BackgroundTasks. The initial state is committed by dependency cleanup BEFORE the BackgroundTasks run — so we can safely add a parallel BackgroundTask that publishes `jd.status_changed`.

### 2.1: Write the failing test

Create `backend/nexus/tests/test_jd_events.py`:
```python
"""Integration tests for pub/sub event emission from JD mutations.

Each test hits the real handler path but replaces pubsub.publish with
the `capture_publishes` fixture stub (defined in conftest.py, B2)."""
from __future__ import annotations

import pytest

from app import pubsub

pytestmark = pytest.mark.asyncio


async def test_create_job_publishes_initial_status(
    client, tenant_and_user, org_unit_with_company_profile, capture_publishes
):
    org_unit = org_unit_with_company_profile
    resp = await client.post(
        "/api/jobs",
        json={
            "title": "Senior Engineer",
            "description_raw": "We're hiring a senior engineer with 5+ years of Python experience.",
            "org_unit_id": str(org_unit.id),
        },
    )
    assert resp.status_code in (200, 201)
    job_id = resp.json()["id"]

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job_id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["job_id"] == job_id
    assert pub.payload["status"] == "signals_extracting"
    assert pub.correlation_id
```

Note: `org_unit_with_company_profile` fixture — check existing JD tests (`test_jd_router.py`, `test_jd_service_create.py`) for the fixture pattern. If it exists in a local conftest, lift to the shared one. If not, build a minimal one that:
- Creates an org unit with `company_profile` set (required for `create_job_posting` ancestry check)
- Assigns the test user to it with a role that has `jobs:create`
Reuse the pattern from `test_jd_router.py::test_create_job_returns_201` or equivalent.

### 2.2: Run — expect FAIL
```bash
docker compose run --rm nexus pytest tests/test_jd_events.py::test_create_job_publishes_initial_status -xvs 2>&1 | tail -25
# Expect: AssertionError — publish count is 0
```

### 2.3: Update the handler

In `backend/nexus/app/modules/jd/router.py`, find `create_job`. Current shape (approximate):
```python
@router.post("", status_code=201, response_model=JobPostingResponse)
async def create_job(
    request: Request,
    body: CreateJobRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: UserContext = Depends(require_job_creation),
):
    correlation_id = _get_correlation_id(request)
    job = await create_job_posting(
        db, tenant_id=ctx.tenant_id, org_unit_id=body.org_unit_id,
        title=body.title, description_raw=body.description_raw,
        created_by=ctx.user_id, correlation_id=correlation_id,
    )
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_id=job.id, tenant_id=ctx.tenant_id, correlation_id=correlation_id,
    )
    return job
```

Add a SECOND `background_tasks.add_task` call AFTER the existing one, to publish the initial state:
```python
from app import pubsub

# ... inside create_job, after the extraction dispatch:
background_tasks.add_task(
    pubsub.publish,
    pubsub.job_channel(job.id),
    pubsub.Events.JD_STATUS_CHANGED,
    {
        "job_id": str(job.id),
        "status": job.status,
        "enrichment_status": job.enrichment_status,
        "signal_snapshot_version": None,
        "error": None,
        "is_confirmed": False,
    },
    correlation_id=correlation_id,
)
```

### 2.4: Run — expect PASS
```bash
docker compose run --rm nexus pytest tests/test_jd_events.py::test_create_job_publishes_initial_status tests/test_jd_router.py -x 2>&1 | tail -10
```

### 2.5: Commit
```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_events.py backend/nexus/tests/conftest.py  # if fixture added
git commit -m "feat(jd): publish jd.status_changed on create_job

Handler uses BackgroundTasks to enqueue the publish after response
send. Initial status='signals_extracting' is emitted so SSE
subscribers connecting immediately after job creation see the
state without waiting for the next poll."
```

---

## Task 3: `confirm_signals` handler publishes

**Files:** `backend/nexus/app/modules/jd/router.py`, `backend/nexus/tests/test_jd_events.py`

### 3.1: Failing test

Append to `test_jd_events.py`:
```python
async def test_confirm_signals_publishes_status_changed(
    client, tenant_and_user, seed_job_signals_extracted, capture_publishes
):
    """Confirm signals transitions status signals_extracted → signals_confirmed.
    Fixture produces a job in signals_extracted state with at least one snapshot."""
    job = seed_job_signals_extracted
    resp = await client.post(f"/api/jobs/{job.id}/confirm-signals")
    assert resp.status_code == 200

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["status"] == "signals_confirmed"
    assert pub.payload["is_confirmed"] is True
```

### 3.2: Run — expect FAIL

### 3.3: Update handler

Find `confirm_signals` (~router.py:470-493). Add BackgroundTasks publish after the service call:
```python
@router.post("/{job_id}/confirm-signals", response_model=JobPostingResponse)
async def confirm_signals_endpoint(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    # ... existing deps
):
    correlation_id = _get_correlation_id(request)
    job = await confirm_signals(db, job_id=job_id, ..., correlation_id=correlation_id)

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job.id),
        pubsub.Events.JD_STATUS_CHANGED,
        {
            "job_id": str(job.id),
            "status": job.status,
            "enrichment_status": job.enrichment_status,
            "signal_snapshot_version": <latest snapshot version from service return>,
            "error": job.status_error,
            "is_confirmed": True,
        },
        correlation_id=correlation_id,
    )
    return job
```

**Note on `signal_snapshot_version`:** `get_job_status()` in service.py derives this from `SELECT MAX(version) FROM job_posting_signal_snapshots WHERE job_posting_id = ? AND confirmed_at IS NOT NULL` (or similar). Mirror that query when building the publish payload, OR have `confirm_signals()` return the version alongside the job object.

**Preferred:** have `confirm_signals()` return `(JobPosting, snapshot_version: int | None)`. Tighter contract than re-querying in the handler.

### 3.4: Run — expect PASS
### 3.5: Commit: `feat(jd): publish jd.status_changed on confirm_signals`

---

## Task 4: `save_signals` handler publishes

**Files:** `backend/nexus/app/modules/jd/router.py`, `backend/nexus/tests/test_jd_events.py`

**Why this is new behavior:** The current JD SSE only emits when `status`/`enrichment_status` change. `save_signals` may write a new snapshot without touching status (if the job was already `signals_extracted`). So tab-B subscribers currently DO NOT see signal edits made in tab A. After this task, they will.

### 4.1: Failing test

```python
async def test_save_signals_publishes_status_changed(
    client, tenant_and_user, seed_job_signals_extracted, capture_publishes
):
    """Writing a new snapshot (signal edit) publishes even when status doesn't change."""
    job = seed_job_signals_extracted
    resp = await client.post(
        f"/api/jobs/{job.id}/save-signals",
        json={
            "signals": [
                # minimal valid signal list — match existing test data shape
                {"slug": "skill-python", "label": "Python",
                 "source": "ai_extracted", "weight": 2,
                 # ... other required fields per SignalSchemaV2
                 }
            ]
        },
    )
    assert resp.status_code == 200

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    # Status may remain signals_extracted, but snapshot_version bumps.
    assert isinstance(pub.payload["signal_snapshot_version"], int)
    assert pub.payload["signal_snapshot_version"] >= 1
```

### 4.2: Run — expect FAIL

### 4.3: Update handler

Find `save_signals` endpoint (~router.py:445-467). Apply same BackgroundTasks pattern. Have `service.save_signals()` return the new snapshot version so the handler knows what to publish.

If `save_signals()` currently returns just the updated job, extend it to return `(JobPosting, new_version: int)` — one small change. Update call sites.

### 4.4-4.5: Run + commit: `feat(jd): publish jd.status_changed on save_signals`

---

## Task 5: `retry_extraction` handler publishes

**Files:** `backend/nexus/app/modules/jd/router.py`, `backend/nexus/tests/test_jd_events.py`

### 5.1: Failing test

```python
async def test_retry_extraction_publishes_status_changed(
    client, tenant_and_user, seed_job_signals_extraction_failed, capture_publishes
):
    """Retry transitions signals_extraction_failed → signals_extracting."""
    job = seed_job_signals_extraction_failed
    resp = await client.post(f"/api/jobs/{job.id}/retry")
    assert resp.status_code == 200

    # Two publishes expected: the status change + the extraction dispatch is itself not a publish.
    # Actually only the state-transition publish is expected here.
    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["status"] == "signals_extracting"
    assert pub.payload["error"] is None  # status_error is cleared on retry
```

### 5.2-5.5: Same pattern — wire BackgroundTasks publish after `retry_failed_extraction()`. Commit: `feat(jd): publish jd.status_changed on retry_extraction`

Note: The handler already does `background_tasks.add_task(_safe_dispatch_extraction, ...)`. Add ours alongside. Also note B1 flagged `jobsApi.delete`/role mutations for return-type cleanup — `retry_failed_extraction` similarly; if current signature is `Promise<{status: string}>`, consider tightening as a sidebar. Out of this task's scope.

---

## Task 6: `enrich_job` handler publishes

**Files:** `backend/nexus/app/modules/jd/router.py`, `backend/nexus/tests/test_jd_events.py`

### 6.1: Failing test

```python
async def test_enrich_job_publishes_status_changed(
    client, tenant_and_user, seed_job_signals_extracted, capture_publishes
):
    """Trigger re-enrichment: sets enrichment_status='streaming'."""
    job = seed_job_signals_extracted
    resp = await client.post(f"/api/jobs/{job.id}/enrich")
    assert resp.status_code == 200

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["enrichment_status"] == "streaming"
```

### 6.2-6.5: Wire after `trigger_reenrichment()` return. Commit: `feat(jd): publish jd.status_changed on enrich_job`

---

## Task 7: `extract_and_enhance_jd` actor publishes post-commit

**Files:** `backend/nexus/app/modules/jd/actors.py`, `backend/nexus/tests/test_jd_events.py`

### Scene

Recon showed the actor's wrapper (line 265-302) does:
```python
async with get_bypass_session() as session:
    # ... set tenant context ...
    # ... call _run_extraction() ...
    # ... commit on success OR on final retry (retries_so_far >= 2) ...
```

The publish goes AFTER the commit. Depending on the control flow:
- Success path → commit → publish with final status (`signals_extracted`)
- Final-retry failure → commit → publish with `signals_extraction_failed`
- Non-final retry failure → no commit → no publish (Dramatiq will retry; a later attempt will publish)

**Single publish per actor invocation** is the right granularity. Intermediate transitions (setting `enrichment_status='completed'` mid-run) don't need their own publishes — the final emit carries all necessary state.

### 7.1: Failing test

```python
async def test_extract_actor_publishes_on_success(
    tenant_and_user, seed_job_signals_extracting, capture_publishes, monkeypatch
):
    """The extract actor publishes jd.status_changed after its commit."""
    from app.modules.jd import actors

    # Stub the LLM call — reuse the mock pattern from test_jd_actor.py.
    async def fake_extract(*args, **kwargs):
        # Return a minimal valid ExtractionOutput — mirror existing test's helper.
        pass
    monkeypatch.setattr("app.modules.jd.actors._call_llm_extract", fake_extract)

    job = seed_job_signals_extracting  # job with status='signals_extracting'
    # Invoke the actor directly (sync wrapper is `actor.fn`; real coroutine is `.fn.__wrapped__`).
    await actors.extract_and_enhance_jd.fn.__wrapped__(
        job_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id="test-corr-extract",
    )

    assert len(capture_publishes) == 1
    pub = capture_publishes[0]
    assert pub.event == pubsub.Events.JD_STATUS_CHANGED
    assert pub.payload["status"] == "signals_extracted"
    assert pub.correlation_id == "test-corr-extract"
```

Match the existing actor test harness in `test_jd_actor.py` — same LLM stub pattern, same fixture style.

### 7.2-7.3: Wire the actor

In `backend/nexus/app/modules/jd/actors.py`, find the `extract_and_enhance_jd` actor wrapper. Post-commit publish:
```python
@dramatiq.actor(queue_name="jd_extraction", max_retries=2)
@observe(...)
async def extract_and_enhance_jd(
    job_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    async with get_bypass_session() as session:
        # ... existing tenant-set + _run_extraction() logic ...
        # ... commit on success OR final retry ...
        committed_state: dict | None = None  # capture final state for publish
        try:
            result = await _run_extraction(session, job_id, correlation_id)
            await session.commit()
            # Re-query to get final snapshot version + status.
            committed_state = await _fetch_publish_state(session, job_id)
        except ...:
            # ... existing failure handling ...
            if retries_so_far >= 2:
                await session.commit()
                committed_state = await _fetch_publish_state(session, job_id)
            else:
                raise  # Dramatiq retry

    # Publish AFTER the session.commit() above, AFTER the `async with` context ends
    # (no DB access needed; publish is transport-only).
    if committed_state is not None:
        await pubsub.publish(
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            committed_state,
            correlation_id=correlation_id,
        )
```

**Helper `_fetch_publish_state(session, job_id)`** — reuses `get_job_status()` from `jd/service.py` and converts its return to a plain dict. Defines the one shape for publish payloads across the JD module (handler + actor both use it).

Actually — better: make `get_job_status()` itself usable from actors (it already accepts a `session`). Just call it + serialize to dict.

### 7.4-7.5: Run + commit: `feat(jd/actors): extract_and_enhance_jd publishes post-commit`

---

## Task 8: `reenrich_jd` actor publishes post-commit

**Files:** `backend/nexus/app/modules/jd/actors.py`, `backend/nexus/tests/test_jd_events.py`

### Scene

Same wrapper pattern as Task 7. Actor mutates `enrichment_status` instead of `status`; commit is similarly at the end of the wrapper.

### 8.1-8.5: Follow Task 7 pattern. Test asserts `pub.payload["enrichment_status"] in ("completed", "failed")`. Commit: `feat(jd/actors): reenrich_jd publishes post-commit`

---

## Task 9: SSE generator refactor — fan-in fast path + backstop

**Files:** `backend/nexus/app/modules/jd/sse.py`, `backend/nexus/tests/test_jd_sse.py`

### Scene

Current `jd/sse.py` (~85 lines) is a single async generator polling at 1.5s. We rewrite it into the same fan-in shape as `question_bank/sse.py`:

- Fast path: `pubsub.subscribe(pubsub.job_channel(job_id))` — forwards envelopes directly
- Backstop: existing DB poll, interval bumped to 5s, output as Envelopes into the shared queue
- Two tasks feed one `asyncio.Queue`; generator yields the union as SSE frames
- `try/finally` cancels both tasks on generator close (client disconnect)

Preserve these current behaviors:
- De-dupe on `(status, enrichment_status)` — tracked per connection
- Terminal-state termination — `status in TERMINAL_STATES AND enrichment_status != 'streaming'` → return
- `request.is_disconnected()` check — still needed as a router-level wrapper

### 9.1: Failing test

Extend `backend/nexus/tests/test_jd_sse.py` (or create anew if the current tests don't fit the new generator signature):

```python
async def test_jd_sse_forwards_pubsub_events(seed_job_signals_extracting, monkeypatch):
    """An envelope published to job:{id} is forwarded by the SSE generator."""
    from app import pubsub
    from app.modules.jd import sse

    job = seed_job_signals_extracting
    received: list[str] = []

    async def consume():
        async for frame in sse.job_status_event_generator(
            tenant_id=str(job.tenant_id),
            job_id=job.id,
            request=MockRequest(),  # is_disconnected returns False
        ):
            received.append(frame)
            if "signals_extracted" in frame:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let subscribe connect

    await pubsub.publish(
        pubsub.job_channel(job.id),
        pubsub.Events.JD_STATUS_CHANGED,
        {"job_id": str(job.id), "status": "signals_extracted",
         "enrichment_status": "idle", "signal_snapshot_version": 1,
         "error": None, "is_confirmed": False},
        correlation_id="test-jd-sse",
    )

    try:
        await asyncio.wait_for(consumer, timeout=2.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail("SSE did not forward pub/sub event within 2s")

    assert any("signals_extracted" in f for f in received)
```

### 9.2: Run — expect FAIL (generator doesn't subscribe yet)

### 9.3: Rewrite `sse.py`

Structure:
```python
"""SSE for JD status + enrichment transitions.

Fan-in of two sources into one emit stream:
  1. Fast path: pubsub.subscribe("job:{id}") — typical latency <100ms.
     Driven by JD handlers' BackgroundTasks + actor post-commit publishes.
  2. Backstop: DB poll at 5s — correctness insurance for pub/sub misses.
     Same detection logic as before (status + enrichment_status diff).

Both paths push Envelopes into a shared asyncio.Queue; the generator
yields the union as SSE frames. No server-side dedup — client-side
query invalidation is idempotent.

Termination:
  - request.is_disconnected() → return
  - Terminal status AND enrichment not streaming → return

Mirrors the question_bank/sse.py pattern (B2)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

import orjson
import structlog
from fastapi import Request

from app import pubsub
from app.database import get_tenant_session
from app.modules.jd.service import get_job_status

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS: float = 5.0  # up from 1.5s; pub/sub is the fast path now
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed", "signals_confirmed"}
)


async def job_status_event_generator(
    tenant_id: str,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until terminal state, client disconnect, or cancellation."""
    import uuid as uuidlib
    safe_tenant_id = str(uuidlib.UUID(str(tenant_id)))

    emit_queue: asyncio.Queue[pubsub.Envelope] = asyncio.Queue(maxsize=100)
    last_status: str | None = None
    last_enrichment_status: str | None = None
    terminal_reached = False

    async def fast_path() -> None:
        try:
            async for envelope in pubsub.subscribe(pubsub.job_channel(job_id)):
                # Filter to JD events only; ignore bank.* events on the same channel.
                if envelope.event != pubsub.Events.JD_STATUS_CHANGED:
                    continue
                await emit_queue.put(envelope)
        except asyncio.CancelledError:
            raise

    async def backstop() -> None:
        try:
            while True:
                async with get_tenant_session(safe_tenant_id) as db:
                    event = await get_job_status(db, job_id)
                if event is None:
                    return
                # Convert to Envelope with a cycle correlation_id so forensic
                # traces still connect to the poll iteration.
                import uuid as uuidlib2
                cycle_correlation = f"jd-backstop-{uuidlib2.uuid4()}"
                envelope = pubsub.Envelope(
                    event=pubsub.Events.JD_STATUS_CHANGED,
                    payload=orjson.loads(event.model_dump_json()),
                    correlation_id=cycle_correlation,
                    emitted_at=datetime.now(timezone.utc).isoformat(),
                )
                await emit_queue.put(envelope)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    fast_task = asyncio.create_task(fast_path())
    backstop_task = asyncio.create_task(backstop())

    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                env = await asyncio.wait_for(emit_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # check disconnect, try again

            payload = env.payload
            status = payload.get("status")
            enrichment_status = payload.get("enrichment_status")
            # De-dupe on (status, enrichment_status) per connection.
            if status == last_status and enrichment_status == last_enrichment_status:
                continue
            last_status = status
            last_enrichment_status = enrichment_status

            yield {
                "event": "status",
                "data": orjson.dumps(payload).decode("utf-8"),
            }

            if status in TERMINAL_STATES and enrichment_status != "streaming":
                return
    finally:
        fast_task.cancel()
        backstop_task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(fast_task, backstop_task, return_exceptions=True)
```

**Key invariants:**
- Single SSE event name (`"status"`) preserved — client doesn't need to change.
- Payload shape preserved — mirrors `JobStatusEvent.model_dump()`.
- De-dupe on (status, enrichment_status) — current behavior preserved.
- Terminal termination — same triggers as before.
- Disconnect detection — polled at ≤1s intervals via the `wait_for` timeout.

### 9.4: Run — expect PASS
### 9.5: Commit: `feat(jd/sse): pub/sub fast path + 5s polling backstop`

---

## Task 10: E2E integration test

**File:** `backend/nexus/tests/test_jd_sse.py`

### 10.1: Add E2E test

```python
async def test_e2e_confirm_signals_to_sse(
    client, tenant_and_user, seed_job_signals_extracted
):
    """HTTP POST /confirm-signals → BackgroundTasks publishes → SSE forwards."""
    job = seed_job_signals_extracted
    received: list[str] = []

    async def consume():
        async for frame in sse.job_status_event_generator(
            tenant_id=str(job.tenant_id),
            job_id=job.id,
            request=MockRequest(),
        ):
            received.append(frame)
            if "signals_confirmed" in frame:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let subscribe connect

    resp = await client.post(f"/api/jobs/{job.id}/confirm-signals")
    assert resp.status_code == 200

    try:
        await asyncio.wait_for(consumer, timeout=3.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail("SSE did not receive confirm-signals event end-to-end")

    assert any("signals_confirmed" in f for f in received)
```

### 10.2: Run — expect PASS (fast path <500ms)
### 10.3: Commit: `test(jd/sse): end-to-end confirm → publish → subscribe → client`

---

## Task 11: Full gauntlet

### 11.1: Backend full suite

```bash
cd backend/nexus
docker compose run --rm nexus pytest \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_valid_token_returns_payload \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_projectx_admin_token \
  --deselect tests/test_auth_service.py::TestVerifyAccessToken::test_empty_custom_claims_returns_defaults \
  --deselect tests/test_session_schemas.py::test_pre_check_response_round_trips \
  2>&1 | tail -5
```
Expected: all green (~468-475 passed — B2's 462 + new JD event tests).

### 11.2: Alembic check

```bash
docker compose run --rm nexus alembic check
```
Expected: no pending revisions (this plan adds none).

### 11.3: Frontend gauntlet (no code changes expected)

```bash
cd frontend/app
npx tsc --noEmit; echo "tsc=$?"
npm run lint 2>&1 | tail -3
npm run test 2>&1 | tail -3
npm run build 2>&1 | tail -5
```
Expected: all green.

### 11.4: Manual smoke (optional — matches B2 smoke plan)
- Create a new job → SSE shows `signals_extracting` within ~100ms.
- Wait for extraction → SSE shows `signals_extracted`.
- Edit a signal → SSE refreshes with new snapshot version (was silent before; now emits).
- Confirm signals → SSE shows `signals_confirmed` and closes cleanly.
- `docker compose stop redis` → edit a signal → backstop delivers within ~5s.

### 11.5: Push the branch (optional)
```bash
git push -u origin cleanup/jd-sse-pubsub
```

---

## Appendix: what this plan intentionally DOES do beyond current behavior

- **save_signals events now reach SSE subscribers.** Was silent before (no status change → no SSE emit). Now tab B sees tab A's signal edits within ~100ms.
- **enrich_job trigger is immediately visible.** Was a 1.5s poll delay; now <100ms.
- **JD events share `job:{job_id}` channel with bank events.** SSE generator filters to JD event name. One channel, two consumers — no scaling impact.

## Appendix: what this plan intentionally does NOT do

- **No `job_postings.updated_at` trigger/migration.** JD SSE doesn't need it (status-based detection works).
- **No frontend changes.** `use-job-status-stream.ts` already invalidates on any event arrival. No new handler needed.
- **No consolidation of the two SSE hooks.** `use-job-status-stream` and `use-questions-status-stream` remain separate. Consolidating would be a larger refactor.

**End of plan.**
