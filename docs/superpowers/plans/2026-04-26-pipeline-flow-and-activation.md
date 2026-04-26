# Pipeline Flow & Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the recruiter end-to-end pipeline flow from JD-paste through Activate, with per-category stage config, the front-door picker, the activation gate, the A/B/C/D edit-category model with pause-stage flow, pipeline versioning, and LLM-mediated question editing.

**Architecture:** One Alembic migration (`0017`) introduces `pipeline_version`, stage `paused_at`, persisted `is_stale`, the `entered_at_pipeline_version` stamp on candidate assignments, and a `ck_job_postings_status` constraint. Backend changes layer on top: per-category Pydantic enforcement, new `pipeline_built` and `active` job states, removal of silent auto-apply, picker discriminator on `POST /pipeline`, dry-run change-classifier, activation gate, pause/unpause endpoints, persisted-stale recompute, and stateless LLM Refine/Add for questions. Candidates module gains an `active`-state assignment gate and pipeline-version stamping. Frontend mirrors the backend: discriminated `PipelineStageInput`, per-matrix StageConfigDrawer, PipelineSourcePicker, ActivationGate, SourcePill, EditCategoryWarningModal, RefineQuestionDialog, AddQuestionDialog. Spec: `docs/superpowers/specs/2026-04-26-pipeline-flow-and-activation-design.md` (commit 207bdde).

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy async + Alembic + Dramatiq; Next.js 16 + React + Tailwind v4 + TanStack Query + RHF/Zod + shadcn-v4 + vitest; pytest + httpx for backend tests.

**Batch checkpoints:** This plan groups tasks into 10 batches. Each batch ends at a natural commit point and produces working software (passes lint + type-check + relevant tests). The executor can pause between batches for review without leaving the codebase in an in-between state.

---

## File Structure

### Backend new
- `backend/nexus/migrations/versions/0017_pipeline_versioning_and_pause.py`
- `backend/nexus/app/modules/pipelines/classifier.py` (edit-category diff classifier)
- `backend/nexus/app/modules/question_bank/refine.py` (LLM-mediated refine + draft)
- `backend/nexus/prompts/v1/question_refine_single.txt`
- `backend/nexus/prompts/v1/question_create_single.txt`

### Backend modified
- `backend/nexus/app/models.py`
- `backend/nexus/app/modules/pipelines/schemas.py`
- `backend/nexus/app/modules/pipelines/router.py`
- `backend/nexus/app/modules/pipelines/service.py`
- `backend/nexus/app/modules/jd/state_machine.py`
- `backend/nexus/app/modules/jd/service.py`
- `backend/nexus/app/modules/jd/router.py`
- `backend/nexus/app/modules/question_bank/service.py`
- `backend/nexus/app/modules/question_bank/router.py`
- `backend/nexus/app/modules/candidates/service.py`

### Frontend new
- `frontend/app/lib/api/questions.ts`
- `frontend/app/lib/hooks/use-pipeline-classify.ts`
- `frontend/app/lib/hooks/use-activate-job.ts`
- `frontend/app/lib/hooks/use-refine-question.ts`
- `frontend/app/lib/hooks/use-draft-question.ts`
- `frontend/app/components/dashboard/pipeline/PipelineSourcePicker.tsx`
- `frontend/app/components/dashboard/pipeline/ActivationGate.tsx`
- `frontend/app/components/dashboard/pipeline/SourcePill.tsx`
- `frontend/app/components/dashboard/pipeline/EditCategoryWarningModal.tsx`
- `frontend/app/components/dashboard/question-bank/RefineQuestionDialog.tsx`
- `frontend/app/components/dashboard/question-bank/AddQuestionDialog.tsx`

### Frontend modified
- `frontend/app/lib/api/pipelines.ts`
- `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`
- `frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx`
- `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx`
- `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`
- `frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx`

---

## Batch 1 — Foundations (DB migration + ORM models)

Outcome: schema columns + constraint exist; ORM mirrors them; migrations test passes.

### Task 1: Migration `0017_pipeline_versioning_and_pause`

**Files:**
- Create: `backend/nexus/migrations/versions/0017_pipeline_versioning_and_pause.py`

- [ ] **Step 1: Write the migration**

```python
"""pipeline versioning + stage pause + persisted stale + activation states

Adds:
  - pipeline_instances.pipeline_version (monotonic per-instance counter)
  - job_pipeline_stages.paused_at (soft-removal state)
  - question_banks.{pipeline_version_at_generation, stage_config_snapshot, is_stale}
  - candidate_job_assignments.entered_at_pipeline_version (forensic stamp)
  - ck_job_postings_status CHECK with new states (pipeline_built, active, archived)

Data migration: any job in 'signals_confirmed' that already has a pipeline
instance is moved to 'pipeline_built' (matches the new auto-apply-removed
front-door flow — see spec §10).

Revision ID: 0017_pipeline_versioning_and_pause
Revises: 0016_stage_v5_participants
Create Date: 2026-04-26
"""
from __future__ import annotations

from alembic import op

revision = "0017_pipeline_versioning_and_pause"
down_revision = "0016_stage_v5_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pipeline_instances: monotonic version counter
    op.execute("""
        ALTER TABLE pipeline_instances
          ADD COLUMN pipeline_version int NOT NULL DEFAULT 1
    """)

    # job_pipeline_stages: pause state
    op.execute("""
        ALTER TABLE job_pipeline_stages
          ADD COLUMN paused_at timestamptz NULL
    """)
    op.execute("""
        CREATE INDEX ix_job_pipeline_stages_paused_at
          ON job_pipeline_stages (instance_id) WHERE paused_at IS NOT NULL
    """)

    # question_banks: forensic + persisted is_stale
    op.execute("""
        ALTER TABLE question_banks
          ADD COLUMN pipeline_version_at_generation int NULL,
          ADD COLUMN stage_config_snapshot jsonb NULL,
          ADD COLUMN is_stale bool NOT NULL DEFAULT false
    """)

    # Backfill is_stale to match current compute_is_stale (signal-snapshot drift only).
    op.execute("""
        UPDATE question_banks qb
           SET is_stale = (
               qb.signal_snapshot_id != (
                   SELECT id FROM job_posting_signal_snapshots
                    WHERE job_posting_id = (
                        SELECT instance.job_posting_id
                          FROM job_pipeline_stages stage
                          JOIN pipeline_instances instance ON instance.id = stage.instance_id
                         WHERE stage.id = qb.stage_id
                    )
                    AND confirmed_at IS NOT NULL
                    ORDER BY version DESC LIMIT 1
               )
           )
    """)

    # candidate_job_assignments: forensic version stamp
    op.execute("""
        ALTER TABLE candidate_job_assignments
          ADD COLUMN entered_at_pipeline_version int NULL
    """)

    # job_postings.status CHECK — net-new constraint
    op.execute("""
        ALTER TABLE job_postings
          ADD CONSTRAINT ck_job_postings_status
          CHECK (status IN ('draft', 'signals_extracting', 'signals_extraction_failed',
                            'signals_extracted', 'signals_confirmed',
                            'pipeline_built', 'active', 'archived'))
    """)

    # Data migration: confirmed jobs that already auto-applied → pipeline_built
    op.execute("""
        UPDATE job_postings
           SET status = 'pipeline_built'
         WHERE status = 'signals_confirmed'
           AND id IN (SELECT job_posting_id FROM pipeline_instances)
    """)


def downgrade() -> None:
    # Lossy: pipeline_built/active rows downgrade to signals_confirmed.
    op.execute("""
        UPDATE job_postings
           SET status = 'signals_confirmed'
         WHERE status IN ('pipeline_built', 'active')
    """)
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS ck_job_postings_status")

    op.execute("ALTER TABLE candidate_job_assignments DROP COLUMN IF EXISTS entered_at_pipeline_version")

    op.execute("""
        ALTER TABLE question_banks
          DROP COLUMN IF EXISTS is_stale,
          DROP COLUMN IF EXISTS stage_config_snapshot,
          DROP COLUMN IF EXISTS pipeline_version_at_generation
    """)

    op.execute("DROP INDEX IF EXISTS ix_job_pipeline_stages_paused_at")
    op.execute("ALTER TABLE job_pipeline_stages DROP COLUMN IF EXISTS paused_at")

    op.execute("ALTER TABLE pipeline_instances DROP COLUMN IF EXISTS pipeline_version")
```

- [ ] **Step 2: Run migration up**

```bash
cd backend/nexus && docker compose run --rm nexus alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 0016_stage_v5_participants -> 0017_pipeline_versioning_and_pause`. No errors.

- [ ] **Step 3: Verify columns exist**

```bash
docker compose run --rm nexus python -c "
import asyncio
from sqlalchemy import text
from app.database import engine_bypass
async def check():
    async with engine_bypass.connect() as c:
        for q in [
            \"SELECT column_name FROM information_schema.columns WHERE table_name='pipeline_instances' AND column_name='pipeline_version'\",
            \"SELECT column_name FROM information_schema.columns WHERE table_name='job_pipeline_stages' AND column_name='paused_at'\",
            \"SELECT column_name FROM information_schema.columns WHERE table_name='question_banks' AND column_name='is_stale'\",
            \"SELECT column_name FROM information_schema.columns WHERE table_name='candidate_job_assignments' AND column_name='entered_at_pipeline_version'\",
            \"SELECT conname FROM pg_constraint WHERE conname='ck_job_postings_status'\",
        ]:
            r = (await c.execute(text(q))).fetchone()
            print(q.split(\"'\")[3] if \"column_name=\" in q else 'ck_job_postings_status', '→', r)
asyncio.run(check())
"
```

Expected: each query returns a non-None row.

- [ ] **Step 4: Run migration down + back up to confirm reversibility**

```bash
docker compose run --rm nexus alembic downgrade 0016_stage_v5_participants
docker compose run --rm nexus alembic upgrade head
```

Expected: both succeed; columns disappear after downgrade and reappear after upgrade.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/migrations/versions/0017_pipeline_versioning_and_pause.py
git commit -m "feat(db): migration 0017 — pipeline_version, paused_at, is_stale, entered_at_pipeline_version"
```

---

### Task 2: ORM model updates

**Files:**
- Modify: `backend/nexus/app/models.py` — `JobPipelineInstance`, `JobPipelineStage`, `QuestionBank`, `CandidateJobAssignment` classes.

- [ ] **Step 1: Add `pipeline_version` to `JobPipelineInstance`**

Locate `class JobPipelineInstance(Base):` (around line 273) and add inside the class body alongside existing columns:

```python
pipeline_version: Mapped[int] = mapped_column(
    Integer, nullable=False, default=1, server_default="1"
)
```

- [ ] **Step 2: Add `paused_at` to `JobPipelineStage`**

Locate `class JobPipelineStage(Base):` (around line 306) and add:

```python
paused_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

- [ ] **Step 3: Add three columns to `QuestionBank`**

Locate the QuestionBank class and add:

```python
pipeline_version_at_generation: Mapped[int | None] = mapped_column(
    Integer, nullable=True
)
stage_config_snapshot: Mapped[dict | None] = mapped_column(
    JSONB, nullable=True
)
is_stale: Mapped[bool] = mapped_column(
    Boolean, nullable=False, default=False, server_default="false"
)
```

- [ ] **Step 4: Add `entered_at_pipeline_version` to `CandidateJobAssignment`**

Locate the CandidateJobAssignment class and add:

```python
entered_at_pipeline_version: Mapped[int | None] = mapped_column(
    Integer, nullable=True
)
```

- [ ] **Step 5: Type-check + run a smoke test**

```bash
cd backend/nexus && docker compose run --rm nexus mypy app/models.py
docker compose run --rm nexus pytest tests/test_database.py -v
```

Expected: mypy clean (or no new errors); database fixture loads without error.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/models.py
git commit -m "feat(models): mirror migration 0017 columns on JobPipelineInstance, JobPipelineStage, QuestionBank, CandidateJobAssignment"
```

---

## Batch 2 — Pydantic per-category enforcement

Outcome: server rejects/locks fields per the matrix; existing test paths still pass.

### Task 3: Add per-category field-rules validator

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/schemas.py` — add `_validate_fields_for_stage_type`.
- Test: `backend/nexus/tests/test_pipelines_stage_field_rules.py` (new).

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/test_pipelines_stage_field_rules.py`:

```python
"""Per-category field rules: ✓ fields permitted, ✗ rejected, locked stamped."""
import pytest
from pydantic import ValidationError

from app.modules.pipelines.schemas import (
    AdvanceBehavior,
    PassCriteriaKnockout,
    PassCriteriaManual,
    PipelineStageInput,
    SignalFilter,
    StageDifficulty,
)


def _base(stage_type: str, **overrides):
    """Minimal valid kwargs for a given type, overridable for negative tests."""
    base = {
        "position": 1,
        "name": "Test Stage",
        "stage_type": stage_type,
    }
    base.update(overrides)
    return base


# --- Forbidden field rejection -------------------------------------------------

def test_intake_rejects_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("intake", difficulty="medium"))


def test_intake_rejects_duration():
    with pytest.raises(ValidationError, match="duration_minutes"):
        PipelineStageInput(**_base("intake", duration_minutes=30))


def test_intake_rejects_signal_filter():
    with pytest.raises(ValidationError, match="signal_filter"):
        PipelineStageInput(
            **_base("intake", signal_filter={"include_types": ["competency"]})
        )


def test_debrief_rejects_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("debrief", difficulty="hard"))


def test_take_home_rejects_otp_required():
    with pytest.raises(ValidationError, match="otp_required"):
        PipelineStageInput(**_base("take_home", otp_required=True))


# --- Required field enforcement -----------------------------------------------

def test_phone_screen_requires_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("phone_screen",
            duration_minutes=30,
            signal_filter={"include_types": ["competency"]},
            pass_criteria={"type": "all_knockouts_pass"},
            advance_behavior="auto_advance",
        ))


def test_human_interview_full_required_set_succeeds():
    stage = PipelineStageInput(**_base("human_interview",
        duration_minutes=45,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    ))
    assert stage.stage_type == "human_interview"
    assert stage.difficulty == "medium"


# --- Locked field stamping -----------------------------------------------------

def test_intake_pass_criteria_stamped_to_all_knockouts_pass():
    stage = PipelineStageInput(**_base("intake"))
    assert stage.pass_criteria.type == "all_knockouts_pass"
    assert stage.advance_behavior == "auto_advance"


def test_intake_pass_criteria_stamp_overrides_request_value():
    # Even if a client sends a different pass_criteria, intake stamps the canonical one.
    stage = PipelineStageInput(**_base("intake", pass_criteria={"type": "manual_review"}))
    assert stage.pass_criteria.type == "all_knockouts_pass"


def test_debrief_pass_criteria_stamped_to_manual_review():
    stage = PipelineStageInput(**_base("debrief"))
    assert stage.pass_criteria.type == "manual_review"
    assert stage.advance_behavior == "manual_review"


# --- Optional fields are pass-through -----------------------------------------

def test_intake_accepts_optional_sla_days():
    stage = PipelineStageInput(**_base("intake", sla_days=7))
    assert stage.sla_days == 7


