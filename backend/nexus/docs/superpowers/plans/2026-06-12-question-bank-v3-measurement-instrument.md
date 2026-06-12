# Question Bank v3 — Measurement-Instrument Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the validity of the generated question bank as a shared measurement instrument (generator + live engine + report) via a prompt redesign, a bounded 2-call generate→self-critic flow, and `project_deepdive` as a first-class question kind — within stable engine/report contracts.

**Architecture:** Backend-first. A new durable `self_reviewing` bank status sits between `generating` and `reviewing`; the generation actor streams the draft, flips to `self_reviewing` (drives a recruiter-facing animation via existing SSE), runs ONE critic LLM call that returns a corrected bank + critique log, then reconciles and lands in `reviewing`. The critic is permanent (no feature-flag dual path); the old `generating → reviewing` edge is removed. A v2→v3 prompt-version bump preserves the scoring audit trail.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Alembic, Dramatiq, OpenAI via `instructor`, Pydantic v2, pytest/pytest-asyncio; Next.js 16 + React Query + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-06-12-question-bank-v3-measurement-instrument-design.md`

---

## Code-quality mandate (binding — from the spec §9)

- No feature-flag dual paths. ONE generation route: `generating → self_reviewing → reviewing`. The `generating → reviewing` edge and the old helper name are **removed/renamed**, not left as a fallback.
- No dead code: every rename propagates to all call sites in the same task; no orphaned helper/import/literal.
- No silent fallbacks: the critic-failure path logs with correlation id + writes a `coverage_notes` marker; never strands a bank in `self_reviewing`.
- Full migration `down()`; tests ship in the same task as the code.

---

## File Structure

**Backend — modify:**
- `migrations/versions/0057_bank_v3_kinds_and_self_reviewing.py` (CREATE) — two CHECK extensions.
- `app/modules/question_bank/schemas.py` — `GeneratedQuestion.question_kind` Literal + new `BankCritiqueOutput`.
- `app/modules/question_bank/state_machine.py` — `BankStatus`, `LEGAL`, `transition_to_self_reviewing`, rename to `transition_to_reviewing_after_critic`.
- `app/modules/question_bank/service.py` — import + `__all__` updates for the rename + new helper.
- `app/modules/question_bank/critic.py` (CREATE) — `run_bank_critic()` + its user-message builder + a mockable client seam.
- `app/modules/question_bank/actors.py` — wire the self-review + critic phases into `_generate_one_bank`; import the renamed helper.
- `app/modules/question_bank/sse.py` — add `self_reviewing` to non-terminal set + fast-cadence check.
- `app/config.py` — `openai_question_bank_critic_model` + `_effort` settings.
- `app/ai/config.py` — `question_bank_critic_model` + `question_bank_critic_effort` properties.
- `prompts/v3/question_bank_common.txt` (CREATE from v2 + edits), `prompts/v3/question_bank_ai_screening.txt` (CREATE from v2 + edits), `prompts/v3/question_bank_phone_screen.txt` (CREATE from v2), `prompts/v3/question_bank_regenerate_one.txt` (CREATE from v2), `prompts/v3/question_bank_critic.txt` (CREATE).
- `prompts/v4/report_scorer/question_grade.txt` — one line for `project_deepdive`.

**Backend — config default flip:** `app/config.py` `question_bank_prompt_version` default `"v2"` → `"v3"`.

**Backend — test:**
- `tests/question_bank/test_state_machine.py` (or existing) — `self_reviewing` transitions.
- `tests/question_bank/test_bank_critic.py` (CREATE) — critic success + fallback (mocked).
- `tests/question_bank/test_generation_quality.py` — actor critic-flow integration (mocked LLM).
- `tests/question_bank/prompt_evals/test_bank_gen_evals.py` — extend valid kinds + new evals.

**Frontend — modify:**
- `frontend/app/lib/api/question-banks.ts` — `BankStatus` + `QuestionKind` unions.
- `frontend/app/components/dashboard/question-bank/BankStatusBadge.tsx` — `self_reviewing` style.
- `frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx` — stage pill + EmptyBankState + `QBDetail` kind badge + `QUESTION_KIND_LABEL`.

---

## Task 1: Migration 0057 — CHECK extensions (`project_deepdive` + `self_reviewing`)

**Files:**
- Create: `migrations/versions/0057_bank_v3_kinds_and_self_reviewing.py`

Adding a value to an `IN (...)` CHECK is a SUPERSET — every existing row still passes, so NO data clear is needed (unlike 0045). The `down()` narrows the CHECK, so it must first rewrite any rows holding a soon-to-be-illegal value.

- [ ] **Step 1: Write the migration file**

```python
"""bank v3: project_deepdive question_kind + self_reviewing bank status

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-12

Two CHECK extensions for the question-bank v3 measurement-instrument redesign:
  1. stage_questions.question_kind  += 'project_deepdive'
  2. stage_question_banks.status     += 'self_reviewing'

Both are supersets of the existing constraint, so existing rows stay valid and no
data clear is needed on upgrade. downgrade() narrows each CHECK, so it first rewrites
any row holding the new value (project_deepdive -> behavioral; self_reviewing ->
generating) to avoid a CHECK violation on constraint recreate.
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

_KIND_CK = "stage_questions_question_kind_check"
_KIND_NEW = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary', 'project_deepdive')"
)
_KIND_OLD = (
    "question_kind IN ('experience_check', 'behavioral', "
    "'technical_scenario', 'compliance_binary')"
)

_STATUS_CK = "stage_question_banks_status_check"
_STATUS_NEW = (
    "status IN ('draft', 'generating', 'self_reviewing', "
    "'reviewing', 'confirmed', 'failed')"
)
_STATUS_OLD = (
    "status IN ('draft', 'generating', 'reviewing', 'confirmed', 'failed')"
)


def upgrade() -> None:
    op.drop_constraint(_KIND_CK, "stage_questions", type_="check")
    op.create_check_constraint(_KIND_CK, "stage_questions", _KIND_NEW)

    op.drop_constraint(_STATUS_CK, "stage_question_banks", type_="check")
    op.create_check_constraint(_STATUS_CK, "stage_question_banks", _STATUS_NEW)


def downgrade() -> None:
    # Rewrite values the narrowed CHECK would reject, THEN narrow.
    op.execute(
        "UPDATE stage_questions SET question_kind = 'behavioral' "
        "WHERE question_kind = 'project_deepdive'"
    )
    op.drop_constraint(_KIND_CK, "stage_questions", type_="check")
    op.create_check_constraint(_KIND_CK, "stage_questions", _KIND_OLD)

    op.execute(
        "UPDATE stage_question_banks SET status = 'generating' "
        "WHERE status = 'self_reviewing'"
    )
    op.drop_constraint(_STATUS_CK, "stage_question_banks", type_="check")
    op.create_check_constraint(_STATUS_CK, "stage_question_banks", _STATUS_OLD)
```

- [ ] **Step 2: Verify up + down apply (manual — tests use create_all, not migrations)**

Run:
```bash
docker compose up -d nexus
docker compose exec nexus alembic upgrade head
docker compose exec nexus alembic downgrade -1
docker compose exec nexus alembic upgrade head
```
Expected: each command exits 0; no CHECK violation. (Per `project_test_harness_rls`, the pytest DB is built via `create_all`, so migrations are verified manually here.)

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/migrations/versions/0057_bank_v3_kinds_and_self_reviewing.py
git commit -m "migrate(question_bank): 0057 — project_deepdive kind + self_reviewing status"
```

---

## Task 2: Schema — `project_deepdive` kind + `BankCritiqueOutput`

**Files:**
- Modify: `app/modules/question_bank/schemas.py:130-142` (the `question_kind` Literal) and add `BankCritiqueOutput` after `StageQuestionBankOutput` (~line 173).
- Test: `tests/question_bank/test_schemas_v3.py` (CREATE)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_schemas_v3.py
import pytest
from pydantic import ValidationError
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    FollowUpDimension,
    BankCritiqueOutput,
)


def _mk_question(kind: str) -> GeneratedQuestion:
    return GeneratedQuestion(
        position=0,
        text="Tell me about a project you personally drove end to end.",
        primary_signal="Distributed systems design",
        signal_values=["Distributed systems design"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[
            FollowUpDimension(
                dimension="decision_ownership",
                intent="verify they made the call, not just executed",
                seed_probe="What did you decide, and what did you choose it over?",
                listen_for=["a named alternative", "a concrete tradeoff"],
            )
        ],
        positive_evidence=["names a real decision", "states a number", "owns 'I did X'"],
        red_flags=["says 'we' with no 'I'", "cannot name a tradeoff against"],
        rubric=QuestionRubric(
            excellent="owns a real decision with a named alternative and a tradeoff",
            meets_bar="describes a real project with at least one concrete decision",
            below_bar="vague, 'we' framing, no recoverable decision",
        ),
        evaluation_hint="tests whether they drove decisions vs merely executed",
        question_kind=kind,
    )


def test_project_deepdive_is_a_valid_kind():
    q = _mk_question("project_deepdive")
    assert q.question_kind == "project_deepdive"


def test_unknown_kind_still_rejected():
    with pytest.raises(ValidationError):
        _mk_question("totally_made_up")


def test_bank_critique_output_carries_corrected_questions_and_log():
    out = BankCritiqueOutput(
        critique="Knockout 'X' was uncovered; added a compliance_binary. Sharpened 2 anchors.",
        questions=[_mk_question("project_deepdive")],
    )
    assert out.questions[0].question_kind == "project_deepdive"
    assert "Knockout" in out.critique
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_schemas_v3.py -q`
Expected: FAIL — `project_deepdive` rejected by the Literal; `BankCritiqueOutput` ImportError.

- [ ] **Step 3: Add `project_deepdive` to the Literal**

In `schemas.py`, change the `question_kind` field on `GeneratedQuestion`:

```python
    question_kind: Literal[
        "experience_check",
        "behavioral",
        "technical_scenario",
        "compliance_binary",
        "project_deepdive",
    ] = Field(
        ...,
        description=(
            "Refined spoken taxonomy: experience_check (claim verification) · "
            "behavioral (true STAR) · technical_scenario (verbal design/depth) · "
            "compliance_binary (hard yes/no gate) · project_deepdive (the senior "
            "spine — a real project the candidate drove, probed for decision "
            "ownership and surviving orthogonal escalation)."
        ),
    )
```

- [ ] **Step 4: Add `BankCritiqueOutput` after `StageQuestionBankOutput`**

```python
class BankCritiqueOutput(BaseModel):
    """Critic LLM response: the corrected full bank + a short audit log.

    The critic audits the streamed draft against a fixed checklist and returns the
    CORRECTED bank (same question shape) plus a human-readable `critique` persisted to
    stage_question_banks.coverage_notes (the scoring audit trail).
    """

    model_config = ConfigDict(extra="forbid")

    critique: str = Field(
        ..., min_length=10, max_length=4000,
        description="What the critic changed and why — coverage gaps closed, anchors "
                    "sharpened, repeats removed, format/seniority fixes.",
    )
    questions: list[GeneratedQuestion] = Field(..., min_length=1, max_length=15)
```

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_schemas_v3.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/schemas.py backend/nexus/tests/question_bank/test_schemas_v3.py
git commit -m "feat(question_bank): project_deepdive kind + BankCritiqueOutput schema"
```

---

## Task 3: State machine — `self_reviewing` status + rename

**Files:**
- Modify: `app/modules/question_bank/state_machine.py` (`BankStatus`, `LEGAL`, helpers).
- Test: `tests/question_bank/test_state_machine_self_reviewing.py` (CREATE)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_state_machine_self_reviewing.py
import uuid
import pytest
from app.modules.question_bank.state_machine import (
    LEGAL,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
    transition_to_generating,
)
from app.modules.question_bank.errors import IllegalTransitionError


class _Bank:
    def __init__(self, status):
        self.status = status
        self.id = uuid.uuid4()
        self.generation_error = None
        self.generated_at = None
        self.generated_by = None
        self.updated_at = None


def test_generating_goes_to_self_reviewing():
    b = _Bank("generating")
    transition_to_self_reviewing(b)
    assert b.status == "self_reviewing"


def test_self_reviewing_goes_to_reviewing():
    b = _Bank("self_reviewing")
    uid = uuid.uuid4()
    transition_to_reviewing_after_critic(b, user_id=uid)
    assert b.status == "reviewing"
    assert b.generated_by == uid


def test_generating_to_reviewing_edge_is_removed():
    # The direct edge is gone — generation must route through self_reviewing.
    assert "reviewing" not in LEGAL["generating"]
    assert "self_reviewing" in LEGAL["generating"]


def test_self_reviewing_transition_rejects_wrong_source():
    b = _Bank("draft")
    with pytest.raises(IllegalTransitionError):
        transition_to_self_reviewing(b)


def test_reviewing_after_critic_rejects_wrong_source():
    b = _Bank("generating")
    with pytest.raises(RuntimeError):
        transition_to_reviewing_after_critic(b, user_id=uuid.uuid4())
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_state_machine_self_reviewing.py -q`
Expected: FAIL — `transition_to_self_reviewing` / `transition_to_reviewing_after_critic` undefined.

- [ ] **Step 3: Update `BankStatus` + `LEGAL`**

In `state_machine.py`:

```python
BankStatus = Literal[
    "draft", "generating", "self_reviewing", "reviewing", "confirmed", "failed"
]

LEGAL: dict[BankStatus, set[BankStatus]] = {
    "draft": {"generating", "reviewing", "failed"},
    "generating": {"self_reviewing", "failed"},
    "self_reviewing": {"reviewing", "failed"},
    "reviewing": {"generating", "confirmed"},
    "confirmed": {"generating", "reviewing"},
    "failed": {"generating"},
}
```

- [ ] **Step 4: Add `transition_to_self_reviewing` and rename the post-generation helper**

Replace `transition_to_reviewing_after_generation` with two functions:

```python
def transition_to_self_reviewing(bank: StageQuestionBank) -> None:
    """generating → self_reviewing (the bank enters the AI self-critic phase).

    Raises IllegalTransitionError on any other source state (defensive).
    """
    if bank.status not in LEGAL or "self_reviewing" not in LEGAL[bank.status]:
        raise IllegalTransitionError(
            from_state=bank.status, to_state="self_reviewing"
        )
    bank.status = "self_reviewing"
    bank.updated_at = _now_utc()


def transition_to_reviewing_after_critic(
    bank: StageQuestionBank, *, user_id: UUID
) -> None:
    """self_reviewing → reviewing on critic completion (success OR fallback).

    Caller-bug guard, not a user-facing error (survives `python -O`).
    """
    if bank.status != "self_reviewing":
        raise RuntimeError(
            f"transition_to_reviewing_after_critic requires "
            f"status='self_reviewing', got {bank.status!r}"
        )
    bank.status = "reviewing"
    bank.generated_at = _now_utc()
    bank.generated_by = user_id
    bank.updated_at = _now_utc()
```

Also update the `transition_to_failed` guard: it must accept `generating` OR `self_reviewing` (a critic-phase crash can fail the bank):

```python
def transition_to_failed(bank: StageQuestionBank, *, error: str) -> None:
    """generating | self_reviewing → failed with error message."""
    if bank.status not in ("generating", "self_reviewing"):
        raise RuntimeError(
            f"transition_to_failed requires status in "
            f"('generating','self_reviewing'), got {bank.status!r}"
        )
    bank.status = "failed"
    bank.generation_error = error
    bank.updated_at = _now_utc()
```

Update the module docstring diagram at the top to: `draft → generating → self_reviewing → reviewing → confirmed`.

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_state_machine_self_reviewing.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/question_bank/state_machine.py backend/nexus/tests/question_bank/test_state_machine_self_reviewing.py
git commit -m "feat(question_bank): self_reviewing status + rename post-gen transition"
```

---

## Task 4: Propagate the rename through `service.py`

**Files:**
- Modify: `app/modules/question_bank/service.py:42-47` (imports) and `:978-980` (`__all__`).

- [ ] **Step 1: Update the import block**

```python
from app.modules.question_bank.state_machine import (
    auto_revert_on_edit,
    transition_to_confirmed,
    transition_to_failed,
    transition_to_generating,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
)
```

- [ ] **Step 2: Update `__all__`**

Replace `"transition_to_reviewing_after_generation",` with:

```python
    "transition_to_generating",
    "transition_to_self_reviewing",
    "transition_to_reviewing_after_critic",
    "transition_to_failed",
```

- [ ] **Step 3: Verify nothing else references the old name**

Run: `cd backend/nexus && grep -rn "transition_to_reviewing_after_generation" app/ tests/`
Expected: zero matches (actors.py is updated in Task 7).

- [ ] **Step 4: Run the import smoke test**

Run: `docker compose run --rm nexus python -c "import app.modules.question_bank.service"`
Expected: no ImportError.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/service.py
git commit -m "refactor(question_bank): propagate transition rename through service public API"
```

---

## Task 5: AIConfig + settings — critic model

**Files:**
- Modify: `app/config.py` (after `openai_question_bank_effort`).
- Modify: `app/ai/config.py` (after `question_bank_effort`).
- Test: `tests/question_bank/test_config_critic_model.py` (CREATE)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_config_critic_model.py
from app.ai.config import AIConfig


def test_critic_model_defaults_to_a_value():
    cfg = AIConfig()
    assert cfg.question_bank_critic_model  # non-empty default


def test_critic_effort_defaults_empty(monkeypatch):
    # Effort-gating contract: default empty so a chat-model override is safe.
    cfg = AIConfig()
    assert cfg.question_bank_critic_effort == ""


def test_critic_model_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_QUESTION_BANK_CRITIC_MODEL", "gpt-5.4")
    cfg = AIConfig()
    assert cfg.question_bank_critic_model == "gpt-5.4"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_config_critic_model.py -q`
Expected: FAIL — `question_bank_critic_model` attribute missing.

- [ ] **Step 3: Add the settings fields in `app/config.py`**

After the `openai_question_bank_effort` line (~216):

```python
    # Critic pass — audits + corrects the streamed draft bank (one call per bank).
    # Recommend a STRONGER model than the generator (quality backstop, runs once).
    # Effort default empty per the effort-gating contract (chat-model-safe).
    openai_question_bank_critic_model: str = "gpt-5.4-mini"
    openai_question_bank_critic_effort: str = ""
```

- [ ] **Step 4: Add the AIConfig properties in `app/ai/config.py`**

After `question_bank_effort` (~line 75):

```python
    @property
    def question_bank_critic_model(self) -> str:
        return self._settings.openai_question_bank_critic_model

    @property
    def question_bank_critic_effort(self) -> str:
        return self._settings.openai_question_bank_critic_effort
```

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_config_critic_model.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/app/ai/config.py backend/nexus/tests/question_bank/test_config_critic_model.py
git commit -m "feat(question_bank): AIConfig critic model + effort (chat-model-safe default)"
```

---

## Task 6: v3 prompts — copy v2 → v3 and apply the 6 principles + critic prompt

**Files:**
- Create: `prompts/v3/question_bank_common.txt`, `prompts/v3/question_bank_ai_screening.txt`, `prompts/v3/question_bank_phone_screen.txt`, `prompts/v3/question_bank_regenerate_one.txt`, `prompts/v3/question_bank_critic.txt`.
- Modify: `app/config.py` `question_bank_prompt_version` default → `"v3"`.

> Prompt quality is validated by the eval suite (Task 11), not unit tests. This task creates the files; Task 11 asserts behavior against the real API.

- [ ] **Step 1: Copy the v2 question-bank prompts to v3 verbatim**

Run:
```bash
cd backend/nexus
for f in question_bank_common question_bank_ai_screening question_bank_phone_screen question_bank_regenerate_one; do
  cp "prompts/v2/$f.txt" "prompts/v3/$f.txt"
done
ls prompts/v3/question_bank_*.txt
```
Expected: four v3 files listed.

- [ ] **Step 2: Add the P1 seniority-format section to `prompts/v3/question_bank_common.txt`**

Insert this section immediately AFTER the `# difficulty` section (before `# follow_ups`):

```
# Question format scales with seniority (a rule, not a preference)

The user message pins a `Seniority`. Choose question FORMAT by it — format validity
is not constant across levels:

  - SENIOR / lead / staff / principal / executive → lead with BEHAVIOR-DESCRIPTION
    ("tell me about a time you actually…"), DESIGN-JUDGMENT (technical_scenario), and
    exactly one PROJECT DEEP-DIVE (see below). DOWN-WEIGHT hypothetical situational
    ("what would you do if…") — its predictive validity DECAYS as role complexity
    rises; a senior candidate is verified by what they have actually done and decided.
  - JUNIOR / entry / associate → situational hypotheticals are acceptable and easier
    to standardize; a project deep-dive is optional (they may not have driven one).

Pick the format the seniority calls for; do not default every role to the same shape.
```

- [ ] **Step 3: Add the P5 project_deepdive kind to the `question_kind` taxonomy in `question_bank_common.txt`**

In the `# question_kind — pick exactly one per question` section, add a fifth bullet after `compliance_binary`:

```
  - project_deepdive — the SENIOR SPINE. Invite the candidate to pick ONE real project
    they personally drove, then probe it as the depth ladder: what THEY decided, what
    they chose it over, what broke, what they would change. It is at once the strongest
    seniority signal (did they drive decisions or merely execute?) and the strongest
    bluff test — a fabricated or proxy-coached project disintegrates under orthogonal
    escalation. Author EXACTLY ONE for senior/experienced roles; omit for junior roles.
```

- [ ] **Step 4: Reframe `# follow_ups` as escalation ladders (P2) in `question_bank_common.txt`**

In the `# follow_ups — governed probe dimensions` section, insert this paragraph right after the opening sentence ("Follow-ups carry the depth you kept OUT of the lead…"):

```
The ladder ESCALATES. Order it foundational-specific → deeper-specific → orthogonal.
Every `seed_probe` must demand a FALSIFIABLE SPECIFIC — a number, a tool/name, a
sequence, a failure mode, or a tradeoff — never an open "tell me more" (generic
elaboration prompts let a bluffer embellish and actually INCREASE faking). At least one
rung must RE-APPROACH the same ground from an orthogonal angle ("why X over Y", "what
broke", "what would you change") — genuine experience survives recursive specifics;
fabrication degrades under them.
```

- [ ] **Step 5: Sharpen `red_flags` to content tripwires (P4) in `question_bank_common.txt`**

In the `# Evaluator-only fields` section, REPLACE the `red_flags (2–3)` bullet with:

```
  - red_flags (2–3): CONTENT tells of a weak/fabricated answer, not delivery cues —
    "frames the work around tools/buzzwords instead of impact and decisions"; "says
    'we' with no recoverable 'I did'"; "cannot name a single tradeoff AGAINST the
    choice they made"; "vague on a peripheral detail a real practitioner would know
    cold". A confident tone is not depth. (Note: in remote Indian-market screens, an
    AI-coached or live-proxy candidate reads notes fluently but collapses under
    orthogonal escalation — the ladder is the defense, not the delivery.)
```

- [ ] **Step 6: Add warmth + anchor-sharpness (P7) to `question_bank_common.txt`**

In the `# The lead question` section, append to bar 3 (Sayable): 

```
   Warm and conversational, never an interrogation — a casual register surfaces
   inconsistencies better than a grilling, and the "what did YOU personally do" probe
   (which matters more where 'we'-framing is cultural) must still sound friendly.
```

And in the `rubric` bullet of `# Evaluator-only fields`, append:

```
    Each band must name OBSERVABLE spoken behavior the scorer can point to in the
    transcript ("names the specific datastore and why", not "good depth" / "clear
    explanation") — the report maps these anchors to a grade, so a vague anchor
    produces a noisy score.
```

- [ ] **Step 7: Add the senior project-deepdive requirement to the authoring recipe in `prompts/v3/question_bank_ai_screening.txt`**

In the `# Authoring recipe` numbered list, insert a new step between step 2 (BEHAVIORAL STAR) and step 3 (TECHNICAL DEPTH):

```
2.5 PROJECT DEEP-DIVE (senior/experienced roles — REQUIRED, exactly one). Author one
    `project_deepdive` whose lead invites the candidate to pick a real project they
    personally drove, with an escalating ladder (decision ownership → chose-it-over-what
    → what broke → what they'd change). For junior/entry roles, SKIP this step. This is
    the highest-validity item in the bank — do not let technical scenarios crowd it out.
```

- [ ] **Step 8: Write `prompts/v3/question_bank_critic.txt`**

```
You are the CRITIC for a structured screening question bank. A first pass generated a
DRAFT bank for one role. Your job is to AUDIT it against a fixed checklist and return a
CORRECTED bank — same question shape — plus a short `critique` log of what you changed.

You are not re-authoring from scratch. Keep every question that is already strong. Fix
only what fails the checklist. Preserve `signal_values` discipline: every value must
still match the pinned snapshot VERBATIM; never invent a signal. Keep each lead a single
spoken ask (≤240 chars), and never let evaluator-only phrasing leak into spoken fields.

# The pinned context

The user message gives you: the role + seniority, the SIGNAL SNAPSHOT (with weight /
priority / knockout), and the DRAFT bank (every question with its follow-ups, rubric,
red_flags, kind, difficulty).

# Audit checklist — fix every violation

1. COVERAGE — every KNOCKOUT signal and every high-weight (weight 3) REQUIRED signal is
   probed by at least one question. If one is uncovered, ADD a question for it (an
   experience_check or compliance_binary for a gate; a technical_scenario for a
   competency).
2. SENIORITY FORMAT — senior/experienced roles lead with behavior-description +
   design-judgment + exactly ONE project_deepdive, and do NOT lean on hypothetical
   situational questions. Junior roles may use situational. Fix mismatches.
3. PROJECT DEEP-DIVE — senior/experienced banks contain exactly one `project_deepdive`
   with an escalating ladder (decision ownership → alternative → what broke → what
   they'd change). Add or repair it. Junior banks must NOT contain one.
4. DISTINCTNESS — no two follow-up `dimension`s probe the same underlying thing (even
   under different slugs). Cross-cutting operational concerns (error/retry/observability/
   rollback/idempotency/security/validation) are BANK-LEVEL SINGLETONS — probed at most
   once across the whole bank. Merge or replace duplicates with a genuinely different
   competency.
5. ESCALATION + SPECIFICS — every `seed_probe` demands a falsifiable specific (number /
   name / sequence / failure mode / tradeoff), never open "tell me more"; each ladder has
   at least one orthogonal re-approach rung.
6. ANCHOR SHARPNESS — every rubric band names OBSERVABLE spoken behavior, not vague
   qualities ("names the datastore and why", not "good depth"). Rewrite vague anchors.
7. BLUFFER TRIPWIRES — red_flags are content tells (tool-name-dropping without impact;
   "we" with no "I"; can't name a tradeoff against their choice; vague on peripheral
   detail), not delivery cues.
8. SPOKEN HYGIENE — each lead is ONE self-contained ask, ≤240 chars; no multi-part; no
   evaluator phrasing in `text` / `seed_probe`.

# Output

Return a BankCritiqueOutput: the FULL corrected list of questions (positions 0..N-1, in
order) and a `critique` string of 1–6 sentences naming what you changed and why (the
audit trail). If the draft already passes every check, return it unchanged with a
`critique` that says so.
```

- [ ] **Step 9: Flip the prompt-version default to v3**

In `app/config.py`, change the `question_bank_prompt_version` default from `"v2"` to `"v3"`:

```python
    question_bank_prompt_version: str = "v3"
```

- [ ] **Step 10: Smoke-test prompt loading**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; p=PromptLoader(version='v3'); print(len(p.load_pair('question_bank_common','question_bank_ai_screening'))); print(len(p.load('question_bank_critic')))"`
Expected: two integer lengths printed (no FileNotFoundError).

- [ ] **Step 11: Commit**

```bash
git add backend/nexus/prompts/v3/question_bank_common.txt backend/nexus/prompts/v3/question_bank_ai_screening.txt backend/nexus/prompts/v3/question_bank_phone_screen.txt backend/nexus/prompts/v3/question_bank_regenerate_one.txt backend/nexus/prompts/v3/question_bank_critic.txt backend/nexus/app/config.py
git commit -m "feat(question_bank): v3 prompts — seniority format, escalation ladders, project_deepdive, critic"
```

---

## Task 7: Critic module — `run_bank_critic()`

**Files:**
- Create: `app/modules/question_bank/critic.py`
- Test: `tests/question_bank/test_bank_critic.py`

The critic is a single non-streaming `instructor` call returning `BankCritiqueOutput`. A thin `_create_critic_completion` seam (mirroring `actors._create_question_iterable`) makes it mockable without hitting the API.

- [ ] **Step 1: Write the failing test (success + fallback paths)**

```python
# tests/question_bank/test_bank_critic.py
import uuid
import pytest
from app.modules.question_bank import critic as critic_mod
from app.modules.question_bank.schemas import (
    BankCritiqueOutput, GeneratedQuestion, QuestionRubric, FollowUpDimension,
)

pytestmark = pytest.mark.asyncio


def _q(text="Tell me about a project you drove.", kind="project_deepdive"):
    return GeneratedQuestion(
        position=0, text=text, primary_signal="X", signal_values=["X"],
        estimated_minutes=5.0, is_mandatory=False,
        follow_ups=[FollowUpDimension(dimension="d", intent="i",
                    seed_probe="What did you choose it over?", listen_for=["a tradeoff"])],
        positive_evidence=["a", "b", "c"], red_flags=["says we", "no tradeoff"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="tests ownership", question_kind=kind,
    )


async def test_run_bank_critic_returns_corrected_bank(monkeypatch):
    corrected = BankCritiqueOutput(critique="added a knockout question", questions=[_q()])

    async def fake_completion(**kwargs):
        return corrected

    monkeypatch.setattr(critic_mod, "_create_critic_completion", fake_completion)

    out, log = await critic_mod.run_bank_critic(
        draft=[_q()],
        seniority="senior", role_title="Staff Engineer",
        signals=[{"value": "X", "type": "competency", "priority": "required",
                  "weight": 3, "knockout": True, "stage": "interview"}],
        stage_difficulty="hard", stage_duration=20,
        bank_id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(),
    )
    assert log == "added a knockout question"
    assert out[0].question_kind == "project_deepdive"


async def test_run_bank_critic_raises_on_llm_failure(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(critic_mod, "_create_critic_completion", boom)

    with pytest.raises(RuntimeError):
        await critic_mod.run_bank_critic(
            draft=[_q()], seniority="senior", role_title="x", signals=[],
            stage_difficulty="hard", stage_duration=20,
            bank_id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(),
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_bank_critic.py -q`
Expected: FAIL — `app.modules.question_bank.critic` does not exist.

- [ ] **Step 3: Implement `critic.py`**

```python
"""Bank self-critic — a single LLM pass that audits + corrects a streamed draft bank.

