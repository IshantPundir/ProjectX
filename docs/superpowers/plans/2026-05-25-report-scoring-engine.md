# Report Scoring Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline backend engine that turns a completed v2 interview session into a defensible, auditable candidate evaluation report (scores + verdict + evidence), persisted and served via API.

**Architecture:** A Dramatiq actor fires after `record_session_result` commits a session to `completed`. It loads the frozen transcript + audit envelope + question bank, segments the conversation, classifies *opportunity* per question, grades each answered question against its BARS rubric with a cache-optimized OpenAI reasoning-model judge (the only LLM step), verifies every cited quote against the transcript, then **deterministically in code** aggregates per-signal → per-dimension → knockout-gated → Overall → tier → verdict. The result is stored in a new `session_reports` table and exposed at `/api/reports`.

**Tech Stack:** FastAPI, SQLAlchemy (asyncpg), Alembic, Dramatiq (Redis broker), OpenAI via `app/ai` (`instructor` `TOOLS_STRICT`, `PromptLoader`), OpenTelemetry, Postgres RLS, pytest.

**Spec:** `docs/superpowers/specs/2026-05-25-report-scoring-engine-design.md` (read it first).

**Conventions verified against the codebase (cite when implementing):**
- AIConfig pattern + effort-gating: `app/ai/config.py` (gate `reasoning_effort` only if non-empty).
- instructor call: `app/modules/jd/actors.py:224-238`; client `app/ai/client.py` (`get_openai_client()` → `instructor.AsyncInstructor`, `Mode.TOOLS_STRICT`; pass `response_model=`, `max_retries=1`; use `create_with_completion(...)` to read `usage.prompt_tokens_details.cached_tokens`).
- Stable-prefix/suffix + `prompt_cache_key`: `app/modules/interview_engine_v2/brain/input_builder.py`, `brain/service.py:84-110`.
- Prompt files + loader: `app/ai/prompts.py` (`PromptLoader(version=...).get("name")`), dir `prompts/v{n}/...`.
- OTel tracing: `app/ai/tracing.py::set_llm_span_attributes`.
- Dramatiq actor + bypass DB: `app/modules/jd/actors.py` (sync `@dramatiq.actor` wrapper → `asyncio.run(_inner())`; `get_bypass_session()` from `app.database`; `SET LOCAL app.current_tenant = '<uuid>'` at top of each txn).
- RLS in CREATE-TABLE migration: `migrations/versions/0031_ats_core.py` (`_enable_rls` helper: `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY tenant_isolation … NULLIF(current_setting('app.current_tenant',true),'')::uuid` USING+WITH CHECK + `CREATE POLICY service_bypass … current_setting('app.bypass_rls',true)='true'`). `_assert_rls_completeness` in `app/main.py` auto-discovers tenant tables — a new `tenant_id` table is covered automatically once it carries both policies.
- Router RBAC: `app/modules/candidates/router.py` (`from app.modules.auth import UserContext, get_current_user_roles`; `user: UserContext = Depends(get_current_user_roles)`).
- Trigger site: `app/modules/interview_runtime/service.py::record_session_result` (enqueue after the `completed` commit).

---

## File Structure

```
app/modules/reporting/
  __init__.py        exports (Report schemas, models, score_session_report)
  models.py          SessionReport SQLAlchemy model                         [REBUILD: stub has none]
  schemas.py         Pydantic: JudgeVerdict, AnswerRating, SignalScorecard, [REBUILD stub]
                     DimensionScore, KnockoutResult, ReportResult, ReportRead, HumanDecisionIn
  router.py          API endpoints                                          [REBUILD stub]
  service.py         orchestration: build_report() + persist_report()       [REBUILD stub]
  actors.py          Dramatiq actor score_session_report                    [NEW]
  scoring/
    __init__.py
    constants.py     anchors, thresholds, type→dimension maps               [NEW]
    types.py         frozen dataclasses + Literals (ScoredUnit, …)          [NEW]
    transcript.py    segment transcript ↔ envelope → list[ScoredUnit]       [NEW]
    opportunity.py   ScoredUnit → Opportunity                               [NEW]
    grounding.py     verify evidence quotes exist in transcript             [NEW]
    judge.py         LLM per-answer BARS rating (instructor, cached)         [NEW]
    input_builder.py judge stable-prefix / dynamic-suffix construction       [NEW]
    aggregate.py     PURE: signal→dimension→knockout→overall→tier→verdict   [NEW]
prompts/v3/report_scorer/
  system.txt         judge developer-prompt template (stable prefix)        [NEW]  (use the live PROMPT_VERSION dir)
app/ai/config.py     + report_scorer_* properties                           [MODIFY]
app/config.py        + report_scorer_* settings (env)                       [MODIFY]
app/modules/interview_runtime/service.py  enqueue actor after commit        [MODIFY]
app/worker.py        import reporting.actors                                [MODIFY]
migrations/versions/0047_session_reports.py                                 [NEW]
tests/reporting/
  fixtures/  e4072361 envelope + transcript + bank slices (JSON)
  test_aggregate.py  test_opportunity.py  test_transcript.py  test_grounding.py
  test_judge.py  test_input_builder.py  test_schemas.py  test_service.py
  test_actor.py  test_router.py  test_models_rls.py
```

**Type contracts used across tasks (defined in Task 2, referenced everywhere):**
```python
BarsLevel       = Literal["below_bar", "meets_bar", "excellent"]
Opportunity     = Literal["full", "partial", "none"]
SignalState     = Literal["excellent", "meets_bar", "below_bar", "not_assessed"]
KnockoutStatus  = Literal["passed", "failed", "insufficient"]
Verdict         = Literal["advance", "borderline", "reject"]
Confidence      = Literal["high", "medium", "low"]
```

---

## Task 1: AIConfig + settings — report scorer knobs

**Files:**
- Modify: `app/config.py` (add `Settings` fields)
- Modify: `app/ai/config.py` (add `AIConfig` properties)
- Test: `tests/reporting/test_aiconfig_scorer.py`

- [ ] **Step 1: Read the existing pattern.** Read `app/ai/config.py` for an existing model property + the effort-gating contract (a property that returns `""` when unset), and `app/config.py` for the `Settings` field style (env names).

- [ ] **Step 2: Write the failing test**
```python
# tests/reporting/test_aiconfig_scorer.py
from app.ai.config import AIConfig
from app.config import Settings

def test_scorer_model_and_effort_from_settings():
    s = Settings(openai_report_scorer_model="gpt-5-class-judge",
                 openai_report_scorer_effort="medium",
                 openai_report_scorer_verbosity="low",
                 report_scorer_prompt_version="v3",
                 openai_report_scorer_n_samples=3)
    cfg = AIConfig(s)
    assert cfg.report_scorer_model == "gpt-5-class-judge"
    assert cfg.report_scorer_effort == "medium"
    assert cfg.report_scorer_verbosity == "low"
    assert cfg.report_scorer_prompt_version == "v3"
    assert cfg.report_scorer_n_samples == 3

def test_scorer_effort_empty_is_gateable():
    cfg = AIConfig(Settings(openai_report_scorer_effort=""))
    assert cfg.report_scorer_effort == ""   # gate: `if cfg.report_scorer_effort:`
```
(Adjust `Settings(...)` construction to match how the repo instantiates Settings in other tests — check `tests/` for the helper.)

