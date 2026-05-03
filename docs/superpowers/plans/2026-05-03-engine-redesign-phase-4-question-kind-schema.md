# Engine Redesign — Phase 4: `question_kind` schema + bank-generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `question_kind` data layer end-to-end so the engine's already-shipped per-kind task routing fires on real questions: DB column with CHECK + DEFAULT, ORM field, strict 3-value Literal on the LLM output schema, bank-generator + regen-one persistence, prompt edits with kind-selection guidance, and runtime read into `QuestionConfig`.

**Architecture:** Strictly additive. PG11+ metadata-only column add (no table rewrite). Bank-generator emits `question_kind` per question via instructor-validated Pydantic field; the new common-prompt §6 + per-stage calibration paragraphs teach the LLM how to pick a kind. No backfill of existing banks — recruiter-triggered regenerate is the upgrade path. Recruiter API surface (request/response schemas) is intentionally untouched. Engine-side schemas + factory + tasks are already correct from Phase 3.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async (asyncpg), Alembic, Pydantic v2, instructor + OpenAI, pytest + pytest-asyncio. Local dev: Docker Compose, Supabase Postgres on `:54322`.

**Spec:** [`docs/superpowers/specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md`](../specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md)

**Working agreement:** Stay on `main`. Per-task commits. The session that completes the final task updates the overview spec's `Phase status index` row in the same commit. Skip e2e — manual end-to-end runs after Phase 6.

---

## File structure

Each task touches a focused set of files. Listed by responsibility, not by task:

| File | Role | Phase 4 change |
|---|---|---|
| `migrations/versions/0026_question_kind_column.py` (NEW) | Column add + CHECK constraint + post-migration-state docstring | T1 |
| `app/modules/question_bank/models.py` | `StageQuestion` ORM mapping | T2 — add column + `__table_args__` CheckConstraint |
| `app/modules/question_bank/schemas.py` | `GeneratedQuestion` LLM-output schema | T3 — add strict 3-value Literal field, required |
| `app/modules/question_bank/service.py` | `write_generated_questions` + `replace_question_in_place` + `create_recruiter_question` | T4, T5, T6 — persist new field on each path |
| `prompts/v1/question_bank_common.txt` | Shared system header | T7 — append §6 |
| `prompts/v1/question_bank_phone_screen.txt` | Per-stage prompt | T7 — append calibration |
| `prompts/v1/question_bank_ai_screening.txt` | Per-stage prompt | T7 — append calibration with HARD BAN |
| `prompts/v1/question_bank_regenerate_one.txt` | Regen-one prompt | T7 — append "preserve prior kind" rule |
| `app/modules/interview_runtime/service.py` (line 189 area) | `build_session_config` constructor | T8 — pass `question_kind=q.question_kind` |
| `tests/test_question_banks_schemas.py` | Schema validation unit tests | T3 — extend |
| `tests/test_question_banks_actors.py` | Actor mock tests | T3 — fixture update; T4 — persistence assertions |
| `tests/test_question_banks_service.py` | Service unit tests | T3 — fixture update; T4, T5, T6 — persistence assertions |
| `tests/test_question_banks_events.py` | Pubsub/regen events | T3 — fixture update |
| `tests/test_question_banks_integration.py` | Integration | T3 — fixture update |
| `tests/test_question_banks_migration_0026.py` (NEW) | Migration-level constraint behavior | T1 (extends in T3 once Literal lands) |
| `tests/interview_runtime/test_service.py` (NEW or extend) | `build_session_config` runtime read | T8 |
| `tests/test_question_banks_prompt_quality.py` (NEW, `prompt_quality` tier) | Real-LLM kind selection coverage | T9 |
| `backend/nexus/CLAUDE.md` | Migration list + Phase 4 status block | T10 |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Phase status row | T11 |

**Files explicitly NOT touched in Phase 4** (any edit here is out of scope):
- `app/modules/interview_engine/tasks/{factory.py,base.py,technical_depth.py,behavioral.py,compliance_binary.py}` — Phase 3 correct.
- `app/modules/interview_engine/controller.py` — Phase 3 correct.
- Any prompt under `prompts/v1/interview/` — engine prompts, not bank-gen.
- `prompts/v1/question_bank_human_interview.txt`, `prompts/v1/question_bank_take_home.txt` — exist on disk but no callers.
- `app/modules/question_bank/router.py`, `prompts/v1/question_create_single.txt`, `prompts/v1/question_refine_single.txt`, `app/modules/question_bank/refine.py` — recruiter-mediated, separate response schemas.
- `CreateQuestionBody`, `UpdateQuestionBody`, `QuestionResponse` in `schemas.py` — recruiter API surface, intentionally clean.
- Any frontend file.

---

## Task 1: Alembic migration 0026 — column + CHECK + docstring

**Files:**
- Create: `backend/nexus/migrations/versions/0026_question_kind_column.py`
- Test: `backend/nexus/tests/test_question_banks_migration_0026.py` (NEW)

**Why first:** Every later code path either reads or writes the column. Landing the migration first means every subsequent commit leaves the system coherent.

- [ ] **Step 1: Write the failing migration test**

The convention in this repo is precedented by `tests/test_migration_0014.py` — `db` fixture from conftest, `create_test_*` helpers from conftest, ORM round-trip + IntegrityError for CHECK violations. Create `backend/nexus/tests/test_question_banks_migration_0026.py`:

```python
"""ORM smoke tests for migration 0026 (Phase 4).

Covers:
- StageQuestion.question_kind default = 'technical_depth' on plain insert.
- All four engine-side Literal values round-trip through the column.
- CHECK constraint rejects an out-of-allowlist value.

Tested against the create_all-built test DB (see tests/conftest.py).
The CHECK constraint and server_default are mirrored on the ORM model
in app/modules/question_bank/models.py via __table_args__ +
server_default so this test file exercises the same behavior under
create_all that production gets via the raw-SQL Alembic migration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "company_stage": "Series C",
    "hiring_bar": "standard",
}


async def _seed_bank_and_question_kwargs(db) -> tuple[StageQuestionBank, dict]:
    """Build the minimum graph (client → user → org_unit → job → snapshot →
    instance → stage → bank) and return (bank, kwargs-for-StageQuestion)."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(
        __import__("sqlalchemy").text(
            f"SET LOCAL app.current_tenant = '{tenant.id}'"
        )
    )

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python", "type": "competency", "priority": "required",
                "weight": 2, "knockout": False, "stage": "screen",
                "evaluation_method": "verbal_response",
                "evaluation_hint": None, "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id, job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="medium",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="draft",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    base_kwargs = dict(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Walk me through a production incident you handled.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric={
            "excellent": "x" * 25, "meets_bar": "y" * 25, "below_bar": "z" * 25,
        },
        evaluation_hint="Strong answer names tools, describes structured approach.",
    )
    return bank, base_kwargs


@pytest.mark.asyncio
async def test_question_kind_default_is_technical_depth(db):
    """Inserting a StageQuestion without question_kind reads back as 'technical_depth'."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    question = StageQuestion(**base_kwargs)
    db.add(question)
    await db.flush()
    await db.refresh(question)
    assert question.question_kind == "technical_depth"


@pytest.mark.parametrize(
    "kind",
    ["technical_depth", "behavioral_star", "compliance_binary", "open_culture"],
)
@pytest.mark.asyncio
async def test_question_kind_accepts_each_allowlist_value(db, kind):
    """All 4 engine-side Literal values round-trip through the DB column."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    question = StageQuestion(**base_kwargs, question_kind=kind)
    db.add(question)
    await db.flush()
    await db.refresh(question)
    assert question.question_kind == kind


@pytest.mark.asyncio
async def test_question_kind_check_rejects_invalid_value(db):
    """The CHECK constraint rejects any value outside the 4-value allowlist."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    bad = StageQuestion(**base_kwargs, question_kind="not_a_real_kind")
    db.add(bad)
    with pytest.raises(IntegrityError):
        await db.flush()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec nexus pytest tests/test_question_banks_migration_0026.py -v
```

Expected: FAIL with `AttributeError: 'StageQuestion' object has no attribute 'question_kind'` (Task 2 will fix), OR fail at fixture composition (acceptable — fix that too in step 3 below by inlining the fixtures).

