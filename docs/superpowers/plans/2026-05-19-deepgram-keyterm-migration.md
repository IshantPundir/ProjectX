# Deepgram nova-3 + en-IN with LLM-extracted keyterm prompting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the interview engine's default STT from Sarvam `saaras:v3` to Deepgram `nova-3` (`en-IN`) with **LLM-extracted per-bank-cached** keyterm prompting. The LLM call runs once per question-bank generation (as the final step of `question_bank/actors.py:generate_question_bank_stage`); the result lives in a new JSONB column on `stage_question_banks`; the engine reads it at session start with zero hot-path LLM latency.

**Architecture:**
- **Bank-side** (one-time per bank): a new `_extract_bank_keyterms` helper in `question_bank/refine.py` makes one structured LLM call returning `KeytermExtractionOutput { keyterms: list[str] }`. The actor writes the list to `stage_question_banks.extracted_keyterms`.
- **Engine-side** (every session start): `build_session_config` loads the cached list onto `SessionConfig.keyterms`. The engine's `assemble_keyterms` merger prepends the candidate's first name, dedupes, caps at 50, and hands the list to `deepgram.STT(keyterm=[…])`.
- **Fallback:** if a bank's `extracted_keyterms` is null (legacy, never regenerated), the engine ships `[candidate.first_name]` only. Deepgram still works, just without brand boost. User has confirmed (2026-05-19) this is acceptable in dev mode — no backfill needed.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async + asyncpg, Alembic, instructor + OpenAI, LiveKit Agents, `livekit.plugins.deepgram`, pytest. Docker Compose for tests.

**Spec:** `docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md` (current head `879b1e5`).

**Out of scope (per spec Non-goals):** `orchestrator.py` continuation watcher (preserved verbatim — must remain functional after this migration); EOU/endpointing re-tuning; TTS; VAD; noise cancellation; mid-session keyterm updates; hybrid regex+LLM; Sarvam removal; backfill of legacy banks.

---

## Phase A — Bank-side keyterm extraction (LLM call at bank generation)

## Task 1: Alembic migration `0029_extracted_keyterms`

**Files:**
- Create: `backend/nexus/migrations/versions/0029_extracted_keyterms.py`

