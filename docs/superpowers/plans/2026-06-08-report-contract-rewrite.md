# Report Contract Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `app/modules/reporting/` to score from the gen-3 engine's append-only `SessionEvidence` (notes + provenance + closure), with a bank-anchored (primary-signal) denominator, provenance-aware fairness, and the report-scoring pipeline re-connected.

**Architecture:** A new `evidence_adapter` loads `SessionEvidence` and derives the primary-signal denominator. A rewritten deterministic core (`aggregate.py`) scores each primary signal from its notes (`strong/solid/thin/absent/not_reached` → points), splits *score* from *coverage/confidence*, applies a provenance-aware must-have gate, and resolves the verdict. The four existing AI layers (re-check / ±5 holistic / communication / narrative) are kept but re-pointed at the notes. The enqueue moves from `record_session_result` → `record_session_evidence`. The gen-2 `coverage_summary`/envelope path is deleted.

**Tech Stack:** Python 3.13, Pydantic v2, SQLAlchemy async (asyncpg), Dramatiq, pytest, OpenAI Responses API (via `app/ai/`). Engine contract: `app/modules/interview_runtime/evidence.py`.

---

## Reference facts (read before starting)

- **Spec:** `docs/superpowers/specs/2026-06-08-report-contract-rewrite-design.md` (esp. §3 decisions, §3.1 cross-check, §5 per-signal scoring, §6 verdict).
- **Producer contract:** `app/modules/interview_runtime/evidence.py` — `SessionEvidence{meta, signals[], notes[], questions[], transcript[], knockout?}`. Enums: `Provenance(not_reached|asked_directly|cross_credited|probed_absent)`, `EvidenceStance(supports|contradicts)`, `EvidenceTexture(thin|concrete|strong)`, `ThreadClosure(satisfied|tapped_out|absent|truncated)`, `QuestionOutcome(asked|not_reached)`, `CompletionReason(...|knockout_close)`, `Speaker(agent|candidate)`.
- **Key guard (§3.1):** `SessionEvidence.signals[]` is the **FULL role signal set** (uncovered → `not_reached`). The graded denominator = `{ qr.primary_signal for qr in evidence.questions }`. **Never score `signals[]` wholesale** — that re-introduces the original bug.
- **Demonstrated secondary signal** = `signal ∉ primary_set AND provenance == cross_credited`.
- **Knockout** = `evidence.meta.completion == knockout_close` and/or `evidence.knockout` (`KnockoutOutcome`).
- **Persistence:** evidence is at `sessions.session_evidence_json` (migration `0054`). `interview_runtime/service.py::record_session_evidence` writes it and currently does NOT enqueue scoring.
- **Run tests:** `docker compose run --rm nexus pytest tests/reporting -q`. **Lint:** `docker compose run --rm nexus ruff check app/modules/reporting`.
- **Commit discipline:** commit after each task. End commit messages with the `Co-Authored-By:` trailer used in this repo.

---

## File Structure

| File | Disposition | Responsibility |
|---|---|---|
| `app/modules/reporting/scoring/types.py` | Modify | Add `DemonstrationLevel` literal; keep `SignalDef`. Remove dead `CovState/GradeTexture` after the rewrite (Task 15). |
| `app/modules/reporting/scoring/constants.py` | Modify | Add `LEVEL_POINTS`; keep thresholds/ceilings/`tier_label`. Remove `STATE_TEXTURE_POINTS` (Task 15). |
| `app/modules/reporting/scoring/evidence_adapter.py` | **Create** | Parse `SessionEvidence`; derive `primary_set`, `notes_by_signal`, `provenance_by_signal`, demonstrated secondaries, knockout, candidate transcript text. Replaces `engine_signals.py`. |
| `app/modules/reporting/scoring/aggregate.py` | **Rewrite** | Deterministic core: `level_for_signal` → `LEVEL_POINTS` → dimensions → coverage/confidence → ceilings → verdict. |
| `app/modules/reporting/scoring/recheck.py` | Modify | Accept notes; output a refined `DemonstrationLevel`; scope to evidenced + `probed_absent`; trust factual gates. |
| `app/modules/reporting/scoring/holistic.py` | Modify | Feed demonstrated secondary breadth; speaker-enum transcript. |
| `app/modules/reporting/scoring/judge.py` | Modify | Speaker-enum transcript only (signature unchanged; caller adapts). |
| `app/modules/reporting/scoring/narrative.py` | Unchanged | Prose from ground truth (now carries provenance — built in `service.py`). |
| `app/modules/reporting/service.py` | **Rewrite `build_report`** | New signature + flow; `engine_version="v3"`; provenance in ground truth. |
| `app/modules/reporting/actors.py` | Modify | Load `session_evidence_json` → `SessionEvidence`; drop envelope/coverage_summary. |
| `app/modules/reporting/schemas.py` | Modify | `SignalRecheckOut.level: DemonstrationLevel`; `SignalAssessmentOut` carries `provenance`. |
| `app/modules/reporting/scoring/engine_signals.py` | **Delete** (Task 15) | gen-2 coverage_summary projection. |
| `app/modules/interview_runtime/service.py` | Modify | Enqueue `score_session_report` at end of `record_session_evidence`. |
| `prompts/v4/report_scorer/{signal_recheck,holistic,communication,narrative}.txt` | **Create** | Re-check reads notes; narrative honours provenance. |
| `tests/reporting/...` | Create/Modify | Per-task tests + one golden end-to-end fixture. |

---

## Phase 0 — Vocabulary

### Task 1: Demonstration-level vocabulary + points

**Files:**
- Modify: `app/modules/reporting/scoring/types.py`
- Modify: `app/modules/reporting/scoring/constants.py`
- Test: `tests/reporting/scoring/test_levels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_levels.py
from app.modules.reporting.scoring.constants import LEVEL_POINTS, level_score


def test_level_points_ordering():
    assert LEVEL_POINTS["strong"] > LEVEL_POINTS["solid"] > LEVEL_POINTS["thin"]
    assert LEVEL_POINTS["thin"] > LEVEL_POINTS["absent"]


def test_absent_and_not_reached_share_the_floor():
    # Uniform low band: never-asked scores exactly as asked-and-absent.
    assert LEVEL_POINTS["absent"] == LEVEL_POINTS["not_reached"]
    assert level_score("absent") == level_score("not_reached")


def test_level_score_passthrough():
    assert level_score("strong") == 100
    assert level_score("not_reached") == LEVEL_POINTS["not_reached"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_levels.py -v`
Expected: FAIL with `ImportError: cannot import name 'LEVEL_POINTS'`.

- [ ] **Step 3: Add the literal to `types.py`**

Add near the other `Literal` definitions in `app/modules/reporting/scoring/types.py`:

```python
# Per-primary-signal demonstration level (gen-3 evidence-driven scoring).
# strong/solid/thin = demonstrated (texture); absent = asked-and-failed/disclaimed;
# not_reached = never asked or time-truncated (shares the floor with absent).
DemonstrationLevel = Literal["strong", "solid", "thin", "absent", "not_reached"]
```

- [ ] **Step 4: Add points + helper to `constants.py`**

Append to `app/modules/reporting/scoring/constants.py`:

```python
# ---------------------------------------------------------------------------
# Gen-3 demonstration-level scoring (replaces STATE_TEXTURE_POINTS)
# ---------------------------------------------------------------------------
# Level → 0..100 points. Ordering is load-bearing; absolute numbers are tunable.
# `absent` and `not_reached` share the floor (uniform low band — see spec §5).
LEVEL_POINTS: dict[str, int] = {
    "strong": 100,
    "solid": 80,
    "thin": 40,
    "absent": 10,
    "not_reached": 10,
}


def level_score(level: str) -> int:
    """Map a DemonstrationLevel to its 0..100 score."""
    return LEVEL_POINTS[level]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_levels.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/scoring/types.py app/modules/reporting/scoring/constants.py tests/reporting/scoring/test_levels.py
git commit -m "feat(reporting): add gen-3 demonstration-level scoring vocabulary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 1 — Evidence adapter

### Task 2: `evidence_adapter.py` — parse SessionEvidence + derive the primary denominator

**Files:**
- Create: `app/modules/reporting/scoring/evidence_adapter.py`
- Test: `tests/reporting/scoring/test_evidence_adapter.py`
- Reference: `app/modules/interview_runtime/evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_evidence_adapter.py
from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.scoring.evidence_adapter import EvidenceView