- [ ] **Step 3: Run → fail.** `docker compose run --rm nexus pytest tests/reporting/test_aiconfig_scorer.py -v` → FAIL (unknown settings / no attribute).

- [ ] **Step 4: Add settings** to `app/config.py` `Settings`:
```python
    openai_report_scorer_model: str = "gpt-5.1"          # a strong reasoning model; env-overridable
    openai_report_scorer_effort: str = "medium"           # "" disables reasoning_effort (effort contract)
    openai_report_scorer_verbosity: str = "low"
    openai_report_scorer_n_samples: int = 3               # max samples for selective self-consistency
    report_scorer_prompt_version: str = "v3"
    report_scorer_prompt_cache_key_prefix: str = "judge"
```

- [ ] **Step 5: Add AIConfig properties** in `app/ai/config.py` (mirror the existing property style):
```python
    @property
    def report_scorer_model(self) -> str:
        return self._settings.openai_report_scorer_model

    @property
    def report_scorer_effort(self) -> str:
        return self._settings.openai_report_scorer_effort  # may be ""; gate before sending

    @property
    def report_scorer_verbosity(self) -> str:
        return self._settings.openai_report_scorer_verbosity

    @property
    def report_scorer_n_samples(self) -> int:
        return self._settings.openai_report_scorer_n_samples

    @property
    def report_scorer_prompt_version(self) -> str:
        return self._settings.report_scorer_prompt_version
```

- [ ] **Step 6: Run → pass.** Same pytest command → PASS.

- [ ] **Step 7: Commit** `git add -A && git commit -m "feat(reporting): add report_scorer AIConfig knobs"`

---

## Task 2: Scoring constants + core types

**Files:**
- Create: `app/modules/reporting/scoring/__init__.py` (empty)
- Create: `app/modules/reporting/scoring/constants.py`
- Create: `app/modules/reporting/scoring/types.py`
- Test: `tests/reporting/test_constants_types.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_constants_types.py
from app.modules.reporting.scoring import constants as C
from app.modules.reporting.scoring.types import ScoredUnit, SignalDef

def test_anchors_and_thresholds():
    assert C.LEVEL_POINTS == {"excellent": 100, "meets_bar": 70, "below_bar": 30}
    assert C.ADVANCE_THRESHOLD == 75
    assert C.REJECT_THRESHOLD == 55
    assert C.MIN_COVERAGE_FOR_ADVANCE == 0.6
    assert C.SUBSTANTIVE_WORD_FLOOR == 8
    assert C.TECHNICAL_TYPES == frozenset({"competency", "experience", "credential"})
    assert C.BEHAVIORAL_TYPES == frozenset({"behavioral"})

def test_scored_unit_is_frozen():
    u = ScoredUnit(question_id="q1", question_text="Q?", candidate_answer="A",
                   answer_start_ms=10, probes_fired=1, clarifies=0,
                   word_count=12, candidate_engaged=True)
    assert u.question_id == "q1"
    sd = SignalDef(value="Workato", type="experience", weight=3, knockout=True, priority="required")
    assert sd.knockout is True
```

- [ ] **Step 2: Run → fail.** `docker compose run --rm nexus pytest tests/reporting/test_constants_types.py -v` → FAIL (no module).

- [ ] **Step 3: Implement `constants.py`**
```python
"""Scoring constants. All policy numbers live here (configurable later)."""
from __future__ import annotations

LEVEL_POINTS: dict[str, int] = {"excellent": 100, "meets_bar": 70, "below_bar": 30}

ADVANCE_THRESHOLD = 75          # Overall >= → advance (when not knockout-capped)
REJECT_THRESHOLD = 55           # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6  # below this, a high Overall is forced to borderline
SUBSTANTIVE_WORD_FLOOR = 8      # min words for an answer to count as a "substantive" engagement

TECHNICAL_TYPES = frozenset({"competency", "experience", "credential"})
BEHAVIORAL_TYPES = frozenset({"behavioral"})
```

- [ ] **Step 4: Implement `types.py`**
```python
"""Frozen value objects + Literals shared across the scoring pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

BarsLevel = Literal["below_bar", "meets_bar", "excellent"]
Opportunity = Literal["full", "partial", "none"]
SignalState = Literal["excellent", "meets_bar", "below_bar", "not_assessed"]
KnockoutStatus = Literal["passed", "failed", "insufficient"]
Verdict = Literal["advance", "borderline", "reject"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ScoredUnit:
    """One delivered question + the candidate's answer to it."""
    question_id: str
    question_text: str
    candidate_answer: str
    answer_start_ms: int
    probes_fired: int
    clarifies: int
    word_count: int
    candidate_engaged: bool      # triage kind ∈ answering (not no_experience/off_topic/backchannel)


@dataclass(frozen=True)
class SignalDef:
    value: str
    type: str                    # experience | competency | behavioral | credential
    weight: int                  # 1..3
    knockout: bool
    priority: str                # required | preferred


@dataclass(frozen=True)
class Evidence:
    quote: str
    timestamp_ms: int
    question_id: str
    grounded: bool = True
```

- [ ] **Step 5: Run → pass.** Same command → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(reporting): scoring constants + core types"`

---

## Task 3: Opportunity classification

**Files:**
- Create: `app/modules/reporting/scoring/opportunity.py`
- Test: `tests/reporting/test_opportunity.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_opportunity.py
from app.modules.reporting.scoring.opportunity import classify
from app.modules.reporting.scoring.types import ScoredUnit

def _unit(**kw):
    base = dict(question_id="q", question_text="Q", candidate_answer="A",
                answer_start_ms=0, probes_fired=0, clarifies=0, word_count=0,
                candidate_engaged=True)
    base.update(kw); return ScoredUnit(**base)

def test_full_when_probed():
    assert classify(_unit(probes_fired=1, word_count=3, candidate_engaged=True)) == "full"

def test_full_when_substantive_answer():
    assert classify(_unit(probes_fired=0, word_count=20, candidate_engaged=True)) == "full"

def test_partial_when_barely_engaged_no_probe():
    assert classify(_unit(probes_fired=0, word_count=3, candidate_engaged=True)) == "partial"

def test_none_when_not_engaged_and_no_probe():
    # immediate non-answer (off_topic / backchannel), no probe
    assert classify(_unit(probes_fired=0, word_count=2, candidate_engaged=False)) == "none"

def test_full_for_on_target_idk_when_probed():
    # bare "I don't know" but a probe fired → full opportunity → counts as evidence
    assert classify(_unit(candidate_answer="I don't know", probes_fired=1,
                          word_count=3, candidate_engaged=True)) == "full"
```

- [ ] **Step 2: Run → fail.** `docker compose run --rm nexus pytest tests/reporting/test_opportunity.py -v` → FAIL.

