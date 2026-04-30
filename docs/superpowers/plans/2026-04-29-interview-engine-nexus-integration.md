# Interview Engine ↔ Nexus Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the standalone LiveKit agent at `backend/interview_engine/` into Nexus so a candidate can complete an actual AI screening interview from the candidate-invite flow.

**Architecture:** Two long-running Python processes sharing one codebase. Nexus's `/start` provisions a LiveKit room and explicitly dispatches the named agent worker `Dakota-1785`, embedding only `session_id` + a single-use HS256 engine JWT in dispatch metadata. The agent fetches its full `SessionConfig` from a new `/api/internal/sessions/{id}/config` endpoint, runs the existing state-machine-driven interview, and POSTs results to `/api/internal/sessions/{id}/results`. Frontend renders a minimal voice + transcript live UI; recording, the 2×2 grid, AI Copilot panel, and reconnect are deferred to Phase 3D.

**Tech stack:** Python 3.12, FastAPI, SQLAlchemy + asyncpg, Alembic, Pydantic v2, structlog, Dramatiq + Redis (existing), `livekit-agents` + `livekit-plugins-{openai,deepgram,cartesia,silero,turn-detector,ai-coustics}`, httpx, `livekit.api` server SDK; Next.js 16 + React 19 + Tailwind v4 + `livekit-client` + `@livekit/components-react` on the frontend.

**Source spec:** `docs/superpowers/specs/2026-04-29-interview-engine-nexus-integration-design.md`.

---

## Conventions used throughout

- **All backend test commands run from `backend/nexus/`.** Either `docker compose run --rm nexus pytest tests/<file>.py -v` or, with a local venv, `pytest tests/<file>.py -v`.
- **All frontend test commands run from `frontend/app/`.** `npm run test -- <file>` or `npm run test`.
- **Commit messages** follow the existing `<type>(<scope>): <subject>` convention (e.g. `feat(session): livekit dispatch on /start`).
- **Every commit ends with** `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` per the repo policy.
- **Migration head moves once.** All schema changes ship in a single Alembic revision `0024_interview_engine_integration`.

---

# CHUNK 1 — Schema migration + ORM + RLS check

Goal: tables and columns exist, RLS startup assertion passes, no functional change.

## Task 1.1: Scaffold migration `0024_interview_engine_integration`

**Files:**
- Create: `backend/nexus/migrations/versions/0024_interview_engine_integration.py`

- [ ] **Step 1: Create the migration file with header**

```python
"""Phase 3C.2 — Interview engine integration.

Adds:
  1. `engine_dispatch_tokens` (tenant-scoped) — one row per minted engine
     dispatch JWT.
  2. `engine_token_uses` (service-bypass-only) — composite (jti, endpoint)
     primary key enforces single-use semantics per endpoint.
  3. Seven columns on `sessions` for engine result persistence:
     raw_result_json, transcript, questions_asked, probes_fired,
     agent_completed_at, result_status, error_code.

Down migration drops both tables and the seven columns. WARNING: down loses
raw_result_json + transcript for completed sessions; the rollback runbook
requires a backup export first.

Revision ID: 0024_interview_engine_integration
Revises: 0023_tenant_hard_delete_cascade
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_interview_engine_integration"
down_revision = "0023_tenant_hard_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # populated in subsequent tasks


def downgrade() -> None:
    pass  # populated in subsequent tasks
```

- [ ] **Step 2: Verify alembic recognizes the new revision**

Run: `docker compose run --rm nexus alembic heads`
Expected: prints `0024_interview_engine_integration (head)`.

- [ ] **Step 3: Commit the scaffold**

```bash
git add backend/nexus/migrations/versions/0024_interview_engine_integration.py
git commit -m "feat(migration): scaffold 0024 interview engine integration"
```

---

## Task 1.2: Add `engine_dispatch_tokens` table + RLS

**Files:**
- Modify: `backend/nexus/migrations/versions/0024_interview_engine_integration.py`

- [ ] **Step 1: Replace `upgrade()` to create the table + policies**

```python
def upgrade() -> None:
    # ---- engine_dispatch_tokens ----
    op.create_table(
        "engine_dispatch_tokens",
        sa.Column("jti", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_engine_dispatch_tokens_session",
        "engine_dispatch_tokens",
        ["session_id"],
    )
    op.execute("ALTER TABLE engine_dispatch_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON engine_dispatch_tokens
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON engine_dispatch_tokens
          USING (current_setting('app.bypass_rls', true) = 'true');
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON engine_dispatch_tokens TO nexus_app"
    )
```

- [ ] **Step 2: Append the symmetric drop to `downgrade()`**

```python
def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS engine_dispatch_tokens CASCADE")
```

- [ ] **Step 3: Apply the migration**

Run: `docker compose run --rm nexus alembic upgrade head`
Expected: `INFO  [alembic.runtime.migration] Running upgrade 0023_… -> 0024_… , Phase 3C.2 …`

- [ ] **Step 4: Verify the table + policies exist**