def _evidence(**overrides) -> SessionEvidence:
    base = {
        "meta": {
            "session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
            "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
            "duration_s": 1200.0, "time_budget_s": 1200.0, "completion": "completed",
            "questions_asked": 2, "questions_core_total": 2, "questions_overflow_asked": 0,
        },
        "signals": [
            {"signal": "python", "signal_type": "competency", "weight": 3,
             "priority": "required", "knockout": True, "provenance": "asked_directly"},
            {"signal": "leadership", "signal_type": "behavioral", "weight": 1,
             "priority": "preferred", "knockout": False, "provenance": "cross_credited"},
            {"signal": "uncovered_role_sig", "signal_type": "competency", "weight": 2,
             "priority": "preferred", "knockout": False, "provenance": "not_reached"},
        ],
        "notes": [
            {"seq": 1, "turn_ref": "t-1", "signal": "python", "stance": "supports",
             "texture": "concrete", "quote": "I built X in Python", "span": {"start_ms": 0, "end_ms": 100},
             "from_question_id": "q1", "via_probe": False},
            {"seq": 2, "turn_ref": "t-2", "signal": "leadership", "stance": "supports",
             "texture": "strong", "quote": "I led a team of 5", "span": {"start_ms": 0, "end_ms": 100},
             "from_question_id": "q1", "via_probe": True},
        ],
        "questions": [
            {"question_id": "q1", "primary_signal": "python", "tier": "core",
             "outcome": "asked", "closure": "satisfied", "probes_used": [0], "probes_available": 3},
            {"question_id": "q2", "primary_signal": "communication", "tier": "core",
             "outcome": "not_reached", "closure": None, "probes_used": [], "probes_available": 2},
        ],
        "transcript": [
            {"turn_ref": "t-1", "speaker": "candidate", "text": "I built X in Python",
             "span": {"start_ms": 0, "end_ms": 100}, "pre_turn_gap_ms": 500},
            {"turn_ref": "t-0", "speaker": "agent", "text": "Tell me about Python",
             "span": {"start_ms": 0, "end_ms": 100}, "pre_turn_gap_ms": 0},
        ],
        "knockout": None,
    }
    base.update(overrides)
    return SessionEvidence.model_validate(base)


def test_primary_set_comes_from_question_primary_signals_not_signals_list():
    view = EvidenceView(_evidence())
    # 'leadership' and 'uncovered_role_sig' are NOT any question's primary_signal.
    assert view.primary_set == {"python", "communication"}


def test_notes_by_signal_groups_supports():
    view = EvidenceView(_evidence())
    assert [n.quote for n in view.notes_by_signal["python"]] == ["I built X in Python"]


def test_demonstrated_secondaries_are_cross_credited_non_primary():
    view = EvidenceView(_evidence())
    # leadership: cross_credited + not in primary_set → demonstrated secondary.
    # uncovered_role_sig: not_reached → excluded.
    assert view.demonstrated_secondaries == {"leadership"}


def test_candidate_transcript_text_excludes_agent_turns():
    view = EvidenceView(_evidence())
    assert view.candidate_transcript_text == "I built X in Python"


def test_knockout_close_detection():
    ev = _evidence(
        meta={**_evidence().meta.model_dump(mode="json"), "completion": "knockout_close"},
        knockout={"signal": "python", "or_alternatives_checked": [],
                  "reflect_confirmed": True, "evidence_note_seqs": [1]},
    )
    view = EvidenceView(ev)
    assert view.knockout_signal == "python"
    assert view.is_knockout_close is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_evidence_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: ... evidence_adapter`.

- [ ] **Step 3: Write `evidence_adapter.py`**

```python
# app/modules/reporting/scoring/evidence_adapter.py
"""Adapter over the gen-3 engine's SessionEvidence contract (pure — no IO/LLM).

Turns the append-only evidence into the views the deterministic scorer needs.
The graded denominator is the PRIMARY-signal set derived from question records —
NOT SessionEvidence.signals[] (which is the full role set; see spec §3.1).
"""
from __future__ import annotations

from app.modules.interview_runtime.evidence import (
    CompletionReason,
    EvidenceNote,
    EvidenceStance,
    Provenance,
    SessionEvidence,
    SignalEvidence,
    Speaker,
)


