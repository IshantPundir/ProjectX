# Follow-ups as Governed Dimensions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make interview probing deterministic — each follow-up becomes a governed *dimension* (intent + seed + listen-for) that fires at most once per thread under a hard cap — and make the generator author distinct, non-redundant dimensions.

**Architecture:** `follow_ups: list[str]` becomes `list[FollowUpDimension]` (a `{dimension, intent, seed_probe, listen_for}` object), defined independently in the two bounded contexts (`question_bank` for generation, `interview_runtime` for the engine wire contract) with the DB JSONB column as the contract between them. A one-time Alembic data backfill rewrites existing rows so there is a single shape at runtime. The engine binds each probe to a declared `dimension` slug, tracks a per-thread fired-dimension ledger in the driver, and a deterministic policy gate (`coerce_probe_dimension`) enforces fire-once + a hard probe cap. The generator emits the richer shape and dedups dimensions within and across its two phase calls.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async + asyncpg, Alembic, `instructor` (OpenAI structured output), pytest. The engine orchestration core (`driver.py`, `brain/`, `loop.py`) is LiveKit-free and unit-testable with fakes.

**Phasing:** Phase 1 (Tasks 1–11) = engine + backfill, shippable on its own. Phase 2 (Tasks 12–17) = generator quality. The backfill in Task 2 guarantees a single shape so the phases don't interleave shapes.

**Test commands (run inside the long-running container — avoids the pytest-cov livekit segfault):**
```bash
docker compose up -d nexus
docker compose exec -T nexus pytest <path> -v
```
Migrations: `docker compose run --rm nexus alembic upgrade head` / `... downgrade -1`.

---

## File Structure

**Phase 1 — engine + backfill**
- `app/modules/interview_runtime/schemas.py` — define `FollowUpDimension`; `QuestionConfig.follow_ups: list[FollowUpDimension]`.
- `app/modules/interview_runtime/service.py` — `build_session_config` maps DB JSON → `FollowUpDimension`.
- `migrations/versions/0054_followups_governed_dimensions.py` — data backfill `list[str]` → object (+ rollback).
- `app/modules/interview_engine/contracts.py` — engine `FollowUpDimension`; `BankQuestionIndex.follow_ups`/`ActiveQuestionRubric.follow_ups` → objects; `ActiveQuestionRubric.probes_used` → `fired_dimensions: list[str]`; `BrainTurnOutput.probe_index` → `probe_dimension: str | None`; `BrainDecision.probe_index` → `probe_dimension: str | None`.
- `app/modules/interview_engine/brain/policy.py` — `coerce_probe_dimension` replaces `coerce_probe_index` (fire-once + hard cap).
- `app/modules/interview_engine/brain/input_builder.py` — render dimension objects + `fired_dimensions`; `active_question_rubric` signature.
- `app/modules/interview_engine/brain/service.py` — `_resolve_probe` dimension-based; `decide()` records `probe_dimension`.
- `app/modules/interview_engine/driver.py` — `_fired_dimensions` ledger; pass it into the rubric; record `probe_dimension`; E2 floor-pointer guard.
- `app/config.py` — `engine_probe_cap_per_thread: int = 2`.
- `prompts/v4/engine/brain.system.txt`, `prompts/v4/engine/mouth/{clarify,repeat}.txt` — dimension probing, E1/E3 boundaries.

**Phase 2 — generator**
- `app/modules/question_bank/schemas.py` — `FollowUpDimension`; `GeneratedQuestion.follow_ups: list[FollowUpDimension]`; request/response shapes.
- `app/modules/question_bank/actors.py` — persist the new JSON shape.
- `prompts/v2/question_bank_common.txt`, `prompts/v2/question_bank_ai_screening*.txt`, `prompts/v2/question_bank_phone_screen.txt` — dimension shape + within/cross-phase dedup.
- (`refine.py` unchanged — refine/draft proposals carry no follow-ups; Task 14 only guards that contract.)

---

# PHASE 1 — Engine + Backfill

## Task 1: Define `FollowUpDimension` in the engine wire contract

**Files:**
- Modify: `app/modules/interview_runtime/schemas.py`
- Test: `tests/interview_runtime/test_followup_dimension_schema.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_runtime/test_followup_dimension_schema.py
import pytest
from pydantic import ValidationError

from app.modules.interview_runtime.schemas import FollowUpDimension, QuestionConfig


def test_followup_dimension_valid():
    d = FollowUpDimension(
        dimension="validate_impact",
        intent="Did they verify policy impact before changing it?",
        seed_probe="How would you validate impact before adjusting a policy?",
        listen_for=["pilot/canary group", "rollback readiness"],
    )
    assert d.dimension == "validate_impact"
    assert d.listen_for == ["pilot/canary group", "rollback readiness"]


def test_followup_dimension_listen_for_defaults_empty():
    d = FollowUpDimension(
        dimension="d1", intent="i", seed_probe="p",
    )
    assert d.listen_for == []


def test_followup_dimension_rejects_blank_dimension():
    with pytest.raises(ValidationError):
        FollowUpDimension(dimension="", intent="i", seed_probe="p")


def test_question_config_follow_ups_are_dimensions():
    q = QuestionConfig(
        id="q1", position=0, text="A real question here?",
        signal_values=["sig"], estimated_minutes=2.0, is_mandatory=False,
        follow_ups=[
            {"dimension": "d1", "intent": "i1", "seed_probe": "p1", "listen_for": ["x"]},
        ],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric={"excellent": "e" * 20, "meets_bar": "m" * 20, "below_bar": "b" * 20},
        evaluation_hint="h" * 12, question_kind="technical_scenario",
        primary_signal="sig", difficulty="medium",
    )
    assert q.follow_ups[0].dimension == "d1"
    assert isinstance(q.follow_ups[0], FollowUpDimension)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_runtime/test_followup_dimension_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'FollowUpDimension'`.

- [ ] **Step 3: Add `FollowUpDimension` and change `QuestionConfig.follow_ups`**

In `app/modules/interview_runtime/schemas.py`, add the model near `QuestionRubric` (around line 50):

```python
class FollowUpDimension(BaseModel):
    """One governed probe dimension — a sub-template the engine composes WITHIN.

    Mirrors question_bank.schemas.FollowUpDimension (two bounded contexts; the
    stage_questions.follow_ups JSONB column is the contract between them).
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(
        ..., min_length=1,
        description="Stable slug; the per-thread fire-once ledger key (e.g. 'validate_impact').",
    )
    intent: str = Field(
        ..., min_length=1,
        description="WHAT this probe verifies — the brain composes a natural probe within this.",
    )
    seed_probe: str = Field(
        ..., min_length=1,
        description="Pre-authored spoken seed (the legacy follow-up string).",
    )
    listen_for: list[str] = Field(
        default_factory=list,
        description="Observable specifics that satisfy this dimension (the brain targets these).",
    )
```

Then change the `QuestionConfig.follow_ups` field (currently around line 90):

```python
    follow_ups: list[FollowUpDimension] = Field(
        default_factory=list,
        description="Governed probe dimensions for this question (0-3).",
    )
```

