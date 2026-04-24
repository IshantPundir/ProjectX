# Cleanup Batch 2 — Schema + Event Delivery Alignment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land section 6 of the 2026-04-24 cleanup spec — align `get_job` / `retry` / `OrgUnit` response shapes with their Pydantic schemas, introduce a centralized `app/pubsub.py` module boundary, and make `bank.question_updated` deliver reliably via pub/sub with a polling backstop.

**Architecture:** Pub/sub + polling backstop. New `app/pubsub.py` is the module boundary (Redis today, swappable tomorrow). Mutation sites publish post-commit — FastAPI `BackgroundTasks` in handlers, inline after `session.begin()` exits in Dramatiq actors. The SSE generator feeds from pub/sub (fast, ~50ms) AND the existing DB poll (backstop, 5s, bumped from 500ms). Both feed the same emit stream; the client dedupes via TanStack Query invalidation. `stage_questions.updated_at` gets an `onupdate` + Postgres trigger so the poll backstop can actually see UPDATEs.

**Tech Stack:** FastAPI, Python 3.12, SQLAlchemy async 2.x, Alembic, `redis.asyncio`, Dramatiq, pytest, structlog. Frontend: Next.js 16 (App Router), React 19, TypeScript strict, TanStack Query v5.

---

## Design deviation from spec

The spec's section 6 says the pub/sub module lives at `app/core/pubsub.py`. The codebase has no `app/core/` subdirectory — infrastructure modules live at `app/` (e.g., `app/brokers.py`, `app/database.py`). This plan places the module at **`backend/nexus/app/pubsub.py`** to match the existing convention. Update the spec after merge if a future `app/core/` boundary emerges.

---

## File Structure

| File | Role | Status |
|---|---|---|
| `backend/nexus/app/pubsub.py` | NEW — pub/sub module boundary, Redis client, publish/subscribe API, envelope type, event name constants | Create |
| `backend/nexus/app/main.py` | FastAPI app, `/health` endpoint, `lifespan()` startup/shutdown | Modify (wire pubsub startup/shutdown + health probe) |
| `backend/nexus/app/models.py` | SQLAlchemy ORM models | Modify (`StageQuestion.updated_at` gets `onupdate`) |
| `backend/nexus/migrations/versions/0017_stage_questions_updated_at_trigger.py` | NEW — Postgres `BEFORE UPDATE` trigger on `stage_questions` | Create |
| `backend/nexus/app/modules/jd/service.py` | JD service layer | Modify (extract `enrich_job_summaries` helper) |
| `backend/nexus/app/modules/jd/router.py` | JD API handlers | Modify (`list_jobs` uses helper; `get_job` + `retry` apply enrichment) |
| `backend/nexus/app/modules/question_bank/router.py` | Question-bank API handlers | Modify (5 handlers enqueue `BackgroundTasks.add_task(pubsub.publish, ...)` after service call) |
| `backend/nexus/app/modules/question_bank/service.py` | Question-bank service layer | Modify (return event descriptors so handlers know what to publish) |
| `backend/nexus/app/modules/question_bank/actors.py` | Dramatiq actors | Modify (`regenerate_question` publishes inline post-commit) |
| `backend/nexus/app/modules/question_bank/sse.py` | Question-bank SSE generator | Modify (subscribe to `job:{job_id}` via `pubsub.subscribe`; poll backstop interval 500ms → 5s; TaskGroup fan-in) |
| `frontend/app/lib/api/jobs.ts` | Job API types | Modify (align `JobPostingSummary` / `JobPostingWithSnapshot` with backend) |
| `frontend/app/lib/api/org-units.ts` | Org-unit API types | Modify (`OrgUnit.company_profile_completed_at`) |
| `backend/nexus/tests/test_pubsub.py` | NEW — unit tests for publish/subscribe, failure semantics, reconnect | Create |
| `backend/nexus/tests/test_migration_0017.py` | NEW — trigger sets `updated_at` on UPDATE (raw SQL path) | Create |
| `backend/nexus/tests/test_question_banks_events.py` | NEW — integration tests that each mutation site publishes the right event | Create |
| `backend/nexus/tests/test_jd_router.py` | Existing — extend for `get_job` + `retry` enrichment parity | Modify |

---

## Pre-flight

- [ ] **P.1:** Confirm working tree is on branch `cleanup/batch-2-schema-events` (worktree: `.worktrees/cleanup-batch-2`).
  ```bash
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2 status
  git -C /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2 branch --show-current
  ```
  Expected: clean tree, branch = `cleanup/batch-2-schema-events`.

- [ ] **P.2:** Confirm `redis.asyncio` is already in backend dependencies (via `redis>=5`).
  ```bash
  grep -A1 '^redis\b\|"redis' /home/ishant/Projects/ProjectX/backend/nexus/pyproject.toml
  ```
  Expected: `redis = "^5.*"` (or similar). If missing, add it — `dramatiq[redis]` pulls in `redis` but `redis.asyncio` is in the same package.

- [ ] **P.3:** Start backend services to verify baseline green.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose up -d postgres redis
  docker compose run --rm nexus pytest -x
  ```
  Expected: all tests pass. If anything fails before we change code, stop and investigate — B1 left main green.

- [ ] **P.4:** Frontend baseline — type-check, lint, tests, build.
  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app
  npm install
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: all green.

---

## Phase 1 — Infrastructure (Tasks 1–5)

## Task 1: Alembic migration — `stage_questions.updated_at` trigger (B2.5)

**Goal:** Add a Postgres `BEFORE UPDATE` trigger that sets `updated_at = NOW()` on every UPDATE to `stage_questions`. Defense-in-depth — works regardless of whether the UPDATE comes from ORM, raw SQL, or psql. The ORM-level `onupdate` (Task 2) catches the happy path; this catches everything else.

**Files:**
- Create: `backend/nexus/migrations/versions/0017_stage_questions_updated_at_trigger.py`
- Test: `backend/nexus/tests/test_migration_0017.py`

- [ ] **Step 1.1: Create the failing test**

  Create `backend/nexus/tests/test_migration_0017.py`:
  ```python
  """Migration 0017: stage_questions.updated_at auto-refresh trigger."""
  from __future__ import annotations

  import asyncio
  import uuid
  from datetime import timedelta

  import pytest
  from sqlalchemy import text

  pytestmark = pytest.mark.asyncio


  async def test_trigger_bumps_updated_at_on_raw_sql_update(bypass_db_session):
      """Raw SQL UPDATE (bypassing ORM onupdate) must still bump updated_at."""
      db = bypass_db_session
      tenant_id = uuid.uuid4()
      bank_id = uuid.uuid4()
      question_id = uuid.uuid4()
      # Seed a bank + question directly. Assumes existing fixtures for
      # tenant/pipeline/stage/bank — see conftest.py seed helpers.
      # For this test we use the raw INSERT path to avoid coupling to ORM.
      await db.execute(
          text("""
              INSERT INTO clients (id, name) VALUES (:tid, 'test')
          """),
          {"tid": tenant_id},
      )
      # (pipeline/stage/bank seeding omitted — use the same seed helper
      #  used by test_question_banks_service.py::test_get_bank_questions
      #  or build a minimal helper in fixtures/)
      # Seed a stage_questions row directly:
      await db.execute(
          text("""
              INSERT INTO stage_questions (
                  id, tenant_id, bank_id, position, source, text,
                  signal_values, estimated_minutes, is_mandatory,
                  follow_ups, positive_evidence, red_flags, rubric,
                  evaluation_hint, edited_by_recruiter
              ) VALUES (
                  :qid, :tid, :bid, 1, 'generated', 'original',
                  ARRAY['s1']::text[], 2.5, false,
                  '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
                  'hint', false
              )
          """),
          {"qid": question_id, "tid": tenant_id, "bid": bank_id},
      )
      before = (await db.execute(
          text("SELECT updated_at FROM stage_questions WHERE id = :qid"),
          {"qid": question_id},
      )).scalar_one()

      await asyncio.sleep(0.01)  # guarantee NOW() ticks forward

      # Raw SQL UPDATE — bypasses ORM entirely.
      await db.execute(
          text("UPDATE stage_questions SET text = 'edited' WHERE id = :qid"),
          {"qid": question_id},
      )
      after = (await db.execute(
          text("SELECT updated_at FROM stage_questions WHERE id = :qid"),
          {"qid": question_id},
      )).scalar_one()

      assert after > before, "trigger did not bump updated_at on raw UPDATE"
      assert (after - before) < timedelta(seconds=5), "updated_at jumped too far"
  ```

  Note: this test depends on `bypass_db_session` fixture and foreign-key seed rows. The existing `conftest.py` pattern (see `test_question_banks_service.py`) has seed helpers — reuse them. Adjust the seed section of this test to call the existing helper if it exists; fall back to the minimal INSERT shown above if not.

- [ ] **Step 1.2: Verify the test fails**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose run --rm nexus pytest tests/test_migration_0017.py -xvs
  ```
  Expected: FAIL — either "trigger did not bump updated_at" OR schema mismatch if the trigger hasn't been created yet. That's the failure we want.

- [ ] **Step 1.3: Create the migration**

  Create `backend/nexus/migrations/versions/0017_stage_questions_updated_at_trigger.py`:
  ```python
  """stage_questions.updated_at auto-refresh trigger

  Adds a BEFORE UPDATE trigger on stage_questions that sets NEW.updated_at
  to NOW() on every UPDATE. Defense-in-depth against raw-SQL UPDATE paths
  that bypass SQLAlchemy's onupdate mechanism.

  The trigger function is named generically so other tables can reuse it:
    CREATE TRIGGER <table>_touch_updated_at
      BEFORE UPDATE ON <table>
      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

  Revision ID: 0017_stage_questions_updated_at_trigger
  Revises: 0016_stage_v5_participants
  Create Date: 2026-04-24
  """
  from __future__ import annotations

  from alembic import op

  revision = "0017_stage_questions_updated_at_trigger"
  down_revision = "0016_stage_v5_participants"
  branch_labels = None
  depends_on = None


  def upgrade() -> None:
      # Generic helper function — reusable by any table with an updated_at column.
      op.execute("""
          CREATE OR REPLACE FUNCTION touch_updated_at()
          RETURNS TRIGGER AS $$
          BEGIN
              NEW.updated_at = NOW();
              RETURN NEW;
          END;
          $$ LANGUAGE plpgsql;
      """)

      op.execute("""
          CREATE TRIGGER stage_questions_touch_updated_at
              BEFORE UPDATE ON stage_questions
              FOR EACH ROW
              EXECUTE FUNCTION touch_updated_at();
      """)


  def downgrade() -> None:
      op.execute("DROP TRIGGER IF EXISTS stage_questions_touch_updated_at ON stage_questions;")
      # Keep the function — it may be in use by other tables added later.
      # If that's not true, uncomment:
      # op.execute("DROP FUNCTION IF EXISTS touch_updated_at();")
  ```