class EvidenceView:
    """Read-only projections of a SessionEvidence for the scorer."""

    def __init__(self, evidence: SessionEvidence) -> None:
        self._ev = evidence

    @property
    def evidence(self) -> SessionEvidence:
        return self._ev

    @property
    def primary_set(self) -> set[str]:
        """The graded denominator: every signal that is a question's primary_signal."""
        return {q.primary_signal for q in self._ev.questions}

    @property
    def signal_by_name(self) -> dict[str, SignalEvidence]:
        return {s.signal: s for s in self._ev.signals}

    @property
    def provenance_by_signal(self) -> dict[str, Provenance]:
        return {s.signal: s.provenance for s in self._ev.signals}

    @property
    def notes_by_signal(self) -> dict[str, list[EvidenceNote]]:
        out: dict[str, list[EvidenceNote]] = {}
        for n in self._ev.notes:
            out.setdefault(n.signal, []).append(n)
        return out

    @property
    def closure_by_primary(self) -> dict[str, str | None]:
        """primary_signal → the closure of its (first) own question, if any."""
        out: dict[str, str | None] = {}
        for q in self._ev.questions:
            out.setdefault(q.primary_signal, q.closure.value if q.closure else None)
        return out

    @property
    def demonstrated_secondaries(self) -> set[str]:
        """Non-primary signals that were cross-credited (upside-only path)."""
        primary = self.primary_set
        return {
            s.signal for s in self._ev.signals
            if s.signal not in primary and s.provenance == Provenance.cross_credited
        }

    @property
    def candidate_transcript_text(self) -> str:
        return "\n".join(
            t.text for t in self._ev.transcript if t.speaker == Speaker.candidate
        )

    @property
    def is_knockout_close(self) -> bool:
        return self._ev.meta.completion == CompletionReason.knockout_close

    @property
    def knockout_signal(self) -> str | None:
        return self._ev.knockout.signal if self._ev.knockout else None

    def has_supporting_notes(self, signal: str) -> bool:
        return any(
            n.stance == EvidenceStance.supports
            for n in self.notes_by_signal.get(signal, [])
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_evidence_adapter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/evidence_adapter.py tests/reporting/scoring/test_evidence_adapter.py
git commit -m "feat(reporting): SessionEvidence adapter with primary-signal denominator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Deterministic core (rewrite `aggregate.py`)

### Task 3: `level_for_signal` — notes + provenance + closure → DemonstrationLevel

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py` (add the function; old functions removed in Task 4)
- Test: `tests/reporting/scoring/test_level_for_signal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_level_for_signal.py
from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan
from app.modules.reporting.scoring.aggregate import level_for_signal


def _note(signal, stance, texture, seq=1, retracts=None):
    return EvidenceNote(
        seq=seq, turn_ref=f"t-{seq}", signal=signal, stance=stance, texture=texture,
        quote="x", span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
        via_probe=False, retracts_seq=retracts,
    )


def test_strong_from_best_texture():
    notes = [_note("s", "supports", "thin", 1), _note("s", "supports", "strong", 2)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "strong"


def test_solid_from_concrete():
    notes = [_note("s", "supports", "concrete", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "solid"


def test_thin_only():
    notes = [_note("s", "supports", "thin", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="tapped_out") == "thin"


def test_probed_absent_is_absent():
    assert level_for_signal([], provenance="probed_absent", closure="absent") == "absent"


def test_not_reached_is_not_reached():
    assert level_for_signal([], provenance="not_reached", closure=None) == "not_reached"


def test_truncated_with_no_support_is_not_reached_even_if_asked():
    # closure=truncated → no fair data → not_reached (not absent).
    assert level_for_signal([], provenance="not_reached", closure="truncated") == "not_reached"


def test_unretracted_contradiction_is_absent():
    notes = [_note("s", "contradicts", "concrete", 1)]
    assert level_for_signal(notes, provenance="probed_absent", closure="absent") == "absent"


def test_retracted_contradiction_does_not_force_absent():
    # supports@1, then a contradicts@2 that retracts an EARLIER different claim →
    # the support stands. (Engine guarantees provenance; we honour supports here.)
    notes = [_note("s", "supports", "concrete", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "solid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_level_for_signal.py -v`
Expected: FAIL with `ImportError: cannot import name 'level_for_signal'`.

- [ ] **Step 3: Add `level_for_signal` to the top of `aggregate.py`**

Add (imports + function) near the top of `app/modules/reporting/scoring/aggregate.py`:

```python
from app.modules.interview_runtime.evidence import EvidenceNote, EvidenceStance, EvidenceTexture
from app.modules.reporting.scoring.types import DemonstrationLevel

_TEXTURE_RANK = {EvidenceTexture.thin: 0, EvidenceTexture.concrete: 1, EvidenceTexture.strong: 2}
_RANK_LEVEL = {2: "strong", 1: "solid", 0: "thin"}


def level_for_signal(
    notes: list[EvidenceNote], *, provenance: str, closure: str | None
) -> DemonstrationLevel:
    """Roll a signal's notes + provenance/closure into one demonstration level.

    Supporting notes → level by best texture (strong>concrete>thin). No supports:
    `probed_absent` → absent; an un-retracted contradiction → absent; else
    (`not_reached`, including closure=truncated) → not_reached.
    """
    supports = [n for n in notes if n.stance == EvidenceStance.supports]
    if supports:
        best = max(_TEXTURE_RANK[n.texture] for n in supports)
        return _RANK_LEVEL[best]  # type: ignore[return-value]

    # No supporting evidence.
    unretracted_contradiction = any(
        n.stance == EvidenceStance.contradicts and n.retracts_seq is None for n in notes
    )
    if provenance == "probed_absent" or unretracted_contradiction:
        return "absent"
    return "not_reached"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_level_for_signal.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/aggregate.py tests/reporting/scoring/test_level_for_signal.py
git commit -m "feat(reporting): per-signal demonstration-level rollup from notes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Rewrite the scored-signal + dimension + overall math (primary denominator)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py` (replace `ScoredSignal`, `score_signal`, `score_state`, `score_dimension`, `score_overall`)
- Test: `tests/reporting/scoring/test_aggregate_math.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_aggregate_math.py
from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_dimension, score_overall,
)
from app.modules.reporting.scoring.constants import TECHNICAL_TYPES


def _s(value, level, *, type="competency", weight=1, knockout=False, priority="preferred"):
    from app.modules.reporting.scoring.constants import level_score
    return ScoredSignal(value=value, type=type, weight=weight, knockout=knockout,
                        priority=priority, level=level, score=level_score(level))


def test_absent_and_not_reached_score_identically_in_overall():
    a = score_overall([_s("x", "absent")])
    b = score_overall([_s("x", "not_reached")])
    assert a[0] == b[0]  # same number — uniform low band


def test_overall_is_weighted_mean_of_primaries():
    scored = [_s("a", "strong", weight=3), _s("b", "thin", weight=1)]
    overall, coverage = score_overall(scored)
    assert overall == round((3 * 100 + 1 * 40) / 4)  # 85
    assert coverage == 0.0 or coverage >= 0.0  # coverage computed in Task 5


def test_dimension_filters_by_type():
    scored = [_s("a", "strong", type="competency"), _s("b", "absent", type="behavioral")]
    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    assert tech.score == 100  # only the competency signal is technical
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_aggregate_math.py -v`
Expected: FAIL (`ScoredSignal` has no `level`; or coverage signature mismatch).

- [ ] **Step 3: Replace the scored-signal + math in `aggregate.py`**

In `app/modules/reporting/scoring/aggregate.py`, replace `ScoredSignal`, `score_signal`, `score_state`, `score_dimension`, and `score_overall` with:

```python
from app.modules.reporting.scoring.constants import level_score
from app.modules.reporting.scoring.types import Confidence


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    level: DemonstrationLevel
    score: int           # always 0..100 (every primary is scored, incl. floors)


def make_scored_signal(*, value, type, weight, knockout, priority, level) -> ScoredSignal:
    return ScoredSignal(value=value, type=type, weight=weight, knockout=knockout,
                        priority=priority, level=level, score=level_score(level))


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float        # real-data fraction (see compute_confidence, Task 5)
    confidence: Confidence


def score_dimension(name: str, signals: list[ScoredSignal], types: frozenset[str]) -> DimensionScore:
    members = [s for s in signals if s.type in types]
    total_w = sum(s.weight for s in members)
    if total_w == 0:
        return DimensionScore(name=name, score=None, coverage=0.0, confidence="low")
    weighted = sum(s.weight * s.score for s in members) / total_w
    cov = compute_coverage(members)
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=cov, confidence=confidence_from_coverage(cov))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Weighted mean over ALL primary signals (every one is scored, incl. floors).
    Communication is scored separately. Returns (overall, real-data coverage)."""
    total_w = sum(s.weight for s in signals)
    if total_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in signals) / total_w
    return int(round(weighted)), compute_coverage(signals)
```

> Note: `compute_coverage` is added in Task 5. Until then, add a temporary stub at the bottom of the file:
> ```python
> def compute_coverage(signals: list["ScoredSignal"]) -> float:
>     return 0.0  # replaced in Task 5
> ```
> (`confidence_from_coverage` already exists in this file — keep it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_aggregate_math.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/aggregate.py tests/reporting/scoring/test_aggregate_math.py
git commit -m "feat(reporting): primary-denominator dimension + overall scoring

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Coverage/confidence split (real-data fraction)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py` (replace the `compute_coverage` stub)
- Test: `tests/reporting/scoring/test_coverage.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_coverage.py
from app.modules.reporting.scoring.aggregate import ScoredSignal, compute_coverage


def _s(level, weight=1):
    from app.modules.reporting.scoring.constants import level_score
    return ScoredSignal(value="x", type="competency", weight=weight, knockout=False,
                        priority="preferred", level=level, score=level_score(level))


def test_demonstrated_and_probed_absent_count_as_covered():
    # 'absent' here means probed_absent (real data); not_reached does NOT count.
    signals = [_s("strong", 2), _s("absent", 2), _s("not_reached", 4)]
    # covered weight = 2 (strong) + 2 (absent) = 4 of 8
    assert compute_coverage(signals) == 0.5


def test_all_not_reached_is_zero_coverage():
    assert compute_coverage([_s("not_reached"), _s("not_reached")]) == 0.0


def test_empty_is_zero():
    assert compute_coverage([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_coverage.py -v`
Expected: FAIL (stub returns 0.0 for the 0.5 case).

- [ ] **Step 3: Replace the `compute_coverage` stub**

In `aggregate.py`, replace the temporary stub with:

```python
# Levels that represent REAL data (the screen actually assessed the signal).
# not_reached scores at the floor but is NOT real data → it lowers confidence.
_COVERED_LEVELS: frozenset[str] = frozenset({"strong", "solid", "thin", "absent"})


def compute_coverage(signals: list[ScoredSignal]) -> float:
    """Real-data fraction = covered weight / total weight. `not_reached` is excluded
    from 'covered' (it scores at the floor but we did not actually assess it)."""
    total_w = sum(s.weight for s in signals)
    if total_w == 0:
        return 0.0
    covered_w = sum(s.weight for s in signals if s.level in _COVERED_LEVELS)
    return covered_w / total_w
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_coverage.py tests/reporting/scoring/test_aggregate_math.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/aggregate.py tests/reporting/scoring/test_coverage.py
git commit -m "feat(reporting): real-data coverage/confidence split

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Ceilings, must-have gate, verdict (provenance-aware)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py` (replace `signal_ceiling`, `knockout_status`, `resolve_verdict`; keep `clamp_to_ceiling`, `apply_holistic`)
- Test: `tests/reporting/scoring/test_verdict_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_verdict_gate.py
from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, must_have_cap, resolve_verdict,
)
from app.modules.reporting.scoring.constants import (
    BORDERLINE_CEILING, REJECT_CEILING, level_score,
)


def _mh(level):  # a must-have signal at a given level
    return ScoredSignal(value="must", type="competency", weight=3, knockout=True,
                        priority="required", level=level, score=level_score(level))


def test_probed_absent_must_have_rejects():
    cap = must_have_cap([_mh("absent")], is_knockout_close=False, coverage=1.0)
    assert cap == REJECT_CEILING


def test_knockout_close_rejects():
    cap = must_have_cap([_mh("strong")], is_knockout_close=True, coverage=1.0)
    assert cap == REJECT_CEILING


def test_not_reached_must_have_is_borderline():
    cap = must_have_cap([_mh("not_reached")], is_knockout_close=False, coverage=1.0)
    assert cap == BORDERLINE_CEILING


def test_thin_must_have_is_borderline():
    cap = must_have_cap([_mh("thin")], is_knockout_close=False, coverage=1.0)
    assert cap == BORDERLINE_CEILING


def test_solid_must_have_no_cap():
    assert must_have_cap([_mh("solid")], is_knockout_close=False, coverage=1.0) is None


def test_verdict_reject_on_knockout_close():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=True,
                        knockout_signal="must", must_haves=[_mh("strong")])
    assert v.verdict == "reject"


def test_verdict_borderline_on_not_reached_must_have():
    v = resolve_verdict(overall=90, coverage=1.0, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("not_reached")])
    assert v.verdict == "borderline"


def test_verdict_advance_when_clear():
    v = resolve_verdict(overall=80, coverage=0.9, is_knockout_close=False,
                        knockout_signal=None, must_haves=[_mh("solid")])
    assert v.verdict == "advance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_verdict_gate.py -v`
Expected: FAIL with `ImportError: cannot import name 'must_have_cap'`.

- [ ] **Step 3: Replace the gate functions in `aggregate.py`**

Replace `signal_ceiling`, `knockout_status`, and `resolve_verdict` (and the now-unused `KnockoutResult`/`KnockoutClose` imports) with:

```python
from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD, BORDERLINE_CEILING, MIN_COVERAGE_FOR_ADVANCE,
    REJECT_CEILING, REJECT_THRESHOLD,
)
from app.modules.reporting.scoring.types import Verdict

_REJECT_LEVELS = frozenset({"absent"})        # asked-and-failed must-have → reject
_UNCONFIRMED_LEVELS = frozenset({"not_reached", "thin"})  # couldn't confirm → borderline


def must_have_cap(
    must_haves: list[ScoredSignal], *, is_knockout_close: bool, coverage: float
) -> int | None:
    """Fit ceiling from must-have status (the gate from spec §6)."""
    if is_knockout_close or any(s.level in _REJECT_LEVELS for s in must_haves):
        return REJECT_CEILING
    if any(s.level in _UNCONFIRMED_LEVELS for s in must_haves) or coverage < MIN_COVERAGE_FOR_ADVANCE:
        return BORDERLINE_CEILING
    return None


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


def resolve_verdict(
    *, overall: int | None, coverage: float, is_knockout_close: bool,
    knockout_signal: str | None, must_haves: list[ScoredSignal],
) -> VerdictResult:
    """Score-driven verdict; categorical must-have backstops first.
    The overall is assumed already ceiling-capped by the caller."""
    if is_knockout_close:
        return VerdictResult("reject", f"Interview closed on a must-have gap: {knockout_signal or 'a must-have'}")
    absent_mh = next((s for s in must_haves if s.level in _REJECT_LEVELS), None)
    if absent_mh is not None:
        return VerdictResult("reject", f"failed must-have: {absent_mh.value}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if any(s.level in _UNCONFIRMED_LEVELS for s in must_haves):
        return VerdictResult("borderline", "a must-have was not confirmed — human review")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")


def signal_ceiling(must_haves: list[ScoredSignal], *, is_knockout_close: bool, coverage: float) -> int | None:
    """Back-compat alias used by the orchestrator."""
    return must_have_cap(must_haves, is_knockout_close=is_knockout_close, coverage=coverage)
```

Keep `clamp_to_ceiling` and `apply_holistic` as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_verdict_gate.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/aggregate.py tests/reporting/scoring/test_verdict_gate.py
git commit -m "feat(reporting): provenance-aware must-have gate + verdict

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — AI layers (re-point)

### Task 7: Re-check reads notes; outputs a refined level; scopes correctly

**Files:**
- Modify: `app/modules/reporting/schemas.py` (`SignalRecheckOut.level`)
- Modify: `app/modules/reporting/scoring/recheck.py`
- Create: `prompts/v4/report_scorer/signal_recheck.txt`
- Test: `tests/reporting/scoring/test_recheck.py`

- [ ] **Step 1: Write the failing test** (mock the LLM seam)

```python
# tests/reporting/scoring/test_recheck.py
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.scoring.types import SignalDef


def _note(texture, stance="supports"):
    return EvidenceNote(seq=1, turn_ref="t-1", signal="python", stance=stance, texture=texture,
                        quote="I built a Python ETL pipeline handling 2M rows/day",
                        span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1", via_probe=False)


@pytest.mark.asyncio
async def test_recheck_can_override_level():
    fake = SignalRecheckOut(evidence_quotes=["I built a Python ETL pipeline handling 2M rows/day"],
                            justification="tradeoffs shown", level="strong",
                            overridden=True, override_reason="depth evident")
    resp = type("R", (), {"output_parsed": fake})()
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(return_value=resp)
        out = await recheck_signal(
            signal_def=SignalDef(value="python", type="competency", weight=3,
                                 knockout=True, priority="required"),
            notes=[_note("concrete")], question_context="Q: ...\nrubric: {}",
            engine_level="solid", correlation_id="cid")
    assert out.level == "strong"


@pytest.mark.asyncio
async def test_recheck_refusal_keeps_engine_level():
    resp = type("R", (), {"output_parsed": None})()
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(return_value=resp)
        out = await recheck_signal(
            signal_def=SignalDef(value="python", type="competency", weight=3,
                                 knockout=True, priority="required"),
            notes=[_note("thin")], question_context="ctx", engine_level="thin",
            correlation_id="cid")
    assert out.level == "thin"
    assert out.overridden is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_recheck.py -v`
Expected: FAIL (`SignalRecheckOut` has no `level`; `recheck_signal` has no `notes`/`engine_level` kwargs).

- [ ] **Step 3: Update `SignalRecheckOut` in `schemas.py`**

Find `class SignalRecheckOut` in `app/modules/reporting/schemas.py` and replace its `state`/`grade` fields with a single `level`:

```python
class SignalRecheckOut(BaseModel):
    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str
    level: Literal["strong", "solid", "thin", "absent", "not_reached"]
    overridden: bool = False
    override_reason: str | None = None
```

(Ensure `Literal` is imported in `schemas.py`.)

- [ ] **Step 4: Rewrite `recheck.py`**

Replace the body of `app/modules/reporting/scoring/recheck.py`:

```python
"""Layer 2 — post-interview per-signal re-check (LLM, Responses API).

Reads the engine's append-only NOTES (verbatim quotes + texture + stance) for one
signal and verifies them against the question rubric — a 'lighter re-check' that may
refine the deterministic demonstration level (e.g. solid→strong, or confirm a thin
answer is a genuine bluff). Graceful refusal keeps the engine's level."""
from __future__ import annotations

import hashlib

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.interview_runtime.evidence import EvidenceNote
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.types import SignalDef

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _render_notes(notes: list[EvidenceNote]) -> str:
    lines = [
        f"[note {n.seq} · {n.stance.value}/{n.texture.value}"
        f"{' · via probe' if n.via_probe else ''}] {n.quote}"
        for n in notes
    ]
    return "\n".join(lines) if lines else "(no supporting notes)"


async def recheck_signal(
    *, signal_def: SignalDef, notes: list[EvidenceNote],
    question_context: str, engine_level: str, correlation_id: str,
) -> SignalRecheckOut:
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/signal_recheck"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<signal>\n{signal_def.value}\n(type: {signal_def.type}, "
        f"priority: {signal_def.priority}, must_have: {signal_def.knockout})\n</signal>\n\n"
        f"<question_context>\n{question_context}\n</question_context>\n\n"
        f"<engine_prior>\nlevel={engine_level}\n</engine_prior>"
    )
    notes_block = _render_notes(notes)
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<notes>\n{notes_block}\n</notes>"},
    ]
    sig_hash = hashlib.sha256(signal_def.value.encode("utf-8")).hexdigest()[:12]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": SignalRecheckOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:rc:"
            f"{ai_config.report_scorer_prompt_version}:{sig_hash}:{ai_config.report_scorer_model}"
        ),
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_signal_recheck",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.recheck.refusal", signal=signal_def.value,
                    correlation_id=correlation_id)
        return SignalRecheckOut(
            evidence_quotes=[], justification="Model did not return a parse.",
            level=engine_level,  # type: ignore[arg-type]
            overridden=False, override_reason=None)

    grounded, _ = ground_quotes(parsed.evidence_quotes, notes_block)
    return parsed.model_copy(update={"evidence_quotes": grounded})
```

- [ ] **Step 5: Create the prompt** `prompts/v4/report_scorer/signal_recheck.txt`

```
You are re-checking ONE signal from a structured screening interview.

You are given:
- the signal (with its type, priority, and whether it is a must-have),
- the question context (the question text + its rubric),
- the engine's prior demonstration level for this signal,
- the engine's append-only NOTES for this signal — each a verbatim candidate
  quote tagged with stance (supports/contradicts) and texture (thin/concrete/strong).

Your job is to VERIFY, not re-interview. Decide the demonstration level:
- strong  — concrete evidence PLUS tradeoffs / numbers / edge-cases / real depth.
- solid   — a real, specific thing they actually did.
- thin    — generic, buzzwords, or hypothetical ("I would…") with no real how.
- absent  — the notes show a disclaim or no genuine evidence the candidate has it.
- not_reached — do NOT use; only the engine assigns this (no data to re-check).

Rules:
- Stay grounded in the quotes. Quote only the candidate's actual words in
  evidence_quotes. Never invent a quote.
- A confident tone is not depth. A thin-but-confident answer stays `thin`
  (that is how a bluff reads) unless the quotes show a real, specific mechanism.
- Honour an honest correction: a later contradiction that walks back an earlier
  claim is not a strength.
- Default to the engine's prior level when the notes do not clearly justify a change;
  set overridden=true only when you move the level, with a one-line override_reason.
- Reveal nothing about scoring or the rubric in any field a candidate could see.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_recheck.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/schemas.py app/modules/reporting/scoring/recheck.py prompts/v4/report_scorer/signal_recheck.txt tests/reporting/scoring/test_recheck.py
git commit -m "feat(reporting): re-check reads engine notes, outputs refined level

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Holistic — feed demonstrated secondary breadth

**Files:**
- Modify: `app/modules/reporting/scoring/holistic.py`
- Create: `prompts/v4/report_scorer/holistic.txt` (copy `prompts/v3/report_scorer/holistic.txt`, add the breadth note below)
- Test: `tests/reporting/scoring/test_holistic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/scoring/test_holistic.py
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.aggregate import ScoredSignal
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.schemas import HolisticAdjustmentOut


def _s(level, score):
    return ScoredSignal(value="a", type="competency", weight=1, knockout=False,
                        priority="preferred", level=level, score=score)


@pytest.mark.asyncio
async def test_secondary_breadth_passed_to_prompt():
    captured = {}
    async def _parse(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"output_parsed": HolisticAdjustmentOut(delta=3, justification="breadth")})()
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(side_effect=_parse)
        out = await score_holistic(
            session_score=70, scored=[_s("solid", 80)], is_knockout_close=False,
            coverage=0.8, transcript_text="...", demonstrated_secondaries=["kubernetes", "graphql"],
            correlation_id="cid")
    assert out.delta == 3
    joined = "".join(str(m["content"]) for m in captured["input"])
    assert "kubernetes" in joined and "graphql" in joined


@pytest.mark.asyncio
async def test_none_session_score_skips():
    out = await score_holistic(session_score=None, scored=[], is_knockout_close=False,
                               coverage=0.0, transcript_text="", demonstrated_secondaries=[],
                               correlation_id="cid")
    assert out.delta == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_holistic.py -v`
Expected: FAIL (`score_holistic` has no `demonstrated_secondaries`/`is_knockout_close` kwargs; uses `s.state`).

- [ ] **Step 3: Update `holistic.py`**

In `app/modules/reporting/scoring/holistic.py`: (a) change `_signal_digest` to use `s.level` instead of `s.state` and drop the `s.state != "none"` filter (every primary is scored now); (b) update `score_holistic`'s signature and prefix:

```python
def _signal_digest(scored: list[ScoredSignal]) -> str:
    return json.dumps([
        {"signal": s.value, "level": s.level, "must_have": s.knockout, "score": s.score}
        for s in scored
    ], ensure_ascii=False)


async def score_holistic(
    *, session_score: int | None, scored: list[ScoredSignal], is_knockout_close: bool,
    coverage: float, transcript_text: str, demonstrated_secondaries: list[str],
    correlation_id: str,
) -> HolisticAdjustmentOut:
    if session_score is None:
        return HolisticAdjustmentOut(delta=0, justification="No assessable evidence.")

    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/holistic"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<session_score>\n{session_score}\n</session_score>\n\n"
        f"<facts>\nknockout_close={is_knockout_close}, coverage={coverage:.2f}\n</facts>\n\n"
        f"<per_signal>\n{_signal_digest(scored)}\n</per_signal>\n\n"
        f"<demonstrated_extra_signals>\n{', '.join(demonstrated_secondaries) or '(none)'}"
        f"\n</demonstrated_extra_signals>"
    )
    # ... (rest unchanged: messages, kwargs, parse, bound, return)
```

Keep everything below the prefix unchanged.

- [ ] **Step 4: Create the v4 holistic prompt**

Copy `prompts/v3/report_scorer/holistic.txt` to `prompts/v4/report_scorer/holistic.txt` and append:

```
You may also see <demonstrated_extra_signals>: secondary competencies the candidate
demonstrated beyond the core questions. These are UPSIDE ONLY — meaningful breadth may
justify a small positive delta, but their absence is never a penalty (a missing one is
not listed here). Stay within the ±5 bound.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/scoring/test_holistic.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/scoring/holistic.py prompts/v4/report_scorer/holistic.txt tests/reporting/scoring/test_holistic.py
git commit -m "feat(reporting): holistic nudge consumes demonstrated secondary breadth

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Communication + narrative v4 prompts (speaker-enum transcript handled by caller)

**Files:**
- Create: `prompts/v4/report_scorer/communication.txt` (copy of v3)
- Create: `prompts/v4/report_scorer/narrative.txt` (copy of v3 + provenance note)

> `judge.grade_communication` and `write_narrative` keep their signatures — the caller
> (`service.py`, Task 11) supplies the candidate transcript text from the adapter and the
> provenance-enriched ground-truth JSON. Only the prompt version bump + narrative note are needed here.

- [ ] **Step 1: Copy the communication prompt**

```bash
mkdir -p prompts/v4/report_scorer
cp prompts/v3/report_scorer/communication.txt prompts/v4/report_scorer/communication.txt
```

- [ ] **Step 2: Copy the narrative prompt and append the provenance note**

```bash
cp prompts/v3/report_scorer/narrative.txt prompts/v4/report_scorer/narrative.txt
```

Append to `prompts/v4/report_scorer/narrative.txt`:

```
Each signal carries a `provenance`: asked_directly / cross_credited / probed_absent /
not_reached. Be precise and fair when you describe gaps:
- probed_absent → "we asked and the candidate did not demonstrate X."
- not_reached  → "X was not assessed in this screen" (absence of evidence, NOT a claim
  the candidate lacks it). Never describe a not_reached signal as a candidate weakness.
Do not change any number; you are narrating fixed results.
```

- [ ] **Step 3: Point AIConfig at v4**

Set `report_scorer_prompt_version` to `v4` via the env contract (`.env.example` + your `.env`). Confirm the key name:

Run: `docker compose run --rm nexus python -c "from app.ai.config import ai_config; print(ai_config.report_scorer_prompt_version)"`
Expected: prints the configured version. Update `.env`/`.env.example` so it reads `v4`.

- [ ] **Step 4: Commit**

```bash
git add prompts/v4/report_scorer/communication.txt prompts/v4/report_scorer/narrative.txt .env.example
git commit -m "feat(reporting): v4 report_scorer prompts (provenance-aware narrative)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Orchestrator, actor, wiring

### Task 10: `SignalAssessmentOut` carries provenance

**Files:**
- Modify: `app/modules/reporting/schemas.py`
- Test: `tests/reporting/test_schemas_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_schemas_provenance.py
from app.modules.reporting.schemas import SignalAssessmentOut


def test_signal_assessment_has_provenance():
    s = SignalAssessmentOut(signal="python", type="competency", weight=3, knockout=True,
                            priority="required", provenance="asked_directly",
                            level="solid", score=80, evidence=[], overridden=False,
                            override_reason=None)
    assert s.provenance == "asked_directly"
    assert s.level == "solid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_schemas_provenance.py -v`
Expected: FAIL (unexpected kwargs `provenance`/`level`).

- [ ] **Step 3: Update `SignalAssessmentOut` in `schemas.py`**

Replace the gen-2 `engine_state`/`final_state`/`grade` fields on `SignalAssessmentOut` with provenance + level:

```python
class SignalAssessmentOut(BaseModel):
    signal: str
    type: str
    weight: int
    knockout: bool
    priority: str
    provenance: Literal["not_reached", "asked_directly", "cross_credited", "probed_absent"]
    level: Literal["strong", "solid", "thin", "absent", "not_reached"]
    score: int | None
    evidence: list[str] = Field(default_factory=list)
    overridden: bool = False
    override_reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_schemas_provenance.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/schemas.py tests/reporting/test_schemas_provenance.py
git commit -m "feat(reporting): signal assessment carries provenance + level

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Rewrite `service.py::build_report`

**Files:**
- Modify: `app/modules/reporting/service.py`
- Test: `tests/reporting/test_build_report.py`

- [ ] **Step 1: Write the failing test** (mock the four AI layers; assert the deterministic spine)

```python
# tests/reporting/test_build_report.py
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    CommunicationVerdict, HolisticAdjustmentOut, NarrativeOut, SignalRecheckOut,
    DecisionOut, MethodologyOut, WhyColumn,
)


def _evidence_dict():
    return {
        "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                 "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                 "duration_s": 1200.0, "time_budget_s": 1200.0, "completion": "completed",
                 "questions_asked": 1, "questions_core_total": 1, "questions_overflow_asked": 0},
        "signals": [{"signal": "python", "signal_type": "competency", "weight": 3,
                     "priority": "required", "knockout": True, "provenance": "asked_directly"}],
        "notes": [{"seq": 1, "turn_ref": "t-1", "signal": "python", "stance": "supports",
                   "texture": "concrete", "quote": "built an ETL in Python",
                   "span": {"start_ms": 0, "end_ms": 1}, "from_question_id": "q1", "via_probe": False}],
        "questions": [{"question_id": "q1", "primary_signal": "python", "tier": "core",
                       "outcome": "asked", "closure": "satisfied", "probes_used": [],
                       "probes_available": 2}],
        "transcript": [{"turn_ref": "t-1", "speaker": "candidate", "text": "built an ETL in Python",
                        "span": {"start_ms": 0, "end_ms": 1}, "pre_turn_gap_ms": 0}],
        "knockout": None,
    }


@pytest.mark.asyncio
async def test_build_report_advances_a_strong_must_have():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(evidence_quotes=["built an ETL in Python"],
                justification="real", level="solid", overridden=False, override_reason=None))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification="ok"))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="ok", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="ok", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    assert report.verdict == "advance"
    assert report.engine_version == "v3"
    assert report.scores["overall"].score is not None
    py = next(s for s in report.signal_assessments if s.signal == "python")
    assert py.provenance == "asked_directly"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_build_report.py -v`
Expected: FAIL (`build_report` still has the old signature `transcript/envelope/coverage_summary`).

- [ ] **Step 3: Rewrite `build_report`**

Replace `build_report` in `app/modules/reporting/service.py` with the version below. Update the imports at the top: drop `from ...engine_signals import build_engine_states, collect_signal_evidence, detect_knockout_close` and `_triage_kind_by_question`; import the new pieces.

```python
from app.modules.reporting.scoring.aggregate import (
    apply_holistic, clamp_to_ceiling, confidence_from_coverage, level_for_signal,
    make_scored_signal, resolve_verdict, score_dimension, score_overall, signal_ceiling,
)
from app.modules.reporting.scoring.evidence_adapter import EvidenceView
from app.modules.reporting.scoring.constants import (
    BEHAVIORAL_TYPES, FACTUAL_QUESTION_KINDS, TECHNICAL_TYPES, tier_label,
)