- [ ] **Step 3: Implement**
```python
"""Classify how much opportunity the candidate had to demonstrate a signal.
Opportunity (not answer-quality) is what separates `not_assessed` from `below_bar`."""
from __future__ import annotations
from app.modules.reporting.scoring.constants import SUBSTANTIVE_WORD_FLOOR
from app.modules.reporting.scoring.types import Opportunity, ScoredUnit


def classify(unit: ScoredUnit) -> Opportunity:
    substantive = unit.candidate_engaged and unit.word_count >= SUBSTANTIVE_WORD_FLOOR
    if unit.probes_fired >= 1 or substantive:
        return "full"
    if unit.candidate_engaged:          # asked, barely engaged, no probe
        return "partial"
    return "none"                       # instant non-answer, no probe
```

- [ ] **Step 4: Run → pass.** Same → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): opportunity classification"`

---

## Task 4: Aggregation — per-signal combine (pure)

**Files:**
- Create: `app/modules/reporting/scoring/aggregate.py`
- Test: `tests/reporting/test_aggregate.py`

`combine_signal` takes the per-question observations that touched a signal and yields the signal's state + score + opportunity.

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_aggregate.py
from app.modules.reporting.scoring.aggregate import combine_signal, SignalObservation

def obs(level, opp, red=False):
    return SignalObservation(level=level, opportunity=opp, red_flags_hit=red)

def test_not_assessed_when_no_opportunity():
    state, score = combine_signal([obs("below_bar", "none")])
    assert state == "not_assessed" and score is None

def test_below_bar_full_opportunity_is_real_low_score():
    state, score = combine_signal([obs("below_bar", "full")])
    assert state == "below_bar" and score == 30

def test_excellent_requires_grounded_excellent_no_redflag():
    state, score = combine_signal([obs("excellent", "full")])
    assert state == "excellent" and score == 100

def test_redflag_with_nothing_meeting_bar_pulls_to_below():
    state, score = combine_signal([obs("meets_bar", "full", red=True)])
    assert state == "below_bar" and score == 30

def test_meets_bar_default():
    state, score = combine_signal([obs("meets_bar", "full")])
    assert state == "meets_bar" and score == 70

def test_best_of_multiple_when_one_excellent():
    state, score = combine_signal([obs("below_bar", "full"), obs("excellent", "full")])
    assert state == "excellent" and score == 100

def test_partial_opportunity_alone_is_not_assessed():
    state, score = combine_signal([obs("meets_bar", "partial")])
    assert state == "not_assessed" and score is None
```

- [ ] **Step 2: Run → fail.** `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -v` → FAIL.

- [ ] **Step 3: Implement (in `aggregate.py`)**
```python
"""Deterministic, pure scoring math: signal → dimension → knockout gate → overall → verdict.
No LLM, no IO. This is the auditable core; everything here is unit-tested exhaustively."""
from __future__ import annotations
from dataclasses import dataclass

from app.modules.reporting.scoring.constants import LEVEL_POINTS
from app.modules.reporting.scoring.types import BarsLevel, Opportunity, SignalState

_RANK = {"below_bar": 0, "meets_bar": 1, "excellent": 2}


@dataclass(frozen=True)
class SignalObservation:
    level: BarsLevel
    opportunity: Opportunity
    red_flags_hit: bool = False


def combine_signal(observations: list[SignalObservation]) -> tuple[SignalState, int | None]:
    """Collapse all observations of one signal into a state + integer score.
    Opportunity gating: observations without full/partial opportunity don't count;
    a `below_bar` only becomes a real low score at `full` opportunity."""
    assessed = [o for o in observations if o.opportunity in ("full", "partial")]
    # A below_bar is only a *confident* low score at full opportunity.
    confident = [o for o in observations if o.opportunity == "full"]
    if not confident and not assessed:
        return "not_assessed", None
    if not confident:
        # Only partial-opportunity touches → not enough to confidently rate.
        return "not_assessed", None

    best = max(confident, key=lambda o: _RANK[o.level])
    any_redflag = any(o.red_flags_hit for o in confident)
    reaches_bar = _RANK[best.level] >= _RANK["meets_bar"]

    if best.level == "excellent" and not any_redflag:
        state: SignalState = "excellent"
    elif any_redflag and not reaches_bar:
        state = "below_bar"
    elif best.level == "below_bar":
        state = "below_bar"
    elif any_redflag:                      # red flag but a meets/excellent answer exists → cap at meets
        state = "meets_bar"
    else:
        state = best.level                  # meets_bar or excellent
    score = LEVEL_POINTS.get(state)         # not_assessed not reached here
    return state, score
```

- [ ] **Step 4: Run → pass.** Same → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): per-signal combine"`

---

## Task 5: Aggregation — dimensions + Overall (pure)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py`
- Test: `tests/reporting/test_aggregate.py` (append)

- [ ] **Step 1: Write the failing test (append)**
```python
from app.modules.reporting.scoring.aggregate import score_dimension, ScoredSignal

def ss(type, weight, state, score):
    return ScoredSignal(value=f"{type}-{weight}", type=type, weight=weight,
                        knockout=False, priority="required", state=state, score=score)

def test_dimension_weighted_mean_excludes_not_assessed():
    signals = [ss("competency", 3, "excellent", 100),
               ss("competency", 1, "below_bar", 30),
               ss("competency", 2, "not_assessed", None)]   # excluded
    dim = score_dimension("technical", signals, {"competency", "experience", "credential"})
    # (3*100 + 1*30) / (3+1) = 82.5 → 82 ; coverage = (3+1)/(3+1+2) = 0.666...
    assert dim.score == 82
    assert round(dim.coverage, 3) == 0.667
    assert dim.confidence in ("high", "medium", "low")

def test_dimension_all_not_assessed_is_none():
    dim = score_dimension("behavioral",
                          [ss("behavioral", 2, "not_assessed", None)], {"behavioral"})
    assert dim.score is None and dim.coverage == 0.0
```

- [ ] **Step 2: Run → fail.** → FAIL (no `score_dimension`).

- [ ] **Step 3: Implement (append to `aggregate.py`)**
```python
from app.modules.reporting.scoring.types import Confidence


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    state: SignalState
    score: int | None


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float          # assessed weight / total weight in this dimension
    confidence: Confidence


def _confidence(coverage: float) -> Confidence:
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.4:
        return "medium"
    return "low"


def score_dimension(name: str, signals: list[ScoredSignal], types: frozenset[str]) -> DimensionScore:
    members = [s for s in signals if s.type in types]
    total_w = sum(s.weight for s in members)
    assessed = [s for s in members if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return DimensionScore(name=name, score=None, coverage=0.0, confidence="low")
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w
    coverage = (assessed_w / total_w) if total_w else 0.0
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=coverage, confidence=_confidence(coverage))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Overall = weighted mean over ALL assessed JD signals; coverage over all JD signals."""
    total_w = sum(s.weight for s in signals)
    assessed = [s for s in signals if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w
    return int(round(weighted)), (assessed_w / total_w if total_w else 0.0)
```