def test_phone_screen_omits_optional_otp_required():
    stage = PipelineStageInput(**_base("phone_screen",
        duration_minutes=30,
        difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    assert stage.otp_required is None or stage.otp_required is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_pipelines_stage_field_rules.py -v
```

Expected: tests fail (validator doesn't exist; e.g. intake-with-difficulty currently passes).

- [ ] **Step 3: Implement the validator in `schemas.py`**

In `backend/nexus/app/modules/pipelines/schemas.py`, after the existing `_PARTICIPANT_ROLE_FOR_TYPE` block (around line 90-97), add:

```python
from typing import Any

# Field-rule enums (private to this module).
_REQUIRED = "required"
_FORBIDDEN = "forbidden"
_OPTIONAL = "optional"
_LOCKED = "locked"

# Per-stage-type field rules. Keyed by stage_type → {field_name → rule}.
# Fields not listed are passthrough (no validation here).
_FIELD_RULES_BY_TYPE: dict[str, dict[str, str]] = {
    "intake": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _LOCKED,
        "advance_behavior": _LOCKED, "sla_days": _OPTIONAL,
        "otp_required": _FORBIDDEN,
    },
    "phone_screen": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "ai_screening": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "human_interview": {
        "duration_minutes": _REQUIRED, "difficulty": _REQUIRED,
        "signal_filter": _REQUIRED, "pass_criteria": _REQUIRED,
        "advance_behavior": _REQUIRED, "sla_days": _OPTIONAL,
        "otp_required": _OPTIONAL,
    },
    "debrief": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _LOCKED,
        "advance_behavior": _LOCKED, "sla_days": _OPTIONAL,
        "otp_required": _FORBIDDEN,
    },
    "take_home": {
        "duration_minutes": _FORBIDDEN, "difficulty": _FORBIDDEN,
        "signal_filter": _FORBIDDEN, "pass_criteria": _FORBIDDEN,
        "advance_behavior": _FORBIDDEN, "sla_days": _FORBIDDEN,
        "otp_required": _FORBIDDEN,
    },
}

# Locked values per (stage_type, field).
_LOCKED_VALUES: dict[str, dict[str, Any]] = {
    "intake": {
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
    },
    "debrief": {
        "pass_criteria": {"type": "manual_review"},
        "advance_behavior": "manual_review",
    },
}


def _validate_fields_for_stage_type(values: dict) -> dict:
    """Validate matrix-driven field rules; mutate `values` for LOCKED fields."""
    stage_type = values.get("stage_type")
    if stage_type not in _FIELD_RULES_BY_TYPE:
        return values
    rules = _FIELD_RULES_BY_TYPE[stage_type]
    locked = _LOCKED_VALUES.get(stage_type, {})
    for field, rule in rules.items():
        present = field in values and values[field] is not None
        if rule == _FORBIDDEN and present:
            raise ValueError(
                f"{field} is not allowed for stage_type='{stage_type}'"
            )
        if rule == _REQUIRED and not present:
            raise ValueError(
                f"{field} is required for stage_type='{stage_type}'"
            )
        if rule == _LOCKED:
            values[field] = locked[field]  # stamp canonical value
    return values
```

- [ ] **Step 4: Wire the validator into `PipelineStageInput`**

Find the existing `PipelineStageInput` class (around line 146). Add a `model_validator(mode="before")` that calls `_validate_fields_for_stage_type`, alongside the existing `_validate_participants_role_for_type` call:

```python
from pydantic import model_validator

class PipelineStageInput(PipelineStageBase):
    participants: list[StageParticipantInput] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _apply_field_rules(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return _validate_fields_for_stage_type(data)
        return data

    @model_validator(mode="after")
    def _check_participants(self) -> "PipelineStageInput":
        _validate_participants_role_for_type(self.stage_type, self.participants)
        return self
```

Same pattern for `PipelineStageUpdateInput`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_pipelines_stage_field_rules.py -v
```

Expected: all 12 tests pass.

- [ ] **Step 6: Run the broader pipelines test suite to confirm no regression**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_router.py tests/test_pipelines_service.py tests/test_pipelines_starter_pack.py tests/test_pipeline_participants.py -v
```

Expected: all green. Note: starter pack stages must already comply with the matrix; the existing tests verify that.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/pipelines/schemas.py backend/nexus/tests/test_pipelines_stage_field_rules.py
git commit -m "feat(pipelines): per-category field rules — server-stamp locked fields, reject forbidden ones"
```

---

## Batch 3 — State machine + auto-apply removal + picker discriminator

Outcome: `confirm_signals` no longer auto-applies; `POST /pipeline` accepts a discriminated `source` body and transitions job to `pipeline_built`.

### Task 4: New legal transitions in `LEGAL_TRANSITIONS`

**Files:**
- Modify: `backend/nexus/app/modules/jd/state_machine.py:24-29`.
- Test: `backend/nexus/tests/test_jd_state_machine.py` (existing — extend).

- [ ] **Step 1: Add a failing test for new transitions**

Append to `backend/nexus/tests/test_jd_state_machine.py`:

```python
def test_signals_confirmed_to_pipeline_built_legal():
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("signals_confirmed", "pipeline_built") is True


def test_pipeline_built_to_active_legal():
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("pipeline_built", "active") is True


def test_active_has_no_outbound_transitions():
    from app.modules.jd.state_machine import LEGAL_TRANSITIONS
    assert LEGAL_TRANSITIONS["active"] == set()


def test_archived_has_no_outbound_transitions():
    from app.modules.jd.state_machine import LEGAL_TRANSITIONS
    assert LEGAL_TRANSITIONS["archived"] == set()


def test_pipeline_built_back_to_signals_confirmed_illegal():
    # Pipeline-built does not transition back to signals_confirmed in this design.
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("pipeline_built", "signals_confirmed") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_jd_state_machine.py -v -k "pipeline_built or active or archived"
```

Expected: KeyError or assertion failures because `pipeline_built` / `active` / `archived` are not in `LEGAL_TRANSITIONS`.

- [ ] **Step 3: Update `LEGAL_TRANSITIONS`**

In `backend/nexus/app/modules/jd/state_machine.py`, replace:

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},
    "signals_extracted": {"signals_confirmed"},
    "signals_confirmed": {"signals_extracted"},
}
```

with:

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},
    "signals_extracted": {"signals_confirmed"},
    "signals_confirmed": {"signals_extracted", "pipeline_built"},
    "pipeline_built": {"active"},
    "active": set(),
    "archived": set(),
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm nexus pytest tests/test_jd_state_machine.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/jd/state_machine.py backend/nexus/tests/test_jd_state_machine.py
git commit -m "feat(jd/state): add pipeline_built, active, archived states + transitions"
```

---

### Task 5: Remove silent auto-apply from `confirm_signals`

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py:427-440` — remove the auto-apply block.
- Modify: `backend/nexus/tests/test_pipelines_auto_apply.py` — adapt tests.

- [ ] **Step 1: Update the existing test to reflect new behavior**

Open `backend/nexus/tests/test_pipelines_auto_apply.py`. Find any test that calls `confirm_signals` and asserts a pipeline instance is created. Add new assertion: after `confirm_signals`, **no** pipeline instance exists. The existing direct-helper coverage of `auto_apply_pipeline_on_confirmation` stays (call it directly in those tests).

Add this fresh test at the bottom:

```python
@pytest.mark.asyncio
async def test_confirm_signals_does_not_auto_apply_pipeline(test_db_session, _confirmed_signals_fixture):
    """After this design lands, confirm_signals leaves the pipeline empty.
    The recruiter creates one explicitly via the picker (POST /api/jobs/{id}/pipeline)."""
    job = _confirmed_signals_fixture  # job is in signals_extracted with one snapshot
    await confirm_signals(test_db_session, job=job, actor_id=job.created_by, correlation_id="cid")
    # No instance should exist after confirm_signals.
    result = await test_db_session.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == job.id)
    )
    assert result.scalar_one_or_none() is None
    # Job is in signals_confirmed, not pipeline_built (picker hasn't run yet).
    await test_db_session.refresh(job)
    assert job.status == "signals_confirmed"
```

(If `_confirmed_signals_fixture` doesn't exist verbatim, reuse whatever fixture the existing tests in this file use — pattern-match the helpers like `create_test_user` etc. already imported there.)

- [ ] **Step 2: Run the new test to verify it fails**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_auto_apply.py::test_confirm_signals_does_not_auto_apply_pipeline -v
```

Expected: FAIL — `confirm_signals` currently still calls `auto_apply_pipeline_on_confirmation`, so an instance gets created.

- [ ] **Step 3: Remove the auto-apply block in `jd/service.py`**