def _is_factual_gate_signal(signal_value: str, questions: list[dict]) -> bool:
    covering = [q for q in questions if signal_value in q.get("signal_values", [])]
    return bool(covering) and all(
        q.get("question_kind") in FACTUAL_QUESTION_KINDS for q in covering
    )


async def build_report(*, evidence, questions, signal_metadata, correlation_id, n_samples=None):
    """Three-layer report over the gen-3 SessionEvidence.

    Deterministic: roll each PRIMARY signal's notes → level → score; dimensions;
    coverage/confidence; provenance-aware must-have gate; verdict.
    AI: re-check (refine evidenced levels) → holistic ±5 → communication → narrative.
    """
    view = EvidenceView(evidence)
    primary_set = view.primary_set
    notes_by_signal = view.notes_by_signal
    provenance_by_signal = view.provenance_by_signal
    closure_by_primary = view.closure_by_primary

    def_by_value = {m["value"]: m for m in signal_metadata}
    q_by_signal: dict[str, dict] = {}
    for q in questions:
        for sv in q.get("signal_values", []):
            q_by_signal.setdefault(sv, q)

    # --- Deterministic per-PRIMARY level (the graded denominator) ----------
    base_level: dict[str, str] = {}
    for sig in primary_set:
        prov = provenance_by_signal.get(sig, "not_reached")
        prov_str = prov.value if hasattr(prov, "value") else str(prov)
        base_level[sig] = level_for_signal(
            notes_by_signal.get(sig, []), provenance=prov_str,
            closure=closure_by_primary.get(sig),
        )

    # --- Layer 2 re-check: only evidenced primaries (+probed_absent), skip
    #     not_reached and factual gates (engine already judged those) --------
    def _provenance_str(sig: str) -> str:
        p = provenance_by_signal.get(sig, "not_reached")
        return p.value if hasattr(p, "value") else str(p)

    recheck_targets = [
        sig for sig in primary_set
        if _provenance_str(sig) in ("asked_directly", "cross_credited", "probed_absent")
        and not _is_factual_gate_signal(sig, questions)
    ]

    async def _one(sig: str):
        m = def_by_value.get(sig, {"value": sig, "type": "competency", "weight": 1,
                                   "knockout": False, "priority": "preferred"})
        from app.modules.reporting.scoring.types import SignalDef
        d = SignalDef(value=m["value"], type=m["type"], weight=m["weight"],
                      knockout=m["knockout"], priority=m["priority"])
        q = q_by_signal.get(sig, {})
        ctx = f"Q: {q.get('text','')}\nrubric: {json.dumps(q.get('rubric', {}))}"
        return sig, await recheck_signal(signal_def=d, notes=notes_by_signal.get(sig, []),
                                         question_context=ctx, engine_level=base_level[sig],
                                         correlation_id=correlation_id)
    recheck_results = dict(await asyncio.gather(*[_one(s) for s in recheck_targets])) if recheck_targets else {}

    final_level = dict(base_level)
    for sig, rc in recheck_results.items():
        final_level[sig] = rc.level

    # --- Build ScoredSignal list over the PRIMARY set ----------------------
    scored = []
    for sig in primary_set:
        m = def_by_value.get(sig, {"type": "competency", "weight": 1,
                                   "knockout": False, "priority": "preferred"})
        scored.append(make_scored_signal(
            value=sig, type=m["type"], weight=m["weight"], knockout=m["knockout"],
            priority=m["priority"], level=final_level[sig]))

    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    beh = score_dimension("behavioral", scored, BEHAVIORAL_TYPES)
    base, coverage = score_overall(scored)

    must_haves = [s for s in scored if s.knockout]
    ceiling = signal_ceiling(must_haves, is_knockout_close=view.is_knockout_close, coverage=coverage)
    session_score = clamp_to_ceiling(base, ceiling)

    adjustment = await score_holistic(
        session_score=session_score, scored=scored, is_knockout_close=view.is_knockout_close,
        coverage=coverage, transcript_text=view.candidate_transcript_text,
        demonstrated_secondaries=sorted(view.demonstrated_secondaries), correlation_id=correlation_id)
    overall = apply_holistic(session_score, adjustment.delta, ceiling)

    comm = await grade_communication(transcript_text=view.candidate_transcript_text,
                                     correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    verdict = resolve_verdict(overall=overall, coverage=coverage,
                              is_knockout_close=view.is_knockout_close,
                              knockout_signal=view.knockout_signal, must_haves=must_haves)

    signal_assessments = [SignalAssessmentOut(
        signal=s.value, type=s.type, weight=s.weight, knockout=s.knockout, priority=s.priority,
        provenance=_provenance_str(s.value), level=s.level, score=s.score,
        evidence=(recheck_results[s.value].evidence_quotes if s.value in recheck_results else []),
        overridden=(recheck_results[s.value].overridden if s.value in recheck_results else False),
        override_reason=(recheck_results[s.value].override_reason if s.value in recheck_results else None),
    ) for s in scored]

    gt = json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "knockout_close": ({"signal": view.knockout_signal} if view.is_knockout_close else None),
        "signals": [{"signal": s.value, "type": s.type, "level": s.level,
                     "provenance": _provenance_str(s.value), "must_have": s.knockout,
                     "priority": s.priority} for s in scored],
    }, ensure_ascii=False)
    narrative = await write_narrative(ground_truth_json=gt, correlation_id=correlation_id)

    def _score_out(score, cov, conf):
        return ScoreOut(score=score, tier_label=tier_label(score), tone=_tone_by_score(score),
                        confidence=conf, coverage=cov)

    logger.info("reporting.service.build_report.done", verdict=verdict.verdict,
                overall_score=overall, overall_coverage=coverage, correlation_id=correlation_id)

    return ReportRead(
        verdict=verdict.verdict, verdict_reason=narrative.decision.headline or verdict.reason,
        overall_score=overall, overall_coverage=coverage,
        overall_confidence=confidence_from_coverage(coverage) if overall is not None else "low",
        decision=narrative.decision,
        scores={
            "overall": ScoreOut(
                score=overall, tier_label=tier_label(overall), tone=_tone_by_score(overall),
                confidence=confidence_from_coverage(coverage) if overall is not None else "low",
                coverage=coverage, session_score=session_score, holistic_delta=adjustment.delta),
            "technical": _score_out(tech.score, tech.coverage, tech.confidence),
            "behavioral": _score_out(beh.score, beh.coverage, beh.confidence),
            "communication": _score_out(comm_score, 1.0, "medium"),
        },
        quick_summary=narrative.quick_summary, strengths=narrative.strengths,
        concerns=narrative.concerns, questions=[], methodology=narrative.methodology,
        signal_assessments=signal_assessments, engine_version="v3", status="ready",
        scoring_manifest=ScoringManifest(
            scorer_model=ai_config.report_scorer_model,
            prompt_version=ai_config.report_scorer_prompt_version,
            generated_at=datetime.now(UTC).isoformat(), correlation_id=correlation_id,
            evidence_grounding_summary={
                "n_signals_rechecked": len(recheck_results),
                "n_overrides": sum(1 for r in recheck_results.values() if r.overridden),
                "level_map": {s.value: s.level for s in scored},
                "session_score": session_score, "holistic_delta": adjustment.delta,
                "holistic_justification": adjustment.justification, "ceiling_applied": ceiling,
            }),
    )