- [ ] **Step 3: Write the migration**

Create `backend/nexus/migrations/versions/0026_question_kind_column.py`:

```python
"""Phase 4 — add stage_questions.question_kind.

Adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT
'technical_depth') with a CHECK constraint allowing all 4 engine-side
Literal values. Metadata-only column add (PG11+); no table rewrite.

The bank-generator LLM (Phase 4) emits this field per question as one of
3 values: `technical_depth | behavioral_star | compliance_binary`. The
4th value (`open_culture`) is allowed by the CHECK as a forward-compat
slot for an eventual `OpenCultureTask`; the generator does not emit it
in Phase 4. See `app/modules/interview_engine/tasks/factory.py` and the
spec at
`docs/superpowers/specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md`.

POST-MIGRATION STATE:
  Every existing row reads `'technical_depth'`. Existing banks remain in
  their current `confirmed`/`reviewing` status. To get real per-question
  kinds, recruiters regenerate via
  `POST /api/jobs/{id}/banks/{bank_id}/regenerate` (existing endpoint) —
  the new bank-gen prompt picks the right kind per question. NO automatic
  backfill is performed, by design (see Phase-4 design spec
  §"Backfill"). Engine routes default-kind questions through
  `TechnicalDepthTask` — the same behavior as `main` today, so no
  regression.

Revision ID: 0026_question_kind_column
Revises: 0025_drop_engine_dispatch_tables
Create Date: 2026-05-03
"""

from alembic import op


revision = "0026_question_kind_column"
down_revision = "0025_drop_engine_dispatch_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE stage_questions "
        "ADD COLUMN question_kind TEXT NOT NULL DEFAULT 'technical_depth'"
    )
    op.execute(
        "ALTER TABLE stage_questions "
        "ADD CONSTRAINT stage_questions_question_kind_check "
        "CHECK (question_kind IN "
        "('technical_depth', 'behavioral_star', 'compliance_binary', 'open_culture'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE stage_questions "
        "DROP CONSTRAINT IF EXISTS stage_questions_question_kind_check"
    )
    op.execute(
        "ALTER TABLE stage_questions DROP COLUMN IF EXISTS question_kind"
    )
```

- [ ] **Step 4: Apply the migration to local Postgres**

```bash
docker compose run --rm nexus alembic upgrade head
```

Expected output: `Running upgrade 0025_drop_engine_dispatch_tables -> 0026_question_kind_column, Phase 4 — add stage_questions.question_kind.`

If the head was already at 0025 before the migration file existed, this is just `INFO  [alembic.runtime.migration] Running upgrade ... -> 0026_question_kind_column`.

- [ ] **Step 5: Verify the migration is reversible**

```bash
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```

Expected: both commands succeed. The downgrade drops the constraint then the column; the upgrade re-applies. No errors.

- [ ] **Step 6: Run the migration test to confirm it still fails the right way**

```bash
docker compose exec nexus pytest tests/test_question_banks_migration_0026.py -v
```

Expected: still FAILS. The migration alters production-DB schema, but the test DB is built via `Base.metadata.create_all` on the ORM (see `tests/conftest.py`), which doesn't see the migration. Task 2 fixes this by adding the column + CheckConstraint to the ORM model so create_all picks them up.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/migrations/versions/0026_question_kind_column.py \
        backend/nexus/tests/test_question_banks_migration_0026.py
git commit -m "$(cat <<'EOF'
feat(question_bank): migration 0026 — stage_questions.question_kind

ADD COLUMN question_kind TEXT NOT NULL DEFAULT 'technical_depth' with a
CHECK constraint allowing all 4 engine-side Literal values. Metadata-only
column add (PG11+); no table rewrite. Existing rows get the default;
recruiters regenerate to upgrade old banks (no automatic backfill).

Test file added but currently fails — Task 2 mirrors the column +
constraint onto the ORM so create_all-built test DBs see the same shape.
EOF
)"
```

---

## Task 2: ORM column + CheckConstraint on `StageQuestion`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/models.py`
- Test: `backend/nexus/tests/test_question_banks_migration_0026.py` (already exists from T1; will pass after this task)

**Why second:** Once the ORM has the column + constraint, `Base.metadata.create_all` (used by tests) creates the column with the same shape Alembic does in production. Task 1's migration test now passes; subsequent tasks read/write `q.question_kind` without `AttributeError`.

- [ ] **Step 1: Add CheckConstraint import + ORM field**

Open `backend/nexus/app/modules/question_bank/models.py`. Add `CheckConstraint` to the SQLAlchemy import:

```python
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, text
```

Add `__table_args__` to the `StageQuestion` class right after `__tablename__`:

```python
class StageQuestion(Base):
    """Phase 2C.2 — single question within a stage question bank.

    Note: this class has a ``text`` column which would shadow the
    module-level ``text()`` SQL function within the class body, so
    server_default expressions here use the ``sql_text`` alias."""

    __tablename__ = "stage_questions"
    __table_args__ = (
        CheckConstraint(
            "question_kind IN ('technical_depth', 'behavioral_star', "
            "'compliance_binary', 'open_culture')",
            name="stage_questions_question_kind_check",
        ),
    )
```

Add the `question_kind` column. Insert it AFTER the existing `evaluation_hint` column and BEFORE `edited_by_recruiter`:

```python
    evaluation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    question_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'technical_depth'")
    )
    edited_by_recruiter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
```

- [ ] **Step 2: Run the migration test**

```bash
docker compose exec nexus pytest tests/test_question_banks_migration_0026.py -v
```

Expected: All four tests PASS:
- `test_question_kind_default_is_technical_depth` — default fires
- `test_question_kind_accepts_each_allowlist_value[technical_depth]` and three other parametrizations — all 4 values round-trip
- `test_question_kind_check_rejects_invalid_value` — CHECK rejects bad value

If any test still errors at fixture composition, look at the existing `tests/test_question_banks_service.py` for how its `_make_bank` / `_make_question` fixtures are wired and copy that wiring locally into the test file. Do NOT introduce new top-level conftest fixtures.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/question_bank/models.py
git commit -m "feat(question_bank): add StageQuestion.question_kind ORM column + CheckConstraint"
```

---

## Task 3: `GeneratedQuestion.question_kind` strict Literal + fixture updates

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/schemas.py`
- Modify: `backend/nexus/tests/test_question_banks_schemas.py`
- Modify: `backend/nexus/tests/test_question_banks_actors.py`
- Modify: `backend/nexus/tests/test_question_banks_service.py`
- Modify: `backend/nexus/tests/test_question_banks_events.py`
- Modify: `backend/nexus/tests/test_question_banks_integration.py`

**Why third:** `write_generated_questions` and `replace_question_in_place` (T4, T5) read `incoming.question_kind`. That attribute must exist on the schema first. Adding the field as required in this task forces every fixture to declare a value — atomic update prevents a half-broken intermediate commit.

**Atomic by design:** schema flip + fixture updates ship in ONE commit. Splitting would leave the test suite red between commits.

- [ ] **Step 1: Write the failing schema validation tests**

In `backend/nexus/tests/test_question_banks_schemas.py`, add these tests after the existing `test_generated_question_rejects_too_few_positive_evidence`:

```python
def test_generated_question_requires_question_kind():
    """The strict Literal field must be present — instructor relies on this
    to reject any LLM output that omits the kind."""
    base = _valid_generated_question()
    base.pop("question_kind", None)  # ensure it's not present
    with pytest.raises(ValidationError):
        GeneratedQuestion(**base)


@pytest.mark.parametrize(
    "kind", ["technical_depth", "behavioral_star", "compliance_binary"]
)
def test_generated_question_accepts_each_generator_kind(kind):
    """All 3 generator-allowed kinds parse cleanly."""
    q = GeneratedQuestion(**_valid_generated_question(question_kind=kind))
    assert q.question_kind == kind


def test_generated_question_rejects_open_culture():
    """`open_culture` is reserved for the engine-side Literal only — the
    generator must not emit it. instructor enforces this on every LLM call."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="open_culture"))


def test_generated_question_rejects_unknown_kind():
    """Any out-of-Literal value is rejected."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="not_a_kind"))
```