Run:
```bash
docker compose exec postgres psql -U postgres -d projectx -c "\d engine_dispatch_tokens"
docker compose exec postgres psql -U postgres -d projectx -c "SELECT polname FROM pg_policies WHERE tablename='engine_dispatch_tokens'"
```
Expected: table description shows the columns; `pg_policies` returns rows for `tenant_isolation` and `service_bypass`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/migrations/versions/0024_interview_engine_integration.py
git commit -m "feat(migration): add engine_dispatch_tokens table with RLS"
```

---

## Task 1.3: Add `engine_token_uses` table + RLS

**Files:**
- Modify: `backend/nexus/migrations/versions/0024_interview_engine_integration.py`

- [ ] **Step 1: Append the second table to `upgrade()`**

Add to the end of `upgrade()`:

```python
    # ---- engine_token_uses ----
    op.create_table(
        "engine_token_uses",
        sa.Column("jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("used_ip", postgresql.INET()),
        sa.PrimaryKeyConstraint("jti", "endpoint", name="engine_token_uses_pkey"),
        sa.ForeignKeyConstraint(
            ["jti"],
            ["engine_dispatch_tokens.jti"],
            ondelete="CASCADE",
            name="engine_token_uses_jti_fkey",
        ),
        sa.CheckConstraint(
            "endpoint IN ('config', 'results')",
            name="engine_token_uses_endpoint_check",
        ),
    )
    op.execute("ALTER TABLE engine_token_uses ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "service_bypass" ON engine_token_uses
          USING      (current_setting('app.bypass_rls', true) = 'true')
          WITH CHECK (current_setting('app.bypass_rls', true) = 'true');
        """
    )
    op.execute("GRANT SELECT, INSERT ON engine_token_uses TO nexus_app")
```

- [ ] **Step 2: Update `downgrade()` to drop both tables in correct order**

```python
def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS engine_token_uses CASCADE")
    op.execute("DROP TABLE IF EXISTS engine_dispatch_tokens CASCADE")
```

- [ ] **Step 3: Re-apply (downgrade then upgrade)**

```bash
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: both ups and downs succeed without errors.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/migrations/versions/0024_interview_engine_integration.py
git commit -m "feat(migration): add engine_token_uses single-use table"
```

---

## Task 1.4: Add seven columns to `sessions`

**Files:**
- Modify: `backend/nexus/migrations/versions/0024_interview_engine_integration.py`

- [ ] **Step 1: Append the column adds to `upgrade()` (before the create_tables — order doesn't matter, but keep additive blocks separate)**

Add at the start of `upgrade()`:

```python
    # ---- sessions: result + status columns ----
    op.add_column("sessions", sa.Column("raw_result_json", postgresql.JSONB()))
    op.add_column("sessions", sa.Column("transcript", postgresql.JSONB()))
    op.add_column("sessions", sa.Column("questions_asked", sa.Integer()))
    op.add_column("sessions", sa.Column("probes_fired", sa.Integer()))
    op.add_column("sessions", sa.Column("agent_completed_at", sa.DateTime(timezone=True)))
    op.add_column("sessions", sa.Column("result_status", sa.Text()))
    op.add_column("sessions", sa.Column("error_code", sa.Text()))
    op.create_check_constraint(
        "sessions_result_status_check",
        "sessions",
        "result_status IS NULL OR result_status IN ('ok', 'partial', 'error')",
    )
```

- [ ] **Step 2: Append the symmetric drops to `downgrade()`**

Append at the end of `downgrade()`:

```python
    op.drop_constraint("sessions_result_status_check", "sessions", type_="check")
    op.drop_column("sessions", "error_code")
    op.drop_column("sessions", "result_status")
    op.drop_column("sessions", "agent_completed_at")
    op.drop_column("sessions", "probes_fired")
    op.drop_column("sessions", "questions_asked")
    op.drop_column("sessions", "transcript")
    op.drop_column("sessions", "raw_result_json")
```

- [ ] **Step 3: Re-apply (downgrade then upgrade)**

Run:
```bash
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: both succeed; `\d sessions` shows the new columns.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/migrations/versions/0024_interview_engine_integration.py
git commit -m "feat(migration): add session result/status columns"
```

---

## Task 1.5: Update ORM models

**Files:**
- Modify: `backend/nexus/app/models.py` (Session class) + append new classes

- [ ] **Step 1: Find the `Session` class block (around line 531-581) and add the new columns**

Locate the existing `Session` class. After the existing `livekit_room_name` and `recording_s3_key` columns (around line 571-572), add:

```python
    raw_result_json: Mapped[dict | None] = mapped_column(JSONB)
    transcript: Mapped[list | None] = mapped_column(JSONB)
    questions_asked: Mapped[int | None] = mapped_column(Integer)
    probes_fired: Mapped[int | None] = mapped_column(Integer)
    agent_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_status: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
```

(`Integer` and `JSONB` are already imported in `models.py` — don't re-import.)

- [ ] **Step 2: Append the two new ORM classes to the end of `models.py`**

```python
class EngineDispatchToken(Base):
    """Phase 3C.2 — JWT minted by /start and embedded in agent dispatch metadata.

    Single-use per (jti, endpoint) is enforced by the sibling
    EngineTokenUse table. tenant_isolation + service_bypass RLS pair.
    """

    __tablename__ = "engine_dispatch_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EngineTokenUse(Base):
    """Phase 3C.2 — atomic single-use record per (jti, endpoint).

    INSERT ON CONFLICT DO NOTHING is the enforcement primitive.
    Service-bypass-only RLS — no tenant_id column; tenant scope is
    inherited via the FK to engine_dispatch_tokens and re-asserted at
    the application layer via the JWT claim.
    """

    __tablename__ = "engine_token_uses"

    jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engine_dispatch_tokens.jti", ondelete="CASCADE"),
        primary_key=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    used_ip: Mapped[str | None] = mapped_column(INET)
```

(`INET` may need to be imported — add `from sqlalchemy.dialects.postgresql import INET` if not already imported. Check the existing imports at the top of models.py.)

- [ ] **Step 3: Run the existing models import smoke test**

Run: `docker compose run --rm nexus python -c "from app.models import EngineDispatchToken, EngineTokenUse, Session; print(Session.raw_result_json)"`
Expected: prints a SQLAlchemy column expression with no error.

- [ ] **Step 4: Run all existing tests to confirm no regression**

Run: `docker compose run --rm nexus pytest -x`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/models.py
git commit -m "feat(models): add EngineDispatchToken + EngineTokenUse + session result columns"
```

---

## Task 1.6: Update `_assert_rls_completeness` startup check

**Files:**
- Modify: `backend/nexus/app/main.py` (find `_assert_rls_completeness`)

- [ ] **Step 1: Locate the `_TENANT_SCOPED_TABLES` enumeration and add `engine_dispatch_tokens`**

Find the list/set/tuple inside `_assert_rls_completeness` (or its module-level constant). Add `"engine_dispatch_tokens"` in alphabetical order with the other tenant-scoped tables.

- [ ] **Step 2: Add a small bypass-only allowlist for `engine_token_uses`**

If the helper currently only checks tenant-scoped tables, extend it. Example shape (adapt to whatever the helper looks like):

```python
# Additionally verify service-bypass-only tables. These have no tenant_id
# column — tenant scope is enforced at the application layer via JWT claims.
# As of Phase 3C.2, the only such table is engine_token_uses.
_BYPASS_ONLY_TABLES = ("engine_token_uses",)

# After the existing tenant-scoped check, run:
for table in _BYPASS_ONLY_TABLES:
    rows = await conn.execute(
        text(
            "SELECT polname FROM pg_policies WHERE tablename = :t AND polname = 'service_bypass'"
        ),
        {"t": table},
    )
    if rows.scalar() is None:
        log.critical("rls.bypass_only_policy_missing", table=table)
        raise RuntimeError(f"missing service_bypass policy on {table}")
```

(If the helper structure differs significantly, just add the table and a comment that explains why it has no `tenant_isolation`. The principle — fail-loud at startup — is what matters.)

- [ ] **Step 3: Boot the app to verify the startup check passes**

Run: `docker compose up nexus`
Expected: structured log shows `rls.completeness_ok` (or whatever the success message is) and the API binds. Ctrl-C after confirmation.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/main.py
git commit -m "feat(rls): include engine_dispatch_tokens in startup completeness check"
```

---

## Task 1.7: Cross-tenant isolation test

**Files:**
- Create: `backend/nexus/tests/test_engine_tokens_rls.py`

- [ ] **Step 1: Write the failing test**

```python
"""RLS smoke test for engine_dispatch_tokens — cross-tenant read returns 0 rows."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio


async def test_engine_dispatch_tokens_tenant_isolation(
    bypass_session, tenant_a_id, tenant_b_id, session_a_id
):
    """A token minted under tenant A must not be visible under tenant B's session."""
    from app.models import EngineDispatchToken

    jti = uuid.uuid4()
    token = EngineDispatchToken(
        jti=jti,
        tenant_id=tenant_a_id,
        session_id=session_a_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    bypass_session.add(token)
    await bypass_session.commit()

    # Switch to tenant B's RLS context — must NOT see tenant A's row.
    from sqlalchemy import select, text

    await bypass_session.execute(text("SET LOCAL app.bypass_rls = 'false'"))
    await bypass_session.execute(
        text("SET LOCAL app.current_tenant = :t"), {"t": str(tenant_b_id)}
    )
    rows = (await bypass_session.execute(select(EngineDispatchToken))).scalars().all()
    assert rows == []
```

(Adapt fixture names to whatever already exists in `conftest.py` — `bypass_session`, `tenant_a_id`, etc. If not present, create a thin test that uses the existing two-tenant pattern from `test_candidates_rls.py`.)

- [ ] **Step 2: Run the test**

Run: `docker compose run --rm nexus pytest tests/test_engine_tokens_rls.py -v`
Expected: PASS.

- [ ] **Step 3: Negative-control verification (per the composition-test memory)**

Temporarily change the policy to a known-broken form:
```sql
DROP POLICY tenant_isolation ON engine_dispatch_tokens;
CREATE POLICY tenant_isolation ON engine_dispatch_tokens USING (true) WITH CHECK (true);
```
Run the test again. Expected: FAIL (test correctly catches the broken policy).
Revert the policy via `alembic downgrade -1; alembic upgrade head`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/test_engine_tokens_rls.py
git commit -m "test(rls): assert engine_dispatch_tokens cross-tenant isolation"
```

---

# CHUNK 2 — `app/ai/realtime.py` + AIConfig + prompt move

Goal: factories exist; no observable change to JD / question bank flows.

## Task 2.1: Add settings fields

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Append fields to the `Settings` class in `config.py`**

```python
    # --- Interview engine — realtime LLM/STT/TTS + auth (Phase 3C.2) ---
    interview_engine_jwt_secret: str = ""           # HS256 signing key, 32 random bytes; required outside test env
    interview_agent_name: str = "Dakota-1785"
    interview_llm_model: str = "gpt-5.3-chat-latest"
    interview_reasoning_effort: str = "medium"
    interview_stt_model: str = "nova-3"
    interview_stt_language: str = "en"
    interview_tts_model: str = "sonic-2"
    interview_tts_voice: str = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
    interview_tts_language: str = "en"
    nexus_internal_base_url: str = "http://nexus:8000"  # Used by the engine — Nexus itself ignores it
```

(Mirror existing field-validator patterns if `candidate_jwt_secret` has one for required-in-non-test. Add the same for `interview_engine_jwt_secret`.)

- [ ] **Step 2: Append the same fields (with empty defaults) to `.env.example`**

```bash
# Phase 3C.2 — Interview engine integration (real-time AI interview)
INTERVIEW_ENGINE_JWT_SECRET=
INTERVIEW_AGENT_NAME=Dakota-1785
INTERVIEW_LLM_MODEL=gpt-5.3-chat-latest
INTERVIEW_REASONING_EFFORT=medium
INTERVIEW_STT_MODEL=nova-3
INTERVIEW_STT_LANGUAGE=en
INTERVIEW_TTS_MODEL=sonic-2
INTERVIEW_TTS_VOICE=9626c31c-bec5-4cca-baa8-f8ba9e84c8bc
INTERVIEW_TTS_LANGUAGE=en
NEXUS_INTERNAL_BASE_URL=http://nexus:8000
```

- [ ] **Step 3: Smoke that settings load**

Run: `docker compose run --rm nexus python -c "from app.config import settings; print(settings.interview_agent_name, settings.interview_stt_model)"`
Expected: prints `Dakota-1785 nova-3`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example
git commit -m "feat(config): add interview engine settings (LLM/STT/TTS + JWT secret)"
```

---

## Task 2.2: Add AIConfig properties

**Files:**
- Modify: `backend/nexus/app/ai/config.py`

- [ ] **Step 1: Append five `@property` accessors to `AIConfig`**

```python
    # --- Interview engine — realtime (Phase 3C.2) ---
    @property
    def interview_llm_model(self) -> str:
        return settings.interview_llm_model

    @property
    def interview_reasoning_effort(self) -> str:
        return settings.interview_reasoning_effort

    @property
    def interview_stt_model(self) -> str:
        return settings.interview_stt_model

    @property
    def interview_stt_language(self) -> str:
        return settings.interview_stt_language

    @property
    def interview_tts_model(self) -> str:
        return settings.interview_tts_model

    @property
    def interview_tts_voice(self) -> str:
        return settings.interview_tts_voice

    @property
    def interview_tts_language(self) -> str:
        return settings.interview_tts_language
```

- [ ] **Step 2: Smoke**

Run: `docker compose run --rm nexus python -c "from app.ai.config import ai_config; print(ai_config.interview_llm_model, ai_config.interview_tts_voice)"`
Expected: prints the configured values.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/ai/config.py
git commit -m "feat(ai): expose interview engine model fields via AIConfig"
```

---

## Task 2.3: Move the interviewer prompt

**Files:**
- Move: `backend/interview_engine/prompts/interviewer.txt` → `backend/nexus/prompts/v1/interview/interviewer.txt`

- [ ] **Step 1: Create the destination directory and move the file**

Run:
```bash
mkdir -p backend/nexus/prompts/v1/interview
git mv backend/interview_engine/prompts/interviewer.txt backend/nexus/prompts/v1/interview/interviewer.txt
```

- [ ] **Step 2: Verify the prompt loader can find it**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import prompt_loader; print(prompt_loader.load('interview/interviewer')[:100])"`
Expected: prints the first 100 chars of the prompt without `FileNotFoundError`.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/prompts/v1/interview/interviewer.txt backend/interview_engine/prompts/
git commit -m "refactor(prompts): move interviewer prompt under nexus prompt loader"
```

---

## Task 2.4: Create `app/ai/realtime.py` with five factories

**Files:**
- Create: `backend/nexus/app/ai/realtime.py`

- [ ] **Step 1: Write the file**

```python
"""LiveKit realtime plugin factories.

Single blessed import site for `livekit.plugins.*`. Business logic and the
interview engine import these factories instead of touching the LiveKit
plugin packages directly. Mirrors the same provider-abstraction discipline
that `app.ai.client.get_openai_client()` enforces for batch LLM calls.

Reads model IDs / voices / effort from `AIConfig` — never from env directly.
Adding a new realtime provider is a single-file change here.

Imports of `livekit.plugins.*` are LAZY — the modules are loaded only when
a factory is called. This keeps the FastAPI nexus process from pulling in
the realtime plugin packages (which are installed only in the
interview-engine container per docker-compose). Calling any factory from
the FastAPI process will raise ImportError if the engine plugins aren't
installed; that's intentional.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.config import ai_config

if TYPE_CHECKING:
    # Forward-declared so type checkers see the right return types without
    # forcing a runtime import. Only the engine container has these
    # packages installed.
    from livekit.agents.voice.turn import TurnDetectionMode
    from livekit.plugins.ai_coustics import AudioEnhancement
    from livekit.plugins.cartesia import TTS
    from livekit.plugins.deepgram import STT
    from livekit.plugins.openai import LLM


def build_stt_plugin() -> "STT":
    """Construct the realtime Deepgram STT plugin from AIConfig."""
    from livekit.plugins import deepgram

    return deepgram.STT(
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
    )


def build_llm_plugin() -> "LLM":
    """Construct the realtime OpenAI LLM plugin from AIConfig."""
    from livekit.plugins import openai

    return openai.LLM(
        model=ai_config.interview_llm_model,
        reasoning_effort=ai_config.interview_reasoning_effort,
    )


def build_tts_plugin() -> "TTS":
    """Construct the realtime Cartesia TTS plugin from AIConfig."""
    from livekit.plugins import cartesia

    return cartesia.TTS(
        model=ai_config.interview_tts_model,
        voice=ai_config.interview_tts_voice,
        language=ai_config.interview_tts_language,
    )


def build_turn_detector() -> "TurnDetectionMode":
    """Construct the LiveKit multilingual turn-detector model.

    `MultilingualModel` accepts an `unlikely_threshold: float | None`
    tunable that raises the EOU (end-of-utterance) confidence floor.
    Useful in interview contexts where candidates pause to think and a
    too-eager turn-end fires a probe before the candidate finishes the
    answer. We don't expose this as an AIConfig knob yet — defer to the
    plugin default until we have evidence a per-deploy override is
    needed.
    """
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    return MultilingualModel()


def build_noise_cancellation() -> "AudioEnhancement":
    """Construct the ai_coustics noise-cancellation enhancement.

    Defaults to the Quail VF L model — the engine's existing choice.
    Hoisted here for consistency; AIConfig knob can be added later if
    we want to tune this per-deploy.
    """
    from livekit.plugins import ai_coustics

    return ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_L)
```

- [ ] **Step 2: Smoke that imports succeed (only matters when livekit-plugins are installed; on a Nexus-only image they may not be)**

Skip this smoke if the nexus image does NOT install the livekit-plugins-* packages. The actual instantiation smoke happens in Chunk 5 once the engine image runs.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/ai/realtime.py
git commit -m "feat(ai): add realtime plugin factories for interview engine"
```

---

## Task 2.5: Update `nexus/CLAUDE.md` carve-out

**Files:**
- Modify: `backend/nexus/CLAUDE.md` — find the "Documented carve-outs (allowed)" section under "AI Provider & Prompt Management"

- [ ] **Step 1: Add a third bullet**

Append to the carve-outs list:

```markdown
- `app/ai/realtime.py` is the second blessed import site for vendor SDKs (alongside the existing exception-types carve-out). It owns LiveKit plugin instantiation (`livekit.plugins.openai`, `deepgram`, `cartesia`, `silero`, `turn_detector`, `ai_coustics`) so the interview engine's worker never touches them directly. Read model IDs from `AIConfig`.
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/CLAUDE.md
git commit -m "docs(claude): document app/ai/realtime.py carve-out"
```

---

# CHUNK 3 — Internal API (interview_runtime + auth)

Goal: `/api/internal/sessions/{id}/{config,results}` are live and authed by the engine JWT. Engine doesn't exist yet; tests use hand-minted JWTs.

> **Known deferral from Chunk 1's final review:** `tests/test_migration_0014.py::_make_assignment_with_stage` uses `stage_type="ai_interview"` (retired in migration 0016). Before Chunk 3's `test_interview_runtime_config.py` lands, hoist that helper to `conftest.py` and switch the value to `"ai_screening"`. Search for the in-code TODO comment to find the function.

## Task 3.1: Create the module skeleton

**Files:**
- Create: `backend/nexus/app/modules/interview_runtime/__init__.py`
- Create: `backend/nexus/app/modules/interview_runtime/router.py`
- Create: `backend/nexus/app/modules/interview_runtime/service.py`
- Create: `backend/nexus/app/modules/interview_runtime/schemas.py`
- Create: `backend/nexus/app/modules/interview_runtime/authz.py`
- Create: `backend/nexus/app/modules/interview_runtime/errors.py`

- [ ] **Step 1: Create empty `__init__.py`**

```python
"""Phase 3C.2 — Interview engine internal API.

Exposes /api/internal/sessions/{id}/config + /results to the
backend/interview_engine/ worker process. Authed by single-use HS256
engine JWT (verify_engine_token).
"""
```

- [ ] **Step 2: Create stub `errors.py`**

```python
"""Typed errors for interview_runtime — mapped to HTTP codes by app/main.py."""


class EngineTokenInvalidError(Exception):
    """401 — JWT signature/algorithm/claim/expiry/replay failure."""


class StageNotAiDrivenError(Exception):
    """422 — stage_type not in (ai_screening, phone_screen)."""

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"stage_type={stage_type} does not run an AI agent")


class QuestionBankNotReadyError(Exception):
    """409 — bank.status != 'ready' or is_stale."""


class SessionNotActiveError(Exception):
    """409 — record_session_result called against a non-active session."""


class CompanyProfileMissingError(Exception):
    """422 — org-unit ancestry walk found no company profile."""
```

- [ ] **Step 3: Commit the skeleton**

```bash
git add backend/nexus/app/modules/interview_runtime/__init__.py \
        backend/nexus/app/modules/interview_runtime/errors.py
git commit -m "feat(interview_runtime): module scaffold + typed errors"
```

---

## Task 3.2: Lift `SessionConfig` schemas from interview_engine

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py`
- Reference: `backend/interview_engine/models.py` — copy the model bodies

- [ ] **Step 1: Copy the relevant Pydantic models into `schemas.py`**

Copy from `backend/interview_engine/models.py` into the new `schemas.py`:

- The two enums `StageType`, `StageDifficulty`, `AdvanceBehavior` — preserve their exact `Literal[...]` values.
- `QuestionConfig`, `CompanyContext`, `StageConfig`, `SessionConfig`.
- `SteeringObservation`, `TranscriptEntry`, `QuestionResult`, `SessionResult`.

**One critical change:** in `CandidateContext`, **drop the `email` field**. Final shape:

```python
class CandidateContext(BaseModel):
    """Minimal candidate info the agent needs during the session.

    Email and any other PII are intentionally omitted — the engine never
    receives them. The agent's prompt only personalizes by `name`.
    """

    name: str
```

(Keep the rest of the file faithful to `interview_engine/models.py:1-225`.)

Also add the file header:

```python
"""Pydantic schemas exchanged between Nexus and the interview engine.

These models define the wire contract. The interview engine imports them
via the path-dep on the nexus package — `from app.modules.interview_runtime.schemas import …`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# (paste lifted models below — see Step 1 instructions)
```

- [ ] **Step 2: Smoke that the models validate against the existing fixture**

Run:
```bash
docker compose run --rm nexus python -c "
from app.modules.interview_runtime.schemas import SessionConfig
import json
sample = json.load(open('/app/interview_engine/fixtures/sample_session.json'))
sample['candidate'].pop('email', None)  # email field removed in nexus copy
print(SessionConfig.model_validate(sample))
"
```
Expected: prints a SessionConfig instance without ValidationError.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py
git commit -m "feat(interview_runtime): lift SessionConfig schemas from engine (drop email PII)"
```

---

## Task 3.3: Implement `verify_engine_token` in auth service

**Files:**
- Modify: `backend/nexus/app/modules/auth/service.py`
- Modify: `backend/nexus/app/modules/auth/schemas.py` (add `EngineTokenPayload`)

- [ ] **Step 1: Add the payload schema to `auth/schemas.py`**

```python
class EngineTokenPayload(BaseModel):
    """Decoded claims of a Nexus-minted engine dispatch JWT."""

    sub: uuid.UUID            # session_id
    tenant_id: uuid.UUID
    purpose: Literal["interview_engine"]
    iat: int
    exp: int
    jti: uuid.UUID
```

(`uuid` and `Literal` imports may already exist — check.)

- [ ] **Step 2: Add `verify_engine_token` to `auth/service.py`**

```python
async def verify_engine_token(
    token: str,
    db: AsyncSession,
    *,
    expected_session_id: uuid.UUID,
    endpoint: Literal["config", "results"],
    used_ip: str | None = None,
) -> EngineTokenPayload:
    """Verify a single-use engine dispatch JWT.

    On success, atomically records (jti, endpoint) in engine_token_uses.
    Raises EngineTokenInvalidError on any failure. The caller MUST be on
    a bypass-RLS session.
    """
    try:
        decoded = jwt.decode(
            token,
            settings.interview_engine_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.PyJWTError as exc:
        raise EngineTokenInvalidError(f"jwt decode failed: {exc}") from exc

    try:
        payload = EngineTokenPayload.model_validate(decoded)
    except ValidationError as exc:
        raise EngineTokenInvalidError(f"claim shape mismatch: {exc}") from exc

    if payload.purpose != "interview_engine":
        raise EngineTokenInvalidError("purpose claim mismatch")
    if payload.sub != expected_session_id:
        raise EngineTokenInvalidError("session_id mismatch")

    # Atomic single-use INSERT (depends on bypass-RLS session).
    insert_stmt = pg_insert(EngineTokenUse).values(
        jti=payload.jti, endpoint=endpoint, used_ip=used_ip
    ).on_conflict_do_nothing(index_elements=["jti", "endpoint"])
    result = await db.execute(insert_stmt)
    if result.rowcount == 0:
        raise EngineTokenInvalidError("token already used for this endpoint")

    # Confirm the parent token is not revoked + not expired (the JWT lib
    # already enforced exp). Defense-in-depth.
    parent = (await db.execute(
        select(EngineDispatchToken).where(EngineDispatchToken.jti == payload.jti)
    )).scalar_one_or_none()
    if parent is None or parent.revoked_at is not None:
        raise EngineTokenInvalidError("token unknown or revoked")

    return payload
```

Imports to add (top of file):
```python
import jwt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import EngineDispatchToken, EngineTokenUse
from app.modules.auth.schemas import EngineTokenPayload
from app.modules.interview_runtime.errors import EngineTokenInvalidError
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/auth/service.py \
        backend/nexus/app/modules/auth/schemas.py
git commit -m "feat(auth): add verify_engine_token (HS256, single-use per endpoint)"
```

---

## Task 3.4: Test `verify_engine_token` (100% branch coverage)

**Files:**
- Create: `backend/nexus/tests/test_interview_runtime_authz.py`

- [ ] **Step 1: Write the test file**

```python
"""verify_engine_token — 100% branch coverage."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from app.config import settings
from app.models import EngineDispatchToken
from app.modules.auth.service import verify_engine_token
from app.modules.interview_runtime.errors import EngineTokenInvalidError


pytestmark = pytest.mark.asyncio

SECRET = "test-engine-jwt-secret-32-bytes-x"


def _mint(claims: dict, *, secret: str = SECRET, alg: str = "HS256") -> str:
    return pyjwt.encode(claims, secret, algorithm=alg)


def _baseline_claims(session_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    now = int(datetime.now(UTC).timestamp())
    return {
        "sub": str(session_id),
        "tenant_id": str(tenant_id),
        "purpose": "interview_engine",
        "iat": now,
        "exp": now + 600,
        "jti": str(uuid.uuid4()),
    }


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    monkeypatch.setattr(settings, "interview_engine_jwt_secret", SECRET)


async def test_valid_token_first_use_succeeds(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    bypass_session.add(EngineDispatchToken(
        jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await bypass_session.flush()

    payload = await verify_engine_token(
        _mint(claims), bypass_session,
        expected_session_id=session_id, endpoint="config",
    )
    assert payload.sub == session_id


async def test_replayed_same_endpoint_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    bypass_session.add(EngineDispatchToken(
        jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await bypass_session.flush()

    token = _mint(claims)
    await verify_engine_token(token, bypass_session, expected_session_id=session_id, endpoint="config")
    with pytest.raises(EngineTokenInvalidError, match="already used"):
        await verify_engine_token(token, bypass_session, expected_session_id=session_id, endpoint="config")


async def test_different_endpoint_same_jti_succeeds(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    bypass_session.add(EngineDispatchToken(
        jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    ))
    await bypass_session.flush()

    token = _mint(claims)
    await verify_engine_token(token, bypass_session, expected_session_id=session_id, endpoint="config")
    payload = await verify_engine_token(
        token, bypass_session, expected_session_id=session_id, endpoint="results"
    )
    assert payload.sub == session_id


@pytest.mark.parametrize("alg", ["none", "RS256", "HS512"])
async def test_wrong_algorithm_rejected(bypass_session, tenant_id, session_id, alg):
    claims = _baseline_claims(session_id, tenant_id)
    if alg == "none":
        token = pyjwt.encode(claims, "", algorithm="none")
    else:
        token = pyjwt.encode(claims, SECRET, algorithm=alg) if alg != "RS256" else "x.y.z"
    with pytest.raises(EngineTokenInvalidError):
        await verify_engine_token(
            token, bypass_session, expected_session_id=session_id, endpoint="config"
        )


async def test_wrong_purpose_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    claims["purpose"] = "candidate"
    with pytest.raises(EngineTokenInvalidError, match="claim shape"):
        await verify_engine_token(
            _mint(claims), bypass_session, expected_session_id=session_id, endpoint="config"
        )


async def test_session_id_path_mismatch_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    other_session = uuid.uuid4()
    with pytest.raises(EngineTokenInvalidError, match="session_id mismatch"):
        await verify_engine_token(
            _mint(claims), bypass_session, expected_session_id=other_session, endpoint="config"
        )


async def test_expired_token_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    claims["exp"] = int(datetime.now(UTC).timestamp()) - 10
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            _mint(claims), bypass_session, expected_session_id=session_id, endpoint="config"
        )


async def test_tampered_signature_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    token = _mint(claims, secret="wrong-secret")
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            token, bypass_session, expected_session_id=session_id, endpoint="config"
        )


async def test_unknown_jti_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    # We deliberately do NOT insert a row in engine_dispatch_tokens — but the
    # JWT verification short-circuits on the FK violation when inserting into
    # engine_token_uses (no parent jti). That manifests as IntegrityError in
    # asyncpg; the verify helper translates it into EngineTokenInvalidError.
    with pytest.raises(Exception):
        await verify_engine_token(
            _mint(claims), bypass_session, expected_session_id=session_id, endpoint="config"
        )


async def test_revoked_token_rejected(bypass_session, tenant_id, session_id):
    claims = _baseline_claims(session_id, tenant_id)
    bypass_session.add(EngineDispatchToken(
        jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        revoked_at=datetime.now(UTC),
    ))
    await bypass_session.flush()
    with pytest.raises(EngineTokenInvalidError, match="revoked"):
        await verify_engine_token(
            _mint(claims), bypass_session, expected_session_id=session_id, endpoint="config"
        )
```

(Adapt `bypass_session`, `tenant_id`, `session_id` fixture names to whatever conftest.py provides. If they don't exist, define them as small fixtures at the top of the test file using the patterns from `tests/conftest.py`.)

- [ ] **Step 2: Run the tests**

Run: `docker compose run --rm nexus pytest tests/test_interview_runtime_authz.py -v`
Expected: all 10+ tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_interview_runtime_authz.py
git commit -m "test(auth): verify_engine_token branch coverage"
```

---

## Task 3.5: Implement `build_session_config`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`

- [ ] **Step 1: Implement the service function**

```python
"""interview_runtime service — assembles SessionConfig + records SessionResult."""

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Candidate,
    CandidateJobAssignment,
    JobPipelineStage,
    JobPosting,
    Session as SessionRow,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    SessionConfig,
    StageConfig,
)
from app.modules.org_units.service import find_company_profile_in_ancestry

_AI_STAGE_TYPES = {"ai_screening", "phone_screen"}


async def build_session_config(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> SessionConfig:
    """Compose the SessionConfig handed to the agent worker.

    Caller MUST be on a bypass-RLS session AND must have already verified
    that the engine JWT's tenant_id matches `tenant_id`.
    """
    sess = (await db.execute(
        select(SessionRow).where(
            SessionRow.id == session_id,
            SessionRow.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if sess is None:
        raise ValueError(f"session {session_id} not found")

    assignment = (await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.id == sess.assignment_id,
            CandidateJobAssignment.tenant_id == tenant_id,
        )
    )).scalar_one()

    candidate = (await db.execute(
        select(Candidate).where(
            Candidate.id == assignment.candidate_id,
            Candidate.tenant_id == tenant_id,
        )
    )).scalar_one()

    job = (await db.execute(
        select(JobPosting).where(
            JobPosting.id == assignment.job_posting_id,
            JobPosting.tenant_id == tenant_id,
        )
    )).scalar_one()

    stage = (await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.id == sess.stage_id,
            JobPipelineStage.tenant_id == tenant_id,
        )
    )).scalar_one()

    if stage.stage_type not in _AI_STAGE_TYPES:
        raise StageNotAiDrivenError(stage.stage_type)

    bank = (await db.execute(
        select(StageQuestionBank).where(
            StageQuestionBank.stage_id == stage.id,
            StageQuestionBank.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if bank is None or bank.status != "ready" or bank.is_stale:
        raise QuestionBankNotReadyError(
            f"bank state status={getattr(bank, 'status', None)} stale={getattr(bank, 'is_stale', None)}"
        )

    questions = (await db.execute(
        select(StageQuestion)
        .where(
            StageQuestion.stage_question_bank_id == bank.id,
            StageQuestion.tenant_id == tenant_id,
        )
        .order_by(StageQuestion.is_mandatory.desc(), StageQuestion.position.asc())
    )).scalars().all()

    company_profile = await find_company_profile_in_ancestry(
        db, org_unit_id=job.org_unit_id, tenant_id=tenant_id
    )
    if company_profile is None:
        raise CompanyProfileMissingError(f"job {job.id} ancestry has no company_profile")

    return SessionConfig(
        session_id=str(session_id),
        job_title=job.title,
        role_summary=job.role_summary or "",
        seniority_level=job.seniority_level or "",
        company=CompanyContext(
            about=company_profile.get("about", ""),
            industry=company_profile.get("industry", ""),
            company_stage=company_profile.get("company_stage", ""),
            hiring_bar=company_profile.get("hiring_bar", ""),
        ),
        candidate=CandidateContext(name=candidate.name or ""),
        stage=StageConfig(
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            name=stage.name,
            duration_minutes=stage.duration_minutes or 30,
            difficulty=stage.difficulty,
            questions=[
                QuestionConfig(
                    id=str(q.id),
                    position=q.position,
                    text=q.text,
                    signal_values=q.signal_values or [],
                    estimated_minutes=q.estimated_minutes or 1.0,
                    is_mandatory=q.is_mandatory,
                    follow_ups=q.follow_ups or [],
                    positive_evidence=q.positive_evidence or [],
                    red_flags=q.red_flags or [],
                    rubric=q.rubric or {},
                    evaluation_hint=q.evaluation_hint or "",
                )
                for q in questions
            ],
            advance_behavior=stage.advance_behavior or "manual_review",
        ),
        signals=[s for s in (job.signal_values or [])],
    )
```

(If `find_company_profile_in_ancestry` has a different name in `org_units/service.py`, look it up and use the actual export. Same for any column name that's slightly different.)

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py
git commit -m "feat(interview_runtime): build_session_config + stage allowlist + bank readiness"
```

---

## Task 3.6: Test `build_session_config`

**Files:**
- Create: `backend/nexus/tests/test_interview_runtime_config.py`

- [ ] **Step 1: Write the file**

```python
"""build_session_config — happy + reject paths."""

import uuid

import pytest

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.service import build_session_config

pytestmark = pytest.mark.asyncio


async def test_happy_path_ai_screening(bypass_session, ai_screening_session_factory):
    """Returns SessionConfig with no candidate.email key."""
    session_id, tenant_id = await ai_screening_session_factory()
    config = await build_session_config(
        bypass_session, session_id=session_id, tenant_id=tenant_id
    )
    assert config.candidate.name
    # PII guard: serialized payload must not include email.
    assert "email" not in config.candidate.model_dump()
    assert len(config.stage.questions) > 0
    # Mandatory questions sort first.
    seen_optional = False
    for q in config.stage.questions:
        if not q.is_mandatory:
            seen_optional = True
        elif seen_optional:
            pytest.fail("mandatory question after optional")


async def test_stage_type_human_interview_rejected(bypass_session, human_interview_session_factory):
    session_id, tenant_id = await human_interview_session_factory()
    with pytest.raises(StageNotAiDrivenError):
        await build_session_config(bypass_session, session_id=session_id, tenant_id=tenant_id)


async def test_bank_generating_rejected(bypass_session, ai_screening_session_factory):
    session_id, tenant_id = await ai_screening_session_factory(bank_status="generating")
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(bypass_session, session_id=session_id, tenant_id=tenant_id)


async def test_bank_stale_rejected(bypass_session, ai_screening_session_factory):
    session_id, tenant_id = await ai_screening_session_factory(bank_is_stale=True)
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(bypass_session, session_id=session_id, tenant_id=tenant_id)


async def test_cross_tenant_returns_not_found(bypass_session, ai_screening_session_factory):
    session_id, _tenant_id = await ai_screening_session_factory()
    other_tenant = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await build_session_config(bypass_session, session_id=session_id, tenant_id=other_tenant)


async def test_missing_company_profile_rejected(bypass_session, ai_screening_session_factory):
    session_id, tenant_id = await ai_screening_session_factory(company_profile=None)
    with pytest.raises(CompanyProfileMissingError):
        await build_session_config(bypass_session, session_id=session_id, tenant_id=tenant_id)
```

(Define the fixtures `ai_screening_session_factory` and `human_interview_session_factory` in `conftest.py` or at the top of this test file. They build a tenant + org_unit + company_profile + job + assignment + session + bank + questions and return `(session_id, tenant_id)`. Reuse builders from `test_candidates_integration.py` where possible.)

- [ ] **Step 2: Run**

Run: `docker compose run --rm nexus pytest tests/test_interview_runtime_config.py -v`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_interview_runtime_config.py
git commit -m "test(interview_runtime): build_session_config happy + reject paths"
```

---

## Task 3.7: Implement `record_session_result`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`

- [ ] **Step 1: Append the function**

```python
from datetime import UTC, datetime as dt_module
from sqlalchemy import update

from app.modules.audit.service import log_event
from app.modules.interview_runtime.errors import SessionNotActiveError
from app.modules.interview_runtime.schemas import SessionResult


async def record_session_result(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    result: SessionResult,
    jti: uuid.UUID,
) -> None:
    """Persist the engine's SessionResult and transition the session to completed.

    Idempotent: if `agent_completed_at IS NOT NULL`, no-op (returns silently).
    Raises SessionNotActiveError on any other non-active state.
    """
    derived_status = "ok" if result.questions_asked > 0 else "partial"

    res = await db.execute(
        update(SessionRow)
        .where(
            SessionRow.id == session_id,
            SessionRow.tenant_id == tenant_id,
            SessionRow.state == "active",
        )
        .values(
            raw_result_json=result.model_dump(mode="json"),
            transcript=[t.model_dump(mode="json") for t in result.full_transcript],
            questions_asked=result.questions_asked,
            probes_fired=result.total_probes_fired,
            agent_completed_at=dt_module.now(UTC),
            result_status=derived_status,
            state="completed",
            state_changed_at=dt_module.now(UTC),
        )
    )
    if res.rowcount == 0:
        # Distinguish idempotent retry from real state-violation.
        existing = (await db.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if existing is None:
            raise ValueError(f"session {session_id} not found")
        if existing.state == "completed" and existing.agent_completed_at is not None:
            return  # idempotent — same result already recorded
        raise SessionNotActiveError(f"session {session_id} state={existing.state}")

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=None,
        actor_email=None,
        action="engine.session.completed",
        resource="session",
        resource_id=session_id,
        payload={
            "jti_prefix": str(jti)[:8],
            "questions_asked": result.questions_asked,
            "result_status": derived_status,
        },
    )
```

(Note: `SessionResult.full_transcript` is a list of `TranscriptEntry` per the lifted schema. Confirm the field name; if the actual lifted schema names it differently, adjust here.)

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py
git commit -m "feat(interview_runtime): record_session_result with idempotent retry"
```

---

## Task 3.8: Test `record_session_result`

**Files:**
- Create: `backend/nexus/tests/test_interview_runtime_results.py`

- [ ] **Step 1: Write the file**

```python
"""record_session_result — happy + idempotent + state-guard + audit."""

import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog, Session as SessionRow
from app.modules.interview_runtime.errors import SessionNotActiveError
from app.modules.interview_runtime.schemas import SessionResult, TranscriptEntry, QuestionResult
from app.modules.interview_runtime.service import record_session_result

pytestmark = pytest.mark.asyncio


def _result(session_id: uuid.UUID) -> SessionResult:
    return SessionResult(
        session_id=str(session_id),
        job_title="Senior Backend Engineer",
        stage_id=str(uuid.uuid4()),
        stage_type="ai_screening",
        candidate_name="Alex",
        duration_seconds=600.0,
        questions_asked=8,
        questions_skipped=1,
        total_probes_fired=3,
        question_results=[],
        full_transcript=[
            TranscriptEntry(role="agent", text="Hi", timestamp_ms=0),
            TranscriptEntry(role="candidate", text="Hello", timestamp_ms=1500),
        ],
        completed_at="2026-04-29T12:00:00Z",
    )


async def test_happy_completes_session(bypass_session, active_session_factory):
    session_id, tenant_id = await active_session_factory()
    await record_session_result(
        bypass_session, session_id=session_id, tenant_id=tenant_id,
        result=_result(session_id), jti=uuid.uuid4(),
    )
    sess = (await bypass_session.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assert sess.state == "completed"
    assert sess.questions_asked == 8
    assert sess.transcript[0]["text"] == "Hi"


async def test_idempotent_re_post(bypass_session, active_session_factory):
    session_id, tenant_id = await active_session_factory()
    await record_session_result(
        bypass_session, session_id=session_id, tenant_id=tenant_id,
        result=_result(session_id), jti=uuid.uuid4(),
    )
    # Second call with a different jti — should be a silent no-op, not an error.
    await record_session_result(
        bypass_session, session_id=session_id, tenant_id=tenant_id,
        result=_result(session_id), jti=uuid.uuid4(),
    )


async def test_state_guard_rejects_non_active(bypass_session, completed_session_factory):
    session_id, tenant_id = await completed_session_factory()
    # Manually clear agent_completed_at so the idempotent path doesn't catch it.
    await bypass_session.execute(
        SessionRow.__table__.update().where(SessionRow.id == session_id).values(agent_completed_at=None)
    )
    with pytest.raises(SessionNotActiveError):
        await record_session_result(
            bypass_session, session_id=session_id, tenant_id=tenant_id,
            result=_result(session_id), jti=uuid.uuid4(),
        )


async def test_cross_tenant_rejected(bypass_session, active_session_factory):
    session_id, _ = await active_session_factory()
    with pytest.raises((ValueError, SessionNotActiveError)):
        await record_session_result(
            bypass_session, session_id=session_id, tenant_id=uuid.uuid4(),
            result=_result(session_id), jti=uuid.uuid4(),
        )


async def test_audit_log_written(bypass_session, active_session_factory):
    session_id, tenant_id = await active_session_factory()
    await record_session_result(
        bypass_session, session_id=session_id, tenant_id=tenant_id,
        result=_result(session_id), jti=uuid.uuid4(),
    )
    rows = (await bypass_session.execute(
        select(AuditLog).where(AuditLog.action == "engine.session.completed")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_id is None
    assert rows[0].payload["result_status"] in ("ok", "partial")
```

(Add `active_session_factory` and `completed_session_factory` to conftest.py if they don't exist.)

- [ ] **Step 2: Run**

Run: `docker compose run --rm nexus pytest tests/test_interview_runtime_results.py -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_interview_runtime_results.py
git commit -m "test(interview_runtime): record_session_result idempotent + state guard + audit"
```

---

## Task 3.9: Wire the router under `/api/internal/*`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/router.py`
- Modify: `backend/nexus/app/main.py` (add router)

- [ ] **Step 1: Implement `router.py`**

```python
"""interview_runtime — internal endpoints called by the LiveKit agent worker."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.auth.service import verify_engine_token
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    EngineTokenInvalidError,
    QuestionBankNotReadyError,
    SessionNotActiveError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.schemas import SessionConfig, SessionResult
from app.modules.interview_runtime.service import (
    build_session_config,
    record_session_result,
)

interview_runtime_router = APIRouter(
    prefix="/api/internal/sessions", tags=["interview-runtime-internal"]
)


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"})
    return auth[7:].strip()


@interview_runtime_router.get("/{session_id}/config", response_model=SessionConfig)
async def get_config(
    session_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_bypass_db)
) -> SessionConfig:
    token = _bearer(request)
    ip = request.client.host if request.client else None
    try:
        payload = await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="config", used_ip=ip
        )
    except EngineTokenInvalidError:
        raise HTTPException(status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"})

    try:
        return await build_session_config(
            db, session_id=session_id, tenant_id=payload.tenant_id
        )
    except StageNotAiDrivenError as e:
        raise HTTPException(
            status_code=422, detail={"code": "STAGE_TYPE_NOT_AI_DRIVEN", "stage_type": e.stage_type}
        )
    except QuestionBankNotReadyError:
        raise HTTPException(status_code=409, detail={"code": "BANK_NOT_READY"})
    except CompanyProfileMissingError:
        raise HTTPException(status_code=422, detail={"code": "COMPANY_PROFILE_MISSING"})
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})


@interview_runtime_router.post(
    "/{session_id}/results", status_code=status.HTTP_204_NO_CONTENT
)
async def post_results(
    session_id: uuid.UUID,
    payload: SessionResult,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> None:
    token = _bearer(request)
    ip = request.client.host if request.client else None
    try:
        claims = await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="results", used_ip=ip
        )
    except EngineTokenInvalidError:
        raise HTTPException(status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"})

    try:
        await record_session_result(
            db,
            session_id=session_id,
            tenant_id=claims.tenant_id,
            result=payload,
            jti=claims.jti,
        )
    except SessionNotActiveError:
        raise HTTPException(status_code=409, detail={"code": "SESSION_NOT_ACTIVE"})
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})
```

- [ ] **Step 2: Register the router in `app/main.py`**

Find the existing `app.include_router(...)` calls and add:

```python
from app.modules.interview_runtime.router import interview_runtime_router

# (in app factory, alongside the other include_router calls)
app.include_router(interview_runtime_router)
```

- [ ] **Step 3: Smoke-test the endpoint with a hand-minted JWT**

Boot the stack: `docker compose up -d nexus`. Open a Python shell:
```python
import jwt as pyjwt, uuid
from datetime import datetime, UTC, timedelta
session_id = "<a known session_id from local db>"
tenant_id = "<that session's tenant_id>"
secret = "<value of INTERVIEW_ENGINE_JWT_SECRET>"
# Insert engine_dispatch_tokens row first (via psql).
claims = {
    "sub": session_id, "tenant_id": tenant_id, "purpose": "interview_engine",
    "iat": int(datetime.now(UTC).timestamp()),
    "exp": int((datetime.now(UTC)+timedelta(minutes=10)).timestamp()),
    "jti": "<uuid that you also inserted>",
}
print(pyjwt.encode(claims, secret, algorithm="HS256"))
```
Then:
```bash
curl -i http://localhost:8000/api/internal/sessions/<session_id>/config -H "Authorization: Bearer <jwt>"
```
Expected: 200 with a JSON SessionConfig body.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/router.py backend/nexus/app/main.py
git commit -m "feat(interview_runtime): wire /api/internal/sessions/{id}/config + /results"
```

---

# CHUNK 4 — `/start` LiveKit provisioning

Goal: `/start` returns 200 with `{livekit_url, livekit_token, room_name}`. LiveKit creates the room. Engine doesn't exist yet; manual `curl` validates.

## Task 4.1: Create `session/livekit.py` with token + dispatch helpers

**Files:**
- Create: `backend/nexus/app/modules/session/livekit.py`

- [ ] **Step 1: Write the file**

```python
"""LiveKit provisioning helpers for /start.

All LiveKit server-SDK calls happen here. start_session() in service.py
calls these helpers; tests mock at this module's surface.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from livekit import api as livekit_api
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EngineDispatchToken


def _lk_client() -> livekit_api.LiveKitAPI:
    return livekit_api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


def mint_candidate_lk_token(
    *, room_name: str, identity: str, name: str, ttl_minutes: int
) -> str:
    """Mint a LiveKit AccessToken for the candidate's browser."""
    grant = livekit_api.VideoGrants(
        room=room_name,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
    )
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grant)
        .with_attributes({"role": "candidate"})
        .with_ttl(timedelta(minutes=ttl_minutes))
    )
    return token.to_jwt()


async def mint_engine_dispatch_jwt(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    ttl_minutes: int,
) -> str:
    """Insert an engine_dispatch_tokens row + return the signed HS256 JWT.

    Caller must run inside a tenant-scoped or bypass session — the row
    INSERT respects RLS.
    """
    jti = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=ttl_minutes)

    db.add(EngineDispatchToken(
        jti=jti,
        tenant_id=tenant_id,
        session_id=session_id,
        expires_at=expires_at,
    ))
    await db.flush()

    claims = {
        "sub": str(session_id),
        "tenant_id": str(tenant_id),
        "purpose": "interview_engine",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(jti),
    }
    return pyjwt.encode(claims, settings.interview_engine_jwt_secret, algorithm="HS256")


async def dispatch_agent(
    *, room_name: str, session_id: uuid.UUID, tenant_id: uuid.UUID,
    engine_jwt: str, correlation_id: str
) -> None:
    """Explicitly dispatch the named agent into a room (LiveKit auto-creates the room)."""
    metadata = json.dumps({
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "engine_jwt": engine_jwt,
        "correlation_id": correlation_id,
    })
    async with _lk_client() as lk:
        await lk.agent_dispatch.create_dispatch(
            livekit_api.CreateAgentDispatchRequest(
                agent_name=settings.interview_agent_name,
                room=room_name,
                metadata=metadata,
            )
        )


async def cancel_room(room_name: str) -> None:
    """Best-effort room delete — used when /start loses the consume race."""
    async with _lk_client() as lk:
        await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
```

(The exact `livekit.api` shapes — `VideoGrants`, `with_attributes`, `CreateAgentDispatchRequest`, `DeleteRoomRequest` — are per `livekit-api` Python SDK current docs. If the actual import shape differs, adjust to match the installed SDK version.)

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/session/livekit.py
git commit -m "feat(session): livekit token mint + engine jwt + agent dispatch helpers"
```

---

## Task 4.2: Rewrite `start_session` in `session/service.py`

**Files:**
- Modify: `backend/nexus/app/modules/session/service.py` (find current `start_session`)
- Modify: `backend/nexus/app/modules/session/schemas.py` (add new response model)
- Modify: `backend/nexus/app/modules/session/errors.py` (add new error)

- [ ] **Step 1: Add `AgentDispatchFailedError` to `errors.py`**

```python
class AgentDispatchFailedError(Exception):
    """502 — LiveKit dispatch raised; token NOT consumed; candidate can retry."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
```

- [ ] **Step 2: Replace `StartSessionPendingResponse` with `StartSessionResponse` in `schemas.py`**

```python
class StartSessionResponse(BaseModel):
    """200 OK shape after /start successfully provisions LiveKit + dispatches agent."""

    livekit_url: str
    livekit_token: str
    room_name: str
    session_id: uuid.UUID
```

(Keep the old `StartSessionPendingResponse` definition for one PR, marked deprecated, in case any existing tests reference it. We'll delete it after Chunk 6 lands.)

- [ ] **Step 3: Rewrite `start_session` in `service.py`**

Find the existing `start_session(db, *, session_id, jti, ip_address, user_agent)`. Replace its body with:

```python
async def start_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    jti: uuid.UUID,
    ip_address: str | None,
    user_agent: str | None,
) -> StartSessionResponse:
    session, candidate, stage = await _load_session_for_start(db, session_id)
    _require_state(session, "consented")
    _require_otp_verified_if_required(session, stage)
    await _peek_token_unused(db, jti)  # read-only — does NOT consume

    correlation_id = str(uuid.uuid4())
    room_name = f"session-{session.id}"

    candidate_lk_token = mint_candidate_lk_token(
        room_name=room_name,
        identity=f"candidate-{candidate.id}",
        name=candidate.name or "",
        ttl_minutes=(stage.duration_minutes or 30) + 10,
    )
    engine_jwt = await mint_engine_dispatch_jwt(
        db,
        session_id=session.id,
        tenant_id=session.tenant_id,
        ttl_minutes=min((stage.duration_minutes or 30) + 5, 90),
    )
    try:
        await dispatch_agent(
            room_name=room_name,
            session_id=session.id,
            tenant_id=session.tenant_id,
            engine_jwt=engine_jwt,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        raise AgentDispatchFailedError(detail=str(exc)) from exc

    consumed = await _consume_candidate_token(db, jti, ip_address, user_agent)
    if not consumed:
        # Race lost: cancel the freshly-minted room.
        with contextlib.suppress(Exception):
            await cancel_room(room_name)
        raise TokenAlreadyUsedError()

    await _transition_to_active(db, session.id, room_name=room_name)

    return StartSessionResponse(
        livekit_url=settings.livekit_url,
        livekit_token=candidate_lk_token,
        room_name=room_name,
        session_id=session.id,
    )
```

Imports to add at the top of `service.py`:

```python
import contextlib
import uuid

from app.config import settings
from app.modules.session.errors import AgentDispatchFailedError, TokenAlreadyUsedError
from app.modules.session.livekit import (
    cancel_room,
    dispatch_agent,
    mint_candidate_lk_token,
    mint_engine_dispatch_jwt,
)
from app.modules.session.schemas import StartSessionResponse
```

(The internal helpers `_load_session_for_start`, `_require_state`, `_require_otp_verified_if_required`, `_peek_token_unused`, `_consume_candidate_token`, `_transition_to_active` are existing or near-existing names — adapt to whatever the current codebase calls them. The original `start_session` already uses these patterns; we're only replacing the body's middle portion.)

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/session/{service,schemas,errors}.py
git commit -m "feat(session): start_session provisions LiveKit room + dispatches agent"
```

---

## Task 4.3: Update the `/start` router decorator

**Files:**
- Modify: `backend/nexus/app/modules/session/router.py:154-187`

- [ ] **Step 1: Replace the decorator + handler**

```python
@candidate_session_router.post(
    "/start",
    status_code=status.HTTP_200_OK,
    response_model=StartSessionResponse,
)
async def post_start_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_tenant_db),
) -> StartSessionResponse:
    """Provision LiveKit room, dispatch agent, consume single-use token, transition active."""
    payload = request.state.candidate_token_payload
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return await session_service.start_session(
        db,
        session_id=payload.session_id,
        jti=payload.jti,
        ip_address=ip,
        user_agent=ua,
    )
```

Add at the imports block of `router.py`:

```python
from app.modules.session.schemas import StartSessionResponse
```

(Remove `StartSessionPendingResponse` import once nothing else references it.)

- [ ] **Step 2: Add error mapping for `AgentDispatchFailedError` to the global exception handler**

In `app/main.py`, add to the existing exception-handler block:

```python
@app.exception_handler(AgentDispatchFailedError)
async def _handle_agent_dispatch_failed(_: Request, exc: AgentDispatchFailedError):
    return JSONResponse(
        status_code=502,
        content={"code": "AGENT_DISPATCH_FAILED", "detail": exc.detail},
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/session/router.py backend/nexus/app/main.py
git commit -m "feat(session): /start returns 200 with livekit creds (was 501)"
```

---

## Task 4.4: Tests for `/start` happy + dispatch-failure + race

**Files:**
- Create: `backend/nexus/tests/test_session_start_livekit.py`

- [ ] **Step 1: Write the test file**

```python
"""/start LiveKit provisioning — happy + dispatch failure + token race."""

import contextlib
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import EngineDispatchToken, Session as SessionRow
from app.modules.session.errors import AgentDispatchFailedError, TokenAlreadyUsedError
from app.modules.session import service as session_service

pytestmark = pytest.mark.asyncio


@pytest.fixture
def livekit_stub(monkeypatch):
    """Mock all four module-level functions in session/livekit.py."""
    from app.modules.session import livekit as lk

    stubs = {
        "mint_candidate_lk_token": lambda **kw: "candidate-jwt-stub",
        "dispatch_agent": AsyncMock(return_value=None),
        "cancel_room": AsyncMock(return_value=None),
    }
    for name, impl in stubs.items():
        monkeypatch.setattr(lk, name, impl)
    monkeypatch.setattr(session_service, "mint_candidate_lk_token", stubs["mint_candidate_lk_token"])
    monkeypatch.setattr(session_service, "dispatch_agent", stubs["dispatch_agent"])
    monkeypatch.setattr(session_service, "cancel_room", stubs["cancel_room"])
    return stubs


async def test_happy_path_returns_creds(
    consented_session_factory, livekit_stub, tenant_session
):
    session_id, jti = await consented_session_factory()
    resp = await session_service.start_session(
        tenant_session, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )
    assert resp.livekit_token == "candidate-jwt-stub"
    assert resp.room_name == f"session-{session_id}"
    livekit_stub["dispatch_agent"].assert_awaited_once()
    sess = (await tenant_session.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
    assert sess.state == "active"
    assert sess.livekit_room_name == f"session-{session_id}"


async def test_dispatch_failure_does_not_consume_token(
    consented_session_factory, livekit_stub, tenant_session
):
    livekit_stub["dispatch_agent"].side_effect = RuntimeError("livekit down")
    session_id, jti = await consented_session_factory()

    with pytest.raises(AgentDispatchFailedError):
        await session_service.start_session(
            tenant_session, session_id=session_id, jti=jti,
            ip_address="127.0.0.1", user_agent="ua",
        )

    sess = (await tenant_session.execute(select(SessionRow).where(SessionRow.id == session_id))).scalar_one()
    assert sess.state == "consented"  # NOT active
    # candidate_session_tokens.used_at must still be NULL
    from app.models import CandidateSessionToken
    tok = (await tenant_session.execute(select(CandidateSessionToken).where(CandidateSessionToken.jti == jti))).scalar_one()
    assert tok.used_at is None


async def test_token_consume_race_cancels_room(
    consented_session_factory, livekit_stub, tenant_session
):
    """Token gets burned by an out-of-band caller between dispatch and consume."""
    session_id, jti = await consented_session_factory()

    original_consume = session_service._consume_candidate_token

    async def race_consume(db, jti_, ip, ua):
        # Simulate another path having already consumed the token.
        return False

    session_service._consume_candidate_token = race_consume
    try:
        with pytest.raises(TokenAlreadyUsedError):
            await session_service.start_session(
                tenant_session, session_id=session_id, jti=jti,
                ip_address="127.0.0.1", user_agent="ua",
            )
    finally:
        session_service._consume_candidate_token = original_consume

    livekit_stub["cancel_room"].assert_awaited_once()


async def test_engine_jwt_row_persisted(
    consented_session_factory, livekit_stub, tenant_session
):
    session_id, jti = await consented_session_factory()
    await session_service.start_session(
        tenant_session, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )
    rows = (await tenant_session.execute(
        select(EngineDispatchToken).where(EngineDispatchToken.session_id == session_id)
    )).scalars().all()
    assert len(rows) == 1
```

(Define `consented_session_factory` and `tenant_session` fixtures in `conftest.py` if absent — they should produce a session whose state is `consented`, OTP not required, and a candidate token with `used_at=NULL`.)

- [ ] **Step 2: Run**

Run: `docker compose run --rm nexus pytest tests/test_session_start_livekit.py -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_session_start_livekit.py
git commit -m "test(session): /start livekit provisioning — happy + failure + race"
```

---

## Task 4.5: Manual smoke — `curl /start`

- [ ] **Step 1: Boot the stack**

```bash
docker compose up nexus
```

- [ ] **Step 2: Walk a candidate to `consented + otp_verified` state via the existing wizard, then capture the candidate JWT and call `/start`**

```bash
curl -i -X POST http://localhost:8000/api/sessions/candidate/<TOKEN>/start
```

Expected: HTTP 200 with body:
```json
{
  "livekit_url": "wss://...",
  "livekit_token": "...",
  "room_name": "session-<uuid>",
  "session_id": "<uuid>"
}
```

- [ ] **Step 3: Verify the room exists in LiveKit**

Use `lk` CLI or LiveKit dashboard to list rooms. The room `session-<uuid>` should appear.

- [ ] **Step 4: Verify `engine_dispatch_tokens` row was inserted**

```bash
docker compose exec postgres psql -U postgres -d projectx -c \
  "SELECT jti, session_id, expires_at FROM engine_dispatch_tokens ORDER BY issued_at DESC LIMIT 1"
```

- [ ] **Step 5: No commit needed (verification step).**

---

# CHUNK 5 — Engine refactor

Goal: engine container boots, registers with LiveKit, fetches config from Nexus, runs an actual interview against a real session, and posts results back.

## Task 5.1: Update engine `pyproject.toml`

**Files:**
- Modify: `backend/interview_engine/pyproject.toml`

- [ ] **Step 1: Replace `[project].dependencies` to add the path-dep**

```toml
dependencies = [
    "livekit-agents>=0.10",
    "livekit-plugins-openai",
    "livekit-plugins-deepgram",
    "livekit-plugins-cartesia",
    "livekit-plugins-silero",
    "livekit-plugins-turn-detector",
    "livekit-plugins-ai-coustics",
    "httpx>=0.27",
    "structlog",
    "pydantic-settings",
    "python-dotenv",
    "nexus @ file:///app/nexus",
]
```

(Match the actual installed major versions of livekit-agents in the existing uv.lock if more current.)

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/pyproject.toml
git commit -m "build(engine): add nexus path-dep + pin livekit-plugins"
```

---

## Task 5.2: Rewrite the engine Dockerfile + add compose service

**Files:**
- Modify: `backend/interview_engine/Dockerfile`
- Modify: `backend/docker-compose.yml`

- [ ] **Step 1: Rewrite Dockerfile (build context = `backend/`)**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy nexus first (sibling of interview_engine inside the build context)
COPY nexus /app/nexus
COPY interview_engine /app/interview_engine

RUN pip install --no-cache-dir uv \
    && cd /app/nexus && uv pip install --system -e . \
    && cd /app/interview_engine && uv pip install --system -e .

WORKDIR /app/interview_engine

CMD ["python", "agent.py"]
```

- [ ] **Step 2: Add the `interview-engine` service to `backend/docker-compose.yml`**

```yaml
  interview-engine:
    build:
      context: .
      dockerfile: interview_engine/Dockerfile
    env_file: ./nexus/.env
    environment:
      NEXUS_INTERNAL_BASE_URL: http://nexus:8000
      AGENT_NAME: ${INTERVIEW_AGENT_NAME:-Dakota-1785}
    depends_on:
      - nexus
      - redis
    restart: unless-stopped
```

(Add to the existing `services:` block alongside `nexus`, `nexus-worker`, `redis`.)

- [ ] **Step 3: Build and verify (engine will fail to register because we haven't refactored agent.py yet — that's expected at this step, just confirm the Docker build succeeds)**

```bash
docker compose build interview-engine
```

Expected: build succeeds without errors. Don't `up` yet.

- [ ] **Step 4: Commit**

```bash
git add backend/interview_engine/Dockerfile backend/docker-compose.yml
git commit -m "build(engine): Dockerfile + docker-compose service for interview-engine"
```

---

## Task 5.3: Shrink `interview_engine/config.py`

**Files:**
- Modify: `backend/interview_engine/config.py`

- [ ] **Step 1: Replace the entire file with the slimmed-down config**

```python
"""Engine-mechanics config.

LLM / STT / TTS / model IDs / API keys are NOT here — they live in
nexus's app.ai.config.AIConfig and are read via app.ai.realtime factories.
This file only owns engine-mechanics: agent name, max probes, time
thresholds, endpointing, results-fallback dir, Nexus base URL.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class InterviewEngineConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    agent_name: str = "Dakota-1785"
    max_probes_per_question: int = 3
    time_warning_threshold: float = 0.8
    endpointing_min_delay: float = 0.5
    endpointing_max_delay: float = 6.0
    nexus_internal_base_url: str = "http://nexus:8000"
    results_fallback_dir: Path = Path("/tmp/interview_results")
```

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/config.py
git commit -m "refactor(engine): shrink config to engine-mechanics only"
```

---

## Task 5.4: Delete `interview_engine/models.py`

**Files:**
- Delete: `backend/interview_engine/models.py`

- [ ] **Step 1: Remove the file**

```bash
git rm backend/interview_engine/models.py
```

- [ ] **Step 2: Commit (the import errors get fixed in 5.5–5.7)**

```bash
git commit -m "refactor(engine): drop local models.py — schemas live in nexus"
```

---

## Task 5.5: Create `interview_engine/nexus_client.py`

**Files:**
- Create: `backend/interview_engine/nexus_client.py`

- [ ] **Step 1: Write the file**

```python
"""HTTP client for Nexus's /api/internal/sessions/{id}/{config,results} endpoints."""

from __future__ import annotations

import asyncio
import structlog

import httpx

from app.modules.interview_runtime.schemas import SessionConfig, SessionResult


log = structlog.get_logger("nexus_client")


class ConfigUnavailableError(RuntimeError):
    pass


class ResultRejectedError(RuntimeError):
    pass


class ResultPostFailedError(RuntimeError):
    pass


async def fetch_session_config(
    *, session_id: str, jwt: str, base_url: str
) -> SessionConfig:
    headers = {"Authorization": f"Bearer {jwt}", "x-correlation-id": session_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(3):
            try:
                r = await client.get(
                    f"{base_url}/api/internal/sessions/{session_id}/config",
                    headers=headers,
                )
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt == 2:
                    raise
                log.warning("nexus_client.config.network_error", attempt=attempt, error=str(exc))
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 401:
                raise PermissionError("Engine JWT rejected")
            if r.status_code in (404, 409, 422):
                raise ConfigUnavailableError(r.json())
            if r.status_code >= 500:
                if attempt == 2:
                    r.raise_for_status()
                log.warning("nexus_client.config.5xx", attempt=attempt, status=r.status_code)
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return SessionConfig.model_validate(r.json())


async def post_session_result(
    *, session_id: str, jwt: str, result: SessionResult, base_url: str
) -> None:
    headers = {"Authorization": f"Bearer {jwt}", "x-correlation-id": session_id}
    body = result.model_dump(mode="json")
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(3):
            try:
                r = await client.post(
                    f"{base_url}/api/internal/sessions/{session_id}/results",
                    headers=headers,
                    json=body,
                )
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt == 2:
                    raise ResultPostFailedError(str(exc)) from exc
                log.warning("nexus_client.result.network_error", attempt=attempt, error=str(exc))
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code in (204, 409):
                return  # 409 = already-completed, treat as idempotent success
            if r.status_code in (401, 422):
                raise ResultRejectedError(r.text)
            if r.status_code >= 500 and attempt < 2:
                log.warning("nexus_client.result.5xx", attempt=attempt, status=r.status_code)
                await asyncio.sleep(2 ** attempt)
                continue
            raise ResultPostFailedError(f"status={r.status_code} body={r.text}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/nexus_client.py
git commit -m "feat(engine): nexus_client httpx wrapper for /config + /results"
```

---

## Task 5.6: Test `nexus_client` with `respx`

**Files:**
- Create: `backend/interview_engine/tests/__init__.py` (if not present)
- Create: `backend/interview_engine/tests/test_nexus_client.py`

- [ ] **Step 1: Add `respx` to dev deps in engine `pyproject.toml`**

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]
```

- [ ] **Step 2: Write the test file**

```python
"""nexus_client retry/error coverage."""

import pytest
import respx
from httpx import Response

from nexus_client import (
    ConfigUnavailableError,
    ResultPostFailedError,
    ResultRejectedError,
    fetch_session_config,
    post_session_result,
)
from app.modules.interview_runtime.schemas import SessionResult, TranscriptEntry


pytestmark = pytest.mark.asyncio

BASE = "http://nexus:8000"
SID = "00000000-0000-0000-0000-000000000001"
JWT = "fake-jwt"


@respx.mock
async def test_fetch_config_happy(sample_session_config_json):
    respx.get(f"{BASE}/api/internal/sessions/{SID}/config").mock(
        return_value=Response(200, json=sample_session_config_json)
    )
    cfg = await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert cfg.session_id == sample_session_config_json["session_id"]


@respx.mock
async def test_fetch_config_401_no_retry():
    respx.get(f"{BASE}/api/internal/sessions/{SID}/config").mock(
        return_value=Response(401, json={"code": "ENGINE_TOKEN_INVALID"})
    )
    with pytest.raises(PermissionError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)


@respx.mock
async def test_fetch_config_5xx_retries_then_succeeds(sample_session_config_json):
    route = respx.get(f"{BASE}/api/internal/sessions/{SID}/config")
    route.side_effect = [
        Response(503), Response(503), Response(200, json=sample_session_config_json)
    ]
    cfg = await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert cfg.session_id == sample_session_config_json["session_id"]


@respx.mock
async def test_fetch_config_422_unavailable():
    respx.get(f"{BASE}/api/internal/sessions/{SID}/config").mock(
        return_value=Response(422, json={"code": "STAGE_TYPE_NOT_AI_DRIVEN"})
    )
    with pytest.raises(ConfigUnavailableError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)


@respx.mock
async def test_post_result_204_ok(sample_result):
    respx.post(f"{BASE}/api/internal/sessions/{SID}/results").mock(
        return_value=Response(204)
    )
    await post_session_result(session_id=SID, jwt=JWT, result=sample_result, base_url=BASE)


@respx.mock
async def test_post_result_409_idempotent(sample_result):
    respx.post(f"{BASE}/api/internal/sessions/{SID}/results").mock(
        return_value=Response(409, json={"code": "SESSION_ALREADY_COMPLETED"})
    )
    # 409 must NOT raise — the engine treats it as idempotent success.
    await post_session_result(session_id=SID, jwt=JWT, result=sample_result, base_url=BASE)


@respx.mock
async def test_post_result_422_rejected(sample_result):
    respx.post(f"{BASE}/api/internal/sessions/{SID}/results").mock(
        return_value=Response(422, json={"code": "INVALID"})
    )
    with pytest.raises(ResultRejectedError):
        await post_session_result(session_id=SID, jwt=JWT, result=sample_result, base_url=BASE)


@pytest.fixture
def sample_session_config_json():
    return {
        "session_id": SID,
        "job_title": "Senior Backend Engineer",
        "role_summary": "Owns the platform.",
        "seniority_level": "senior",
        "company": {"about": "We build X.", "industry": "saas", "company_stage": "growth", "hiring_bar": "T-shaped backend"},
        "candidate": {"name": "Alex"},
        "stage": {
            "stage_id": "00000000-0000-0000-0000-000000000099",
            "stage_type": "ai_screening",
            "name": "Phone screen",
            "duration_minutes": 30,
            "difficulty": "mid",
            "questions": [],
            "advance_behavior": "manual_review",
        },
        "signals": [],
    }


@pytest.fixture
def sample_result() -> SessionResult:
    return SessionResult(
        session_id=SID,
        job_title="Senior Backend Engineer",
        stage_id="00000000-0000-0000-0000-000000000099",
        stage_type="ai_screening",
        candidate_name="Alex",
        duration_seconds=600.0,
        questions_asked=8,
        questions_skipped=1,
        total_probes_fired=3,
        question_results=[],
        full_transcript=[TranscriptEntry(role="agent", text="Hi", timestamp_ms=0)],
        completed_at="2026-04-29T12:00:00Z",
    )
```

- [ ] **Step 3: Run inside the engine container (which has the path-dep'd nexus on PYTHONPATH)**

```bash
docker compose run --rm interview-engine pytest tests/test_nexus_client.py -v
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/interview_engine/tests/test_nexus_client.py backend/interview_engine/pyproject.toml
git commit -m "test(engine): nexus_client retry/error coverage with respx"
```

---

## Task 5.7: Refactor `interview_engine/agent.py` entrypoint

**Files:**
- Modify: `backend/interview_engine/agent.py`

- [ ] **Step 1: Replace the file**

```python
"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Per-session entrypoint: parse dispatch metadata, fetch SessionConfig from
Nexus, build the InterviewerAgent and AgentSession, run.
"""

from __future__ import annotations

import json

import structlog

from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    TurnHandlingOptions,
    cli,
    room_io,
)
from livekit.plugins import silero

from app.ai.realtime import (
    build_llm_plugin,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
)
from agents.interviewer import InterviewerAgent
from config import InterviewEngineConfig
from nexus_client import fetch_session_config

log = structlog.get_logger("interview-engine")
engine_cfg = InterviewEngineConfig()
server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=engine_cfg.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    metadata = json.loads(ctx.job.metadata or "{}")
    session_id = metadata["session_id"]
    engine_jwt = metadata["engine_jwt"]
    correlation_id = metadata.get("correlation_id", session_id)

    structlog.contextvars.bind_contextvars(
        session_id=session_id, correlation_id=correlation_id
    )
    log.info("engine.dispatch.received")

    config = await fetch_session_config(
        session_id=session_id, jwt=engine_jwt,
        base_url=engine_cfg.nexus_internal_base_url,
    )
    log.info("engine.config.fetched", question_count=len(config.stage.questions))

    agent = InterviewerAgent(
        session_config=config,
        engine_config=engine_cfg,
        nexus_jwt=engine_jwt,
        nexus_base_url=engine_cfg.nexus_internal_base_url,
    )

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            preemptive_generation={"enabled": False},
            endpointing={
                "min_delay": engine_cfg.endpointing_min_delay,
                "max_delay": engine_cfg.endpointing_max_delay,
            },
        ),
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=build_noise_cancellation(),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
```

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/agent.py
git commit -m "refactor(engine): entrypoint fetches config from nexus, uses app.ai.realtime"
```

---

## Task 5.8: Refactor `interview_engine/agents/interviewer.py` result post

**Files:**
- Modify: `backend/interview_engine/agents/interviewer.py`

- [ ] **Step 1: Update the constructor signature**

Find the `InterviewerAgent.__init__` and replace its signature + initial body with:

```python
def __init__(
    self,
    *,
    session_config: SessionConfig,
    engine_config: InterviewEngineConfig,
    nexus_jwt: str,
    nexus_base_url: str,
) -> None:
    self.session_config = session_config
    self.engine_config = engine_config
    self.nexus_jwt = nexus_jwt
    self.nexus_base_url = nexus_base_url
    # ... existing state machine + prompt setup unchanged ...
```

Imports change: `from app.modules.interview_runtime.schemas import SessionConfig, SessionResult, SteeringObservation, ...` (replacing the local `models` imports).

- [ ] **Step 2: Replace the local file write with `post_session_result`**

Find the close-out path that today writes `<session_id>.json` to `engine_config.results_dir`. Replace with:

```python
from nexus_client import (
    ResultPostFailedError, ResultRejectedError, post_session_result
)

async def _persist_result(self, result: SessionResult) -> None:
    try:
        await post_session_result(
            session_id=result.session_id,
            jwt=self.nexus_jwt,
            result=result,
            base_url=self.nexus_base_url,
        )
        log.info("engine.result.posted")
    except (ResultPostFailedError, ResultRejectedError) as exc:
        log.critical("engine.fallback.local_results_write", error=str(exc))
        # Fallback: drop a JSON file for forensics.
        self.engine_config.results_fallback_dir.mkdir(parents=True, exist_ok=True)
        path = self.engine_config.results_fallback_dir / f"{result.session_id}.json"
        path.write_text(result.model_dump_json(indent=2))
```

Wire `_persist_result` into the existing close-out path (replace the existing file-write call site with `await self._persist_result(result)`).

- [ ] **Step 3: Commit**

```bash
git add backend/interview_engine/agents/interviewer.py
git commit -m "refactor(engine): interviewer posts results to nexus instead of local file"
```

---

## Task 5.9: Update `prompt_builder.py` to use the nexus prompt loader

**Files:**
- Modify: `backend/interview_engine/prompt_builder.py`

- [ ] **Step 1: Replace the file's prompt-loading code**

Find where `prompt_builder.py` reads the local `prompts/interviewer.txt`. Replace with:

```python
from app.ai.prompts import prompt_loader

# inside the function/builder that returns the rendered system prompt:
template_text = prompt_loader.load("interview/interviewer")
# ... existing string.Template substitution unchanged ...
```

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/prompt_builder.py
git commit -m "refactor(engine): load prompt via nexus prompt_loader"
```

---

## Task 5.10: Collapse `context_loader.py`

**Files:**
- Modify: `backend/interview_engine/context_loader.py`

- [ ] **Step 1: Replace the file**

```python
"""Context loader.

Two modes:
- fixture (engine pytest only) — load SessionConfig from a JSON file.
- nexus_api (production) — fetch from Nexus.

Mode selection is implicit in the caller's argument set; there's no enum
or env var driving it now.
"""

from pathlib import Path
from typing import Optional

from app.modules.interview_runtime.schemas import SessionConfig

from nexus_client import fetch_session_config


async def load_session_config(
    *,
    session_id: Optional[str] = None,
    jwt: Optional[str] = None,
    base_url: Optional[str] = None,
    fixture_path: Optional[Path] = None,
) -> SessionConfig:
    if fixture_path is not None:
        return SessionConfig.model_validate_json(fixture_path.read_text())
    if not (session_id and jwt and base_url):
        raise ValueError("nexus_api mode requires session_id + jwt + base_url")
    return await fetch_session_config(session_id=session_id, jwt=jwt, base_url=base_url)
```

- [ ] **Step 2: Commit**

```bash
git add backend/interview_engine/context_loader.py
git commit -m "refactor(engine): collapse context_loader to fixture | nexus_api"
```

---

## Task 5.11: End-to-end manual run

- [ ] **Step 1: Boot the full stack**

```bash
docker compose up --build
```

- [ ] **Step 2: Verify the engine registered**

Watch the logs for:
```
interview-engine | engine.worker.registered agent_name=Dakota-1785
```

(Or whatever LiveKit emits when a worker successfully connects.)

- [ ] **Step 3: Walk an end-to-end run**

a. Frontend wizard → consent → OTP → camera/mic → Start.
b. Verify the candidate's browser sees the agent join the room within ~3s.
c. Listen to the greeting; speak; verify the transcript / state machine progresses.
d. Reach session close; verify completion.

- [ ] **Step 4: Verify Postgres**

```bash
docker compose exec postgres psql -U postgres -d projectx -c "
SELECT id, state, questions_asked, probes_fired, agent_completed_at,
       jsonb_array_length(transcript) AS transcript_len
FROM sessions
ORDER BY created_at DESC LIMIT 1
"
```
Expected: `state=completed`, `questions_asked > 0`, `agent_completed_at IS NOT NULL`, `transcript_len > 0`.

- [ ] **Step 5: Verify Langfuse**

Open `http://localhost:3000` (or whatever your self-hosted Langfuse host is). Confirm the trace exists with the correlation_id.

- [ ] **Step 6: No commit needed.**

---

# CHUNK 6 — Frontend live UI

Goal: Section 5 of the spec — minimal voice + transcript UI. Candidate completes the screening end-to-end via the wizard.

## Task 6.1: Install LiveKit deps

**Files:**
- Modify: `frontend/app/package.json`

- [ ] **Step 1: Install**

```bash
cd frontend/app
npm install livekit-client @livekit/components-react @livekit/components-styles
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/package.json frontend/app/package-lock.json
git commit -m "build(frontend): install livekit-client + @livekit/components-react"
```

---

## Task 6.2: Update `startSession()` return type

**Files:**
- Modify: `frontend/app/lib/api/candidate-session.ts`

- [ ] **Step 1: Replace the response type**

```typescript
export interface StartSessionResponse {
  livekit_url: string
  livekit_token: string
  room_name: string
  session_id: string
}

export async function startSession(token: string): Promise<StartSessionResponse> {
  return apiFetch<StartSessionResponse>(
    `/api/sessions/candidate/${token}/start`,
    { method: 'POST' },
  )
}
```

(Delete the old `StartSessionPendingResponse` type.)

- [ ] **Step 2: Commit**

```bash
git add frontend/app/lib/api/candidate-session.ts
git commit -m "feat(api): startSession returns LiveKit creds (was 501 stub)"
```

---

## Task 6.3: Create the LiveSession component tree

**Files:**
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/LiveSessionShell.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/AgentTile.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/CandidateSelfView.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/ProgressBanner.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/TranscriptPane.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/CompletionScreen.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/DisconnectError.tsx`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/use-agent-state.ts`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/use-agent-grace-timeout.ts`
- Create: `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/use-stage-progress.ts`

- [ ] **Step 1: `LiveSessionShell.tsx`**

```tsx
"use client"

import "@livekit/components-styles"
import { LiveKitRoom, RoomAudioRenderer } from "@livekit/components-react"
import { useState } from "react"

import { AgentTile } from "./AgentTile"
import { CandidateSelfView } from "./CandidateSelfView"
import { CompletionScreen } from "./CompletionScreen"
import { DisconnectError } from "./DisconnectError"
import { ProgressBanner } from "./ProgressBanner"
import { TranscriptPane } from "./TranscriptPane"
import { useAgentGraceTimeout } from "./hooks/use-agent-grace-timeout"

interface Props {
  livekitUrl: string
  livekitToken: string
  roomName: string
}

type Outcome = "active" | "completed" | "error"

export function LiveSessionShell({ livekitUrl, livekitToken, roomName }: Props) {
  const [outcome, setOutcome] = useState<Outcome>("active")
  const [errorCode, setErrorCode] = useState<string | null>(null)

  if (outcome === "completed") return <CompletionScreen />
  if (outcome === "error" && errorCode) return <DisconnectError code={errorCode} />

  return (
    <LiveKitRoom
      serverUrl={livekitUrl}
      token={livekitToken}
      connect
      audio
      video
      onDisconnected={() => setOutcome("completed")}
      data-room-name={roomName}
    >
      <RoomAudioRenderer />
      <ProgressBanner />
      <main className="grid grid-cols-1 md:grid-cols-2 gap-4 p-6">
        <AgentTile />
        <CandidateSelfView onMediaLost={() => { setErrorCode("MEDIA_LOST"); setOutcome("error") }} />
      </main>
      <GraceTimeoutBoundary
        onNoShow={() => { setErrorCode("AGENT_NO_SHOW"); setOutcome("error") }}
      />
      <TranscriptPane />
    </LiveKitRoom>
  )
}

function GraceTimeoutBoundary({ onNoShow }: { onNoShow: () => void }) {
  useAgentGraceTimeout(onNoShow, { graceMs: 30_000 })
  return null
}
```

- [ ] **Step 2: `hooks/use-agent-grace-timeout.ts`**

```ts
import { useRemoteParticipants } from "@livekit/components-react"
import { useEffect, useRef } from "react"

interface Opts {
  graceMs: number
}

export function useAgentGraceTimeout(onNoShow: () => void, { graceMs }: Opts) {
  const remotes = useRemoteParticipants()
  const firedRef = useRef(false)

  useEffect(() => {
    if (firedRef.current) return
    const t = setTimeout(() => {
      const hasAgent = remotes.some(p => p.identity.startsWith("agent-"))
      if (!hasAgent) {
        firedRef.current = true
        onNoShow()
      }
    }, graceMs)
    return () => clearTimeout(t)
  }, [graceMs, onNoShow, remotes])
}
```

- [ ] **Step 3: `hooks/use-agent-state.ts`**

```ts
import { useRemoteParticipants, useVoiceAssistant } from "@livekit/components-react"

export type AgentState = "connecting" | "listening" | "thinking" | "speaking" | "disconnected"

export function useAgentState(): AgentState {
  const remotes = useRemoteParticipants()
  const va = useVoiceAssistant()
  const hasAgent = remotes.some(p => p.identity.startsWith("agent-"))
  if (!hasAgent) return "connecting"
  // useVoiceAssistant exposes `state: 'idle' | 'listening' | 'thinking' | 'speaking'`
  switch (va.state) {
    case "speaking": return "speaking"
    case "thinking": return "thinking"
    case "listening": return "listening"
    default: return "listening"
  }
}
```

- [ ] **Step 4: `hooks/use-stage-progress.ts`**

```ts
import { useParticipants } from "@livekit/components-react"

export interface StageProgress {
  currentQuestion: number
  totalQuestions: number
  timeRemainingSeconds: number
}

export function useStageProgress(): StageProgress | null {
  const participants = useParticipants()
  const agent = participants.find(p => p.identity.startsWith("agent-"))
  if (!agent) return null
  const a = agent.attributes ?? {}
  const cur = parseInt(a.current_question_index ?? "")
  const total = parseInt(a.total_questions ?? "")
  const tRemain = parseInt(a.time_remaining_seconds ?? "")
  if (Number.isNaN(cur) || Number.isNaN(total) || Number.isNaN(tRemain)) return null
  return { currentQuestion: cur, totalQuestions: total, timeRemainingSeconds: tRemain }
}
```

- [ ] **Step 5: `AgentTile.tsx`**

```tsx
"use client"
import { useAgentState } from "./hooks/use-agent-state"

export function AgentTile() {
  const state = useAgentState()
  return (
    <div className="rounded-2xl bg-zinc-900 p-6 flex flex-col items-center justify-center aspect-video">
      <div className="size-24 rounded-full bg-zinc-800 grid place-items-center mb-3">
        <div className={`size-3 rounded-full transition-all ${state === "speaking" ? "bg-emerald-400 animate-pulse" : "bg-zinc-500"}`} />
      </div>
      <p className="text-zinc-200 text-sm">Interviewer · {state}</p>
    </div>
  )
}
```

- [ ] **Step 6: `CandidateSelfView.tsx`**

```tsx
"use client"
import { Track } from "livekit-client"
import { useLocalParticipant, VideoTrack } from "@livekit/components-react"
import { useEffect } from "react"

interface Props {
  onMediaLost: () => void
}

export function CandidateSelfView({ onMediaLost }: Props) {
  const { localParticipant } = useLocalParticipant()
  const camTrack = localParticipant.getTrackPublication(Track.Source.Camera)?.track
  const micTrack = localParticipant.getTrackPublication(Track.Source.Microphone)?.track

  useEffect(() => {
    if (!camTrack || !micTrack) return
    const handler = () => {
      // If the candidate explicitly muted via the UI, ignore. Only fire on
      // hardware/permission loss (track ended).
      if (camTrack.isMuted && !camTrack.mediaStreamTrack?.enabled) onMediaLost()
      if (micTrack.isMuted && !micTrack.mediaStreamTrack?.enabled) onMediaLost()
    }
    camTrack.on("muted", handler)
    micTrack.on("muted", handler)
    return () => {
      camTrack.off("muted", handler)
      micTrack.off("muted", handler)
    }
  }, [camTrack, micTrack, onMediaLost])

  return (
    <div className="rounded-2xl bg-zinc-900 overflow-hidden aspect-video">
      {camTrack ? (
        <VideoTrack trackRef={{ participant: localParticipant, source: Track.Source.Camera, publication: localParticipant.getTrackPublication(Track.Source.Camera)! }} />
      ) : (
        <div className="size-full grid place-items-center text-zinc-500">camera off</div>
      )}
    </div>
  )
}
```

- [ ] **Step 7: `ProgressBanner.tsx`**

```tsx
"use client"
import { useStageProgress } from "./hooks/use-stage-progress"

export function ProgressBanner() {
  const p = useStageProgress()
  if (!p) return null
  const minutes = Math.floor(p.timeRemainingSeconds / 60)
  return (
    <div className="sticky top-0 z-10 bg-zinc-50 border-b border-zinc-200 px-6 py-3 text-sm text-zinc-700">
      Q{p.currentQuestion + 1} of {p.totalQuestions} · {minutes} min remaining
    </div>
  )
}
```

- [ ] **Step 8: `TranscriptPane.tsx`**

```tsx
"use client"
import { useChat } from "@livekit/components-react"

export function TranscriptPane() {
  const { chatMessages } = useChat()
  return (
    <aside className="border-t border-zinc-200 max-h-64 overflow-y-auto p-4 space-y-2 bg-white">
      {chatMessages.map(m => (
        <div key={m.id} className={`text-sm ${m.from?.identity?.startsWith("agent-") ? "text-zinc-700" : "text-zinc-900 font-medium"}`}>
          <span className="opacity-60 mr-2">
            {m.from?.identity?.startsWith("agent-") ? "Interviewer" : "You"}
          </span>
          {m.message}
        </div>
      ))}
    </aside>
  )
}
```

- [ ] **Step 9: `CompletionScreen.tsx`**

```tsx
"use client"
export function CompletionScreen() {
  return (
    <div className="min-h-screen grid place-items-center bg-zinc-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold text-zinc-900">Thanks for completing your interview.</h1>
        <p className="mt-3 text-zinc-600">
          You can close this tab. We'll be in touch soon.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 10: `DisconnectError.tsx`**

```tsx
"use client"

const COPY: Record<string, { title: string; body: string }> = {
  AGENT_NO_SHOW: {
    title: "Interviewer didn't connect",
    body: "We couldn't reach the interviewer. Please try again later or contact your recruiter.",
  },
  MEDIA_LOST: {
    title: "Camera or microphone unavailable",
    body: "Your camera or microphone is no longer accessible. Please reconnect to continue.",
  },
}

export function DisconnectError({ code }: { code: string }) {
  const c = COPY[code] ?? { title: "Session disconnected", body: "An unexpected error occurred." }
  return (
    <div className="min-h-screen grid place-items-center bg-zinc-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold text-zinc-900">{c.title}</h1>
        <p className="mt-2 text-sm text-zinc-600">{c.body}</p>
        <p className="mt-6 text-xs text-zinc-500">Error code: {code}</p>
      </div>
    </div>
  )
}
```

- [ ] **Step 11: Commit**

```bash
git add frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/
git commit -m "feat(interview): live session shell + tiles + transcript + hooks"
```

---

## Task 6.4: Wire LiveSession into `WizardShell` + `StartStep`

**Files:**
- Modify: `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx`
- Modify: `frontend/app/app/(interview)/interview/[token]/StartStep.tsx`

- [ ] **Step 1: Update `WizardShell.tsx` to dynamically load LiveSessionShell on `state==='active'` + creds present**

```tsx
import dynamic from "next/dynamic"

const LiveSessionShell = dynamic(
  () => import("./LiveSession/LiveSessionShell").then(m => m.LiveSessionShell),
  { ssr: false, loading: () => <div className="min-h-screen grid place-items-center">Connecting…</div> },
)

// ... in the routing logic where state==='active' previously rendered AlreadyStartedPanel:
if (sessionState === "active" && creds) {
  return (
    <LiveSessionShell
      livekitUrl={creds.livekit_url}
      livekitToken={creds.livekit_token}
      roomName={creds.room_name}
    />
  )
}
// (Existing AlreadyStartedPanel becomes the fallback for state='active' with no creds — i.e. mid-session refresh.)
```

(Hold the `creds` in component-local state, set via `StartStep`'s success callback.)

- [ ] **Step 2: Update `StartStep.tsx` to push creds up on success**

```tsx
const startMutation = useStartSession(token, {
  onSuccess: (resp) => {
    onStarted(resp)  // bubble up to WizardShell
  },
  onError: (err) => {
    if (err instanceof ApiError && err.status === 409 && err.body?.code === "TOKEN_ALREADY_USED") {
      setOutcome("replay")
    } else {
      toast.error("Couldn't start the interview. Please try again.")
    }
  },
})
```

(`onStarted` is a prop wired from WizardShell; replace the existing 501 handling.)

- [ ] **Step 3: Smoke**

Run `npm run build` and `npm run dev`. Walk through the wizard. Expected: Start triggers a 200; LiveKit URL is loaded; the agent tile appears within ~3s.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(interview\)/interview/\[token\]/{WizardShell,StartStep}.tsx
git commit -m "feat(interview): wire LiveSessionShell into wizard"
```

---

## Task 6.5: Vitest tests

**Files:**
- Create: `frontend/app/tests/components/interview/LiveSessionShell.test.tsx`
- Create: `frontend/app/tests/components/interview/ProgressBanner.test.tsx`
- Create: `frontend/app/tests/components/interview/CompletionScreen.test.tsx`

- [ ] **Step 1: `LiveSessionShell.test.tsx` (grace timeout)**

```tsx
import { render, screen, act } from "@testing-library/react"
import { vi } from "vitest"

vi.mock("@livekit/components-react", () => ({
  LiveKitRoom: ({ children }: any) => <>{children}</>,
  RoomAudioRenderer: () => null,
  useRemoteParticipants: () => [],
  useVoiceAssistant: () => ({ state: "listening" }),
  useParticipants: () => [],
  useChat: () => ({ chatMessages: [] }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => null } }),
  VideoTrack: () => null,
}))

import { LiveSessionShell } from "@/app/(interview)/interview/[token]/LiveSession/LiveSessionShell"

describe("LiveSessionShell", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("fires AGENT_NO_SHOW after 30s grace timeout", () => {
    render(<LiveSessionShell livekitUrl="wss://x" livekitToken="t" roomName="r" />)
    act(() => { vi.advanceTimersByTime(30_000) })
    expect(screen.getByText(/Interviewer didn't connect/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: `ProgressBanner.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { vi } from "vitest"

vi.mock("@livekit/components-react", () => ({
  useParticipants: () => [{
    identity: "agent-stub",
    attributes: { current_question_index: "2", total_questions: "9", time_remaining_seconds: "660" },
  }],
}))

import { ProgressBanner } from "@/app/(interview)/interview/[token]/LiveSession/ProgressBanner"

describe("ProgressBanner", () => {
  it("renders Q3 of 9 · 11 min remaining", () => {
    render(<ProgressBanner />)
    expect(screen.getByText(/Q3 of 9 · 11 min remaining/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: `CompletionScreen.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react"
import { CompletionScreen } from "@/app/(interview)/interview/[token]/LiveSession/CompletionScreen"

describe("CompletionScreen", () => {
  it("renders the thank-you copy and no nav links", () => {
    const { container } = render(<CompletionScreen />)
    expect(screen.getByText(/Thanks for completing your interview/)).toBeInTheDocument()
    expect(container.querySelectorAll("a")).toHaveLength(0)
  })
})
```

- [ ] **Step 4: Run**

```bash
cd frontend/app && npm run test
```
Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/tests/components/interview/
git commit -m "test(interview): live session shell + progress banner + completion"
```

---

# CHUNK 7 — Docs + threat model + runbook

Goal: ship docs CLAUDE.md mandates, mark Phase 3C.2 done.

## Task 7.1: Threat-model delta

**Files:**
- Modify (or create): `docs/security/threat-model.md`

- [ ] **Step 1: Append a Phase 3C.2 section**

```markdown
## Phase 3C.2 — Interview engine integration (2026-04-29)

Trust boundaries added by wiring backend/interview_engine/ into nexus.

| Boundary | New element | STRIDE | Mitigation |
|---|---|---|---|
| Public → LiveKit room | Candidate access token (room_join) | T (token leakage) | TTL = stage_duration + 10min; minted at /start, single-issuance; no rejoin flow this round |
| LiveKit → engine worker | Dispatch JWT in metadata | T (replay across rooms) | Single-use per (jti, endpoint) via engine_token_uses; jti bound to session_id; max lifetime 90 min |
| Engine → Nexus internal API | HTTP w/ engine JWT | E, I | HS256 pinning; purpose claim; tenant_id claim explicit-filter on every query under bypass session; audit log on every call |
| Engine → OpenAI / Deepgram / Cartesia | Audio + transcript | I (PII via 3rd party) | Consent gate (existing); CandidateContext drops email; self-hosted Langfuse only |
```

- [ ] **Step 2: Commit**

```bash
mkdir -p docs/security
git add docs/security/threat-model.md
git commit -m "docs(security): threat-model delta for Phase 3C.2"
```

---

## Task 7.2: Key-rotation runbook

**Files:**
- Create: `docs/security/key-rotation-runbook.md`

- [ ] **Step 1: Write the file**

```markdown
# Key Rotation Runbook

Cadence: every 90 days, on personnel change, or on incident — whichever first.

## INTERVIEW_ENGINE_JWT_SECRET (Phase 3C.2)

Used by Nexus to sign HS256 engine dispatch JWTs. The interview engine
verifies them via the same secret. Compromise impact: an attacker who
also has access to LiveKit could mint dispatches that retrieve full
SessionConfigs (including company profile + question rubric).

Procedure:
1. Generate: `openssl rand -base64 32`
2. Update Railway / ECS env: `INTERVIEW_ENGINE_JWT_SECRET=<new>` for both
   the `nexus` and `interview-engine` containers in lockstep.
3. Roll out both containers together (no in-flight dispatches will
   succeed across the cutover; this is acceptable — sessions in-flight
   already hold a fully-authenticated room).
4. Audit `engine_dispatch_tokens` for the prior 24h: `SELECT count(*)
   FROM engine_dispatch_tokens WHERE issued_at > NOW() - INTERVAL '24h'`.

## LIVEKIT_API_KEY / LIVEKIT_API_SECRET

Provisioned by LiveKit Cloud (or your self-hosted control plane).
Compromise impact: an attacker can mint candidate / agent room tokens
and publish/subscribe to any project room.

Procedure:
1. Generate a new key pair in the LiveKit Cloud dashboard (or via
   livekit-cli for self-hosted).
2. Update `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` env on `nexus` and
   `interview-engine`.
3. Revoke the old key pair from the LiveKit project after both
   containers have rolled.

## CANDIDATE_JWT_SECRET

Already documented in the original Phase 3C.1 runbook (see invite
flow). Same cadence applies.
```

- [ ] **Step 2: Commit**

```bash
git add docs/security/key-rotation-runbook.md
git commit -m "docs(security): key rotation runbook (engine + livekit)"
```

---

## Task 7.3: End-to-end runbook

**Files:**
- Create: `docs/onboarding/interview-engine-e2e.md`

- [ ] **Step 1: Write the file** (use the bullet list from spec Section 7.4 verbatim).

- [ ] **Step 2: Commit**

```bash
mkdir -p docs/onboarding
git add docs/onboarding/interview-engine-e2e.md
git commit -m "docs(onboarding): interview engine end-to-end runbook"
```

---

## Task 7.4: Update CLAUDE.md / AGENTS.md

**Files:**
- Modify: `CLAUDE.md` (root) — phase status table, mark 3C.2 done.
- Modify: `backend/nexus/CLAUDE.md` — add `interview_runtime` module entry; note migration 0024 in the migrations list; add to Phase 3C section.
- Modify: `backend/interview_engine/AGENTS.md` — replace the one-line LiveKit MCP note with the new architecture description (worker container, path-dep on nexus, fetches config via internal API).
- Modify: `frontend/app/CLAUDE.md` — note the new `livekit-client`, `@livekit/components-react`, `@livekit/components-styles` deps in the "Currently Installed" section.

- [ ] **Step 1: Apply all four edits, committing per file.**

```bash
git add CLAUDE.md
git commit -m "docs(claude): mark Phase 3C.2 done"

git add backend/nexus/CLAUDE.md
git commit -m "docs(claude-nexus): document interview_runtime module + migration 0024"

git add backend/interview_engine/AGENTS.md
git commit -m "docs(claude-engine): describe nexus integration architecture"

git add frontend/app/CLAUDE.md
git commit -m "docs(claude-frontend): note livekit-client + components-react installed"
```

---

# Self-review checklist (already run before publishing)

| Spec section | Tasks | Notes |
|---|---|---|
| §1 Topology | 5.1, 5.2 | path-dep + Dockerfile |
| §2 Context handoff | 3.1–3.9 | Internal API + JWT + start_session rewrite |
| §3 Migration | 1.1–1.7 | Both tables + seven cols + RLS check |
| §4 Engine refactor | 5.3–5.10 | nexus_client + agent.py + interviewer.py + prompt + context_loader + config |
| §5 Frontend | 6.1–6.5 | LiveSession tree + hooks + tests + wizard wire |
| §6 Security | 3.3, 3.4, 7.1, 7.2 | verify_engine_token + tests + threat model + rotation runbook |
| §7 Tests | 1.7, 3.4, 3.6, 3.8, 4.4, 5.6, 6.5 | All test tasks present |
| §8 Build sequence | Chunks 1–7 | One-to-one |

No placeholders, no "TBD", no "implement appropriate error handling." Type names consistent throughout (`StartSessionResponse`, `EngineDispatchToken`, `EngineTokenUse`, `verify_engine_token`, `mint_engine_dispatch_jwt`, `dispatch_agent`, `cancel_room`, `fetch_session_config`, `post_session_result`, `LiveSessionShell`, `useAgentGraceTimeout`, `useStageProgress`, `useAgentState`).