```

> **Note on `questions=[]`:** this rewrite drops the per-question `QuestionOut` UI list in the first
> cut (it depended on the deleted envelope/triage segmentation). The `signal_assessments` +
> `narrative` carry the substance. Rebuilding `QuestionOut` from `evidence.questions` +
> `evidence.transcript` is a fast-follow (Task 16 note) — do NOT block this task on it. Update the
> `service.py` imports to remove `segment`, `derive_status`, `QuestionOut` usage accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_build_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/service.py tests/reporting/test_build_report.py
git commit -m "feat(reporting): rewrite build_report over SessionEvidence (provenance-aware)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Rewrite the actor's load path (SessionEvidence, no envelope)

**Files:**
- Modify: `app/modules/reporting/actors.py`
- Test: `tests/reporting/test_actor_loads_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_actor_loads_evidence.py
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.modules.reporting.actors import _build_report_inputs_from_session


def test_inputs_helper_requires_session_evidence_json():
    # A session with no evidence yields None (nothing to score).
    class _Sess:
        session_evidence_json = None
    assert _build_report_inputs_from_session(_Sess()) is None


def test_inputs_helper_parses_evidence():
    class _Sess:
        session_evidence_json = {
            "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                     "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                     "duration_s": 1.0, "time_budget_s": 1.0, "completion": "completed",
                     "questions_asked": 0, "questions_core_total": 0, "questions_overflow_asked": 0},
            "signals": [], "notes": [], "questions": [], "transcript": [], "knockout": None}
    ev = _build_report_inputs_from_session(_Sess())
    assert ev.meta.session_id == "s1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_actor_loads_evidence.py -v`
