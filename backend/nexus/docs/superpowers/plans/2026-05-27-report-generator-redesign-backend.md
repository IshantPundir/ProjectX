# Report Generator Redesign — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the post-session report scorer so the candidate's scores are a deterministic, reproducible function of the engine's own coverage map (refined per-signal by a strong LLM), and the report's prose is written by an LLM that is handed those numbers as fixed ground truth — producing complete, accurate, auditable reports shaped like `tmp/Ishant_Interview_Report_{2,3}.pdf`.

**Architecture:** Three strictly-separated layers. (1) **Deterministic core** (`scoring/aggregate.py`, pure math) turns a per-signal state map into dimension/overall scores, a knockout gate, and a verdict. (2) **Hybrid re-check** (`scoring/recheck.py`, LLM) re-validates every *reached* signal's state with full thread context and records overrides. (3) **Narrative layer** (`scoring/narrative.py`, LLM) writes prose only, never a number. The engine's persisted `coverage_summary` + audit envelope are the input; the offline per-answer BARS re-grade is retired.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Dramatiq, Pydantic v2, OpenAI via `app/ai/` (`get_raw_openai_client()` Responses API), `structlog`, pytest.

**Scope:** Backend only. The frontend rendering (new components, `report-format.ts`, deleting `SignalScorecards`) is a separate follow-on plan (Plan B) that consumes the schema produced here. Spec: `docs/superpowers/specs/2026-05-27-report-generator-redesign-design.md`.

**Reference fixtures:** sessions `c7173674-7795-4268-b4ab-829ad45b801b` (Borderline) and `bc7ba6d3-848b-49f7-8311-0aa01cb8b4aa` (Not Recommended, knockout_close). Job `ce6dad9a-8903-4396-8f29-8e36da9bd2a3`.

**Run tests with:** `docker compose run --rm nexus pytest tests/reporting -q` (the stack is already up via `docker compose up`).

---

## File map (what changes and why)

**Create**
- `tests/reporting/fixtures/envelope_c7173674.json`, `envelope_bc7ba6d3.json` — real audit envelopes (copied) for integration-grade scorer tests.
- `tests/reporting/fixtures/reference_inputs.py` — loads the two envelopes + a hand-written `questions`/`signal_metadata` slice for the reference job, shared across tests.
- `app/modules/reporting/scoring/engine_signals.py` — parse the engine's outputs: coverage map → states, `knockout_close` detection, per-signal evidence collection.
- `app/modules/reporting/scoring/status.py` — deterministic per-question status badge.
- `app/modules/reporting/scoring/recheck.py` — Layer 2 per-signal LLM re-check.
- `app/modules/reporting/scoring/narrative.py` — Layer 3 prose-only LLM.
- `prompts/v3/report_scorer/signal_recheck.txt`, `prompts/v3/report_scorer/narrative.txt`.

**Modify**
- `app/modules/reporting/scoring/types.py` — new state/grade vocab.
- `app/modules/reporting/scoring/constants.py` — new points map, thresholds, tier bands.
- `app/modules/reporting/scoring/aggregate.py` — rework around the new state vocab + `knockout_close`.
- `app/modules/reporting/schemas.py` — new `ReportRead` shape (PDF sections).
- `app/modules/reporting/service.py` — `build_report` rewritten to orchestrate the three layers.
- `app/modules/reporting/actors.py` — resolve the envelope by `session_id` against config dir; pass `coverage_summary`.
- `app/modules/reporting/router.py` — `_row_to_read` mapped to the new shape.
- `docker-compose.yml` — add the `./engine-events:/tmp/engine-events` mount to `nexus-worker`.
- `app/config.py` / `app/ai/config.py` — add `report_narrative_*` knobs (reuse `report_scorer_*` for the re-check).

**Retire (delete or stop calling)**
- `scoring/judge.py::grade_answer` / `grade_answer_consistent` and `prompts/v3/report_scorer/system.txt` (the per-answer BARS path). Keep `grade_communication` + `communication.txt`. Keep `grounding.py`. `scoring/opportunity.py` + `scoring/transcript.py::segment` stay (segment is reused for per-question assembly); `opportunity.py` is no longer used by scoring and is deleted in Task 8.

---

## Task 1: Fix the envelope-path bug (unblocks everything)

**Files:**
- Modify: `app/modules/reporting/actors.py:226-239`
- Modify: `docker-compose.yml` (the `nexus-worker` service `volumes:`)
- Test: `tests/reporting/test_actor.py`

The report actor currently trusts the stored absolute `audit_envelope_ref` (`/tmp/engine-events/<id>.json`), which doesn't exist inside the worker container. Resolve by `session_id` against the worker's own configured dir, with the stored ref as fallback.

- [ ] **Step 1: Write the failing test**

Add to `tests/reporting/test_actor.py`:

```python
import json
from pathlib import Path
from app.modules.reporting.actors import _resolve_envelope


def test_resolve_envelope_prefers_config_dir(tmp_path, monkeypatch):
    sid = "c7173674-7795-4268-b4ab-829ad45b801b"
    (tmp_path / f"{sid}.json").write_text(json.dumps({"events": [{"kind": "x"}]}))
    monkeypatch.setattr("app.modules.reporting.actors.settings.engine_event_log_dir", str(tmp_path))
    env = _resolve_envelope(session_id=sid, stored_ref="/tmp/engine-events/does-not-exist.json")
    assert env["events"] == [{"kind": "x"}]


def test_resolve_envelope_falls_back_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.modules.reporting.actors.settings.engine_event_log_dir", str(tmp_path))
    env = _resolve_envelope(session_id="nope", stored_ref=None)
    assert env == {"events": []}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_actor.py -k resolve_envelope -q`
Expected: FAIL — `_resolve_envelope` does not exist (ImportError).

- [ ] **Step 3: Implement `_resolve_envelope` and use it in the actor**

In `app/modules/reporting/actors.py`, add the import near the top:

```python
from app.config import settings
```

Add this helper above `_score_session_report_async`:

```python
def _resolve_envelope(*, session_id: str, stored_ref: str | None) -> dict:
    """Load the audit envelope by session_id from the worker's own configured
    event-log dir; fall back to the stored ref; degrade to empty events.

    The engine stores an absolute container path (e.g. /tmp/engine-events/<id>.json)
    that is NOT mounted in the worker. Resolving by session_id against this
    process's engine_event_log_dir makes the path portable across services.
    """
    candidates: list[Path] = []
    if settings.engine_event_log_dir:
        candidates.append(Path(settings.engine_event_log_dir) / f"{session_id}.json")
    if stored_ref:
        candidates.append(Path(stored_ref))
        candidates.append(Path(settings.engine_event_log_dir or "") / Path(stored_ref).name)
    for path in candidates:
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    return {"events": []}
```

Replace the existing envelope-loading block (`actors.py:226-239`, the `audit_envelope_ref` / `try Path(...).read_text()` block) with:

```python
        raw_result: dict = sess.raw_result_json or {}
        envelope: dict = await asyncio.to_thread(
            _resolve_envelope,
            session_id=str(session_id),
            stored_ref=raw_result.get("audit_envelope_ref"),
        )
        if not envelope.get("events"):
            log.warning("reporting.actor.envelope_empty",
                        audit_envelope_ref=raw_result.get("audit_envelope_ref"))
        coverage_summary: dict = raw_result.get("coverage_summary") or {}
```

- [ ] **Step 4: Pass `coverage_summary` into `build_report`**

In the `build_report(...)` call in `actors.py` (currently `actors.py:252-258`), add the new kwarg:

```python
            report = await build_report(
                transcript=transcript,
                envelope=envelope,
                coverage_summary=coverage_summary,
                questions=questions,
                signal_metadata=signal_metadata,
                correlation_id=correlation_id,
            )
```

(`build_report` gains the `coverage_summary` parameter in Task 7; until then this call site is ahead of the signature — that's fine, the actor isn't imported by the unit tests for Tasks 2–6.)

- [ ] **Step 5: Add the worker mount**

In `docker-compose.yml`, under the `nexus-worker` service `volumes:` list, add the same mount the engine service has:

```yaml
      - ./engine-events:/tmp/engine-events
```

- [ ] **Step 6: Run the resolver tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_actor.py -k resolve_envelope -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add app/modules/reporting/actors.py docker-compose.yml tests/reporting/test_actor.py
git commit -m "fix(reporting): resolve audit envelope by session_id; mount engine-events in worker"
```

---

## Task 2: New scoring state/grade vocabulary + constants

**Files:**
- Modify: `app/modules/reporting/scoring/types.py`
- Modify: `app/modules/reporting/scoring/constants.py`
- Test: `tests/reporting/test_constants_types.py`

Replace the BARS-era vocab with the engine's coverage vocab plus the LLM-only `exceeded` headroom state.

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/reporting/test_constants_types.py` with:

```python
from app.modules.reporting.scoring.constants import (
    STATE_POINTS, ADVANCE_THRESHOLD, REJECT_THRESHOLD,
    MIN_COVERAGE_FOR_ADVANCE, TECHNICAL_TYPES, BEHAVIORAL_TYPES, tier_label,
)


def test_state_points():
    assert STATE_POINTS == {
        "exceeded": 100, "sufficient": 70, "partial": 30, "failed": 0, "none": None,
    }


def test_thresholds():
    assert ADVANCE_THRESHOLD == 65
    assert REJECT_THRESHOLD == 40
    assert MIN_COVERAGE_FOR_ADVANCE == 0.6


def test_tier_label_bands():
    assert tier_label(80) == "Strong"
    assert tier_label(60) == "Meets Bar"
    assert tier_label(50) == "Below Bar"
    assert tier_label(30) == "Well Below Bar"
    assert tier_label(None) == "Not Assessed"


def test_type_sets():
    assert TECHNICAL_TYPES == frozenset({"competency", "experience", "credential"})
    assert BEHAVIORAL_TYPES == frozenset({"behavioral"})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_constants_types.py -q`
Expected: FAIL — `STATE_POINTS` / `tier_label` not importable.

- [ ] **Step 3: Rewrite `types.py`**

Replace `app/modules/reporting/scoring/types.py` with:

```python
"""Frozen value objects + Literals shared across the scoring pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Engine coverage states + the LLM-only `exceeded` headroom state.
CovState = Literal["exceeded", "sufficient", "partial", "failed", "none"]
GradeTexture = Literal["concrete", "thin", "null"]
Verdict = Literal["advance", "borderline", "reject"]
Confidence = Literal["high", "medium", "low"]
CommLevel = Literal["weak", "adequate", "strong"]
StatusBadge = Literal[
    "passed", "partial", "failed_required",
    "not_demonstrated", "not_attempted", "not_fully_assessed",
]


@dataclass(frozen=True)
class SignalDef:
    value: str
    type: str            # experience | competency | behavioral | credential
    weight: int          # 1..3
    knockout: bool
    priority: str        # required | preferred


@dataclass(frozen=True)
class SignalTurn:
    """One turn that touched a signal (from the audit envelope)."""
    candidate_quote: str
    grade: str | None            # concrete | thin | null
    reasoning: str
    question_id: str | None


@dataclass(frozen=True)
class Evidence:
    quote: str
    question_id: str | None = None
```

- [ ] **Step 4: Rewrite `constants.py`**

Replace `app/modules/reporting/scoring/constants.py` with:

```python
"""Scoring constants. All policy numbers live here (calibration-tunable).

Calibrated against the two reference sessions: session 1 (2 sufficient / 6
partial-thin technical) → Technical 4.1/10 (PDF showed 4.2); an all-sufficient
candidate (70) lands as a confident pass.
"""
from __future__ import annotations

# state -> 0..100 points (None = excluded from the denominator, a coverage gap).
STATE_POINTS: dict[str, int | None] = {
    "exceeded": 100, "sufficient": 70, "partial": 30, "failed": 0, "none": None,
}

ADVANCE_THRESHOLD = 65           # Overall >= → advance (when not knockout-capped)
REJECT_THRESHOLD = 40            # Overall <  → reject
MIN_COVERAGE_FOR_ADVANCE = 0.6   # below this, a high Overall is forced to borderline

TECHNICAL_TYPES = frozenset({"competency", "experience", "credential"})
BEHAVIORAL_TYPES = frozenset({"behavioral"})

# 0-100 score -> tier label (display bands; tunable). Descending order.
_TIER_BANDS: list[tuple[int, str]] = [
    (70, "Strong"),
    (55, "Meets Bar"),
    (40, "Below Bar"),
    (0, "Well Below Bar"),
]


def tier_label(score: int | None) -> str:
    if score is None:
        return "Not Assessed"
    for floor, label in _TIER_BANDS:
        if score >= floor:
            return label
    return "Well Below Bar"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_constants_types.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/scoring/types.py app/modules/reporting/scoring/constants.py tests/reporting/test_constants_types.py
git commit -m "feat(reporting): coverage-state scoring vocab + calibrated constants"
```

---

## Task 3: Engine-signal parsing (coverage map, knockout_close, per-signal evidence)

**Files:**
- Create: `app/modules/reporting/scoring/engine_signals.py`
- Test: `tests/reporting/test_engine_signals.py`

Pure functions that read the engine's persisted `coverage_summary` and audit envelope.

- [ ] **Step 1: Write the failing test**

Create `tests/reporting/test_engine_signals.py`:

```python
from app.modules.reporting.scoring.engine_signals import (
    build_engine_states, detect_knockout_close, collect_signal_evidence,
)
from app.modules.reporting.scoring.types import SignalDef

SIGS = [
    SignalDef("A", "competency", 3, knockout=False, priority="required"),
    SignalDef("B", "experience", 3, knockout=True, priority="required"),
]


def test_build_engine_states_defaults_unknown_to_none():
    states = build_engine_states({"A": "partial"}, SIGS)
    assert states == {"A": "partial", "B": "none"}


def test_build_engine_states_ignores_signals_not_in_metadata():
    states = build_engine_states({"A": "sufficient", "ZZ": "failed"}, SIGS)
    assert "ZZ" not in states


def test_detect_knockout_close_returns_trigger():
    env = {"events": [
        {"kind": "turn.decision", "payload": {
            "move": "advance", "attributed_signals": ["B"],
            "coverage_delta": {"B": "failed"}, "candidate_quote": "never did it"}},
        {"kind": "turn.decision", "payload": {
            "move": "knockout_close", "attributed_signals": [],
            "coverage_delta": {}, "candidate_quote": "hello?"}},
    ]}
    ko = detect_knockout_close(env)
    assert ko is not None
    assert ko.signal == "B"      # most-recent failed signal before the close
    assert ko.reason


def test_detect_knockout_close_none_when_no_close():
    env = {"events": [{"kind": "turn.decision", "payload": {"move": "advance"}}]}
    assert detect_knockout_close(env) is None


def test_collect_signal_evidence_gathers_touching_turns():
    env = {"events": [
        {"kind": "turn.decision", "payload": {
            "attributed_signals": ["A"], "coverage_delta": {"A": "partial"},
            "candidate_quote": "q1", "grade": "thin", "reasoning": "r1",
            "active_question_id": "qid1"}},
        {"kind": "turn.decision", "payload": {
            "attributed_signals": [], "coverage_delta": {"B": "sufficient"},
            "candidate_quote": "q2", "grade": "concrete", "reasoning": "r2",
            "active_question_id": "qid2"}},
    ]}
    ev_a = collect_signal_evidence(env, "A")
    assert [t.candidate_quote for t in ev_a] == ["q1"]
    ev_b = collect_signal_evidence(env, "B")          # via coverage_delta key
    assert [t.candidate_quote for t in ev_b] == ["q2"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_engine_signals.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `engine_signals.py`**

Create `app/modules/reporting/scoring/engine_signals.py`:

```python
"""Parse the interview engine's persisted outputs for the report scorer (pure).

Inputs:
- coverage_summary: dict[signal -> sufficient|partial|failed|none] (sessions.raw_result_json)
- audit envelope: {"events": [...]} with turn.decision / directive.delivered / triage events
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.reporting.scoring.types import CovState, SignalDef, SignalTurn

_VALID_STATES: frozenset[str] = frozenset({"exceeded", "sufficient", "partial", "failed", "none"})


def build_engine_states(
    coverage_summary: dict[str, str], signals: list[SignalDef]
) -> dict[str, CovState]:
    """Project the engine coverage map onto the role's signals; default `none`."""
    states: dict[str, CovState] = {}
    for sig in signals:
        raw = coverage_summary.get(sig.value, "none")
        states[sig.value] = raw if raw in _VALID_STATES else "none"  # type: ignore[assignment]
    return states


@dataclass(frozen=True)
class KnockoutClose:
    signal: str | None       # the must-have the candidate failed/disclaimed
    quote: str
    reason: str


def detect_knockout_close(envelope: dict[str, Any]) -> KnockoutClose | None:
    """Return a KnockoutClose if the engine ended on a knockout, else None.

    Trigger: a turn.decision with move == 'knockout_close'. The triggering signal
    is the most-recent signal marked `failed` in a turn.decision at/ before that
    close (its own attributed_signals first, else the last failed coverage_delta).
    """
    events: list[dict] = envelope.get("events") or []
    last_failed: str | None = None
    for e in events:
        if e.get("kind") != "turn.decision":
            continue
        p = e.get("payload") or {}
        for sig, st in (p.get("coverage_delta") or {}).items():
            if st == "failed":
                last_failed = sig
        if p.get("move") == "knockout_close":
            trigger = None
            attributed = p.get("attributed_signals") or []
            if attributed:
                trigger = attributed[0]
            trigger = trigger or last_failed
            quote = (p.get("candidate_quote") or "").strip()
            reason = (
                f"Interview closed on a must-have gap: '{trigger}'."
                if trigger else "Interview closed early on a knockout."
            )
            return KnockoutClose(signal=trigger, quote=quote, reason=reason)
    return None