Open `backend/nexus/app/modules/jd/service.py`. Locate the block at lines 427-440 (around the `confirm_signals` function — after the state transition, there's a try/except that imports and awaits `auto_apply_pipeline_on_confirmation`). Delete the entire try/except block. The structlog `logger.info("jd.service.signals_confirmed", ...)` call should remain.

The code before:

```python
try:
    from app.modules.pipelines.errors import PipelineAlreadyExistsError
    from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

    await auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=actor_id,
    )
except PipelineAlreadyExistsError:
    logger.debug(
        "jd.pipeline_auto_apply_skipped_existing",
        ...
    )
except Exception:
    logger.error(
        "jd.pipeline_auto_apply_failed",
        ...
    )
```

is removed entirely.

- [ ] **Step 4: Run the new test to verify it passes**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_auto_apply.py::test_confirm_signals_does_not_auto_apply_pipeline -v
```

Expected: PASS.

- [ ] **Step 5: Update existing tests in this file that assumed auto-apply happens**

Re-run the rest of the file:

```bash
docker compose run --rm nexus pytest tests/test_pipelines_auto_apply.py -v
```

For any FAILED tests that asserted auto-apply happened **via `confirm_signals`**, update them to **call `auto_apply_pipeline_on_confirmation` directly** (the helper is still in `pipelines/service.py` per the spec). The helper remains exercised; only its automatic invocation from `confirm_signals` is gone.

- [ ] **Step 6: Run broader JD tests to confirm no regression**

```bash
docker compose run --rm nexus pytest tests/test_jd_router.py tests/test_jd_service_create.py tests/test_jd_state_transitions_integration.py -v
```

Expected: all green. If the integration test asserts auto-apply, fix it the same way (direct helper call).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/jd/service.py backend/nexus/tests/test_pipelines_auto_apply.py
git commit -m "feat(jd): remove silent auto_apply_pipeline_on_confirmation from confirm_signals

The picker on /pipeline is now the only path that creates an instance.
auto_apply_pipeline_on_confirmation stays as a direct-call helper for tests
and any future fast-path use case."
```

---

### Task 6: Discriminated `source` body on `POST /api/jobs/{id}/pipeline`

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/schemas.py` — add `PipelineCreateRequest` discriminated union.
- Modify: `backend/nexus/app/modules/pipelines/router.py` (around line 341) — accept new body shape.
- Modify: `backend/nexus/app/modules/pipelines/service.py` — add unified `create_job_pipeline_from_source` if not already present, and transition job to `pipeline_built`.
- Test: `backend/nexus/tests/test_pipelines_router.py` (existing — extend).

- [ ] **Step 1: Write failing tests**

Append to `backend/nexus/tests/test_pipelines_router.py`:

```python
@pytest.mark.asyncio
async def test_post_pipeline_with_source_template_creates_instance_and_transitions(
    auth_client, _job_in_signals_confirmed,
):
    """Picker template path: creates instance + transitions job to pipeline_built."""
    job, template = _job_in_signals_confirmed  # fixture provides both
    resp = await auth_client.post(
        f"/api/jobs/{job.id}/pipeline",
        json={"source": "template", "template_id": str(template.id)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["pipeline_version"] == 1
    assert body["source_template_id"] == str(template.id)
    # Job now in pipeline_built.
    job_resp = await auth_client.get(f"/api/jd/{job.id}")
    assert job_resp.json()["status"] == "pipeline_built"


@pytest.mark.asyncio
async def test_post_pipeline_with_source_starter_creates_instance(auth_client, _job_in_signals_confirmed):
    job, _ = _job_in_signals_confirmed
    resp = await auth_client.post(
        f"/api/jobs/{job.id}/pipeline",
        json={"source": "starter", "starter_key": "standard_technical"},
    )
    assert resp.status_code == 201
    assert resp.json()["pipeline_version"] == 1


@pytest.mark.asyncio
async def test_post_pipeline_with_source_scratch_creates_intake_debrief_only(auth_client, _job_in_signals_confirmed):
    job, _ = _job_in_signals_confirmed
    resp = await auth_client.post(
        f"/api/jobs/{job.id}/pipeline",
        json={"source": "scratch"},
    )
    assert resp.status_code == 201
    stages = resp.json()["stages"]
    assert len(stages) == 2
    assert stages[0]["stage_type"] == "intake"
    assert stages[1]["stage_type"] == "debrief"


@pytest.mark.asyncio
async def test_post_pipeline_when_instance_already_exists_returns_409(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    resp = await auth_client.post(
        f"/api/jobs/{job.id}/pipeline",
        json={"source": "starter", "starter_key": "standard_technical"},
    )
    assert resp.status_code == 409
    assert "pipeline_already_exists" in resp.json()["detail"].lower() or \
           resp.json()["detail"].get("code") == "pipeline_already_exists"
```

(If existing fixtures `_job_in_signals_confirmed` / `_job_with_pipeline` don't exist, create them by following patterns in the same test file. Use existing helpers `create_test_client`, `create_test_org_unit`, `create_test_user` from `tests/conftest.py`.)

- [ ] **Step 2: Add the discriminated request schema**

In `backend/nexus/app/modules/pipelines/schemas.py`, add:

```python
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, Discriminator, Tag

StarterKey = Literal[
    "standard_technical",
    "fast_track",
    "screening_only",
    "senior_leadership",
]


class PipelineCreateFromTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["template"]
    template_id: UUID


class PipelineCreateFromStarter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["starter"]
    starter_key: StarterKey


class PipelineCreateFromScratch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["scratch"]


PipelineCreateRequest = Annotated[
    PipelineCreateFromTemplate | PipelineCreateFromStarter | PipelineCreateFromScratch,
    Field(discriminator="source"),
]
```

- [ ] **Step 3: Update the router endpoint**

In `backend/nexus/app/modules/pipelines/router.py` (around line 341 — the existing `POST /api/jobs/{job_id}/pipeline` handler), change the request body parameter to `body: PipelineCreateRequest` and dispatch by `body.source`:

```python
from app.modules.jd.state_machine import transition as jd_transition
from app.modules.pipelines.schemas import (
    PipelineCreateFromScratch,
    PipelineCreateFromStarter,
    PipelineCreateFromTemplate,
    PipelineCreateRequest,
)

@router.post(
    "/api/jobs/{job_id}/pipeline",
    response_model=PipelineInstanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_job_pipeline_endpoint(
    job_id: UUID,
    body: PipelineCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
    correlation_id: str = Depends(require_correlation_id),
) -> PipelineInstanceResponse:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status != "signals_confirmed":
        raise HTTPException(409, detail={
            "code": "pipeline_create_wrong_state",
            "message": f"Pipeline can only be created when job is in signals_confirmed; current: {job.status}",
        })
    if isinstance(body, PipelineCreateFromTemplate):
        instance = await service.create_pipeline_from_template(
            db, job=job, template_id=body.template_id, actor_id=user.user_id,
        )
    elif isinstance(body, PipelineCreateFromStarter):
        instance = await service.create_pipeline_from_starter(
            db, job=job, starter_key=body.starter_key, actor_id=user.user_id,
        )
    else:  # PipelineCreateFromScratch
        instance = await service.create_pipeline_from_scratch(
            db, job=job, actor_id=user.user_id,
        )
    await jd_transition(db, job, to_state="pipeline_built", actor_id=user.user_id, correlation_id=correlation_id)
    return await service.instance_response(db, instance)
```

- [ ] **Step 4: Implement the three service helpers**

In `backend/nexus/app/modules/pipelines/service.py`, ensure the three helpers exist (some already do — the existing `auto_apply_pipeline_on_confirmation` calls them under different names). Refactor to:

```python
async def create_pipeline_from_template(
    db: AsyncSession, *, job: JobPosting, template_id: UUID, actor_id: UUID,
) -> JobPipelineInstance: ...

async def create_pipeline_from_starter(
    db: AsyncSession, *, job: JobPosting, starter_key: str, actor_id: UUID,
) -> JobPipelineInstance: ...

async def create_pipeline_from_scratch(
    db: AsyncSession, *, job: JobPosting, actor_id: UUID,
) -> JobPipelineInstance:
    """Creates an instance with only intake (position 0) + debrief (last position).
    No participants, no banks, no source_template_id, no source_starter_key.
    pipeline_version = 1."""
    # Reuse existing stage-creation helper; emit two PipelineStageInputs:
    intake = PipelineStageInput(position=0, name="Intake", stage_type="intake")
    debrief = PipelineStageInput(position=1, name="Debrief", stage_type="debrief")
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
        source_starter_key=None,
        pipeline_version=1,
        created_by=actor_id,
    )
    db.add(instance)
    await db.flush()
    for stage_input in (intake, debrief):
        await _persist_stage_from_input(db, instance, stage_input)
    return instance
```

The `auto_apply_pipeline_on_confirmation` helper remains as-is — it picks one of these three and calls it. That helper is no longer wired in to `confirm_signals` (Task 5) but stays for direct-call test coverage and any future fast-path.

- [ ] **Step 5: Define `PipelineAlreadyExistsError` if not already raised**

The existing service already raises `PipelineAlreadyExistsError` if an instance exists for the job. Verify in router that this maps to 409 with `code: pipeline_already_exists` (existing exception handler in `app/main.py` should cover this — verify).

- [ ] **Step 6: Run the new tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_router.py -v -k "post_pipeline"
```

Expected: all four new tests pass.

- [ ] **Step 7: Run the full pipeline test suite**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_router.py tests/test_pipelines_service.py tests/test_pipelines_auto_apply.py tests/test_pipelines_starter_pack.py -v
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/pipelines/schemas.py backend/nexus/app/modules/pipelines/router.py backend/nexus/app/modules/pipelines/service.py backend/nexus/tests/test_pipelines_router.py
git commit -m "feat(pipelines): discriminated source body on POST /pipeline + transition to pipeline_built"
```

---

## Batch 4 — Pipeline versioning + change classifier + preview-changes

Outcome: every save bumps `pipeline_version`; a server-side classifier categorizes diffs A/B/C/D; `POST /pipeline/preview-changes` returns the classification.

### Task 7: `pipeline_version` bumps on every save

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/service.py` — bump in `update_job_pipeline_stages` and any other write path.
- Test: `backend/nexus/tests/test_pipelines_versioning.py` (new).

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_pipelines_versioning.py`:

```python
"""pipeline_version bumps on every save."""
import pytest
from sqlalchemy import select

from app.models import JobPipelineInstance


@pytest.mark.asyncio
async def test_patch_pipeline_bumps_version(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    # Read initial version
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    v0 = r0.json()["pipeline_version"]

    # Reorder a stage (no-op semantically — just to trigger save)
    stages = r0.json()["stages"]
    payload = {"stages": [
        {"id": s["id"], "position": s["position"], "name": s["name"],
         "stage_type": s["stage_type"]}
        for s in stages
    ]}
    r1 = await auth_client.patch(f"/api/jobs/{job.id}/pipeline", json=payload)
    assert r1.status_code == 200
    assert r1.json()["pipeline_version"] == v0 + 1


@pytest.mark.asyncio
async def test_question_crud_bumps_pipeline_version(auth_client, _job_with_pipeline_and_bank):
    job, stage_id, qid = _job_with_pipeline_and_bank
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    v0 = r0.json()["pipeline_version"]
    # Toggle mandatory on a question
    await auth_client.patch(
        f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/{qid}",
        json={"mandatory": True},
    )
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    assert r1.json()["pipeline_version"] == v0 + 1


@pytest.mark.asyncio
async def test_signal_edit_does_not_bump_pipeline_version(auth_client, _job_with_pipeline_and_signals):
    job, snapshot_id = _job_with_pipeline_and_signals
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    v0 = r0.json()["pipeline_version"]
    # Edit a signal (Phase 2B save_signals path bumps signal_snapshot.version, not pipeline_version)
    await auth_client.post(
        f"/api/jd/{job.id}/signals",
        json={"expected_version": 1, "signals": [], "knockouts": []},
    )
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    assert r1.json()["pipeline_version"] == v0  # unchanged
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_pipelines_versioning.py -v
```

Expected: FAIL — `pipeline_version` field returns 1 always (server-default), no bump on save.

- [ ] **Step 3: Add a centralized bump helper**

In `backend/nexus/app/modules/pipelines/service.py`, add at module scope:

```python
async def bump_pipeline_version(db: AsyncSession, instance: JobPipelineInstance) -> None:
    """Atomically increment the instance's pipeline_version. Caller must be inside
    the same transaction as the mutation that triggers the bump."""
    instance.pipeline_version = instance.pipeline_version + 1
    await db.flush()
```

- [ ] **Step 4: Wire `bump_pipeline_version` into every write path**

Search for all save paths in `app/modules/pipelines/service.py` and `app/modules/question_bank/service.py`. Add `await bump_pipeline_version(db, instance)` after the mutation in each, before commit:

- `update_job_pipeline_stages` (PATCH /pipeline)
- `replace_stage_participants` (participant changes)
- All bank-question CRUD (will be added in Batch 5; placeholder note only here)

Concrete edit in `app/modules/pipelines/service.py::update_job_pipeline_stages`:

```python
async def update_job_pipeline_stages(
    db: AsyncSession, *, instance: JobPipelineInstance, stages_input: list[PipelineStageUpdateInput], actor_id: UUID,
) -> JobPipelineInstance:
    # ... existing diff-and-sync logic ...
    await bump_pipeline_version(db, instance)
    return instance
```

Repeat for the participant write path.

- [ ] **Step 5: Run tests to verify pass**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_versioning.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/pipelines/service.py backend/nexus/tests/test_pipelines_versioning.py
git commit -m "feat(pipelines): bump_pipeline_version on every save path"
```

---

### Task 8: Change classifier (`classify_pipeline_diff`)

**Files:**
- Create: `backend/nexus/app/modules/pipelines/classifier.py`.
- Test: `backend/nexus/tests/test_pipelines_classify_diff.py` (new).

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_pipelines_classify_diff.py`:

```python
"""Edit-category classifier tests — A/B/C/D mapping per spec §8."""
import pytest

from app.modules.pipelines.classifier import classify_pipeline_diff, EditCategory


def _stage(id_, position, stage_type, **overrides):
    base = {
        "id": id_, "position": position, "stage_type": stage_type,
        "name": f"S{position}", "paused_at": None,
        "duration_minutes": 30 if stage_type not in ("intake", "debrief") else None,
        "difficulty": "medium" if stage_type not in ("intake", "debrief") else None,
        "signal_filter": {"include_types": ["competency"]} if stage_type not in ("intake", "debrief") else None,
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
        "sla_days": None,
    }
    base.update(overrides)
    return base


def test_no_changes_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_duration_change_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", duration_minutes=45), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_add_stage_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
                _stage("new", 2, "ai_screening"), _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_reorder_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
               _stage("s2", 2, "ai_screening"), _stage("s3", 3, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "ai_screening"),
                _stage("s1", 2, "phone_screen"), _stage("s3", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_remove_stage_with_zero_in_flight_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 0})
    assert result.category == EditCategory.C
    assert result.in_flight.get("s1", 0) == 0


def test_remove_stage_with_in_flight_is_category_c_with_in_flight_count():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 3})
    assert result.category == EditCategory.C
    assert result.in_flight["s1"] == 3


def test_pause_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", paused_at="2026-04-26T10:00:00Z"),
                _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.C


def test_stage_type_change_is_category_d():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "ai_screening"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.D


def test_highest_category_wins():
    """If a diff contains both A and B changes, B wins. If B and C, C wins."""
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    # Add a stage AND change duration on existing
    proposed = [_stage("s0", 0, "intake"),
                _stage("s1", 1, "phone_screen", duration_minutes=45),
                _stage("new", 2, "ai_screening"),
                _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B  # B wins over A
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/test_pipelines_classify_diff.py -v
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Implement the classifier**

Create `backend/nexus/app/modules/pipelines/classifier.py`:

```python
"""Edit-category diff classifier — see spec §8.

Classifies a proposed pipeline edit into one of four categories:
  A — Forward-only safe (config tweaks, participant swaps, question CRUD)
  B — Shape additive (add a stage, reorder, unpause)
  C — Shape subtractive (remove a stage, pause)
  D — Identity-changing (stage_type change)

The PATCH endpoint runs this server-side as the source of truth; the
:preview-changes endpoint exposes it to the frontend for warning UX.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EditCategory(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


@dataclass
class ClassificationResult:
    category: EditCategory
    warnings: list[str] = field(default_factory=list)
    in_flight: dict[str, int] = field(default_factory=dict)


def _stages_by_id(stages: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in stages if "id" in s}


def classify_pipeline_diff(
    *,
    current: list[dict],
    proposed: list[dict],
    in_flight: dict[str, int],
) -> ClassificationResult:
    """Classify the diff between current and proposed stage lists.

    current/proposed: list of stage dicts with at minimum {id, position, stage_type,
                      name, paused_at, duration_minutes, difficulty, signal_filter,
                      pass_criteria, advance_behavior, sla_days}.
    in_flight: stage_id → count of in-flight candidates currently in that stage.

    Returns the highest-severity category triggered by any change.
    Returns A by default (including no-changes case).
    """
    cur_by_id = _stages_by_id(current)
    new_by_id = _stages_by_id(proposed)

    cur_ids = set(cur_by_id) - {None}
    new_ids = set(new_by_id) - {None}

    # D: stage_type changed on a kept stage
    for sid in cur_ids & new_ids:
        if cur_by_id[sid]["stage_type"] != new_by_id[sid]["stage_type"]:
            return ClassificationResult(
                category=EditCategory.D,
                warnings=[f"stage_type changed on stage {sid}"],
                in_flight={sid: in_flight.get(sid, 0)},
            )

    # C: stages removed OR newly paused
    removed_ids = cur_ids - new_ids
    paused_ids = {
        sid for sid in cur_ids & new_ids
        if not cur_by_id[sid].get("paused_at") and new_by_id[sid].get("paused_at")
    }
    if removed_ids or paused_ids:
        affected = removed_ids | paused_ids
        return ClassificationResult(
            category=EditCategory.C,
            warnings=[f"stages affected: {sorted(affected)}"],
            in_flight={sid: in_flight.get(sid, 0) for sid in affected},
        )

    # B: stages added OR reordered OR unpaused
    added_ids = new_ids - cur_ids
    reordered = any(
        cur_by_id[sid]["position"] != new_by_id[sid]["position"]
        for sid in cur_ids & new_ids
    )
    unpaused_ids = {
        sid for sid in cur_ids & new_ids
        if cur_by_id[sid].get("paused_at") and not new_by_id[sid].get("paused_at")
    }
    if added_ids or reordered or unpaused_ids:
        return ClassificationResult(
            category=EditCategory.B,
            warnings=([f"added: {sorted(added_ids)}"] if added_ids else [])
                + (["reordered"] if reordered else [])
                + ([f"unpaused: {sorted(unpaused_ids)}"] if unpaused_ids else []),
            in_flight={},
        )

    # A: anything else (config tweaks on kept stages, no shape change)
    return ClassificationResult(category=EditCategory.A)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_classify_diff.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/pipelines/classifier.py backend/nexus/tests/test_pipelines_classify_diff.py
git commit -m "feat(pipelines): classify_pipeline_diff — A/B/C/D edit-category classifier"
```

---

### Task 9: `POST /pipeline/preview-changes` endpoint + PATCH integration

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/router.py` — add preview-changes endpoint; integrate classifier into PATCH.
- Modify: `backend/nexus/app/modules/pipelines/service.py` — add `count_in_flight_per_stage` helper.
- Test: `backend/nexus/tests/test_pipelines_classify_diff.py` (extend with HTTP cases).

- [ ] **Step 1: Add the in-flight counter helper**

In `backend/nexus/app/modules/pipelines/service.py`, add:

```python
from app.models import CandidateJobAssignment

async def count_in_flight_per_stage(
    db: AsyncSession, *, instance: JobPipelineInstance,
) -> dict[str, int]:
    """Count active candidate_job_assignments per stage_id in this instance.
    Returns {stage_id: count}, only including stages with count > 0."""
    q = (
        select(CandidateJobAssignment.current_stage_id, func.count())
        .where(CandidateJobAssignment.status == "active")
        .where(CandidateJobAssignment.current_stage_id.in_(
            select(JobPipelineStage.id).where(JobPipelineStage.instance_id == instance.id)
        ))
        .group_by(CandidateJobAssignment.current_stage_id)
    )
    rows = (await db.execute(q)).all()
    return {str(stage_id): n for stage_id, n in rows}
```

- [ ] **Step 2: Add the preview-changes endpoint to the router**

In `backend/nexus/app/modules/pipelines/router.py`, add:

```python
from app.modules.pipelines.classifier import classify_pipeline_diff, EditCategory
from app.modules.pipelines.schemas import UpdateJobPipelineRequest

class PreviewChangesResponse(BaseModel):
    category: Literal["A", "B", "C", "D"]
    warnings: list[str]
    in_flight: dict[str, int]


@router.post(
    "/api/jobs/{job_id}/pipeline/preview-changes",
    response_model=PreviewChangesResponse,
)
async def preview_pipeline_changes(
    job_id: UUID,
    body: UpdateJobPipelineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PreviewChangesResponse:
    job = await require_job_access(db, job_id, user, "manage")
    instance = await service.get_job_pipeline_instance(db, job=job)
    if instance is None:
        raise HTTPException(404, detail="Pipeline instance not found")
    current = [
        service.stage_to_dict(s) for s in await service.list_stages(db, instance=instance)
    ]
    proposed = [stage.model_dump() for stage in body.stages]
    in_flight = await service.count_in_flight_per_stage(db, instance=instance)
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight=in_flight)
    return PreviewChangesResponse(
        category=result.category.value,
        warnings=result.warnings,
        in_flight=result.in_flight,
    )
```

(`stage_to_dict` is a helper that serializes a SQLAlchemy stage to the dict shape the classifier expects; add it to service.py if it doesn't already exist.)

- [ ] **Step 3: Integrate classifier into PATCH for active-job enforcement**

In `backend/nexus/app/modules/pipelines/router.py`, modify the existing `PATCH /api/jobs/{job_id}/pipeline` handler. After loading current state, before applying the diff:

```python
if job.status == "active":
    in_flight = await service.count_in_flight_per_stage(db, instance=instance)
    current = [service.stage_to_dict(s) for s in current_stages]
    proposed = [s.model_dump() for s in body.stages]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight=in_flight)
    if result.category == EditCategory.D:
        raise HTTPException(409, detail={
            "code": "stage_type_change_forbidden",
            "message": "Stage type can't be changed once the job is active. Remove this stage and add a new one.",
        })
    # B/C are allowed but the frontend should have warned. The actual semantics
    # (pause-first for in-flight > 0) is enforced in service.update_job_pipeline_stages.
```

- [ ] **Step 4: Add tests for the new endpoint**

Append to `backend/nexus/tests/test_pipelines_classify_diff.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_preview_endpoint_returns_category_A_for_no_op(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    body = {"stages": [
        {"id": s["id"], "position": s["position"], "name": s["name"],
         "stage_type": s["stage_type"]}
        for s in r0.json()["stages"]
    ]}
    r = await auth_client.post(f"/api/jobs/{job.id}/pipeline/preview-changes", json=body)
    assert r.status_code == 200
    assert r.json()["category"] == "A"


@pytest.mark.asyncio
async def test_active_job_blocks_stage_type_change_with_409(auth_client, _active_job_with_pipeline):
    job = _active_job_with_pipeline
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    stages = r0.json()["stages"]
    # Flip a non-IO stage's type
    middle = next(s for s in stages if s["stage_type"] == "phone_screen")
    middle["stage_type"] = "ai_screening"
    payload = {"stages": [
        {"id": s["id"], "position": s["position"], "name": s["name"], "stage_type": s["stage_type"]}
        for s in stages
    ]}
    r = await auth_client.patch(f"/api/jobs/{job.id}/pipeline", json=payload)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "stage_type_change_forbidden"
```

- [ ] **Step 5: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_classify_diff.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/pipelines/router.py backend/nexus/app/modules/pipelines/service.py backend/nexus/tests/test_pipelines_classify_diff.py
git commit -m "feat(pipelines): preview-changes endpoint + active-job Category-D enforcement on PATCH"
```

---

## Batch 5 — Persisted `is_stale` + recompute helper

Outcome: `is_stale` is read from the DB column instead of computed on every request; write paths that mutate signals or stage_config recompute it.

### Task 10: `recompute_and_persist_stale` + write-path wiring

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/service.py:150` — repurpose `compute_is_stale` into `recompute_and_persist_stale`.
- Modify: `backend/nexus/app/modules/question_bank/router.py` (read paths at lines 275, 337) — read `bank.is_stale` directly.
- Modify: `backend/nexus/app/modules/jd/service.py::save_signals` — call `recompute_and_persist_stale` for all banks of the affected job.
- Modify: `backend/nexus/app/modules/pipelines/service.py::update_job_pipeline_stages` — call `recompute_and_persist_stale` for any bank whose stage's signal_filter or difficulty changed.
- Test: `backend/nexus/tests/test_question_banks_stale_persisted.py` (new).

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_question_banks_stale_persisted.py`:

```python
"""Persisted is_stale flag — set on writes, read directly from column."""
import pytest


@pytest.mark.asyncio
async def test_signal_edit_flips_is_stale(auth_client, _job_with_generated_bank):
    job, stage_id = _job_with_generated_bank
    # Bank starts not stale
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r0.json()["is_stale"] is False
    # Edit a signal
    await auth_client.post(f"/api/jd/{job.id}/signals", json={
        "expected_version": 1, "signals": [], "knockouts": [],
    })
    # Bank now stale
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r1.json()["is_stale"] is True


@pytest.mark.asyncio
async def test_signal_filter_change_flips_is_stale(auth_client, _job_with_generated_bank):
    job, stage_id = _job_with_generated_bank
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r0.json()["is_stale"] is False
    # Change the stage's signal_filter via PATCH /pipeline
    pipeline = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()
    stages = pipeline["stages"]
    target = next(s for s in stages if s["id"] == str(stage_id))
    target["signal_filter"] = {"include_types": ["behavioral"]}  # was ["competency"]
    await auth_client.patch(f"/api/jobs/{job.id}/pipeline", json={"stages": stages})
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r1.json()["is_stale"] is True


@pytest.mark.asyncio
async def test_confirmed_bank_drops_to_generated_on_stale(auth_client, _job_with_confirmed_bank):
    job, stage_id = _job_with_confirmed_bank
    # Confirmed
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r0.json()["status"] == "confirmed"
    # Edit signal → bank goes stale + drops to generated
    await auth_client.post(f"/api/jd/{job.id}/signals", json={
        "expected_version": 1, "signals": [], "knockouts": [],
    })
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")
    assert r1.json()["is_stale"] is True
    assert r1.json()["status"] == "generated"
    assert r1.json()["confirmed_at"] is None
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm nexus pytest tests/test_question_banks_stale_persisted.py -v
```

Expected: failures (current code computes on read; column not yet wired).

- [ ] **Step 3: Refactor `compute_is_stale` → `recompute_and_persist_stale`**

In `backend/nexus/app/modules/question_bank/service.py:150`:

```python
async def recompute_and_persist_stale(
    db: AsyncSession, bank: QuestionBank, *, current_stage_config: dict | None = None,
) -> bool:
    """Recompute is_stale and persist. Returns the new value.

    Source-of-truth predicate: stale if the latest signal_snapshot id differs from
    what the bank was generated against, OR the stage's signal_filter/difficulty
    differs from stage_config_snapshot.
    """
    latest_snapshot = await _latest_confirmed_signal_snapshot_id_for_stage(db, bank)
    signal_drift = bank.signal_snapshot_id != latest_snapshot

    config_drift = False
    if current_stage_config is not None and bank.stage_config_snapshot is not None:
        # Compare a small subset that affects question generation:
        for key in ("signal_filter", "difficulty"):
            if current_stage_config.get(key) != bank.stage_config_snapshot.get(key):
                config_drift = True
                break

    new_stale = signal_drift or config_drift

    if new_stale and bank.status == "confirmed":
        # Per spec §11.5: confirmation drops back to generated when bank goes stale
        bank.status = "generated"
        bank.confirmed_at = None
        bank.confirmed_by = None

    bank.is_stale = new_stale
    await db.flush()
    return new_stale


async def _latest_confirmed_signal_snapshot_id_for_stage(
    db: AsyncSession, bank: QuestionBank,
) -> UUID | None:
    """Lookup the latest confirmed signal snapshot for the job that owns this bank's stage."""
    q = (
        select(JobPostingSignalSnapshot.id)
        .join(JobPipelineStage, JobPostingSignalSnapshot.job_posting_id == _stage_to_job_subquery())  # see below
        .where(JobPipelineStage.id == bank.stage_id)
        .where(JobPostingSignalSnapshot.confirmed_at.is_not(None))
        .order_by(JobPostingSignalSnapshot.version.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


# Backward-compat shim — keep `compute_is_stale` returning the persisted value
async def compute_is_stale(db: AsyncSession, bank: QuestionBank) -> bool:
    """Backward-compat: returns bank.is_stale directly. New code should not call this."""
    return bank.is_stale
```

(The exact join in `_latest_confirmed_signal_snapshot_id_for_stage` already exists in the current `compute_is_stale` — port the logic; this is a refactor, not a new query.)

- [ ] **Step 4: Update bank read paths to read column directly**

In `backend/nexus/app/modules/question_bank/router.py`, find the calls to `compute_is_stale(db, bank)` (lines around 361, 607, 797 per earlier audit). Replace each with:

```python
is_stale = bank.is_stale
```

Also update the bulk-load `service.py::list_banks_for_pipeline` (which uses `compute_is_stale` internally) to read `bank.is_stale` directly.

- [ ] **Step 5: Wire `recompute_and_persist_stale` into write paths**

In `backend/nexus/app/modules/jd/service.py::save_signals`, after persisting the new snapshot, recompute stale for all banks of the job:

```python
async def save_signals(...) -> ...:
    # ... existing snapshot creation logic ...
    # After commit-flush of the new snapshot:
    instance = await pipelines_service.get_job_pipeline_instance(db, job=job)
    if instance:
        banks = await question_bank_service.list_banks_for_instance(db, instance=instance)
        for bank in banks:
            await question_bank_service.recompute_and_persist_stale(db, bank)
    return new_snapshot
```

In `backend/nexus/app/modules/pipelines/service.py::update_job_pipeline_stages`, after applying stage changes:

```python
for stage in stages_with_changed_signal_filter_or_difficulty:
    bank = await question_bank_service.get_bank_for_stage(db, stage=stage)
    if bank:
        await question_bank_service.recompute_and_persist_stale(
            db, bank,
            current_stage_config={
                "signal_filter": stage.signal_filter,
                "difficulty": stage.difficulty,
            },
        )
```

In `backend/nexus/app/modules/question_bank/actors.py::generate_question_bank_stage`, when the actor finishes generating, **also stamp the snapshot fields**:

```python
bank.pipeline_version_at_generation = instance.pipeline_version
bank.stage_config_snapshot = {
    "signal_filter": stage.signal_filter,
    "difficulty": stage.difficulty,
}
bank.is_stale = False
bank.status = "generated"
```

- [ ] **Step 6: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_question_banks_stale_persisted.py tests/test_question_banks_service.py tests/test_question_banks_actors.py tests/test_question_banks_router.py -v
```

Expected: all green. Existing tests that mock `compute_is_stale` may need to set `bank.is_stale` directly instead.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py backend/nexus/app/modules/question_bank/router.py backend/nexus/app/modules/question_bank/actors.py backend/nexus/app/modules/jd/service.py backend/nexus/app/modules/pipelines/service.py backend/nexus/tests/test_question_banks_stale_persisted.py
git commit -m "feat(question_bank): persist is_stale; recompute on write paths; stale drops confirmation"
```

---

## Batch 6 — Activation gate + pause/unpause + candidates integration

Outcome: `POST /jobs/{id}/activate` enforces the checklist; pause/unpause endpoints work; `candidates/service.py::create_assignment` rejects on non-active jobs and stamps `entered_at_pipeline_version`; `transition_stage` skips paused stages.

### Task 11: Pause / unpause endpoints

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/router.py` — add pause/unpause endpoints.
- Modify: `backend/nexus/app/modules/pipelines/service.py` — add `pause_stage` / `unpause_stage`.
- Test: `backend/nexus/tests/test_pipelines_pause.py` (new).

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_pipelines_pause.py`:

```python
"""Stage pause/unpause endpoint tests."""
import pytest


@pytest.mark.asyncio
async def test_pause_intake_returns_409(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    stages = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    intake = next(s for s in stages if s["stage_type"] == "intake")
    r = await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{intake['id']}/pause")
    assert r.status_code == 409
    assert "stage_pause_forbidden" in r.json()["detail"].get("code", "")


@pytest.mark.asyncio
async def test_pause_debrief_returns_409(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    stages = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    debrief = next(s for s in stages if s["stage_type"] == "debrief")
    r = await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{debrief['id']}/pause")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_pause_phone_screen_succeeds(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    stages = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    middle = next(s for s in stages if s["stage_type"] == "phone_screen")
    r = await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause")
    assert r.status_code == 200
    refreshed = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    paused = next(s for s in refreshed if s["id"] == middle["id"])
    assert paused["paused_at"] is not None


@pytest.mark.asyncio
async def test_unpause_clears_paused_at(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    stages = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    middle = next(s for s in stages if s["stage_type"] == "phone_screen")
    await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause")
    r = await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/unpause")
    assert r.status_code == 200
    refreshed = (await auth_client.get(f"/api/jobs/{job.id}/pipeline")).json()["stages"]
    revived = next(s for s in refreshed if s["id"] == middle["id"])
    assert revived["paused_at"] is None


@pytest.mark.asyncio
async def test_pause_bumps_pipeline_version(auth_client, _job_with_pipeline):
    job = _job_with_pipeline
    r0 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    v0 = r0.json()["pipeline_version"]
    middle = next(s for s in r0.json()["stages"] if s["stage_type"] == "phone_screen")
    await auth_client.post(f"/api/jobs/{job.id}/pipeline/stages/{middle['id']}/pause")
    r1 = await auth_client.get(f"/api/jobs/{job.id}/pipeline")
    assert r1.json()["pipeline_version"] == v0 + 1
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_pause.py -v
```

Expected: FAIL — endpoint doesn't exist (404).

- [ ] **Step 3: Implement service helpers**

In `backend/nexus/app/modules/pipelines/service.py`:

```python
from datetime import datetime, timezone

_UNPAUSABLE_TYPES = {"intake", "debrief"}


async def pause_stage(
    db: AsyncSession, *, instance: JobPipelineInstance, stage: JobPipelineStage,
) -> JobPipelineStage:
    if stage.stage_type in _UNPAUSABLE_TYPES:
        raise StagePauseForbiddenError(stage.stage_type)
    if stage.paused_at is not None:
        return stage  # idempotent
    stage.paused_at = datetime.now(timezone.utc)
    await db.flush()
    await bump_pipeline_version(db, instance)
    return stage


async def unpause_stage(
    db: AsyncSession, *, instance: JobPipelineInstance, stage: JobPipelineStage,
) -> JobPipelineStage:
    if stage.paused_at is None:
        return stage
    stage.paused_at = None
    await db.flush()
    await bump_pipeline_version(db, instance)
    return stage
```

Add the error class to `backend/nexus/app/modules/pipelines/errors.py`:

```python
class StagePauseForbiddenError(Exception):
    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"Cannot pause stage of type {stage_type}")
```

- [ ] **Step 4: Add the router endpoints**

In `backend/nexus/app/modules/pipelines/router.py`:

```python
from app.modules.pipelines.errors import StagePauseForbiddenError

@router.post("/api/jobs/{job_id}/pipeline/stages/{stage_id}/pause")
async def pause_stage_endpoint(
    job_id: UUID, stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await service.get_stage_in_instance(db, job=job, stage_id=stage_id)
    try:
        await service.pause_stage(db, instance=instance, stage=stage)
    except StagePauseForbiddenError as e:
        raise HTTPException(409, detail={
            "code": "stage_pause_forbidden_for_endpoint_type",
            "message": f"Cannot pause stage of type '{e.stage_type}' (intake/debrief are structural).",
        })
    return await service.instance_response(db, instance)


@router.post("/api/jobs/{job_id}/pipeline/stages/{stage_id}/unpause")
async def unpause_stage_endpoint(
    job_id: UUID, stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await service.get_stage_in_instance(db, job=job, stage_id=stage_id)
    await service.unpause_stage(db, instance=instance, stage=stage)
    return await service.instance_response(db, instance)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_pause.py -v
```

Expected: all 5 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/pipelines/router.py backend/nexus/app/modules/pipelines/service.py backend/nexus/app/modules/pipelines/errors.py backend/nexus/tests/test_pipelines_pause.py
git commit -m "feat(pipelines): pause/unpause endpoints + 409 on intake/debrief"
```

---

### Task 12: Activation gate endpoint

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py` — add `POST /api/jobs/{id}/activate`.
- Modify: `backend/nexus/app/modules/jd/service.py` — add `evaluate_activation_predicates` + `activate_job`.
- Test: `backend/nexus/tests/test_pipelines_activate.py` (new).

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/test_pipelines_activate.py`:

```python
"""Activation gate predicate tests + endpoint tests — spec §7."""
import pytest


@pytest.mark.asyncio
async def test_cannot_activate_from_signals_confirmed(auth_client, _job_in_signals_confirmed):
    job, _ = _job_in_signals_confirmed
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 422
    assert "predicates_failed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_activate_fails_when_human_led_has_no_interviewer(auth_client, _pipeline_built_no_participants):
    job = _pipeline_built_no_participants
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 422
    failures = r.json()["detail"]["predicates_failed"]
    assert any("interviewer" in f.lower() for f in failures)


@pytest.mark.asyncio
async def test_activate_fails_when_debrief_has_no_reviewer(auth_client, _pipeline_built_no_reviewer):
    job = _pipeline_built_no_reviewer
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 422
    failures = r.json()["detail"]["predicates_failed"]
    assert any("reviewer" in f.lower() for f in failures)


@pytest.mark.asyncio
async def test_activate_fails_when_no_middle_stage(auth_client, _pipeline_built_intake_debrief_only):
    job = _pipeline_built_intake_debrief_only
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 422
    failures = r.json()["detail"]["predicates_failed"]
    assert any("screening stage" in f.lower() for f in failures)


@pytest.mark.asyncio
async def test_activate_fails_when_bank_missing_for_eligible_stage(auth_client, _pipeline_built_no_banks):
    job = _pipeline_built_no_banks
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 422
    failures = r.json()["detail"]["predicates_failed"]
    assert any("question bank" in f.lower() for f in failures)


@pytest.mark.asyncio
async def test_activate_succeeds_when_checklist_passes(auth_client, _pipeline_built_ready):
    job = _pipeline_built_ready
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 200
    job_resp = await auth_client.get(f"/api/jd/{job.id}")
    assert job_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_already_active_returns_409(auth_client, _active_job_with_pipeline):
    job = _active_job_with_pipeline
    r = await auth_client.post(f"/api/jobs/{job.id}/activate")
    assert r.status_code == 409
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_activate.py -v
```

Expected: 404 — endpoint doesn't exist.

- [ ] **Step 3: Implement the predicate evaluator**

In `backend/nexus/app/modules/jd/service.py`, add:

```python
from app.modules.pipelines.categories import (
    bank_eligible_stage_types, middle_stage_types_for_activation,
)  # New helper module — see Step 5
from app.models import (
    JobPipelineInstance, JobPipelineStage, PipelineStageParticipant, QuestionBank,
)


@dataclass
class ActivationPredicateFailure:
    code: str
    message: str
    stage_id: UUID | None = None


async def evaluate_activation_predicates(
    db: AsyncSession, *, job: JobPosting,
) -> list[ActivationPredicateFailure]:
    failures: list[ActivationPredicateFailure] = []
    instance = await pipelines_service.get_job_pipeline_instance(db, job=job)
    if instance is None:
        return [ActivationPredicateFailure("no_pipeline", "Pipeline not yet built")]

    stages = await pipelines_service.list_stages(db, instance=instance)
    middle_types = middle_stage_types_for_activation()
    middle_stages = [s for s in stages if s.stage_type in middle_types]
    if not middle_stages:
        failures.append(ActivationPredicateFailure(
            "no_middle_stage",
            "Add at least one screening stage between Intake and Debrief.",
        ))

    # Per-stage participant + bank checks
    participants_by_stage = await pipelines_service.list_participants_by_stage(db, instance=instance)
    banks_by_stage = await question_bank_service.list_banks_by_stage(db, instance=instance)
    bank_types = bank_eligible_stage_types()

    for s in stages:
        if s.stage_type in {"phone_screen", "human_interview"}:
            interviewers = [p for p in participants_by_stage.get(s.id, []) if p.role == "interviewer"]
            if not interviewers:
                failures.append(ActivationPredicateFailure(
                    "missing_interviewer",
                    f"Assign an interviewer to '{s.name}'.",
                    stage_id=s.id,
                ))
        if s.stage_type == "debrief":
            reviewers = [p for p in participants_by_stage.get(s.id, []) if p.role == "reviewer"]
            if not reviewers:
                failures.append(ActivationPredicateFailure(
                    "missing_reviewer",
                    f"Assign a reviewer to '{s.name}'.",
                    stage_id=s.id,
                ))
        if s.stage_type in bank_types:
            bank = banks_by_stage.get(s.id)
            if bank is None or bank.status not in ("generated", "confirmed"):
                failures.append(ActivationPredicateFailure(
                    "missing_bank",
                    f"Generate a question bank for '{s.name}'.",
                    stage_id=s.id,
                ))
        if not s.name.strip():
            failures.append(ActivationPredicateFailure(
                "empty_stage_name",
                f"Stage at position {s.position} has no name.",
                stage_id=s.id,
            ))

    return failures


async def activate_job(
    db: AsyncSession, *, job: JobPosting, actor_id: UUID, correlation_id: str,
) -> JobPosting:
    if job.status == "active":
        raise IllegalTransitionError(from_state=job.status, to_state="active")
    failures = await evaluate_activation_predicates(db, job=job)
    if failures:
        raise ActivationPredicatesFailed(failures)
    await transition(db, job=job, to_state="active", actor_id=actor_id, correlation_id=correlation_id)
    return job
```

Add `ActivationPredicatesFailed` to `app/modules/jd/errors.py`:

```python
class ActivationPredicatesFailed(Exception):
    def __init__(self, failures: list) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} predicate(s) failed")
```

- [ ] **Step 4: Add the activate endpoint to the JD router**

In `backend/nexus/app/modules/jd/router.py`:

```python
from app.modules.jd.errors import ActivationPredicatesFailed

@router.post("/api/jobs/{job_id}/activate")
async def activate_job_endpoint(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
    correlation_id: str = Depends(require_correlation_id),
):
    job = await require_job_access(db, job_id, user, "manage")
    try:
        await service.activate_job(db, job=job, actor_id=user.user_id, correlation_id=correlation_id)
    except IllegalTransitionError:
        raise HTTPException(409, detail={"code": "job_already_active_or_invalid_transition"})
    except ActivationPredicatesFailed as e:
        raise HTTPException(422, detail={
            "code": "activation_predicates_failed",
            "predicates_failed": [
                {"code": f.code, "message": f.message,
                 "stage_id": str(f.stage_id) if f.stage_id else None}
                for f in e.failures
            ],
        })
    return {"status": "active", "job_id": str(job.id)}
```

- [ ] **Step 5: Add the helper module `pipelines/categories.py`**

Create `backend/nexus/app/modules/pipelines/categories.py`:

```python
"""Server-side mirror of the spec §6 capability matrix."""

def bank_eligible_stage_types() -> set[str]:
    return {"phone_screen", "ai_screening", "human_interview", "take_home"}


def middle_stage_types_for_activation() -> set[str]:
    """Per spec §7.1 predicate #2 — take_home is excluded (disabled)."""
    return {"phone_screen", "ai_screening", "human_interview"}


def is_paused(stage) -> bool:
    return getattr(stage, "paused_at", None) is not None
```

- [ ] **Step 6: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_activate.py -v
```

Expected: all 7 pass. (Fixtures may need to be authored in `tests/conftest.py` or the test file itself — pattern-match existing helpers.)

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/jd/router.py backend/nexus/app/modules/jd/service.py backend/nexus/app/modules/jd/errors.py backend/nexus/app/modules/pipelines/categories.py backend/nexus/tests/test_pipelines_activate.py
git commit -m "feat(jd): activation gate endpoint with predicate evaluator"
```

---

### Task 13: Candidates module — `active`-state gate + version stamp + paused-skip

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py:267` (`create_assignment`) and `:391` (`transition_stage`).
- Modify: `backend/nexus/tests/test_candidates_router.py` — add active-state gate tests.
- Modify: `backend/nexus/tests/test_candidates_stage_transitions.py` — add paused-stage skip test.

- [ ] **Step 1: Write failing tests**

Append to `backend/nexus/tests/test_candidates_router.py`:

```python
@pytest.mark.asyncio
async def test_create_assignment_rejects_when_job_not_active(auth_client, _candidate, _pipeline_built_job):
    """Job must be 'active' to accept new candidate assignments — spec §7.3."""
    job = _pipeline_built_job  # status = pipeline_built, not active
    r = await auth_client.post(
        f"/api/candidates/{_candidate.id}/assignments",
        json={"job_posting_id": str(job.id)},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "job_not_active"


@pytest.mark.asyncio
async def test_create_assignment_stamps_pipeline_version(auth_client, _candidate, _active_job_with_pipeline):
    job = _active_job_with_pipeline  # active, instance with pipeline_version >= 1
    r = await auth_client.post(
        f"/api/candidates/{_candidate.id}/assignments",
        json={"job_posting_id": str(job.id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entered_at_pipeline_version"] is not None
    assert body["entered_at_pipeline_version"] >= 1
```

Append to `backend/nexus/tests/test_candidates_stage_transitions.py`:

```python
@pytest.mark.asyncio
async def test_transition_skips_paused_stage(test_db_session, _active_job_with_paused_middle):
    """transition_stage must skip stages where paused_at is not None — spec §5.4 resolver."""
    assignment, paused_stage_id, expected_next_stage_id = _active_job_with_paused_middle
    # ... call transition_stage(advance) ...
    # Assertion: assignment.current_stage_id == expected_next_stage_id (skipping paused_stage_id)
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm nexus pytest tests/test_candidates_router.py::test_create_assignment_rejects_when_job_not_active -v
docker compose run --rm nexus pytest tests/test_candidates_router.py::test_create_assignment_stamps_pipeline_version -v
docker compose run --rm nexus pytest tests/test_candidates_stage_transitions.py::test_transition_skips_paused_stage -v
```

Expected: failures.

- [ ] **Step 3: Update `create_assignment` in `candidates/service.py:267`**

Find `create_assignment`. Add at the start (after authz/lookup):

```python
from app.modules.candidates.errors import JobNotActiveError

async def create_assignment(
    db: AsyncSession, candidate_id: UUID, body: AssignmentCreateRequest, user: UserContext,
) -> CandidateJobAssignment:
    job = await db.get(JobPosting, body.job_posting_id)
    if job is None:
        raise JobNotFoundError(body.job_posting_id)
    if job.status != "active":
        raise JobNotActiveError(job.status)
    instance = await pipelines_service.get_job_pipeline_instance(db, job=job)
    if instance is None:
        raise PipelineNotFoundError(job.id)
    # ... existing logic to determine current_stage_id (intake at position 0) ...
    assignment = CandidateJobAssignment(
        tenant_id=job.tenant_id,
        candidate_id=candidate_id,
        job_posting_id=job.id,
        current_stage_id=intake_stage.id,
        entered_at_pipeline_version=instance.pipeline_version,  # NEW
        assigned_by=user.user_id,
        # ... existing fields ...
    )
    db.add(assignment)
    await db.flush()
    return assignment
```

Add to `app/modules/candidates/errors.py`:

```python
class JobNotActiveError(Exception):
    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__(f"Job is in '{current_status}' state; activation required")
```

Map to 409 in the candidates router (or in the global exception handler — pattern-match existing errors).

- [ ] **Step 4: Update `transition_stage` to skip paused stages**

In `app/modules/candidates/service.py:391` (`transition_stage`), find the "next stage" resolution. Update to skip stages with `paused_at IS NOT NULL`:

```python
async def _next_active_stage(
    db: AsyncSession, *, instance: JobPipelineInstance, current_position: int, completed_stage_ids: set[UUID],
) -> JobPipelineStage | None:
    q = (
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .where(JobPipelineStage.position > current_position)
        .where(JobPipelineStage.paused_at.is_(None))  # NEW: skip paused
        .order_by(JobPipelineStage.position.asc())
    )
    rows = (await db.execute(q)).scalars().all()
    for s in rows:
        if s.id not in completed_stage_ids:
            return s
    return None
```

Replace the existing resolver call with this helper. (The exact form depends on the current implementation — pattern-match.)

- [ ] **Step 5: Update the assignment response schema**

Ensure `AssignmentResponse` in `app/modules/candidates/schemas.py` includes `entered_at_pipeline_version: int | None`.

- [ ] **Step 6: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_candidates_router.py tests/test_candidates_stage_transitions.py -v
```

Expected: all green (including pre-existing tests).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/app/modules/candidates/errors.py backend/nexus/app/modules/candidates/schemas.py backend/nexus/tests/test_candidates_router.py backend/nexus/tests/test_candidates_stage_transitions.py
git commit -m "feat(candidates): active-state gate + entered_at_pipeline_version + paused-stage skip in transition"
```

---

## Batch 7 — LLM Refine + Add for question editing

Outcome: stateless preview endpoints `POST /questions/{q_id}/refine` and `POST /questions/draft` return LLM proposals with full pipeline context; new prompt files; `build_question_context` factored out as a shared loader.

### Task 14: New prompt files

**Files:**
- Create: `backend/nexus/prompts/v1/question_refine_single.txt`
- Create: `backend/nexus/prompts/v1/question_create_single.txt`

- [ ] **Step 1: Write `question_refine_single.txt`**

```text
You are refining a single interview question for a structured interview pipeline. The recruiter has provided an instruction; rewrite the question to satisfy that instruction while preserving alignment with the JD signals, the stage's purpose, and the rest of the bank.

# JD signals (current snapshot)
{signals_json}

# This stage
- name: {stage_name}
- type: {stage_type}
- difficulty: {stage_difficulty}
- duration_minutes: {stage_duration_minutes}
- signal_filter.include_types: {signal_filter_types}
- pass_criteria: {pass_criteria_json}

# This stage's existing bank (do NOT duplicate; you may reference them as siblings)
{existing_bank_json}

# Prior stages' banks (do NOT duplicate)
{prior_banks_json}

# The question being refined
- text: "{question_text}"
- signal_probed: "{question_signal_probed}"
- mandatory: {question_mandatory}

# Recruiter instruction
{instruction}

# Output
Return JSON of shape:
{{
  "proposed_text": "...",
  "proposed_signal_probed": "...",
  "proposed_mandatory": true/false,
  "rationale": "one-sentence explanation"
}}
```

- [ ] **Step 2: Write `question_create_single.txt`**

```text
You are drafting a single new interview question for a structured interview pipeline. The recruiter has described what they want; produce a question that fits the stage's purpose, aligns with the JD signals, and does not duplicate questions already in this or prior stages' banks.

# JD signals (current snapshot)
{signals_json}

# This stage
- name: {stage_name}
- type: {stage_type}
- difficulty: {stage_difficulty}
- duration_minutes: {stage_duration_minutes}
- signal_filter.include_types: {signal_filter_types}
- pass_criteria: {pass_criteria_json}

# This stage's existing bank (do NOT duplicate)
{existing_bank_json}

# Prior stages' banks (do NOT duplicate)
{prior_banks_json}

# Recruiter instruction
{instruction}

# Output
Return JSON of shape:
{{
  "proposed_text": "...",
  "proposed_signal_probed": "<must be one of signal_filter.include_types>",
  "proposed_mandatory": true/false,
  "proposed_position": <integer; append at end of stage by default>,
  "rationale": "one-sentence explanation"
}}
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/prompts/v1/question_refine_single.txt backend/nexus/prompts/v1/question_create_single.txt
git commit -m "feat(prompts): add question_refine_single + question_create_single"
```

---

### Task 15: Refactor `build_question_context` as a shared loader

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` — extract context-building.
- Create: `backend/nexus/app/modules/question_bank/context.py` — shared `build_question_context`.

- [ ] **Step 1: Identify the existing context-building code**

In `app/modules/question_bank/actors.py`, find the prelude inside `generate_question_bank_stage` that loads JD signals, stage config, prior-stages questions, and the existing bank. Extract it into a free function.

- [ ] **Step 2: Create `context.py`**

```python
"""Shared loader for question-generation prompt context.

Used by:
  - question_bank.actors.generate_question_bank_stage (full-bank generation)
  - question_bank.refine.refine_single_question (Refine endpoint)
  - question_bank.refine.draft_single_question (Add endpoint)
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance, JobPipelineStage, JobPosting, JobPostingSignalSnapshot,
    QuestionBank,
)


@dataclass
class QuestionContext:
    signals_json: str
    stage_name: str
    stage_type: str
    stage_difficulty: str
    stage_duration_minutes: int
    signal_filter_types: list[str]
    pass_criteria_json: str
    existing_bank_json: str
    prior_banks_json: str


async def build_question_context(
    db: AsyncSession, *, job: JobPosting, instance: JobPipelineInstance, stage: JobPipelineStage,
) -> QuestionContext:
    # 1. Latest confirmed signal snapshot
    snapshot = await _latest_confirmed_snapshot(db, job)
    # 2. Existing bank for this stage (questions inline)
    existing = await _bank_with_questions_for_stage(db, stage)
    # 3. Prior stages' banks
    prior = await _prior_banks_for_stage(db, instance, stage)
    return QuestionContext(
        signals_json=_json(snapshot.signals if snapshot else []),
        stage_name=stage.name,
        stage_type=stage.stage_type,
        stage_difficulty=stage.difficulty,
        stage_duration_minutes=stage.duration_minutes,
        signal_filter_types=(stage.signal_filter or {}).get("include_types", []),
        pass_criteria_json=_json(stage.pass_criteria or {}),
        existing_bank_json=_json(existing),
        prior_banks_json=_json(prior),
    )


# Helper implementations (private):
async def _latest_confirmed_snapshot(db, job): ...
async def _bank_with_questions_for_stage(db, stage): ...
async def _prior_banks_for_stage(db, instance, stage): ...
def _json(obj) -> str: ...
```

(Port the existing logic from `actors.py` into `_latest_confirmed_snapshot`, `_bank_with_questions_for_stage`, `_prior_banks_for_stage`. The bodies should be straight copy-paste from the actor's prelude.)

- [ ] **Step 3: Update the actor to use the shared loader**

In `app/modules/question_bank/actors.py::generate_question_bank_stage`, replace the inline context-building with:

```python
from app.modules.question_bank.context import build_question_context

context = await build_question_context(db, job=job, instance=instance, stage=stage)
# ... rest of actor logic uses context.signals_json, context.stage_name, etc.
```

- [ ] **Step 4: Run question-bank tests**

```bash
docker compose run --rm nexus pytest tests/test_question_banks_actors.py tests/test_question_banks_service.py -v
```

Expected: all green (refactor preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/context.py backend/nexus/app/modules/question_bank/actors.py
git commit -m "refactor(question_bank): extract build_question_context as shared loader"
```

---

### Task 16: Refine + Draft endpoints

**Files:**
- Create: `backend/nexus/app/modules/question_bank/refine.py` (router + service for refine/draft).
- Modify: `backend/nexus/app/modules/question_bank/router.py` — register the new router.
- Test: `backend/nexus/tests/test_question_banks_refine.py` (new), `tests/test_question_banks_draft.py` (new).

- [ ] **Step 1: Write failing tests for refine**

Create `backend/nexus/tests/test_question_banks_refine.py`:

```python
"""LLM-mediated refine endpoint — stateless preview."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_refine_returns_proposal_without_persisting(auth_client, _job_with_generated_bank):
    job, stage_id = _job_with_generated_bank
    bank = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    qid = bank["questions"][0]["id"]
    original_text = bank["questions"][0]["text"]

    fake_response = {
        "proposed_text": "Refined version of the question.",
        "proposed_signal_probed": "competency:python",
        "proposed_mandatory": True,
        "rationale": "User asked to make this stricter.",
    }
    with patch("app.modules.question_bank.refine._call_llm", return_value=fake_response):
        r = await auth_client.post(
            f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/{qid}/refine",
            json={"instruction": "Make this stricter."},
        )
    assert r.status_code == 200
    assert r.json()["proposed_text"] == "Refined version of the question."
    # Verify persisted state unchanged
    bank2 = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    q_after = next(q for q in bank2["questions"] if q["id"] == qid)
    assert q_after["text"] == original_text


@pytest.mark.asyncio
async def test_refine_then_accept_via_patch(auth_client, _job_with_generated_bank):
    job, stage_id = _job_with_generated_bank
    bank = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    qid = bank["questions"][0]["id"]
    fake_response = {
        "proposed_text": "Refined.", "proposed_signal_probed": "competency",
        "proposed_mandatory": True, "rationale": "x",
    }
    with patch("app.modules.question_bank.refine._call_llm", return_value=fake_response):
        await auth_client.post(
            f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/{qid}/refine",
            json={"instruction": "..."},
        )
    # Recruiter clicks Accept → frontend submits PATCH with proposed values
    r = await auth_client.patch(
        f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/{qid}",
        json={"text": "Refined.", "signal_probed": "competency", "mandatory": True},
    )
    assert r.status_code == 200
    bank2 = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    q_after = next(q for q in bank2["questions"] if q["id"] == qid)
    assert q_after["text"] == "Refined."
```

- [ ] **Step 2: Write failing tests for draft**

Create `backend/nexus/tests/test_question_banks_draft.py`:

```python
"""LLM-mediated draft endpoint — stateless preview for adding a new question."""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_draft_returns_proposal_without_persisting(auth_client, _job_with_generated_bank):
    job, stage_id = _job_with_generated_bank
    bank0 = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    initial_count = len(bank0["questions"])

    fake = {
        "proposed_text": "New question about deadlines.",
        "proposed_signal_probed": "behavioral:resilience",
        "proposed_mandatory": False,
        "proposed_position": initial_count,
        "rationale": "x",
    }
    with patch("app.modules.question_bank.refine._call_llm", return_value=fake):
        r = await auth_client.post(
            f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/draft",
            json={"instruction": "Add a behavioral question about deadline pressure."},
        )
    assert r.status_code == 200
    assert "deadlines" in r.json()["proposed_text"].lower()
    # Bank unchanged.
    bank1 = (await auth_client.get(f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions")).json()
    assert len(bank1["questions"]) == initial_count
```

- [ ] **Step 3: Implement `refine.py`**

Create `backend/nexus/app/modules/question_bank/refine.py`:

```python
"""Stateless LLM-mediated Refine + Draft endpoints (sync HTTP, no DB writes)."""
from __future__ import annotations

import json as _json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.database import get_tenant_db
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.authz import require_job_access
from app.modules.question_bank import service as bank_service
from app.modules.question_bank.context import build_question_context

router = APIRouter()


# --- Request/response schemas -------------------------------------------------

class RefineRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class RefineResponse(BaseModel):
    proposed_text: str
    proposed_signal_probed: str
    proposed_mandatory: bool
    rationale: str = ""


class DraftRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class DraftResponse(BaseModel):
    proposed_text: str
    proposed_signal_probed: str
    proposed_mandatory: bool
    proposed_position: int
    rationale: str = ""


# --- LLM call helper (mockable in tests) -------------------------------------

async def _call_llm(prompt: str, response_schema: type[BaseModel]) -> dict[str, Any]:
    client = get_openai_client()
    completion = await client.chat.completions.create(
        model=ai_config.model_for("question_refine"),
        messages=[{"role": "user", "content": prompt}],
        response_format=response_schema,
    )
    return completion.choices[0].message.parsed.model_dump()


# --- Endpoints ----------------------------------------------------------------

@router.post(
    "/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/refine",
    response_model=RefineResponse,
)
async def refine_question(
    job_id: UUID, stage_id: UUID, question_id: UUID,
    body: RefineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> RefineResponse:
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await _resolve_instance_and_stage(db, job, stage_id)
    question = await bank_service.get_question(db, stage_id=stage.id, question_id=question_id)
    if question is None:
        raise HTTPException(404, detail="Question not found")
    context = await build_question_context(db, job=job, instance=instance, stage=stage)
    template = prompt_loader.load("question_refine_single")
    prompt = template.format(
        signals_json=context.signals_json,
        stage_name=context.stage_name,
        stage_type=context.stage_type,
        stage_difficulty=context.stage_difficulty,
        stage_duration_minutes=context.stage_duration_minutes,
        signal_filter_types=context.signal_filter_types,
        pass_criteria_json=context.pass_criteria_json,
        existing_bank_json=context.existing_bank_json,
        prior_banks_json=context.prior_banks_json,
        question_text=question.text,
        question_signal_probed=question.signal_probed or "",
        question_mandatory=question.mandatory,
        instruction=body.instruction,
    )
    result = await _call_llm(prompt, RefineResponse)
    return RefineResponse(**result)


@router.post(
    "/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/draft",
    response_model=DraftResponse,
)
async def draft_question(
    job_id: UUID, stage_id: UUID,
    body: DraftRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> DraftResponse:
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await _resolve_instance_and_stage(db, job, stage_id)
    context = await build_question_context(db, job=job, instance=instance, stage=stage)
    template = prompt_loader.load("question_create_single")
    prompt = template.format(
        signals_json=context.signals_json,
        stage_name=context.stage_name,
        stage_type=context.stage_type,
        stage_difficulty=context.stage_difficulty,
        stage_duration_minutes=context.stage_duration_minutes,
        signal_filter_types=context.signal_filter_types,
        pass_criteria_json=context.pass_criteria_json,
        existing_bank_json=context.existing_bank_json,
        prior_banks_json=context.prior_banks_json,
        instruction=body.instruction,
    )
    result = await _call_llm(prompt, DraftResponse)
    return DraftResponse(**result)


async def _resolve_instance_and_stage(db, job, stage_id):
    """Reuse existing helper from pipelines.service."""
    from app.modules.pipelines import service as pipelines_service
    instance, stage = await pipelines_service.get_stage_in_instance(db, job=job, stage_id=stage_id)
    return instance, stage
```

- [ ] **Step 4: Register the new router**

In `backend/nexus/app/main.py` (or wherever the existing question_bank router is registered), add:

```python
from app.modules.question_bank.refine import router as question_bank_refine_router
app.include_router(question_bank_refine_router)
```

- [ ] **Step 5: Add `model_for("question_refine")` to AIConfig**

In `backend/nexus/app/ai/config.py`, add `question_refine` to the task → model map (use the same model as the bank generator, or a faster/cheaper variant — env-driven). Pattern-match existing entries.

- [ ] **Step 6: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_question_banks_refine.py tests/test_question_banks_draft.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/question_bank/refine.py backend/nexus/app/main.py backend/nexus/app/ai/config.py backend/nexus/tests/test_question_banks_refine.py backend/nexus/tests/test_question_banks_draft.py
git commit -m "feat(question_bank): refine + draft endpoints (stateless LLM preview)"
```

---

That completes the backend. Frontend batches follow.

## Batch 8 — Frontend types + API namespaces + hooks

Outcome: TypeScript discriminated `PipelineStageInput`, `lib/api/questions.ts`, four new hooks. UI components are unchanged in this batch.

### Task 17: Discriminated `PipelineStageInput` in `lib/api/pipelines.ts`

**Files:**
- Modify: `frontend/app/lib/api/pipelines.ts`.
- Test: `frontend/app/lib/api/pipelines.test.ts` (new).

- [ ] **Step 1: Write a failing compile-time test**

Create `frontend/app/lib/api/pipelines.test.ts`:

```typescript
import { describe, it, expectTypeOf } from 'vitest'
import type { PipelineStageInput } from './pipelines'

describe('PipelineStageInput discriminated union', () => {
  it('intake variant rejects difficulty at compile time', () => {
    // @ts-expect-error — difficulty is not allowed on intake
    const _bad: PipelineStageInput = {
      position: 0, name: 'Intake', stage_type: 'intake', difficulty: 'medium',
    }
  })

  it('phone_screen variant requires difficulty + duration', () => {
    const ok: PipelineStageInput = {
      position: 1, name: 'Phone Screen', stage_type: 'phone_screen',
      duration_minutes: 30, difficulty: 'medium',
      signal_filter: { include_types: ['competency'] },
      pass_criteria: { type: 'all_knockouts_pass' },
      advance_behavior: 'auto_advance',
    }
    expectTypeOf(ok).toEqualTypeOf<PipelineStageInput>()
  })

  it('debrief variant rejects signal_filter at compile time', () => {
    // @ts-expect-error
    const _bad: PipelineStageInput = {
      position: 4, name: 'Debrief', stage_type: 'debrief',
      signal_filter: { include_types: ['competency'] },
    }
  })
})
```

- [ ] **Step 2: Run vitest with type checking**

```bash
cd frontend/app && npm run test -- lib/api/pipelines.test.ts
```

Expected: failures (current type accepts everything).

- [ ] **Step 3: Refactor `PipelineStageInput` to discriminated union**

In `frontend/app/lib/api/pipelines.ts`, replace the existing `PipelineStageInput` definition:

```typescript
type StageBase = {
  position: number
  name: string
  sla_days?: number | null
  participants?: StageParticipantInput[]
}

type IntakeStage = StageBase & {
  stage_type: 'intake'
  // No duration, difficulty, signal_filter, pass_criteria, advance_behavior, otp_required
}

type DebriefStage = StageBase & {
  stage_type: 'debrief'
  // Same as intake — IO stage
}

type ScreeningStageBase = StageBase & {
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
  otp_required?: boolean
}

type PhoneScreenStage    = ScreeningStageBase & { stage_type: 'phone_screen' }
type AiScreeningStage    = ScreeningStageBase & { stage_type: 'ai_screening' }
type HumanInterviewStage = ScreeningStageBase & { stage_type: 'human_interview' }
type TakeHomeStage       = StageBase & { stage_type: 'take_home' }  // disabled — no fields configurable

export type PipelineStageInput =
  | IntakeStage
  | PhoneScreenStage
  | AiScreeningStage
  | HumanInterviewStage
  | DebriefStage
  | TakeHomeStage
```

Add the picker/preview/activation types:

```typescript
export type PipelineSourceTemplate = { source: 'template'; template_id: string }
export type PipelineSourceStarter  = { source: 'starter'; starter_key:
    'standard_technical' | 'fast_track' | 'screening_only' | 'senior_leadership' }
export type PipelineSourceScratch  = { source: 'scratch' }
export type PipelineCreateRequest =
  | PipelineSourceTemplate | PipelineSourceStarter | PipelineSourceScratch

export type EditCategory = 'A' | 'B' | 'C' | 'D'
export type PreviewChangesResponse = {
  category: EditCategory
  warnings: string[]
  in_flight: Record<string, number>
}

export type ActivationPredicateFailure = {
  code: string
  message: string
  stage_id: string | null
}
export type ActivationFailedResponse = {
  code: 'activation_predicates_failed'
  predicates_failed: ActivationPredicateFailure[]
}
```

Add `pipelinesApi.create(token, jobId, body)`, `pipelinesApi.previewChanges`, `pipelinesApi.activate`, `pipelinesApi.pauseStage`, `pipelinesApi.unpauseStage` methods following the existing namespace pattern.

- [ ] **Step 4: Run tests + type-check**

```bash
npm run test -- lib/api/pipelines.test.ts
npm run type-check
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/api/pipelines.ts frontend/app/lib/api/pipelines.test.ts
git commit -m "feat(api): discriminated PipelineStageInput + picker/activation/preview-changes types"
```

---

### Task 18: `lib/api/questions.ts` namespace + four hooks

**Files:**
- Create: `frontend/app/lib/api/questions.ts`.
- Create: `frontend/app/lib/hooks/use-pipeline-classify.ts`.
- Create: `frontend/app/lib/hooks/use-activate-job.ts`.
- Create: `frontend/app/lib/hooks/use-refine-question.ts`.
- Create: `frontend/app/lib/hooks/use-draft-question.ts`.

- [ ] **Step 1: `lib/api/questions.ts`**

```typescript
import { apiFetch } from './client'

export type RefineRequest = { instruction: string }
export type RefineResponse = {
  proposed_text: string
  proposed_signal_probed: string
  proposed_mandatory: boolean
  rationale?: string
}
export type DraftRequest = { instruction: string }
export type DraftResponse = RefineResponse & { proposed_position: number }

export const questionsApi = {
  refine: (token: string, jobId: string, stageId: string, qid: string, body: RefineRequest) =>
    apiFetch<RefineResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}/refine`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),
  draft: (token: string, jobId: string, stageId: string, body: DraftRequest) =>
    apiFetch<DraftResponse>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/draft`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),
  acceptRefine: (token: string, jobId: string, stageId: string, qid: string,
                 body: { text: string; signal_probed: string; mandatory: boolean }) =>
    apiFetch<void>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'PATCH', body: JSON.stringify(body), token },
    ),
  acceptDraft: (token: string, jobId: string, stageId: string,
                body: { text: string; signal_probed: string; mandatory: boolean; position: number }) =>
    apiFetch<{ id: string }>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions`,
      { method: 'POST', body: JSON.stringify(body), token },
    ),
  toggleMandatory: (token: string, jobId: string, stageId: string, qid: string, mandatory: boolean) =>
    apiFetch<void>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'PATCH', body: JSON.stringify({ mandatory }), token },
    ),
  remove: (token: string, jobId: string, stageId: string, qid: string) =>
    apiFetch<void>(
      `/api/jobs/${jobId}/pipeline/stages/${stageId}/questions/${qid}`,
      { method: 'DELETE', token },
    ),
}
```

- [ ] **Step 2: `lib/hooks/use-pipeline-classify.ts`**

```typescript
import { useMutation } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { pipelinesApi } from '@/lib/api/pipelines'
import type { PreviewChangesResponse } from '@/lib/api/pipelines'

export function usePipelineClassify(jobId: string) {
  return useMutation<PreviewChangesResponse, Error, unknown>({
    mutationFn: async (proposedBody) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.previewChanges(token, jobId, proposedBody)
    },
  })
}
```

- [ ] **Step 3: `lib/hooks/use-activate-job.ts`**

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { pipelinesApi } from '@/lib/api/pipelines'

export function useActivateJob(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.activate(token, jobId)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
  })
}
```