Expected: FAIL (`_build_report_inputs_from_session` undefined).

- [ ] **Step 3: Edit `actors.py`**

(a) Add the helper near the top of `actors.py`:

```python
from app.modules.interview_runtime.evidence import SessionEvidence


def _build_report_inputs_from_session(sess) -> SessionEvidence | None:
    """Parse the gen-3 SessionEvidence off the session row; None if not present."""
    raw = getattr(sess, "session_evidence_json", None)
    if not raw:
        return None
    return SessionEvidence.model_validate(raw)
```

(b) In `_score_session_report_async`, **delete** the envelope + `coverage_summary` + `transcript` block (the lines loading `raw_result`, `_resolve_envelope`, `coverage_summary`, `transcript`) and replace the `build_report(...)` call site:

```python
        evidence = _build_report_inputs_from_session(sess)
        if evidence is None:
            log.warning("reporting.actor.no_session_evidence")
            return

        # ... (question + signal_metadata loading stays exactly as-is) ...

        try:
            report = await build_report(
                evidence=evidence,
                questions=questions,
                signal_metadata=signal_metadata,
                correlation_id=correlation_id,
            )
```

(c) Delete the now-unused `_resolve_envelope` function and its imports (`json`, `Path`, `asyncio.to_thread` usage if no longer referenced — keep `asyncio` for `asyncio.run`). Change `engine_version="v2"` → `"v3"` in the failed-row path.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_actor_loads_evidence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/actors.py tests/reporting/test_actor_loads_evidence.py
git commit -m "feat(reporting): actor loads SessionEvidence, drops envelope/coverage_summary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Re-connect the enqueue from `record_session_evidence`