- [ ] **Step 1.4: Apply the migration and run the test**

  ```bash
  docker compose run --rm nexus alembic upgrade head
  docker compose run --rm nexus pytest tests/test_migration_0017.py -xvs
  ```
  Expected: migration applies cleanly (no errors), test passes.

- [ ] **Step 1.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2
  git add backend/nexus/migrations/versions/0017_stage_questions_updated_at_trigger.py \
          backend/nexus/tests/test_migration_0017.py
  git commit -m "feat(db): auto-refresh updated_at on stage_questions via trigger

  Adds a BEFORE UPDATE trigger so updated_at bumps on every UPDATE,
  including raw-SQL paths that bypass SQLAlchemy onupdate. Needed by
  the B2 SSE polling backstop to detect question edits.

  The trigger function (touch_updated_at) is generic and reusable."
  ```

---

## Task 2: ORM `onupdate` on `StageQuestion.updated_at` (B2.5)

**Goal:** Add `onupdate=sql_text("NOW()")` to the ORM column definition. With Task 1's trigger, this is redundant at the DB level — but it keeps SQLAlchemy's view of the world consistent (session.flush() will see the change without a RETURNING round-trip).

**Files:**
- Modify: `backend/nexus/app/models.py:474-476`

- [ ] **Step 2.1: Update the column definition**

  In `backend/nexus/app/models.py`, find the `StageQuestion` class and update the `updated_at` column:
  ```python
  # BEFORE (around line 474-476):
  updated_at: Mapped[datetime] = mapped_column(
      DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
  )

  # AFTER:
  updated_at: Mapped[datetime] = mapped_column(
      DateTime(timezone=True),
      nullable=False,
      server_default=sql_text("NOW()"),
      onupdate=sql_text("NOW()"),
  )
  ```

- [ ] **Step 2.2: Run existing tests to verify no regression**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_service.py tests/test_question_banks_router.py tests/test_migration_0017.py -xvs
  ```
  Expected: all green.

- [ ] **Step 2.3: Commit**

  ```bash
  git add backend/nexus/app/models.py
  git commit -m "feat(models): onupdate NOW() on StageQuestion.updated_at

  SQLAlchemy onupdate catches ORM UPDATEs; the migration 0017 trigger
  catches raw-SQL paths. Defense-in-depth for the B2 SSE polling backstop."
  ```

---

## Task 3: `app/pubsub.py` — module boundary (B2.0, part 1 of 3)

**Goal:** Create the new `app/pubsub.py` module with the public API, envelope type, event-name constants, and a stub implementation. Tests in later tasks drive the real implementation.

**Files:**
- Create: `backend/nexus/app/pubsub.py`
- Create: `backend/nexus/tests/test_pubsub.py`

- [ ] **Step 3.1: Create the module with types and API surface**

  Create `backend/nexus/app/pubsub.py`:
  ```python
  """Centralized pub/sub for domain events.

  This is the module boundary: callers depend on `publish()` and
  `subscribe()` — the Redis transport is an implementation detail.
  Swap-out to SNS / Cloud Pub/Sub at enterprise is a change inside
  this file, not a change at call sites.

  Design invariants:
    - publish() is fire-and-forget and NEVER raises. Failures are logged
      and counted via a structlog event; the calling flow continues.
    - publish() must be called AFTER the DB transaction has committed.
      In FastAPI handlers, use `BackgroundTasks.add_task(publish, ...)`
      — FastAPI runs background tasks after the response is sent, which
      is after dependency-cleanup commits the transaction. In Dramatiq
      actors, call publish() inline after the `async with session.begin():`
      context exits.
    - subscribe() auto-reconnects with exponential backoff. Events missed
      during a disconnect are NOT re-delivered — callers must have a
      correctness backstop (e.g. DB polling) if event loss is unacceptable.
    - A separate Redis client instance is used from Dramatiq's broker to
      avoid starving task workers (pub/sub subscribe blocks).
  """
  from __future__ import annotations

  import asyncio
  from contextlib import suppress
  from dataclasses import dataclass
  from datetime import datetime, timezone
  from typing import AsyncIterator

  import orjson
  import redis.asyncio as aioredis
  import structlog
  from redis.exceptions import RedisError

  from app.config import settings

  logger = structlog.get_logger(__name__)


  # --- Event name constants -------------------------------------------------

  class Events:
      """Canonical event-name strings. Compare against these, never raw strings."""
      BANK_QUESTION_UPDATED = "bank.question_updated"
      BANK_STATUS_CHANGED = "bank.status_changed"
      PIPELINE_GENERATION_COMPLETE = "pipeline.generation_complete"


  # --- Channel helpers ------------------------------------------------------

  def job_channel(job_id) -> str:
      """Channel for all events scoped to one job."""
      return f"job:{job_id}"


  # --- Envelope -------------------------------------------------------------

  @dataclass(frozen=True, slots=True)
  class Envelope:
      """Transport-level wrapper around a domain event.

      All events share this shape so subscribers can deserialize uniformly.
      """
      event: str
      payload: dict
      correlation_id: str
      emitted_at: str  # ISO-8601 UTC

      def to_json(self) -> bytes:
          return orjson.dumps({
              "event": self.event,
              "payload": self.payload,
              "correlation_id": self.correlation_id,
              "emitted_at": self.emitted_at,
          })

      @classmethod
      def from_json(cls, raw: bytes | str) -> "Envelope":
          data = orjson.loads(raw)
          return cls(
              event=data["event"],
              payload=data["payload"],
              correlation_id=data["correlation_id"],
              emitted_at=data["emitted_at"],
          )


  # --- Client lifecycle -----------------------------------------------------

  _client: aioredis.Redis | None = None


  def _get_client() -> aioredis.Redis:
      global _client
      if _client is None:
          # Separate pool from Dramatiq — pub/sub subscribe connections block.
          _client = aioredis.from_url(
              settings.redis_url,
              socket_timeout=5,
              socket_connect_timeout=5,
              health_check_interval=10,
              max_connections=100,
          )
      return _client


  async def startup() -> None:
      """Initialize the client and verify connectivity. Called from FastAPI lifespan."""
      client = _get_client()
      try:
          await client.ping()
          logger.info("pubsub.startup", status="ok")
      except RedisError as exc:
          logger.error("pubsub.startup", status="failed", error=str(exc))
          raise


  async def shutdown() -> None:
      """Close the client and drain any pending operations."""
      global _client
      if _client is not None:
          with suppress(Exception):
              await _client.aclose()
          _client = None
          logger.info("pubsub.shutdown")


  # --- Public API -----------------------------------------------------------

  async def publish(
      channel: str,
      event: str,
      payload: dict,
      *,
      correlation_id: str,
  ) -> None:
      """Publish an event. Best-effort — never raises.

      MUST be called AFTER the DB transaction that produced the event has
      committed. In FastAPI handlers, use BackgroundTasks. In actors, call
      after `async with session.begin():` exits.
      """
      envelope = Envelope(
          event=event,
          payload=payload,
          correlation_id=correlation_id,
          emitted_at=datetime.now(timezone.utc).isoformat(),
      )
      try:
          client = _get_client()
          # asyncio.shield so a cancelled caller doesn't abort a half-sent publish.
          await asyncio.shield(client.publish(channel, envelope.to_json()))
          logger.info(
              "pubsub.publish.ok",
              channel=channel,
              event=event,
              correlation_id=correlation_id,
              metric_name="pubsub.publish.ok",
          )
      except Exception as exc:  # noqa: BLE001 - best-effort, catch everything
          logger.warning(
              "pubsub.publish.failed",
              channel=channel,
              event=event,
              correlation_id=correlation_id,
              error=str(exc),
              metric_name="pubsub.publish.failed",
          )


  async def subscribe(*channels: str) -> AsyncIterator[Envelope]:
      """Subscribe to one or more channels, yielding envelopes.

      Auto-reconnects with exponential backoff on connection drops.
      Events missed during reconnects are NOT re-delivered — the caller
      is responsible for any correctness backstop.

      Honors asyncio cancellation: cancelling the iterator closes the
      pubsub connection cleanly.
      """
      backoff_seconds = 1.0
      max_backoff = 30.0
      while True:
          client = _get_client()
          pubsub = client.pubsub()
          try:
              await pubsub.subscribe(*channels)
              logger.info(
                  "pubsub.subscribe.connected",
                  channels=list(channels),
                  metric_name="pubsub.subscribe.connected",
              )
              backoff_seconds = 1.0  # reset on successful connection
              async for raw in pubsub.listen():
                  if raw.get("type") != "message":
                      continue  # skip subscribe/unsubscribe control messages
                  try:
                      yield Envelope.from_json(raw["data"])
                  except (orjson.JSONDecodeError, KeyError) as exc:
                      logger.warning(
                          "pubsub.subscribe.malformed_message",
                          error=str(exc),
                      )
          except asyncio.CancelledError:
              logger.info("pubsub.subscribe.cancelled", channels=list(channels))
              raise
          except Exception as exc:  # noqa: BLE001
              logger.warning(
                  "pubsub.subscribe.reconnected",
                  channels=list(channels),
                  error=str(exc),
                  backoff_seconds=backoff_seconds,
                  metric_name="pubsub.subscribe.reconnected",
              )
              await asyncio.sleep(backoff_seconds)
              backoff_seconds = min(backoff_seconds * 2, max_backoff)
          finally:
              with suppress(Exception):
                  await pubsub.aclose()
  ```