- [ ] **Step 4: `lib/hooks/use-refine-question.ts` and `use-draft-question.ts`**

```typescript
// use-refine-question.ts
import { useMutation } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { questionsApi, RefineRequest, RefineResponse } from '@/lib/api/questions'

export function useRefineQuestion(jobId: string, stageId: string, questionId: string) {
  return useMutation<RefineResponse, Error, RefineRequest>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionsApi.refine(token, jobId, stageId, questionId, body)
    },
  })
}
```

```typescript
// use-draft-question.ts
import { useMutation } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { questionsApi, DraftRequest, DraftResponse } from '@/lib/api/questions'

export function useDraftQuestion(jobId: string, stageId: string) {
  return useMutation<DraftResponse, Error, DraftRequest>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return questionsApi.draft(token, jobId, stageId, body)
    },
  })
}
```

- [ ] **Step 5: Smoke type-check**

```bash
npm run type-check
```

Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/questions.ts frontend/app/lib/hooks/use-pipeline-classify.ts frontend/app/lib/hooks/use-activate-job.ts frontend/app/lib/hooks/use-refine-question.ts frontend/app/lib/hooks/use-draft-question.ts
git commit -m "feat(frontend): questions API namespace + four mutation hooks"
```

---

## Batch 9 — Frontend StageConfigDrawer + StageConfigurationTab (matrix-driven rendering)

Outcome: per-category fields shown only where the matrix marks ✓; locked fields rendered as disabled chips with tooltips; Advanced section hidden for intake/debrief.

### Task 19: Matrix-driven `StageConfigDrawer.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`.
- Test: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.test.tsx` (new or extend if exists).