(`BaseModel`, `ConfigDict`, `Field` are already imported at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_runtime/test_followup_dimension_schema.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_runtime/schemas.py tests/interview_runtime/test_followup_dimension_schema.py
git commit -m "feat(engine): FollowUpDimension wire contract + QuestionConfig.follow_ups objects"
```

---

## Task 2: Alembic data backfill (`list[str]` → object shape) + rollback

**Files:**
- Create: `migrations/versions/0054_followups_governed_dimensions.py`
- Test: `tests/question_bank/test_followups_backfill.py` (create)

The migration head today is `0053_session_reels`; this revision is `0054`, `down_revision="0053_session_reels"`. It rewrites `stage_questions.follow_ups` in place (JSONB), idempotently (skip rows already in object shape).

The pure transform helpers live in a **normal importable module** (`app/modules/question_bank/followups_migration.py`), NOT in `migrations/versions/` — that directory is not a clean Python package (`migrations/__init__.py` is absent) and the migration filename starts with a digit (not importable by name). The Alembic migration and the unit test both import the helpers from `app.modules.question_bank.followups_migration`.

- [ ] **Step 1: Write the failing test (pure transform helpers)**

```python
# tests/question_bank/test_followups_backfill.py
from app.modules.question_bank import followups_migration as bf


def test_slug_basic():
    assert bf.slug("How would you validate impact before adjusting a policy?") \
        == "how_would_you_validate_impact_before_adjusting_a_policy"


def test_slug_truncates_and_collapses():
    s = bf.slug("A/B  test!!  rollout — safely")
    assert s == "a_b_test_rollout_safely"
    assert len(s) <= 60


def test_upgrade_wraps_strings():
    out = bf.upgrade_value(["Probe one?", "Probe two?"])
    assert out == [
        {"dimension": "probe_one", "intent": "Probe one?", "seed_probe": "Probe one?", "listen_for": []},
        {"dimension": "probe_two", "intent": "Probe two?", "seed_probe": "Probe two?", "listen_for": []},
    ]


def test_upgrade_dedups_duplicate_slugs():
    out = bf.upgrade_value(["Same probe", "Same probe"])
    assert out[0]["dimension"] == "same_probe"
    assert out[1]["dimension"] == "same_probe_2"


def test_upgrade_is_idempotent_on_objects():
    already = [{"dimension": "d", "intent": "i", "seed_probe": "p", "listen_for": []}]
    assert bf.upgrade_value(already) == already


def test_downgrade_takes_seed_probe():
    objs = [{"dimension": "d", "intent": "i", "seed_probe": "P1", "listen_for": ["x"]}]
    assert bf.downgrade_value(objs) == ["P1"]


def test_downgrade_is_idempotent_on_strings():
    assert bf.downgrade_value(["P1", "P2"]) == ["P1", "P2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_followups_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: ... followups_migration`.

- [ ] **Step 3: Write the transform helpers + migration**

Create `app/modules/question_bank/followups_migration.py` (pure helpers, importable by both the migration and tests):

```python
"""Pure transform helpers for the 0054 follow-ups backfill (importable + unit-tested)."""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(text: str, *, max_len: int = 60) -> str:
    s = _NON_ALNUM.sub("_", (text or "").strip().lower()).strip("_")
    return s[:max_len].rstrip("_")


def _is_object_shape(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(v, dict) and "dimension" in v and "seed_probe" in v for v in value)
    )


def upgrade_value(follow_ups: object) -> list[dict]:
    """list[str] -> list[{dimension, intent, seed_probe, listen_for}]. Idempotent."""
    if not isinstance(follow_ups, list):
        return []
    if _is_object_shape(follow_ups):
        return follow_ups  # already migrated
    out: list[dict] = []
    seen: dict[str, int] = {}
    for item in follow_ups:
        text = item if isinstance(item, str) else str(item)
        base = slug(text) or "probe"
        seen[base] = seen.get(base, 0) + 1
        dim = base if seen[base] == 1 else f"{base}_{seen[base]}"
        out.append({"dimension": dim, "intent": text, "seed_probe": text, "listen_for": []})
    return out


def downgrade_value(follow_ups: object) -> list[str]:
    """object shape -> list[str] (seed_probe). Idempotent on plain strings."""
    if not isinstance(follow_ups, list):
        return []
    if not _is_object_shape(follow_ups):
        return [x if isinstance(x, str) else str(x) for x in follow_ups]
    return [str(v.get("seed_probe", "")) for v in follow_ups]
```

Create `migrations/versions/0054_followups_governed_dimensions.py`:

```python
"""followups governed dimensions backfill

Revision ID: 0054_followups_governed_dimensions
Revises: 0053_session_reels
Create Date: 2026-06-11
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.modules.question_bank.followups_migration import downgrade_value, upgrade_value

revision = "0054_followups_governed_dimensions"
down_revision = "0053_session_reels"
branch_labels = None
depends_on = None


def _rewrite(transform) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, follow_ups FROM stage_questions")).fetchall()
    for row_id, follow_ups in rows:
        value = follow_ups if isinstance(follow_ups, (list, dict)) else json.loads(follow_ups or "[]")
        new_value = transform(value)
        conn.execute(
            sa.text("UPDATE stage_questions SET follow_ups = CAST(:fu AS JSONB) WHERE id = :id"),
            {"fu": json.dumps(new_value), "id": row_id},
        )


def upgrade() -> None:
    _rewrite(upgrade_value)


def downgrade() -> None:
    _rewrite(downgrade_value)
```

- [ ] **Step 4: Run helper tests + apply the migration round-trip**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_followups_backfill.py -v`
Expected: PASS (7 passed).

Run: `docker compose run --rm nexus alembic upgrade head`
Expected: applies `0054`; no error.

Verify the EMM bank converted (its `follow_ups` are now objects):
```bash
docker compose exec -T nexus python -c "
import asyncio, os, json, asyncpg
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    r=await c.fetchval('SELECT follow_ups FROM stage_questions WHERE bank_id=\$1 AND position=3','7b67e66a-ddb8-475a-ba88-2915a3db6f4e')
    print(json.loads(r) if isinstance(r,str) else r); await c.close()
asyncio.run(m())"
```
Expected: a list of objects, each with `dimension`/`intent`/`seed_probe`/`listen_for`.

Confirm rollback works, then re-apply:
```bash
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0054_followups_governed_dimensions.py app/modules/question_bank/followups_migration.py tests/question_bank/test_followups_backfill.py
git commit -m "feat(db): 0054 backfill stage_questions.follow_ups to governed-dimension objects"
```

---

> **Cohesive-refactor note (Tasks 3–10).** The contract rename in Task 3 (`probe_index`→`probe_dimension`, `probes_used`→`fired_dimensions`, `follow_ups`→objects) ripples through `policy.py`, `input_builder.py`, `service.py`, and `driver.py`. Each task's OWN new test passes, but the **whole engine suite goes red mid-sequence** and is green again only at **Task 11**. This is expected for a cross-cutting contract change — do not try to keep the global suite green between Tasks 3 and 10; keep each task's targeted test green and land them in order.

## Task 3: Engine contracts — dimension objects, `probe_dimension`, `fired_dimensions`

**Files:**
- Modify: `app/modules/interview_engine/contracts.py`
- Test: `tests/interview_engine/test_contracts_dimensions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_contracts_dimensions.py
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric, BankQuestionIndex, BrainTurnOutput, BrainMove, FollowUpDimension,
)


def _dim(slug="d1"):
    return FollowUpDimension(dimension=slug, intent="i", seed_probe="p", listen_for=["x"])


def test_bank_index_holds_dimensions():
    idx = BankQuestionIndex(
        question_id="q1", primary_signal="s", signals=["s"], kind="technical_scenario",
        difficulty="medium", is_mandatory=False, tier="core", text="t", follow_ups=[_dim()],
    )
    assert idx.follow_ups[0].dimension == "d1"


def test_active_rubric_has_fired_dimensions_not_probes_used():
    r = ActiveQuestionRubric(
        question_id="q1", text="t", excellent="e", meets_bar="m", below_bar="b",
        positive_evidence=["a"], red_flags=["r"], evaluation_hint="h",
        follow_ups=[_dim()], fired_dimensions=["d0"],
    )
    assert r.fired_dimensions == ["d0"]
    assert not hasattr(r, "probes_used")


def test_brain_output_uses_probe_dimension():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="validate_impact")
    assert out.probe_dimension == "validate_impact"
    assert not hasattr(out, "probe_index")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_contracts_dimensions.py -v`
Expected: FAIL — `ImportError: cannot import name 'FollowUpDimension'`.

- [ ] **Step 3: Edit `contracts.py`**

Add the engine-side `FollowUpDimension` right after the shared-vocabulary imports (after line 37):

```python
class FollowUpDimension(BaseModel):
    """A governed probe dimension (engine copy; mirrors interview_runtime.schemas)."""
    dimension: str
    intent: str
    seed_probe: str
    listen_for: list[str] = Field(default_factory=list)