def collect_signal_evidence(envelope: dict[str, Any], signal: str) -> list[SignalTurn]:
    """Every turn.decision that attributed evidence to `signal`, in order."""
    out: list[SignalTurn] = []
    for e in envelope.get("events") or []:
        if e.get("kind") != "turn.decision":
            continue
        p = e.get("payload") or {}
        touches = signal in (p.get("attributed_signals") or []) or signal in (
            p.get("coverage_delta") or {}
        )
        if not touches:
            continue
        out.append(SignalTurn(
            candidate_quote=(p.get("candidate_quote") or "").strip(),
            grade=p.get("grade"),
            reasoning=p.get("reasoning") or "",
            question_id=p.get("active_question_id"),
        ))
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_engine_signals.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/engine_signals.py tests/reporting/test_engine_signals.py
git commit -m "feat(reporting): parse engine coverage map + knockout_close + per-signal evidence"
```

---

## Task 4: Deterministic core rework (`aggregate.py`)

**Files:**
- Modify: `app/modules/reporting/scoring/aggregate.py`
- Test: `tests/reporting/test_aggregate.py` (rewrite)

Score per the new state vocab; verdict honors `knockout_close` and the knockout-flag gate.

- [ ] **Step 1: Write the failing test (rewrite the file)**

Replace `tests/reporting/test_aggregate.py` with:

```python
from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_state, score_dimension, score_overall,
    knockout_status, resolve_verdict, KnockoutResult,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose


def ss(t, w, state, *, knockout=False, priority="required"):
    return ScoredSignal(value=f"{t}-{w}-{state}", type=t, weight=w,
                        knockout=knockout, priority=priority, state=state,
                        score=score_state(state))


def test_score_state_mapping():
    assert score_state("exceeded") == 100
    assert score_state("sufficient") == 70
    assert score_state("partial") == 30
    assert score_state("failed") == 0
    assert score_state("none") is None


def test_dimension_excludes_none():
    dim = score_dimension("technical",
                          [ss("competency", 3, "sufficient"), ss("competency", 1, "partial"),
                           ss("competency", 2, "none")],
                          {"competency", "experience", "credential"})
    # (3*70 + 1*30)/(3+1) = 60 ; coverage (3+1)/(3+1+2)=0.667
    assert dim.score == 60
    assert round(dim.coverage, 3) == 0.667
    assert dim.confidence == "medium"


def test_reference_session1_technical_lands_at_41():
    sigs = (
        [ss("experience", 3, "sufficient"), ss("experience", 3, "sufficient")]
        + [ss("competency", 3, "partial")] * 3
        + [ss("competency", 2, "partial")] * 3
    )
    dim = score_dimension("technical", sigs, {"competency", "experience", "credential"})
    assert dim.score == 41          # (420+450)/21 → 41.4 → 41 ; matches PDF 4.2


def test_overall_excludes_unassessed_and_communication():
    score, cov = score_overall([ss("competency", 3, "sufficient"),
                                ss("behavioral", 1, "partial")])
    assert score == 60 and round(cov, 2) == 1.0


def test_knockout_status():
    assert knockout_status(state="failed") == "failed"
    assert knockout_status(state="sufficient") == "passed"
    assert knockout_status(state="exceeded") == "passed"
    assert knockout_status(state="partial") == "failed"     # partial must-have = not met
    assert knockout_status(state="none") == "insufficient"


def test_verdict_knockout_close_is_reject():
    v = resolve_verdict(overall=90, coverage=0.9, knockouts=[],
                        knockout_close=KnockoutClose(signal="API", quote="never", reason="x"))
    assert v.verdict == "reject" and "API" in v.reason


def test_verdict_reject_on_failed_knockout_flag():
    v = resolve_verdict(overall=90, coverage=0.9, knockout_close=None,
                        knockouts=[KnockoutResult(signal="prog", status="failed", reason="x")])
    assert v.verdict == "reject"


def test_verdict_borderline_on_unconfirmed_knockout():
    v = resolve_verdict(overall=90, coverage=0.9, knockout_close=None,
                        knockouts=[KnockoutResult(signal="prog", status="insufficient", reason="x")])
    assert v.verdict == "borderline"