- [ ] **Step 1: Write failing tests**

Create `frontend/app/components/dashboard/pipeline/StageConfigDrawer.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StageConfigDrawer } from './StageConfigDrawer'

const baseProps = {
  open: true, onOpenChange: () => {}, onChange: () => {}, jobId: 'j1',
}

describe('StageConfigDrawer matrix-driven rendering', () => {
  it('intake stage hides duration, difficulty, signal_filter, pass_criteria', () => {
    const stage = { id: 's0', position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(<StageConfigDrawer {...baseProps} stage={stage} />)
    expect(screen.queryByLabelText(/duration/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/difficulty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/signal filter/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/pass criteria/i)).not.toBeInTheDocument()
  })

  it('intake stage shows name and SLA days', () => {
    const stage = { id: 's0', position: 0, name: 'Intake', stage_type: 'intake' as const }
    render(<StageConfigDrawer {...baseProps} stage={stage} />)
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/sla days/i)).toBeInTheDocument()
  })

  it('phone_screen stage shows all matrix-required fields', () => {
    const stage = {
      id: 's1', position: 1, name: 'Phone Screen', stage_type: 'phone_screen' as const,
      duration_minutes: 30, difficulty: 'medium' as const,
      signal_filter: { include_types: ['competency'] },
      pass_criteria: { type: 'all_knockouts_pass' as const },
      advance_behavior: 'auto_advance' as const,
    }
    render(<StageConfigDrawer {...baseProps} stage={stage} />)
    expect(screen.getByLabelText(/duration/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/difficulty/i)).toBeInTheDocument()
    expect(screen.getByText(/signal filter/i)).toBeInTheDocument()
    expect(screen.getByText(/pass criteria/i)).toBeInTheDocument()
  })

  it('debrief stage shows locked pass_criteria chip with manual_review tooltip', () => {
    const stage = { id: 's4', position: 4, name: 'Debrief', stage_type: 'debrief' as const }
    render(<StageConfigDrawer {...baseProps} stage={stage} />)
    expect(screen.getByText(/manual review/i)).toBeInTheDocument()
    expect(screen.getByText(/manual review/i).closest('[aria-disabled]')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify failure**

```bash
cd frontend/app && npm run test -- StageConfigDrawer.test.tsx
```

Expected: failures (current drawer renders all fields uniformly).

- [ ] **Step 3: Refactor the drawer**

Open `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`. Import the matrix helper:

```tsx
import { stageCategory, participantSlotsFor } from '@/lib/pipelines/categories'
```

Wrap each field block in a conditional based on the stage type. Concrete pattern:

```tsx
const category = stageCategory(stage.stage_type)
const isIO = category === 'entry' || category === 'review'
const isHumanLed = category === 'human_led'
const isAiLed = category === 'ai_led'
const isScreening = isHumanLed || isAiLed