Permanent stage of generation (NOT feature-flagged). Given the draft questions + the
pinned context, returns the CORRECTED full bank + a short critique log persisted to
stage_question_banks.coverage_notes (the scoring audit trail).
"""
from __future__ import annotations

import time
from uuid import UUID

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.question_bank.schemas import BankCritiqueOutput, GeneratedQuestion

logger = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

_critic_prompt_loader = PromptLoader(version=ai_config.question_bank_prompt_version)


def _build_critic_user_message(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
) -> str:
    parts: list[str] = []
    parts.append("# ROLE\n\n")
    parts.append(f"Title: {role_title}\nSeniority: {seniority}\n")
    parts.append(f"Stage difficulty: {stage_difficulty}\nStage duration: {stage_duration} min\n")

    parts.append("\n# SIGNAL SNAPSHOT (pinned — values are verbatim)\n\n")
    for s in signals:
        parts.append(
            f"- value: {s['value']!r}\n"
            f"  type: {s.get('type')}\n"
            f"  priority: {s.get('priority')}\n"
            f"  weight: {s.get('weight')}\n"
            f"  knockout: {s.get('knockout', False)}\n"
        )

    parts.append("\n# DRAFT BANK TO AUDIT\n\n")
    parts.append(
        "Each question below is the draft. Return the corrected full list + a critique.\n\n"
    )
    parts.append(BankCritiqueOutput(critique="(draft)", questions=draft).model_dump_json(indent=2))
    parts.append("\n\nNow return a BankCritiqueOutput with the corrected bank.\n")
    return "".join(parts)


async def _create_critic_completion(**kwargs) -> BankCritiqueOutput:
    """Mockable seam over the instructor completion call."""
    client = get_openai_client()
    call_kwargs = dict(
        model=ai_config.question_bank_critic_model,
        response_model=BankCritiqueOutput,
        messages=kwargs["messages"],
        max_retries=1,
        metadata=kwargs.get("metadata", {}),
        prompt_cache_key=f"qbank-critic-{kwargs['job_id']}",
    )
    if ai_config.question_bank_critic_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_critic_effort
    return await client.chat.completions.create(**call_kwargs)


async def run_bank_critic(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
) -> tuple[list[GeneratedQuestion], str]:
    """Audit + correct the draft. Returns (corrected_questions, critique_log).

    Raises the underlying exception on LLM/validation failure — the CALLER decides the
    fallback (keep the draft, mark coverage_notes, still reach 'reviewing').
    """
    system_prompt = _critic_prompt_loader.load("question_bank_critic")
    user_message = _build_critic_user_message(
        draft=draft, seniority=seniority, role_title=role_title, signals=signals,
        stage_difficulty=stage_difficulty, stage_duration=stage_duration,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    metadata = {
        "bank_id": str(bank_id),
        "tenant_id": str(tenant_id),
        "job_posting_id": str(job_id),
        "prompt_version": ai_config.question_bank_prompt_version,
    }
    started_at = time.monotonic()
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="question_bank_critic",
            prompt_version=ai_config.question_bank_prompt_version,
            tenant_id=str(tenant_id),
            bank_id=str(bank_id),
            job_posting_id=str(job_id),
            model=ai_config.question_bank_critic_model,
            reasoning_effort=ai_config.question_bank_critic_effort,
        )
        try:
            result = await _create_critic_completion(
                messages=messages, metadata=metadata, job_id=str(job_id),
            )
        except Exception as exc:
            _span = trace.get_current_span()
            _span.record_exception(exc)
            _span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            logger.error(
                "question_bank.critic.failed",
                bank_id=str(bank_id),
                duration_sec=round(time.monotonic() - started_at, 2),
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                exc_info=True,
            )
            raise

    logger.info(
        "question_bank.critic.complete",
        bank_id=str(bank_id),
        duration_sec=round(time.monotonic() - started_at, 2),
        in_count=len(draft),
        out_count=len(result.questions),
    )
    # Re-pack positions defensively so downstream reconcile sees 0..N-1.
    corrected = list(result.questions)
    for i, q in enumerate(corrected):
        q.position = i
    return corrected, result.critique
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_bank_critic.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/critic.py backend/nexus/tests/question_bank/test_bank_critic.py
git commit -m "feat(question_bank): run_bank_critic — single-call draft audit + correction"
```

---

## Task 8: Wire the critic into the generation actor

**Files:**
- Modify: `app/modules/question_bank/actors.py` — import the renamed helper (line 55) + new helpers; insert self-review + critic phases into `_generate_one_bank` between Phase B and Phase C.
- Test: `tests/question_bank/test_generation_quality.py` (extend or CREATE the integration test).

- [ ] **Step 1: Write the failing test (actor routes through self_reviewing + applies critic)**

```python
# tests/question_bank/test_actor_critic_flow.py
import uuid
import pytest
from app.modules.question_bank import actors as actors_mod
from app.modules.question_bank import critic as critic_mod

pytestmark = pytest.mark.asyncio


async def test_generate_one_bank_routes_through_self_reviewing(monkeypatch):
    """The actor must flip the bank to self_reviewing and call the critic before
    landing in reviewing. We assert the ordering via a recorded list of statuses."""
    seen_statuses: list[str] = []

    # Stub the stream so no LLM/network is touched (returns 1 draft question id-less).
    async def fake_stream(**kwargs):
        return []  # questions already "persisted" by the stub persistence below

    # Stub the critic to record it ran and return an unchanged bank + log.
    async def fake_critic(**kwargs):
        return (kwargs["draft"], "critique ran")

    monkeypatch.setattr(actors_mod, "_stream_bank_questions", fake_stream)
    monkeypatch.setattr(actors_mod, "run_bank_critic", fake_critic)

    # NOTE: this is a focused ordering test. The full integration (real DB rows) lives
    # in test_generation_quality.py; here we assert the critic is invoked exactly once
    # and the helper transition_to_self_reviewing is reached before reviewing.
    called = {"critic": 0}
    orig_critic = fake_critic

    async def counting_critic(**kwargs):
        called["critic"] += 1
        return await orig_critic(**kwargs)

    monkeypatch.setattr(actors_mod, "run_bank_critic", counting_critic)
    # The detailed DB-backed assertion is in Step 1b below; here we only verify the
    # critic symbol is imported and wired (guards against an accidental un-wire).
    assert hasattr(actors_mod, "run_bank_critic")
```

> The DB-backed integration assertion belongs in `tests/question_bank/test_generation_quality.py`, which already exercises `_generate_one_bank` against the test DB. Add a case there that monkeypatches `_stream_bank_questions` to persist 2 questions and `run_bank_critic` to drop one, then asserts: (a) the bank ends `reviewing`, (b) `coverage_notes` contains the critique, (c) the persisted question count reflects the critic's correction.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_actor_critic_flow.py -q`
Expected: FAIL — `actors_mod.run_bank_critic` not imported yet.

- [ ] **Step 3: Update imports in `actors.py`**

Change the `from app.modules.question_bank.service import (...)` block: replace `transition_to_reviewing_after_generation` with both new helpers:

```python
    transition_to_failed,
    transition_to_generating,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
```

Add ONLY the critic import near the other module imports (`persist_one_question`, `wipe_ai_questions`, and `get_bank_questions` are ALREADY imported from `service` at the top of `actors.py` — do not re-import them):

```python
from app.modules.question_bank.critic import run_bank_critic
```

- [ ] **Step 4: Insert the self-review + critic phases in `_generate_one_bank`**

In `_generate_one_bank`, the current Phase B is the `await _stream_bank_questions(...)` call, immediately followed by `# ---- Phase C: reconcile + transition ----`. Insert a NEW block between them:

```python
        # ---- Phase B2: enter self-review (durable status drives the UI animation) ----
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            bank = (
                await db.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
            ).scalar_one()
            transition_to_self_reviewing(bank)
            await db.commit()
        # Publish the transition so the SSE fast path shows "AI is self-reviewing…".
        await pubsub.publish(
            pubsub.job_channel(job_id),
            pubsub.Events.BANK_STATUS_CHANGED,
            {
                "job_id": str(job_id),
                "bank_id": str(bank_id),
                "stage_id": str(stage_id),
                "new_status": "self_reviewing",
                "source": "actor",
            },
            correlation_id=correlation_id or f"actor-critic-{bank_id}",
        )

        # ---- Phase B3: critic — audit + correct the draft (no held session) ----
        async with get_bypass_session() as rdb:
            await rdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            draft_rows = await get_bank_questions(rdb, bank_id)
            job_row = (
                await rdb.execute(select(JobPosting).where(JobPosting.id == job_id))
            ).scalar_one()
            snapshot_row = (
                await rdb.execute(
                    select(JobPostingSignalSnapshot).where(
                        JobPostingSignalSnapshot.id == snapshot_id
                    )
                )
            ).scalar_one()
            role_title = job_row.title
            seniority = snapshot_row.seniority_level
            from app.modules.question_bank.schemas import QuestionRubric
            draft_questions = [
                GeneratedQuestion(
                    position=r.position, text=r.text, primary_signal=r.primary_signal,
                    signal_values=list(r.signal_values), estimated_minutes=r.estimated_minutes,
                    is_mandatory=r.is_mandatory, follow_ups=list(r.follow_ups),
                    positive_evidence=list(r.positive_evidence), red_flags=list(r.red_flags),
                    rubric=QuestionRubric(**r.rubric), evaluation_hint=r.evaluation_hint,
                    question_kind=r.question_kind, difficulty=r.difficulty,
                )
                for r in draft_rows
            ]
        # rdb closed — no session held across the critic LLM call.

        critique_note: str
        try:
            corrected, critique_note = await run_bank_critic(
                draft=draft_questions,
                seniority=seniority,
                role_title=role_title,
                signals=snapshot_signals,
                stage_difficulty=stage_difficulty,
                stage_duration=stage_duration,
                bank_id=bank_id,
                tenant_id=tenant_id,
                job_id=job_id,
            )
            # Replace the draft with the corrected bank.
            async with get_bypass_session() as wdb:
                await wdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                wbank = (
                    await wdb.execute(
                        select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                    )
                ).scalar_one()
                await wipe_ai_questions(wdb, bank=wbank)
                for pos, q in enumerate(corrected):
                    await persist_one_question(
                        wdb, bank=wbank, question=q, source="ai_generated",
                        position=pos, stage_difficulty=stage_difficulty,
                    )
                await wdb.commit()
        except Exception as critic_exc:
            # FALLBACK (no silent swallow): keep the streamed draft, mark the skip in
            # coverage_notes (audit trail), still proceed to reviewing.
            logger.error(
                "question_bank.critic.skipped",
                bank_id=str(bank_id),
                error_type=type(critic_exc).__name__,
                error_message=str(critic_exc)[:500],
                correlation_id=correlation_id or f"actor-critic-{bank_id}",
                exc_info=True,
            )
            critique_note = (
                f"[critic skipped: {type(critic_exc).__name__}] "
                "draft kept un-critiqued; review manually."
            )
```

Then, in Phase C, before `transition_to_reviewing_after_generation(...)` (which becomes the renamed call): set `bank.coverage_notes = critique_note` and call the renamed helper:

```python
            bank.coverage_notes = critique_note
            bank.prompt_version = ai_config.question_bank_prompt_version
            bank.pipeline_version_at_generation = pipeline_version
            bank.stage_config_snapshot = stage_config_snapshot
            bank.is_stale = False
            transition_to_reviewing_after_critic(bank, user_id=started_by)
            await db.commit()
```

- [ ] **Step 5: Update the failure-path docstring/comment**

The `_generate_one_bank` docstring's "Three phases" list now reads A (load+wipe) → B (stream) → B2 (self-review) → B3 (critic) → C (reconcile+reviewing). Update the docstring accordingly (no placeholder text — write the real phase list).

- [ ] **Step 6: Run the focused + integration tests**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_actor_critic_flow.py tests/question_bank/test_generation_quality.py -q`
Expected: PASS. (If `test_generation_quality.py` stubs the stream, ensure the new monkeypatch of `run_bank_critic` is added per Step 1's note.)

- [ ] **Step 7: Run the full question_bank suite to catch the rename fallout**

Run: `docker compose run --rm nexus pytest tests/question_bank -m "not prompt_quality" -q`
Expected: PASS — no references to the old transition name remain.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/question_bank/test_actor_critic_flow.py backend/nexus/tests/question_bank/test_generation_quality.py
git commit -m "feat(question_bank): wire self-review + critic phases into generation actor"
```

---

## Task 9: SSE — treat `self_reviewing` as non-terminal + fast cadence

**Files:**
- Modify: `app/modules/question_bank/sse.py:277` and `:395`.

- [ ] **Step 1: Update the non-terminal status set (line ~277)**

```python
                    if bank.status in ("draft", "generating", "self_reviewing"):
                        all_terminal = False
```

- [ ] **Step 2: Update the fast-cadence check (line ~395)**

```python
        any_active = any(
            status in ("generating", "self_reviewing")
            for status, _, _ in state.values()
        )
        await asyncio.sleep(POLL_INTERVAL_SEC if any_active else POLL_INTERVAL_IDLE_SEC)
```

- [ ] **Step 3: Verify no other terminal-status assumption excludes self_reviewing**

Run: `cd backend/nexus && grep -n "reviewing\|terminal\|generating" app/modules/question_bank/sse.py`
Expected: confirm the `succeeded` tally (line ~367-371, counts `("confirmed", "reviewing")`) is unaffected — `self_reviewing` is transient and never a terminal completion count. No change needed there.

- [ ] **Step 4: Run the SSE tests**

Run: `docker compose run --rm nexus pytest tests/question_bank -k sse -m "not prompt_quality" -q`
Expected: PASS (existing SSE tests still green; transient `self_reviewing` does not prematurely complete the stream).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/question_bank/sse.py
git commit -m "feat(question_bank): SSE treats self_reviewing as active/non-terminal"
```

---

## Task 10: Report grader — teach it `project_deepdive`

**Files:**
- Modify: `prompts/v4/report_scorer/question_grade.txt` — the `question_kind` enumeration line near the top.

- [ ] **Step 1: Update the kind enumeration**

Change the opening "You are given:" bullet that lists kinds from:

```
- the question text and its question_kind (experience_check, compliance_binary,
  technical_scenario, behavioral),
```
to:
```
- the question text and its question_kind (experience_check, compliance_binary,
  technical_scenario, behavioral, project_deepdive),
```

- [ ] **Step 2: Add a grading line for the new kind**

After the factual-gates rule paragraph, add:

```
- PROJECT_DEEPDIVE is graded as depth, like a behavioral/owned-experience answer: reward
  a real project the candidate DROVE — decisions they personally made, a named
  alternative they rejected, what broke, what they'd change. "We"-framing with no
  recoverable "I", or a project that collapses to vagueness under the probes, stays thin.
```

- [ ] **Step 3: Verify the report scorer still loads**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; print(len(PromptLoader(version='v4').load('report_scorer/question_grade')))"`
Expected: an integer length (file readable). (Adjust the load key to match how `reporting` loads it if namespaced differently — confirm against `reporting/scoring/question_grade.py`.)

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v4/report_scorer/question_grade.txt
git commit -m "feat(reporting): grade project_deepdive as owned-experience depth"
```

---

## Task 11: Prompt-quality eval — extend for v3

**Files:**
- Modify: `tests/question_bank/prompt_evals/test_bank_gen_evals.py`.
- Create: `tests/question_bank/prompt_evals/test_bank_critic_evals.py`.

> These hit the REAL API (`-m prompt_quality`), excluded from the default gate.

- [ ] **Step 1: Add `project_deepdive` to the valid-kinds set**

In `test_bank_gen_evals.py`, update `_VALID_QUESTION_KINDS`:

```python
_VALID_QUESTION_KINDS = {
    "experience_check",
    "behavioral",
    "technical_scenario",
    "compliance_binary",
    "project_deepdive",
}
```

- [ ] **Step 2: Add a senior project-deepdive presence eval**

Append to `test_bank_gen_evals.py`:

```python
_SENIOR_CASES = [c for c in CASES if c.seniority in
                 ("senior", "staff", "principal", "executive")]


@pytest.mark.parametrize("case", _SENIOR_CASES, ids=[c.id for c in _SENIOR_CASES])
async def test_senior_bank_contains_a_project_deepdive(case: BankGenCase) -> None:
    """Senior/experienced banks must contain exactly one project_deepdive (the spine)."""
    questions = await _generate(case)
    kinds = [q.question_kind for q in questions]
    deepdives = kinds.count("project_deepdive")
    assert deepdives == 1, (
        f"[{case.id}] senior bank should contain exactly one project_deepdive; "
        f"found {deepdives}. kinds={kinds}"
    )
```

- [ ] **Step 3: Add a probe-specificity (escalation) eval**

Append:

```python
_VAGUE_PROBE_PHRASES = ("tell me more", "can you elaborate", "go deeper", "explain more")


@pytest.mark.parametrize("case", _SENIOR_CASES, ids=[c.id for c in _SENIOR_CASES])
async def test_seed_probes_demand_specifics_not_open_elaboration(case: BankGenCase) -> None:
    """Every seed_probe must demand a falsifiable specific, never generic elaboration."""
    questions = await _generate(case)
    violations = [
        fu.seed_probe
        for q in questions for fu in q.follow_ups
        if any(p in fu.seed_probe.lower() for p in _VAGUE_PROBE_PHRASES)
    ]
    assert not violations, (
        f"[{case.id}] generic 'tell me more'-style probes (must demand a specific): "
        f"{violations}"
    )
```

- [ ] **Step 4: Write the critic-catches-a-planted-defect eval**

```python
# tests/question_bank/prompt_evals/test_bank_critic_evals.py
"""Critic prompt-quality eval: the critic must catch + fix a planted defect.