- [ ] **Step 1: Generate the migration scaffold.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  alembic revision -m "extracted_keyterms"
```

Note the generated filename (Alembic will name it something like `0029_xxx_extracted_keyterms.py`). If the prefix isn't `0029`, rename the file to start with `0029_` to match the project's convention (see `backend/nexus/CLAUDE.md` migration log).

- [ ] **Step 2: Fill in the migration body.**

Open the new file. Replace its body with:

```python
"""extracted_keyterms

Revision ID: 0029_extracted_keyterms
Revises: 0028_audio_tuning_summary
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0029_extracted_keyterms"
down_revision = "0028_audio_tuning_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add stage_question_banks.extracted_keyterms (JSONB, nullable).

    Populated by question_bank/actors.py:generate_question_bank_stage as its
    final step. NULL means "extraction hasn't run for this bank yet" — the
    engine falls back to candidate-name-only STT boosting. Per spec
    docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md,
    legacy banks are NOT backfilled; recruiter regenerates to populate.
    """
    op.add_column(
        "stage_question_banks",
        sa.Column("extracted_keyterms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "extracted_keyterms")
```

- [ ] **Step 3: Run the migration up.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  alembic upgrade head
```

Expected: clean upgrade, exit 0.

- [ ] **Step 4: Verify the column exists.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "
import asyncio
from sqlalchemy import text
from app.database import get_bypass_db

async def check():
    async with get_bypass_db() as db:
        result = await db.execute(text(\"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='stage_question_banks' AND column_name='extracted_keyterms'\"))
        rows = result.fetchall()
        print(rows)

asyncio.run(check())
"
```

Expected output: `[('extracted_keyterms', 'jsonb')]`

- [ ] **Step 5: Verify the downgrade works.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  alembic downgrade -1
docker compose -f backend/nexus/docker-compose.yml run --rm nexus \
  alembic upgrade head
```

Both commands should succeed. Re-run the check from Step 4 to confirm the column re-exists after the re-upgrade.

- [ ] **Step 6: Update `backend/nexus/CLAUDE.md` migration log.**

Find the section that lists migrations (search for `0028_audio_tuning_summary`). Add a one-line entry immediately below:

```
- `0029_extracted_keyterms` — Adds `stage_question_banks.extracted_keyterms JSONB NULL`. Populated by `question_bank/actors.py:generate_question_bank_stage` after refinement completes; consumed by the engine at session start for Deepgram nova-3 keyterm prompting. Legacy banks not backfilled (regeneration repopulates).
```

Also update the "current head" line at the top of that section from `0028_audio_tuning_summary` to `0029_extracted_keyterms`.

- [ ] **Step 7: Add the ORM column.**

Open `backend/nexus/app/modules/question_bank/models.py`. Find the `StageQuestionBank` (or similarly named) SQLAlchemy class and the existing columns (`is_stale`, `pipeline_version_at_generation`, `stage_config_snapshot`, etc.). Add immediately after them:

```python
    extracted_keyterms: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
```

Make sure `JSONB` is imported at the top of the file (e.g., `from sqlalchemy.dialects.postgresql import JSONB` if it isn't already).

- [ ] **Step 8: Smoke-test the ORM.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.question_bank.models import StageQuestionBankModel; print(StageQuestionBankModel.__table__.columns['extracted_keyterms'])"
```

Expected: prints a column reference without error.

- [ ] **Step 9: Commit.**

```bash
git add backend/nexus/migrations/versions/0029_extracted_keyterms.py \
        backend/nexus/CLAUDE.md \
        backend/nexus/app/modules/question_bank/models.py
git commit -m "$(cat <<'EOF'
feat(migrations): 0029 — stage_question_banks.extracted_keyterms

Lazy-populated JSONB column for the per-bank STT keyterm list. Populated
by the question_bank actor at bank-generation time and consumed by the
engine for Deepgram nova-3 keyterm prompting. NULL on legacy banks;
regenerating the bank populates it.
EOF
)"
```

---

## Task 2: AI schema `KeytermExtractionOutput`

**Files:**
- Modify: `backend/nexus/app/ai/schemas.py`
- Modify: `backend/nexus/tests/ai/test_schemas.py` (or create if missing)

- [ ] **Step 1: Write failing tests for the schema.**

Add to `backend/nexus/tests/ai/test_schemas.py` (create the file if it doesn't exist; mirror the imports of any existing test file under `tests/ai/`):

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.schemas import KeytermExtractionOutput


class TestKeytermExtractionOutput:
    def test_valid_input_accepted(self) -> None:
        out = KeytermExtractionOutput(
            keyterms=[f"Brand{i}" for i in range(20)]
        )
        assert len(out.keyterms) == 20

    def test_too_few_terms_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Only", "Five", "Brands", "Here", "x"])

    def test_too_many_terms_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=[f"X{i}" for i in range(51)])

    def test_empty_string_in_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Valid"] * 9 + [""])

    def test_overly_long_term_rejected(self) -> None:
        too_long = "x" * 81
        with pytest.raises(ValidationError):
            KeytermExtractionOutput(keyterms=["Valid"] * 9 + [too_long])
```

- [ ] **Step 2: Verify tests fail.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/ai/test_schemas.py::TestKeytermExtractionOutput -v
```

Expected: `ImportError` on `KeytermExtractionOutput`.

- [ ] **Step 3: Add the schema.**

Open `backend/nexus/app/ai/schemas.py`. At the end of the file, append:

```python
class KeytermExtractionOutput(BaseModel):
    """Output schema for the per-bank STT keyterm extraction LLM call.

    Used by question_bank/actors.py:generate_question_bank_stage to populate
    stage_question_banks.extracted_keyterms. Consumed by the engine at session
    start to bias Deepgram nova-3 STT toward role-specific vocabulary.

    See docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    """

    keyterms: list[str] = Field(min_length=10, max_length=50)

    @model_validator(mode="after")
    def _validate_each_term(self) -> "KeytermExtractionOutput":
        for term in self.keyterms:
            if not term.strip():
                raise ValueError("keyterms must not contain empty strings")
            if len(term) > 80:
                raise ValueError(f"keyterm too long ({len(term)} chars): {term!r}")
        return self
```

If `model_validator` and `Field` aren't already imported at the top, add them — `app/ai/schemas.py` already imports several Pydantic primitives, follow that pattern.

- [ ] **Step 4: Verify tests pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/ai/test_schemas.py::TestKeytermExtractionOutput -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/ai/schemas.py backend/nexus/tests/ai/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(ai/schemas): KeytermExtractionOutput — strict 10-50 term Pydantic model

Output schema for the upcoming per-bank STT keyterm extraction LLM call.
Bounds enforce Deepgram's 20-50 recommended range with safety floor and
ceiling; per-term validators reject empty strings and entries over 80
chars.
EOF
)"
```

---

## Task 3: New AIConfig field `question_bank_keyterm_model`

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/app/ai/config.py`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Add the field to `Settings`.**

Open `backend/nexus/app/config.py`. Find the existing `question_bank_*` fields (search for `question_bank_model` — there should be a line like `question_bank_model: str = "gpt-5.3-...`). Add immediately below:

```python
    question_bank_keyterm_model: str = "gpt-5.4-nano-2026-03-17"
```

- [ ] **Step 2: Expose it on `AIConfig`.**

Open `backend/nexus/app/ai/config.py`. Find the existing `question_bank_model` property. Add immediately below:

```python
    @property
    def question_bank_keyterm_model(self) -> str:
        return self._settings.question_bank_keyterm_model
```

- [ ] **Step 3: Add to `.env.example`.**

Open `backend/nexus/.env.example`. Find the existing `QUESTION_BANK_MODEL=…` line. Add immediately below:

```
# Fast/cheap model for the per-bank STT keyterm extraction call. Runs
# once per bank generation; result is cached in
# stage_question_banks.extracted_keyterms. Default matches the speaker
# model (nano-class).
QUESTION_BANK_KEYTERM_MODEL=gpt-5.4-nano-2026-03-17
```

- [ ] **Step 4: Smoke-check.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.ai.config import ai_config; print(ai_config.question_bank_keyterm_model)"
```

Expected output: `gpt-5.4-nano-2026-03-17` (or whatever's in your local `.env`).

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/.env.example
git commit -m "$(cat <<'EOF'
feat(config): add question_bank_keyterm_model — gpt-5.4-nano-2026-03-17

New AIConfig field for the upcoming per-bank STT keyterm extraction LLM
call. Defaults to the same nano-class model used by Speaker — fast
and cheap; this call runs once per bank generation.
EOF
)"
```

---

## Task 4: Versioned prompt `question_bank_keyterms.txt`

**Files:**
- Create: `backend/nexus/prompts/v1/question_bank_keyterms.txt`

- [ ] **Step 1: Write the prompt.**

Create `backend/nexus/prompts/v1/question_bank_keyterms.txt` with the following content. The prompt-loader convention is `system_prompt` then a `==USER==` separator then `user_template` (verify by looking at any existing v1 prompt file, e.g., `question_bank_common.txt`, and follow its exact separator convention if different):

```
You extract speech-recognition keyterms for Deepgram STT. The output list
will boost recognition of role-specific vocabulary during a live spoken
interview, so the speech-to-text correctly transcribes brand names and
technical jargon the candidate is likely to say.

INCLUDE in the keyterms:
- Proper-noun brand / product names: MuleSoft, Salesforce, Kubernetes, PostgreSQL, ServiceNow
- Multi-word brand names as a single keyterm string: "Dell Boomi", "Apache Kafka", "Amazon Redshift"
- Acronyms commonly spoken in this role: API, REST, SOAP, ESB, ETL, SQL, JSON, XML, OAuth2, JWT
- Methodology / pattern names that are spoken jargon: API-led, microservices, event-driven, EIP, DLQ
- Mixed-case brands: iPaaS, gRPC, mTLS, eBay
- Digit-bearing identifiers: S3, EC2, Java21, Postgres15
- Industry-specific abbreviations spoken by senior practitioners in this domain

DO NOT include:
- Common English words (the, system, platform, experience, candidate)
- Generic adjectives (scalable, reliable, robust)
- Words that are unlikely to confuse a speech recognizer (plain English nouns)
- The candidate's name (added separately at session start)
- Job-title scaffolding ("Sr.", "Engineer", "Manager" by themselves)

FORMAT REQUIREMENTS:
- Preserve canonical capitalization for proper nouns: MuleSoft (not mulesoft); Salesforce (not SalesForce)
- Each entry is distinct — no near-duplicates ("API" + "APIs" → just "API")
- 20 to 40 entries total (hard maximum 50)
- Use the candidate-facing canonical name (Kubernetes, not K8s)
- Each entry must be a real spoken phrase, not generic English

==USER==
You are extracting speech-recognition keyterms for a {job_title} interview at {hiring_company_name}.

Company industry: {industry}
Company about: {company_about}
Hiring bar: {hiring_bar}

Role summary:
{role_summary}

Hiring signals (the criteria recruiters care about for this role):
{signals_bullet_list}

Final question bank for this interview stage:
{questions_block}

Extract the 20-40 most useful keyterms for Deepgram nova-3 STT recognition. Return them in the
order most likely to be spoken during the interview.
```

The exact template-variable names (`{job_title}`, `{questions_block}`, etc.) are placeholders consumed by Python's `.format(...)` in the helper from Task 5 — the helper supplies them.

- [ ] **Step 2: Verify prompt_loader picks it up.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.ai.prompts import prompt_loader; system, user_template = prompt_loader.load_pair('question_bank_common', 'question_bank_keyterms'); print(f'system: {len(system)} chars'); print(f'user_template: {len(user_template)} chars')"
```

Expected output: both strings non-empty. If `load_pair` raises `FileNotFoundError` or similar, the prompt-loader convention may need a different file path (check `app/ai/prompts.py` for how it composes the path).

- [ ] **Step 3: Commit.**

```bash
git add backend/nexus/prompts/v1/question_bank_keyterms.txt
git commit -m "$(cat <<'EOF'
feat(prompts): question_bank_keyterms — v1 prompt for STT keyterm extraction

System prompt instructs gpt-5.4-nano to extract 20-40 role-specific
speech-recognition keyterms from the bundle (job title, company profile,
role summary, signals, question bank). Includes inclusion / exclusion
criteria, canonical-capitalization rules, and multi-word phrase guidance
aligned with Deepgram nova-3 keyterm prompting docs.
EOF
)"
```

---

## Task 5: `_extract_bank_keyterms` helper in `refine.py`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/refine.py`
- Create: `backend/nexus/tests/question_bank/test_refine_keyterms.py`

- [ ] **Step 1: Write the failing test.**

Create `backend/nexus/tests/question_bank/test_refine_keyterms.py`:

```python
"""Tests for the per-bank keyterm extraction LLM helper."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.schemas import KeytermExtractionOutput
from app.modules.question_bank.refine import extract_bank_keyterms


@pytest.mark.asyncio
async def test_helper_returns_keyterm_extraction_output() -> None:
    """The helper builds the user message, calls instructor, returns the model."""
    mock_response = KeytermExtractionOutput(
        keyterms=[f"Term{i}" for i in range(15)]
    )

    fake_client = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch(
        "app.modules.question_bank.refine.get_openai_client",
        return_value=fake_client,
    ):
        result = await extract_bank_keyterms(
            job_title="Sr. Integration Engineer",
            hiring_company_name="Workato",
            industry="SaaS",
            company_about="Workato is an enterprise automation platform.",
            hiring_bar="Builders who ship.",
            role_summary="Lead end-to-end iPaaS delivery on MuleSoft / TIBCO / Boomi.",
            signals=["5+ years with MuleSoft, TIBCO, or Boomi", "API-led architecture"],
            questions=[
                {"text": "How would you design API-led connectivity for order sync?"},
                {"text": "Walk through your end-to-end MuleSoft deployment."},
            ],
            bank_id="bank-1",
            tenant_id="tenant-1",
        )

    assert isinstance(result, KeytermExtractionOutput)
    assert len(result.keyterms) == 15
    # Confirm instructor was called with the configured model + schema
    call_kwargs = fake_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["response_model"] is KeytermExtractionOutput


@pytest.mark.asyncio
async def test_helper_propagates_llm_exception() -> None:
    """LLM failure surfaces as an exception — caller (actor) catches and logs."""
    fake_client = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("simulated LLM failure")
    )

    with patch(
        "app.modules.question_bank.refine.get_openai_client",
        return_value=fake_client,
    ):
        with pytest.raises(RuntimeError, match="simulated LLM failure"):
            await extract_bank_keyterms(
                job_title="x",
                hiring_company_name="x",
                industry="x",
                company_about="x",
                hiring_bar="x",
                role_summary="x",
                signals=["x"],
                questions=[{"text": "x"}],
                bank_id="bank-1",
                tenant_id="tenant-1",
            )
```

- [ ] **Step 2: Verify the test fails.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/question_bank/test_refine_keyterms.py -v
```

Expected: `ImportError` on `extract_bank_keyterms`.

- [ ] **Step 3: Implement the helper.**

Open `backend/nexus/app/modules/question_bank/refine.py`. Append to the end of the file:

```python
from app.ai.schemas import KeytermExtractionOutput


async def extract_bank_keyterms(
    *,
    job_title: str,
    hiring_company_name: str,
    industry: str,
    company_about: str,
    hiring_bar: str,
    role_summary: str,
    signals: list[str],
    questions: list[dict],
    bank_id: str,
    tenant_id: str,
) -> KeytermExtractionOutput:
    """Extract STT keyterms for one bank via a single nano-class LLM call.

    See spec docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    Caller (generate_question_bank_stage) is expected to write the returned
    list to stage_question_banks.extracted_keyterms and to tolerate exceptions
    (an empty column is acceptable; the engine falls back to candidate-name-only).
    """
    system_prompt, user_template = prompt_loader.load_pair(
        "question_bank_common", "question_bank_keyterms",
    )

    signals_bullet_list = "\n".join(f"- {s}" for s in signals)
    questions_block = "\n\n".join(
        f"Q{i+1}: {q.get('text', '')}" for i, q in enumerate(questions)
    )

    user_message = user_template.format(
        job_title=job_title,
        hiring_company_name=hiring_company_name,
        industry=industry,
        company_about=company_about,
        hiring_bar=hiring_bar,
        role_summary=role_summary,
        signals_bullet_list=signals_bullet_list,
        questions_block=questions_block,
    )

    client = get_openai_client()

    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="question_bank_keyterms",
            prompt_version="v1",
            tenant_id=tenant_id,
            bank_id=bank_id,
        )
        result: KeytermExtractionOutput = await client.chat.completions.create(
            model=ai_config.question_bank_keyterm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_model=KeytermExtractionOutput,
        )

    return result
```

Make sure `_tracer`, `prompt_loader`, `get_openai_client`, `set_llm_span_attributes`, and `ai_config` are already imported at the top of the file (the existing refine helpers use all of these — follow their pattern).

- [ ] **Step 4: Verify both tests pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/question_bank/test_refine_keyterms.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/modules/question_bank/refine.py backend/nexus/tests/question_bank/test_refine_keyterms.py
git commit -m "$(cat <<'EOF'
feat(question-bank): extract_bank_keyterms — one LLM call per bank

New refine.py helper makes a single nano-class instructor call returning
KeytermExtractionOutput. Caller (the generate_question_bank_stage actor,
Task 6) will write the result to stage_question_banks.extracted_keyterms.
Mock-based tests cover the happy path and exception propagation.
EOF
)"
```

---

## Task 6: Wire the keyterm helper into `generate_question_bank_stage`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`
- Modify: `backend/nexus/tests/question_bank/test_generation_status_by_kind.py` (or new file `tests/question_bank/test_actors_keyterm.py`)

- [ ] **Step 1: Find the actor's final step in `actors.py`.**

Open `backend/nexus/app/modules/question_bank/actors.py`. Find `generate_question_bank_stage` (around line 735 per earlier grep). Locate the point where all per-question refinement has completed and the bank status is about to be transitioned to `confirmed` / `completed`. The new keyterm call must run AFTER all questions are persisted and BEFORE the bank transitions to its terminal status, so the column is populated when downstream consumers see the bank as ready.

- [ ] **Step 2: Add the keyterm extraction step.**

Insert the following block at the identified location (the exact local-variable names will depend on the actor's existing context; the engineer should map `job`, `company_profile`, `snapshot`, `bank`, `final_questions` to the actual names in scope):

```python
# Final step: extract STT keyterms for Deepgram nova-3 prompting (2026-05-19 spec).
# Runs ONCE per bank generation; result cached in extracted_keyterms.
# Failures here are NOT fatal — log and continue (the engine falls back
# to candidate-name-only STT boosting).
try:
    keyterm_output = await extract_bank_keyterms(
        job_title=job.title,
        hiring_company_name=(company_profile.org_unit_name or ""),
        industry=company_profile.industry,
        company_about=company_profile.about,
        hiring_bar=company_profile.hiring_bar,
        role_summary=snapshot.role_summary,
        signals=[s.value for s in snapshot.signals],
        questions=[{"text": q.text} for q in final_questions],
        bank_id=str(bank.id),
        tenant_id=str(bank.tenant_id),
    )
    await db.execute(
        update(StageQuestionBankModel)
        .where(StageQuestionBankModel.id == bank.id)
        .values(extracted_keyterms=keyterm_output.keyterms),
    )
    logger.info(
        "question_bank.keyterm_extraction.complete",
        bank_id=str(bank.id),
        count=len(keyterm_output.keyterms),
    )
except Exception:
    logger.exception(
        "question_bank.keyterm_extraction.failed",
        bank_id=str(bank.id),
    )
    # Do not re-raise — keyterm extraction is best-effort.
```

Add to the imports at the top of `actors.py` if not already present:

```python
from app.modules.question_bank.refine import extract_bank_keyterms
from sqlalchemy import update
```

- [ ] **Step 3: Write the actor-level test.**

Create `backend/nexus/tests/question_bank/test_actors_keyterm.py`:

```python
"""Integration test: bank actor writes extracted_keyterms to the DB row."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.schemas import KeytermExtractionOutput


@pytest.mark.asyncio
async def test_actor_writes_keyterms_to_bank_row(
    # The exact fixtures depend on the existing test harness for
    # generate_question_bank_stage — follow tests/question_bank/test_generation_status_by_kind.py
    # for the right fixture names (db, fully_seeded_bank_id, etc.).
) -> None:
    """When extract_bank_keyterms returns a valid output, the actor persists it."""
    mock_keyterms = KeytermExtractionOutput(
        keyterms=[f"Brand{i}" for i in range(15)]
    )

    with patch(
        "app.modules.question_bank.actors.extract_bank_keyterms",
        AsyncMock(return_value=mock_keyterms),
    ):
        # Run generate_question_bank_stage to completion using existing fixtures.
        # ... (engineer fills in based on the harness) ...
        pass

    # Assert the bank row's extracted_keyterms column matches mock_keyterms.keyterms.


@pytest.mark.asyncio
async def test_actor_tolerates_keyterm_extraction_failure() -> None:
    """LLM failure does NOT crash the actor; the column stays NULL."""
    with patch(
        "app.modules.question_bank.actors.extract_bank_keyterms",
        AsyncMock(side_effect=RuntimeError("simulated")),
    ):
        # Run the actor; it should complete successfully (no raise).
        pass
    # Assert the bank row's extracted_keyterms is None.
```

NOTE: this test file has placeholders because the existing fixture conventions for `generate_question_bank_stage` aren't captured here. Engineer fills in by mirroring `tests/question_bank/test_generation_status_by_kind.py`. If that's too much friction, skip this test for v1 and rely on the unit test from Task 5 — the actor integration is verified manually during the end-to-end smoke (Task 14).

- [ ] **Step 4: Run the test suite to confirm no existing regressions.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/question_bank -v -x
```

Expected: all pre-existing question_bank tests still pass; the new keyterm tests from Task 5 still pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/modules/question_bank/actors.py \
        backend/nexus/tests/question_bank/test_actors_keyterm.py
git commit -m "$(cat <<'EOF'
feat(question-bank): actor writes extracted_keyterms after refinement

generate_question_bank_stage now appends one final call to
extract_bank_keyterms and writes the result to
stage_question_banks.extracted_keyterms before the bank transitions to
its terminal status. Failures are logged and swallowed — the engine
falls back to candidate-name-only STT boosting if the column is NULL.
EOF
)"
```

---

## Task 7: Add `SessionConfig.keyterms` field

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/schemas.py`

- [ ] **Step 1: Add the field.**

Open `backend/nexus/app/modules/interview_runtime/schemas.py`. Find the `SessionConfig` class (around line 181). Locate the existing `signal_metadata: list[SignalMetadata]` field. Add immediately after it:

```python
    keyterms: list[str] = Field(
        default_factory=list,
        description=(
            "STT keyterm-prompting list, extracted at bank-generation time "
            "(see question_bank/refine.py:extract_bank_keyterms) and cached "
            "on stage_question_banks.extracted_keyterms. Empty list when the "
            "bank hasn't had keyterm extraction run yet — the engine then "
            "falls back to candidate-name-only boosting. See spec "
            "docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md."
        ),
    )
```

- [ ] **Step 2: Smoke-check round-trip.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "
from app.modules.interview_runtime.schemas import SessionConfig
sc = SessionConfig.model_construct(keyterms=['MuleSoft', 'TIBCO'])
print(sc.keyterms)
"
```

Expected: `['MuleSoft', 'TIBCO']`.

- [ ] **Step 3: Commit.**

```bash
git add backend/nexus/app/modules/interview_runtime/schemas.py
git commit -m "$(cat <<'EOF'
feat(interview-runtime): SessionConfig.keyterms — STT keyterm cache pass-through

Additive Pydantic field on the SessionConfig wire contract. Populated
by build_session_config from stage_question_banks.extracted_keyterms;
consumed by the interview engine for Deepgram nova-3 keyterm prompting.
Defaults to [] so legacy callers and pre-extraction banks are
backward-compatible.
EOF
)"
```

---

## Task 8: Wire `build_session_config` to load `extracted_keyterms`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py`
- Modify: `backend/nexus/tests/interview_runtime/` (new file or existing)

- [ ] **Step 1: Write the failing test.**

Find the existing test that exercises `build_session_config` (search `tests/interview_runtime/` for `build_session_config`). Append (or create a new file `test_build_session_config_keyterms.py`):

```python
@pytest.mark.asyncio
async def test_build_session_config_loads_extracted_keyterms_when_present(
    db, seeded_session_with_confirmed_bank
) -> None:
    """When the bank row has extracted_keyterms set, SessionConfig.keyterms reflects it."""
    # Pre-set the column on the bank row
    await db.execute(
        update(StageQuestionBankModel)
        .where(StageQuestionBankModel.id == seeded_session_with_confirmed_bank.bank_id)
        .values(extracted_keyterms=["MuleSoft", "TIBCO", "Boomi"]),
    )
    await db.commit()

    sc = await build_session_config(
        db,
        session_id=seeded_session_with_confirmed_bank.session_id,
        tenant_id=seeded_session_with_confirmed_bank.tenant_id,
    )
    assert sc.keyterms == ["MuleSoft", "TIBCO", "Boomi"]


@pytest.mark.asyncio
async def test_build_session_config_keyterms_empty_when_column_null(
    db, seeded_session_with_confirmed_bank
) -> None:
    """When extracted_keyterms IS NULL, SessionConfig.keyterms is an empty list."""
    sc = await build_session_config(
        db,
        session_id=seeded_session_with_confirmed_bank.session_id,
        tenant_id=seeded_session_with_confirmed_bank.tenant_id,
    )
    assert sc.keyterms == []
```

Adapt the fixture name `seeded_session_with_confirmed_bank` to whatever the existing harness in `tests/interview_runtime/` uses. If the test harness is unfamiliar, copy the setup from the closest existing `build_session_config` test in the same directory.

- [ ] **Step 2: Verify the tests fail.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_runtime/test_build_session_config_keyterms.py -v
```

Expected: `AssertionError: assert [] == ['MuleSoft', 'TIBCO', 'Boomi']` on the first test (because `build_session_config` doesn't yet read the new column).

- [ ] **Step 3: Modify `build_session_config` to read the column.**

Open `backend/nexus/app/modules/interview_runtime/service.py`. Find the section (around lines 123–135 per earlier exploration) that loads the question bank row. Wherever the function returns or constructs `SessionConfig(...)`, add the `keyterms` argument sourced from the bank row:

```python
keyterms_value: list[str] = []
if bank.extracted_keyterms is not None:
    # JSONB → Python list. Stored as list[str]; defensive copy.
    keyterms_value = list(bank.extracted_keyterms)

# ... (existing SessionConfig construction) ...

return SessionConfig(
    # ... (existing fields) ...
    keyterms=keyterms_value,
)
```

- [ ] **Step 4: Verify both tests pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_runtime/test_build_session_config_keyterms.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py \
        backend/nexus/tests/interview_runtime/test_build_session_config_keyterms.py
git commit -m "$(cat <<'EOF'
feat(interview-runtime): propagate extracted_keyterms into SessionConfig

build_session_config now reads stage_question_banks.extracted_keyterms
and threads it onto SessionConfig.keyterms for the engine to consume.
NULL column → empty list (engine falls back to candidate-name-only).
EOF
)"
```

---

## Phase B — Engine-side keyterm consumption

## Task 9: Register the `audio.stt.keyterms_applied` audit event

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/event_kinds.py`
- Modify: `backend/nexus/app/modules/interview_engine/audit_events.py`

- [ ] **Step 1: Add the event-kind constant.**

In `event_kinds.py`, after `AUDIO_STT_TRANSCRIBED = "audio.stt.transcribed"`, add:

```python
AUDIO_STT_KEYTERMS_APPLIED = "audio.stt.keyterms_applied"
```

- [ ] **Step 2: Add the payload model.**

Append to `audit_events.py`:

```python
# STT keyterm prompting (Phase 3D.deepgram-keyterm — 2026-05-19)
class STTKeytermsAppliedPayload(BaseModel):
    """One-shot record of the keyterm list passed to the STT plugin at session start."""

    provider: Literal["sarvam", "deepgram"]
    count: int = Field(ge=0)
    terms: list[str]
    sources: dict[str, int]
```

- [ ] **Step 3: Smoke-import.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine.event_kinds import AUDIO_STT_KEYTERMS_APPLIED; from app.modules.interview_engine.audit_events import STTKeytermsAppliedPayload; print(AUDIO_STT_KEYTERMS_APPLIED); print(STTKeytermsAppliedPayload(provider='deepgram', count=2, terms=['a','b'], sources={'x': 2}).model_dump())"
```

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/event_kinds.py \
        backend/nexus/app/modules/interview_engine/audit_events.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): register audio.stt.keyterms_applied audit event

Event kind + STTKeytermsAppliedPayload Pydantic model. Emitted once per
session by agent.py with the keyterms passed to the STT plugin.
EOF
)"
```

---

## Task 10: Engine merger `assemble_keyterms`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/keyterms.py`
- Create: `backend/nexus/tests/interview_engine/test_keyterms.py`

- [ ] **Step 1: Write the failing tests.**

Create `backend/nexus/tests/interview_engine/test_keyterms.py`:

```python
"""Unit tests for the engine-side keyterm merger.

Reference spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from app.modules.interview_engine.keyterms import KeytermExtraction, assemble_keyterms
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    SessionConfig,
    StageConfig,
)


def _make_session_config(
    *,
    candidate_name: str = "Ishant Pundir",
    keyterms: list[str] | None = None,
) -> SessionConfig:
    return SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        job_title="Sr. Integration Engineer",
        hiring_company_name="Workato",
        role_summary="x",
        jd_text=None,
        seniority_level="senior",
        company=CompanyContext(about="x", industry="x", hiring_bar="x"),
        candidate=CandidateContext(name=candidate_name),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000004",
            stage_type="ai_screening",
            name="Bot Screening",
            duration_minutes=15,
            difficulty="hard",
            questions=[],
            advance_behavior="auto_advance",
        ),
        signals=[],
        signal_metadata=[],
        keyterms=keyterms or [],
    )


class TestAssembleKeyterms:
    def test_returns_keyterm_extraction(self) -> None:
        result = assemble_keyterms(_make_session_config())
        assert isinstance(result, KeytermExtraction)

    def test_empty_keyterms_falls_back_to_candidate_first_name(self) -> None:
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant Pundir", keyterms=[]),
        )
        assert result.terms == ["Ishant"]
        assert result.sources == {"candidate_name": 1}

    def test_keyterms_merged_after_candidate_name(self) -> None:
        result = assemble_keyterms(
            _make_session_config(
                candidate_name="Ishant",
                keyterms=["MuleSoft", "TIBCO", "Boomi"],
            )
        )
        assert result.terms == ["Ishant", "MuleSoft", "TIBCO", "Boomi"]
        assert result.sources == {"candidate_name": 1, "bank_cached": 3}

    def test_case_insensitive_dedupe_first_seen_wins(self) -> None:
        result = assemble_keyterms(
            _make_session_config(
                candidate_name="MuleSoft",  # contrived collision
                keyterms=["mulesoft", "TIBCO"],
            )
        )
        # First-seen "MuleSoft" wins; the second "mulesoft" entry is dropped
        lowered = [t.lower() for t in result.terms]
        assert lowered.count("mulesoft") == 1
        assert "MuleSoft" in result.terms
        assert "TIBCO" in result.terms

    def test_cap_at_fifty(self) -> None:
        many = [f"Brand{i}Term" for i in range(100)]
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant", keyterms=many),
        )
        assert len(result.terms) == 50
        assert result.terms[0] == "Ishant"  # candidate-name survives at the front

    def test_candidate_first_token_only(self) -> None:
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant Pundir Kumar"),
        )
        assert "Ishant" in result.terms
        assert "Pundir" not in result.terms
        assert "Kumar" not in result.terms

    def test_empty_candidate_name_handled(self) -> None:
        result = assemble_keyterms(
            _make_session_config(
                candidate_name="",
                keyterms=["MuleSoft"],
            )
        )
        # No candidate-name term emitted
        assert "candidate_name" not in result.sources
        assert result.terms == ["MuleSoft"]
```

- [ ] **Step 2: Verify tests fail.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the merger.**

Create `backend/nexus/app/modules/interview_engine/keyterms.py`:

```python
"""Engine-side keyterm assembly for Deepgram nova-3 STT.

The heavy lifting (LLM-driven keyterm extraction from the job + question bank
+ company profile) happens upstream in question_bank/refine.py at
bank-generation time and is cached on stage_question_banks.extracted_keyterms.

This module's only job is to merge that cached list with session-specific
context (the candidate's first name) and produce a final, deduped, capped
list for deepgram.STT(keyterm=[...]).

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime.schemas import SessionConfig

_KEYTERM_CAP = 50


@dataclass(frozen=True)
class KeytermExtraction:
    """Output of assemble_keyterms — final list + per-source attribution counts."""

    terms: list[str]
    sources: dict[str, int]


def assemble_keyterms(session_config: SessionConfig) -> KeytermExtraction:
    terms: list[str] = []
    sources: dict[str, int] = {}

    def _add(term: str, source: str) -> None:
        if not term:
            return
        if len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == term.lower() for t in terms):
            return
        terms.append(term)
        sources[source] = sources.get(source, 0) + 1

    # Candidate first name — the only session-specific term
    if session_config.candidate.name.strip():
        _add(session_config.candidate.name.split()[0], "candidate_name")

    # Bank-cached terms (LLM-extracted at bank-generation time)
    for term in session_config.keyterms:
        _add(term, "bank_cached")

    return KeytermExtraction(terms=terms, sources=sources)
```

- [ ] **Step 4: Verify all 7 tests pass.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine/test_keyterms.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/keyterms.py \
        backend/nexus/tests/interview_engine/test_keyterms.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): assemble_keyterms — merge bank cache + candidate name