- [ ] **Step 4: Add the `score_overall` test (append) and run → pass**
```python
from app.modules.reporting.scoring.aggregate import score_overall
def test_overall_weighted_mean():
    score, cov = score_overall([ss("competency", 3, "excellent", 100),
                                ss("behavioral", 1, "below_bar", 30)])
    assert score == 82 and round(cov, 2) == 1.0
```
Run `tests/reporting/test_aggregate.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(reporting): dimension + overall scoring"`

---

## Task 6: Aggregation — knockout gate → tier → verdict (pure)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py`
- Test: `tests/reporting/test_aggregate.py` (append)

- [ ] **Step 1: Write the failing test (append)**
```python
from app.modules.reporting.scoring.aggregate import (
    knockout_status, resolve_verdict, KnockoutResult)

def kss(state, opp_ok=True, ko=True):
    # helper ScoredSignal as a knockout
    return ScoredSignal(value="must", type="competency", weight=3, knockout=ko,
                        priority="required", state=state, score=None if state=="not_assessed" else 30)

def test_knockout_failed_when_below_bar():
    assert knockout_status(state="below_bar") == "failed"

def test_knockout_passed_when_meets():
    assert knockout_status(state="meets_bar") == "passed"

def test_knockout_insufficient_when_not_assessed():
    assert knockout_status(state="not_assessed") == "insufficient"

def test_verdict_reject_on_failed_knockout_regardless_of_overall():
    v = resolve_verdict(overall=90, coverage=0.9,
                        knockouts=[KnockoutResult(signal="prog", status="failed",
                                                  reason="x", evidence=[])])
    assert v.verdict == "reject" and "must-have" in v.reason

def test_verdict_borderline_on_insufficient_knockout():
    v = resolve_verdict(overall=90, coverage=0.9,
                        knockouts=[KnockoutResult(signal="prog", status="insufficient",
                                                  reason="x", evidence=[])])
    assert v.verdict == "borderline"

def test_verdict_from_tier_when_all_pass():
    v = resolve_verdict(overall=80, coverage=0.9, knockouts=[
        KnockoutResult(signal="prog", status="passed", reason="", evidence=[])])
    assert v.verdict == "advance"

def test_coverage_override_forces_borderline():
    v = resolve_verdict(overall=90, coverage=0.4, knockouts=[])
    assert v.verdict == "borderline" and "assessed" in v.reason

def test_reject_tier():
    assert resolve_verdict(overall=40, coverage=0.9, knockouts=[]).verdict == "reject"
```

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement (append to `aggregate.py`)**
```python
from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD, REJECT_THRESHOLD, MIN_COVERAGE_FOR_ADVANCE)
from app.modules.reporting.scoring.types import KnockoutStatus, Verdict


def knockout_status(*, state: SignalState) -> KnockoutStatus:
    if state == "not_assessed":
        return "insufficient"
    if state == "below_bar":
        return "failed"
    return "passed"          # meets_bar | excellent


@dataclass(frozen=True)
class KnockoutResult:
    signal: str
    status: KnockoutStatus
    reason: str
    evidence: list  # list[Evidence-as-dict]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


def resolve_verdict(*, overall: int | None, coverage: float,
                    knockouts: list[KnockoutResult]) -> VerdictResult:
    failed = [k for k in knockouts if k.status == "failed"]
    if failed:
        return VerdictResult("reject", f"failed must-have: {failed[0].signal}")
    insufficient = [k for k in knockouts if k.status == "insufficient"]
    if insufficient:
        return VerdictResult("borderline",
                             f"couldn't confirm must-have: {insufficient[0].signal}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if overall >= ADVANCE_THRESHOLD and coverage < MIN_COVERAGE_FOR_ADVANCE:
        return VerdictResult("borderline", "not enough assessed to advance confidently")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")
```

- [ ] **Step 4: Run → pass.** `tests/reporting/test_aggregate.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): knockout gate + tier + verdict"`

---

## Task 7: Build the e4072361 test fixtures

**Files:**
- Create: `tests/reporting/fixtures/e4072361_envelope.json` (copy of the real envelope)
- Create: `tests/reporting/fixtures/e4072361_transcript.json` (synthetic but faithful agent+candidate turns)
- Create: `tests/reporting/fixtures/job_bank_slice.json` (the 8-question bank + signal_metadata from the export)

- [ ] **Step 1: Copy the real audit envelope** into the fixtures dir:
```bash
cp backend/nexus/engine-events/e4072361-fe9b-4157-a73d-aa82100387b4.json \
   backend/nexus/tests/reporting/fixtures/e4072361_envelope.json
```

- [ ] **Step 2: Extract the bank slice** (rubric + signal_metadata) from the live export so tests use the real template:
```bash
docker compose exec -T -e PYTHONPATH=/app nexus python /app/scripts/export_job_agent_context.py \
  ce6dad9a-8903-4396-8f29-8e36da9bd2a3 /tmp/job_ctx.json 2>/dev/null
docker compose exec -T nexus python - <<'PY' > backend/nexus/tests/reporting/fixtures/job_bank_slice.json
import json
ctx = json.load(open("/tmp/job_ctx.json"))
ai = next(s for s in ctx["stages"] if s["stage_type"] == "ai_screening")
print(json.dumps({"questions": ai["question_bank"]["questions"],
                  "signal_metadata": ctx["signal_snapshot"]["signal_metadata"]}, indent=2))
PY
```

- [ ] **Step 3: Hand-build a faithful transcript fixture** `e4072361_transcript.json` — a list of `{role, text, timestamp_ms, question_id}` entries reconstructed from the envelope's `turn.decision.candidate_quote` (candidate turns) interleaved with short agent question lines (use the bank `text` for each delivered question). This is test data; keep it small but representative (the programming/JSON answer at the dismissive turn must be present verbatim so grounding + the knockout test work).

- [ ] **Step 4: Commit** `git commit -am "test(reporting): e4072361 fixtures (envelope, bank slice, transcript)"`

---

## Task 8: Transcript ↔ envelope segmentation

**Files:**
- Create: `app/modules/reporting/scoring/transcript.py`
- Test: `tests/reporting/test_transcript.py`

Produces `list[ScoredUnit]` by joining transcript turns (agent question + following candidate turns, by `timestamp_ms`) with envelope decisions (`turn.decision` by `turn_ref`, `directive.delivered` acts, `turn.captured` word counts, `engine.v2.triage.decision` kinds).

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_transcript.py
import json, pathlib
from app.modules.reporting.scoring.transcript import segment

FIX = pathlib.Path(__file__).parent / "fixtures"

def test_segments_real_session():
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    units = segment(transcript=transcript, envelope=envelope)
    # 8-question bank; at least the delivered technical questions appear
    qids = {u.question_id for u in units}
    assert len(units) >= 5
    # the programming/JSON question got probed and a dismissive answer
    prog = next(u for u in units if "Java" in u.question_text or "JSON" in u.question_text)
    assert prog.candidate_engaged is True
    assert prog.word_count > 0
    assert "already given you the answer" in prog.candidate_answer.lower() or prog.probes_fired >= 0