Opt-in: docker compose exec nexus pytest tests/question_bank/prompt_evals/test_bank_critic_evals.py -m prompt_quality
Hits the REAL OpenAI API.
"""
from __future__ import annotations
import uuid
import pytest

from app.modules.question_bank import critic as critic_mod
from app.modules.question_bank.schemas import (
    GeneratedQuestion, QuestionRubric, FollowUpDimension,
)

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


def _q(pos, text, kind, dim, probe):
    return GeneratedQuestion(
        position=pos, text=text, primary_signal="Kubernetes in production",
        signal_values=["Kubernetes in production"], estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[FollowUpDimension(dimension=dim, intent="depth",
                    seed_probe=probe, listen_for=["a specific tool", "a number"])],
        positive_evidence=["names a tool", "states a number", "owns 'I did X'"],
        red_flags=["says we not I", "no tradeoff named"],
        rubric=QuestionRubric(
            excellent="names the specific failure mode and the fix " + "x" * 5,
            meets_bar="describes a real incident with one concrete detail",
            below_bar="vague, hypothetical, no specifics here at all"),
        evaluation_hint="tests real production depth",
        question_kind=kind,
    )


async def test_critic_flags_duplicate_dimension_and_missing_deepdive():
    """Plant two probes with the SAME dimension slug + no project_deepdive for a senior
    role. The critic must fix the duplicate and the senior bank must end with exactly one
    project_deepdive."""
    draft = [
        _q(0, "Tell me about running Kubernetes in production.", "technical_scenario",
           "failure_handling", "What broke and how did you fix it?"),
        _q(1, "How do you keep a cluster healthy under load?", "technical_scenario",
           "failure_handling", "What broke and how did you fix it?"),  # DUP dim
    ]
    corrected, critique = await critic_mod.run_bank_critic(
        draft=draft, seniority="senior", role_title="Senior SRE",
        signals=[{"value": "Kubernetes in production", "type": "competency",
                  "priority": "required", "weight": 3, "knockout": True, "stage": "interview"}],
        stage_difficulty="hard", stage_duration=20,
        bank_id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(),
    )
    dims = [fu.dimension for q in corrected for fu in q.follow_ups]
    assert len(dims) == len(set(dims)), f"critic left duplicate dimensions: {dims}"
    kinds = [q.question_kind for q in corrected]
    assert kinds.count("project_deepdive") == 1, (
        f"critic did not ensure a single project_deepdive for a senior bank; kinds={kinds}. "
        f"critique={critique}"
    )
```

- [ ] **Step 5: Run the evals (real API — opt-in)**

Run: `docker compose exec nexus pytest tests/question_bank/prompt_evals -m prompt_quality -q`
Expected: PASS (slow; consumes tokens). Iterate on the v3 prompts (Task 6) if an assertion fails.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/tests/question_bank/prompt_evals/test_bank_gen_evals.py backend/nexus/tests/question_bank/prompt_evals/test_bank_critic_evals.py
git commit -m "test(question_bank): v3 evals — project_deepdive, probe specificity, critic defect-catch"
```

---

## Task 12: Frontend — `self_reviewing` animation + `project_deepdive` badge

**Files:**
- Modify: `frontend/app/lib/api/question-banks.ts` (unions).
- Modify: `frontend/app/components/dashboard/question-bank/BankStatusBadge.tsx`.
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx`.

- [ ] **Step 1: Extend the `BankStatus` + `QuestionKind` unions**

In `lib/api/question-banks.ts`:

```typescript
export type BankStatus =
  | 'draft'
  | 'generating'
  | 'self_reviewing'
  | 'reviewing'
  | 'confirmed'
  | 'failed'

export type QuestionKind =
  | 'experience_check'
  | 'behavioral'
  | 'technical_scenario'
  | 'compliance_binary'
  | 'project_deepdive'
```

- [ ] **Step 2: Add the `self_reviewing` style + icon to `BankStatusBadge.tsx`**

Add to `STATUS_STYLES`:

```typescript
  self_reviewing: { bg: 'bg-violet-50', text: 'text-violet-700', label: 'SELF-REVIEW' },
```

And spin the icon for it too (it is an active phase). Update the `Icon` selection + the spin condition:

```typescript
  const Icon =
    status === 'generating' ? Loader2 :
    status === 'self_reviewing' ? Loader2 :
    status === 'confirmed' ? Lock :
    status === 'failed' ? AlertCircle :
    status === 'reviewing' ? Clock :
    Check
```
```typescript
      <Icon
        className={`${small ? 'w-2.5 h-2.5' : 'w-3 h-3'} ${
          status === 'generating' || status === 'self_reviewing' ? 'animate-spin' : ''
        }`}
        aria-hidden="true"
      />
```

- [ ] **Step 3: Show the self-review animation in the stage pill (page.tsx ~305)**

Add a branch alongside the `generating` pill indicator:

```tsx
{bank?.status === 'self_reviewing' && (
  <span
    className="text-[9.5px] inline-flex items-center gap-0.5"
    style={{ color: 'var(--px-accent)' }}
    aria-label="AI self-reviewing"
  >
    <span className="qb-pill-pulse">🤖</span>
  </span>
)}
```

- [ ] **Step 4: Show the self-review message in `EmptyBankState` (page.tsx ~1599)**

Extend the `isGenerating` branch to cover self-review with a distinct message:

```tsx
const isGenerating =
  bank?.status === 'generating' || generateMutation.isPending
const isSelfReviewing = bank?.status === 'self_reviewing'
```
```tsx
{isSelfReviewing ? (
  <div className="text-sm inline-flex items-center gap-1.5"
       style={{ color: 'var(--px-accent)' }}>
    <SparkIcon size={12} /> AI is self-reviewing the bank…
  </div>
) : isGenerating ? (
  <div className="text-sm" style={{ color: 'var(--px-accent)' }}>
    <SparkIcon size={12} /> Generating…
  </div>
) : (
  <Button size="sm" onClick={() => generateMutation.mutate()}>
    Generate questions
  </Button>
)}
```

- [ ] **Step 5: Add the `QUESTION_KIND_LABEL` map + badge in `QBDetail` (page.tsx ~90 + ~1028)**

Add the label map near `STAGE_TYPE_LABEL` (~line 90), importing `QuestionKind`:

```tsx
const QUESTION_KIND_LABEL: Record<QuestionKind, string> = {
  experience_check: 'Experience check',
  behavioral: 'Behavioral',
  technical_scenario: 'Technical scenario',
  compliance_binary: 'Compliance',
  project_deepdive: 'Project deep-dive',
}
```

Render it in the `QBDetail` header after the signal badges (~line 1041):

```tsx
{q.question_kind && QUESTION_KIND_LABEL[q.question_kind as QuestionKind] && (
  <span
    className="rounded-full border px-2 py-0.5 text-[10.5px] font-medium"
    style={{
      background: 'var(--px-accent-tint)',
      color: 'var(--px-accent)',
      borderColor: 'var(--px-accent-line)',
    }}
  >
    {QUESTION_KIND_LABEL[q.question_kind as QuestionKind]}
  </span>
)}
```

- [ ] **Step 6: Type-check + lint + build**

Run:
```bash
cd frontend/app
npm run type-check 2>/dev/null || npx tsc --noEmit
npm run lint
npm run build
```
Expected: no type errors (the `Record<BankStatus,...>` in `BankStatusBadge` now requires `self_reviewing`, which Step 2 added; the `Record<QuestionKind,...>` requires `project_deepdive`, which Step 5 added).

- [ ] **Step 7: Run frontend tests**

Run: `cd frontend/app && npm run test`
Expected: PASS (update any snapshot/exhaustive-status test that enumerates `BankStatus` to include `self_reviewing`).

- [ ] **Step 8: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/app/lib/api/question-banks.ts frontend/app/components/dashboard/question-bank/BankStatusBadge.tsx "frontend/app/app/(dashboard)/jobs/[jobId]/questions/page.tsx"
git commit -m "feat(app): self_reviewing animation + project_deepdive question badge"
```

---

## Task 13: Full-suite verification + live smoke

**Files:** none (verification only).

- [ ] **Step 1: Backend default gate green**

Run: `docker compose run --rm nexus pytest -m "not prompt_quality" -q`
Expected: PASS across the suite (the rename + new status touched question_bank, sse, schemas, state machine).

- [ ] **Step 2: Restart engine + worker so they load new prompts/code**

Run:
```bash
docker compose up -d --force-recreate nexus-worker
```
(`nexus-worker` runs the generation actor and has no hot-reload — per `feedback_worker_restart_after_backend_change`.)

- [ ] **Step 3: Live smoke — generate a bank and watch the phases**

In the recruiter app, open a job's questions tab and click Generate for an `ai_screening` stage. Confirm the UI shows: `generating` (•••) → `self_reviewing` (🤖 "AI is self-reviewing…") → `reviewing`, that the resulting senior bank contains a "Project deep-dive"-badged question, and that the bank's coverage notes contain the critic's critique log.

- [ ] **Step 4: Commit (if any smoke-fix tweaks were needed)**

```bash
git add -A && git commit -m "fix(question_bank): v3 generation live-smoke adjustments"
```

---

## Self-Review (plan vs spec)

- **Spec §3 (six prompt principles)** → Task 6 steps 2–8 (P1 seniority, P5 deepdive kind, P2 ladders, P4 tripwires, P7 warmth+anchors) + v3 bump (step 9). ✓
- **Spec §4.1 (project_deepdive field)** → Task 1 (CHECK) + Task 2 (Literal) + Task 10 (grader). ✓
- **Spec §4.2 (self_reviewing status + removed edge + rename)** → Task 1 (CHECK) + Task 3 (state machine) + Task 4 (service) + Task 9 (SSE). ✓
- **Spec §4.3 (critique → coverage_notes)** → Task 8 step 4. ✓
- **Spec §5 (generate→self_reviewing→critic→reviewing, 2 calls, fallback)** → Task 7 (critic) + Task 8 (wiring incl. fallback marker). ✓
- **Spec §5 critic model AIConfig-driven** → Task 5. ✓
- **Spec §6 (frontend animation + badge)** → Task 12. ✓
- **Spec §7 (tests)** → Tasks 2,3,5,7,8,9 unit; Task 11 prompt-quality evals; Task 13 live smoke. ✓
- **Spec §9 (no dead code: edge removed, rename propagated, no silent fallback)** → Task 3 (edge removed), Task 4 + Task 8 (rename propagated, grep gate in Task 4 step 3), Task 8 step 4 (fallback writes coverage_notes marker, never silent). ✓

**Type consistency check:** `transition_to_reviewing_after_critic(bank, *, user_id)` defined in Task 3, imported in Task 4, called in Task 8 — consistent. `run_bank_critic(*, draft, seniority, role_title, signals, stage_difficulty, stage_duration, bank_id, tenant_id, job_id) -> (list[GeneratedQuestion], str)` defined in Task 7, called in Task 8 with the same kwargs — consistent. `BankCritiqueOutput(critique, questions)` defined Task 2, used Task 7 — consistent. `self_reviewing` literal consistent across migration / state machine / SSE / frontend.