20-line pure function: prepends the candidate's first name to the
bank-cached keyterm list, dedupes case-insensitively, caps at 50.
Heavy lifting (LLM extraction) happens upstream in question_bank/refine.
Empty session_config.keyterms falls back to [candidate.first_name]
gracefully.
EOF
)"
```

---

## Task 11: Update `stt_factory.py` to plumb the merger through

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/stt_factory.py`

- [ ] **Step 1: Replace `stt_factory.py` contents.**

Replace the entire file with:

```python
"""Per-session STT plugin factory — keyterm assembly seam.

The factory function returns BOTH the STT plugin and the KeytermExtraction
so the caller (agent.py) can emit a single `audio.stt.keyterms_applied`
audit event without re-running the assembler.

Sarvam ignores the keyterms argument (no equivalent feature). The actual
provider dispatch lives in app/ai/realtime.py.

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.realtime import build_stt_plugin
from app.modules.interview_engine.keyterms import KeytermExtraction, assemble_keyterms
from app.modules.interview_runtime.schemas import SessionConfig

if TYPE_CHECKING:
    from livekit.agents.stt import STT as _BaseSTT


def build_stt_plugin_for_session(
    *, session_config: SessionConfig,
) -> tuple["_BaseSTT", KeytermExtraction]:
    extraction = assemble_keyterms(session_config)
    return build_stt_plugin(keyterms=extraction.terms), extraction
```