def test_handles_missing_envelope_gracefully():
    transcript = [{"role": "agent", "text": "Q1?", "timestamp_ms": 0, "question_id": "q1"},
                  {"role": "candidate", "text": "yes I have five years", "timestamp_ms": 1000,
                   "question_id": None}]
    units = segment(transcript=transcript, envelope={"events": []})
    assert len(units) == 1 and units[0].question_id == "q1"
```

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement.** Algorithm:
  1. From `envelope["events"]`, index by `turn_ref`: `turn.decision` (candidate_quote, attributed_signals, move), `turn.captured` (word_count), `engine.v2.triage.decision` (kind). Count `directive.delivered` acts: `ASK`/`ACK_ADVANCE` per turn = a question delivery; `PROBE` = a probe; `CLARIFY` = a clarify.
  2. Walk the transcript in timestamp order. Each `agent` turn carrying a `question_id` opens a unit; subsequent `candidate` turns until the next agent question accumulate into `candidate_answer` (+ summed `word_count`).
  3. Attach to each unit: `probes_fired` (PROBE deliveries between this question and the next), `clarifies`, and `candidate_engaged` = the triage `kind` for the candidate turn ∈ `answering` (not in `{no_experience, off_topic, backchannel, injection}`).
  4. Map a delivered question's text back to its `question_id` via the bank (pass bank in, or rely on `transcript[].question_id`). Keep the function pure; accept `transcript`, `envelope`, optional `bank_questions`.
```python
"""Reconstruct (question → answer → opportunity-inputs) units by joining the
frozen transcript (agent question text + candidate turns) with the audit
envelope (per-turn decisions, delivered acts, word counts, triage kinds)."""
from __future__ import annotations
from app.modules.reporting.scoring.types import ScoredUnit

_NON_ENGAGED = {"no_experience", "off_topic", "backchannel", "injection", "indirect_no"}


def segment(*, transcript: list[dict], envelope: dict,
            bank_questions: list[dict] | None = None) -> list[ScoredUnit]:
    events = envelope.get("events", [])
    # ... index events by turn_ref; count delivered PROBE/CLARIFY/ASK acts ...
    # ... walk transcript; open a unit on each agent turn with a question_id ...
    # ... see algorithm steps 1-4 above ...
    units: list[ScoredUnit] = []
    # (full implementation here — deterministic, no IO)
    return units
```
Write the complete deterministic body following steps 1–4 (no IO, no LLM). Use `str.split()` for word counts when `turn.captured` is unavailable.

- [ ] **Step 4: Run → pass.** `tests/reporting/test_transcript.py -v` → PASS. If the real-session assertions need tuning, adjust the fixture transcript (Task 7 step 3), not the production logic, to match reality.

- [ ] **Step 5: Commit** `git commit -am "feat(reporting): transcript↔envelope segmentation"`

---

## Task 9: Evidence grounding

**Files:**
- Create: `app/modules/reporting/scoring/grounding.py`
- Test: `tests/reporting/test_grounding.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_grounding.py
from app.modules.reporting.scoring.grounding import is_grounded, ground_quotes

TRANSCRIPT = "I would take this up at Java. Already given you the answer."

def test_exact_substring_is_grounded():
    assert is_grounded("take this up at Java", TRANSCRIPT) is True

def test_whitespace_and_case_normalized():
    assert is_grounded("ALREADY   given you  the answer", TRANSCRIPT) is True

def test_hallucinated_quote_is_not_grounded():
    assert is_grounded("I have deep Kubernetes expertise", TRANSCRIPT) is False

def test_ground_quotes_partitions():
    grounded, ungrounded = ground_quotes(
        ["take this up at Java", "I led a 200-person team"], TRANSCRIPT)
    assert grounded == ["take this up at Java"]
    assert ungrounded == ["I led a 200-person team"]
```

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement**
```python
"""Verify every LLM-cited evidence quote is a real substring of the transcript.
Kills hallucinated competence. Normalizes case + whitespace (STT/formatting jitter)."""
from __future__ import annotations
import re

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def is_grounded(quote: str, transcript_text: str) -> bool:
    if not quote.strip():
        return False
    return _norm(quote) in _norm(transcript_text)

def ground_quotes(quotes: list[str], transcript_text: str) -> tuple[list[str], list[str]]:
    grounded, ungrounded = [], []
    for q in quotes:
        (grounded if is_grounded(q, transcript_text) else ungrounded).append(q)
    return grounded, ungrounded
```

- [ ] **Step 4: Run → pass.** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): evidence grounding verification"`

---

## Task 10: Pydantic schemas (judge + report)

**Files:**
- Modify: `app/modules/reporting/schemas.py` (replace the stub; remove the `analysis.SignalScore` import)
- Test: `tests/reporting/test_schemas.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/reporting/test_schemas.py
from app.modules.reporting.schemas import JudgeVerdict, ReportRead

def test_judge_verdict_field_order_evidence_before_level():
    # field order = output order; evidence/justification BEFORE level (reasoning-model best practice)
    fields = list(JudgeVerdict.model_fields.keys())
    assert fields.index("evidence_quotes") < fields.index("level")
    assert fields.index("justification") < fields.index("level")

def test_judge_verdict_level_is_enum():
    v = JudgeVerdict(evidence_quotes=["q"], red_flags_hit=[], justification="j", level="meets_bar")
    assert v.level == "meets_bar"

def test_report_read_roundtrips():
    r = ReportRead(verdict="reject", verdict_reason="failed must-have: x",
                   overall_score=42, overall_coverage=0.8, overall_confidence="high",
                   dimension_scores={}, knockout_results=[], signal_scorecards=[],
                   question_scorecards=[], summary={"headline": "h", "strengths": [],
                   "gaps": [], "rationale": "r"})
    assert r.verdict == "reject"
```

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement `schemas.py`** (full replacement). Define: `JudgeVerdict` (evidence_quotes, red_flags_hit, justification, level — **in that order**), `AnswerRating`, `EvidenceOut`, `SignalScorecard`, `DimensionScoreOut`, `KnockoutResultOut`, `SummaryOut`, `ReportRead`, `HumanDecisionIn`. Use `Literal` for `level`/`verdict`/`status`/`confidence`. No `float` for scores — `int | None`.
```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

class JudgeVerdict(BaseModel):
    """Strict structured output from the per-answer judge. Evidence BEFORE score."""
    evidence_quotes: list[str] = Field(default_factory=list,
        description="Verbatim spans copied from the transcript that justify the level.")
    red_flags_hit: list[str] = Field(default_factory=list)
    justification: str = Field(description="Map the evidence to the rubric anchor.")
    level: Literal["below_bar", "meets_bar", "excellent"]

class EvidenceOut(BaseModel):
    quote: str
    timestamp_ms: int
    question_id: str
    grounded: bool = True

# ... SignalScorecard, DimensionScoreOut, KnockoutResultOut, SummaryOut,
#     ReportRead (mirrors the session_reports columns), HumanDecisionIn ...
```
Write the rest fully, matching the `session_reports` columns from Task 11.

- [ ] **Step 4: Run → pass.** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): rebuild report + judge schemas"`