```

In `BrainTurnOutput`, replace the `probe_index` field (lines 101–109) with:

```python
    probe_dimension: str | None = Field(
        default=None,
        description="For `probe`: the `dimension` slug of the ACTIVE question's follow-up this probe "
                    "serves. The brain composes `composed_say` WITHIN that dimension's intent. The engine "
                    "fires each dimension at most once per thread (the driver's ledger) and force-advances "
                    "at the probe cap; an already-fired or unknown slug is coerced to an unfired one, or to "
                    "`ask` when none remain. None → let the engine pick the next unfired dimension.",
    )
```

In `BankQuestionIndex` (line 177) change:
```python
    follow_ups: list[FollowUpDimension]   # the pre-written probe dimensions
```

In `ActiveQuestionRubric` change `follow_ups` (line 208) and replace `probes_used` (lines 209–212):
```python
    follow_ups: list[FollowUpDimension]
    fired_dimensions: list[str] = Field(
        default_factory=list,
        description="dimension slugs already fired on this thread — fire-once + cap input.",
    )
```

In `BrainDecision`, replace the `probe_index` field (lines 395–403) with:
```python
    probe_dimension: str | None = Field(
        default=None,
        description="When directive.act == probe: the coerced dimension slug the brain served "
                    "(valid + unfired). The SessionDriver appends it to the thread's fired_dimensions "
                    "ledger. None for non-probe acts.",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_contracts_dimensions.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/contracts.py tests/interview_engine/test_contracts_dimensions.py
git commit -m "feat(engine): contracts carry FollowUpDimension + probe_dimension + fired_dimensions"
```

---

## Task 4: Policy gate — `coerce_probe_dimension` (fire-once + hard cap)

**Files:**
- Modify: `app/modules/interview_engine/brain/policy.py`
- Test: `tests/interview_engine/test_policy_probe_dimension.py` (create)

Replaces `coerce_probe_index`. Pure, never raises. Returns a valid **unfired** dimension slug, or `None` when all dimensions are fired **or** the per-thread cap is reached.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_policy_probe_dimension.py
from app.modules.interview_engine.brain.policy import coerce_probe_dimension
from app.modules.interview_engine.contracts import FollowUpDimension


def _dims(*slugs):
    return [FollowUpDimension(dimension=s, intent="i", seed_probe="p") for s in slugs]


def test_returns_proposed_when_valid_and_unfired():
    out = coerce_probe_dimension("b", follow_ups=_dims("a", "b", "c"), fired=["a"], cap=2)
    assert out == "b"


def test_coerces_fired_proposal_to_first_unfired():
    out = coerce_probe_dimension("a", follow_ups=_dims("a", "b", "c"), fired=["a"], cap=2)
    assert out == "b"


def test_coerces_unknown_slug_to_first_unfired():
    out = coerce_probe_dimension("zzz", follow_ups=_dims("a", "b"), fired=[], cap=2)
    assert out == "a"


def test_none_when_all_fired():
    assert coerce_probe_dimension("a", follow_ups=_dims("a", "b"), fired=["a", "b"], cap=5) is None


def test_none_when_cap_reached_even_if_unfired_remain():
    assert coerce_probe_dimension("c", follow_ups=_dims("a", "b", "c"), fired=["a", "b"], cap=2) is None


def test_none_when_no_follow_ups():
    assert coerce_probe_dimension("a", follow_ups=[], fired=[], cap=2) is None


def test_never_raises_on_garbage():
    assert coerce_probe_dimension(None, follow_ups=None, fired=None, cap=2) is None  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_policy_probe_dimension.py -v`
Expected: FAIL — `ImportError: cannot import name 'coerce_probe_dimension'`.

- [ ] **Step 3: Replace `coerce_probe_index` with `coerce_probe_dimension`**

In `app/modules/interview_engine/brain/policy.py`, replace the whole `coerce_probe_index` function (lines 265–312) with:

```python
def coerce_probe_dimension(
    probe_dimension: str | None,
    *,
    follow_ups: list,            # list[FollowUpDimension]
    fired: list[str],
    cap: int,
) -> str | None:
    """Coerce the brain's probe_dimension to a valid, UNFIRED dimension slug.

    Returns a slug that exists in follow_ups and is not in `fired`. Returns None when
    every dimension is fired OR the per-thread probe cap is reached (→ caller advances).
    Never raises.
    """
    try:
        if not follow_ups:
            return None
        fired_set: set[str] = set(fired or [])
        # Hard cap: total probes on this thread is bounded regardless of remaining dims.
        if len(fired_set) >= cap:
            return None
        slugs = [d.dimension for d in follow_ups]
        available = [s for s in slugs if s not in fired_set]
        if not available:
            return None
        if probe_dimension in available:
            return probe_dimension
        return available[0]
    except Exception:  # pragma: no cover — defensive
        return None
```

Update the module docstring item 3 (lines 23–26) to describe `coerce_probe_dimension` (fire-once + cap) instead of `coerce_probe_index`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_policy_probe_dimension.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/brain/policy.py tests/interview_engine/test_policy_probe_dimension.py
git commit -m "feat(engine): coerce_probe_dimension gate (fire-once + hard probe cap)"
```

---

## Task 5: Config knob — `engine_probe_cap_per_thread`

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config_probe_cap.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_probe_cap.py
from app.ai.config import ai_config


def test_probe_cap_default_is_two():
    assert ai_config.engine_probe_cap_per_thread == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/test_config_probe_cap.py -v`
Expected: FAIL — `AttributeError: ... engine_probe_cap_per_thread`.

- [ ] **Step 3: Add the field**

In `app/config.py`, in the engine turn-handling block (near `engine_endpointing_max_delay_s`), add:

```python
    engine_probe_cap_per_thread: int = 2
    """Max probes fired on one question thread before the engine force-advances (deterministic
    anti-grind). 1-2 probes/question typical; the brain may advance earlier on primary_signal."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/test_config_probe_cap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config_probe_cap.py
git commit -m "feat(engine): engine_probe_cap_per_thread config (default 2)"
```

---

## Task 6: Input builder — render dimensions + `fired_dimensions`

**Files:**
- Modify: `app/modules/interview_engine/brain/input_builder.py`
- Test: `tests/interview_engine/test_input_builder_dimensions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_input_builder_dimensions.py
from app.modules.interview_engine.brain.input_builder import active_question_rubric, render_suffix, build_turn_input, CoverageProjection
from app.modules.interview_engine.contracts import BudgetPhase, FollowUpDimension
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q():
    return QuestionConfig(
        id="q1", position=0, text="How would you assess a messy tenant?",
        signal_values=["intune_admin"], estimated_minutes=3.0, is_mandatory=True,
        follow_ups=[FollowUpDimension(dimension="validate_impact", intent="verify impact",
                                      seed_probe="How would you validate impact?",
                                      listen_for=["pilot group", "rollback"])],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="e" * 20, meets_bar="m" * 20, below_bar="b" * 20),
        evaluation_hint="h" * 12, question_kind="technical_scenario",
        primary_signal="intune_admin", difficulty="medium",
    )


def test_active_rubric_carries_dimensions_and_fired():
    r = active_question_rubric(_q(), fired_dimensions=["validate_impact"])
    assert r.follow_ups[0].dimension == "validate_impact"
    assert r.fired_dimensions == ["validate_impact"]


def test_render_suffix_shows_dimension_intent_listen_for():
    r = active_question_rubric(_q(), fired_dimensions=[])
    ti = build_turn_input(
        turn_ref="t1", active_question=r, on_the_floor="...", candidate_utterance="hi",
        thread_turn_count=1, projection=CoverageProjection(), all_specs=[],
        transcript_window=[], budget_phase=BudgetPhase.on_track,
    )
    content = render_suffix(ti)[0]["content"]
    assert "validate_impact" in content
    assert "verify impact" in content        # intent
    assert "pilot group" in content          # listen_for
    assert "fired_dimensions" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_input_builder_dimensions.py -v`
Expected: FAIL — `active_question_rubric() got an unexpected keyword argument 'fired_dimensions'`.

- [ ] **Step 3: Edit `input_builder.py`**

In `build_session_context` change the `BankQuestionIndex(... follow_ups=...)` line (line 130) to pass dimension objects directly:

```python
                follow_ups=list(q.follow_ups),  # already list[FollowUpDimension]
```

Replace `active_question_rubric` (lines 148–171):

```python
def active_question_rubric(
    q: QuestionConfig,
    *,
    fired_dimensions: list[str],
) -> ActiveQuestionRubric:
    """Map the active question's full rubric into the per-turn suffix object."""
    return ActiveQuestionRubric(
        question_id=q.id,
        text=q.text,
        excellent=q.rubric.excellent,
        meets_bar=q.rubric.meets_bar,
        below_bar=q.rubric.below_bar,
        positive_evidence=list(q.positive_evidence),
        red_flags=list(q.red_flags),
        evaluation_hint=q.evaluation_hint,
        follow_ups=list(q.follow_ups),
        fired_dimensions=list(fired_dimensions),
    )
```

In `render_suffix`, replace the `rubric_block` `follow_ups`/`probes_used` lines (lines 395–396) so dimensions render with intent + listen_for, and `fired_dimensions` shows:

```python
    follow_ups_rendered = json.dumps(
        [
            {"dimension": d.dimension, "intent": d.intent,
             "seed_probe": d.seed_probe, "listen_for": d.listen_for}
            for d in r.follow_ups
        ],
        ensure_ascii=False,
    )
    rubric_block = (
        f"## Active Question Rubric\n"
        f"question_id: {r.question_id}\n"
        f"text: {r.text}\n"
        f"excellent: {r.excellent}\n"
        f"meets_bar: {r.meets_bar}\n"
        f"below_bar: {r.below_bar}\n"
        f"positive_evidence: {json.dumps(r.positive_evidence, ensure_ascii=False)}\n"
        f"red_flags: {json.dumps(r.red_flags, ensure_ascii=False)}\n"
        f"evaluation_hint: {r.evaluation_hint}\n"
        f"follow_up_dimensions: {follow_ups_rendered}\n"
        f"fired_dimensions: {json.dumps(r.fired_dimensions, ensure_ascii=False)}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_input_builder_dimensions.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/brain/input_builder.py tests/interview_engine/test_input_builder_dimensions.py
git commit -m "feat(engine): brain suffix renders follow-up dimensions + fired_dimensions"
```

---

## Task 7: Brain service — dimension-based probe resolution

**Files:**
- Modify: `app/modules/interview_engine/brain/service.py`
- Test: `tests/interview_engine/test_service_probe_dimension.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_service_probe_dimension.py
import pytest

from app.modules.interview_engine.brain.input_builder import CoverageProjection, active_question_rubric, build_turn_input
from app.modules.interview_engine.brain.resolver import ResolverQuestion, budget_config_from_ai_config
from app.modules.interview_engine.brain.service import ControlPlane
from app.modules.interview_engine.contracts import (
    BrainMove, BrainSessionContext, BrainTurnOutput, BudgetPhase, DirectiveAct, FollowUpDimension, SignalSpec,
)
from app.modules.interview_runtime.evidence import SignalPriority, SignalType
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q():
    return QuestionConfig(
        id="q1", position=0, text="Assess a messy tenant?", signal_values=["s"],
        estimated_minutes=3.0, is_mandatory=True,
        follow_ups=[
            FollowUpDimension(dimension="validate_impact", intent="verify impact", seed_probe="seed A", listen_for=[]),
            FollowUpDimension(dimension="stage_safely", intent="stage safely", seed_probe="seed B", listen_for=[]),
        ],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="e" * 20, meets_bar="m" * 20, below_bar="b" * 20),
        evaluation_hint="h" * 12, question_kind="technical_scenario", primary_signal="s", difficulty="medium",
    )


def _cp(monkeypatch_output):
    ctx = BrainSessionContext(job_title="t", seniority_level="mid", role_summary="r", hiring_bar="hb",
                              signals=[SignalSpec(signal="s", signal_type=SignalType.competency, weight=2,
                                                  priority=SignalPriority.required, knockout=False)],
                              bank_index=[])

    async def fake_llm(_messages):
        return monkeypatch_output

    return ControlPlane(
        session_context=ctx, system_prompt="sys", projection=CoverageProjection(),
        resolver_questions=[ResolverQuestion(question_id="q1", primary_signal="s", tier="core",
                                             is_mandatory=True, position=0, weight=2, estimated_minutes=3.0)],
        all_specs=ctx.signals, budget_cfg=budget_config_from_ai_config(), llm_call=fake_llm,
    )


def _turn(fired):
    r = active_question_rubric(_q(), fired_dimensions=fired)
    return build_turn_input(turn_ref="t1", active_question=r, on_the_floor="Assess a messy tenant?",
                            candidate_utterance="we made some changes", thread_turn_count=1,
                            projection=CoverageProjection(), all_specs=[], transcript_window=[],
                            budget_phase=BudgetPhase.on_track)


@pytest.mark.asyncio
async def test_probe_records_served_dimension():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="validate_impact",
                          composed_say="So concretely, what did you check first?")
    cp = _cp(out)
    decision = await cp.decide(_turn(fired=[]), asked_ids={"q1"}, time_remaining_s=600.0)
    assert decision.directive.act == DirectiveAct.probe
    assert decision.probe_dimension == "validate_impact"


@pytest.mark.asyncio
async def test_probe_at_cap_advances_to_ask():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="stage_safely",
                          composed_say="and how would you roll it out safely?")
    cp = _cp(out)
    # cap is 2; both dimensions already fired -> coerce returns None -> advance
    decision = await cp.decide(_turn(fired=["validate_impact", "stage_safely"]),
                               asked_ids={"q1"}, time_remaining_s=600.0)
    assert decision.directive.act in (DirectiveAct.ask, DirectiveAct.close)
    assert decision.probe_dimension is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_service_probe_dimension.py -v`
Expected: FAIL — `AttributeError` / `BrainTurnOutput` has no `probe_dimension` wiring in service, or `probe_index` import error.

- [ ] **Step 3: Edit `service.py`**

Change the import (lines 39–44): replace `coerce_probe_index` with `coerce_probe_dimension`.

In `decide()` replace the probe-index recording block (lines 206–215) with dimension recording:

```python
        # When this turn is a probe, record which dimension was served (coerced to a
        # valid, UNFIRED slug under the cap) so the driver's fired-dimension ledger
        # advances and the same dimension is never re-fired.
        probe_dimension_used: str | None = None
        if directive.act == DirectiveAct.probe:
            from app.ai.config import ai_config
            probe_dimension_used = coerce_probe_dimension(
                output.probe_dimension,
                follow_ups=turn_input.active_question.follow_ups,
                fired=turn_input.active_question.fired_dimensions,
                cap=ai_config.engine_probe_cap_per_thread,
            )
```

Update the `_log.info("engine.brain.decision", ...)` call (lines 219–231): replace `probe_index=output.probe_index` with `probe_dimension=output.probe_dimension`.

Update the `BrainDecision(...)` return (lines 234–241): replace `probe_index=probe_index_used` with `probe_dimension=probe_dimension_used`.

Replace `_resolve_probe` (lines 489–538) so the cap/ledger decides probe-vs-advance and the composed text stays the spoken probe (seed fallback uses the served dimension's `seed_probe`):

```python
    def _resolve_probe(
        self,
        *,
        output: BrainTurnOutput,
        turn_input: BrainTurnInput,
        asked_ids: set[str],
        covered_signals: set[str],
        time_remaining_s: float,
    ) -> tuple[Directive, str | None]:
        """Resolve a `probe` move. The dimension gate decides probe-vs-advance.

        coerce_probe_dimension returns the served (valid, unfired) slug under the cap,
        or None → fall back to `ask` (advance). The spoken text is the brain's composed
        probe (leak-scrubbed); when not composed, the served dimension's seed_probe.
        """
        from app.ai.config import ai_config

        served = coerce_probe_dimension(
            output.probe_dimension,
            follow_ups=turn_input.active_question.follow_ups,
            fired=turn_input.active_question.fired_dimensions,
            cap=ai_config.engine_probe_cap_per_thread,
        )
        if served is None:
            # Cap reached or all dimensions fired → advance.
            return self._resolve_ask(
                output=output, asked_ids=asked_ids,
                covered_signals=covered_signals, time_remaining_s=time_remaining_s,
            )

        composed = scrub_composed_say(output.composed_say, turn_input.active_question)
        if composed:
            say = composed
        else:
            # Seed fallback: the served dimension's pre-authored probe.
            by_slug = {d.dimension: d.seed_probe for d in turn_input.active_question.follow_ups}
            say = by_slug.get(served, "")

        return Directive(
            act=DirectiveAct.probe,
            say=say,
            tone=_ACT_TONE[DirectiveAct.probe],
            spoken_setup=None,
            is_terminal=False,
        ), None
```

NOTE: `_resolve_probe` no longer returns the served slug (it returns `(Directive, None)` like before). `decide()` recomputes the served dimension via `coerce_probe_dimension` (same inputs, deterministic) to set `BrainDecision.probe_dimension` — the recompute is intentional and keeps `_resolve_move`'s tuple shape unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_service_probe_dimension.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/brain/service.py tests/interview_engine/test_service_probe_dimension.py
git commit -m "feat(engine): brain resolves probes by dimension; cap force-advances"
```

---

## Task 8: Driver — fired-dimension ledger + plumbing

**Files:**
- Modify: `app/modules/interview_engine/driver.py`
- Test: `tests/interview_engine/test_driver_fired_dimensions.py` (create)

The driver currently keeps `_probes_used: dict[str, list[int]]` (line 288) and passes `probes_used=...` to `active_question_rubric` (line 552) + records `decision.probe_index` (lines 706–716). Replace with a slug ledger.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_driver_fired_dimensions.py
import pytest
from datetime import UTC, datetime

from app.modules.interview_engine.driver import build_session_driver
from app.modules.interview_engine.contracts import (
    BrainDecision, Directive, DirectiveAct, DirectiveTone, FollowUpDimension,
)
from app.modules.interview_engine.turn_source import AssembledTurn
from app.modules.interview_runtime.evidence import TimeSpan
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric, SessionConfig,
    SignalMetadata, StageConfig,
)


class _Voice:
    def __init__(self): self.last_interrupted = False
    async def say(self, text, *, allow_interruptions=True): pass


def _config():
    q = QuestionConfig(
        id="q1", position=0, text="Assess a messy tenant?", signal_values=["s"],
        estimated_minutes=3.0, is_mandatory=True,
        follow_ups=[FollowUpDimension(dimension="validate_impact", intent="i", seed_probe="seed A"),
                    FollowUpDimension(dimension="stage_safely", intent="i", seed_probe="seed B")],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="e" * 20, meets_bar="m" * 20, below_bar="b" * 20),
        evaluation_hint="h" * 12, question_kind="technical_scenario", primary_signal="s", difficulty="medium",
    )
    return SessionConfig(
        session_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        candidate_id="33333333-3333-3333-3333-333333333333", job_title="EMM Engineer",
        hiring_company_name="Acme", role_summary="r", jd_text="jd", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", company_stage="", hiring_bar="hb"),
        candidate=CandidateContext(name="Punar"),
        stage=StageConfig(stage_id="44444444-4444-4444-4444-444444444444", stage_type="ai_screening",
                          name="Screen", duration_minutes=15, difficulty="medium", questions=[q],
                          advance_behavior="manual_review"),
        signals=["s"],
        signal_metadata=[SignalMetadata(value="s", type="competency", priority="required", weight=2,
                                        knockout=False, stage="screen", evaluation_method="verbal_response")],
        keyterms=[],
    )


@pytest.mark.asyncio
async def test_probe_decision_records_fired_dimension():
    captured = {}

    async def persist(ev): captured["ev"] = ev

    driver = build_session_driver(_config(), voice=_Voice(), persist=persist,
                                  started_at=datetime.now(UTC))
    # opener sets the active question
    await driver.opener()

    # Stub the brain to return a probe on 'validate_impact'
    async def fake_handle(turn, turn_ref):
        return await driver.handle_turn(turn=turn, turn_ref=turn_ref)

    # Inject a decision by monkeypatching the brain adapter's decide via the loop is heavy;
    # instead assert the ledger field exists and starts empty.
    assert driver._fired_dimensions == {}  # type: ignore[attr-defined]
    assert not hasattr(driver, "_probes_used")
```

(NOTE: the lightweight assertion keeps this test livekit-free and fast; the probe→ledger recording is covered end-to-end by the existing driver turn tests once they are updated in Step 3's regression run.)

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_driver_fired_dimensions.py -v`
Expected: FAIL — `AttributeError: 'SessionDriver' object has no attribute '_fired_dimensions'`.

- [ ] **Step 3: Edit `driver.py`**

Rename the state field (line 288):
```python
        self._fired_dimensions: dict[str, list[str]] = {}   # question_id → fired dimension slugs
```

In `opener()` replace `self._probes_used.setdefault(...)` (line 453) with:
```python
        self._fired_dimensions.setdefault(nxt.question_id, [])
```

In `handle_turn`, replace the `active_question_rubric(...)` call (lines 550–553):
```python
        aq_rubric = active_question_rubric(
            self._active_q,
            fired_dimensions=self._fired_dimensions.get(q_id, []),
        )
```

Replace the probe-recording branch (lines 706–716) with dimension recording:
```python
        elif act == DirectiveAct.probe:
            # Probe on current question — record the served dimension slug so it is
            # never fired again on this thread (fire-once ledger).
            self._is_on_probe = True
            served = decision.probe_dimension
            if served:
                fired = self._fired_dimensions.setdefault(q_id, [])
                if served not in fired:
                    fired.append(served)
```

In the `ask`-advance branch, replace `self._probes_used.setdefault(next_q_id, [])` (line 696) with:
```python
            self._fired_dimensions.setdefault(next_q_id, [])
```

In `finalize()`, the `build_question_records(... probes_used=self._probes_used ...)` call (lines 766–772) needs `probes_used` as `dict[str, list[int]]`. Map the slug ledger back to indices for the record (the report contract still uses indices). Add right before that call:
```python
        # QuestionRecord.probes_used is index-based; map fired dimension slugs → indices.
        probes_used_idx: dict[str, list[int]] = {}
        for q in self._resolver_questions:
            cfg = self._q_by_id.get(q.question_id)
            if cfg is None:
                continue
            slug_to_idx = {d.dimension: i for i, d in enumerate(cfg.follow_ups)}
            probes_used_idx[q.question_id] = [
                slug_to_idx[s] for s in self._fired_dimensions.get(q.question_id, [])
                if s in slug_to_idx
            ]
```
and change the call to `probes_used=probes_used_idx`.

- [ ] **Step 4: Run test + the existing driver regression suite**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_driver_fired_dimensions.py tests/interview_engine/ -k "driver" -v`
Expected: the new test PASSES; fix any existing driver tests that referenced `probes_used`/`probe_index`/`active_question_rubric(probes_used=...)` by updating them to the new names (rename-only).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/driver.py tests/interview_engine/test_driver_fired_dimensions.py
git commit -m "feat(engine): driver tracks per-thread fired-dimension ledger"
```

---

## Task 9: Driver — E2 floor-pointer integrity guard

**Files:**
- Modify: `app/modules/interview_engine/driver.py`
- Test: `tests/interview_engine/test_driver_floor_integrity.py` (create)

The floor line (`_last_agent_line`, used by `repeat`/`clarify` via `on_the_floor`) must always be the most recent **question-bearing** act (ask/probe/repeat) and must never silently go stale. Today it updates only on ask/probe (lines 641–645). Add an explicit invariant + a log when a non-question act runs while the floor is empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_driver_floor_integrity.py
from app.modules.interview_engine.driver import _is_question_act
from app.modules.interview_engine.contracts import DirectiveAct


def test_question_acts_classified():
    assert _is_question_act(DirectiveAct.ask)
    assert _is_question_act(DirectiveAct.probe)
    assert _is_question_act(DirectiveAct.repeat)


def test_non_question_acts_not_classified():
    for a in (DirectiveAct.clarify, DirectiveAct.hold, DirectiveAct.reassure,
              DirectiveAct.confirm, DirectiveAct.answer_meta, DirectiveAct.redirect, DirectiveAct.close):
        assert not _is_question_act(a)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_driver_floor_integrity.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_question_act'`.

- [ ] **Step 3: Add the classifier + use it for floor tracking**

In `driver.py`, add near the module constants (after `_NON_ANSWER_ACTS`, line 95):

```python
# Acts that DELIVER the floor question — only these may update the floor pointer.
# (clarify/hold/reassure/confirm/answer_meta/redirect/close must NOT clobber the floor,
#  or a later `repeat`/`clarify` would replay a non-question line. E2 invariant.)
_QUESTION_ACTS: frozenset[DirectiveAct] = frozenset({
    DirectiveAct.ask, DirectiveAct.probe, DirectiveAct.repeat,
})


def _is_question_act(act: DirectiveAct) -> bool:
    return act in _QUESTION_ACTS
```

Replace the floor-update block (lines 641–645) to use the classifier (behaviour-preserving + explicit):
```python
        if capturing.captured:
            real_line_text = capturing.captured[-1]
            self._add_to_recent_openers(real_line_text)
            if _is_question_act(decision.directive.act):
                self._last_agent_line = real_line_text  # floor = latest question-bearing line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_driver_floor_integrity.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/driver.py tests/interview_engine/test_driver_floor_integrity.py
git commit -m "feat(engine): explicit floor-pointer invariant (E2 — no stale re-pose)"
```

---

## Task 10: Engine prompts — dimension probing + E1/E3 boundaries

**Files:**
- Modify: `prompts/v4/engine/brain.system.txt`
- Modify: `prompts/v4/engine/mouth/clarify.txt`
- Modify: `prompts/v4/engine/mouth/repeat.txt`
- Test: `tests/interview_engine/test_engine_prompts_present.py` (create — a cheap content assertion; deep validation is the opt-in `prompt_quality` talk-test)

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_engine/test_engine_prompts_present.py
from app.ai.prompts import PromptLoader


def test_brain_prompt_teaches_dimension_probing():
    txt = PromptLoader("v4").get("engine/brain.system").lower()
    assert "dimension" in txt
    assert "fired_dimensions" in txt or "already fired" in txt


def test_clarify_prompt_says_simplify_not_repeat():
    txt = PromptLoader("v4").get("engine/mouth/clarify").lower()
    assert "simpl" in txt  # "simpler" / "simplify"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_engine_prompts_present.py -v`
Expected: FAIL (current `brain.system.txt` has no `dimension` language).

- [ ] **Step 3: Edit the prompts (principle + why, no replayed examples)**

In `prompts/v4/engine/brain.system.txt`, in the `probe` move section, replace the `probe_index` guidance with dimension guidance. Add a block teaching:
- Each follow-up is a governed **dimension** with an `intent` and `listen_for`. To probe, set `probe_dimension` to ONE dimension's slug you have NOT already fired (see `fired_dimensions`), and compose `composed_say` WITHIN that dimension's `intent`, aimed at its `listen_for` specifics, anchored to the candidate's actual words.
- **Fire each dimension at most once.** Never re-ask a dimension already in `fired_dimensions`; pick a *different* unfired one or advance (`ask`).
- **One or two probes verify a thread; a third grinds.** Advance (`ask`) as soon as the `primary_signal` is sufficiently evidenced — do not march through every dimension.
- **Cross-question:** if a dimension targets a signal already `sufficient` in coverage, do not re-probe it on this question — advance.
- **E3:** if the candidate reads the question back to confirm ("…correct?"), that is a confirmation, not confusion — acknowledge briefly and wait; do NOT `clarify`/`repeat`.

In `prompts/v4/engine/mouth/clarify.txt` (E1): make it explicit that `clarify` re-poses the floor question in **simpler, plainer words** (a genuine simplification when the candidate did not understand), keeping technical terms exact — it is NOT a verbatim restatement.

In `prompts/v4/engine/mouth/repeat.txt`: reinforce that `repeat` is a near-verbatim re-delivery for an explicit "say it again" with no confusion — when the candidate signals they did not understand, the brain chooses `clarify` (simpler), not `repeat`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/interview_engine/test_engine_prompts_present.py -v`
Expected: PASS.

- [ ] **Step 5: Restart the engine (prompts load per-session) + commit**

```bash
docker compose up -d --force-recreate nexus-engine
git add prompts/v4/engine/brain.system.txt prompts/v4/engine/mouth/clarify.txt prompts/v4/engine/mouth/repeat.txt tests/interview_engine/test_engine_prompts_present.py
git commit -m "feat(engine): prompts — dimension probing, anti-grind, E1 clarify, E3 read-back"
```

---

## Task 11: Phase 1 integration check (engine runs on a backfilled bank)

**Files:**
- Test: run the full engine suite + a manual talk-test.

- [ ] **Step 1: Run the whole engine + runtime suite**

Run: `docker compose exec -T nexus pytest tests/interview_engine tests/interview_runtime -m "not prompt_quality" -q`
Expected: PASS. Fix any remaining references to `probe_index`/`probes_used`/`coerce_probe_index` in tests (rename-only).

- [ ] **Step 2: Manual talk-test (user)**

Re-run an EMM-style screen against the backfilled bank. Confirm: no verbatim/near-verbatim re-asks of the same probe; the agent fires ≤2 probes per question; it advances willingly on thin answers; a "simplify" request yields a genuinely simpler re-pose (not the identical sentence).

- [ ] **Step 3: Commit (if any test fixups were needed)**

```bash
git add -A && git commit -m "test(engine): align engine suite with dimension-based probing"
```

---

# PHASE 2 — Generator Quality

## Task 12: `FollowUpDimension` in the generator schema

**Files:**
- Modify: `app/modules/question_bank/schemas.py`
- Test: `tests/question_bank/test_generated_question_dimensions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_generated_question_dimensions.py
import pytest
from pydantic import ValidationError

from app.modules.question_bank.schemas import FollowUpDimension, GeneratedQuestion


def _q(follow_ups):
    return GeneratedQuestion(
        position=0, text="A real spoken question here?", primary_signal="s", signal_values=["s"],
        estimated_minutes=2.0, is_mandatory=False, follow_ups=follow_ups,
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric={"excellent": "e" * 20, "meets_bar": "m" * 20, "below_bar": "b" * 20},
        evaluation_hint="h" * 12, question_kind="technical_scenario", difficulty="medium",
    )


def test_generated_question_accepts_dimensions():
    q = _q([{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": ["x"]}])
    assert isinstance(q.follow_ups[0], FollowUpDimension)
    assert q.follow_ups[0].dimension == "d1"


def test_max_three_follow_ups():
    with pytest.raises(ValidationError):
        _q([{"dimension": f"d{i}", "intent": "i", "seed_probe": "p"} for i in range(4)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_generated_question_dimensions.py -v`
Expected: FAIL — `ImportError: cannot import name 'FollowUpDimension'`.

- [ ] **Step 3: Edit `question_bank/schemas.py`**

Add after `QuestionRubric` (line 49):

```python
class FollowUpDimension(BaseModel):
    """A governed probe dimension the live engine composes within (generation copy)."""

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(..., min_length=1,
                           description="Stable slug; distinct across the whole bank.")
    intent: str = Field(..., min_length=1,
                        description="What this probe verifies — distinct from the lead and other dimensions.")
    seed_probe: str = Field(..., min_length=1, max_length=240,
                            description="A short single-ask spoken seed probe.")
    listen_for: list[str] = Field(..., min_length=1, max_length=4,
                                  description="Observable specifics a strong answer to THIS dimension names.")
```

Change `GeneratedQuestion.follow_ups` (line 82):
```python
    follow_ups: list[FollowUpDimension] = Field(..., min_length=0, max_length=3)
```

Change `CreateQuestionBody.follow_ups` (line 160) and `UpdateQuestionBody.follow_ups` (line 177) and `QuestionResponse.follow_ups` (line 244) to `list[FollowUpDimension]` (Create/Update keep `default_factory=list` / `default=None`).

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_generated_question_dimensions.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/question_bank/schemas.py tests/question_bank/test_generated_question_dimensions.py
git commit -m "feat(question_bank): GeneratedQuestion.follow_ups are FollowUpDimension objects"
```

---

## Task 13: Persist the new shape in the generation actor

**Files:**
- Modify: `app/modules/question_bank/actors.py` (the two `StageQuestion(... follow_ups=list(r.follow_ups) ...)` sites: ~line 794 and ~line 1830)
- Test: `tests/question_bank/test_actor_persists_dimensions.py` (create — a focused persistence-shape unit test)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_actor_persists_dimensions.py
from app.modules.question_bank.actors import _followups_to_json
from app.modules.question_bank.schemas import FollowUpDimension


def test_followups_to_json_serializes_objects():
    fus = [FollowUpDimension(dimension="d1", intent="i", seed_probe="p", listen_for=["x"])]
    out = _followups_to_json(fus)
    assert out == [{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": ["x"]}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_actor_persists_dimensions.py -v`
Expected: FAIL — `ImportError: cannot import name '_followups_to_json'`.

- [ ] **Step 3: Add the helper + use it at both persistence sites**

In `app/modules/question_bank/actors.py`, add a module-level helper near the top (after imports):

```python
def _followups_to_json(follow_ups: list) -> list[dict]:
    """Serialize FollowUpDimension objects to JSONB-ready dicts for stage_questions.follow_ups."""
    return [fu.model_dump() for fu in follow_ups]
```

At both `StageQuestion(...)` construction sites, replace `follow_ups=list(r.follow_ups)` with:
```python
                    follow_ups=_followups_to_json(r.follow_ups),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_actor_persists_dimensions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/question_bank/actors.py tests/question_bank/test_actor_persists_dimensions.py
git commit -m "feat(question_bank): persist follow-up dimensions as JSONB objects"
```

---

## Task 14: Guard the refine/draft contract (no follow-up change needed)

**Files:**
- Test: `tests/question_bank/test_refine_followups_shape.py` (create)

`RefineResponse`/`DraftResponse` return only `proposed_text` / `proposed_signal_probed` / `proposed_mandatory` (+ `proposed_position` for draft) — they carry **no follow-ups**, so they need no shape change (YAGNI: do not add a follow-up field the recruiter flow never sends). This task adds a guard test so a future edit can't silently couple them to the dimension change.

- [ ] **Step 1: Write the test**

```python
# tests/question_bank/test_refine_followups_shape.py
from app.modules.question_bank.refine import RefineResponse, DraftResponse


def test_refine_response_contract_unchanged():
    r = RefineResponse(proposed_text="t", proposed_signal_probed="s", proposed_mandatory=False)
    assert r.proposed_text == "t"
    assert not hasattr(r, "follow_ups")


def test_draft_response_contract_unchanged():
    d = DraftResponse(proposed_text="t", proposed_signal_probed="s",
                      proposed_mandatory=False, proposed_position=0)
    assert d.proposed_position == 0
    assert not hasattr(d, "follow_ups")
```

- [ ] **Step 2: Run the test**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_refine_followups_shape.py -v`
Expected: PASS immediately (contract unchanged — this is a guard).

- [ ] **Step 3: Commit**

```bash
git add tests/question_bank/test_refine_followups_shape.py
git commit -m "test(question_bank): guard refine/draft contract carries no follow-ups"
```

---

## Task 15: Generation prompts — dimension shape + within/cross-phase dedup

**Files:**
- Modify: `prompts/v2/question_bank_common.txt`
- Modify: `prompts/v2/question_bank_ai_screening.txt`
- Modify: `prompts/v2/question_bank_ai_screening_behavioral.txt`
- Modify: `prompts/v2/question_bank_phone_screen.txt`
- Test: `tests/question_bank/test_generation_prompts_present.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_generation_prompts_present.py
from app.ai.prompts import PromptLoader


def test_common_prompt_teaches_dimension_shape_and_distinctness():
    txt = PromptLoader("v2").get("question_bank_common").lower()
    assert "dimension" in txt
    assert "listen_for" in txt or "listen for" in txt
    assert "distinct" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_generation_prompts_present.py -v`
Expected: FAIL.

- [ ] **Step 3: Edit the generation prompts (principle + why)**

In `question_bank_common.txt`, replace the `follow_ups` instructions with the `FollowUpDimension` shape and these principles:
- Each follow-up is a governed **dimension**: `{dimension (a short stable slug), intent (what it verifies), seed_probe (a short single-ask spoken probe), listen_for (1-4 observable specifics a strong answer names)}`.
- **Within a bank, every follow-up `dimension` must be DISTINCT.** Never author the same probe intent (e.g. "roll out safely") on two questions. If two scenarios share an angle, deepen each differently so no dimension repeats across the bank. *Why:* the live engine fires each dimension once; duplicated dimensions across questions make the screen ask the candidate the same thing repeatedly and break report comparability.
- Each follow-up explores a **different dimension** of its question — never a restatement of the lead or of another follow-up.

In `question_bank_ai_screening.txt` and `question_bank_ai_screening_behavioral.txt`: where `prior_phase_questions` is surfaced, instruct: *"These questions and their follow-up dimensions are ALREADY covered by the earlier phase — do not author any follow-up dimension that repeats their intent."* In `question_bank_phone_screen.txt`, add the same within-bank distinctness rule.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_generation_prompts_present.py -v`
Expected: PASS.

- [ ] **Step 5: Restart the worker (no hot-reload) + commit**

```bash
docker compose up -d --force-recreate nexus-worker
git add prompts/v2/question_bank_common.txt prompts/v2/question_bank_ai_screening.txt prompts/v2/question_bank_ai_screening_behavioral.txt prompts/v2/question_bank_phone_screen.txt tests/question_bank/test_generation_prompts_present.py
git commit -m "feat(question_bank): prompts — dimension shape + within/cross-phase dedup"
```

---

## Task 16: Generation prompt-quality eval (real API, opt-in)

**Files:**
- Test: `tests/question_bank/test_generation_quality.py` (create, marked `prompt_quality`)

- [ ] **Step 1: Write the eval test**

```python
# tests/question_bank/test_generation_quality.py
import pytest

pytestmark = pytest.mark.prompt_quality


@pytest.mark.asyncio
async def test_generated_bank_has_distinct_dimensions_across_questions():
    """Regenerate a known stage and assert no follow-up dimension intent repeats across questions.

    Uses the real generation actor against the EMM test job/stage. Skips if the fixtures
    are absent. The assertion: the set of dimension slugs across ALL questions has no
    duplicates, and each question's follow-ups have non-empty listen_for.
    """
    from app.modules.question_bank.actors import _generate_questions_for_kind  # type: ignore
    # Build minimal kwargs from the EMM fixtures or skip.
    pytest.skip("Wire to the EMM test fixtures during implementation; assertion logic below.")

    # questions: list[GeneratedQuestion] = await _generate_questions_for_kind(...)
    # slugs = [d.dimension for q in questions for d in q.follow_ups]
    # assert len(slugs) == len(set(slugs)), f"duplicate dimensions: {slugs}"
    # for q in questions:
    #     for d in q.follow_ups:
    #         assert d.listen_for, f"empty listen_for on {q.text!r}/{d.dimension}"
```

- [ ] **Step 2: Run it (opt-in marker)**

Run: `docker compose exec -T nexus pytest tests/question_bank/test_generation_quality.py -m prompt_quality -v`
Expected: SKIP (until wired to fixtures). Wire it to the EMM fixtures and confirm it PASSES against the real API.

- [ ] **Step 3: Commit**

```bash
git add tests/question_bank/test_generation_quality.py
git commit -m "test(question_bank): prompt-quality eval — distinct dimensions, non-empty listen_for"
```

---

## Task 17: Regenerate the test banks + Phase 2 talk-test

**Files:** none (operational).

- [ ] **Step 1: Regenerate the EMM bank via the API/worker**

Trigger a regeneration of the EMM stage bank (recruiter "regenerate" flow or the actor directly) so it gets full `FollowUpDimension` objects with `listen_for`. Verify:
```bash
docker compose exec -T nexus python -c "
import asyncio, os, json, asyncpg
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    rows=await c.fetch('SELECT position, follow_ups FROM stage_questions WHERE bank_id=\$1 ORDER BY position','7b67e66a-ddb8-475a-ba88-2915a3db6f4e')
    slugs=[]
    for r in rows:
        fu=json.loads(r['follow_ups']) if isinstance(r['follow_ups'],str) else r['follow_ups']
        slugs += [d['dimension'] for d in fu]
    print('total dims:', len(slugs), 'distinct:', len(set(slugs)))
    assert len(slugs)==len(set(slugs)), 'DUPLICATE DIMENSIONS'
    await c.close()
asyncio.run(m())"
```
Expected: `total == distinct` (no "stage safely" ×4).

- [ ] **Step 2: Manual talk-test (user)**

Run a full EMM screen end-to-end. Confirm the engine fires distinct dimensions, ≤2 probes/question, advances willingly, and never repeats a probe across questions.

- [ ] **Step 3: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to decide merge/PR.

---

## Self-Review

**Spec coverage:**
- §3 contract (`FollowUpDimension`, both modules, JSONB contract) → Tasks 1, 12.
- §3 single-shape backfill (+ rollback) → Task 2.
- §4.1 probe→dimension binding → Tasks 3, 7.
- §4.2 fire-once ledger + hard cap → Tasks 4 (gate), 8 (ledger), 5 (config).
- §4.3 early advance on `primary_signal` → Task 7 (`_resolve_probe` → `_resolve_ask`) + Task 10 (prompt).
- §4.4 cross-question net (no new field; coverage + cap + prompt) → Tasks 4/8 (cap) + Task 10 (prompt).
- §4.5 E1/E2/E3 → Task 10 (E1/E3 prompts), Task 9 (E2 floor invariant).
- §4.6 input builder render → Task 6.
- §5.1 generator shape → Task 12 (schema), Task 13 (persistence).
- §5.2 inline within/cross-phase dedup → Task 15.
- §5.3 prompts → Task 15 (generation), Task 10 (engine). Refine/draft carry no follow-ups → Task 14 guards that contract.
- §5.4 / §8 backfill + regen → Tasks 2, 17.
- §6 testing → Tasks 1–17 (unit), Task 16 (prompt-quality), Tasks 11/17 (talk-test).

**Type consistency:** `FollowUpDimension` (both modules), `probe_dimension` (`BrainTurnOutput`, `BrainDecision`), `fired_dimensions` (`ActiveQuestionRubric`, driver `_fired_dimensions`), `coerce_probe_dimension(probe_dimension, *, follow_ups, fired, cap)`, `active_question_rubric(q, *, fired_dimensions)`, `engine_probe_cap_per_thread`, `_followups_to_json`, `_is_question_act` — used consistently across tasks.

**Placeholder scan:** Task 16's eval is intentionally a `pytest.skip` scaffold wired to fixtures during implementation (the assertion logic is shown inline); every other step has concrete code/commands.
