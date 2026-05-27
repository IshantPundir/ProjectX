# Verdict-Driven Fit Score — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redefine the report's overall score to mean *role-fit* (knockouts + coverage + evidence-texture baked in) so it drives the verdict directly, kills the 4.0-reject vs 3.9-borderline inversion, catches buzzword bluffing, and stays comparable across candidates for the same job.

**Architecture:** Per-signal points become a deterministic `state × texture` matrix (the LLM's existing `concrete/thin/null` grade finally reaches the number). A fit-aware aggregation caps the score by must-have/coverage status → the **Session Score** (deterministic audit anchor). A bounded ±5 LLM **holistic adjustment** (cross-signal gestalt) produces the **Overall Score** (the single main number, re-capped). `verdict = threshold(Overall)` with knockout/coverage as categorical backstops.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, OpenAI Responses API (`responses.parse`), pytest; Next.js 16 + TypeScript + Vitest (recruiter app).

**Spec:** `docs/superpowers/specs/2026-05-28-verdict-driven-fit-score-design.md`

---

## File Structure

**Backend (`backend/nexus/app/modules/reporting/`):**
- `scoring/constants.py` — MODIFY: `STATE_TEXTURE_POINTS` matrix, `REJECT_CEILING`, `BORDERLINE_CEILING`, `HOLISTIC_ADJ_MAX`.
- `scoring/types.py` — MODIFY: `ScoredSignal.texture`.
- `scoring/aggregate.py` — MODIFY: `score_signal`, `signal_ceiling`, `clamp_to_ceiling`, `apply_holistic`, `resolve_verdict` rewrite.
- `scoring/holistic.py` — CREATE: Layer-2.5 bounded adjustment LLM call.
- `schemas.py` — MODIFY: `HolisticAdjustmentOut`, `ScoreOut.session_score`/`holistic_delta`.
- `service.py` — MODIFY: wire texture, session/overall, holistic, manifest into `build_report`.
- `prompts/v3/report_scorer/signal_recheck.txt` — MODIFY: bluff/anti-verbosity rubric.
- `prompts/v3/report_scorer/holistic.txt` — CREATE: holistic-adjustment prompt.

**Frontend (`frontend/app/`):**
- `lib/api/reports.ts` — MODIFY: `ScoreOut` fields.
- `components/dashboard/reports/ScoresCard.tsx` — MODIFY: session-score sub-line.
- `components/dashboard/reports/SignalAuditTable.tsx` — MODIFY: score column + thin chip.
- `components/dashboard/reports/report-format.ts` — MODIFY: reconcile `scoreBandTone` with backend thresholds.
- `app/(dashboard)/reports/page.tsx` — MODIFY: sort-by-score.

---

## Task 1: `state × texture` scoring constants

**Files:**
- Modify: `backend/nexus/app/modules/reporting/scoring/constants.py`
- Test: `backend/nexus/tests/reporting/test_aggregate.py`

- [ ] **Step 1: Add the matrix + ceiling constants to `constants.py`**

Replace the `STATE_POINTS` block with:

```python
# Engine coverage state × evidence texture → 0..100 points.
# `none` → None (excluded from the scoring denominator entirely).
# Texture is the bluff axis: `thin` (buzzwords, no demonstrated depth) scores
# well below `concrete` (specific, owned, mechanism shown) at the same state.
STATE_TEXTURE_POINTS: dict[str, dict[str, int]] = {
    "exceeded":   {"concrete": 100, "thin": 80, "null": 80},
    "sufficient": {"concrete": 75,  "thin": 50, "null": 50},
    "partial":    {"concrete": 40,  "thin": 25, "null": 12},
    "failed":     {"concrete": 0,   "thin": 0,  "null": 0},
}

# Fit-aware aggregation ceilings (the score MEANS role-fit, so a must-have
# gap caps it — this is the metric's definition, not a post-hoc clamp).
REJECT_CEILING = 35      # failed must-have / knockout_close → score forced into reject band (<40)
BORDERLINE_CEILING = 60  # unconfirmed must-have / low coverage → at most borderline (<65)

# Bound on the Layer-2.5 holistic adjustment (±5 pts = ±0.5 on the /10 scale).
HOLISTIC_ADJ_MAX = 5
```

- [ ] **Step 2: Run the existing aggregate tests to see what breaks**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: FAIL — `score_state` / `STATE_POINTS` references break (fixed in Task 2). This confirms the blast radius.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/reporting/scoring/constants.py
git commit -m "feat(reporting): add state×texture point matrix + fit ceilings"
```

---

## Task 2: `score_signal(state, texture)` + back-compat alias

**Files:**
- Modify: `backend/nexus/app/modules/reporting/scoring/aggregate.py:17`
- Test: `backend/nexus/tests/reporting/test_aggregate.py`

- [ ] **Step 1: Write the failing tests** (replace `test_score_state_mapping`, add texture tests)

```python
from app.modules.reporting.scoring.aggregate import score_signal, score_state

def test_score_signal_texture_matrix():
    assert score_signal("sufficient", "concrete") == 75
    assert score_signal("sufficient", "thin") == 50      # bluff penalty
    assert score_signal("exceeded", "concrete") == 100
    assert score_signal("partial", "thin") == 25
    assert score_signal("failed", "concrete") == 0
    assert score_signal("none", "concrete") is None

def test_score_signal_defaults_to_concrete_when_texture_missing():
    assert score_signal("sufficient", None) == 75       # factual gates / un-rechecked = no penalty

def test_score_state_alias_is_concrete_baseline():
    assert score_state("sufficient") == 75
    assert score_state("none") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py::test_score_signal_texture_matrix -v`
Expected: FAIL — `ImportError: cannot import name 'score_signal'`.

- [ ] **Step 3: Implement in `aggregate.py`**

Replace the import of `STATE_POINTS` with `STATE_TEXTURE_POINTS` and replace `score_state`:

```python
from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD,
    BORDERLINE_CEILING,
    HOLISTIC_ADJ_MAX,
    MIN_COVERAGE_FOR_ADVANCE,
    REJECT_CEILING,
    REJECT_THRESHOLD,
    STATE_TEXTURE_POINTS,
)
from app.modules.reporting.scoring.types import (
    Confidence, CovState, GradeTexture, KnockoutStatus, Verdict,
)