- [ ] **Step 2: Smoke-import.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine.stt_factory import build_stt_plugin_for_session; print(build_stt_plugin_for_session)"
```

- [ ] **Step 3: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/stt_factory.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): stt_factory wires assemble_keyterms into Deepgram

The seam now runs the engine-side keyterm merger (candidate name + bank
cache) and forwards the list to build_stt_plugin, returning both the STT
plugin and the full KeytermExtraction for the upcoming audit event.
EOF
)"
```

---

## Task 12: Update `realtime.py` to forward keyterms to Deepgram

**Files:**
- Modify: `backend/nexus/app/ai/realtime.py`

- [ ] **Step 1: Update `build_stt_plugin` signature.**

Find the function at `app/ai/realtime.py:39`. Replace with:

```python
def build_stt_plugin(keyterms: list[str] | None = None) -> "_BaseSTT":
    """Construct the realtime STT plugin selected by AIConfig.

    Provider chosen by AIConfig.interview_stt_provider. Default 'deepgram'
    (nova-3); 'sarvam' (saaras:v3) is the switchable alternate.

    `keyterms` is the Deepgram nova-3 keyterm-prompting list (10-50
    role-specific terms; see spec
    docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
    Sarvam ignores it (no equivalent feature). Pass None to skip boosting.
    """
    provider = ai_config.interview_stt_provider
    if provider == "sarvam":
        return _build_stt_sarvam()
    if provider == "deepgram":
        return _build_stt_deepgram(keyterms=keyterms)
    raise ValueError(
        f"Unknown interview_stt_provider {provider!r}; "
        "expected 'sarvam' or 'deepgram'."
    )
```