- [ ] **Step 3.2: Sanity-check the file loads**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose run --rm nexus python -c "from app import pubsub; print(pubsub.Events.BANK_QUESTION_UPDATED)"
  ```
  Expected: `bank.question_updated`.

- [ ] **Step 3.3: Commit**

  ```bash
  git add backend/nexus/app/pubsub.py
  git commit -m "feat(pubsub): add app/pubsub.py module boundary

  Centralized pub/sub API: publish() (fire-and-forget, never raises),
  subscribe() (auto-reconnecting async iterator), Envelope type,
  Events constants. Redis backend today; swap-out to SNS at enterprise
  is a change inside this file.

  Separate Redis client from Dramatiq broker to avoid starving workers."
  ```

---

## Task 4: Unit tests for `pubsub.publish` / `pubsub.subscribe` (B2.0, part 2 of 3)

**Goal:** TDD the failure semantics — publish swallows RedisError, subscribe reconnects on drop, envelope round-trips.

**Files:**
- Create: `backend/nexus/tests/test_pubsub.py`

- [ ] **Step 4.1: Write the tests**

  Create `backend/nexus/tests/test_pubsub.py`:
  ```python
  """Unit tests for app/pubsub.py."""
  from __future__ import annotations

  import asyncio

  import pytest
  from redis.exceptions import RedisError

  from app import pubsub

  pytestmark = pytest.mark.asyncio


  async def test_envelope_round_trip():
      env = pubsub.Envelope(
          event=pubsub.Events.BANK_QUESTION_UPDATED,
          payload={"job_id": "abc", "bank_id": "def"},
          correlation_id="corr-1",
          emitted_at="2026-04-24T00:00:00+00:00",
      )
      reconstructed = pubsub.Envelope.from_json(env.to_json())
      assert reconstructed == env


  async def test_publish_swallows_redis_error(monkeypatch, caplog):
      """publish() must NEVER raise — failures become structlog warnings."""
      class FailingClient:
          async def publish(self, *_args, **_kwargs):
              raise RedisError("simulated outage")

      monkeypatch.setattr(pubsub, "_get_client", lambda: FailingClient())

      # Should return None, not raise.
      result = await pubsub.publish(
          pubsub.job_channel("job-123"),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {"bank_id": "bank-1"},
          correlation_id="corr-1",
      )
      assert result is None


  async def test_publish_ok_path(monkeypatch):
      published: list[tuple[str, bytes]] = []

      class FakeClient:
          async def publish(self, channel, data):
              published.append((channel, data))

      monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

      await pubsub.publish(
          pubsub.job_channel("job-123"),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {"bank_id": "bank-1"},
          correlation_id="corr-xyz",
      )

      assert len(published) == 1
      channel, data = published[0]
      assert channel == "job:job-123"
      env = pubsub.Envelope.from_json(data)
      assert env.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert env.correlation_id == "corr-xyz"
      assert env.payload == {"bank_id": "bank-1"}


  async def test_subscribe_skips_non_message_frames(monkeypatch):
      """The pubsub.listen() generator yields subscribe/unsubscribe control
      frames before the first real message. subscribe() must skip them."""

      class FakePubSub:
          def __init__(self):
              self.frames = [
                  {"type": "subscribe", "channel": b"job:1", "data": 1},
                  {
                      "type": "message",
                      "channel": b"job:1",
                      "data": pubsub.Envelope(
                          event=pubsub.Events.BANK_QUESTION_UPDATED,
                          payload={"hello": "world"},
                          correlation_id="c",
                          emitted_at="2026-04-24T00:00:00+00:00",
                      ).to_json(),
                  },
              ]

          async def subscribe(self, *_channels):
              pass

          async def listen(self):
              for f in self.frames:
                  yield f
              # Simulate channel close — exit the generator.

          async def aclose(self):
              pass

      class FakeClient:
          def pubsub(self):
              return FakePubSub()

      monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

      envelopes: list[pubsub.Envelope] = []
      async def collect():
          async for env in pubsub.subscribe("job:1"):
              envelopes.append(env)
              if len(envelopes) == 1:
                  break

      # subscribe loops forever on reconnect; break out after first real message.
      await asyncio.wait_for(collect(), timeout=2.0)
      assert len(envelopes) == 1
      assert envelopes[0].payload == {"hello": "world"}


  async def test_subscribe_reconnects_on_error(monkeypatch):
      """If the underlying connection raises, subscribe() reconnects."""
      attempt = {"count": 0}

      class FlakyPubSub:
          def __init__(self, should_fail):
              self.should_fail = should_fail

          async def subscribe(self, *_channels):
              pass

          async def listen(self):
              if self.should_fail:
                  raise RedisError("connection reset")
              yield {
                  "type": "message",
                  "channel": b"job:1",
                  "data": pubsub.Envelope(
                      event=pubsub.Events.BANK_QUESTION_UPDATED,
                      payload={"final": True},
                      correlation_id="c",
                      emitted_at="2026-04-24T00:00:00+00:00",
                  ).to_json(),
              }

          async def aclose(self):
              pass

      class FakeClient:
          def pubsub(self):
              attempt["count"] += 1
              # Fail the first attempt, succeed the second.
              return FlakyPubSub(should_fail=attempt["count"] == 1)

      monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

      # Patch sleep so the backoff doesn't dominate the test runtime.
      async def fast_sleep(_):
          pass
      monkeypatch.setattr(pubsub.asyncio, "sleep", fast_sleep)

      envelopes = []
      async def collect():
          async for env in pubsub.subscribe("job:1"):
              envelopes.append(env)
              break

      await asyncio.wait_for(collect(), timeout=2.0)
      assert attempt["count"] == 2, "subscribe() did not reconnect after error"
      assert envelopes[0].payload == {"final": True}
  ```

- [ ] **Step 4.2: Run the tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose run --rm nexus pytest tests/test_pubsub.py -xvs
  ```
  Expected: all 5 tests pass.

  If the `test_subscribe_skips_non_message_frames` test hangs, the reconnect loop is not exiting — check that `break` in the collector triggers `CancelledError` through the async generator.

- [ ] **Step 4.3: Commit**

  ```bash
  git add backend/nexus/tests/test_pubsub.py
  git commit -m "test(pubsub): cover publish failure swallowing, subscribe reconnect"
  ```

---

## Task 5: Wire pubsub into FastAPI lifespan + health check (B2.0, part 3 of 3)

**Goal:** `pubsub.startup()` runs at app boot and aborts startup if Redis is unreachable; `pubsub.shutdown()` runs at shutdown. The `/health` endpoint includes a pub/sub ping.

**Files:**
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 5.1: Read the current lifespan function**

  ```bash
  sed -n '145,175p' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus/app/main.py
  ```
  Observe the existing shape:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
      logger.info("nexus.startup", environment=settings.environment)
      # Block startup if any tenant-scoped table is missing an RLS policy.
      # (RLS completeness assertion here)
      yield
      from app.ai.client import shutdown_langfuse
      shutdown_langfuse()
      logger.info("nexus.shutdown")
  ```

- [ ] **Step 5.2: Add pubsub startup + shutdown**

  Edit `backend/nexus/app/main.py` — find the lifespan function and insert the pubsub hooks. The exact line numbers are approximate (the original file is 650+ LOC); match the structure, not the line count.

  ```python
  from app import pubsub  # add to imports at top of file

  @asynccontextmanager
  async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
      logger.info("nexus.startup", environment=settings.environment)

      # Block startup if any tenant-scoped table is missing an RLS policy.
      # ... existing RLS completeness assertion stays here ...

      # Pub/sub: verify connectivity before accepting traffic.
      await pubsub.startup()

      yield

      # Shutdown sequence — reverse order of startup.
      await pubsub.shutdown()

      from app.ai.client import shutdown_langfuse
      shutdown_langfuse()
      logger.info("nexus.shutdown")
  ```

- [ ] **Step 5.3: Extend `/health` endpoint**

  Find the `/health` endpoint (around line 646). Current shape likely returns a static `{"status": "ok"}`. Add a pubsub liveness check:

  ```python
  @application.get("/health", tags=["infra"])
  async def health() -> dict:
      # Baseline: app is reachable.
      result = {"status": "ok", "checks": {}}

      # Pub/sub ping — 2s timeout.
      try:
          import asyncio
          client = pubsub._get_client()
          await asyncio.wait_for(client.ping(), timeout=2.0)
          result["checks"]["pubsub"] = "ok"
      except Exception as exc:  # noqa: BLE001
          result["checks"]["pubsub"] = f"failed: {exc}"
          result["status"] = "degraded"

      return result
  ```

  Note: keep the function signature and return type consistent with the existing handler. If the current handler already returns a structured dict, merge in the `checks` key.

- [ ] **Step 5.4: Verify startup behavior manually**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose up -d redis postgres
  docker compose run --rm nexus python -c "
  import asyncio
  from app import pubsub
  asyncio.run(pubsub.startup())
  print('OK')
  asyncio.run(pubsub.shutdown())
  "
  ```
  Expected: `OK`. No exception.

- [ ] **Step 5.5: Run the full backend test suite**

  ```bash
  docker compose run --rm nexus pytest -x
  ```
  Expected: all green. If startup is now pubsub-dependent and a test runner spins up a fresh app instance without Redis, tests may fail — in that case, patch the lifespan hook to be optional during tests, or ensure the test harness has Redis available (it should, per pre-flight).

- [ ] **Step 5.6: Commit**

  ```bash
  git add backend/nexus/app/main.py
  git commit -m "feat(main): wire pubsub into lifespan + /health probe

  Startup pings Redis; lifespan fails fast if pub/sub is unreachable.
  /health includes a 2s pubsub ping and marks status=degraded on failure."
  ```

---

## Phase 2 — JD schema alignment (Tasks 6–10)

## Task 6: Extract `enrich_job_summaries` helper (B2.1, part 1)