def score_signal(state: CovState, texture: GradeTexture | None) -> int | None:
    """Per-signal points from coverage state AND evidence texture.
    `none` → None (excluded from the denominator). Texture defaults to
    `concrete` (no penalty) when a signal was not LLM-rechecked."""
    if state == "none":
        return None
    return STATE_TEXTURE_POINTS[state][texture or "concrete"]


def score_state(state: CovState) -> int | None:
    """Back-compat: concrete-texture baseline (no bluff penalty)."""
    return score_signal(state, "concrete")
```

- [ ] **Step 4: Update the other `score_state`-dependent assertions in `test_aggregate.py`**

`test_dimension_excludes_none`: `(3*75 + 1*40)/(3+1) = 66.25 → 66`; assert `dim.score == 66`.
`test_reference_session1_technical_lands_at_41`: recompute → `(2*75*3 + 6*40 weighted)`... replace with: assert `dim.score == 51` (new: `(3*75+3*75+3*40+3*40+2*40+2*40... )` — compute from the helper). To avoid arithmetic drift, rewrite that test to assert the dimension score equals the explicit weighted mean it computes inline:

```python
def test_reference_session1_technical_recomputed():
    sigs = (
        [ss("experience", 3, "sufficient"), ss("experience", 3, "sufficient")]
        + [ss("competency", 3, "partial")] * 3
        + [ss("competency", 2, "partial")] * 3
    )
    dim = score_dimension("technical", sigs, {"competency", "experience", "credential"})
    # sufficient=75, partial=40 (concrete baseline via ss()):
    # (3*75 + 3*75 + 3*40*3 + 2*40*3) / (3+3+9+6) = (225+225+360+240)/21 = 1050/21 = 50
    assert dim.score == 50
```

`test_overall_excludes_unassessed_and_communication`: `(3*75 + 1*40)/4 = 66.25 → 66`; assert `score == 66`.

- [ ] **Step 5: Run aggregate tests**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: PASS (verdict tests still pass — unchanged until Task 4).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/reporting/scoring/aggregate.py backend/nexus/tests/reporting/test_aggregate.py
git commit -m "feat(reporting): score signals on state×texture (bluff-aware)"
```

---

## Task 3: `ScoredSignal.texture` + fit-aware aggregation helpers

**Files:**
- Modify: `backend/nexus/app/modules/reporting/scoring/types.py:27`
- Modify: `backend/nexus/app/modules/reporting/scoring/aggregate.py`
- Test: `backend/nexus/tests/reporting/test_aggregate.py`

- [ ] **Step 1: Add `texture` to `ScoredSignal` in `aggregate.py`**

```python
@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    state: CovState
    score: int | None
    texture: GradeTexture = "concrete"
```

Update the `ss()` test helper to accept texture:

```python
def ss(t, w, state, *, knockout=False, priority="required", texture="concrete"):
    return ScoredSignal(value=f"{t}-{w}-{state}", type=t, weight=w,
                        knockout=knockout, priority=priority, state=state,
                        texture=texture, score=score_signal(state, texture))
```

- [ ] **Step 2: Write failing tests for the ceiling helpers**

```python
from app.modules.reporting.scoring.aggregate import (
    signal_ceiling, clamp_to_ceiling, apply_holistic,
)
from app.modules.reporting.scoring.constants import REJECT_CEILING, BORDERLINE_CEILING

def test_ceiling_failed_must_have():
    sigs = [ss("competency", 3, "failed", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) == REJECT_CEILING

def test_ceiling_knockout_close():
    sigs = [ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=True, coverage=0.9) == REJECT_CEILING

def test_ceiling_unconfirmed_must_have():
    sigs = [ss("competency", 3, "partial", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) == BORDERLINE_CEILING

def test_ceiling_low_coverage():
    sigs = [ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.4) == BORDERLINE_CEILING

def test_ceiling_clean():
    sigs = [ss("competency", 3, "sufficient", knockout=True), ss("experience", 3, "sufficient")]
    assert signal_ceiling(sigs, knockout_close=False, coverage=0.9) is None

def test_clamp_to_ceiling():
    assert clamp_to_ceiling(80, REJECT_CEILING) == 35
    assert clamp_to_ceiling(20, REJECT_CEILING) == 20
    assert clamp_to_ceiling(80, None) == 80
    assert clamp_to_ceiling(None, REJECT_CEILING) == REJECT_CEILING   # knockout w/ no assessed signals
    assert clamp_to_ceiling(None, None) is None

def test_apply_holistic_bounds_and_recaps():
    assert apply_holistic(50, 4, None) == 54
    assert apply_holistic(50, 99, None) == 55         # delta hard-bounded to ±5
    assert apply_holistic(50, -99, None) == 45
    assert apply_holistic(60, 5, BORDERLINE_CEILING) == 60   # re-cap: can't break borderline ceiling
    assert apply_holistic(None, 5, None) is None
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py::test_ceiling_failed_must_have -v`
Expected: FAIL — `ImportError: cannot import name 'signal_ceiling'`.