- [ ] **Step 2: Update `_build_stt_deepgram` to accept and forward the kwarg.**

Replace the existing `_build_stt_deepgram` body:

```python
def _build_stt_deepgram(*, keyterms: list[str] | None = None) -> "_BaseSTT":
    """Deepgram STT (default). Auth via DEEPGRAM_API_KEY env."""
    from livekit.plugins import deepgram

    kwargs: dict[str, object] = {
        "model": ai_config.interview_stt_model,
        "language": ai_config.interview_stt_language,
    }
    if keyterms:
        kwargs["keyterm"] = keyterms

    logger.info(
        "ai.realtime.stt.built",
        provider="deepgram",
        model=ai_config.interview_stt_model,
        language=ai_config.interview_stt_language,
        keyterm_count=len(keyterms) if keyterms else 0,
    )
    return deepgram.STT(**kwargs)
```

- [ ] **Step 3: Verify the module imports.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.ai.realtime import build_stt_plugin; help(build_stt_plugin)"
```

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/ai/realtime.py
git commit -m "$(cat <<'EOF'
feat(ai/realtime): forward keyterms to deepgram.STT as `keyterm` kwarg

build_stt_plugin gains an optional keyterms list[str]; when provider is
deepgram and keyterms is non-empty, the list is passed as the keyterm
REST API parameter. Sarvam ignores it. Logs keyterm_count for forensics.
EOF
)"
```