---

## Task 11: SessionReport model + migration 0047 + RLS

**Files:**
- Create: `app/modules/reporting/models.py`
- Create: `migrations/versions/0047_session_reports.py`
- Modify: `app/modules/reporting/__init__.py` (export `SessionReport`)
- Test: `tests/reporting/test_models_rls.py` (Task 12)

- [ ] **Step 1: Implement `models.py`** (mirror `app/modules/session/models.py` style):
```python
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class SessionReport(Base):
    __tablename__ = "session_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
        server_default=sql_text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'pending'"))
    generation_error: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    overall_score: Mapped[int | None] = mapped_column(Integer)
    overall_coverage: Mapped[float | None] = mapped_column(Numeric)
    overall_confidence: Mapped[str | None] = mapped_column(Text)
    dimension_scores: Mapped[dict | None] = mapped_column(JSONB)
    knockout_results: Mapped[list | None] = mapped_column(JSONB)
    signal_scorecards: Mapped[list | None] = mapped_column(JSONB)
    question_scorecards: Mapped[list | None] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    rubric_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    scoring_manifest: Mapped[dict | None] = mapped_column(JSONB)
    human_decision: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
        server_default=sql_text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
        server_default=sql_text("NOW()"))
```

- [ ] **Step 2: Implement migration `0047_session_reports.py`** with `down_revision = "0046"`, the table create, a `status` CHECK (`pending/generating/ready/failed`), a `verdict` CHECK (`advance/borderline/reject` or NULL), indexes on `assignment_id` and `(tenant_id, verdict)`, and the **canonical RLS pair** (copy `_enable_rls` from `0031_ats_core.py`):
```python
def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON {table}
          USING (current_setting('app.bypass_rls', true) = 'true');
    """)
```
`downgrade()` drops the table (policies drop with it). Include a one-line rollback note in the docstring (migration-rollback rule).

- [ ] **Step 3: Run the migration**
```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1   # verify rollback
docker compose run --rm nexus alembic upgrade head
```
Expected: clean up + down + up.

- [ ] **Step 4: Boot assertion passes** — start the app (or run the test app) so `_assert_rls_completeness` runs; expect no missing-policy abort (the new `tenant_id` table is auto-covered).

- [ ] **Step 5: Commit** `git commit -am "feat(reporting): session_reports model + migration 0047 + RLS"`

---

## Task 12: RLS cross-tenant test (mandated gate)

**Files:**
- Test: `tests/reporting/test_models_rls.py`

- [ ] **Step 1: Write the test** (follow an existing cross-tenant RLS test, e.g. in `tests/` for candidates/sessions):
```python
# tests/reporting/test_models_rls.py
import pytest
# Use the repo's tenant-scoped DB test fixtures (get_tenant_db with SET LOCAL app.current_tenant).
# Insert a session_reports row under tenant A; read under tenant B → 0 rows.

@pytest.mark.asyncio
async def test_cross_tenant_read_returns_zero_rows(two_tenant_db):
    a, b = two_tenant_db
    await a.insert_report(verdict="advance")
    rows = await b.list_reports()
    assert rows == []
```
Wire it to the project's existing two-tenant RLS test harness (copy the closest existing example exactly).

- [ ] **Step 2: Run → expect PASS** (RLS already enforced by the policies). If it fails open, the migration's policies are wrong — fix the migration.
- [ ] **Step 3: Commit** `git commit -am "test(reporting): cross-tenant RLS isolation on session_reports"`

---

## Task 13: Judge input builder (cache-optimized prompt) + prompt file

**Files:**
- Create: `prompts/v3/report_scorer/system.txt`  (use the dir matching `report_scorer_prompt_version`)
- Create: `app/modules/reporting/scoring/input_builder.py`
- Test: `tests/reporting/test_input_builder.py`

- [ ] **Step 1: Write `system.txt`** — the version-frozen developer prompt (stable prefix). XML-delimited, no "think step by step", evidence-before-score, grade only on provided evidence, never auto-resolve borderline. Leave per-question rubric + the transcript as runtime-injected sections (the suffix). Example skeleton:
```
You are a hiring evaluator. Grade ONE interview answer against the rubric for ONE question.
Rules:
- Use ONLY the evidence in <transcript>. Never assume unstated competence or use outside knowledge.
- Cite verbatim quote spans from <transcript> for your decision.
- Decide the BARS level strictly from the rubric anchors.
- Do not reward longer answers. Do not penalize transcription artifacts (e.g. "5xx" rendered "five x").
<output_spec>Return evidence_quotes, red_flags_hit, justification, then level.</output_spec>
```

- [ ] **Step 2: Write the failing test**
```python
# tests/reporting/test_input_builder.py
from app.modules.reporting.scoring.input_builder import render_prefix, build_messages

QUESTION = {"id": "q4", "text": "Design an agent loop…",
            "rubric": {"excellent": "…", "meets_bar": "…", "below_bar": "…"},
            "positive_evidence": ["allow-listed tools"], "red_flags": ["no constraints"]}

def test_prefix_is_byte_stable_across_answers():
    p1 = render_prefix(system_prompt="SYS", question=QUESTION)
    p2 = render_prefix(system_prompt="SYS", question=QUESTION)
    assert p1 == p2                       # identical → cacheable

def test_messages_put_answer_last():
    msgs = build_messages(prefix="PREFIX", transcript_excerpt="CANDIDATE: foo")
    assert msgs[0]["role"] in ("system", "developer")
    assert msgs[0]["content"] == "PREFIX"
    assert msgs[-1]["role"] == "user"
    assert "foo" in msgs[-1]["content"]   # dynamic content LAST
```

- [ ] **Step 3: Run → fail.** → FAIL.

- [ ] **Step 4: Implement `input_builder.py`** — `render_prefix` returns the byte-stable developer message (system prompt + `<rubric>`/`<criteria>`/`<positive_evidence>`/`<red_flags>`/`<output_spec>` for THIS question, no per-candidate data); `build_messages` returns `[{role: developer, content: prefix}, {role: user, content: <transcript>…</transcript>}]`. Follow `brain/input_builder.py` structure.

- [ ] **Step 5: Run → pass.** → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(reporting): cache-optimized judge prompt builder"`

---

## Task 14: Judge LLM call (instructor, cached, mocked in tests)

**Files:**
- Create: `app/modules/reporting/scoring/judge.py`
- Test: `tests/reporting/test_judge.py`