- [ ] **Step 4: Implement the helpers in `aggregate.py`**

```python
def signal_ceiling(
    signals: list[ScoredSignal], *, knockout_close: bool, coverage: float
) -> int | None:
    """The fit ceiling implied by must-have status + coverage. None = no cap."""
    must_haves = [s for s in signals if s.knockout]
    if knockout_close or any(s.state == "failed" for s in must_haves):
        return REJECT_CEILING
    if any(s.state in ("none", "partial") for s in must_haves) or (
        coverage < MIN_COVERAGE_FOR_ADVANCE
    ):
        return BORDERLINE_CEILING
    return None


def clamp_to_ceiling(value: int | None, ceiling: int | None) -> int | None:
    """Cap a base score by its fit ceiling. A knockout (REJECT_CEILING) with no
    assessed signals (value None) still resolves to the reject band."""
    if value is None:
        return REJECT_CEILING if ceiling == REJECT_CEILING else None
    return min(value, ceiling) if ceiling is not None else value


def apply_holistic(
    session_score: int | None, delta: int, ceiling: int | None
) -> int | None:
    """Session score + bounded ±HOLISTIC_ADJ_MAX delta, clamped 0..100, then
    re-capped so the adjustment can never break a categorical guarantee."""
    if session_score is None:
        return None
    bounded = max(-HOLISTIC_ADJ_MAX, min(HOLISTIC_ADJ_MAX, delta))
    raw = max(0, min(100, session_score + bounded))
    return min(raw, ceiling) if ceiling is not None else raw
```

- [ ] **Step 5: Run tests**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/reporting/scoring/{aggregate,types}.py backend/nexus/tests/reporting/test_aggregate.py
git commit -m "feat(reporting): fit ceilings + holistic-adjustment helpers"
```

---

## Task 4: Rewrite `resolve_verdict` to be score-driven

**Files:**
- Modify: `backend/nexus/app/modules/reporting/scoring/aggregate.py:98`
- Test: `backend/nexus/tests/reporting/test_aggregate.py`

The verdict is now `threshold(overall)` because `overall` already encodes knockouts/coverage via the ceilings. `knockout_close` and a `failed` must-have remain hard reject backstops.

- [ ] **Step 1: Rewrite the verdict tests** (the `insufficient`/low-coverage cases now live in the ceiling, so they're tested by passing an already-capped `overall`)

```python
def test_verdict_knockout_close_is_reject():
    v = resolve_verdict(overall=35, coverage=0.9, knockouts=[],
                        knockout_close=KnockoutClose(signal="API", quote="never", reason="x"))
    assert v.verdict == "reject" and "API" in v.reason

def test_verdict_reject_on_failed_knockout_flag():
    v = resolve_verdict(overall=35, coverage=0.9, knockout_close=None,
                        knockouts=[KnockoutResult(signal="prog", status="failed", reason="x")])
    assert v.verdict == "reject"