---

## Task 13: Wire the audit event in `agent.py`

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Add the imports.**

Open `agent.py`. Near the existing interview_engine imports, add:

```python
from app.modules.interview_engine.audit_events import STTKeytermsAppliedPayload
from app.modules.interview_engine.event_kinds import AUDIO_STT_KEYTERMS_APPLIED
```

Also confirm `ai_config` is imported (it's used for `model_versions` already, around line 410, so it should be in scope).

- [ ] **Step 2: Refactor the `AgentSession` construction.**

Find the line `stt=build_stt_plugin_for_session(session_config=session_config),` (around line 468). Replace the entire `session = AgentSession(...)` block:

```python
stt_plugin, keyterm_extraction = build_stt_plugin_for_session(
    session_config=session_config,
)
event_collector.append(
    kind=AUDIO_STT_KEYTERMS_APPLIED,
    payload=STTKeytermsAppliedPayload(
        provider=ai_config.interview_stt_provider,
        count=len(keyterm_extraction.terms),
        terms=keyterm_extraction.terms,
        sources=keyterm_extraction.sources,
    ).model_dump(mode="json"),
)

session = AgentSession(
    stt=stt_plugin,
    llm=build_llm_plugin(),
    tts=tts_plugin,
    vad=build_vad(),
    turn_handling=TurnHandlingOptions(
        turn_detection=build_turn_detector(),
        preemptive_generation={"enabled": False},
        endpointing={
            "mode": settings.engine_endpointing_mode,
            "min_delay": settings.engine_endpointing_min_delay,
            "max_delay": settings.engine_endpointing_max_delay,
        },
        interruption=build_interruption_options(),
    ),
)
```

CRITICAL: copy the `TurnHandlingOptions(...)` argument block VERBATIM from the existing code — preserve `turn_detection`, `preemptive_generation`, `endpointing`, and `interruption` exactly. Only the STT line + the new audit-event emission change. The continuation watcher in `orchestrator.py` is downstream of this; do NOT touch it.

- [ ] **Step 3: Verify the module imports.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.modules.interview_engine import agent"
```

- [ ] **Step 4: Run the full engine test suite.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/interview_engine -v
```

Expected: all pre-existing tests pass + 7 new keyterm-merger tests pass. If any existing test fails because it built `AgentSession(stt=build_stt_plugin_for_session(...))` and now needs the tuple shape, fix it by changing the call to `stt, _ = build_stt_plugin_for_session(...)`.

- [ ] **Step 5: Commit.**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(interview-engine): emit audio.stt.keyterms_applied at session start

agent.py unpacks the (stt, KeytermExtraction) tuple, emits the new audit
event with the full keyterm list and per-source counts, then hands the
STT to AgentSession. orchestrator.py and its continuation watcher are
not touched.
EOF
)"
```

---

## Phase C — Defaults flip + smoke

## Task 14: Flip provider defaults to Deepgram

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`