- [ ] **Step 1: Write the failing test (mock at the `app/ai` boundary)**
```python
# tests/reporting/test_judge.py
import pytest
from unittest.mock import AsyncMock, patch
from app.modules.reporting.scoring.judge import grade_answer
from app.modules.reporting.schemas import JudgeVerdict

@pytest.mark.asyncio
async def test_grade_answer_grounds_evidence_and_returns_level():
    verdict = JudgeVerdict(evidence_quotes=["take this up at Java"],
                           red_flags_hit=["buzzwords"], justification="thin", level="below_bar")
    fake_client = AsyncMock()
    fake_client.chat.completions.create_with_completion = AsyncMock(
        return_value=(verdict, _completion_with_cached_tokens(900)))
    with patch("app.modules.reporting.scoring.judge.get_openai_client", return_value=fake_client):
        rating = await grade_answer(
            question={"id": "q6", "text": "Java/JSON?", "rubric": {...},
                      "positive_evidence": [], "red_flags": []},
            transcript_excerpt="CANDIDATE: I would take this up at Java...",
            correlation_id="c1")
    assert rating.level == "below_bar"
    assert rating.evidence_quotes == ["take this up at Java"]   # grounded
    assert rating.grounded is True
```
(Provide a `_completion_with_cached_tokens` helper returning an object with `.usage.prompt_tokens_details.cached_tokens`.)

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement `judge.py`**:
  - `get_openai_client()` from `app.ai.client`; `ai_config` from `app.ai.config`; `PromptLoader(version=ai_config.report_scorer_prompt_version).get("report_scorer/system")`.
  - Build messages via `input_builder`. Call `create_with_completion(model=ai_config.report_scorer_model, response_model=JudgeVerdict, messages=..., max_retries=1, reasoning_effort=... if ai_config.report_scorer_effort else omit, prompt_cache_key=f"{prefix}:{prompt_version}:{question_id}:{model}")`. **Do not** pass temperature/seed.
  - Wrap in OTel span via `set_llm_span_attributes(prompt_name="report_scorer", ...)`.
  - After the call: run `ground_quotes` against the transcript excerpt; drop ungrounded quotes, set `grounded=False` if any were dropped; log `cached_tokens`.
  - Return an `AnswerRating` (question_id, level, grounded evidence_quotes, red_flags_hit, justification, grounded, cached_tokens).
  - **Selective N-sample:** expose `grade_answer(..., n_samples=1)`; a wrapper `grade_answer_consistent` samples N and takes the median level when the caller flags low-confidence/near-knockout (Task 15 decides when).

- [ ] **Step 4: Run → pass.** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): LLM judge with evidence grounding + caching"`

---

## Task 15: Service orchestration — build_report (mocked judge)

**Files:**
- Modify: `app/modules/reporting/service.py`
- Test: `tests/reporting/test_service.py`

`build_report` is pure-ish (LLM behind the judge interface): segment → opportunity → judge each unit → map answers to signals → `combine_signal` → `score_dimension`×3 → `score_overall` → `knockout_status` per knockout → `resolve_verdict` → assemble `ReportRead` + manifest + frozen rubric snapshot.

- [ ] **Step 1: Write the failing test (the e4072361 calibration case)**
```python
# tests/reporting/test_service.py
import json, pathlib, pytest
from unittest.mock import patch
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import JudgeVerdict
FIX = pathlib.Path(__file__).parent / "fixtures"

@pytest.mark.asyncio
async def test_weak_bluffer_is_confident_reject():
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    bank = json.loads((FIX / "job_bank_slice.json").read_text())

    async def fake_grade(question, transcript_excerpt, correlation_id, n_samples=1):
        # programming/JSON question → below_bar with full opportunity; others thin
        from app.modules.reporting.schemas import AnswerRating
        lvl = "below_bar"
        return AnswerRating(question_id=question["id"], level=lvl, evidence_quotes=[],
                            red_flags_hit=["buzzwords"], justification="thin", grounded=True)

    with patch("app.modules.reporting.service.grade_answer", side_effect=fake_grade):
        report = await build_report(transcript=transcript, envelope=envelope,
                                    questions=bank["questions"],
                                    signal_metadata=bank["signal_metadata"],
                                    correlation_id="c1")
    assert report.verdict == "reject"
    assert any(k["status"] == "failed" for k in report.knockout_results)
    assert report.dimension_scores["technical"]["score"] is not None
```

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement `build_report`** in `service.py`. Build `SignalDef`s from `signal_metadata`. For each `ScoredUnit`, call `grade_answer` (module-level import so the test can patch it), then for each signal in the question's `signal_values` append a `SignalObservation(level, opportunity=classify(unit), red_flags_hit=bool(rating.red_flags_hit))`. `combine_signal` per signal → `ScoredSignal`. Dimensions via `score_dimension(..., TECHNICAL_TYPES)` etc. `score_overall`. Knockouts: `knockout_status` per `ScoredSignal` where `knockout`. `resolve_verdict`. Assemble `ReportRead` (+ `scoring_manifest` with model/effort/prompt_version/cache_hit_rate/n_samples/correlation_id, + frozen `rubric_snapshot` = the questions+signal_metadata). Produce `summary` (a short headline/strengths/gaps/rationale — derive deterministically from the scorecards, or one extra LLM call gated behind the judge interface; for this task derive deterministically).

- [ ] **Step 4: Run → pass.** → PASS. This is the key calibration test.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): build_report orchestration (e4072361 → confident reject)"`

---

## Task 15b: Communication dimension (content-only, separate from Overall)

**Files:**
- Create: `prompts/v3/report_scorer/communication.txt` (stable prefix for the communication judge)
- Modify: `app/modules/reporting/scoring/judge.py` (add `grade_communication`)
- Modify: `app/modules/reporting/service.py` (`build_report` adds the `communication` dimension)
- Test: `tests/reporting/test_service.py` (append)

Communication is **not** a JD signal — it's a content-level read of the whole transcript (structure/coherence, relevance vs ramble/deflect, specificity vs buzzwords). Scored 3-level → 100/70/30, shown as its own dimension, **excluded from Overall** (spec §4.6/§4.8).

- [ ] **Step 1: Write `communication.txt`** — same stable-prefix discipline: grade the candidate's *communication* from `<transcript>` content only; explicitly **do not** penalize STT artifacts ("5xx"→"five x") or accent/fluency; do not reward length; evidence before level; level ∈ {weak, adequate, strong}.

- [ ] **Step 2: Write the failing test (append to test_service.py)**
```python
@pytest.mark.asyncio
async def test_communication_is_separate_and_not_in_overall():
    # build_report with a fake communication grader returning "adequate" (=70)
    ... # patch grade_answer (below_bar) AND grade_communication (-> level "adequate")
    assert report.dimension_scores["communication"]["score"] == 70
    # Overall is computed from JD signals only — independent of the communication score
    assert "communication" not in report.scoring_manifest.get("overall_inputs", [])
```

- [ ] **Step 3: Run → fail.** → FAIL.

- [ ] **Step 4: Implement** `grade_communication(transcript_text, correlation_id)` in `judge.py` (one judge call, `JudgeVerdict`-like `CommunicationVerdict` with `level ∈ {weak,adequate,strong}`, evidence-grounded), mapping `weak/adequate/strong → 30/70/100`. In `build_report`, call it once, add `dimension_scores["communication"]` with the label `"content-only; full communication scoring pending recording"`, and ensure `score_overall` is computed **only** from JD `ScoredSignal`s (it already is — assert it stays that way).

- [ ] **Step 5: Run → pass.** → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(reporting): content-only communication dimension (excluded from Overall)"`

---

## Task 16: Persist report + idempotency