- [ ] **Step 2: Run the schema tests to verify they fail**

```bash
docker compose exec nexus pytest tests/test_question_banks_schemas.py -v
```

Expected: the four new tests FAIL with `ValidationError` not raised (because the field doesn't exist yet — the validator is lenient on unknown kwargs because `_valid_generated_question` returns a dict that goes through `**`). Some existing tests may also fail because they construct `GeneratedQuestion` without `question_kind` once the field becomes required.

- [ ] **Step 3: Add the strict Literal field to `GeneratedQuestion`**

Open `backend/nexus/app/modules/question_bank/schemas.py`. Add the field at the END of `GeneratedQuestion`, after `evaluation_hint`:

```python
class GeneratedQuestion(BaseModel):
    """One question as returned by the LLM inside a StageQuestionBankOutput."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(..., ge=0)
    text: str = Field(..., min_length=10, max_length=500)
    signal_values: list[str] = Field(
        ..., min_length=1, max_length=3,
        description=(
            "Signal values from the pinned snapshot that this question probes. "
            "Must exactly match values in the snapshot's signals array."
        ),
    )
    estimated_minutes: float = Field(..., gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[str] = Field(..., min_length=0, max_length=3)
    positive_evidence: list[str] = Field(..., min_length=3, max_length=5)
    red_flags: list[str] = Field(..., min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(..., min_length=10, max_length=200)
    question_kind: Literal[
        "technical_depth",
        "behavioral_star",
        "compliance_binary",
    ] = Field(
        ...,
        description=(
            "Which task subclass the engine routes this question to. See "
            "the common prompt §6 for selection rules. The 4th engine-side "
            "value `open_culture` is intentionally NOT in this Literal — "
            "it is a forward-compat slot the generator never emits."
        ),
    )
```

- [ ] **Step 4: Update the central `_valid_generated_question` fixture**

In `backend/nexus/tests/test_question_banks_schemas.py`, find the `_valid_generated_question` function. Add `question_kind="technical_depth"` to its `base` dict:

```python
def _valid_generated_question(**overrides) -> dict:
    base = dict(
        position=0,
        text="Walk me through a production incident you handled.",
        signal_values=["Incident response"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[
            "names specific tools",
            "describes hypothesis-verify loop",
            "mentions postmortem",
        ],
        red_flags=["vague answer", "no specifics"],
        rubric=dict(
            excellent="x" * 25, meets_bar="y" * 25, below_bar="z" * 25,
        ),
        evaluation_hint="check for concrete tools and timeline",
        question_kind="technical_depth",
    )
    base.update(overrides)
    return base
```

(If the actual base dict in the codebase looks different from the snippet above — different field values etc. — keep the existing values and ONLY add the `question_kind="technical_depth"` line. Do not refactor unrelated fields.)

- [ ] **Step 5: Update fixture builders in `tests/test_question_banks_actors.py`**

This file has two `GeneratedQuestion` construction sites:

1. **`_build_question(...) -> GeneratedQuestion`** at line ~246. Add `question_kind: str = "technical_depth"` to its signature with a default and pass it through to the `GeneratedQuestion(...)` constructor.

2. **Inline `GeneratedQuestion(...)`** in `_mock_llm_output(...)` at line ~210 (inside a list comprehension or similar). Add `question_kind="technical_depth"` to that constructor call.

Run `grep -n "GeneratedQuestion(" tests/test_question_banks_actors.py` to enumerate every site after edit and confirm all are patched.

- [ ] **Step 6: Update `_make_generated_question` in `tests/test_question_banks_service.py`**

Find the `_make_generated_question` helper at line ~220:

```python
def _make_generated_question(
    *,
    position: int = 0,
    text: str = "Walk me through a production incident you handled.",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
) -> GeneratedQuestion:
    return GeneratedQuestion(
        position=position,
        text=text,
        signal_values=signal_values or ["Python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=["What tools did you use?"],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
    )
```

Add `question_kind: str = "technical_depth"` parameter and pass it through:

```python
def _make_generated_question(
    *,
    position: int = 0,
    text: str = "Walk me through a production incident you handled.",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    question_kind: str = "technical_depth",
) -> GeneratedQuestion:
    return GeneratedQuestion(
        position=position,
        text=text,
        signal_values=signal_values or ["Python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=["What tools did you use?"],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        question_kind=question_kind,
    )
```

- [ ] **Step 7: Update inline fixture in `tests/test_question_banks_events.py`**

Find the inline `GeneratedQuestion(...)` (in the regen-one mock path around line 540) and add `question_kind="technical_depth"` as the last kwarg.

- [ ] **Step 8: Update inline fixture in `tests/test_question_banks_integration.py`**

Find the inline `GeneratedQuestion(...)` in the list comprehension (around line 204) and add `question_kind="technical_depth"`. Example:

```python
return StageQuestionBankOutput(
    questions=[
        GeneratedQuestion(
            position=i,
            text=f"Tell me about your experience with {v} in production systems.",
            signal_values=[v],
            estimated_minutes=estimated_minutes,
            is_mandatory=is_mandatory,
            follow_ups=[],
            positive_evidence=[
                "names specific tools",
                "describes hypothesis-verify loop",
                "mentions postmortem",
            ],
            red_flags=["vague answer", "no specifics"],
            rubric=QuestionRubric(
                excellent="x" * 25, meets_bar="y" * 25, below_bar="z" * 25,
            ),
            evaluation_hint="check for concrete tools and timeline",
            question_kind="technical_depth",
        )
        for i, v in enumerate(signal_values)
    ]
)
```

(Use the existing field values from the file — only add the new kwarg.)

- [ ] **Step 9: Run all schema + actors + service + events + integration tests**

```bash
docker compose exec nexus pytest \
    tests/test_question_banks_schemas.py \
    tests/test_question_banks_actors.py \
    tests/test_question_banks_service.py \
    tests/test_question_banks_events.py \
    tests/test_question_banks_integration.py \
    -v
```

Expected: ALL pass. The 4 new schema validation tests PASS. Existing tests PASS (they use the updated fixture builders). If any test fails with `ValidationError` mentioning `question_kind`, find the unupdated fixture and add the kwarg.

- [ ] **Step 10: Run the full question-bank + interview-runtime subset to catch any other broken fixtures**

```bash
docker compose exec nexus pytest tests/test_question_banks_*.py tests/interview_runtime/ -v
```

Expected: all pass. If something in `tests/test_question_banks_router.py`, `tests/test_question_banks_authz.py`, `tests/test_question_banks_refine.py`, `tests/test_question_banks_draft.py`, or `tests/test_question_banks_stale_persisted.py` constructs `GeneratedQuestion`, fix that file the same way.

- [ ] **Step 11: Verify pre-existing failures are not made worse**

The following tests are pre-existing failures on `main` and Phase 4 does NOT fix them: `test_auth_login.py`, `test_auth_service.py`, `test_pipelines_service.py`, `test_session_schemas.py`, `test_audit.py`. Run them ONLY to confirm Phase 4 didn't add new failures to them:

```bash
docker compose exec nexus pytest \
    tests/test_auth_login.py tests/test_auth_service.py \
    tests/test_pipelines_service.py tests/test_session_schemas.py \
    tests/test_audit.py 2>&1 | tail -5
```

Expected: failure counts match the pre-Phase-4 baseline (whatever was failing on `main` before T1 keeps failing in the same way; nothing new fails).

- [ ] **Step 12: Verify module boundaries stay green**

```bash
docker compose exec nexus pytest tests/test_module_boundaries.py -v
```

Expected: PASS. Phase 3 closed this test in commit `ec3bfb8` — Phase 4 must keep it green.

- [ ] **Step 13: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py \
        backend/nexus/tests/test_question_banks_schemas.py \
        backend/nexus/tests/test_question_banks_actors.py \
        backend/nexus/tests/test_question_banks_service.py \
        backend/nexus/tests/test_question_banks_events.py \
        backend/nexus/tests/test_question_banks_integration.py
git commit -m "$(cat <<'EOF'
feat(question_bank): GeneratedQuestion.question_kind required Literal

Strict 3-value Literal (technical_depth | behavioral_star |
compliance_binary), no default — instructor rejects any LLM output
that omits the kind. The 4th engine-side value `open_culture` is
intentionally NOT in this Literal; it stays only on the engine-side
QuestionConfig as a forward-compat slot.

Updates 5 test files (schemas, actors, service, events, integration)
to thread question_kind through every GeneratedQuestion fixture site.
EOF
)"
```

---

## Task 4: `write_generated_questions` persists `question_kind`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py` (around line 488)
- Modify: `backend/nexus/tests/test_question_banks_service.py`