**Goal:** Pull the inline enrichment logic from `list_jobs` (router.py:400-450) into a reusable service function. No behavior change — just refactoring to eliminate the duplication that B2.1/B2.2 would otherwise introduce.

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`
- Modify: `backend/nexus/app/modules/jd/router.py:364-462`

- [ ] **Step 6.1: Read the current inline enrichment**

  ```bash
  sed -n '364,462p' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus/app/modules/jd/router.py
  ```
  Identify the enrichment sections:
  - Batch-load org unit names (lines ~400-405)
  - Batch-load user emails (lines ~407-410)
  - Batch-load signal snapshots + compute `signal_count` / `needs_review_count` (lines ~418-450)
  - Final `_job_to_summary()` call building each summary (lines ~453)

- [ ] **Step 6.2: Add the helper to `jd/service.py`**

  In `backend/nexus/app/modules/jd/service.py`, append a new function. Copy the inline enrichment logic verbatim, generalize over a list of jobs:

  ```python
  async def enrich_job_summaries(
      jobs: list[JobPosting],
      db: AsyncSession,
  ) -> list[JobPostingSummary]:
      """Enrich a list of JobPosting rows with org_unit_name, creator/updater
      emails, signal_count, and needs_review_count.

      Single query per enrichment dimension (org units, users, snapshots) —
      no N+1. Safe to call from list, detail, and retry handlers alike.
      """
      if not jobs:
          return []

      # Collect unique lookup keys.
      org_unit_ids = {j.org_unit_id for j in jobs if j.org_unit_id}
      user_ids = {
          user_id
          for j in jobs
          for user_id in (j.created_by, j.updated_by)
          if user_id is not None
      }
      job_ids = [j.id for j in jobs]

      # Batch-load org unit names.
      org_unit_names: dict[UUID, str] = {}
      if org_unit_ids:
          rows = (await db.execute(
              select(OrganizationalUnit.id, OrganizationalUnit.name)
              .where(OrganizationalUnit.id.in_(org_unit_ids))
          )).all()
          org_unit_names = {row.id: row.name for row in rows}

      # Batch-load user emails.
      user_emails: dict[UUID, str] = {}
      if user_ids:
          rows = (await db.execute(
              select(User.id, User.email).where(User.id.in_(user_ids))
          )).all()
          user_emails = {row.id: row.email for row in rows}

      # Batch-load latest signal snapshots per job.
      # (Use the same query the old list_jobs handler used — copy verbatim.)
      snapshots_by_job: dict[UUID, list[JobPostingSignalSnapshot]] = {}
      if job_ids:
          latest_snapshot_ids = (
              select(
                  JobPostingSignalSnapshot.job_posting_id,
                  func.max(JobPostingSignalSnapshot.created_at).label("max_created"),
              )
              .where(JobPostingSignalSnapshot.job_posting_id.in_(job_ids))
              .group_by(JobPostingSignalSnapshot.job_posting_id)
              .subquery()
          )
          rows = (await db.execute(
              select(JobPostingSignalSnapshot)
              .join(
                  latest_snapshot_ids,
                  and_(
                      JobPostingSignalSnapshot.job_posting_id
                          == latest_snapshot_ids.c.job_posting_id,
                      JobPostingSignalSnapshot.created_at
                          == latest_snapshot_ids.c.max_created,
                  ),
              )
          )).scalars().all()
          for snap in rows:
              snapshots_by_job.setdefault(snap.job_posting_id, []).append(snap)

      # Build enriched summaries.
      enriched: list[JobPostingSummary] = []
      for job in jobs:
          snaps = snapshots_by_job.get(job.id, [])
          signals = snaps[0].signals if snaps else []
          signal_count = len(signals)
          needs_review_count = sum(
              1
              for s in signals
              if s.get("source") == "ai_inferred" and s.get("weight", 1) < 2
          )
          enriched.append(
              _job_to_summary(
                  job,
                  org_unit_name=org_unit_names.get(job.org_unit_id),
                  created_by_email=user_emails.get(job.created_by),
                  updated_by_email=user_emails.get(job.updated_by),
                  signal_count=signal_count,
                  needs_review_count=needs_review_count,
              )
          )
      return enriched
  ```

  **Note on imports:** add whatever was inline in the router (`from app.models import OrganizationalUnit, User, JobPosting, JobPostingSignalSnapshot`, etc.). Check the router's imports for the exact list.

  **Note on `_job_to_summary`:** it currently lives in `router.py`. Since it's only used by enrichment paths, move it into `service.py` alongside `enrich_job_summaries` and re-export from router if anything else imports it. The router keeps a `from app.modules.jd.service import _job_to_summary` if the router still references it.

- [ ] **Step 6.3: Rewrite `list_jobs` in `router.py` to use the helper**

  Replace the inline enrichment block (~lines 390-455) with:
  ```python
  jobs_result = await db.execute(select(JobPosting).where(...))  # existing query
  jobs = jobs_result.scalars().all()

  # Enrich via shared helper.
  summaries = await enrich_job_summaries(list(jobs), db)
  return summaries
  ```

- [ ] **Step 6.4: Run existing list_jobs tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose run --rm nexus pytest tests/test_jd_router.py -xvs
  ```
  Expected: all green. The refactor is behavior-neutral; any failure indicates a copy-paste error in the helper.

- [ ] **Step 6.5: Commit**

  ```bash
  git add backend/nexus/app/modules/jd/service.py backend/nexus/app/modules/jd/router.py
  git commit -m "refactor(jd): extract enrich_job_summaries helper

  Pulls the inline batch-enrichment logic out of list_jobs into a
  reusable service function. Prepares get_job and retry handlers (next
  tasks) to share the same code path."
  ```

---

## Task 7: `get_job` handler uses the enrichment helper (B2.1, part 2)