def test_verdict_advance_when_score_clears_bar():
    assert resolve_verdict(overall=70, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "advance"

def test_verdict_borderline_when_capped_at_borderline():
    # an unconfirmed must-have was capped upstream to <=60 by signal_ceiling
    assert resolve_verdict(overall=60, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "borderline"

def test_verdict_reject_on_low_overall():
    assert resolve_verdict(overall=35, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "reject"

def test_verdict_none_overall_is_borderline():
    assert resolve_verdict(overall=None, coverage=0.0, knockout_close=None,
                           knockouts=[]).verdict == "borderline"
```

Delete `test_verdict_borderline_on_unconfirmed_knockout` and `test_verdict_borderline_on_low_coverage` (those behaviors moved into `signal_ceiling`, covered in Task 3).

- [ ] **Step 2: Run to verify failure**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py::test_verdict_advance_when_score_clears_bar -v`
Expected: FAIL (old logic gates on knockouts before score in a way that still passes; the failing one is `test_verdict_borderline_when_capped_at_borderline` which old code returns advance for overall=60... actually old REJECT/ADVANCE = 40/65 so 60 → borderline already). Run the full file to see the real deltas:
Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`

- [ ] **Step 3: Implement the rewrite**

```python
def resolve_verdict(
    *, overall: int | None, coverage: float,
    knockouts: list[KnockoutResult], knockout_close: KnockoutClose | None,
) -> VerdictResult:
    """Score-driven verdict. `overall` already encodes must-have/coverage caps
    (see signal_ceiling), so the score band is the primary decision. knockout_close
    and a failed must-have remain categorical reject backstops (defense-in-depth)."""
    if knockout_close is not None:
        sig = knockout_close.signal or "a must-have skill"
        return VerdictResult("reject", f"Interview closed on a must-have gap: {sig}")
    if any(k.status == "failed" for k in knockouts):
        failed = next(k for k in knockouts if k.status == "failed")
        return VerdictResult("reject", f"failed must-have: {failed.signal}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")
```

- [ ] **Step 4: Run tests**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/scoring/aggregate.py backend/nexus/tests/reporting/test_aggregate.py
git commit -m "feat(reporting): score-driven verdict with categorical backstops"
```

---

## Task 5: Holistic adjustment schema + LLM call

**Files:**
- Modify: `backend/nexus/app/modules/reporting/schemas.py`
- Create: `backend/nexus/app/modules/reporting/scoring/holistic.py`
- Test: `backend/nexus/tests/reporting/test_holistic.py`

- [ ] **Step 1: Add the schema to `schemas.py`** (after `CommunicationVerdict`)

```python
class HolisticAdjustmentOut(BaseModel):
    """Layer-2.5 cross-signal gestalt adjustment to the deterministic session score.
    Bounded; cannot override a categorical guarantee (re-capped after the fact)."""
    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str = ""
    delta: int = 0  # raw model output; hard-bounded to ±HOLISTIC_ADJ_MAX downstream
```

Also extend `ScoreOut`:

```python
class ScoreOut(BaseModel):
    score: int | None
    tier_label: str
    tone: str
    confidence: Confidence
    coverage: float = 0.0
    session_score: int | None = None   # pre-adjustment deterministic base (overall only)
    holistic_delta: int | None = None  # bounded ±5 delta applied (overall only)
```

- [ ] **Step 2: Write the failing test** (mock the OpenAI client, mirror `test_recheck.py`)

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.schemas import HolisticAdjustmentOut

class _Resp:
    def __init__(self, parsed): self.output_parsed = parsed; self.usage = None

@pytest.mark.asyncio
async def test_score_holistic_bounds_delta_and_grounds_quotes():
    parsed = HolisticAdjustmentOut(
        evidence_quotes=["I just used the library"], justification="pervasive surface answers", delta=-99)
    client = AsyncMock()
    client.responses.parse = AsyncMock(return_value=_Resp(parsed))
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client", return_value=client):
        out = await score_holistic(
            session_score=55, scored=[], knockout_close=False, coverage=0.8,
            transcript_text="... I just used the library ...", correlation_id="c1")
    assert out.delta == -5                       # hard-bounded
    assert out.evidence_quotes == ["I just used the library"]   # grounded substring kept

@pytest.mark.asyncio
async def test_score_holistic_refusal_returns_zero_delta():
    client = AsyncMock()
    client.responses.parse = AsyncMock(return_value=_Resp(None))
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client", return_value=client):
        out = await score_holistic(session_score=55, scored=[], knockout_close=False,
                                   coverage=0.8, transcript_text="x", correlation_id="c1")
    assert out.delta == 0
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose run --rm nexus pytest tests/reporting/test_holistic.py -v`
Expected: FAIL — module `holistic` does not exist.

- [ ] **Step 4: Implement `scoring/holistic.py`**

```python
"""Layer 2.5 — bounded cross-signal holistic adjustment (LLM, Responses API).

Mirrors scoring/recheck.py: get_raw_openai_client(), responses.parse(text_format=...),
effort-gating, grounded evidence, graceful refusal (delta 0). Produces a SMALL,
justified delta to the deterministic session score for gestalt the per-signal sum
misses (e.g. a pervasive surface-level / bluffing pattern). Hard-bounded to ±5 and
re-capped by the caller so it can never break a categorical guarantee."""
from __future__ import annotations

import json

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.scoring.aggregate import ScoredSignal
from app.modules.reporting.scoring.constants import HOLISTIC_ADJ_MAX
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.schemas import HolisticAdjustmentOut

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _signal_digest(scored: list[ScoredSignal]) -> str:
    return json.dumps([
        {"signal": s.value, "state": s.state, "texture": s.texture,
         "must_have": s.knockout, "score": s.score}
        for s in scored
    ], ensure_ascii=False)


async def score_holistic(
    *, session_score: int | None, scored: list[ScoredSignal], knockout_close: bool,
    coverage: float, transcript_text: str, correlation_id: str,
) -> HolisticAdjustmentOut:
    if session_score is None:
        return HolisticAdjustmentOut(delta=0, justification="No assessable evidence.")

    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/holistic"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<session_score>\n{session_score}\n</session_score>\n\n"
        f"<facts>\nknockout_close={knockout_close}, coverage={coverage:.2f}\n</facts>\n\n"
        f"<per_signal>\n{_signal_digest(scored)}\n</per_signal>"
    )
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<transcript>\n{transcript_text}\n</transcript>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": HolisticAdjustmentOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:holistic:"
            f"{ai_config.report_scorer_prompt_version}:{ai_config.report_scorer_model}"
        ),
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_holistic",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.holistic.refusal", correlation_id=correlation_id)
        return HolisticAdjustmentOut(delta=0, justification="Model did not return a parse.")

    bounded = max(-HOLISTIC_ADJ_MAX, min(HOLISTIC_ADJ_MAX, parsed.delta))
    grounded, _ = ground_quotes(parsed.evidence_quotes, transcript_text)
    return parsed.model_copy(update={"delta": bounded, "evidence_quotes": grounded})