- [ ] **Step 1: Write the failing test**

In `backend/nexus/tests/test_question_banks_service.py`, add this test. It uses the existing local helpers (`_setup_tenant_user_unit`, `_make_job_with_signals`, `_make_pipeline_and_stage`, `_make_generated_question`, `_signal`, `ensure_bank_exists`) — verify they all exist via `grep -n "^async def\|^def " tests/test_question_banks_service.py`:

```python
@pytest.mark.asyncio
async def test_write_generated_questions_persists_question_kind(db):
    """write_generated_questions writes question_kind from each
    GeneratedQuestion to the persisted StageQuestion row. Each of the
    3 generator-allowed kinds round-trips."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(value="UK shift", knockout=True),
            _signal(value="Conflict resolution", signal_type="behavioral"),
            _signal(value="Python"),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    incoming = [
        _make_generated_question(
            position=0, signal_values=["UK shift"], is_mandatory=True,
            question_kind="compliance_binary",
        ),
        _make_generated_question(
            position=1, signal_values=["Conflict resolution"],
            question_kind="behavioral_star",
        ),
        _make_generated_question(
            position=2, signal_values=["Python"],
            question_kind="technical_depth",
        ),
    ]
    await write_generated_questions(
        db, bank=bank, questions=incoming, source="ai_generated",
    )
    persisted = await get_bank_questions(db, bank.id)
    by_signal = {p.signal_values[0]: p for p in persisted}
    assert by_signal["UK shift"].question_kind == "compliance_binary"
    assert by_signal["Conflict resolution"].question_kind == "behavioral_star"
    assert by_signal["Python"].question_kind == "technical_depth"
```