return (
  <Drawer open={open} onOpenChange={onOpenChange}>
    {/* Name — always */}
    <Field label="Name" {...nameProps} />

    {/* Stage type — always */}
    <Field label="Stage type" {...typeProps} />

    {/* SLA days — always (per matrix) */}
    <Field label="SLA days" {...slaProps} />

    {/* Duration + Difficulty + Signal filter — only screening categories */}
    {isScreening && <Field label="Duration (minutes)" {...durationProps} />}
    {isScreening && <Field label="Difficulty" {...difficultyProps} />}
    {isScreening && <SignalFilterEditor {...signalFilterProps} />}

    {/* Pass criteria + Advance behavior — locked for IO, editable for screening */}
    {isScreening && <PassCriteriaEditor {...passCriteriaProps} />}
    {isScreening && <AdvanceBehaviorPicker {...advanceProps} />}
    {category === 'review' && (
      <LockedField label="Pass criteria" value="Manual review" tooltip="Debrief is always manual review (HM decides)" />
    )}
    {category === 'review' && (
      <LockedField label="Advance behavior" value="Manual review (terminal)" tooltip="Debrief is the final decision step." />
    )}

    {/* Participants — only when participantSlotsFor returns slots */}
    {jobId && participantSlotsFor(stage.stage_type).length > 0 && (
      <StageParticipantsEditor stage={stage} jobId={jobId} onChange={onParticipantsChange} />
    )}

    {/* Advanced section — only render for screening categories */}
    {isScreening && <CollapsibleAdvanced>{/* sub-fields */}</CollapsibleAdvanced>}
  </Drawer>
)
```

Add the `LockedField` component (or reuse an existing disabled-input pattern):

```tsx
function LockedField({ label, value, tooltip }: { label: string; value: string; tooltip: string }) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Tooltip>
        <TooltipTrigger render={<div aria-disabled className="...">{value}</div>} />
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
npm run test -- StageConfigDrawer.test.tsx
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx frontend/app/components/dashboard/pipeline/StageConfigDrawer.test.tsx
git commit -m "feat(StageConfigDrawer): matrix-driven field rendering"
```

---

### Task 20: Apply same gating to `StageConfigurationTab.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx`.
- Test: extend `StageConfigurationTab.test.tsx` (new or modify existing) with parallel cases.

- [ ] **Step 1: Mirror the drawer's category-aware rendering pattern**

Apply the same `isScreening` / `isIO` gating in the inline tab variant. Reuse the `LockedField` and `stageCategory` helpers introduced in Task 19.

- [ ] **Step 2: Add parallel tests covering each stage type**

Same shape as Task 19's tests, but rendering `<StageConfigurationTab>` instead.

- [ ] **Step 3: Run tests + lint**

```bash
npm run test -- StageConfigurationTab
npm run lint
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx frontend/app/components/dashboard/pipeline/StageConfigurationTab.test.tsx
git commit -m "feat(StageConfigurationTab): matrix-driven field rendering (mirror drawer)"
```

---

## Batch 10 — Frontend picker, ActivationGate, SourcePill, dialogs, page wiring

Outcome: the front-door picker shows when no instance exists; ActivationGate displays predicate failures; SourcePill on the funnel header; LLM dialogs for Refine/Add; `/pipeline` and `/questions` pages branch correctly.

### Task 21: `PipelineSourcePicker` component

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/PipelineSourcePicker.tsx`.
- Test: `frontend/app/components/dashboard/pipeline/PipelineSourcePicker.test.tsx` (new).