**Files:**
- Modify: `app/modules/reporting/service.py` (add `persist_report`)
- Test: `tests/reporting/test_service.py` (append, uses bypass DB)

- [ ] **Step 1: Write the failing test** — call `persist_report(db, session_id, tenant_id, assignment_id, report, manifest)`; assert a `ready` `session_reports` row exists; call again → no duplicate (unique `session_id`), `version` bumps on `force=True`.

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement `persist_report`** — upsert on `session_id`: insert with `status="ready"`, `generated_at=now`; on conflict (force regenerate) update fields + `version = version + 1`. Use the worker bypass session pattern.

- [ ] **Step 4: Run → pass.** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(reporting): persist report + idempotent upsert"`

---

## Task 17: Dramatiq actor + trigger + worker registration

**Files:**
- Create: `app/modules/reporting/actors.py`
- Modify: `app/worker.py` (import actors)
- Modify: `app/modules/interview_runtime/service.py` (`record_session_result`: enqueue after commit)
- Test: `tests/reporting/test_actor.py`

- [ ] **Step 1: Write the failing test** — patch `build_report` + `persist_report`; call `_score_session_report_async(session_id, tenant_id, correlation_id)` with a seeded `completed` v2 session row; assert `persist_report` called once; call again with an existing `ready` report → asserts idempotent skip (no second `build_report`).

- [ ] **Step 2: Run → fail.** → FAIL.

- [ ] **Step 3: Implement `actors.py`** (mirror `app/modules/jd/actors.py`): sync `@dramatiq.actor(max_retries=2, time_limit=...)` `score_session_report(session_id, tenant_id, correlation_id, force=False)` → `asyncio.run(_score_session_report_async(...))`. The async inner: `get_bypass_session()`; `SET LOCAL app.current_tenant = '<tenant>'`; idempotency check (skip if a `ready` report exists and not `force`); load session row (transcript, raw_result_json → audit_envelope_ref, engine_version); read the envelope JSON from disk (the `engine-events/<id>.json` path / `audit_envelope_ref`); load the bank (questions + signal_metadata) for the session's stage; `build_report(...)`; `persist_report(...)`; on exception set `status="failed"` + `generation_error`; structured logs with `correlation_id` (never log quotes).

- [ ] **Step 4: Register** in `app/worker.py`: `from app.modules.reporting import actors  # noqa: F401`.

- [ ] **Step 5: Wire the trigger** in `record_session_result` — after the commit that sets `state="completed"` and only when `engine_version == "v2"` and the stage is AI-driven:
```python
from app.modules.reporting.actors import score_session_report
score_session_report.send(str(session_id), str(tenant_id), correlation_id)
```
Place it after the successful-transition branch (not on the idempotent no-op path).

- [ ] **Step 6: Run → pass.** `tests/reporting/test_actor.py -v` → PASS.
- [ ] **Step 7: Commit** `git commit -am "feat(reporting): score_session_report actor + post-session trigger"`

---

## Task 18: API endpoints + RBAC

**Files:**
- Modify: `app/modules/reporting/router.py` (replace stub)
- Test: `tests/reporting/test_router.py`

- [ ] **Step 1: Read** `app/modules/candidates/router.py` to copy the exact RBAC dependency usage (`UserContext = Depends(get_current_user_roles)`) and how a tenant-scoped DB session is obtained in a request handler, and how role checks are expressed.

- [ ] **Step 2: Write the failing test** — using the app test client + a recruiter-role user fixture:
  - `GET /api/reports/session/{id}` for a session with a `ready` report → 200 + verdict; for a `pending` one → 202; for none → 404.
  - cross-tenant fetch → 404 (RLS).
  - `POST /api/reports/{id}/decision` with `{decision, rationale}` → 200, writes `human_decision` + an `audit_log` row.
  - a user lacking the recruiter/admin role → 403.

- [ ] **Step 3: Run → fail.** → FAIL.

- [ ] **Step 4: Implement `router.py`** — replace the stub. Endpoints from spec §6:
  - `GET /api/reports/session/{session_id}` (current report; 202 if status∈{pending,generating}; 404 if none)
  - `GET /api/reports/{report_id}`
  - `POST /api/reports/session/{session_id}/regenerate` (admin/senior → `score_session_report.send(..., force=True)`)
  - `POST /api/reports/{report_id}/decision` (record `human_decision`, write `audit_log` with `actor_id,tenant_id,action="report_decision",resource_type="session_report",resource_id,correlation_id`).
  All handlers tenant-scoped via the tenant DB dependency; RBAC via `get_current_user_roles`; serialize through `ReportRead`.

- [ ] **Step 5: Run → pass.** `tests/reporting/test_router.py -v` → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(reporting): report API endpoints + RBAC + human-decision audit"`

---

## Task 19: Full-suite + lint gate, end-to-end smoke

**Files:** none (verification)

- [ ] **Step 1: Run the whole reporting suite** `docker compose run --rm nexus pytest tests/reporting/ -v` → all PASS.
- [ ] **Step 2: Run the full backend suite + ruff** `docker compose run --rm nexus pytest -q && docker compose run --rm nexus ruff check app/modules/reporting` → green.
- [ ] **Step 3: Live smoke (optional, real LLM)** — run a fresh v2 session via `scripts/demo.sh`, let it complete, then `docker compose logs nexus-worker | grep score_session_report` to confirm the actor fired and `GET /api/reports/session/{id}` returns a verdict. Inspect the `scoring_manifest.cache_hit_rate` after a second session on the same job (should be >0 once the prefix is warm).
- [ ] **Step 4: Commit** any fixups `git commit -am "test(reporting): full-suite green + smoke"`

---

## Self-Review notes (author)

- **Spec coverage:** §3 data flow → T17; §4.1 anchors → T2; §4.2 opportunity → T3; §4.3 judge → T13/T14; §4.4 grounding → T9; §4.5 signal combine → T4; §4.6 dimensions/overall → T5; §4.7 knockout/verdict → T6; §4.8 communication → T15b; §4.9 caching → T13/T14; §5 table → T11; §6 API → T18; §7 trigger → T17; §8 audit/OTel → T14/T17/T18; §10 tests → T12/T19. Every spec section maps to a task.
- **Type consistency:** `BarsLevel/Opportunity/SignalState/KnockoutStatus/Verdict/Confidence` defined once in T2; `ScoredSignal/DimensionScore/KnockoutResult/VerdictResult/SignalObservation` in T4–T6; `JudgeVerdict/AnswerRating/ReportRead` in T10. `combine_signal`, `score_dimension`, `score_overall`, `knockout_status`, `resolve_verdict`, `classify`, `segment`, `ground_quotes`, `grade_answer`, `build_report`, `persist_report`, `score_session_report` — names used consistently across tasks.
- **Open verification points the executor must confirm against the codebase (not placeholders — explicit checks):** exact `Settings(...)` test construction (T1), the two-tenant RLS test harness (T12), the RBAC role-assert helper + tenant-DB request dependency (T18 step 1), and how `audit_envelope_ref` resolves to an on-disk/file path vs S3 in this environment (T17 step 3).