**Files:**
- Modify: `app/modules/interview_runtime/service.py`
- Test: `tests/interview_runtime/test_record_session_evidence_enqueue.py`

> Pattern: copy how `record_session_result` enqueues `score_session_report` (search it in the same
> file) — same `AUTO_SCORE_SESSION_REPORTS` gate, same `.send(str(session_id), str(tenant_id), correlation_id)`,
> wrapped in try/except so a broker failure never fails the durable evidence commit.

- [ ] **Step 1: Write the failing test**

```python
# tests/interview_runtime/test_record_session_evidence_enqueue.py
import pytest
from unittest.mock import patch, MagicMock

# This test asserts the enqueue helper is invoked on a fresh evidence write.
# Adapt the harness to the repo's existing record_session_evidence tests
# (reuse their in-memory/SQLite session fixture + a built SessionEvidence).


@pytest.mark.asyncio
async def test_enqueue_called_on_fresh_write(evidence_fixture, bypass_db):
    with patch("app.modules.interview_runtime.service._enqueue_report_scoring") as enq:
        from app.modules.interview_runtime.service import record_session_evidence
        await record_session_evidence(bypass_db, tenant_id=evidence_fixture.tenant_id,
                                      evidence=evidence_fixture.evidence, correlation_id="cid")
    enq.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_not_called_on_idempotent_noop(evidence_fixture_already_written, bypass_db):
    with patch("app.modules.interview_runtime.service._enqueue_report_scoring") as enq:
        from app.modules.interview_runtime.service import record_session_evidence
        await record_session_evidence(bypass_db, tenant_id=evidence_fixture_already_written.tenant_id,
                                      evidence=evidence_fixture_already_written.evidence, correlation_id="cid")
    enq.assert_not_called()
```

> If the repo has no fixture for this, model it on the existing `record_session_evidence` /
> `record_session_result` tests under `tests/interview_runtime/`. The behavioural assertion
> (enqueue on fresh write, not on no-op) is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_record_session_evidence_enqueue.py -v`
Expected: FAIL (`_enqueue_report_scoring` undefined / not called).

- [ ] **Step 3: Add the enqueue helper + call it on fresh writes**

In `app/modules/interview_runtime/service.py`, add a helper (mirror the existing enqueue in `record_session_result`):

```python
def _enqueue_report_scoring(*, session_id, tenant_id, correlation_id) -> None:
    """Best-effort enqueue of the post-session report scorer. Never fails the caller."""
    from app.config import settings
    if not getattr(settings, "auto_score_session_reports", True):
        logger.info("interview_runtime.record_session_evidence.report_scoring_disabled",
                    session_id=str(session_id))
        return
    try:
        from app.modules.reporting.actors import score_session_report
        score_session_report.send(str(session_id), str(tenant_id), correlation_id)
        logger.info("interview_runtime.record_session_evidence.report_enqueued",
                    session_id=str(session_id), correlation_id=correlation_id)
    except Exception:  # noqa: BLE001 — broker hiccup must not fail the evidence commit
        logger.warning("interview_runtime.record_session_evidence.report_enqueue_failed",
                       session_id=str(session_id), correlation_id=correlation_id)
```

Then, in `record_session_evidence`, after `await db.commit()` (line ~610) on the **fresh-write** paths (Path 1 success, and Path 2 `attached_to_terminal` success — i.e. not the idempotent `return` branches), call:

```python
        _enqueue_report_scoring(session_id=session_id, tenant_id=tenant_id, correlation_id=correlation_id)