- [ ] **Step 1: Write failing tests**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PipelineSourcePicker } from './PipelineSourcePicker'

describe('PipelineSourcePicker', () => {
  it('renders all four system starter cards', () => {
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={() => {}} />)
    expect(screen.getByText('Standard Technical')).toBeInTheDocument()
    expect(screen.getByText('Fast Track')).toBeInTheDocument()
    expect(screen.getByText('Screening Only')).toBeInTheDocument()
    expect(screen.getByText('Senior Leadership')).toBeInTheDocument()
  })

  it('renders blank card last', () => {
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={() => {}} />)
    expect(screen.getByText(/build from scratch/i)).toBeInTheDocument()
  })

  it('calls onPick with template body when a recent template is clicked', () => {
    const onPick = vi.fn()
    const tpl = { id: 't1', name: 'Eng Default', stage_count: 4, last_used: '2d ago' }
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[tpl]} teamDefault={null} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: /eng default/i }))
    expect(onPick).toHaveBeenCalledWith({ source: 'template', template_id: 't1' })
  })

  it('calls onPick with starter body when a starter is clicked', () => {
    const onPick = vi.fn()
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: /standard technical/i }))
    expect(onPick).toHaveBeenCalledWith({ source: 'starter', starter_key: 'standard_technical' })
  })

  it('dedupes team default that also appears in recent templates', () => {
    const tpl = { id: 't1', name: 'Eng Default', stage_count: 4, last_used: '2d ago' }
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[tpl]} teamDefault={tpl} onPick={() => {}} />)
    expect(screen.getAllByText('Eng Default')).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Implement the component**

```tsx
import { Card } from '@/components/ui/card'
import type { PipelineCreateRequest } from '@/lib/api/pipelines'

type RecentTemplate = { id: string; name: string; stage_count: number; last_used: string }

const STARTERS: { key: PipelineCreateRequest['starter_key']; label: string; subtitle: string }[] = [
  { key: 'standard_technical', label: 'Standard Technical',
    subtitle: 'Phone → AI Screen → Human Interview' },
  { key: 'fast_track', label: 'Fast Track', subtitle: 'Phone → AI Screen' },
  { key: 'screening_only', label: 'Screening Only', subtitle: 'Phone Screen only' },
  { key: 'senior_leadership', label: 'Senior Leadership',
    subtitle: 'Phone → AI Screen → Two Human Interviews' },
]

export function PipelineSourcePicker({
  jobId, recentTemplates, teamDefault, onPick,
}: {
  jobId: string
  recentTemplates: RecentTemplate[]
  teamDefault: RecentTemplate | null
  onPick: (body: PipelineCreateRequest) => void
}) {
  const recentDeduped = teamDefault
    ? recentTemplates.filter((t) => t.id !== teamDefault.id)
    : recentTemplates

  return (
    <div className="space-y-8 p-8">
      <h2 className="text-xl font-semibold">Choose a starting point for this job's pipeline</h2>

      {recentDeduped.length > 0 && (
        <Section title="Recent templates">
          {recentDeduped.slice(0, 3).map((t) => (
            <TemplateCard key={t.id} template={t}
              onClick={() => onPick({ source: 'template', template_id: t.id })} />
          ))}
        </Section>
      )}

      {teamDefault && (
        <Section title="Team default">
          <TemplateCard template={teamDefault} starred
            onClick={() => onPick({ source: 'template', template_id: teamDefault.id })} />
        </Section>
      )}

      <Section title="System starters">
        {STARTERS.map((s) => (
          <button key={s.key} onClick={() => onPick({ source: 'starter', starter_key: s.key })}>
            <Card>
              <div className="font-medium">{s.label}</div>
              <div className="text-sm text-muted-foreground">{s.subtitle}</div>
            </Card>
          </button>
        ))}
      </Section>

      <Section title="Or start blank">
        <button onClick={() => onPick({ source: 'scratch' })}>
          <Card>
            <div className="font-medium">Build from scratch</div>
            <div className="text-sm text-muted-foreground">Just intake + debrief; you add the middle stages.</div>
          </Card>
        </button>
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">{children}</div>
    </section>
  )
}

function TemplateCard({ template, onClick, starred }: {
  template: RecentTemplate; onClick: () => void; starred?: boolean
}) {
  return (
    <button onClick={onClick} className="text-left">
      <Card>
        <div className="font-medium">{starred && '★ '}{template.name}</div>
        <div className="text-sm text-muted-foreground">
          {template.stage_count} stages · {template.last_used}
        </div>
      </Card>
    </button>
  )
}
```

- [ ] **Step 3: Run tests + commit**

```bash
npm run test -- PipelineSourcePicker.test.tsx
npm run type-check
git add frontend/app/components/dashboard/pipeline/PipelineSourcePicker.tsx frontend/app/components/dashboard/pipeline/PipelineSourcePicker.test.tsx
git commit -m "feat(pipeline): PipelineSourcePicker component"
```

---

### Task 22: `ActivationGate` component

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/ActivationGate.tsx`.
- Test: `frontend/app/components/dashboard/pipeline/ActivationGate.test.tsx` (new).

- [ ] **Step 1: Tests + Step 2: Implementation**

```tsx
// ActivationGate.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ActivationGate } from './ActivationGate'