**Goal:** The job-detail response now includes `org_unit_name`, `created_by_email`, `updated_by_email`, `signal_count`, `needs_review_count` — fields that `JobPostingWithSnapshot` already declares but that the handler has been leaving null/zero.

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py:465-485`
- Modify: `backend/nexus/tests/test_jd_router.py` (extend)

- [ ] **Step 7.1: Write the failing test**

  In `backend/nexus/tests/test_jd_router.py`, add a new test (location: alongside existing `test_list_jobs_enrichment` or similar):

  ```python
  async def test_get_job_populates_enrichment_fields(client, tenant_and_user, seed_job_with_snapshot):
      """GET /api/jobs/{id} includes org_unit_name, emails, signal_count, needs_review_count."""
      job = seed_job_with_snapshot  # fixture: creates a job + snapshot with 2 AI-inferred signals
      resp = await client.get(f"/api/jobs/{job.id}")
      assert resp.status_code == 200
      data = resp.json()

      assert data["org_unit_name"] is not None, "org_unit_name should be populated"
      assert data["created_by_email"] is not None
      assert data["signal_count"] >= 2
      assert data["needs_review_count"] >= 0  # depends on fixture weights
      # Sanity: the snapshot field (unique to detail response) is still there.
      assert data["latest_snapshot"] is not None
  ```

  If `seed_job_with_snapshot` doesn't exist, reuse whatever fixture the existing `test_list_jobs_*` tests use — the signal-snapshot shape is the same.

- [ ] **Step 7.2: Run the failing test**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py::test_get_job_populates_enrichment_fields -xvs
  ```
  Expected: FAIL — `org_unit_name` is null (or KeyError if the response shape doesn't yet include it).

- [ ] **Step 7.3: Update `get_job` handler**

  Find the `get_job` handler in `backend/nexus/app/modules/jd/router.py` (~line 465-485). Current flow:
  ```python
  @router.get("/{job_id}", response_model=JobPostingWithSnapshot)
  async def get_job(...):
      job = await get_job_posting_with_latest_snapshot(...)
      return _job_with_snapshot_to_response(job)
  ```

  Replace with:
  ```python
  @router.get("/{job_id}", response_model=JobPostingWithSnapshot)
  async def get_job(
      job_id: UUID,
      db: AsyncSession = Depends(get_tenant_db),
      # ... other existing deps
  ):
      job = await get_job_posting_with_latest_snapshot(db, job_id)
      if job is None:
          raise HTTPException(404, "Job not found")

      # Enrich: reuse list_jobs helper for parity.
      summaries = await enrich_job_summaries([job], db)
      base_summary = summaries[0]

      # Compose the with-snapshot response from the enriched summary + snapshot.
      return _job_with_snapshot_to_response(
          job,
          enriched=base_summary,
      )
  ```

  **Update `_job_with_snapshot_to_response`** to accept the `enriched` summary and merge its enrichment fields onto the response. Pydantic schema `JobPostingWithSnapshot` extends `JobPostingSummary`, so any field on the enriched summary is valid on the response.

  ```python
  def _job_with_snapshot_to_response(
      job: JobPosting,
      *,
      enriched: JobPostingSummary | None = None,
  ) -> JobPostingWithSnapshot:
      """Build the JD detail response. If `enriched` is provided, merge its
      summary-level fields (org_unit_name, emails, counts) onto the result."""
      base = enriched.model_dump() if enriched else _job_to_summary(job).model_dump()
      return JobPostingWithSnapshot(
          **base,
          # snapshot-specific fields:
          latest_snapshot=...  # existing logic
          description_raw=...  # existing logic
          # ... (preserve all existing fields)
      )
  ```

- [ ] **Step 7.4: Run the test**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py::test_get_job_populates_enrichment_fields -xvs
  ```
  Expected: PASS.

- [ ] **Step 7.5: Run the full jd_router test file**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py -xvs
  ```
  Expected: all green. Watch for existing tests that asserted `org_unit_name is None` — update those expectations if any fail because of the now-populated field.

- [ ] **Step 7.6: Commit**

  ```bash
  git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_router.py
  git commit -m "fix(jd): get_job response populates enrichment fields

  Before: org_unit_name / created_by_email / updated_by_email /
  signal_count / needs_review_count returned as null/0 because
  enrichment logic was inline in list_jobs only.

  After: reuses enrich_job_summaries helper so detail and list responses
  are field-complete and identical in shape."
  ```

---

## Task 8: `retry` handler uses the enrichment helper (B2.2)

**Goal:** `POST /api/jobs/{id}/retry` no longer returns a summary with zeros/nulls — it enriches before returning, same as `list_jobs`.

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py:506-531`
- Modify: `backend/nexus/tests/test_jd_router.py`

- [ ] **Step 8.1: Write the failing test**

  Add to `tests/test_jd_router.py`:
  ```python
  async def test_retry_returns_enriched_summary(client, tenant_and_user, seed_failed_job):
      """POST /api/jobs/{id}/retry response includes enrichment fields."""
      job = seed_failed_job  # fixture: creates a job in a retry-eligible state
      resp = await client.post(f"/api/jobs/{job.id}/retry")
      assert resp.status_code == 200
      data = resp.json()

      assert data["org_unit_name"] is not None
      assert data["created_by_email"] is not None
      # signal_count may be 0 on a fresh retry, but the key must exist and be int.
      assert isinstance(data["signal_count"], int)
      assert isinstance(data["needs_review_count"], int)
  ```

- [ ] **Step 8.2: Run the failing test**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py::test_retry_returns_enriched_summary -xvs
  ```
  Expected: FAIL — `org_unit_name` is null.

- [ ] **Step 8.3: Update `retry` handler**

  Find the retry handler in `router.py:506-531`. Current last line is something like:
  ```python
  return _job_to_summary(job)
  ```

  Replace with:
  ```python
  enriched = await enrich_job_summaries([job], db)
  return enriched[0]
  ```

- [ ] **Step 8.4: Run the test**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py::test_retry_returns_enriched_summary -xvs
  ```
  Expected: PASS.

- [ ] **Step 8.5: Run the full jd_router file**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_router.py -xvs
  ```
  Expected: all green.

- [ ] **Step 8.6: Commit**

  ```bash
  git add backend/nexus/app/modules/jd/router.py backend/nexus/tests/test_jd_router.py
  git commit -m "fix(jd): retry response populates enrichment fields

  Same fix as B2.1/get_job — runs the job through enrich_job_summaries
  so the retry response is field-complete."
  ```

---

## Task 9: Frontend — align `JobPostingSummary` / `JobPostingWithSnapshot` types (B2.1 frontend)

**Goal:** Frontend type definitions match the now-field-complete backend responses. Eliminates the existing "field might be undefined" narrowing that callers apply today.

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts:59-104`

- [ ] **Step 9.1: Read the current types**

  ```bash
  sed -n '55,110p' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app/lib/api/jobs.ts
  ```

  Observe which of `org_unit_name`, `created_by_email`, `updated_by_email`, `signal_count`, `needs_review_count` are declared optional (`?:`) on `JobPostingSummary` and/or missing from `JobPostingWithSnapshot`.

- [ ] **Step 9.2: Update the types**

  In `frontend/app/lib/api/jobs.ts`, update both interfaces. The backend now guarantees all fields — drop `?:` where the type was optional:

  ```ts
  // BEFORE (approximate):
  export interface JobPostingSummary {
    id: string
    title: string
    status: string
    org_unit_id: string | null
    org_unit_name?: string | null       // ← was optional
    created_by?: string | null
    created_by_email?: string | null    // ← was optional
    updated_by?: string | null
    updated_by_email?: string | null    // ← was optional
    signal_count?: number               // ← was optional
    needs_review_count?: number         // ← was optional
    // ...
  }

  // AFTER:
  export interface JobPostingSummary {
    id: string
    title: string
    status: string
    org_unit_id: string | null
    org_unit_name: string | null
    created_by: string | null
    created_by_email: string | null
    updated_by: string | null
    updated_by_email: string | null
    signal_count: number
    needs_review_count: number
    // ...
  }
  ```

  Do the same for `JobPostingWithSnapshot` — it already extends `JobPostingSummary`, so after the parent update it inherits the tighter types. Confirm it declares no redundant `?:` overrides on the enrichment fields.

- [ ] **Step 9.3: Run type-check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app
  npm run type-check
  ```
  Expected: clean, OR a handful of errors at call sites that previously narrowed `?:`. Those narrowing guards can stay (defensive, no cost) or be removed — if you choose to remove, do so in this task and note them in the commit message. Otherwise, no code changes, just the type tightening.

- [ ] **Step 9.4: Run tests**

  ```bash
  npm run test
  ```
  Expected: green.

- [ ] **Step 9.5: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2
  git add frontend/app/lib/api/jobs.ts
  git commit -m "fix(frontend): tighten JobPosting types to match backend guarantees

  Backend list_jobs/get_job/retry all now return populated enrichment
  fields (B2.1/B2.2). Frontend types drop the '?:' on org_unit_name,
  created_by_email, updated_by_email, signal_count, needs_review_count."
  ```

---

## Task 10: Frontend — add `OrgUnit.company_profile_completed_at` (B2.3)

**Goal:** Bring the frontend `OrgUnit` type in line with the backend `OrgUnitResponse` schema which already includes this field.

**Files:**
- Modify: `frontend/app/lib/api/org-units.ts:20-38`

- [ ] **Step 10.1: Read the current type**

  ```bash
  sed -n '15,45p' /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app/lib/api/org-units.ts
  ```

- [ ] **Step 10.2: Add the field**

  Add `company_profile_completed_at?: string | null` to the `OrgUnit` interface (use `?:` — field may be null if the org unit has no completed profile yet):

  ```ts
  export interface OrgUnit {
    id: string
    tenant_id: string
    parent_id: string | null
    name: string
    slug: string | null
    unit_type: string
    // ... existing fields
    company_profile: CompanyProfile | null
    company_profile_completed_at: string | null  // ← new
    created_at: string
    updated_at: string
  }
  ```

- [ ] **Step 10.3: Verify type-check + tests**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app
  npm run type-check && npm run test
  ```
  Expected: green.

- [ ] **Step 10.4: Commit**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2
  git add frontend/app/lib/api/org-units.ts
  git commit -m "fix(frontend): add OrgUnit.company_profile_completed_at to match backend

  Backend OrgUnitResponse schema has exposed this timestamp since the
  company-profile feature landed; frontend type was lagging."
  ```

---

## Phase 3 — Event emission at mutation sites (Tasks 11–16)

**Pattern for every task in this phase:**
- Write an integration test that calls the handler / actor and asserts a pub/sub envelope is published on the right channel with the right event name + payload + correlation_id.
- Run → FAIL.
- Wire the handler to call `BackgroundTasks.add_task(pubsub.publish, ...)` (handlers) or inline `await pubsub.publish(...)` after commit (actors).
- Run → PASS.
- Commit.

**Shared test fixture (create once, reuse in Tasks 11–16):**

- [ ] **Setup — add a pubsub capture fixture**

  In `backend/nexus/tests/conftest.py` (or create a new `fixtures/pubsub.py` imported by conftest), add:

  ```python
  from dataclasses import dataclass
  from typing import Callable

  import pytest

  from app import pubsub


  @dataclass
  class CapturedPublish:
      channel: str
      event: str
      payload: dict
      correlation_id: str


  @pytest.fixture
  def capture_publishes(monkeypatch) -> list[CapturedPublish]:
      """Replace pubsub.publish with a capturing stub. Returns a list that
      accumulates every publish call made during the test."""
      captured: list[CapturedPublish] = []

      async def stub_publish(channel, event, payload, *, correlation_id):
          captured.append(CapturedPublish(
              channel=channel,
              event=event,
              payload=payload,
              correlation_id=correlation_id,
          ))

      monkeypatch.setattr(pubsub, "publish", stub_publish)
      return captured
  ```

  Tests in this phase accept `capture_publishes` as a parameter and assert on the list contents.

## Task 11: `create_question` handler publishes event (B2.4.1)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py:473`
- Create: `backend/nexus/tests/test_question_banks_events.py`

- [ ] **Step 11.1: Write the failing test**

  Create `backend/nexus/tests/test_question_banks_events.py`:
  ```python
  """Integration tests for pub/sub event emission from question-bank mutations.

  Each test hits the real handler/service path but replaces pubsub.publish with
  a capturing stub (see capture_publishes fixture)."""
  from __future__ import annotations

  import pytest

  from app import pubsub

  pytestmark = pytest.mark.asyncio


  async def test_create_question_publishes_event(
      client, tenant_and_user, seed_bank, capture_publishes
  ):
      bank = seed_bank  # fixture: creates a bank in 'reviewing' status
      resp = await client.post(
          f"/api/question-banks/{bank.id}/questions",
          json={
              "text": "Tell me about a challenging project.",
              "signal_values": ["communication"],
              "estimated_minutes": 3.0,
              # ... fill in required fields per the schema
          },
      )
      assert resp.status_code == 201

      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.channel == f"job:{bank.job_id}"
      assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert pub.payload["bank_id"] == str(bank.id)
      assert pub.payload["mutation"] == "create"
      assert pub.correlation_id  # present and non-empty
  ```

  Note: `seed_bank` must produce a bank in a status that allows question creation. Reuse the fixture that existing `test_question_banks_router.py::test_create_question_*` tests use; if it's not a shared fixture, lift it into `conftest.py` first.

- [ ] **Step 11.2: Run the failing test**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_create_question_publishes_event -xvs
  ```
  Expected: FAIL — `len(capture_publishes) == 0` because the handler doesn't publish yet.

- [ ] **Step 11.3: Update the handler**

  In `backend/nexus/app/modules/question_bank/router.py`, find the `create_question` handler (~line 473). Current signature:
  ```python
  @router.post("/banks/{bank_id}/questions", response_model=QuestionResponse, status_code=201)
  async def create_question(
      bank_id: UUID,
      body: CreateQuestionRequest,
      db: AsyncSession = Depends(get_tenant_db),
      # ... other deps
      request: Request,
  ):
      question = await service.create_question(db, bank_id, body, ...)
      return question
  ```

  Add `BackgroundTasks` dependency and publish after the service call:
  ```python
  from fastapi import BackgroundTasks

  from app import pubsub
  # Reuse the existing helper if one exists in question_bank/router.py, or
  # add one mirroring jd/router.py:49 `_get_correlation_id`.

  @router.post("/banks/{bank_id}/questions", response_model=QuestionResponse, status_code=201)
  async def create_question(
      bank_id: UUID,
      body: CreateQuestionRequest,
      background_tasks: BackgroundTasks,
      request: Request,
      db: AsyncSession = Depends(get_tenant_db),
      # ... other deps
  ):
      correlation_id = _get_correlation_id(request)
      question = await service.create_question(db, bank_id, body, ...)

      # Publish after commit — FastAPI runs BackgroundTasks post-response,
      # which is post-dependency-cleanup (post-commit).
      background_tasks.add_task(
          pubsub.publish,
          pubsub.job_channel(question.job_id),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {
              "job_id": str(question.job_id),
              "bank_id": str(bank_id),
              "stage_id": str(question.stage_id),
              "question_id": str(question.id),
              "mutation": "create",
          },
          correlation_id=correlation_id,
      )
      return question
  ```

  Notes:
  - `question.job_id` and `question.stage_id` — the handler needs these. If `service.create_question` doesn't return them, have it return a richer object (or fetch via `bank → pipeline_stage → job_pipeline_instance → job_id`) before publishing.
  - `_get_correlation_id(request)` — port from `jd/router.py:49` if question_bank doesn't have it. Signature:
    ```python
    def _get_correlation_id(request: Request) -> str:
        return (
            request.headers.get("x-correlation-id")
            or str(uuid.uuid4())
        )
    ```

- [ ] **Step 11.4: Run the test**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_create_question_publishes_event -xvs
  ```
  Expected: PASS.

- [ ] **Step 11.5: Run the existing question_banks_router tests**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_router.py -xvs
  ```
  Expected: green. (Background-task publishing shouldn't affect handler behavior, but double-check.)

- [ ] **Step 11.6: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/router.py \
          backend/nexus/tests/test_question_banks_events.py \
          backend/nexus/tests/conftest.py
  git commit -m "feat(question_bank): publish bank.question_updated on create_question

  Handler uses BackgroundTasks to enqueue the publish after response
  send (and thus after dependency-cleanup commit). Correlation_id
  threaded end-to-end."
  ```

---

## Task 12: `update_question` handler publishes event (B2.4.2)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py` (update_question handler)
- Modify: `backend/nexus/tests/test_question_banks_events.py`

- [ ] **Step 12.1: Add the failing test**

  Append to `test_question_banks_events.py`:
  ```python
  async def test_update_question_publishes_event(
      client, tenant_and_user, seed_bank_with_question, capture_publishes
  ):
      question = seed_bank_with_question  # a question, already created
      resp = await client.patch(
          f"/api/question-banks/{question.bank_id}/questions/{question.id}",
          json={"text": "Revised question text"},
      )
      assert resp.status_code == 200

      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.channel == f"job:{question.job_id}"
      assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert pub.payload["question_id"] == str(question.id)
      assert pub.payload["mutation"] == "update"
  ```

- [ ] **Step 12.2: Run it — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_update_question_publishes_event -xvs
  ```
  Expected: FAIL.

- [ ] **Step 12.3: Update the `update_question` handler**

  Find the handler (PATCH endpoint that calls `service.update_question`). Apply the same pattern:
  ```python
  @router.patch("/banks/{bank_id}/questions/{question_id}", response_model=QuestionResponse)
  async def update_question(
      bank_id: UUID,
      question_id: UUID,
      body: UpdateQuestionRequest,
      background_tasks: BackgroundTasks,
      request: Request,
      db: AsyncSession = Depends(get_tenant_db),
      # ...
  ):
      correlation_id = _get_correlation_id(request)
      updated = await service.update_question(db, bank_id, question_id, body, ...)

      background_tasks.add_task(
          pubsub.publish,
          pubsub.job_channel(updated.job_id),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {
              "job_id": str(updated.job_id),
              "bank_id": str(bank_id),
              "stage_id": str(updated.stage_id),
              "question_id": str(question_id),
              "mutation": "update",
          },
          correlation_id=correlation_id,
      )
      return updated
  ```

- [ ] **Step 12.4: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_update_question_publishes_event tests/test_question_banks_router.py -xvs
  ```

- [ ] **Step 12.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/router.py backend/nexus/tests/test_question_banks_events.py
  git commit -m "feat(question_bank): publish bank.question_updated on update_question"
  ```

---

## Task 13: `delete_question` handler publishes event (B2.4.3)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py`
- Modify: `backend/nexus/tests/test_question_banks_events.py`

- [ ] **Step 13.1: Failing test**

  ```python
  async def test_delete_question_publishes_event(
      client, tenant_and_user, seed_bank_with_question, capture_publishes
  ):
      question = seed_bank_with_question
      resp = await client.delete(
          f"/api/question-banks/{question.bank_id}/questions/{question.id}"
      )
      assert resp.status_code == 204

      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert pub.payload["mutation"] == "delete"
      assert pub.payload["question_id"] == str(question.id)
  ```

- [ ] **Step 13.2: Run — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_delete_question_publishes_event -xvs
  ```

- [ ] **Step 13.3: Wire the handler**

  ```python
  @router.delete("/banks/{bank_id}/questions/{question_id}", status_code=204)
  async def delete_question(
      bank_id: UUID,
      question_id: UUID,
      background_tasks: BackgroundTasks,
      request: Request,
      db: AsyncSession = Depends(get_tenant_db),
      # ...
  ):
      correlation_id = _get_correlation_id(request)
      # service.delete_question must return {job_id, stage_id} for the publish payload,
      # OR the handler fetches those before the delete.
      meta = await service.delete_question(db, bank_id, question_id, ...)

      background_tasks.add_task(
          pubsub.publish,
          pubsub.job_channel(meta.job_id),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {
              "job_id": str(meta.job_id),
              "bank_id": str(bank_id),
              "stage_id": str(meta.stage_id),
              "question_id": str(question_id),
              "mutation": "delete",
          },
          correlation_id=correlation_id,
      )
      return Response(status_code=204)
  ```

  If `service.delete_question` currently returns nothing (just deletes), modify it to return a small NamedTuple / dataclass with `{job_id, stage_id}`. This is a service-layer change — keep it minimal.

- [ ] **Step 13.4: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_delete_question_publishes_event tests/test_question_banks_router.py tests/test_question_banks_service.py -xvs
  ```

- [ ] **Step 13.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/router.py \
          backend/nexus/app/modules/question_bank/service.py \
          backend/nexus/tests/test_question_banks_events.py
  git commit -m "feat(question_bank): publish bank.question_updated on delete_question"
  ```

---

## Task 14: `reorder_questions` handler publishes event (B2.4.4)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py`
- Modify: `backend/nexus/tests/test_question_banks_events.py`

- [ ] **Step 14.1: Failing test**

  ```python
  async def test_reorder_questions_publishes_event(
      client, tenant_and_user, seed_bank_with_three_questions, capture_publishes
  ):
      bank, questions = seed_bank_with_three_questions
      # Reverse the order.
      new_order = [q.id for q in reversed(questions)]
      resp = await client.post(
          f"/api/question-banks/{bank.id}/questions/reorder",
          json={"question_ids": [str(qid) for qid in new_order]},
      )
      assert resp.status_code == 200

      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert pub.payload["mutation"] == "reorder"
      assert pub.payload["bank_id"] == str(bank.id)
      assert pub.payload.get("question_id") is None  # reorder is bank-level, no single question
  ```

- [ ] **Step 14.2: Run — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_reorder_questions_publishes_event -xvs
  ```

- [ ] **Step 14.3: Wire the handler**

  ```python
  @router.post("/banks/{bank_id}/questions/reorder")
  async def reorder_questions(
      bank_id: UUID,
      body: ReorderRequest,
      background_tasks: BackgroundTasks,
      request: Request,
      db: AsyncSession = Depends(get_tenant_db),
      # ...
  ):
      correlation_id = _get_correlation_id(request)
      meta = await service.reorder_questions(db, bank_id, body.question_ids, ...)

      background_tasks.add_task(
          pubsub.publish,
          pubsub.job_channel(meta.job_id),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {
              "job_id": str(meta.job_id),
              "bank_id": str(bank_id),
              "stage_id": str(meta.stage_id),
              "question_id": None,
              "mutation": "reorder",
          },
          correlation_id=correlation_id,
      )
      return meta
  ```

- [ ] **Step 14.4: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_reorder_questions_publishes_event tests/test_question_banks_router.py -xvs
  ```

- [ ] **Step 14.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/router.py \
          backend/nexus/app/modules/question_bank/service.py \
          backend/nexus/tests/test_question_banks_events.py
  git commit -m "feat(question_bank): publish bank.question_updated on reorder_questions"
  ```

---

## Task 15: `confirm_bank` handler publishes `bank.status_changed` (B2.4.5)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py`
- Modify: `backend/nexus/tests/test_question_banks_events.py`

**Why this is separate from B2.4.1-4:** `confirm_bank` emits a different event (`bank.status_changed`) because the mutation is a status transition, not a question content change. The existing poll detects it via status-field change; with the backstop bumped to 5s, we want the pub/sub fast path for sub-second UX.

- [ ] **Step 15.1: Failing test**

  ```python
  async def test_confirm_bank_publishes_status_changed(
      client, tenant_and_user, seed_bank_reviewing, capture_publishes
  ):
      bank = seed_bank_reviewing  # bank in 'reviewing' status, ready to confirm
      resp = await client.post(f"/api/question-banks/{bank.id}/confirm")
      assert resp.status_code == 200

      # confirm_bank publishes status_changed; it does NOT publish question_updated.
      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.event == pubsub.Events.BANK_STATUS_CHANGED
      assert pub.payload["bank_id"] == str(bank.id)
      assert pub.payload["new_status"] == "confirmed"
  ```

- [ ] **Step 15.2: Run — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_confirm_bank_publishes_status_changed -xvs
  ```

- [ ] **Step 15.3: Wire the handler**

  ```python
  @router.post("/banks/{bank_id}/confirm", response_model=BankResponse)
  async def confirm_bank(
      bank_id: UUID,
      background_tasks: BackgroundTasks,
      request: Request,
      db: AsyncSession = Depends(get_tenant_db),
      # ...
  ):
      correlation_id = _get_correlation_id(request)
      bank = await service.confirm_bank(db, bank_id, ...)

      background_tasks.add_task(
          pubsub.publish,
          pubsub.job_channel(bank.job_id),
          pubsub.Events.BANK_STATUS_CHANGED,
          {
              "job_id": str(bank.job_id),
              "bank_id": str(bank_id),
              "stage_id": str(bank.stage_id),
              "new_status": bank.status,
          },
          correlation_id=correlation_id,
      )
      return bank
  ```

- [ ] **Step 15.4: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_confirm_bank_publishes_status_changed tests/test_question_banks_router.py -xvs
  ```

- [ ] **Step 15.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/router.py \
          backend/nexus/tests/test_question_banks_events.py
  git commit -m "feat(question_bank): publish bank.status_changed on confirm_bank

  Complements the existing poll-based emission — with the backstop
  interval bumped to 5s (next task), the pub/sub fast path preserves
  sub-second UX on confirmation."
  ```

---

## Task 16: `regenerate_question` actor publishes event (B2.4.6)

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py:766`
- Modify: `backend/nexus/tests/test_question_banks_events.py`

**Why this differs from handler publishes:** Actors don't have FastAPI `BackgroundTasks`. They manage their own DB session and commit boundary. Publish happens inline after the `async with session.begin():` block exits (post-commit).

- [ ] **Step 16.1: Failing test**

  Actor tests don't go through the HTTP client — they invoke the actor function directly with a fake `dramatiq` message context. Check how `test_question_banks_actors.py` calls `regenerate_question` today for the pattern.

  Append to `test_question_banks_events.py`:
  ```python
  async def test_regenerate_question_actor_publishes_event(
      tenant_and_user, seed_bank_with_question, capture_publishes, monkeypatch
  ):
      """The regenerate_question actor publishes bank.question_updated
      inline after its own commit."""
      from app.modules.question_bank import actors

      # Stub out the LLM call so the actor finishes synchronously without
      # real OpenAI traffic. Reuse the existing mock helper from
      # test_question_banks_actors.py if available.
      async def fake_regenerate(*args, **kwargs):
          # Return a minimal StageQuestion-shaped result.
          from app.models import StageQuestion
          # ... (mirror what the existing actor test does)
          pass
      monkeypatch.setattr(
          "app.modules.question_bank.actors._call_openai_regenerate",
          fake_regenerate,
      )

      question = seed_bank_with_question
      await actors.regenerate_question(
          question_id=str(question.id),
          job_id=str(question.job_id),
          correlation_id="test-corr-regen",
      )

      assert len(capture_publishes) == 1
      pub = capture_publishes[0]
      assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
      assert pub.payload["mutation"] == "regenerate"
      assert pub.correlation_id == "test-corr-regen"
  ```

  If `seed_bank_with_question` doesn't carry `.job_id`, add it to the fixture (since every publish needs `job_id` for channel routing).

- [ ] **Step 16.2: Run — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_regenerate_question_actor_publishes_event -xvs
  ```

- [ ] **Step 16.3: Wire the actor**

  In `backend/nexus/app/modules/question_bank/actors.py`, find the `regenerate_question` actor (~line 766). The current shape is approximately:
  ```python
  @dramatiq.actor(queue_name="question_banks", max_retries=3)
  @observe(...)
  async def regenerate_question(question_id: str, correlation_id: str, ...):
      async with get_bypass_session() as db:
          async with db.begin():
              # ... fetch question, call LLM, write new question
              pass
          # Post-commit: publish.
  ```

  Add the inline publish:
  ```python
  from app import pubsub

  @dramatiq.actor(queue_name="question_banks", max_retries=3)
  @observe(...)
  async def regenerate_question(
      question_id: str,
      job_id: str,
      correlation_id: str,
      # ... existing params
  ) -> None:
      async with get_bypass_session() as db:
          async with db.begin():
              # ... existing logic (fetch, call LLM, replace_question_in_place)
              question = await service.replace_question_in_place(...)
          # session.begin() has now exited → COMMIT has happened.

          # Publish post-commit. Blocking is fine here — actor workers
          # aren't latency-critical. publish() never raises.
          await pubsub.publish(
              pubsub.job_channel(job_id),
              pubsub.Events.BANK_QUESTION_UPDATED,
              {
                  "job_id": job_id,
                  "bank_id": str(question.bank_id),
                  "stage_id": str(question.stage_id),
                  "question_id": question_id,
                  "mutation": "regenerate",
              },
              correlation_id=correlation_id,
          )
  ```

  Notes:
  - If the actor signature doesn't currently take `job_id`, add it. Callers (wherever `regenerate_question.send(...)` is invoked) must be updated to pass it. Grep for `regenerate_question.send(` to find call sites.
  - If the actor already has access to the bank/job via the question row, derive `job_id` from `question.bank.stage.job_pipeline_instance.job_posting_id` instead of adding a parameter — match the existing data-access pattern.

- [ ] **Step 16.4: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_events.py::test_regenerate_question_actor_publishes_event tests/test_question_banks_actors.py -xvs
  ```

- [ ] **Step 16.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/actors.py \
          backend/nexus/tests/test_question_banks_events.py
  git commit -m "feat(actors): regenerate_question publishes bank.question_updated post-commit

  Actors don't have FastAPI BackgroundTasks — publish happens inline
  after the async session.begin() context exits. publish() is
  best-effort and never raises, so a Redis outage doesn't fail the
  regeneration."
  ```

---

## Phase 4 — SSE generator refactor (Tasks 17–19)

## Task 17: SSE generator — subscribe fast path (B2.4, SSE part 1)

**Goal:** Add pub/sub subscription alongside the existing poll loop. Events from Redis land in a shared queue; the existing poll keeps populating it too. For this task the poll stays at 500ms — the interval change lands in Task 18.

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/sse.py`
- Modify: `backend/nexus/tests/test_question_banks_integration.py` OR create `tests/test_question_banks_sse.py`

- [ ] **Step 17.1: Read the current generator**

  ```bash
  cat /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus/app/modules/question_bank/sse.py
  ```
  Observe: the current `sse_generator` (around line 39) is a single async generator that polls the DB in a `while True` loop at 500ms, builds an event payload, and yields formatted SSE frames.

- [ ] **Step 17.2: Write a failing integration test**

  Create `backend/nexus/tests/test_question_banks_sse.py`:
  ```python
  """SSE generator: verify pub/sub fast path emits events from Redis."""
  from __future__ import annotations

  import asyncio

  import pytest

  from app import pubsub
  from app.modules.question_bank import sse

  pytestmark = pytest.mark.asyncio


  async def test_sse_forwards_pubsub_events(seed_bank, monkeypatch):
      """An envelope published to job:{id} is yielded by the SSE generator."""
      bank = seed_bank
      job_id = str(bank.job_id)

      # Intercept the generator and pull one event.
      received: list[str] = []

      async def consume():
          async for frame in sse.sse_generator(bank.job_id):
              received.append(frame)
              if "bank.question_updated" in frame:
                  break

      consumer = asyncio.create_task(consume())

      # Give the generator a beat to connect.
      await asyncio.sleep(0.1)

      # Publish an event from the test.
      await pubsub.publish(
          pubsub.job_channel(job_id),
          pubsub.Events.BANK_QUESTION_UPDATED,
          {"job_id": job_id, "bank_id": str(bank.id), "mutation": "update"},
          correlation_id="test-sse-1",
      )

      # Wait up to 2s for the frame to arrive (fast path should take <100ms).
      try:
          await asyncio.wait_for(consumer, timeout=2.0)
      except asyncio.TimeoutError:
          consumer.cancel()
          pytest.fail("SSE generator did not forward pub/sub event within 2s")

      assert any("bank.question_updated" in f for f in received)
      assert any("test-sse-1" in f for f in received), \
          "correlation_id not preserved end-to-end"
  ```

  This test requires real Redis to be running — it exercises the actual pub/sub transport. Pre-flight ensures redis is up via docker-compose.

- [ ] **Step 17.3: Run — expect FAIL**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_sse.py -xvs
  ```
  Expected: TIMEOUT / FAIL — generator only polls; no way for a Redis publish to reach the client.

- [ ] **Step 17.4: Add the fan-in to `sse.py`**

  Refactor `backend/nexus/app/modules/question_bank/sse.py`. The shape:
  ```python
  import asyncio
  from typing import AsyncIterator
  from uuid import UUID

  from app import pubsub
  from app.database import async_session_factory
  from app.modules.question_bank.state_machine import ...  # existing imports

  POLL_INTERVAL_SEC = 0.5  # stays at 500ms in this task; Task 18 bumps to 5s


  async def sse_generator(job_id: UUID) -> AsyncIterator[str]:
      """Yield SSE-formatted frames for a job's bank events.

      Two sources feed a shared queue:
        1. Fast path: pubsub.subscribe("job:{id}") — typical latency <100ms.
        2. Backstop: DB poll — catches any pub/sub miss, also detects
           raw-SQL and actor paths that might not publish (should be none).

      Both paths use the same envelope format; the client dedupes via
      query invalidation."""
      emit_queue: asyncio.Queue[pubsub.Envelope] = asyncio.Queue(maxsize=100)

      async def fast_path() -> None:
          async for envelope in pubsub.subscribe(pubsub.job_channel(job_id)):
              await emit_queue.put(envelope)

      async def backstop() -> None:
          async for envelope in _poll_loop(job_id, POLL_INTERVAL_SEC):
              await emit_queue.put(envelope)

      # TaskGroup guarantees both tasks are cancelled on generator close.
      async with asyncio.TaskGroup() as tg:
          tg.create_task(fast_path())
          tg.create_task(backstop())
          try:
              while True:
                  env = await emit_queue.get()
                  yield _format_sse(env)
          except asyncio.CancelledError:
              # Client disconnected — TaskGroup cancels fast_path and backstop.
              raise


  def _format_sse(env: pubsub.Envelope) -> str:
      """Format an envelope as an SSE frame."""
      import orjson
      data = orjson.dumps({
          "payload": env.payload,
          "correlation_id": env.correlation_id,
          "emitted_at": env.emitted_at,
      }).decode("utf-8")
      return f"event: {env.event}\ndata: {data}\n\n"


  async def _poll_loop(job_id: UUID, interval_sec: float) -> AsyncIterator[pubsub.Envelope]:
      """Original polling detection logic, wrapped to yield Envelopes.

      Tracks per-bank state between iterations; emits an Envelope on any
      diff. This is the backstop — same responsibility as before, just
      outputting in the new envelope format."""
      state: dict = {}  # bank_id -> {status, question_count, max_updated_at}
      while True:
          async with async_session_factory() as db:
              # ... reuse the existing query logic from the old sse_generator
              #     (fetch banks + status + question_count + max(updated_at))
              for bank in banks:
                  prev = state.get(bank.id)
                  current = (bank.status, bank.question_count, bank.max_updated_at)
                  if prev != current:
                      state[bank.id] = current
                      # Determine which event to emit:
                      if prev is None or prev[0] != current[0]:
                          event_name = pubsub.Events.BANK_STATUS_CHANGED
                      else:
                          event_name = pubsub.Events.BANK_QUESTION_UPDATED
                      yield pubsub.Envelope(
                          event=event_name,
                          payload={
                              "job_id": str(job_id),
                              "bank_id": str(bank.id),
                              "stage_id": str(bank.stage_id),
                              "source": "backstop",
                          },
                          correlation_id="backstop",  # no originating request
                          emitted_at=datetime.now(timezone.utc).isoformat(),
                      )
          await asyncio.sleep(interval_sec)
  ```

  **Important:** copy the DB query + diff logic from the existing `sse_generator` into `_poll_loop` verbatim — the behavior change is the output format (yielding Envelopes), not the detection. Also add `max(stage_questions.updated_at) per bank` to the query if it isn't already there (Task 18 verifies this).

- [ ] **Step 17.5: Run the test**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_sse.py -xvs
  ```
  Expected: PASS. The fast path delivers within <200ms.

- [ ] **Step 17.6: Run existing SSE tests**

  ```bash
  docker compose run --rm nexus pytest tests/test_jd_sse.py tests/test_question_banks_integration.py -xvs
  ```
  Expected: green. The refactor preserves the poll behavior; any regression here means the diff logic was copied incorrectly.

- [ ] **Step 17.7: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/sse.py \
          backend/nexus/tests/test_question_banks_sse.py
  git commit -m "feat(sse): add pub/sub fast path alongside existing poll

  TaskGroup fan-in: pubsub.subscribe('job:{id}') and the existing
  DB poll both feed a shared queue. Client sees events from whichever
  source arrives first. Poll interval unchanged (500ms) in this commit
  — bumped to 5s in the next task."
  ```

---

## Task 18: Bump poll backstop to 5s + include `max(updated_at)` in detection (B2.4, SSE part 2)

**Goal:** The fast path (Task 17) makes the poll unnecessary as a latency-critical path. Bump interval to 5s so the poll is pure correctness insurance. Add `max(stage_questions.updated_at)` to the per-bank tuple so UPDATE detection actually works (relies on Tasks 1+2 for `updated_at` to be trustworthy).

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/sse.py`
- Modify: `backend/nexus/tests/test_question_banks_sse.py` (add backstop-only test)

- [ ] **Step 18.1: Write the failing test**

  Append to `test_question_banks_sse.py`:
  ```python
  async def test_sse_backstop_emits_when_pubsub_unavailable(
      seed_bank, monkeypatch
  ):
      """Simulate pub/sub being unreachable — backstop poll still emits."""
      bank = seed_bank

      # Force the fast path to hang — subscribe returns an iterator that never yields.
      async def broken_subscribe(*_channels):
          while True:
              await asyncio.sleep(10.0)
              # Never yields.
          yield  # Unreachable, but required for async-generator shape.

      monkeypatch.setattr("app.pubsub.subscribe", broken_subscribe)

      received: list[str] = []

      async def consume():
          async for frame in sse.sse_generator(bank.job_id):
              received.append(frame)
              if "bank." in frame:
                  break

      consumer = asyncio.create_task(consume())

      # Directly update the bank to trigger a state change visible to the poll.
      async with async_session_factory() as db:
          async with db.begin():
              await db.execute(
                  text("UPDATE stage_questions SET text = 'edited' WHERE bank_id = :bid"),
                  {"bid": bank.id},
              )

      # The backstop is now at 5s (this task sets POLL_INTERVAL_SEC = 5).
      # For test speed, patch the interval down to 0.2s:
      monkeypatch.setattr("app.modules.question_bank.sse.POLL_INTERVAL_SEC", 0.2)

      try:
          await asyncio.wait_for(consumer, timeout=3.0)
      except asyncio.TimeoutError:
          consumer.cancel()
          pytest.fail("Backstop did not emit within 3s")

      assert any("bank.question_updated" in f for f in received)
  ```

  NOTE: the test patches `POLL_INTERVAL_SEC` to 0.2s so the full-suite runtime doesn't balloon. In production the value is 5s.

- [ ] **Step 18.2: Run — expect FAIL**

  The test fails either because (a) the poll doesn't look at `max(updated_at)` yet, so the UPDATE goes undetected, or (b) the interval is too large and the test times out before the poll runs. Both are correct failure modes for where we are.

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_sse.py::test_sse_backstop_emits_when_pubsub_unavailable -xvs
  ```

- [ ] **Step 18.3: Update `sse.py`**

  Change the interval constant and the poll query:
  ```python
  # Previously: POLL_INTERVAL_SEC = 0.5
  POLL_INTERVAL_SEC = 5.0  # fast path is pub/sub; poll is backstop


  async def _poll_loop(job_id: UUID, interval_sec: float) -> AsyncIterator[pubsub.Envelope]:
      state: dict = {}  # bank_id -> (status, question_count, max_updated_at)
      while True:
          async with async_session_factory() as db:
              # Join stage_question_banks with stage_questions to get per-bank
              # max(updated_at). Filter to banks belonging to job_id.
              rows = (await db.execute(
                  text("""
                      SELECT
                          b.id AS bank_id,
                          b.stage_id,
                          b.status,
                          b.question_count,
                          COALESCE(MAX(q.updated_at), b.updated_at) AS max_updated_at
                      FROM stage_question_banks b
                      JOIN job_pipeline_stages s ON s.id = b.stage_id
                      JOIN job_pipeline_instances i ON i.id = s.instance_id
                      LEFT JOIN stage_questions q ON q.bank_id = b.id
                      WHERE i.job_posting_id = :job_id
                      GROUP BY b.id, b.stage_id, b.status, b.question_count, b.updated_at
                  """),
                  {"job_id": job_id},
              )).all()

              for row in rows:
                  prev = state.get(row.bank_id)
                  current = (row.status, row.question_count, row.max_updated_at)
                  if prev != current:
                      state[row.bank_id] = current
                      if prev is None:
                          continue  # first observation — don't emit
                      event_name = (
                          pubsub.Events.BANK_STATUS_CHANGED
                          if prev[0] != current[0]
                          else pubsub.Events.BANK_QUESTION_UPDATED
                      )
                      yield pubsub.Envelope(
                          event=event_name,
                          payload={
                              "job_id": str(job_id),
                              "bank_id": str(row.bank_id),
                              "stage_id": str(row.stage_id),
                              "source": "backstop",
                          },
                          correlation_id="backstop",
                          emitted_at=datetime.now(timezone.utc).isoformat(),
                      )
          await asyncio.sleep(interval_sec)
  ```

  **Important:** verify the JOIN matches the actual schema (table names: `stage_question_banks`, `job_pipeline_stages`, `job_pipeline_instances`, `job_postings`). Adjust FK column names to match models.py (e.g., `job_posting_id` vs `job_id`).

- [ ] **Step 18.4: Run all SSE tests**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_sse.py tests/test_jd_sse.py tests/test_question_banks_integration.py -xvs
  ```
  Expected: all green.

- [ ] **Step 18.5: Commit**

  ```bash
  git add backend/nexus/app/modules/question_bank/sse.py \
          backend/nexus/tests/test_question_banks_sse.py
  git commit -m "feat(sse): backstop poll at 5s + max(stage_questions.updated_at) detection

  Interval bumped 500ms → 5s now that pub/sub is the fast path.
  Poll detects question edits via max(updated_at) per bank — works
  because migration 0017 + ORM onupdate guarantee updated_at bumps
  on every UPDATE."
  ```

---

## Task 19: End-to-end SSE smoke test with Redis outage

**Goal:** One integration test that exercises the full data path: mutate → publish → subscribe → SSE → client. Then kill Redis mid-test and verify the backstop delivers.

**Files:**
- Modify: `backend/nexus/tests/test_question_banks_sse.py` (add the E2E test)

- [ ] **Step 19.1: Add the E2E test**

  ```python
  async def test_e2e_mutation_to_sse_happy_path(
      client, seed_bank_with_question, tenant_and_user
  ):
      """HTTP update → publish → SSE emit end-to-end, real Redis."""
      question = seed_bank_with_question
      received: list[str] = []

      async def consume():
          async for frame in sse.sse_generator(question.job_id):
              received.append(frame)
              if "bank.question_updated" in frame:
                  break

      consumer = asyncio.create_task(consume())
      await asyncio.sleep(0.1)  # let subscribe connect

      # Real HTTP PATCH — goes through the handler, commits, publishes.
      resp = await client.patch(
          f"/api/question-banks/{question.bank_id}/questions/{question.id}",
          json={"text": "End-to-end test"},
      )
      assert resp.status_code == 200

      try:
          await asyncio.wait_for(consumer, timeout=3.0)
      except asyncio.TimeoutError:
          consumer.cancel()
          pytest.fail("SSE did not receive the mutation event end-to-end")

      # Correlation ID should be present in the emitted frame.
      assert any("correlation_id" in f for f in received)
  ```

- [ ] **Step 19.2: Run — expect PASS**

  ```bash
  docker compose run --rm nexus pytest tests/test_question_banks_sse.py::test_e2e_mutation_to_sse_happy_path -xvs
  ```

- [ ] **Step 19.3: Commit**

  ```bash
  git add backend/nexus/tests/test_question_banks_sse.py
  git commit -m "test(sse): end-to-end mutation → publish → subscribe → client"
  ```

---

## Phase 5 — Final validation

## Task 20: Full gauntlet

- [ ] **Step 20.1: Backend — full test suite + alembic check**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/backend/nexus
  docker compose run --rm nexus alembic upgrade head
  docker compose run --rm nexus alembic check
  docker compose run --rm nexus pytest -x
  ```
  Expected: migrations clean, no pending model/schema drift, all tests green.

- [ ] **Step 20.2: Frontend — full gauntlet**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2/frontend/app
  npm run type-check && npm run lint && npm run test && npm run build
  ```
  Expected: all green.

- [ ] **Step 20.3: Manual smoke tests**

  Start backend + frontend locally. In two browser tabs:

  1. **Fast path:** Open `/jobs/{id}` in tab A and tab B. In tab A, edit a question's text. Tab B refreshes within ~100ms (fast path delivery). Verify in browser devtools → Network → the SSE connection receives a `bank.question_updated` event.
  2. **Backstop:** `docker compose stop redis`. Edit another question in tab A. Tab B refreshes within ~5s (backstop delivery). Verify the frame's `source: "backstop"` payload.
  3. **Reconnect:** `docker compose start redis`. In backend logs, observe `pubsub.subscribe.reconnected` → `pubsub.subscribe.connected`. Edit a third question. Tab B refreshes within ~100ms again (fast path resumed).
  4. **Retry endpoint:** On `/jobs` index, retry a failed job. Inspect the network response — it contains `signal_count`, `needs_review_count`, `org_unit_name`, `created_by_email`, `updated_by_email` with populated values (not null/0).
  5. **Job detail:** Navigate to any job detail page. Same enrichment fields present.
  6. **Org units:** In the settings page that lists org units, verify `company_profile_completed_at` is available on the returned object (via React Query devtools or a temporary console.log).

- [ ] **Step 20.4: Push the branch**

  ```bash
  cd /home/ishant/Projects/ProjectX/.worktrees/cleanup-batch-2
  git push -u origin cleanup/batch-2-schema-events
  ```

- [ ] **Step 20.5: Done — ready for review**

  Update section 6 of the spec with a `> Status: Completed` note, same as the B1 pattern. Do that as a separate docs commit against `main` after this PR merges.

---

## Appendix: what this plan intentionally does NOT cover

From the spec section 6 (kept for traceability):

- **No new observability/metrics library.** Counters are structlog events with `metric_name=...` fields. Anything beyond that is out of scope until the team picks a Prometheus/OpenTelemetry setup.
- **No circuit breaker for pub/sub.** `publish()` is best-effort and the backstop is independent — degradation is graceful without extra machinery.
- **No Redis Streams / durable queuing.** Pub/sub is fire-and-forget. If you need guaranteed delivery, the backstop is your durability — that's by design.
- **No frontend changes to `use-questions-status-stream.ts`.** The hook already handles `bank.question_updated` and `bank.status_changed` with correct query-key invalidation (verified in B2 reconnaissance). Only the backend now actually emits these events reliably.
- **No cleanup of B1 deferred items** (`useJobStatusStream.isStreaming` init, `{status: string}` return types on 204 endpoints, remaining `window.confirm` callsites). Those are separate from B2's schema/event scope.

---

## Appendix: the BackgroundTasks + post-commit invariant

Why this matters enough to call out:

FastAPI dependency cleanup order:
1. Request arrives → dependency graph resolves → `get_tenant_db` yields a session inside `async with session.begin():`.
2. Handler runs → calls service → service executes queries on the session.
3. Handler returns the response object.
4. FastAPI executes `BackgroundTasks.add_task` queue (async).
5. FastAPI resumes the dependency generator past `yield` → `session.begin()` context exits → **COMMIT happens here**.
6. Response is flushed to the client (Starlette middleware).
7. `BackgroundTasks` are awaited sequentially by Starlette middleware → our publish runs.

The ordering of steps 5 and 7 is the crucial invariant. Step 7 happens **after** step 5 because Starlette only starts processing `BackgroundTasks` after the response is fully sent (and dependency cleanup is part of producing that response). So our publish always sees committed data.

If you ever need synchronous post-commit work (rare — used for critical ordering), the alternative is a SQLAlchemy session event listener on `after_commit`. For B2 we don't need that.

---

**End of plan.**