def test_verdict_advance_when_clear():
    assert resolve_verdict(overall=70, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "advance"


def test_verdict_borderline_on_low_coverage():
    assert resolve_verdict(overall=90, coverage=0.4, knockout_close=None,
                           knockouts=[]).verdict == "borderline"


def test_verdict_reject_on_low_overall():
    assert resolve_verdict(overall=35, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "reject"


def test_verdict_borderline_middle():
    assert resolve_verdict(overall=50, coverage=0.9, knockout_close=None,
                           knockouts=[]).verdict == "borderline"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: FAIL — new signatures not present.

- [ ] **Step 3: Rewrite `aggregate.py`**

Replace `app/modules/reporting/scoring/aggregate.py` with:

```python
"""Deterministic, pure scoring math: signal-state → dimension → knockout gate →
overall → verdict. No LLM, no IO. This is the auditable core; same logs → same number."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD, MIN_COVERAGE_FOR_ADVANCE, REJECT_THRESHOLD, STATE_POINTS,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose
from app.modules.reporting.scoring.types import Confidence, CovState, KnockoutStatus, Verdict

KnockoutStatusT = KnockoutStatus  # re-export alias for callers


def score_state(state: CovState) -> int | None:
    return STATE_POINTS[state]


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    state: CovState
    score: int | None


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float       # assessed weight / total weight in this dimension
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
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w  # type: ignore[operator]
    coverage = (assessed_w / total_w) if total_w else 0.0
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=coverage, confidence=_confidence(coverage))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Overall = weighted mean over ALL assessed JD signals (tech + behavioral).
    Communication is scored separately and is NOT included here."""
    total_w = sum(s.weight for s in signals)
    assessed = [s for s in signals if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w  # type: ignore[operator]
    return int(round(weighted)), (assessed_w / total_w if total_w else 0.0)


def knockout_status(*, state: CovState) -> KnockoutStatus:
    if state == "none":
        return "insufficient"
    if state in ("failed", "partial"):     # a partially-shown must-have is not met
        return "failed"
    return "passed"                        # sufficient | exceeded


@dataclass(frozen=True)
class KnockoutResult:
    signal: str
    status: KnockoutStatus
    reason: str


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


def resolve_verdict(
    *, overall: int | None, coverage: float,
    knockouts: list[KnockoutResult], knockout_close: KnockoutClose | None,
) -> VerdictResult:
    if knockout_close is not None:
        sig = knockout_close.signal or "a must-have skill"
        return VerdictResult("reject", f"Interview closed on a must-have gap: {sig}")
    failed = [k for k in knockouts if k.status == "failed"]
    if failed:
        return VerdictResult("reject", f"failed must-have: {failed[0].signal}")
    insufficient = [k for k in knockouts if k.status == "insufficient"]
    if insufficient:
        return VerdictResult("borderline", f"couldn't confirm must-have: {insufficient[0].signal}")
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

- [ ] **Step 4: Add `KnockoutStatus` to `types.py`**

In `app/modules/reporting/scoring/types.py`, add to the Literal block:

```python
KnockoutStatus = Literal["passed", "failed", "insufficient"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_aggregate.py -q`
Expected: PASS (13 passed).

- [ ] **Step 6: Commit**

```bash
git add app/modules/reporting/scoring/aggregate.py app/modules/reporting/scoring/types.py tests/reporting/test_aggregate.py
git commit -m "feat(reporting): deterministic core over coverage states + knockout_close gate"
```

---

## Task 5: Per-question status badge (`status.py`)

**Files:**
- Create: `app/modules/reporting/scoring/status.py`
- Test: `tests/reporting/test_status.py`

`segment()` (kept) already yields a `ScoredUnit` per delivered question. This derives its badge from the unit + the final signal states + whether the interview closed before completion.

- [ ] **Step 1: Write the failing test**

Create `tests/reporting/test_status.py`:

```python
from app.modules.reporting.scoring.status import derive_status
from app.modules.reporting.scoring.types import ScoredUnit


def unit(qid="q", kind="technical_scenario", engaged=True, wc=20):
    return ScoredUnit(question_id=qid, question_text="t", candidate_answer="a",
                      answer_start_ms=0, probes_fired=0, clarifies=0, word_count=wc,
                      candidate_engaged=engaged, question_kind=kind)


def test_passed_factual_sufficient():
    b, _ = derive_status(unit(kind="experience_check"), signal_states={"S": "sufficient"},
                         signal_defs={"S": ("experience", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "passed"


def test_failed_required_skill():
    b, _ = derive_status(unit(), signal_states={"S": "failed"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "failed_required"


def test_not_demonstrated_on_no_experience():
    b, _ = derive_status(unit(engaged=False), signal_states={"S": "none"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=True, closed_before_complete=False)
    assert b == "not_demonstrated"


def test_not_attempted_when_unengaged_no_signal():
    b, _ = derive_status(unit(engaged=False, wc=1), signal_states={"S": "none"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "not_attempted"


def test_not_fully_assessed_when_closed_early():
    b, _ = derive_status(unit(), signal_states={"S": "partial"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=True)
    assert b == "not_fully_assessed"


def test_partial_default():
    b, _ = derive_status(unit(), signal_states={"S": "partial"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "partial"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_status.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `status.py`**

Create `app/modules/reporting/scoring/status.py`:

```python
"""Deterministic per-question status badge (pure)."""
from __future__ import annotations

from app.modules.reporting.scoring.constants import STATE_POINTS  # noqa: F401 (doc anchor)
from app.modules.reporting.scoring.types import StatusBadge, ScoredUnit

_FACTUAL_KINDS = frozenset({"experience_check", "compliance_binary"})

_TONE: dict[str, str] = {
    "passed": "ok", "partial": "caution", "failed_required": "danger",
    "not_demonstrated": "danger", "not_attempted": "neutral",
    "not_fully_assessed": "neutral",
}


def derive_status(
    unit: ScoredUnit,
    *,
    signal_states: dict[str, str],                      # signal -> final CovState
    signal_defs: dict[str, tuple[str, bool, str]],      # signal -> (type, knockout, priority)
    no_experience: bool,
    closed_before_complete: bool,
) -> tuple[StatusBadge, str]:
    """Precedence: failed-required > not-demonstrated > not-attempted >
    not-fully-assessed > passed > partial."""
    states = list(signal_states.values())

    # A required/knockout signal explicitly failed → the deal-breaker question.
    for sig, st in signal_states.items():
        if st == "failed":
            _t, knockout, priority = signal_defs.get(sig, ("competency", False, "preferred"))
            if knockout or priority == "required":
                return "failed_required", _TONE["failed_required"]

    if no_experience and not any(s in ("sufficient", "exceeded", "partial") for s in states):
        return "not_demonstrated", _TONE["not_demonstrated"]

    if (not unit.candidate_engaged) and all(s == "none" for s in states):
        return "not_attempted", _TONE["not_attempted"]

    if closed_before_complete and not any(s in ("sufficient", "exceeded") for s in states):
        return "not_fully_assessed", _TONE["not_fully_assessed"]

    if any(s in ("sufficient", "exceeded") for s in states):
        # Factual gate questions read as a clean "passed".
        if unit.question_kind in _FACTUAL_KINDS:
            return "passed", _TONE["passed"]
        return "passed", _TONE["passed"]

    if any(s == "partial" for s in states):
        return "partial", _TONE["partial"]

    return "not_fully_assessed", _TONE["not_fully_assessed"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_status.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/scoring/status.py tests/reporting/test_status.py
git commit -m "feat(reporting): deterministic per-question status badge"
```

---

## Task 6: Hybrid per-signal re-check (`recheck.py` + prompt)

**Files:**
- Create: `prompts/v3/report_scorer/signal_recheck.txt`
- Create: `app/modules/reporting/scoring/recheck.py`
- Modify: `app/modules/reporting/schemas.py` (add `SignalRecheckOut`)
- Test: `tests/reporting/test_recheck.py`

LLM re-validates one signal's state with full thread context. Mirrors `judge.py`'s Responses-API + grounding pattern.

- [ ] **Step 1: Write the prompt**

Create `prompts/v3/report_scorer/signal_recheck.txt`:

```
You are an impartial hiring evaluator performing a POST-INTERVIEW re-check of ONE skill
("signal") for a candidate. A live interview engine already assessed this signal turn-by-turn
under time pressure; you now re-confirm it with the FULL set of everything the candidate said
about it across the whole interview. You produce a structured evidence record a human reviewer
will rely on. You do NOT decide hire/no-hire and you do NOT score other signals.

<role>
Re-confirm the candidate's demonstrated level on ONE signal against its rubric, using ALL the
provided turns as context. Output evidence and justification BEFORE the state. The engine's live
assessment is a PRIOR, not a constraint — change it only when the full context warrants.
</role>

<rules>
- USE ONLY the provided turns (the candidate's own words) and the rubric. Never assume competence
  not stated. Never use outside knowledge of the candidate.
- CITE verbatim quote spans from the turns for every claim. Paraphrase is not evidence.
- DO NOT reward length or confidence. A long, fluent answer with no concrete mechanism is weak.
- DO NOT penalize transcription artifacts (numbers as words like "five x x" for "5xx",
  near-homophones, phonetic spellings). Grade the apparent intent.
- The engine's grade vocabulary: concrete (specific, owned, mechanism stated) / thin (right idea,
  no depth) / null (no usable evidence).
</rules>

<states>
- exceeded   — clearly beyond the bar: deep, specific, complete, owned outcomes.
- sufficient — meets the bar for this signal.
- partial    — engaged with real but incomplete evidence; did not reach the bar.
- failed     — assessed and below the bar, or the candidate disclaimed having the skill.
</states>

<output_spec>
Return a JSON object with fields in this order:
1. evidence_quotes — verbatim spans from the turns that are load-bearing for the state.
2. justification  — 1–3 sentences mapping the evidence to the rubric anchors. Written BEFORE the state.
3. grade          — one of: concrete, thin, null.
4. state          — one of: exceeded, sufficient, partial, failed.
5. overridden     — true if your state differs from the engine's prior, else false.
6. override_reason — one sentence if overridden, else null.
</output_spec>
```

- [ ] **Step 2: Write the failing test**

Create `tests/reporting/test_recheck.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.types import SignalDef, SignalTurn


def _fake_response(out: SignalRecheckOut):
    class R:
        output_parsed = out
        usage = None
    return R()


@pytest.mark.asyncio
async def test_recheck_grounds_quotes_and_records_override():
    sig = SignalDef("API expertise", "competency", 3, knockout=False, priority="required")
    turns = [SignalTurn(candidate_quote="I have built REST connectors end to end",
                        grade="thin", reasoning="r", question_id="q1")]
    model_out = SignalRecheckOut(
        evidence_quotes=["built REST connectors end to end", "totally made up span"],
        justification="Names a concrete mechanism.", grade="concrete",
        state="sufficient", overridden=True, override_reason="Full context shows depth.")
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as gc:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=_fake_response(model_out))
        gc.return_value = client
        res = await recheck_signal(signal_def=sig, evidence_turns=turns,
                                   question_context="Q: build a connector?\nrubric: ...",
                                   engine_state="partial", correlation_id="c1")
    assert res.state == "sufficient"
    assert res.overridden is True
    # ungrounded quote dropped:
    assert "totally made up span" not in res.evidence_quotes
    assert "built REST connectors end to end" in res.evidence_quotes


@pytest.mark.asyncio
async def test_recheck_refusal_falls_back_to_engine_state():
    sig = SignalDef("X", "competency", 2, knockout=False, priority="required")
    turns = [SignalTurn(candidate_quote="q", grade="thin", reasoning="r", question_id="q1")]
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as gc:
        client = AsyncMock()
        class R:
            output_parsed = None
            output = []
            usage = None
        client.responses.parse = AsyncMock(return_value=R())
        gc.return_value = client
        res = await recheck_signal(signal_def=sig, evidence_turns=turns,
                                   question_context="ctx", engine_state="partial",
                                   correlation_id="c1")
    assert res.state == "partial"        # unchanged
    assert res.overridden is False
```

- [ ] **Step 3: Add `SignalRecheckOut` to `schemas.py`**

In `app/modules/reporting/schemas.py`, add near the top (after imports):

```python
class SignalRecheckOut(BaseModel):
    """Structured output from the per-signal post-interview re-check."""
    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str = ""
    grade: Literal["concrete", "thin", "null"] = "null"
    state: Literal["exceeded", "sufficient", "partial", "failed"]
    overridden: bool = False
    override_reason: str | None = None
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_recheck.py -q`
Expected: FAIL — `recheck.py` not found.

- [ ] **Step 5: Implement `recheck.py`**

Create `app/modules/reporting/scoring/recheck.py`:

```python
"""Layer 2 — post-interview per-signal re-check (LLM, Responses API + Structured Outputs).

Mirrors scoring/judge.py: get_raw_openai_client(), responses.parse(text_format=...),
effort-gating via dict reasoning=, evidence grounded against the candidate's turns,
graceful refusal fallback (keep the engine's prior state)."""
from __future__ import annotations

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.types import SignalDef, SignalTurn

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _render_turns(turns: list[SignalTurn]) -> str:
    lines = []
    for i, t in enumerate(turns, 1):
        g = t.grade or "null"
        lines.append(f"[turn {i} · engine grade={g}] {t.candidate_quote}")
    return "\n".join(lines) if lines else "(no turns recorded)"


async def recheck_signal(
    *, signal_def: SignalDef, evidence_turns: list[SignalTurn],
    question_context: str, engine_state: str, correlation_id: str,
) -> SignalRecheckOut:
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/signal_recheck"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<signal>\n{signal_def.value}\n(type: {signal_def.type}, "
        f"priority: {signal_def.priority}, must_have: {signal_def.knockout})\n</signal>\n\n"
        f"<question_context>\n{question_context}\n</question_context>\n\n"
        f"<engine_prior>\nstate={engine_state}\n</engine_prior>"
    )
    transcript_block = _render_turns(evidence_turns)
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<turns>\n{transcript_block}\n</turns>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": SignalRecheckOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:recheck:"
            f"{ai_config.report_scorer_prompt_version}:{signal_def.value}:{ai_config.report_scorer_model}"
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
        return SignalRecheckOut(evidence_quotes=[], justification="Model did not return a parse.",
                                grade="null", state=engine_state if engine_state != "none" else "partial",  # type: ignore[arg-type]
                                overridden=False, override_reason=None)

    grounded, _ungrounded = ground_quotes(parsed.evidence_quotes, transcript_block)
    return parsed.model_copy(update={"evidence_quotes": grounded})
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_recheck.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add prompts/v3/report_scorer/signal_recheck.txt app/modules/reporting/scoring/recheck.py app/modules/reporting/schemas.py tests/reporting/test_recheck.py
git commit -m "feat(reporting): hybrid per-signal LLM re-check with grounded overrides"
```

---

## Task 7: Narrative layer (`narrative.py` + prompt)

**Files:**
- Create: `prompts/v3/report_scorer/narrative.txt`
- Create: `app/modules/reporting/scoring/narrative.py`
- Modify: `app/modules/reporting/schemas.py` (add `NarrativeOut` + sub-models)
- Modify: `app/config.py`, `app/ai/config.py` (add `report_narrative_model` knob)
- Test: `tests/reporting/test_narrative.py`

Prose only. Handed the final numbers as fixed ground truth.

- [ ] **Step 1: Add narrative schema to `schemas.py`**

In `app/modules/reporting/schemas.py`, add:

```python
class WhyColumn(BaseModel):
    title: str
    body: str


class DecisionOut(BaseModel):
    headline: str
    why_positive: WhyColumn
    why_negative: WhyColumn


class StrengthOut(BaseModel):
    title: str
    detail: str


class ConcernOut(BaseModel):
    title: str
    detail: str
    severity: Literal["deal_breaker", "major", "moderate"]


class QuestionNarrative(BaseModel):
    question_id: str
    candidate_quote: str          # cleaned, readable; meaning preserved
    our_read: str


class MethodologyOut(BaseModel):
    note: str
    charity_flags: list[str] = Field(default_factory=list)


class NarrativeOut(BaseModel):
    """Prose-only LLM output. Contains NO scores/verdict."""
    decision: DecisionOut
    quick_summary: str
    strengths: list[StrengthOut] = Field(default_factory=list)
    concerns: list[ConcernOut] = Field(default_factory=list)
    questions: list[QuestionNarrative] = Field(default_factory=list)
    methodology: MethodologyOut
```

- [ ] **Step 2: Write the prompt**

Create `prompts/v3/report_scorer/narrative.txt`:

```
You are writing the human-readable narrative of a completed AI screening interview, for a busy
recruiter who scans hundreds of reports a day. The SCORES and the VERDICT have already been
computed deterministically and are given to you below as FIXED GROUND TRUTH. Your job is to
EXPLAIN them in clear, specific, defensible prose — never to invent, restate differently, or
contradict a number or the verdict.

<inputs>
You are given: the job title; the per-signal final states (sufficient/partial/failed/none/exceeded)
with the candidate's quotes and the engine's notes; the dimension scores, overall score, and
verdict; and the per-question record (question text, what the candidate said, status).
</inputs>

<rules>
- GROUND every claim in the candidate's own words. Cite specifics; no generic praise.
- DO NOT output any number, score, or a verdict label — those are rendered from the fixed data.
- DO NOT contradict the given verdict. The headline and summary must be consistent with it.
- BE FAIR. If a low score is driven by a single missing must-have, say so plainly and note the
  real strengths too. Surface charity flags (a long silence, a cut-off question, a possible
  audio/technical issue) as things to confirm — never use them to penalize.
- CLEAN UP transcription noise in quotes for readability while preserving meaning; never fabricate
  words the candidate did not say.
- Keep it tight: the summary is one paragraph; each strength/concern is a short title + 1–2 sentences.
</rules>

<output_spec>
Return a JSON object:
- decision.headline — 1–2 sentences: why this verdict.
- decision.why_positive — {title, body}: what the candidate has going for them.
- decision.why_negative — {title, body}: what held them back / the gap.
- quick_summary — one narrative paragraph telling the whole story.
- strengths[] — {title, detail}.
- concerns[]  — {title, detail, severity ∈ deal_breaker|major|moderate}.
- questions[] — {question_id, candidate_quote (cleaned), our_read} for each delivered question.
- methodology — {note, charity_flags[]}: how many questions were reached, the scoring basis, and
  any caveats a reviewer should confirm.
</output_spec>
```

- [ ] **Step 3: Add the config knob**

In `app/config.py`, after the `openai_report_scorer_*` block (around line 585), add:

```python
    # ``openai_report_narrative_model`` — model for the prose-only narrative layer.
    openai_report_narrative_model: str = "gpt-5.4"
    openai_report_narrative_effort: str = "low"
```

In `app/ai/config.py`, after the `report_scorer_*` properties (around line 275), add:

```python
    @property
    def report_narrative_model(self) -> str:
        return self._settings.openai_report_narrative_model

    @property
    def report_narrative_effort(self) -> str:
        return self._settings.openai_report_narrative_effort
```

- [ ] **Step 4: Write the failing test**

Create `tests/reporting/test_narrative.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.narrative import write_narrative
from app.modules.reporting.schemas import (
    NarrativeOut, DecisionOut, WhyColumn, MethodologyOut,
)


def _out():
    return NarrativeOut(
        decision=DecisionOut(headline="Borderline.",
                             why_positive=WhyColumn(title="Foundations", body="Meets experience."),
                             why_negative=WhyColumn(title="Depth", body="Thin technical answers.")),
        quick_summary="Sits on the line.",
        strengths=[], concerns=[], questions=[],
        methodology=MethodologyOut(note="7 of 8 questions.", charity_flags=[]))


@pytest.mark.asyncio
async def test_write_narrative_returns_prose():
    class R:
        output_parsed = _out()
        usage = None
    with patch("app.modules.reporting.scoring.narrative.get_raw_openai_client") as gc:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=R())
        gc.return_value = client
        res = await write_narrative(ground_truth_json="{}", correlation_id="c1")
    assert res.decision.headline == "Borderline."
    assert res.methodology.note.startswith("7 of 8")
```

- [ ] **Step 5: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_narrative.py -q`
Expected: FAIL — module not found.

- [ ] **Step 6: Implement `narrative.py`**

Create `app/modules/reporting/scoring/narrative.py`:

```python
"""Layer 3 — prose-only narrative (LLM). Handed the final numbers as fixed ground truth."""
from __future__ import annotations

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import NarrativeOut

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


async def write_narrative(*, ground_truth_json: str, correlation_id: str) -> NarrativeOut:
    """ground_truth_json: a compact JSON string with job_title, signals[], scores,
    verdict, questions[] (see service.py:_narrative_ground_truth)."""
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/narrative"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"<report_data>\n{ground_truth_json}\n</report_data>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_narrative_model,
        "input": messages,
        "text_format": NarrativeOut,
        "prompt_cache_key": (
            f"narrative:{ai_config.report_scorer_prompt_version}:{ai_config.report_narrative_model}"
        ),
    }
    if ai_config.report_narrative_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_narrative_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_narrative",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.narrative.refusal", correlation_id=correlation_id)
        from app.modules.reporting.schemas import DecisionOut, MethodologyOut, WhyColumn
        return NarrativeOut(
            decision=DecisionOut(
                headline="Report narrative unavailable — see scores and signal detail.",
                why_positive=WhyColumn(title="", body=""),
                why_negative=WhyColumn(title="", body="")),
            quick_summary="", strengths=[], concerns=[], questions=[],
            methodology=MethodologyOut(note="Narrative generation failed.", charity_flags=[]))
    return parsed
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/reporting/test_narrative.py -q`
Expected: PASS (1 passed).

- [ ] **Step 8: Commit**

```bash
git add prompts/v3/report_scorer/narrative.txt app/modules/reporting/scoring/narrative.py app/modules/reporting/schemas.py app/config.py app/ai/config.py tests/reporting/test_narrative.py
git commit -m "feat(reporting): prose-only narrative layer over fixed scores"
```

---

## Task 8: New `ReportRead` schema + `build_report` orchestration

**Files:**
- Modify: `app/modules/reporting/schemas.py` (rewrite `ReportRead` + add `ScoreOut`, `QuestionOut`, `SignalAssessmentOut`)
- Modify: `app/modules/reporting/service.py` (rewrite `build_report`)
- Delete: `app/modules/reporting/scoring/opportunity.py`, `scoring/judge.py::grade_answer`/`grade_answer_consistent` usage (keep `grade_communication`)
- Test: `tests/reporting/test_service.py` (rewrite)

- [ ] **Step 1: Add the new output schema to `schemas.py`**

Add to `app/modules/reporting/schemas.py`:

```python
class ScoreOut(BaseModel):
    score: int | None
    tier_label: str
    tone: str                      # ok | caution | danger | neutral
    confidence: Confidence
    coverage: float = 0.0


class QuestionOut(BaseModel):
    seq: int
    question_id: str
    title: str
    status_badge: str              # passed | partial | failed_required | ...
    status_tone: str
    question_text: str
    candidate_quote: str
    our_read: str = ""


class SignalAssessmentOut(BaseModel):
    signal: str
    type: str
    weight: int
    knockout: bool
    priority: str
    engine_state: str
    final_state: str
    grade: str | None = None
    score: int | None = None
    evidence: list[str] = Field(default_factory=list)
    overridden: bool = False
    override_reason: str | None = None
```

Then **replace** the `ReportRead` class with:

```python
class ReportRead(BaseModel):
    """Recruiter-facing report (PDF-shaped). Mirrors session_reports JSONB columns."""
    verdict: Verdict
    verdict_reason: str
    overall_score: int | None
    overall_coverage: float
    overall_confidence: Confidence
    decision: DecisionOut
    scores: dict[str, ScoreOut]                       # overall|technical|behavioral|communication
    quick_summary: str = ""
    strengths: list[StrengthOut] = Field(default_factory=list)
    concerns: list[ConcernOut] = Field(default_factory=list)
    questions: list[QuestionOut] = Field(default_factory=list)
    methodology: MethodologyOut
    signal_assessments: list[SignalAssessmentOut] = Field(default_factory=list)
    # metadata
    id: str | None = None
    session_id: str | None = None
    status: str = "ready"
    engine_version: str | None = None
    version: int = 1
    scoring_manifest: ScoringManifest | None = None
    human_decision: dict | None = None
    generated_at: str | None = None
```

Add `from app.modules.reporting.scoring.types import GradeTexture` if needed; ensure `Confidence`, `Verdict` import lines reference the new `types` module (`SignalState`/`BarsLevel`/`Opportunity`/`KnockoutStatus` imports that no longer exist must be removed from `schemas.py`'s top import). Remove now-dead classes: `JudgeVerdict`, `AnswerRating`, `SignalScorecard`, `DimensionScoreOut`, `KnockoutResultOut`, `QuestionScorecard`, `SummaryOut`, `EvidenceOut`. Keep `CommunicationVerdict`, `ScoringManifest`, `HumanDecisionIn`, `ReportIndexItem`, `ReportIndexPage`.

- [ ] **Step 2: Write the failing service test (rewrite the file)**

Replace `tests/reporting/test_service.py` with a build_report integration test that mocks the two LLM layers:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    NarrativeOut, DecisionOut, WhyColumn, MethodologyOut, SignalRecheckOut, CommunicationVerdict,
)


def _signal_metadata():
    return [
        {"value": "4+ years total professional experience", "type": "experience",
         "weight": 3, "knockout": True, "priority": "required"},
        {"value": "Designing and implementing AI-driven workflows", "type": "competency",
         "weight": 3, "knockout": False, "priority": "required"},
    ]


def _questions():
    return [
        {"id": "q1", "position": 0, "text": "Years of experience?",
         "signal_values": ["4+ years total professional experience"], "estimated_minutes": 1.0,
         "is_mandatory": True, "follow_ups": [], "positive_evidence": [], "red_flags": [],
         "rubric": {"excellent": "", "meets_bar": "", "below_bar": ""},
         "evaluation_hint": "", "question_kind": "experience_check", "difficulty": "easy",
         "primary_signal": "4+ years total professional experience"},
        {"id": "q2", "position": 1, "text": "Design an AI workflow.",
         "signal_values": ["Designing and implementing AI-driven workflows"],
         "estimated_minutes": 3.0, "is_mandatory": True, "follow_ups": [],
         "positive_evidence": [], "red_flags": [], "rubric": {"excellent": "", "meets_bar": "",
         "below_bar": ""}, "evaluation_hint": "", "question_kind": "technical_scenario",
         "difficulty": "medium", "primary_signal": "Designing and implementing AI-driven workflows"},
    ]


def _envelope():
    return {"events": [
        {"kind": "directive.delivered", "t_ms": 1000, "payload": {"act": "ASK", "turn_ref": "t1"}},
        {"kind": "turn.decision", "t_ms": 2000, "payload": {
            "turn_ref": "t1", "active_question_id": "q1", "candidate_quote": "About six years.",
            "attributed_signals": ["4+ years total professional experience"], "grade": "concrete",
            "coverage_delta": {"4+ years total professional experience": "sufficient"},
            "move": "advance"}},
        {"kind": "directive.delivered", "t_ms": 3000, "payload": {"act": "ACK_ADVANCE", "turn_ref": "t2"}},
        {"kind": "turn.decision", "t_ms": 4000, "payload": {
            "turn_ref": "t2", "active_question_id": "q2",
            "candidate_quote": "A recipe triggered on a ticket, an extraction layer...",
            "attributed_signals": ["Designing and implementing AI-driven workflows"],
            "grade": "thin", "coverage_delta": {"Designing and implementing AI-driven workflows": "partial"},
            "move": "probe"}},
    ]}


@pytest.mark.asyncio
async def test_build_report_uses_engine_map_and_is_complete():
    coverage = {"4+ years total professional experience": "sufficient",
                "Designing and implementing AI-driven workflows": "partial"}

    async def fake_recheck(*, signal_def, engine_state, **kw):
        return SignalRecheckOut(evidence_quotes=[], justification="", grade="thin",
                                state=engine_state, overridden=False, override_reason=None)

    narrative = NarrativeOut(
        decision=DecisionOut(headline="Borderline.",
                             why_positive=WhyColumn(title="A", body="b"),
                             why_negative=WhyColumn(title="C", body="d")),
        quick_summary="s", strengths=[], concerns=[],
        questions=[], methodology=MethodologyOut(note="n", charity_flags=[]))

    with patch("app.modules.reporting.service.recheck_signal", side_effect=fake_recheck), \
         patch("app.modules.reporting.service.write_narrative", AsyncMock(return_value=narrative)), \
         patch("app.modules.reporting.service.grade_communication",
               AsyncMock(return_value=CommunicationVerdict(evidence_quotes=[], justification="",
                                                           level="adequate"))):
        report = await build_report(
            transcript=[{"role": "candidate", "text": "About six years."}],
            envelope=_envelope(), coverage_summary=coverage,
            questions=_questions(), signal_metadata=_signal_metadata(), correlation_id="c1")

    assert report.scores["overall"].score is not None      # NOT incomplete
    assert report.scores["technical"].score is not None
    assert report.verdict == "borderline"                  # knockout 4+yrs sufficient, AI partial
    assert len(report.questions) == 2
    assert report.scores["communication"].score == 70      # adequate
    assert any(sa.signal == "4+ years total professional experience"
               for sa in report.signal_assessments)


@pytest.mark.asyncio
async def test_build_report_knockout_close_is_reject():
    env = _envelope()
    env["events"].append({"kind": "turn.decision", "t_ms": 5000, "payload": {
        "turn_ref": "t3", "move": "knockout_close", "attributed_signals": [],
        "coverage_delta": {"Designing and implementing AI-driven workflows": "failed"},
        "candidate_quote": "I've never done that."}})
    coverage = {"4+ years total professional experience": "sufficient",
                "Designing and implementing AI-driven workflows": "failed"}

    async def fake_recheck(*, signal_def, engine_state, **kw):
        return SignalRecheckOut(evidence_quotes=[], justification="", grade="null",
                                state=engine_state, overridden=False, override_reason=None)

    narrative = NarrativeOut(
        decision=DecisionOut(headline="Not recommended.",
                             why_positive=WhyColumn(title="", body=""),
                             why_negative=WhyColumn(title="", body="")),
        quick_summary="", strengths=[], concerns=[], questions=[],
        methodology=MethodologyOut(note="", charity_flags=[]))

    with patch("app.modules.reporting.service.recheck_signal", side_effect=fake_recheck), \
         patch("app.modules.reporting.service.write_narrative", AsyncMock(return_value=narrative)), \
         patch("app.modules.reporting.service.grade_communication",
               AsyncMock(return_value=CommunicationVerdict(evidence_quotes=[], justification="",
                                                           level="weak"))):
        report = await build_report(
            transcript=[], envelope=env, coverage_summary=coverage,
            questions=_questions(), signal_metadata=_signal_metadata(), correlation_id="c1")

    assert report.verdict == "reject"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_service.py -q`
Expected: FAIL — `build_report` has the old signature / imports.

- [ ] **Step 4: Rewrite `build_report` in `service.py`**

Replace `app/modules/reporting/service.py`'s imports and `build_report` (keep `persist_report` unchanged except the value dict — see Step 5). The new `build_report`:

```python
import asyncio
import json
from app.modules.reporting.scoring.aggregate import (
    ScoredSignal, score_state, score_dimension, score_overall,
    knockout_status, resolve_verdict, KnockoutResult,
)
from app.modules.reporting.scoring.constants import TECHNICAL_TYPES, BEHAVIORAL_TYPES, tier_label
from app.modules.reporting.scoring.engine_signals import (
    build_engine_states, detect_knockout_close, collect_signal_evidence,
)
from app.modules.reporting.scoring.narrative import write_narrative
from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.scoring.status import derive_status
from app.modules.reporting.scoring.transcript import segment
from app.modules.reporting.scoring.judge import grade_communication
from app.modules.reporting.scoring.types import SignalDef
from app.modules.reporting.schemas import (
    ReportRead, ScoreOut, QuestionOut, SignalAssessmentOut, ScoringManifest,
)

_TONE_BY_SCORE = lambda s: ("neutral" if s is None else "ok" if s >= 65 else "caution" if s >= 40 else "danger")
_COMM_POINTS = {"weak": 30, "adequate": 70, "strong": 100}


async def build_report(*, transcript, envelope, coverage_summary, questions,
                       signal_metadata, correlation_id, n_samples=None):
    signal_defs = [SignalDef(value=m["value"], type=m["type"], weight=m["weight"],
                             knockout=m["knockout"], priority=m["priority"]) for m in signal_metadata]
    def_by_value = {d.value: d for d in signal_defs}
    engine_states = build_engine_states(coverage_summary, signal_defs)
    knockout_close = detect_knockout_close(envelope)

    # --- Layer 2: re-check every reached signal (state != none), parallel ---
    reached = [d for d in signal_defs if engine_states[d.value] != "none"]
    q_by_signal: dict[str, dict] = {}
    for q in questions:
        for sv in q.get("signal_values", []):
            q_by_signal.setdefault(sv, q)

    async def _one(d: SignalDef):
        ev = collect_signal_evidence(envelope, d.value)
        q = q_by_signal.get(d.value, {})
        ctx = f"Q: {q.get('text','')}\nrubric: {json.dumps(q.get('rubric', {}))}"
        return d.value, await recheck_signal(signal_def=d, evidence_turns=ev,
                                             question_context=ctx,
                                             engine_state=engine_states[d.value],
                                             correlation_id=correlation_id)
    recheck_results = dict(await asyncio.gather(*[_one(d) for d in reached])) if reached else {}

    final_state = dict(engine_states)
    for sv, rc in recheck_results.items():
        final_state[sv] = rc.state

    # --- Layer 1: deterministic scoring ---
    scored = [ScoredSignal(value=d.value, type=d.type, weight=d.weight, knockout=d.knockout,
                           priority=d.priority, state=final_state[d.value],
                           score=score_state(final_state[d.value])) for d in signal_defs]
    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    beh = score_dimension("behavioral", scored, BEHAVIORAL_TYPES)
    overall, coverage = score_overall(scored)

    comm = await grade_communication(
        transcript_text="\n".join(t["text"] for t in transcript if t.get("role") == "candidate"),
        correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    knockouts = [KnockoutResult(signal=s.value, status=knockout_status(state=s.state),
                                reason="") for s in scored if s.knockout]
    verdict = resolve_verdict(overall=overall, coverage=coverage,
                              knockouts=knockouts, knockout_close=knockout_close)

    # --- per-question assembly ---
    units = segment(envelope=envelope, questions=questions)
    closed_early = knockout_close is not None
    triage_kind_by_q = _triage_kind_by_question(envelope)
    q_out: list[QuestionOut] = []
    for i, u in enumerate(units):
        q = next((x for x in questions if x["id"] == u.question_id), {})
        svs = q.get("signal_values", [])
        states = {sv: final_state.get(sv, "none") for sv in svs}
        defs = {sv: (def_by_value[sv].type, def_by_value[sv].knockout, def_by_value[sv].priority)
                for sv in svs if sv in def_by_value}
        badge, tone = derive_status(
            u, signal_states=states, signal_defs=defs,
            no_experience=triage_kind_by_q.get(u.question_id) == "no_experience",
            closed_before_complete=closed_early and i == len(units) - 1)
        q_out.append(QuestionOut(seq=i + 1, question_id=u.question_id, title=q.get("text", "")[:60],
                                 status_badge=badge, status_tone=tone,
                                 question_text=q.get("text", ""), candidate_quote=u.candidate_answer))

    signal_assessments = [SignalAssessmentOut(
        signal=d.value, type=d.type, weight=d.weight, knockout=d.knockout, priority=d.priority,
        engine_state=engine_states[d.value], final_state=final_state[d.value],
        grade=(recheck_results[d.value].grade if d.value in recheck_results else None),
        score=score_state(final_state[d.value]),
        evidence=(recheck_results[d.value].evidence_quotes if d.value in recheck_results else []),
        overridden=(recheck_results[d.value].overridden if d.value in recheck_results else False),
        override_reason=(recheck_results[d.value].override_reason if d.value in recheck_results else None),
    ) for d in signal_defs]

    # --- Layer 3: narrative (handed numbers as ground truth) ---
    gt = _narrative_ground_truth(job_questions=q_out, scored=scored, verdict=verdict,
                                 overall=overall, tech=tech, beh=beh, comm_score=comm_score,
                                 knockout_close=knockout_close)
    narrative = await write_narrative(ground_truth_json=gt, correlation_id=correlation_id)
    read_by_qid = {qn.question_id: qn for qn in narrative.questions}
    for qo in q_out:
        nq = read_by_qid.get(qo.question_id)
        if nq:
            qo.our_read = nq.our_read
            if nq.candidate_quote:
                qo.candidate_quote = nq.candidate_quote

    def _score_out(score, cov, conf):
        return ScoreOut(score=score, tier_label=tier_label(score), tone=_TONE_BY_SCORE(score),
                        confidence=conf, coverage=cov)

    return ReportRead(
        verdict=verdict.verdict, verdict_reason=narrative.decision.headline or verdict.reason,
        overall_score=overall, overall_coverage=coverage,
        overall_confidence=tech.confidence if overall is not None else "low",
        decision=narrative.decision,
        scores={
            "overall": _score_out(overall, coverage, tech.confidence),
            "technical": _score_out(tech.score, tech.coverage, tech.confidence),
            "behavioral": _score_out(beh.score, beh.coverage, beh.confidence),
            "communication": _score_out(comm_score, 1.0, "medium"),
        },
        quick_summary=narrative.quick_summary, strengths=narrative.strengths,
        concerns=narrative.concerns, questions=q_out, methodology=narrative.methodology,
        signal_assessments=signal_assessments, engine_version="v2", status="ready",
        scoring_manifest=ScoringManifest(
            scorer_model=ai_config.report_scorer_model,
            prompt_version=ai_config.report_scorer_prompt_version,
            generated_at=datetime.now(UTC).isoformat(), correlation_id=correlation_id,
            evidence_grounding_summary={
                "n_signals_rechecked": len(recheck_results),
                "n_overrides": sum(1 for r in recheck_results.values() if r.overridden),
                "coverage_map": {k: final_state[k] for k in final_state},
            }),
    )
```

Add the two helpers to `service.py`:

```python
def _triage_kind_by_question(envelope: dict) -> dict[str, str]:
    """Map active_question_id -> the strongest triage kind seen for it."""
    turn_to_q: dict[str, str] = {}
    for e in envelope.get("events", []):
        if e.get("kind") == "turn.decision":
            p = e.get("payload") or {}
            if p.get("turn_ref") and p.get("active_question_id"):
                turn_to_q[p["turn_ref"]] = p["active_question_id"]
    out: dict[str, str] = {}
    for e in envelope.get("events", []):
        if e.get("kind") == "engine.v2.triage.decision":
            p = e.get("payload") or {}
            qid = turn_to_q.get(p.get("turn_ref"))
            if qid and p.get("kind"):
                out[qid] = p["kind"]
    return out


def _narrative_ground_truth(*, job_questions, scored, verdict, overall, tech, beh,
                            comm_score, knockout_close) -> str:
    return json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "knockout_close": (
            {"signal": knockout_close.signal, "quote": knockout_close.quote}
            if knockout_close else None),
        "signals": [{"signal": s.value, "type": s.type, "state": s.state,
                     "must_have": s.knockout, "priority": s.priority} for s in scored],
        "questions": [{"question_id": q.question_id, "question_text": q.question_text,
                       "candidate_said": q.candidate_quote, "status": q.status_badge}
                      for q in job_questions],
    }, ensure_ascii=False)
```

- [ ] **Step 5: Update `persist_report` value dict to the new shape**

In `persist_report`, replace the `values: dict = dict(...)` block's report-derived fields with the new schema fields (store the new sub-objects into the existing JSONB columns):

```python
    values: dict = dict(
        verdict=report.verdict,
        verdict_reason=report.verdict_reason,
        overall_score=report.overall_score,
        overall_coverage=(float(report.overall_coverage) if report.overall_coverage is not None else None),
        overall_confidence=report.overall_confidence,
        dimension_scores={k: v.model_dump(mode="json") for k, v in report.scores.items()},
        knockout_results=[],  # retired; signal_assessments carries the audit trail
        signal_scorecards=[s.model_dump(mode="json") for s in report.signal_assessments],
        question_scorecards=[q.model_dump(mode="json") for q in report.questions],
        summary={
            "decision": report.decision.model_dump(mode="json"),
            "quick_summary": report.quick_summary,
            "strengths": [s.model_dump(mode="json") for s in report.strengths],
            "concerns": [c.model_dump(mode="json") for c in report.concerns],
            "methodology": report.methodology.model_dump(mode="json"),
        },
        scoring_manifest=(report.scoring_manifest.model_dump(mode="json") if report.scoring_manifest else None),
        engine_version=report.engine_version or "v2",
        status="ready",
        generated_at=datetime.now(UTC),
        rubric_snapshot=rubric_snapshot,
    )
```

- [ ] **Step 6: Delete the dead per-answer path**

```bash
git rm app/modules/reporting/scoring/opportunity.py tests/reporting/test_opportunity.py
```

In `scoring/judge.py`, delete `grade_answer`, `grade_answer_consistent`, `_majority_level`, `_extract_parsed_or_refusal`, `_LEVEL_ORDER` (keep `grade_communication` and its imports). In `scoring/types.py` ensure `ScoredUnit` is still defined (it is referenced by `transcript.py` + `status.py`) — it must remain; re-add it if Task 2 removed it:

```python
@dataclass(frozen=True)
class ScoredUnit:
    question_id: str
    question_text: str
    candidate_answer: str
    answer_start_ms: int
    probes_fired: int
    clarifies: int
    word_count: int
    candidate_engaged: bool
    question_kind: str | None = None
```

- [ ] **Step 7: Run the service test + full reporting suite**

Run: `docker compose run --rm nexus pytest tests/reporting -q -k "not prompt_quality"`
Expected: PASS. Fix any test referencing retired schema classes (`test_schemas.py`, `test_judge.py`, `test_input_builder.py`) by deleting the obsolete cases or updating to the new shape.

- [ ] **Step 8: Commit**

```bash
git add -A app/modules/reporting tests/reporting
git commit -m "feat(reporting): orchestrate 3-layer build_report on the engine coverage map; PDF-shaped schema"
```

---

## Task 9: Router serialization + regenerate the reference sessions

**Files:**
- Modify: `app/modules/reporting/router.py` (`_row_to_read`)
- Test: `tests/reporting/test_router.py`

- [ ] **Step 1: Update `_row_to_read` to the new shape**

In `app/modules/reporting/router.py`, replace `_row_to_read` body's constructed dict to read the new columns:

```python
    summary = row.summary or {}
    return ReportRead.model_validate({
        "id": str(row.id), "session_id": str(row.session_id), "status": row.status,
        "engine_version": row.engine_version, "version": row.version,
        "verdict": row.verdict, "verdict_reason": row.verdict_reason,
        "overall_score": row.overall_score,
        "overall_coverage": (float(row.overall_coverage) if row.overall_coverage is not None else 0.0),
        "overall_confidence": row.overall_confidence or "low",
        "decision": summary.get("decision") or {
            "headline": row.verdict_reason or "", "why_positive": {"title": "", "body": ""},
            "why_negative": {"title": "", "body": ""}},
        "scores": row.dimension_scores or {},
        "quick_summary": summary.get("quick_summary", ""),
        "strengths": summary.get("strengths", []),
        "concerns": summary.get("concerns", []),
        "questions": row.question_scorecards or [],
        "methodology": summary.get("methodology") or {"note": "", "charity_flags": []},
        "signal_assessments": row.signal_scorecards or [],
        "scoring_manifest": row.scoring_manifest, "human_decision": row.human_decision,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    })
```

- [ ] **Step 2: Update `test_router.py`**

Fix the report-construction fixtures in `tests/reporting/test_router.py` to build a `SessionReport` row whose `summary`/`dimension_scores`/`question_scorecards`/`signal_scorecards` match the new shapes, then assert the GET endpoints return them. Run:

Run: `docker compose run --rm nexus pytest tests/reporting/test_router.py -q`
Expected: PASS.

- [ ] **Step 3: Lint + typecheck the module**

Run: `docker compose run --rm nexus ruff check app/modules/reporting && docker compose run --rm nexus mypy app/modules/reporting`
Expected: zero errors (fix any that appear).

- [ ] **Step 4: Commit**

```bash
git add app/modules/reporting/router.py tests/reporting/test_router.py
git commit -m "feat(reporting): serialize the new PDF-shaped report schema"
```

- [ ] **Step 5: Regenerate the two reference sessions (real LLM calls) and eyeball**

Ensure the stack is up (`docker compose up -d`). Re-enqueue scoring for both sessions via the worker shell:

```bash
docker compose exec nexus python -c "
from app.modules.reporting.actors import score_session_report
score_session_report.send('c7173674-7795-4268-b4ab-829ad45b801b','<TENANT_ID>','recal-1',True)
score_session_report.send('bc7ba6d3-848b-49f7-8311-0aa01cb8b4aa','<TENANT_ID>','recal-2',True)
"
```

(Get `<TENANT_ID>` with: `docker exec supabase_db_backend psql -U postgres -d postgres -tAc "select tenant_id from sessions where id='c7173674-7795-4268-b4ab-829ad45b801b'"`.)

Then verify completeness:

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "
select left(session_id::text,8) sid, verdict, overall_score,
       jsonb_pretty(dimension_scores) from session_reports
where session_id in ('c7173674-7795-4268-b4ab-829ad45b801b','bc7ba6d3-848b-49f7-8311-0aa01cb8b4aa');"
```

**Acceptance (manual):**
- Session 1: `verdict=borderline`, `overall_score` ≈ 41–50 (Below/Well-Below Bar), technical+behavioral non-null, the cut-off AI-routing question carries `not_fully_assessed`.
- Session 2: `verdict=reject`, deal-breaker = the failed API/connector signal, `methodology.charity_flags` mentions the long silence, technical+behavioral non-null.
- Both read like `tmp/Ishant_Interview_Report_{2,3}.pdf` in content. Tune `constants.py` if scores drift materially from the PDFs, re-run, repeat.

- [ ] **Step 6: Final commit (any constant tuning)**

```bash
git add app/modules/reporting/scoring/constants.py
git commit -m "chore(reporting): calibrate scoring constants against reference sessions"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** §1 bug → Task 1; §4 deterministic core → Tasks 2–4; §5 re-check → Task 6; §6 narrative → Task 7; §7 status → Task 5; §8 schema → Task 8; §10 envelope → Task 1; §11 prompts → Tasks 6–7; §12 audit → manifest in Task 8; §13 tests → every task; §14 sequence = task order. Frontend (§9) is Plan B.
- **Prompt-quality tests** (`@prompt_quality`, real API) for `recheck`/`narrative` against the two envelopes are recommended once mocked tests are green — add under `tests/reporting/` guarded by the marker.
- **`knockout_results` column** is intentionally written as `[]` (retired); the audit trail lives in `signal_scorecards` (now `SignalAssessmentOut[]`). The frontend (Plan B) reads `signal_assessments` from there.
- **No migration** is required — existing `session_reports` JSONB columns are reused with new shapes.