- [ ] **Step 1: Flip the `Settings` field defaults.**

Open `backend/nexus/app/config.py`. Find lines 460-462:

```python
    interview_stt_provider: Literal["sarvam", "deepgram"] = "sarvam"
    interview_stt_model: str = "saaras:v3"
    interview_stt_language: str = "en-IN"
```

Replace with:

```python
    interview_stt_provider: Literal["sarvam", "deepgram"] = "deepgram"
    interview_stt_model: str = "nova-3"
    interview_stt_language: str = "en-IN"
```

Leave `interview_stt_mode` unchanged.

- [ ] **Step 2: Flip `.env.example`.**

Find lines 164-172 in `.env.example`:

```
# STT — provider-switchable. Default sarvam (saaras:v3, en-IN, code-mix capable).
# To use Deepgram (rollback path): set INTERVIEW_STT_PROVIDER=deepgram AND
# INTERVIEW_STT_MODEL=nova-3 AND INTERVIEW_STT_LANGUAGE=en.
# Mode applies to Sarvam saaras:v3 only (transcribe | translate | verbatim |
# translit | codemix). Deepgram ignores INTERVIEW_STT_MODE.
INTERVIEW_STT_PROVIDER=sarvam
INTERVIEW_STT_MODEL=saaras:v3
INTERVIEW_STT_LANGUAGE=en-IN
INTERVIEW_STT_MODE=transcribe
```

Replace with:

```
# STT — provider-switchable. Default deepgram (nova-3, en-IN, with per-session
# keyterm prompting — see
# docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
# Keyterms are LLM-extracted at bank-generation time and cached on
# stage_question_banks.extracted_keyterms. The engine merges them with the
# candidate's first name at session start.
# To switch to Sarvam (alternate, e.g. for code-mix Hindi/English candidates):
# set INTERVIEW_STT_PROVIDER=sarvam, INTERVIEW_STT_MODEL=saaras:v3,
# INTERVIEW_STT_LANGUAGE=en-IN, INTERVIEW_STT_MODE=codemix.
# INTERVIEW_STT_MODE applies to Sarvam only.
INTERVIEW_STT_PROVIDER=deepgram
INTERVIEW_STT_MODEL=nova-3
INTERVIEW_STT_LANGUAGE=en-IN
INTERVIEW_STT_MODE=transcribe
```

- [ ] **Step 3: Smoke-check.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "from app.config import settings; print(settings.interview_stt_provider, settings.interview_stt_model, settings.interview_stt_language)"
```

Expected: `deepgram nova-3 en-IN` (unless your local `.env` overrides).

- [ ] **Step 4: Commit.**

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example
git commit -m "$(cat <<'EOF'
feat(config): flip default STT provider to deepgram/nova-3/en-IN

Sarvam remains as a switchable alternate (INTERVIEW_STT_PROVIDER=sarvam
in .env). Deepgram nova-3 with en-IN now gains the per-session keyterm
boost wired in the earlier commits — keyterms LLM-extracted at bank
generation and cached on stage_question_banks.extracted_keyterms.
EOF
)"
```

---

## Task 15: End-to-end smoke

**Files:** None modified. Verification only.

- [ ] **Step 1: Update the local `.env` for Deepgram.**

Inspect `backend/nexus/.env`. Confirm:

```
DEEPGRAM_API_KEY=<your real key — already present per user>
INTERVIEW_STT_PROVIDER=deepgram
INTERVIEW_STT_MODEL=nova-3
INTERVIEW_STT_LANGUAGE=en-IN
QUESTION_BANK_KEYTERM_MODEL=gpt-5.4-nano-2026-03-17
```

- [ ] **Step 2: Run the full test suite once.**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest -v
```

Expected: all tests pass. Triage any regressions (most likely an existing test that built `AgentSession(stt=build_stt_plugin_for_session(...))` and needs the tuple-unpack fix).

- [ ] **Step 3: Regenerate one question bank to populate `extracted_keyterms`.**

Via the recruiter dashboard (or API), trigger regeneration of one existing bank. After completion, inspect the row:

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus python -c "
import asyncio
from sqlalchemy import text
from app.database import get_bypass_db

async def check():
    async with get_bypass_db() as db:
        result = await db.execute(text(\"SELECT id, extracted_keyterms FROM stage_question_banks WHERE extracted_keyterms IS NOT NULL LIMIT 3\"))
        for row in result.fetchall():
            print(row.id, row.extracted_keyterms)

asyncio.run(check())
"
```

Expected: at least one bank with a list of 20–40 reasonable keyterms. Eyeball the list — does it contain the role's brand names (e.g., `MuleSoft`, `Salesforce`)? If not, the prompt may need iteration — revise `prompts/v1/question_bank_keyterms.txt` and regenerate.

- [ ] **Step 4: Start the stack.**

```bash
docker compose -f backend/nexus/docker-compose.yml up -d
docker compose -f backend/nexus/docker-compose.yml logs nexus | grep -E "ai.realtime.stt.built|stt.keyterms"
```

Expected: clean boot.

- [ ] **Step 5: Run one real interview against the live stack.**

This is your responsibility — talk to the agent yourself per the project's documented preference for manual AI-agent testing. In the resulting `engine-events/<session_id>.json`, confirm:

1. `model_versions.stt` reads `deepgram/nova-3`.
2. Exactly one `audio.stt.keyterms_applied` event is present, near the start.
3. `payload.terms` contains your first name + 20–40 role-specific keyterms.
4. `audio.stt.transcribed` events show correct spelling for the bank's brand names (e.g., `MuleSoft`, not `mule soft`).
5. **Continuation watcher still functional:** if you pause mid-sentence during the session, `turn.aborted_for_continuation` + `turn.stitched_continuation` events should still fire as before. The Phase 3D.deepgram migration must NOT have regressed this — that's the load-bearing assertion from the spec's Non-goals.

- [ ] **Step 6: Tear down.**

```bash
docker compose -f backend/nexus/docker-compose.yml down
```

- [ ] **Step 7 (optional): Tag the commit.**

```bash
git tag -a phase-3d-deepgram-keyterm-llm -m "Deepgram nova-3 + en-IN + LLM-extracted keyterm prompting complete"
```

---

## Self-Review Notes

**Spec coverage:**
- Migration 0029 (spec §1) → Task 1
- KeytermExtractionOutput schema (spec §2) → Task 2
- Prompt file (spec §3) → Task 4
- Bank-actor extension (spec §4) → Tasks 5, 6
- AIConfig field (spec §5) → Task 3
- SessionConfig.keyterms (spec §6) → Tasks 7, 8
- Engine merger (spec §7) → Task 10
- STT factory + realtime + agent wiring (spec §8) → Tasks 11, 12, 13
- Defaults flip (spec §9) → Task 14
- Audit event (spec §10) → Tasks 9, 13
- Tests (spec §11) → Distributed across all tasks
- Continuation-watcher preservation (spec Non-goals) → No task modifies `orchestrator.py`; explicitly asserted in Task 13 Step 2 ("do NOT touch") and Task 15 Step 5 (manual verification).

**Placeholder scan:** Task 6 Step 3's actor-integration test has placeholders because it requires the existing question-bank-actor fixture conventions. The engineer can either fill them in by mirroring `tests/question_bank/test_generation_status_by_kind.py`, or skip and rely on the Task 5 unit tests plus the Task 15 manual smoke. This is the only intentional placeholder in the plan.

**Type consistency:** `assemble_keyterms` is consistently `(SessionConfig) -> KeytermExtraction`. `extract_bank_keyterms` is consistently `(*, …) -> KeytermExtractionOutput`. `build_stt_plugin_for_session` is consistently `(*, session_config) -> tuple[_BaseSTT, KeytermExtraction]`. `build_stt_plugin` is consistently `(keyterms: list[str] | None = None) -> _BaseSTT`. Audit-event constant (`AUDIO_STT_KEYTERMS_APPLIED`) and payload model (`STTKeytermsAppliedPayload`) match across files.