describe('ActivationGate', () => {
  it('shows red strip with predicate failures + disabled button', () => {
    render(<ActivationGate failures={[
      { code: 'missing_interviewer', message: 'Add an interviewer to Phone Screen', stage_id: 's1' },
      { code: 'missing_bank', message: 'Generate a question bank for AI Screening', stage_id: 's2' },
    ]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/2 things needed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled()
  })

  it('shows green strip and enables Activate when no failures', () => {
    render(<ActivationGate failures={[]} onActivate={() => {}} onFocusStage={() => {}} />)
    expect(screen.getByText(/ready to activate/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /activate/i })).not.toBeDisabled()
  })

  it('clicking a failure focuses its stage', () => {
    const onFocus = vi.fn()
    render(<ActivationGate failures={[
      { code: 'missing_interviewer', message: 'Add an interviewer', stage_id: 's1' },
    ]} onActivate={() => {}} onFocusStage={onFocus} />)
    fireEvent.click(screen.getByRole('button', { name: /add an interviewer/i }))
    expect(onFocus).toHaveBeenCalledWith('s1')
  })
})
```

```tsx
// ActivationGate.tsx
import type { ActivationPredicateFailure } from '@/lib/api/pipelines'

export function ActivationGate({
  failures, onActivate, onFocusStage,
}: {
  failures: ActivationPredicateFailure[]
  onActivate: () => void
  onFocusStage: (stageId: string) => void
}) {
  const ready = failures.length === 0

  return (
    <div className={ready
      ? "rounded border border-emerald-300 bg-emerald-50 p-4"
      : "rounded border border-amber-300 bg-amber-50 p-4"}>
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <p className="font-medium">
            {ready
              ? '✓ Ready to activate this job. Candidates will be able to enter the pipeline.'
              : `⚠ ${failures.length} thing${failures.length === 1 ? '' : 's'} needed before you can activate this job:`}
          </p>
          {!ready && (
            <ul className="space-y-1 text-sm">
              {failures.map((f) => (
                <li key={`${f.code}-${f.stage_id ?? ''}`}>
                  <button className="text-left underline"
                    onClick={() => f.stage_id && onFocusStage(f.stage_id)}>
                    • {f.message}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button disabled={!ready} onClick={onActivate}
          className="rounded bg-zinc-900 px-4 py-2 text-white disabled:opacity-40">
          Activate
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Run tests + commit**

```bash
npm run test -- ActivationGate.test.tsx
git add frontend/app/components/dashboard/pipeline/ActivationGate.tsx frontend/app/components/dashboard/pipeline/ActivationGate.test.tsx
git commit -m "feat(pipeline): ActivationGate component"
```

---

### Task 23: `SourcePill` + `EditCategoryWarningModal`

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/SourcePill.tsx`.
- Create: `frontend/app/components/dashboard/pipeline/EditCategoryWarningModal.tsx`.
- Test: companion `.test.tsx` for each.

- [ ] **Step 1–3: Build SourcePill**

A small component that renders the source pill with a kebab menu. Matches the spec §9.3 contract: pill text, "Edited" indicator when `pipeline_version > 1`, four kebab actions (Reset / Swap / Save as template / Update source — last two visible only when source is a tenant template). Tests verify each kebab item renders only when applicable; clicking emits the right callback.

- [ ] **Step 4–6: Build EditCategoryWarningModal**

Renders one of three flavors based on `category`:

- Category B: "This changes the pipeline shape. New candidates will see N+1 stages. M candidates currently in flight stay on their entered shape." → Confirm / Cancel.
- Category C with in_flight=0: "Remove this stage? This is permanent." → Confirm / Cancel.
- Category C with in_flight>0: "K candidates are currently in this stage. Pause it first; drain manually." → Pause Stage / Cancel.

Tests verify each flavor renders the right copy and CTA based on `category` + `in_flight` props.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/SourcePill.* frontend/app/components/dashboard/pipeline/EditCategoryWarningModal.*
git commit -m "feat(pipeline): SourcePill + EditCategoryWarningModal"
```

---

### Task 24: `RefineQuestionDialog` + `AddQuestionDialog`

**Files:**
- Create: `frontend/app/components/dashboard/question-bank/RefineQuestionDialog.tsx`.
- Create: `frontend/app/components/dashboard/question-bank/AddQuestionDialog.tsx`.
- Test: companion `.test.tsx`.

- [ ] **Step 1: Tests for RefineQuestionDialog**

```tsx
// RefineQuestionDialog.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { RefineQuestionDialog } from './RefineQuestionDialog'

describe('RefineQuestionDialog', () => {
  it('shows current question; user types instruction; submit calls refine', async () => {
    const onRefine = vi.fn().mockResolvedValue({
      proposed_text: 'Refined text', proposed_signal_probed: 'competency',
      proposed_mandatory: true,
    })
    const onAccept = vi.fn()
    render(<RefineQuestionDialog
      open={true} onOpenChange={() => {}}
      question={{ id: 'q1', text: 'Original?', signal_probed: 'competency', mandatory: false }}
      onRefine={onRefine} onAccept={onAccept} />)
    expect(screen.getByText('Original?')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/what do you want to change/i),
      { target: { value: 'Make it stricter' } })
    fireEvent.click(screen.getByRole('button', { name: /refine/i }))
    await waitFor(() => expect(onRefine).toHaveBeenCalledWith({ instruction: 'Make it stricter' }))
    await waitFor(() => expect(screen.getByText('Refined text')).toBeInTheDocument())
  })

  it('Accept button submits the proposal to onAccept', async () => {
    const onRefine = vi.fn().mockResolvedValue({
      proposed_text: 'Refined', proposed_signal_probed: 'c', proposed_mandatory: true,
    })
    const onAccept = vi.fn()
    render(<RefineQuestionDialog
      open={true} onOpenChange={() => {}}
      question={{ id: 'q1', text: 'O?', signal_probed: 'c', mandatory: false }}
      onRefine={onRefine} onAccept={onAccept} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i),
      { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /refine/i }))
    await waitFor(() => screen.getByText('Refined'))
    fireEvent.click(screen.getByRole('button', { name: /accept/i }))
    expect(onAccept).toHaveBeenCalledWith({
      text: 'Refined', signal_probed: 'c', mandatory: true,
    })
  })

  it('Refine again allows re-prompting after a proposal', async () => {
    const onRefine = vi.fn()
      .mockResolvedValueOnce({ proposed_text: 'P1', proposed_signal_probed: 'c', proposed_mandatory: false })
      .mockResolvedValueOnce({ proposed_text: 'P2', proposed_signal_probed: 'c', proposed_mandatory: false })
    render(<RefineQuestionDialog
      open={true} onOpenChange={() => {}}
      question={{ id: 'q1', text: 'O?', signal_probed: 'c', mandatory: false }}
      onRefine={onRefine} onAccept={() => {}} />)
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'a' } })
    fireEvent.click(screen.getByRole('button', { name: /refine/i }))
    await waitFor(() => screen.getByText('P1'))
    fireEvent.click(screen.getByRole('button', { name: /refine again/i }))
    fireEvent.change(screen.getByLabelText(/what do you want to change/i), { target: { value: 'b' } })
    fireEvent.click(screen.getByRole('button', { name: /refine/i }))
    await waitFor(() => screen.getByText('P2'))
    expect(onRefine).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Implement RefineQuestionDialog**

```tsx
import { useState } from 'react'
import type { RefineRequest, RefineResponse } from '@/lib/api/questions'

type Question = { id: string; text: string; signal_probed: string; mandatory: boolean }

export function RefineQuestionDialog({
  open, onOpenChange, question, onRefine, onAccept,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  question: Question
  onRefine: (body: RefineRequest) => Promise<RefineResponse>
  onAccept: (body: { text: string; signal_probed: string; mandatory: boolean }) => void
}) {
  const [instruction, setInstruction] = useState('')
  const [proposal, setProposal] = useState<RefineResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setLoading(true); setError(null)
    try {
      const res = await onRefine({ instruction })
      setProposal(res)
    } catch (e) {
      setError((e as Error).message)
    } finally { setLoading(false) }
  }

  const accept = () => {
    if (!proposal) return
    onAccept({
      text: proposal.proposed_text,
      signal_probed: proposal.proposed_signal_probed,
      mandatory: proposal.proposed_mandatory,
    })
    onOpenChange(false)
  }

  const refineAgain = () => {
    setProposal(null); setInstruction('')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Refine question</DialogTitle>
        <div className="space-y-3">
          <div className="rounded bg-zinc-50 p-3 text-sm">{question.text}</div>
          {!proposal && (
            <>
              <Label htmlFor="instruction">What do you want to change?</Label>
              <Textarea id="instruction" value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="e.g. Make it stricter, focus on Python instead of JS, ..." />
              <Button disabled={loading || instruction.length < 3} onClick={submit}>
                {loading ? 'Refining…' : 'Refine'}
              </Button>
            </>
          )}
          {proposal && (
            <>
              <div className="rounded border border-emerald-200 bg-emerald-50 p-3">
                <div className="text-xs uppercase text-emerald-700">Proposed</div>
                <div className="text-sm">{proposal.proposed_text}</div>
                {proposal.rationale && <div className="mt-2 text-xs text-zinc-600">{proposal.rationale}</div>}
              </div>
              <div className="flex gap-2">
                <Button onClick={accept}>Accept</Button>
                <Button variant="ghost" onClick={refineAgain}>Refine again</Button>
                <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
              </div>
            </>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 3: Mirror for AddQuestionDialog**

Same structure but no `question` prop; the proposal includes `proposed_position`, and `onAccept` receives `{ text, signal_probed, mandatory, position }`. Tests follow the same shape.

- [ ] **Step 4: Run tests + commit**

```bash
npm run test -- RefineQuestionDialog AddQuestionDialog
git add frontend/app/components/dashboard/question-bank/RefineQuestionDialog.* frontend/app/components/dashboard/question-bank/AddQuestionDialog.*
git commit -m "feat(question-bank): RefineQuestionDialog + AddQuestionDialog (LLM preview UX)"
```

---

### Task 25: Wire `/pipeline` page

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`.
- Modify: `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx` — header SourcePill + ActivationGate + dry-run integration.

- [ ] **Step 1: Branch on instance presence in `pipeline/page.tsx`**

```tsx
'use client'
// imports omitted for brevity — pattern-match existing page
import { PipelineSourcePicker } from '@/components/dashboard/pipeline/PipelineSourcePicker'
import { JobPipelineFunnel } from '@/components/dashboard/pipeline/JobPipelineFunnel'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'

export default function PipelinePage({ params }: { params: { jobId: string } }) {
  const { data: pipeline, isLoading } = useJobPipeline(params.jobId)

  if (isLoading) return <Skeleton />

  if (!pipeline) {
    return <PipelineSourcePicker
      jobId={params.jobId}
      recentTemplates={[/* fetched via hook */]}
      teamDefault={null}
      onPick={async (body) => {
        const token = await getFreshSupabaseToken()
        await pipelinesApi.create(token, params.jobId, body)
        // refetch
      }}
    />
  }

  return <JobPipelineFunnel jobId={params.jobId} pipeline={pipeline} />
}
```

- [ ] **Step 2: Add SourcePill + ActivationGate to JobPipelineFunnel header**

In `JobPipelineFunnel.tsx`, render:

```tsx
<div className="flex items-center justify-between">
  <SourcePill
    sourceTemplateId={pipeline.source_template_id}
    sourceStarterKey={pipeline.source_starter_key}
    diverged={pipeline.pipeline_version > 1}
    canSwap={job.status !== 'active'}
    onReset={...} onSwap={...} onSaveAsTemplate={...} onUpdateSource={...}
  />
  <button onClick={generateAllBanks}>Generate banks</button>
</div>

<ActivationGate
  failures={activationFailures}
  onActivate={activateMutation.mutate}
  onFocusStage={(stageId) => focusStageDrawer(stageId)}
/>

{/* existing funnel rendering */}
```

The `activationFailures` come from a new query hook `useActivationStatus(jobId)` that issues `POST /jobs/{id}/activate` as a dry-run (or a new GET endpoint — pattern-match per the spec).

Wait: the spec doesn't add a separate dry-run for activation; it surfaces failures only when the recruiter clicks Activate (which 422s). For the UI to show the checklist *before* clicking, add a derived computation client-side from the pipeline data: count interviewers per human_led stage, count reviewers per debrief, check banks. The frontend already has all this data. Implement `computeActivationFailures(pipeline)` as a pure function in `lib/pipelines/activation.ts` and use it in the funnel.

- [ ] **Step 3: Add `computeActivationFailures` helper**

```typescript
// frontend/app/lib/pipelines/activation.ts
import type { JobPipeline, ActivationPredicateFailure } from '@/lib/api/pipelines'
import { stageCategory } from './categories'

export function computeActivationFailures(pipeline: JobPipeline): ActivationPredicateFailure[] {
  const failures: ActivationPredicateFailure[] = []
  const middleTypes = new Set(['phone_screen', 'ai_screening', 'human_interview'])
  const middleStages = pipeline.stages.filter((s) => middleTypes.has(s.stage_type))
  if (middleStages.length === 0) {
    failures.push({
      code: 'no_middle_stage',
      message: 'Add at least one screening stage between Intake and Debrief.',
      stage_id: null,
    })
  }
  for (const s of pipeline.stages) {
    if (!s.name.trim()) {
      failures.push({ code: 'empty_stage_name',
        message: `Stage at position ${s.position} has no name.`, stage_id: s.id })
    }
    if (s.stage_type === 'phone_screen' || s.stage_type === 'human_interview') {
      const hasInterviewer = (s.participants ?? []).some((p) => p.role === 'interviewer')
      if (!hasInterviewer) failures.push({
        code: 'missing_interviewer',
        message: `Assign an interviewer to '${s.name}'.`, stage_id: s.id,
      })
    }
    if (s.stage_type === 'debrief') {
      const hasReviewer = (s.participants ?? []).some((p) => p.role === 'reviewer')
      if (!hasReviewer) failures.push({
        code: 'missing_reviewer',
        message: `Assign a reviewer to '${s.name}'.`, stage_id: s.id,
      })
    }
    // Bank check uses bank data fetched separately — see JobPipelineFunnel wiring
  }
  return failures
}
```

- [ ] **Step 4: Integrate dry-run on save**

In `JobPipelineFunnel.tsx`, the existing debounced auto-save path becomes:

```tsx
const classify = usePipelineClassify(jobId)
const saveStages = useSaveStages(jobId)

const onStageChange = async (newStages) => {
  const result = await classify.mutateAsync({ stages: newStages })
  if (job.status === 'active' && result.category === 'D') {
    showError("Stage type can't be changed once the job is active.")
    return
  }
  if (result.category === 'B' || result.category === 'C') {
    setWarningModal({ category: result.category, in_flight: result.in_flight, pendingStages: newStages })
    return
  }
  // Category A: save with soft inline banner
  await saveStages.mutateAsync(newStages)
}
```

`EditCategoryWarningModal` confirms; on confirm, `saveStages.mutateAsync(pendingStages)` runs.

- [ ] **Step 5: Run dev + manual smoke**

```bash
npm run dev
# Manual: navigate to /jobs/<id>/pipeline on a fresh signals_confirmed job
# Verify: picker shows; clicking a starter creates instance and shows funnel
# Add a stage; verify Category B warning modal; confirm; verify version bumps
```

- [ ] **Step 6: Run all frontend tests + lint + type-check**

```bash
npm run test
npm run lint
npm run type-check
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx frontend/app/lib/pipelines/activation.ts
git commit -m "feat(pipeline): wire picker + ActivationGate + dry-run + edit warnings on /pipeline"
```

---

### Task 26: Wire `/questions` page (filter + Refine/Add dialogs)

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx`.

- [ ] **Step 1: Filter stage pills using `stageSupportsQuestionBank`**

Locate the existing `stages.map((s, i) => <StagePill ... />)` (around line 169-178 of the page). Wrap the iteration:

```tsx
{stages
  .filter((s) => stageSupportsQuestionBank(s.stage_type))
  .map((s, i) => (
    <StagePill key={s.id} index={i} stage={s} bank={...} active={...} onClick={...} />
  ))}
```

This drops intake and debrief from the pill row.

- [ ] **Step 2: Replace any inline text editing with RefineQuestionDialog**

In the question card / table component used by this page, find any direct `<input>` or `<textarea>` editing the question's `text`. Replace with a "Refine" button that opens `<RefineQuestionDialog>`. Same for "Add question" — replace any inline form with `<AddQuestionDialog>`.

- [ ] **Step 3: Wire the refine/draft hooks**

```tsx
const refine = useRefineQuestion(jobId, stageId, q.id)
const draft = useDraftQuestion(jobId, stageId)

const onAcceptRefine = async (body) => {
  const token = await getFreshSupabaseToken()
  await questionsApi.acceptRefine(token, jobId, stageId, q.id, body)
  qc.invalidateQueries({ queryKey: ['banks', jobId, stageId] })
}

const onAcceptDraft = async (body) => {
  const token = await getFreshSupabaseToken()
  await questionsApi.acceptDraft(token, jobId, stageId, body)
  qc.invalidateQueries({ queryKey: ['banks', jobId, stageId] })
}
```

- [ ] **Step 4: Add a test for the page filter**

```tsx
// app/(dashboard)/jobs/[jobId]/questions/page.test.tsx
it('hides intake and debrief from the stage pill row', async () => {
  // Render the page with a pipeline that has intake, phone_screen, debrief.
  // Assert intake and debrief stage names do NOT appear in the pill row.
})
```

- [ ] **Step 5: Run + commit**

```bash
npm run test -- questions/page.test.tsx
npm run dev   # smoke: open /questions; verify intake/debrief not in pill row; click a question's Refine
git add frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.test.tsx
git commit -m "feat(questions): filter IO stages from pill row + wire Refine/Add dialogs"
```

---

## Self-review checklist (run before declaring the plan complete)

- [ ] **Spec coverage:** Every section in the spec maps to at least one task.
  - §1 Problem, §2 Scope/non-goals — framing; no task.
  - §3 Recruiter journey J0–J6 — distributed across all tasks; J3 picker = Task 21, J5 = Tasks 14–16, 24, J6 = Tasks 12 + 22.
  - §4 State machines — Task 4 (job-state transitions), Tasks 11/12 (pause / activate).
  - §5 Versioning + runtime resolution — Task 1 (column), Task 7 (bump helper), Task 13 (candidates resolver skips paused).
  - §6 Capability matrix — Task 2 (ORM), Task 3 (Pydantic enforcement), Tasks 19/20 (frontend gating).
  - §7 Activation gate — Task 12 (endpoint + predicates), Task 22 (UI), Task 25 (page integration), Task 13 (candidates active-state enforcement).
  - §8 Edit categories — Task 8 (classifier), Task 9 (preview endpoint + Category-D enforcement on PATCH), Task 11 (pause/unpause), Task 23 (UI warning modal), Task 25 (wire dry-run on save).
  - §9 Front-door picker — Task 6 (POST discriminator), Task 21 (component), Task 23 (SourcePill), Task 25 (wire).
  - §10 Removal of silent auto-apply — Task 5.
  - §11 Question bank lifecycle — Task 10 (persisted is_stale), Task 14 (prompts), Task 15 (build_question_context refactor), Task 16 (refine/draft endpoints), Task 24 (dialogs), Task 26 (wire).
  - §12 Database schema — Task 1 (migration), Task 2 (ORM models).
  - §13 Endpoints — Tasks 6, 9, 11, 12, 13, 16.
  - §14 Frontend file inventory — Tasks 17, 18, 19, 20, 21, 22, 23, 24, 25, 26.
  - §15 Rollback — Task 1 (downgrade lives in the same migration file).
  - §16 Tests — inline within each task; new test files listed by spec are produced as part of their respective task.
  - §17 Rollout — batch ordering matches the spec's PR step list.
  - §18 Risks, §19 Decisions, §20 Out-of-scope — captured in the spec; no plan tasks needed.
- [ ] **No placeholders:** TBD/TODO scan returns zero hits in this file.
- [ ] **Type consistency:** `EditCategory` is `'A' | 'B' | 'C' | 'D'` everywhere; `PipelineCreateRequest` uses the same discriminator on backend (Pydantic) and frontend (TS); `entered_at_pipeline_version` is the same column in migration, ORM, schemas, and tests; `stageSupportsQuestionBank` is imported from `@/lib/pipelines/categories` (existing).

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-26-pipeline-flow-and-activation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task; review between tasks; fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans; batch execution with checkpoints for review.

Which approach?