Verify imports at the top of the file include `write_generated_questions` and `get_bank_questions` from `app.modules.question_bank.service`. If missing, add them to the existing service-imports block.

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_write_generated_questions_persists_question_kind -v
```

Expected: FAIL. Either the rows have `question_kind="technical_depth"` (the server default — because the kwarg isn't passed yet) or the assertion message shows mismatched values.

- [ ] **Step 3: Update `write_generated_questions`**

Open `backend/nexus/app/modules/question_bank/service.py`. Find the `write_generated_questions` function (around line 488). In the loop that constructs `StageQuestion(...)` (around line 514-529), add `question_kind=incoming.question_kind` as the last kwarg:

```python
    for incoming in questions:
        db.add(
            StageQuestion(
                tenant_id=bank.tenant_id,
                bank_id=bank.id,
                position=offset + incoming.position,
                source=source,
                text=incoming.text,
                signal_values=list(incoming.signal_values),
                estimated_minutes=incoming.estimated_minutes,
                is_mandatory=incoming.is_mandatory,
                follow_ups=list(incoming.follow_ups),
                positive_evidence=list(incoming.positive_evidence),
                red_flags=list(incoming.red_flags),
                rubric=incoming.rubric.model_dump(),
                evaluation_hint=incoming.evaluation_hint,
                question_kind=incoming.question_kind,
            )
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_write_generated_questions_persists_question_kind -v
```

Expected: PASS.

- [ ] **Step 5: Run the full service test file to catch regressions**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py \
        backend/nexus/tests/test_question_banks_service.py
git commit -m "feat(question_bank): write_generated_questions persists question_kind"
```

---

## Task 5: `replace_question_in_place` persists `question_kind`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py` (around line 540)
- Modify: `backend/nexus/tests/test_question_banks_service.py`

- [ ] **Step 1: Write the failing test**

In `backend/nexus/tests/test_question_banks_service.py`, add this test next to the T4 test:

```python
@pytest.mark.asyncio
async def test_replace_question_in_place_updates_question_kind(db):
    """replace_question_in_place writes the new GeneratedQuestion's
    question_kind onto the existing row. Tests the regen-one path."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(value="Python"),
            _signal(value="UK shift", knockout=True),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Seed with a technical_depth question
    await write_generated_questions(
        db, bank=bank,
        questions=[
            _make_generated_question(
                position=0, signal_values=["Python"],
                question_kind="technical_depth",
            ),
        ],
        source="ai_generated",
    )
    seeded = (await get_bank_questions(db, bank.id))[0]
    assert seeded.question_kind == "technical_depth"

    # Regen with a compliance_binary
    new_data = _make_generated_question(
        position=0,
        text="Can you work the UK shift (1pm-9pm UK time)?",
        signal_values=["UK shift"],
        is_mandatory=True,
        question_kind="compliance_binary",
    )
    await replace_question_in_place(db, question=seeded, new_data=new_data)
    await db.refresh(seeded)
    assert seeded.question_kind == "compliance_binary"
    assert seeded.source == "ai_regenerated"
```

Verify `replace_question_in_place` is imported at the top of the file from `app.modules.question_bank.service`. Add to imports if missing.

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_replace_question_in_place_updates_question_kind -v
```

Expected: FAIL. After regen, the row still reads `'technical_depth'` (the kwarg isn't applied).

- [ ] **Step 3: Update `replace_question_in_place`**

Open `backend/nexus/app/modules/question_bank/service.py`. Find `replace_question_in_place` (around line 540). Add `question.question_kind = new_data.question_kind` next to the other field assignments, before `await db.flush()`:

```python
async def replace_question_in_place(
    db: AsyncSession,
    *,
    question: StageQuestion,
    new_data: GeneratedQuestion,
) -> None:
    """Update an existing question row with new LLM-generated data. Preserves id."""
    question.text = new_data.text
    question.signal_values = list(new_data.signal_values)
    question.estimated_minutes = new_data.estimated_minutes
    question.is_mandatory = new_data.is_mandatory
    question.follow_ups = list(new_data.follow_ups)
    question.positive_evidence = list(new_data.positive_evidence)
    question.red_flags = list(new_data.red_flags)
    question.rubric = new_data.rubric.model_dump()
    question.evaluation_hint = new_data.evaluation_hint
    question.question_kind = new_data.question_kind
    question.source = "ai_regenerated"
    question.edited_by_recruiter = False
    question.updated_at = _now_utc()
    await db.flush()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_replace_question_in_place_updates_question_kind -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py \
        backend/nexus/tests/test_question_banks_service.py
git commit -m "feat(question_bank): replace_question_in_place persists question_kind"
```

---

## Task 6: `create_recruiter_question` explicit default kind

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py` (around line 602)
- Modify: `backend/nexus/tests/test_question_banks_service.py`

**Why:** Per spec §4.2 implementation note, explicitly passing `question_kind="technical_depth"` keeps Python-level row state coherent with the DB row state without an extra SELECT after INSERT. The recruiter API surface stays clean (no field on `CreateQuestionBody`), so the call-site default is the canonical recruiter-authored kind.

- [ ] **Step 1: Write the failing test**

In `backend/nexus/tests/test_question_banks_service.py`, add this test. Uses the existing `_add_recruiter_question` helper which wraps `create_recruiter_question`:

```python
@pytest.mark.asyncio
async def test_create_recruiter_question_lands_with_default_kind(db):
    """Recruiter-authored questions take 'technical_depth' as their kind.
    CreateQuestionBody has no question_kind field — the service writes
    the default explicitly so the in-memory row state matches the DB
    without needing a session refresh."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    question = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="What testing tools have you used in production?",
        signal_values=["Python"],
    )
    # The Python-level attribute reads back as 'technical_depth' WITHOUT
    # needing a session refresh — the explicit kwarg in service.py
    # establishes that.
    assert question.question_kind == "technical_depth"
```

`_add_recruiter_question` is already defined in this file (line ~268) and wraps `create_recruiter_question`. No new imports needed.

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_create_recruiter_question_lands_with_default_kind -v
```

Expected: depending on SQLAlchemy 2.0 server-default state ordering, the assertion may fail either with `None` or with the post-flush server default. Either way the test fails until the explicit kwarg lands.

- [ ] **Step 3: Add the explicit kwarg to `create_recruiter_question`**

Open `backend/nexus/app/modules/question_bank/service.py`. Find `create_recruiter_question` (around line 566). In the `StageQuestion(...)` constructor (around line 602), add `question_kind="technical_depth"` next to the other explicit fields:

```python
    question = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=position,
        source="recruiter",
        text=body.text,
        signal_values=list(body.signal_values),
        estimated_minutes=body.estimated_minutes,
        is_mandatory=body.is_mandatory,
        follow_ups=list(body.follow_ups),
        positive_evidence=list(body.positive_evidence),
        red_flags=list(body.red_flags),
        rubric=body.rubric.model_dump(),
        evaluation_hint=body.evaluation_hint,
        question_kind="technical_depth",  # CreateQuestionBody intentionally has no kind field
        edited_by_recruiter=False,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose exec nexus pytest tests/test_question_banks_service.py::test_create_recruiter_question_lands_with_default_kind -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py \
        backend/nexus/tests/test_question_banks_service.py
git commit -m "feat(question_bank): create_recruiter_question writes default kind explicitly"
```

---

## Task 7: Bank-generator prompt edits (atomic)

**Files:**
- Modify: `backend/nexus/prompts/v1/question_bank_common.txt`
- Modify: `backend/nexus/prompts/v1/question_bank_phone_screen.txt`
- Modify: `backend/nexus/prompts/v1/question_bank_ai_screening.txt`
- Modify: `backend/nexus/prompts/v1/question_bank_regenerate_one.txt`

**Why atomic:** All four prompts must ship together so the LLM never sees a state where the schema requires `question_kind` but no prompt teaches it how to pick one. No unit test guards this — the prompt_quality test in T9 is the test for this task.

**Senior-reviewer fairness sign-off required** per Decision #18 + CLAUDE.md "Human Review Required For: candidate scoring and classification thresholds". The PR description carries the §5.5 checklist from the spec.

- [ ] **Step 1: Append §6 to `question_bank_common.txt`**

Open `backend/nexus/prompts/v1/question_bank_common.txt`. Append at the end of the file (after the existing `# CRITICAL: signal_values rule` block):

```

# 6. Question kind — choose the engine task subclass

Each question MUST declare a `question_kind` field that tells the live screening AI which task subclass to dispatch. Three values, exactly one per question:

  - `compliance_binary` — yes/no attestation about a candidate-self-disclosed eligibility fact. The answer is binary; a "no" is a knockout against the signal. ≤60s to ask and answer. Examples: "Can you work the UK shift (1pm-9pm UK time)?", "Are you legally authorized to work in the United States without sponsorship?", "Are you willing to relocate to Bangalore for this role?", "Do you currently hold an active AWS Solutions Architect Professional certification?". Strong fit when: the underlying signal has `knockout=true` AND the substance is a fact about the candidate's situation, eligibility, or credentials (NOT a skill they must demonstrate at depth).

  - `behavioral_star` — past-experience narrative that fits Situation / Task / Action / Result shape. The candidate is describing one specific event from their work history. Examples: "Tell me about a time you had to push back on a technical decision from a senior engineer.", "Walk me through a situation where a production incident required you to coordinate across three teams under pressure." Strong fit when: the underlying signal has `type=behavioral` AND `evaluation_method=behavioral_question`.

  - `technical_depth` — DEFAULT. Open-ended technical, scenario, or design question that probes HOW the candidate thinks. Examples: "How would you design a rate limiter for an API serving 100k req/sec?", "Walk me through debugging a 5xx storm in a payment service." Use this when the question doesn't fit the two above. The vast majority of questions are this kind.

Rules:
  - Mutually exclusive — pick exactly one.
  - The kind drives the engine's per-question budget and probe cap. Misclassification costs the candidate real interview time.
  - Per-stage prompts may BAN certain kinds at this stage. Honor the ban.
  - NEVER use the kind to encode anything that could correlate with a protected class. The kind is a structural routing decision based on question shape, not on the candidate or the signal's social meaning.
```

- [ ] **Step 2: Append "Question kind selection (this stage)" to `question_bank_phone_screen.txt`**

Open `backend/nexus/prompts/v1/question_bank_phone_screen.txt`. Append at the end of the file:

```

### Question kind selection (this stage)

Phone screen is the natural home for `compliance_binary`: short, binary, knockout-gating attestations. Expect 0-2 `compliance_binary` questions per bank — exactly one per binary knockout signal in scope (UK shift, work auth, willingness to relocate, hard credential check). All other questions should be `technical_depth` shallow verifications. `behavioral_star` is not expected at this stage — the phone screen's depth target is shallow verification, not narrative. If a behavioral signal is in scope and you must probe it, emit `technical_depth` framed as a closed verification ("Have you ever had to give negative feedback to a peer in writing?") rather than a STAR-shaped narrative.
```

- [ ] **Step 3: Append "Question kind selection (this stage)" to `question_bank_ai_screening.txt`**

Open `backend/nexus/prompts/v1/question_bank_ai_screening.txt`. Append at the end of the file:

```

### Question kind selection (this stage)

AI screening's per-question budget assumes 3-5 minute deep-dive cognition. `compliance_binary` is BANNED at this stage — a 60-second yes/no in a 30-minute deep-dive robs budget from depth probes this stage exists to deliver. Binary attestations belong in the phone screen. If you find yourself reaching for one here, either re-frame the underlying intent as a `technical_depth` scenario, or recognize the signal should have been verified at the phone screen and skip it.

`behavioral_star` is also not expected — this stage skips behavioral signals entirely (see the allocation rule above). Every question this stage emits should be `technical_depth`.
```

- [ ] **Step 4: Augment `question_bank_regenerate_one.txt`**

Open `backend/nexus/prompts/v1/question_bank_regenerate_one.txt`. Find the bullet list under `## Your task`. Insert this bullet at the END of the list (just before the `## Output` section):

```
- **Preserve the original question's `question_kind` unless `replace_signal_values` is provided AND the new signal class materially changes the question shape** (e.g., swapping a `compliance_binary` knockout signal for a `competency` depth signal). If unsure, keep the same kind. The replacement question's `question_kind` field is REQUIRED — see the common prompt §6 for selection rules.
```

- [ ] **Step 5: Sanity-check the prompts load cleanly**

The PromptLoader caches prompts in memory. A simple way to verify the appended text is well-formed and loadable:

```bash
docker compose exec nexus python -c "
from app.ai.prompts import prompt_loader
for name in [
    'question_bank_common',
    'question_bank_phone_screen',
    'question_bank_ai_screening',
    'question_bank_regenerate_one',
]:
    body = prompt_loader.get(name)
    assert 'question_kind' in body, f'{name} missing question_kind'
    print(f'{name}: {len(body)} chars OK')
"
```

Expected output: 4 lines, each printing the prompt name + char count and confirming `question_kind` appears in the body. Numbers will look roughly like:

```
question_bank_common: ~9000 chars OK
question_bank_phone_screen: ~3000 chars OK
question_bank_ai_screening: ~3500 chars OK
question_bank_regenerate_one: ~1700 chars OK
```

- [ ] **Step 6: Run the full question-bank test subset to catch any regressions**

```bash
docker compose exec nexus pytest tests/test_question_banks_*.py -v -m "not prompt_quality"
```

Expected: all pass (excluding prompt_quality tier — that's T9).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/prompts/v1/question_bank_common.txt \
        backend/nexus/prompts/v1/question_bank_phone_screen.txt \
        backend/nexus/prompts/v1/question_bank_ai_screening.txt \
        backend/nexus/prompts/v1/question_bank_regenerate_one.txt
git commit -m "$(cat <<'EOF'
feat(prompts): question_bank — kind-selection guidance for the LLM

- common.txt §6: defines technical_depth | behavioral_star |
  compliance_binary, with examples + structural-not-social fairness guard.
- phone_screen: compliance_binary preferred for binary knockouts.
- ai_screening: HARD BAN on compliance_binary (would rob deep-dive
  budget for a 60s yes/no).
- regenerate_one: preserve prior kind unless replacement signals
  materially change question shape.

SENIOR-REVIEWER FAIRNESS SIGN-OFF REQUIRED — see Phase 4 spec §5.5
checklist. Reviewer: <name> on <date>.
EOF
)"
```

The reviewer name + date in the commit message gets filled by the human merging the PR (or the human reviewing the agent's commit before merge).

---

## Task 8: `build_session_config` reads `question_kind` into `QuestionConfig`

**Files:**
- Modify: `backend/nexus/app/modules/interview_runtime/service.py` (around line 189)
- Test: `backend/nexus/tests/interview_runtime/test_service.py` (NEW or extend)

**Why:** This is the single line that makes Phase 3's already-shipped factory routing fire on real questions. Without it, every `QuestionConfig` at session start uses the field's default (`"technical_depth"`), and the engine routes everything to `TechnicalDepthTask` regardless of what the bank-generator emitted.

- [ ] **Step 1: Find or create the test file**

```bash
ls -la backend/nexus/tests/interview_runtime/
```

There is no existing test of `build_session_config` anywhere — this task creates one. The directory exists with `test_schemas.py`. Add `test_service.py` next to it.

- [ ] **Step 2: Write the failing test**

Create `backend/nexus/tests/interview_runtime/test_service.py`. Inlining the full fixture composition (no shared `full_session_fixture` exists yet, and adding one to top-level conftest is wider scope than Phase 4 needs):

```python
"""Tests for interview_runtime.service — Phase 4: build_session_config
reads StageQuestion.question_kind into QuestionConfig.question_kind.

This is the first test of build_session_config in the codebase, so the
fixture composition is inlined for self-containment. Future tests of
the same function should factor out a shared helper if more than two
land here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.interview_runtime import build_session_config
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.session.models import Session
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "company_stage": "Series C",
    "hiring_bar": "standard",
}


@pytest.mark.asyncio
async def test_build_session_config_reads_question_kind(db):
    """build_session_config plumbs each StageQuestion's question_kind into
    the corresponding QuestionConfig.question_kind. Tests with a mix of
    default-kind and non-default-kind rows so the read path is exercised
    for every Literal value the engine cares about."""
    # ---- tenant + user + company org_unit (with company_profile) ----
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(
        sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'")
    )

    # ---- job + confirmed snapshot ----
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="UK Customer Support Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "UK shift", "type": "experience", "priority": "required",
                "weight": 3, "knockout": True, "stage": "screen",
                "evaluation_method": "verbal_response", "evaluation_hint": None,
                "source": "ai_extracted", "inference_basis": None,
            },
            {
                "value": "Conflict resolution", "type": "behavioral",
                "priority": "preferred", "weight": 2, "knockout": False,
                "stage": "interview", "evaluation_method": "behavioral_question",
                "evaluation_hint": None, "source": "ai_extracted",
                "inference_basis": None,
            },
            {
                "value": "Python", "type": "competency", "priority": "preferred",
                "weight": 2, "knockout": False, "stage": "screen",
                "evaluation_method": "verbal_response", "evaluation_hint": None,
                "source": "ai_extracted", "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="Customer support engineer role for UK enterprise.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    # ---- pipeline + stage + bank + 3 questions with mixed kinds ----
    instance = JobPipelineInstance(
        tenant_id=tenant.id, job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    rubric = {
        "excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30,
    }
    base_q = dict(
        tenant_id=tenant.id,
        bank_id=bank.id,
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    q0 = StageQuestion(
        position=0, text="Can you work UK shift (1pm-9pm)?",
        signal_values=["UK shift"], estimated_minutes=1.5, is_mandatory=True,
        question_kind="compliance_binary",
        **base_q,
    )
    q1 = StageQuestion(
        position=1, text="Tell me about a time you handled a tough peer conflict.",
        signal_values=["Conflict resolution"], estimated_minutes=4.0, is_mandatory=False,
        question_kind="behavioral_star",
        **base_q,
    )
    q2 = StageQuestion(
        position=2, text="Walk me through your last Python production debug.",
        signal_values=["Python"], estimated_minutes=4.0, is_mandatory=False,
        question_kind="technical_depth",
        **base_q,
    )
    db.add_all([q0, q1, q2])
    await db.flush()

    # ---- candidate + assignment + session ----
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Charlie",
        email=f"charlie-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    # ---- exercise the read path ----
    config = await build_session_config(
        db, session_id=session.id, tenant_id=tenant.id,
    )

    kinds_by_position = {q.position: q.question_kind for q in config.stage.questions}
    assert kinds_by_position[0] == "compliance_binary"
    assert kinds_by_position[1] == "behavioral_star"
    assert kinds_by_position[2] == "technical_depth"
```

**Note on signature:** verify `build_session_config(db, session_id=..., tenant_id=...)` matches the actual function signature in `app/modules/interview_runtime/service.py`. If the call is positional or differently-named, adjust the test accordingly. Run `grep -n "def build_session_config" app/modules/interview_runtime/service.py` to confirm.

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose exec nexus pytest tests/interview_runtime/test_service.py::test_build_session_config_reads_question_kind -v
```

Expected: FAIL with assertion `["technical_depth", "technical_depth", "technical_depth"] == ["compliance_binary", "behavioral_star", "technical_depth"]` — because `build_session_config` doesn't read the column yet.

- [ ] **Step 4: Update `build_session_config`**

Open `backend/nexus/app/modules/interview_runtime/service.py`. Find the `QuestionConfig(...)` construction (around line 189). Add `question_kind=q.question_kind` as the last kwarg:

```python
            questions=[
                QuestionConfig(
                    id=str(q.id),
                    position=q.position,
                    text=q.text,
                    signal_values=list(q.signal_values),
                    estimated_minutes=float(q.estimated_minutes),
                    is_mandatory=q.is_mandatory,
                    follow_ups=list(q.follow_ups),
                    positive_evidence=list(q.positive_evidence),
                    red_flags=list(q.red_flags),
                    rubric=QuestionRubric.model_validate(q.rubric),
                    evaluation_hint=q.evaluation_hint,
                    question_kind=q.question_kind,
                )
                for q in questions
            ],
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker compose exec nexus pytest tests/interview_runtime/test_service.py::test_build_session_config_reads_question_kind -v
```

Expected: PASS.

- [ ] **Step 6: Run the full interview_runtime test subset**

```bash
docker compose exec nexus pytest tests/interview_runtime/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/interview_runtime/service.py \
        backend/nexus/tests/interview_runtime/test_service.py
git commit -m "feat(interview_runtime): build_session_config reads question_kind"
```

---

## Task 9: Prompt-quality test (real LLM, opt-in tier)

**Files:**
- Create: `backend/nexus/tests/test_question_banks_prompt_quality.py`

**Why:** The prompt edits in T7 have no unit-test gate — the only meaningful validation is "does the LLM actually pick the right kinds when given realistic input." This task asserts:
- Phone screen with a UK-shift knockout signal → emits at least one `compliance_binary`.
- AI screening across N=3 runs → emits ZERO `compliance_binary` (the hard-ban assertion).
- Regen-one preserves the kind when `replace_signal_values` is None.

Marks the file with `pytest.mark.prompt_quality` so it's opt-in (matching the existing convention; engine tests use the same marker).

- [ ] **Step 1: Verify the prompt_quality marker is registered**

```bash
grep -n "prompt_quality" /home/ishant/Projects/ProjectX/backend/nexus/pyproject.toml /home/ishant/Projects/ProjectX/backend/nexus/conftest.py /home/ishant/Projects/ProjectX/backend/nexus/tests/conftest.py 2>&1 | head
```

Expected: at least one mention. If not registered (rare), add to `pyproject.toml` under `[tool.pytest.ini_options].markers`:

```toml
markers = [
    "prompt_quality: real-LLM tests; opt-in via -m prompt_quality",
]
```

- [ ] **Step 2: Write the prompt_quality test file**

Create `backend/nexus/tests/test_question_banks_prompt_quality.py`:

```python
"""Phase 4 — prompt-quality coverage of question_kind selection.

Real-LLM tests. Opt-in tier: run via
    docker compose exec nexus pytest tests/test_question_banks_prompt_quality.py -m prompt_quality

These tests EXERCISE the live OpenAI client and the actual bank-generator
prompts. They are slow and consume tokens. Do NOT include in the default
test gate.

Three assertions:
  1. Phone screen with a UK-shift knockout signal emits at least one
     `compliance_binary` question (and at most one per binary knockout).
  2. AI screening across N=3 independent runs emits ZERO `compliance_binary`
     questions — the hard-ban assertion.
  3. Regen-one preserves the kind when `replace_signal_values` is None.
"""

from __future__ import annotations

import asyncio
import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    SingleQuestionOutput,
    StageQuestionBankOutput,
)


pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


def _phone_screen_user_message_with_uk_shift_knockout() -> str:
    """A minimal-but-realistic phone-screen user message featuring a
    UK-shift knockout signal. Mirrors the shape of
    actors.py::_build_user_message but inlined to keep the test
    self-contained."""
    return """# JOB CONTEXT

Job title: Customer Support Engineer (UK hours)
Role summary: Frontline support for our UK-based enterprise customers.
Seniority: mid

# COMPANY PROFILE

about: B2B SaaS serving Fortune 500 retail clients in the UK and EU.
industry: Technology
company_stage: Series C
hiring_bar: standard

# SIGNALS TO ASSESS (pinned snapshot)

- value: 'Available for UK shift (1pm-9pm UK time)'
  type: experience
  priority: required
  weight: 3
  knockout: true
  stage_tag: screen
- value: 'Python'
  type: competency
  priority: preferred
  weight: 2
  knockout: false
  stage_tag: screen
- value: 'Customer support experience'
  type: experience
  priority: required
  weight: 3
  knockout: false
  stage_tag: screen

# PIPELINE CONTEXT

This pipeline has 1 stages. You are generating questions for STAGE 1.

## Stage 1 — Phone Screen (CURRENT — you are generating this)
  Type: phone_screen, Duration: 15 min, Difficulty: medium

# THIS STAGE'S METADATA

Name: Phone Screen
Type: phone_screen
Duration: 15 min
Difficulty: medium
Signal type filter (include_types): ['competency', 'experience', 'credential']
Advance behavior: manual_review

# BUDGET FOR THIS STAGE (HARD CAPS — server-enforced)

Stage duration: 15 min
Mandatory budget cap: 15 min (sum of estimated_minutes across is_mandatory=true questions)
Total budget cap: 20 min (sum across ALL questions, mandatory + optional combined)
Optional buffer: 5 min (reserved for the screening AI's runtime fallback probes)

Eligible signals (after include_types filter):
  - knockouts: 1 (each gets ONE mandatory question)
  - weight=3 non-knockout: 1 (mandatory only if mandatory budget allows; otherwise optional)
  - weight=2: 1 (optional depth probes)
  - weight=1: 0 (skip unless every higher-weight signal is covered AND buffer remains)

Optimize for SIGNAL DENSITY, not question count. Under-using budget by 1-2 minutes is acceptable; padding shallow questions is rejected.

Now generate the structured question bank output as specified in the system instructions.
"""


def _ai_screening_user_message() -> str:
    """A realistic ai_screening user message featuring competency + experience
    signals with no binary-knockout fits — ai_screening should emit only
    technical_depth."""
    return """# JOB CONTEXT

Job title: Senior Backend Engineer
Role summary: Distributed systems on AWS for a fintech platform.
Seniority: senior

# COMPANY PROFILE

about: Fintech platform processing real-time payments at scale.
industry: Financial services
company_stage: Series D
hiring_bar: high

# SIGNALS TO ASSESS (pinned snapshot)

- value: 'Distributed systems design'
  type: competency
  priority: required
  weight: 3
  knockout: true
  stage_tag: interview
- value: 'AWS production experience'
  type: experience
  priority: required
  weight: 3
  knockout: false
  stage_tag: interview
- value: 'Postgres at scale'
  type: competency
  priority: required
  weight: 2
  knockout: false
  stage_tag: interview

# PIPELINE CONTEXT

This pipeline has 1 stages. You are generating questions for STAGE 1.

## Stage 1 — AI Deep Interview (CURRENT — you are generating this)
  Type: ai_screening, Duration: 30 min, Difficulty: hard

# THIS STAGE'S METADATA

Name: AI Deep Interview
Type: ai_screening
Duration: 30 min
Difficulty: hard
Signal type filter (include_types): ['competency', 'experience']
Advance behavior: manual_review

# BUDGET FOR THIS STAGE (HARD CAPS — server-enforced)

Stage duration: 30 min
Mandatory budget cap: 30 min (sum of estimated_minutes across is_mandatory=true questions)
Total budget cap: 35 min (sum across ALL questions, mandatory + optional combined)
Optional buffer: 5 min (reserved for the screening AI's runtime fallback probes)

Eligible signals (after include_types filter):
  - knockouts: 1
  - weight=3 non-knockout: 1
  - weight=2: 1
  - weight=1: 0

Optimize for SIGNAL DENSITY, not question count.

Now generate the structured question bank output as specified in the system instructions.
"""


async def _call_bank_gen(stage_type: str, user_message: str) -> StageQuestionBankOutput:
    """Hit the live LLM for a stage bank, mirroring actors.py composition."""
    system_prompt = prompt_loader.load_pair(
        "question_bank_common", f"question_bank_{stage_type}"
    )
    client = get_openai_client()
    return await client.chat.completions.create(
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        response_model=StageQuestionBankOutput,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_retries=1,
        name=f"question_bank_prompt_quality_{stage_type}",
    )


async def test_phone_screen_emits_compliance_binary_for_uk_shift_knockout():
    """Phone screen with a UK-shift knockout signal must emit at least one
    compliance_binary question."""
    output = await _call_bank_gen("phone_screen", _phone_screen_user_message_with_uk_shift_knockout())
    kinds = [q.question_kind for q in output.questions]
    assert "compliance_binary" in kinds, (
        f"phone_screen with UK-shift knockout produced no compliance_binary "
        f"question; kinds={kinds}"
    )
    assert kinds.count("compliance_binary") <= 1, (
        f"phone_screen with one binary knockout should emit at most one "
        f"compliance_binary question; kinds={kinds}"
    )


async def test_ai_screening_never_emits_compliance_binary():
    """Across N=3 independent runs, ai_screening must emit ZERO
    compliance_binary questions — the hard-ban assertion."""
    runs = await asyncio.gather(*[
        _call_bank_gen("ai_screening", _ai_screening_user_message())
        for _ in range(3)
    ])
    all_kinds: list[str] = []
    for output in runs:
        all_kinds.extend(q.question_kind for q in output.questions)
    assert "compliance_binary" not in all_kinds, (
        f"ai_screening violated the BAN: emitted compliance_binary in N=3 "
        f"runs; kinds across all runs={all_kinds}"
    )


async def test_regenerate_one_preserves_kind_when_signals_unchanged():
    """When replace_signal_values is None, regenerate-one preserves the
    original question's question_kind."""
    system_prompt = prompt_loader.load_pair(
        "question_bank_common", "question_bank_regenerate_one"
    )
    user_parts = [
        "# JOB CONTEXT\n\nJob: Customer Support Engineer (UK hours)\nSeniority: mid\n\n",
        "# SIGNALS (pinned snapshot)\n",
        "- 'Available for UK shift (1pm-9pm UK time)' (type: experience, weight: 3, knockout: True)\n",
        "\n# CURRENT QUESTION BEING REPLACED\n",
        "Text: Can you work the UK shift (1pm-9pm UK time)?\n",
        "Probes: ['Available for UK shift (1pm-9pm UK time)']\n",
        "Rubric meets_bar: Candidate confirms availability with concrete reasoning\n",
        "Estimated minutes: 1.5\n",
        "Original question_kind: compliance_binary\n",
        "\n# TARGET SIGNALS (probe these — same as current)\n",
        "- 'Available for UK shift (1pm-9pm UK time)'\n",
        "\n# OTHER QUESTIONS IN THIS STAGE'S BANK — DO NOT DUPLICATE\n",
        "(none)\n",
        "\n# STAGE METADATA\n",
        "Type: phone_screen, Duration: 15 min, Difficulty: medium\n",
        "\nNow generate ONE replacement question as a SingleQuestionOutput.\n",
    ]
    client = get_openai_client()
    result: SingleQuestionOutput = await client.chat.completions.create(
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        response_model=SingleQuestionOutput,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "".join(user_parts)},
        ],
        max_retries=1,
        name="question_bank_prompt_quality_regen",
    )
    assert result.question.question_kind == "compliance_binary", (
        f"regenerate-one failed to preserve compliance_binary kind; "
        f"got {result.question.question_kind}"
    )
```

(The user_messages are inlined for self-containment — the tests don't need the full DB-backed fixture chain to exercise the prompt. If you want to wire them through the real `_build_user_message` instead, that's also fine but adds DB fixture wiring; the inlined form is preferred for prompt_quality tests.)

- [ ] **Step 3: Run the prompt_quality tests**

```bash
docker compose exec nexus pytest tests/test_question_banks_prompt_quality.py -m prompt_quality -v
```

This consumes real LLM tokens. Expected: all 3 tests PASS. Each takes ~5-30 seconds depending on the model + reasoning_effort.

If a test fails:
- `test_phone_screen_emits_compliance_binary_for_uk_shift_knockout` failure → review the phone_screen calibration prompt; the LLM isn't being told strongly enough that UK-shift knockouts are binary.
- `test_ai_screening_never_emits_compliance_binary` failure → review the BAN wording in the ai_screening prompt; strengthen it.
- `test_regenerate_one_preserves_kind_when_signals_unchanged` failure → review the regen-one preservation rule; make it more explicit.

Re-run after prompt edits. If a flaky failure (1 of 3 runs in `ai_screening`), increase N to 5 and re-run; if still flakes, the BAN wording needs strengthening.

- [ ] **Step 4: Verify the test does NOT run in the default suite**

```bash
docker compose exec nexus pytest tests/test_question_banks_prompt_quality.py -v
```

Expected: 3 tests SKIPPED or DESELECTED (no `-m prompt_quality` flag). The marker gates the run by default.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/tests/test_question_banks_prompt_quality.py
git commit -m "test(question_bank): prompt-quality coverage of question_kind selection"
```

---

## Task 10: Update `backend/nexus/CLAUDE.md` — migration list + Phase 4 status block

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Add migration 0026 entry to the Alembic migrations list**

Open `backend/nexus/CLAUDE.md`. Find the migrations bullet list (search for `0024_engine_integration` or `0025_drop_engine_dispatch_tables` to locate the section). After the `0025_drop_engine_dispatch_tables` bullet, add:

```markdown
- `0026_question_kind_column` — **Phase 4**: adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT `'technical_depth'`, CHECK in `('technical_depth','behavioral_star','compliance_binary','open_culture')`). Bank-generator now emits the field; existing rows get the default. Recruiters regenerate to upgrade old banks (no automatic backfill).
```

- [ ] **Step 2: Update the "Current head" mention if present**

In the same file, search for "Current head: `0025_drop_engine_dispatch_tables`" or similar. Replace with:

```
Current head: `0026_question_kind_column`.
```

- [ ] **Step 3: Add the Phase 4 status block**

Find the "Current State" section. After the existing `Phase 3D.engine-redesign-3` bullet, add:

```markdown
- **Phase 3D.engine-redesign-4** — done: `stage_questions.question_kind`
  column added (migration `0026_question_kind_column`); bank-generator
  now emits the field per question (3-value Literal:
  `technical_depth | behavioral_star | compliance_binary`); regen-one
  preserves prior kind via prompt rule;
  `interview_runtime.build_session_config` reads the column into
  `QuestionConfig.question_kind`. Existing banks unchanged (default
  `'technical_depth'`); recruiters regenerate to pick up the new
  prompt's kind selection. Recruiter API surface unchanged
  (`question_kind` not in request/response schemas). See spec
  `docs/superpowers/specs/2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md`.
```

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/CLAUDE.md
git commit -m "docs(nexus): add Phase 4 status + migration 0026 entry"
```

---

## Task 11: Flip overview spec Phase status row to ✅ shipped

**Files:**
- Modify: `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md`

**This is the final task — per the working agreement, the spec status flip ships in the same commit as the last Phase 4 artifact.**

- [ ] **Step 1: Update the Phase status index row**

Open `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md`. Find the row in the Phase status index table that reads:

```
| 4 — `question_kind` schema | _pending_ | _pending_ | ⚪ not started |
```

Replace with:

```
| 4 — `question_kind` schema | [`2026-05-03-…phase-4-question-kind-schema-design.md`](2026-05-03-engine-redesign-phase-4-question-kind-schema-design.md) | [`2026-05-03-…phase-4-question-kind-schema.md`](../plans/2026-05-03-engine-redesign-phase-4-question-kind-schema.md) | ✅ shipped |
```

- [ ] **Step 2: Verify the table renders cleanly**

Skim the table around the edited row to ensure the column alignment matches the surrounding rows.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md
git commit -m "$(cat <<'EOF'
docs(engine): mark Phase 4 ✅ shipped in overview status index

Phase 4 of the engine-redesign arc shipped: question_kind plumbed
end-to-end (DB column + ORM + LLM schema + bank-gen + regen-one +
runtime read). Acceptance gates §8 of the Phase 4 spec satisfied.

Phase 5 next: knockout policy + tenant settings.
EOF
)"
```

---

## Final acceptance check

After T11 commits, run:

```bash
docker compose exec nexus pytest \
    tests/test_question_banks_*.py \
    tests/interview_runtime/ \
    tests/interview_engine/ \
    tests/test_module_boundaries.py \
    -v -m "not prompt_quality"
```

Expected: all green. The `prompt_quality` tier is opt-in (T9), runs separately when validating prompt changes.

Then verify the Phase 4 acceptance gates from the spec §8:

1. ✅ Migration 0026 applies + downgrade works (T1).
2. ✅ ORM column with `server_default` (T2).
3. ✅ Strict 3-value Literal on `GeneratedQuestion`, required (T3).
4. ✅ Bulk-gen + regen-one persist `question_kind` (T4, T5).
5. ✅ Four prompt edits with senior-reviewer fairness sign-off (T7).
6. ✅ `build_session_config` passes `question_kind` (T8).
7. ✅ All new + extended tests green; `test_module_boundaries.py` green; pre-existing failures unchanged.
8. ✅ Migration docstring + CLAUDE.md migration list + Current State block (T1, T10).
9. ✅ Overview spec status row flipped to ✅ shipped in same commit as last artifact (T11).
10. ✅ Recruiter API surface confirmed unchanged:

```bash
git grep -n "question_kind" backend/nexus/app/modules/question_bank/router.py \
    backend/nexus/app/modules/question_bank/schemas.py 2>&1
```

Expected: no matches in `router.py`. In `schemas.py`, matches ONLY in `GeneratedQuestion` (and possibly its docstring) — NOT in `CreateQuestionBody`, `UpdateQuestionBody`, or `QuestionResponse`.

---

## Self-review notes

**Spec coverage:** Each numbered acceptance gate in spec §8 maps to at least one task above. Each in-scope file in spec §2.1 is touched in the task plan. Each prompt-edit body from spec §5 is reproduced verbatim in T7.

**Placeholder scan:** No "TBD" / "implement later" / "similar to Task N". Every code block is the full code the implementer pastes. Every command is exact.

**Type consistency:** `question_kind` is consistently named across all 11 tasks. The 3-value generator Literal vs. 4-value engine Literal distinction is preserved in every reference (T1 CHECK has 4, T3 schema has 3, T8 still has 4 because QuestionConfig was already shipped in Phase 3).

**Atomicity:** T3 bundles schema flip + 5 fixture updates because splitting would leave the test suite red between commits. T7 bundles 4 prompt edits because splitting would leave the LLM path broken. All other tasks are independent commits.

**Order safety:** Each task leaves the system coherent at HEAD. T1 (migration + ORM-less test) is the only task whose test is red until T2 lands — that's documented in T1 step 7's commit message.

**No engine code touched:** T8 touches `interview_runtime/service.py` (the read site) but does NOT touch any file under `app/modules/interview_engine/` — the factory, controller, tasks, and engine prompts are all Phase 3 territory and remain untouched.