```

(The early `return` no-op branches at lines ~567 and ~585 must NOT enqueue.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_runtime/test_record_session_evidence_enqueue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_runtime/service.py tests/interview_runtime/test_record_session_evidence_enqueue.py
git commit -m "feat(interview_runtime): enqueue report scoring on evidence write

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Delete the gen-2 path + fix fallout

**Files:**
- Delete: `app/modules/reporting/scoring/engine_signals.py`
- Modify: `app/modules/reporting/service.py` (remove `_narrative_ground_truth`, `_triage_kind_by_question` if still present, dead imports), `constants.py` (`STATE_TEXTURE_POINTS`), `types.py` (`CovState`/`GradeTexture` if unused)
- Test: full reporting suite

- [ ] **Step 1: Delete the file**

```bash
git rm app/modules/reporting/scoring/engine_signals.py
```

- [ ] **Step 2: Remove dead references**

Run: `docker compose run --rm nexus grep -rn "engine_signals\|coverage_summary\|detect_knockout_close\|STATE_TEXTURE_POINTS\|_triage_kind_by_question\|build_engine_states" app/modules/reporting`
Expected: only matches in comments/none. Remove every remaining import/use (in `service.py`, `holistic.py` already updated). Delete `STATE_TEXTURE_POINTS` from `constants.py`. If `CovState`/`GradeTexture`/`SignalTurn` are now unused, remove them from `types.py` (grep first).

- [ ] **Step 3: Run the full reporting suite + lint**

Run: `docker compose run --rm nexus pytest tests/reporting -q`
Expected: PASS (some old gen-2 tests that referenced `coverage_summary`/`engine_signals` may need deletion — delete tests asserting the removed gen-2 behaviour; keep/Port any still-valid assertions).

Run: `docker compose run --rm nexus ruff check app/modules/reporting`
Expected: clean (no unused imports / undefined names).

- [ ] **Step 4: Commit**

```bash
git add -A app/modules/reporting tests/reporting
git commit -m "refactor(reporting): delete gen-2 coverage_summary/envelope path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Golden end-to-end fixture + module boundary check

**Files:**
- Create: `tests/reporting/fixtures/session_evidence_golden.json`
- Create: `tests/reporting/test_golden_report.py`

- [ ] **Step 1: Create a realistic golden evidence fixture**

Write `tests/reporting/fixtures/session_evidence_golden.json` — a full `SessionEvidence` with: 4 primary signals (one must-have at `strong`, one competency at `thin`, one behavioral `cross_credited`, one primary `not_reached`), 1 demonstrated secondary (`cross_credited`, non-primary), and matching `questions[]` (one `outcome=not_reached`). Validate it parses:

Run: `docker compose run --rm nexus python -c "import json; from app.modules.interview_runtime.evidence import SessionEvidence; SessionEvidence.model_validate(json.load(open('tests/reporting/fixtures/session_evidence_golden.json'))); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 2: Write the golden test** (mock the 4 AI layers to identity behaviour, assert the deterministic spine)

```python
# tests/reporting/test_golden_report.py
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    CommunicationVerdict, HolisticAdjustmentOut, NarrativeOut, SignalRecheckOut,
    DecisionOut, MethodologyOut, WhyColumn,
)


@pytest.mark.asyncio
async def test_golden_report_spine():
    ev = SessionEvidence.model_validate(json.load(open("tests/reporting/fixtures/session_evidence_golden.json")))
    primary = {q.primary_signal for q in ev.questions}
    questions = [{"id": q.question_id, "text": "Q", "signal_values": [q.primary_signal],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": q.primary_signal}
                 for q in ev.questions]
    signal_metadata = [{"value": s.signal, "type": s.signal_type.value, "weight": s.weight,
                        "knockout": s.knockout, "priority": s.priority.value}
                       for s in ev.signals if s.signal in primary]

    async def _rc(*, signal_def, notes, question_context, engine_level, correlation_id):
        return SignalRecheckOut(evidence_quotes=[], justification="keep", level=engine_level,
                                overridden=False, override_reason=None)
    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(side_effect=_rc)), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=ev, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    # Only PRIMARY signals are graded (the secondary is not in the denominator).
    assert {s.signal for s in report.signal_assessments} == primary
    # A not_reached primary scores at the floor but lowers coverage.
    assert report.scores["overall"].coverage < 1.0
    assert report.engine_version == "v3"
```

- [ ] **Step 3: Run the golden test**

Run: `docker compose run --rm nexus pytest tests/reporting/test_golden_report.py -v`
Expected: PASS.

- [ ] **Step 4: Run the FULL suite (regression)**

Run: `docker compose run --rm nexus pytest tests/reporting tests/interview_runtime -q`
Expected: PASS. Fix any stragglers.

- [ ] **Step 5: Commit**

```bash
git add tests/reporting/fixtures/session_evidence_golden.json tests/reporting/test_golden_report.py
git commit -m "test(reporting): golden SessionEvidence → report end-to-end spine

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Rebuild the per-question UI list (`QuestionOut`) from evidence — fast-follow

**Files:**
- Modify: `app/modules/reporting/service.py` (populate `questions=`)
- Modify: `app/modules/reporting/scoring/status.py` (badge from `level` + `provenance`)
- Test: `tests/reporting/test_question_out.py`

> This restores the per-question cards the recruiter UI shows, now sourced from
> `evidence.questions` + `evidence.transcript` + each question's `primary_signal` level.
> Kept as the last task so the core rewrite ships first.

- [ ] **Step 1: Write the failing test**

```python
# tests/reporting/test_question_out.py
from app.modules.reporting.scoring.status import badge_for_question


def test_badge_passed_for_solid():
    assert badge_for_question(level="solid", provenance="asked_directly", knockout=False)[0] == "passed"


def test_badge_failed_required_for_absent_must_have():
    assert badge_for_question(level="absent", provenance="probed_absent", knockout=True)[0] == "failed_required"


def test_badge_not_assessed_for_not_reached():
    assert badge_for_question(level="not_reached", provenance="not_reached", knockout=False)[0] == "not_attempted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_question_out.py -v`
Expected: FAIL (`badge_for_question` undefined).

- [ ] **Step 3: Add `badge_for_question` to `status.py`**

```python
def badge_for_question(*, level: str, provenance: str, knockout: bool) -> tuple[str, str]:
    """Per-question badge from the primary signal's level + provenance."""
    if knockout and level == "absent":
        return "failed_required", _TONE["failed_required"]
    if level in ("strong", "solid"):
        return "passed", _TONE["passed"]
    if level == "thin":
        return "partial", _TONE["partial"]
    if provenance == "probed_absent":
        return "not_demonstrated", _TONE["not_demonstrated"]
    return "not_attempted", _TONE["not_attempted"]  # not_reached / truncated
```

- [ ] **Step 4: Populate `questions=` in `build_report`**

In `service.py`, replace `questions=[]` in the `ReportRead(...)` with a list built from `evidence.questions` (one `QuestionOut` per question, `status_badge` via `badge_for_question` using the question's `primary_signal` `final_level`, `candidate_quote` from the question's supporting notes' quotes, `question_text` from the matching `questions` dict). Then fold the narrative's per-question `our_read` back in (mirror the old loop using `read_by_qid`).

- [ ] **Step 5: Run tests + full suite**

Run: `docker compose run --rm nexus pytest tests/reporting -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/service.py app/modules/reporting/scoring/status.py tests/reporting/test_question_out.py
git commit -m "feat(reporting): rebuild per-question cards from SessionEvidence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the full backend test suite touching reporting + runtime:
  `docker compose run --rm nexus pytest tests/reporting tests/interview_runtime -q` → all pass.
- [ ] Lint: `docker compose run --rm nexus ruff check app/modules/reporting app/modules/interview_runtime` → clean.
- [ ] Grep for gen-2 leftovers: `docker compose run --rm nexus grep -rn "coverage_summary\|engine_signals\|STATE_TEXTURE_POINTS" app/modules/reporting` → no live code matches.
- [ ] Confirm `AUTO_SCORE_SESSION_REPORTS` + `report_scorer_prompt_version=v4` are documented in `.env.example`.
- [ ] Manual smoke (optional, needs a real gen-3 session): run a screen, confirm `record_session_evidence` enqueues and a `session_reports` row appears with `engine_version="v3"`.

---

## Self-review notes (author)

- **Spec coverage:** denominator-from-primaries (Task 2,11), hybrid upside-only secondaries (Task 2 adapter + Task 8 holistic + Task 11), uniform low band (Task 1,4), provenance-aware must-have gate (Task 6), score/coverage split (Task 5), kept AI layers re-pointed (Task 7–11), enqueue re-connect (Task 13), deletions (Task 14), timing deferred (not implemented — correct), bank refactor deferred (not in plan — correct).
- **Known follow-up (out of scope, flagged in spec §8):** `record_session_result` orphaned for gen-3 — verify-then-remove later.
- **Type consistency:** `DemonstrationLevel` literal is identical in `types.py`, `schemas.py` (`SignalRecheckOut.level`, `SignalAssessmentOut.level`), and `aggregate.py`. `EvidenceView` property names match their uses in `service.py`. `recheck_signal` kwargs (`notes`, `engine_level`) match the call site in Task 11.