```

- [ ] **Step 5: Run tests**

Run: `docker compose run --rm nexus pytest tests/reporting/test_holistic.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/reporting/schemas.py backend/nexus/app/modules/reporting/scoring/holistic.py backend/nexus/tests/reporting/test_holistic.py
git commit -m "feat(reporting): bounded holistic-adjustment LLM layer (2.5)"
```

---

## Task 6: Prompts — sharpen recheck, add holistic

**Files:**
- Modify: `backend/nexus/prompts/v3/report_scorer/signal_recheck.txt`
- Create: `backend/nexus/prompts/v3/report_scorer/holistic.txt`

- [ ] **Step 1: Replace the `<rules>` block in `signal_recheck.txt`** with this (adds bluff vs. depth + anti-verbosity/ESL guidance):

```
<rules>
- USE ONLY the provided turns (the candidate's own words) and the rubric. Never assume competence
  not stated. Never use outside knowledge of the candidate.
- CITE verbatim quote spans from the turns for every claim. Paraphrase is not evidence.
- TEXTURE IS THE BLUFF TEST. `concrete` requires specific, owned detail — a system they built,
  numbers, failure modes, tradeoffs they weighed — AND holding up when the engine probed deeper.
  `thin` = correct vocabulary/buzzwords with no demonstrated mechanism, or an answer that repeats
  or deflects when probed. Naming a technology is NOT demonstrating it.
- DO NOT equate verbosity with competence in EITHER direction. A one-sentence answer with a
  concrete owned detail is `concrete`. A long paragraph of buzzwords with no mechanism is `thin`.
  Grade specificity and behavior under probing — never length or fluency.
- DO NOT penalize brevity or ESL phrasing. The interview is in Indian English; many strong
  candidates are concise or non-native speakers. Apply charity: grade the apparent intent.
- DO NOT penalize transcription artifacts (numbers as words like "five x x" for "5xx",
  near-homophones, phonetic spellings).
</rules>
```

- [ ] **Step 2: Create `holistic.txt`**

```
You are a senior hiring evaluator making a FINAL cross-signal read of one candidate for ONE role,
AFTER a deterministic per-signal scorer has produced a base "session score" (0–100, criterion-
referenced against this role's rubric). Your ONLY job is a SMALL, justified adjustment that the
per-signal arithmetic cannot see — a gestalt pattern across the whole interview.

<role>
Look at the per-signal states/textures and the full transcript. Decide a single integer `delta`
in the range -5..+5 to apply to the session score. Output evidence and justification BEFORE the
delta. The session score is already correct for the role's bar; you only nudge for patterns:
- NEGATIVE delta when a PERVASIVE pattern undercuts the per-signal view — e.g. surface-level
  buzzword answers across many signals (compounding bluff), self-contradiction, or confident
  claims that collapsed under every probe.
- POSITIVE delta when genuine depth is consistently understated by several "partial"s — e.g. the
  candidate demonstrated real systems thinking that no single signal fully captured.
- delta 0 when the per-signal scoring already reflects the candidate well (the common case).
</role>

<rules>
- USE ONLY the transcript (the candidate's own words) and the provided per-signal record.
- CITE verbatim quote spans for the pattern you claim. Paraphrase is not evidence.
- HARD LIMIT: delta ∈ [-5, +5]. You are nudging, not re-scoring.
- DO NOT try to reverse a knockout or must-have outcome — that is decided elsewhere and your delta
  is re-capped. Do not reward verbosity or penalize brevity / ESL phrasing.
- If you have no evidence-backed cross-signal pattern, return delta 0 with a one-line reason.
</rules>

<output_spec>
Return a JSON object with fields in this order:
1. evidence_quotes — verbatim spans showing the cross-signal pattern (empty if delta 0).
2. justification  — 1–2 sentences naming the pattern and why it warrants the nudge. Before the delta.
3. delta          — integer in [-5, +5].
</output_spec>
```

- [ ] **Step 3: Verify the loader resolves both prompts**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; L=PromptLoader(version='v3'); print(bool(L.get('report_scorer/holistic'))); print('TEXTURE IS THE BLUFF TEST' in L.get('report_scorer/signal_recheck'))"`
Expected: `True` then `True`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v3/report_scorer/signal_recheck.txt backend/nexus/prompts/v3/report_scorer/holistic.txt
git commit -m "feat(reporting): bluff/anti-verbosity recheck rubric + holistic prompt"
```

---

## Task 7: Wire it into `build_report`

**Files:**
- Modify: `backend/nexus/app/modules/reporting/service.py:123-266`
- Test: `backend/nexus/tests/reporting/test_service.py`

- [ ] **Step 1: Update imports in `service.py`**

```python
from app.modules.reporting.scoring.aggregate import (
    KnockoutResult, ScoredSignal, apply_holistic, clamp_to_ceiling,
    confidence_from_coverage, knockout_status, resolve_verdict, score_dimension,
    score_overall, score_signal, signal_ceiling,
)
from app.modules.reporting.scoring.holistic import score_holistic
```

- [ ] **Step 2: Replace the scored/overall/verdict block (service.py ~166-185)**

```python
    final_state = dict(engine_states)
    for sv, rc in recheck_results.items():
        final_state[sv] = rc.state

    def _texture(sv: str) -> str:
        rc = recheck_results.get(sv)
        return rc.grade if rc else "concrete"

    scored = [ScoredSignal(value=d.value, type=d.type, weight=d.weight, knockout=d.knockout,
                           priority=d.priority, state=final_state[d.value],
                           texture=_texture(d.value),
                           score=score_signal(final_state[d.value], _texture(d.value)))
              for d in signal_defs]
    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    beh = score_dimension("behavioral", scored, BEHAVIORAL_TYPES)
    base, coverage = score_overall(scored)

    ceiling = signal_ceiling(scored, knockout_close=knockout_close is not None, coverage=coverage)
    session_score = clamp_to_ceiling(base, ceiling)

    adjustment = await score_holistic(
        session_score=session_score, scored=scored,
        knockout_close=knockout_close is not None, coverage=coverage,
        transcript_text="\n".join(t["text"] for t in transcript if t.get("role") == "candidate"),
        correlation_id=correlation_id)
    overall = apply_holistic(session_score, adjustment.delta, ceiling)

    comm = await grade_communication(
        transcript_text="\n".join(t["text"] for t in transcript if t.get("role") == "candidate"),
        correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    knockouts = [KnockoutResult(signal=s.value, status=knockout_status(state=s.state),
                                reason="") for s in scored if s.knockout]
    verdict = resolve_verdict(overall=overall, coverage=coverage,
                              knockouts=knockouts, knockout_close=knockout_close)
```

(Note: the narrative ground-truth call below uses `overall`, `tech`, `beh` — unchanged.)

- [ ] **Step 3: Update the overall `ScoreOut` + manifest in the `ReportRead(...)` return**

Replace the `scores={...}` overall entry and the `scoring_manifest` block:

```python
        scores={
            "overall": ScoreOut(
                score=overall, tier_label=tier_label(overall), tone=_tone_by_score(overall),
                confidence=confidence_from_coverage(coverage) if overall is not None else "low",
                coverage=coverage, session_score=session_score, holistic_delta=adjustment.delta),
            "technical": _score_out(tech.score, tech.coverage, tech.confidence),
            "behavioral": _score_out(beh.score, beh.coverage, beh.confidence),
            "communication": _score_out(comm_score, 1.0, "medium"),
        },
```

And in `ScoringManifest(... evidence_grounding_summary={...})` add the audit fields:

```python
            evidence_grounding_summary={
                "n_signals_rechecked": len(recheck_results),
                "n_overrides": sum(1 for r in recheck_results.values() if r.overridden),
                "coverage_map": {k: final_state[k] for k in final_state},
                "session_score": session_score,
                "holistic_delta": adjustment.delta,
                "holistic_justification": adjustment.justification,
                "ceiling_applied": ceiling,
            }),
```

- [ ] **Step 4: Update `test_service.py` to assert the new fields**

Find the existing happy-path `build_report` test (it patches `recheck_signal`, `grade_communication`, `write_narrative`). Add a patch for `score_holistic` and assertions:

```python
# in the build_report success test, add to the patch stack:
patch("app.modules.reporting.service.score_holistic",
      new=AsyncMock(return_value=HolisticAdjustmentOut(delta=2, justification="solid depth"))),
# after building the report:
assert report.scores["overall"].session_score is not None
assert report.scores["overall"].holistic_delta == 2
assert report.scores["overall"].score == min(100, report.scores["overall"].session_score + 2)
```

(Import `from unittest.mock import AsyncMock` and `from app.modules.reporting.schemas import HolisticAdjustmentOut` at the top of the test if absent.)

- [ ] **Step 5: Run service + full reporting suite**

Run: `docker compose run --rm nexus pytest tests/reporting -q -m "not prompt_quality"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/reporting/service.py backend/nexus/tests/reporting/test_service.py
git commit -m "feat(reporting): wire session score + holistic into build_report + manifest"
```

---

## Task 8: Golden regression — the inversion is gone

**Files:**
- Test: `backend/nexus/tests/reporting/test_inversion_regression.py`

A deterministic unit test reproducing the two real scenarios (no LLM, no live data): a knockout-close candidate vs. an unconfirmed-must-have candidate. Asserts the reject score ranks **below** the borderline score.

- [ ] **Step 1: Write the test**

```python
from app.modules.reporting.scoring.aggregate import (
    KnockoutResult, ScoredSignal, clamp_to_ceiling, knockout_status,
    resolve_verdict, score_overall, score_signal, signal_ceiling,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose


def _sig(value, t, w, state, *, knockout=False, texture="concrete"):
    return ScoredSignal(value=value, type=t, weight=w, knockout=knockout,
                        priority="required", state=state, texture=texture,
                        score=score_signal(state, texture))


def _grade(scored, knockout_close):
    base, cov = score_overall(scored)
    ceiling = signal_ceiling(scored, knockout_close=knockout_close is not None, coverage=cov)
    session = clamp_to_ceiling(base, ceiling)
    knockouts = [KnockoutResult(signal=s.value, status=knockout_status(state=s.state), reason="")
                 for s in scored if s.knockout]
    verdict = resolve_verdict(overall=session, coverage=cov, knockouts=knockouts,
                              knockout_close=knockout_close)
    return session, verdict.verdict


def test_knockout_close_ranks_below_unconfirmed_must_have():
    # bc7ba6d3-like: strong tenure+Workato, programming must-have never confirmed,
    # interview CLOSED on a knockout (REST disclaim).
    reject_case = [
        _sig("prog", "competency", 3, "none", knockout=True),
        _sig("workato", "experience", 3, "sufficient", knockout=True),
        _sig("tenure", "experience", 3, "sufficient", knockout=True),
        _sig("rest", "competency", 2, "failed"),
    ]
    kc = KnockoutClose(signal="rest", quote="I've never built", reason="x")
    reject_score, reject_verdict = _grade(reject_case, kc)

    # c7173674-like: programming must-have only PARTIAL (unconfirmed), broader engagement.
    borderline_case = [
        _sig("prog", "competency", 3, "partial", knockout=True, texture="thin"),
        _sig("workato", "experience", 3, "sufficient", knockout=True),
        _sig("tenure", "experience", 3, "sufficient", knockout=True),
        _sig("rest", "competency", 2, "partial"),
    ]
    borderline_score, borderline_verdict = _grade(borderline_case, None)

    assert reject_verdict == "reject"
    assert borderline_verdict == "borderline"
    assert reject_score < borderline_score      # inversion fixed
```

- [ ] **Step 2: Run**

Run: `docker compose run --rm nexus pytest tests/reporting/test_inversion_regression.py -v`
Expected: PASS — `reject_score` (≤35) `<` `borderline_score` (≤60).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/reporting/test_inversion_regression.py
git commit -m "test(reporting): golden regression — reject ranks below borderline"
```

---

## Task 9: Frontend — `ScoreOut` types

**Files:**
- Modify: `frontend/app/lib/api/reports.ts:15-21`

- [ ] **Step 1: Extend `ScoreOut`**

```typescript
export interface ScoreOut {
  score: number | null
  tier_label: string
  tone: string
  confidence: Confidence
  coverage: number
  session_score?: number | null
  holistic_delta?: number | null
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend/app && npm run type-check`
Expected: PASS (zero errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/api/reports.ts
git commit -m "feat(reports-fe): ScoreOut session_score + holistic_delta"
```

---

## Task 10: Frontend — Session-score provenance sub-line

**Files:**
- Modify: `frontend/app/components/dashboard/reports/ScoresCard.tsx`
- Test: `frontend/app/tests/components/ScoresCard.test.tsx`

- [ ] **Step 1: Write the failing composition test**

```tsx
import { render, screen } from '@testing-library/react'
import { ScoresCard } from '@/components/dashboard/reports/ScoresCard'
import type { ReportRead } from '@/lib/api/reports'

const base = (overrides: Partial<ReportRead['scores']['overall']> = {}) => ({
  verdict: 'borderline', verdict_reason: '', overall_score: 38, overall_coverage: 0.47,
  overall_confidence: 'medium', decision: { headline: 'x', why_positive: { title: '', body: '' },
  why_negative: { title: '', body: '' } },
  scores: { overall: { score: 38, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium',
    coverage: 0.47, session_score: 36, holistic_delta: 2, ...overrides } },
  quick_summary: '', strengths: [], concerns: [], questions: [],
  methodology: { note: '', charity_flags: [] }, signal_assessments: [],
  id: 'r', session_id: 's', status: 'ready', engine_version: 'v2', version: 1,
  scoring_manifest: null, human_decision: null, generated_at: null,
}) as unknown as ReportRead

it('shows the session-score provenance sub-line', () => {
  render(<ScoresCard report={base()} />)
  expect(screen.getByText(/Session score 3\.6/)).toBeInTheDocument()
  expect(screen.getByText(/\+0\.2/)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend/app && npm run test -- ScoresCard`
Expected: FAIL — sub-line not rendered.

- [ ] **Step 3: Implement — add the sub-line under the Overall gauge** (`ScoresCard.tsx`, inside the `my-3 flex justify-center` block area, after the `<ScoreGauge .. label="Overall" />`)

```tsx
      <div className="my-3 flex flex-col items-center">
        <ScoreGauge score={overall?.score ?? null} label="Overall" size={118} toneOverride={verdictTone} />
        {overall?.session_score != null && (overall.holistic_delta ?? 0) !== 0 && (
          <div className="mt-1 text-[10px]" style={{ color: 'var(--px-fg-4)' }}
               title="Deterministic session score, plus a bounded holistic adjustment. See methodology.">
            Session score {(overall.session_score / 10).toFixed(1)}
            {' · holistic '}{(overall.holistic_delta as number) > 0 ? '+' : ''}
            {((overall.holistic_delta as number) / 10).toFixed(1)}
          </div>
        )}
      </div>
```

- [ ] **Step 4: Run test**

Run: `cd frontend/app && npm run test -- ScoresCard`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/ScoresCard.tsx frontend/app/tests/components/ScoresCard.test.tsx
git commit -m "feat(reports-fe): show session-score + holistic provenance under Overall"
```

---

## Task 11: Frontend — audit-table score column + bluff chip

**Files:**
- Modify: `frontend/app/components/dashboard/reports/SignalAuditTable.tsx`

- [ ] **Step 1: Add a Score column header** (after the `Grade` `<th>`)

```tsx
              <th className="py-1 pr-2 font-semibold">Score</th>
```

- [ ] **Step 2: Render score + a thin/bluff chip per row** (replace the `Grade` `<td>` and add the score `<td>`)

```tsx
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  {a.grade ?? '—'}
                  {a.grade === 'thin' && (
                    <span className="ml-1 rounded px-1 text-[9px] font-semibold"
                          style={{ background: 'var(--px-caution-bg)', color: 'var(--px-caution)' }}
                          title="Correct vocabulary but no demonstrated depth — possible bluff.">
                      thin
                    </span>
                  )}
                </td>
                <td className="py-1 pr-2 tabular-nums" style={{ color: 'var(--px-fg-3)' }}>
                  {a.score != null ? (a.score / 10).toFixed(1) : '—'}
                </td>
```

- [ ] **Step 3: Type-check + lint**

Run: `cd frontend/app && npm run type-check && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/reports/SignalAuditTable.tsx
git commit -m "feat(reports-fe): per-signal score column + thin-evidence chip"
```

---

## Task 12: Frontend — reconcile gauge band tones with backend thresholds

**Files:**
- Modify: `frontend/app/components/dashboard/reports/report-format.ts:59-65`

The backend verdict uses ADVANCE=65 / REJECT=40; the gauge `scoreBandTone` currently uses 75/55. Align them so dimension-gauge coloring never disagrees with the verdict.

- [ ] **Step 1: Update `scoreBandTone`**

```typescript
/** Tier tone from a 0–100 score, aligned to backend verdict thresholds
 *  (ADVANCE_THRESHOLD 65 / REJECT_THRESHOLD 40 in reporting/scoring/constants.py). */
export function scoreBandTone(score: number | null): Tone {
  if (score === null || score === undefined) return 'neutral'
  if (score >= 65) return 'ok'
  if (score >= 40) return 'caution'
  return 'danger'
}
```

- [ ] **Step 2: Type-check + run report-format tests if present**

Run: `cd frontend/app && npm run type-check && npm run test -- report-format`
Expected: PASS (no test file → "no tests found" is acceptable; type-check must pass).

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/reports/report-format.ts
git commit -m "fix(reports-fe): align gauge band tones with backend verdict thresholds"
```

---

## Task 13: Frontend — reports hub sort-by-score

**Files:**
- Modify: `frontend/app/app/(dashboard)/reports/page.tsx`

- [ ] **Step 1: Add client-side sort state + a sortable Score header**

In `ReportsPage`, after `const isSuperAdmin = ...`:

```tsx
  const [sortByScore, setSortByScore] = useState(false)
  const items = (data?.items ?? []).slice().sort((a, b) =>
    sortByScore ? (b.overall_score ?? -1) - (a.overall_score ?? -1) : 0,
  )
```

Add `import { useState } from 'react'` at the top. Replace `data.items.map(...)` with `items.map(...)`. Replace the Score `<th>` with a sort toggle:

```tsx
                <th className="px-4 py-2.5 text-right text-[10.5px] font-semibold uppercase tracking-wide">
                  <button type="button" onClick={() => setSortByScore((v) => !v)}
                          className="uppercase tracking-wide hover:underline"
                          style={{ color: sortByScore ? 'var(--px-accent)' : 'inherit' }}>
                    Score {sortByScore ? '↓' : ''}
                  </button>
                </th>
```

- [ ] **Step 2: Type-check + lint**

Run: `cd frontend/app && npm run type-check && npm run lint`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add "frontend/app/app/(dashboard)/reports/page.tsx"
git commit -m "feat(reports-fe): sort the reports hub by Overall score (candidate ranking)"
```

---

## Task 14: Live verification — regenerate the two reports

**Files:** none (operational verification).

- [ ] **Step 1: Ensure the stack is running**

Run: `cd backend/nexus && docker compose up -d nexus nexus-worker redis`
Expected: containers healthy.

- [ ] **Step 2: Re-score both sessions via the worker** (force=True, bypasses idempotency)

Run:
```bash
docker compose exec nexus python -c "
import asyncio
from app.modules.reporting.actors import _score_session_report_async
from uuid import UUID
TENANT='<TENANT_ID>'   # fill from: SELECT tenant_id FROM sessions WHERE id='c7173674-...';
for sid in ['c7173674-7795-4268-b4ab-829ad45b801b','bc7ba6d3-848b-49f7-8311-0aa01cb8b4aa']:
    asyncio.run(_score_session_report_async(UUID(sid), UUID(TENANT), 'verify-'+sid[:8], True))
print('done')
"
```

Get `<TENANT_ID>` first:
```bash
docker exec supabase_db_backend psql -U postgres -d postgres -tAc \
 "SELECT tenant_id FROM sessions WHERE id='c7173674-7795-4268-b4ab-829ad45b801b';"
```

- [ ] **Step 3: Verify the numbers in the DB — the inversion is gone**

Run:
```bash
docker exec supabase_db_backend psql -U postgres -d postgres -P pager=off -c "
SELECT left(session_id::text,8) sess, verdict, overall_score,
       (scoring_manifest->'evidence_grounding_summary'->>'session_score') session_score,
       (scoring_manifest->'evidence_grounding_summary'->>'holistic_delta') delta
FROM session_reports
WHERE session_id IN ('c7173674-7795-4268-b4ab-829ad45b801b','bc7ba6d3-848b-49f7-8311-0aa01cb8b4aa')
ORDER BY overall_score;"
```
Expected: `bc7ba6d3` verdict `reject` with `overall_score` **below** `c7173674`'s `borderline` score (reject ≤ ~35, borderline ≤ ~60).

- [ ] **Step 4: Visually verify both report pages render**

Open both URLs (from the task brief) in the browser; confirm: Overall gauge matches the new number, the `Session score X.X · holistic ±Y.Y` sub-line shows, the audit table shows per-signal scores + any `thin` chips, and the verdict band reads correctly (`Not Recommended` for `bc7ba6d3`, `Borderline` for `c7173674`).

- [ ] **Step 5: Final full-suite gate**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/reporting -q -m "not prompt_quality"` and `cd frontend/app && npm run test && npm run type-check && npm run lint`
Expected: all PASS.

---

## Self-Review notes

- **Spec coverage:** §5.1 → T1/T2; §5.2 → T3; §5.3 → T5; §5.4 → T4; §5.5 → T8; §5.6 → T6; §5.7 (ScoreOut + manifest) → T5/T7; §5.8 → T13; §6 frontend → T9–T13; §7 anti-bias → T6 (prompt) + T8; §9 testing → T2/T3/T4/T5/T8 + FE tests. All covered.
- **Type consistency:** `score_signal(state, texture)` used identically in T2/T3/T7/T8; `signal_ceiling(signals, *, knockout_close, coverage)`, `clamp_to_ceiling(value, ceiling)`, `apply_holistic(session_score, delta, ceiling)`, `score_holistic(...)`, `HolisticAdjustmentOut.delta`, `ScoreOut.session_score/holistic_delta` consistent across tasks and FE.
- **No DB migration** (O1 = Overall is the sole main score; hub sorts existing `overall_score`).
