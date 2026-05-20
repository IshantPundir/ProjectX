# Interview Engine — Conversational Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two worst conversational bugs observed in session `26c2efc3`: (1) `acknowledge_no_experience` promises "let me ask something different" but the State Engine never advances the queue, so the next turn re-enters the same question; (2) the `consecutive_dont_know_count` regex misses common confusion phrasings ("I didn't quite understand"), letting the agent loop on the same clarify (the death-spiral).

**Architecture:** `acknowledge_no_experience` (Judge action), `meta_confession` promotion, and the new "stuck" escalation all route through a single State-Engine helper that advances the queue and tells the Speaker to acknowledge + deliver the next question in ONE turn (Option A). The regex is deleted; stuckness becomes a Judge-emitted `candidate_still_confused` flag that the State Engine counts per-question and caps at 2 clarify attempts before escalating.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. All work is inside `app/modules/interview_engine/` plus its prompt files. No DB migration. No new external deps.

---

## Background context for the implementing engineer

Read these files before starting — they are the surfaces this plan touches:
- `app/modules/interview_engine/state/engine.py` — the deterministic State Engine. `process_judge_output()` is the per-turn entry point. It dispatches on `judge_output.next_action`, then runs meta_confession promotion (step "5a"), then the knockout_policy override (step "6"), then `_build_speaker_input`.
- `app/modules/interview_engine/models/judge.py` — `JudgeOutput`, `TurnMetadata`, the cross-field `@model_validator`s.
- `app/modules/interview_engine/models/speaker.py` — `SpeakerInput`. Already has `is_post_cap_advance` and `is_post_phase_transition` flags; we add `is_post_acknowledge`.
- `app/modules/interview_engine/models/queue.py` — `QuestionState`. Has `consecutive_dont_know_count` (we rename) and `push_back_count`.
- `app/modules/interview_engine/state/queue.py` — `QuestionQueue` mutators.
- `app/modules/interview_engine/speaker/input_builder.py` — `build_speaker_input()`. Anti-leak boundary: NEVER add rubric/anchor/signal content.
- `app/modules/interview_engine/judge/input_builder.py` — `JudgeInputPayload` + `build_judge_input()`.
- `prompts/v2/engine/judge.system.txt` — Judge brain.
- `prompts/v2/engine/speaker/deliver_question.txt` — Speaker deliver-question scaffold.

**Test conventions:** State-Engine unit tests live in `tests/interview_engine/state/`. They construct a `StateEngine` from a hand-built `SessionConfig` and call `process_judge_output(...)`, then assert on `decision.speaker_input.instruction_kind`, `eng.queue_snapshot()`, etc. See `tests/interview_engine/state/test_meta_confession_promotion.py` for the fixture-helper style (`_make_judge_output`, `_make_question`, `_make_question_state`, `_make_signal_metadata`). Reuse those helpers' shape.

**Run a single test:** `docker compose run --rm nexus pytest tests/interview_engine/state/test_engine.py::test_name -v`
**Run a test file:** `docker compose run --rm nexus pytest tests/interview_engine/state/test_engine.py -v`
The `prompt_quality` marker covers LLM-eval tests; exclude with `-m "not prompt_quality"` for fast deterministic runs.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/modules/interview_engine/models/speaker.py` | Speaker wire-input model | Add `is_post_acknowledge: bool` |
| `app/modules/interview_engine/speaker/input_builder.py` | Project state → SpeakerInput | Thread `is_post_acknowledge` (deliver_question only) |
| `app/modules/interview_engine/models/judge.py` | Judge output model | Add `candidate_still_confused` to `TurnMetadata` + validator |
| `app/modules/interview_engine/models/queue.py` | Per-question state | Rename `consecutive_dont_know_count` → `still_confused_count` |
| `app/modules/interview_engine/state/queue.py` | Queue mutators | Rename the three `*_dont_know_count` methods |
| `app/modules/interview_engine/judge/input_builder.py` | Judge wire-input | Rename `active_question_consecutive_dont_know_count` → `active_question_still_confused_count` |
| `app/modules/interview_engine/state/engine.py` | Deterministic routing | `_acknowledge_and_advance` helper; reroute ack/meta/stuck; delete regex; drive counter off the flag |
| `app/modules/interview_engine/orchestrator.py` | Per-turn glue | Update the renamed snapshot field reference |
| `prompts/v2/engine/judge.system.txt` | Judge brain | Drop dont-know rules; add `candidate_still_confused` semantics |
| `prompts/v2/engine/speaker/deliver_question.txt` | Speaker scaffold | Add POST-ACKNOWLEDGE branch |
| `tests/interview_engine/judge/eval/fixtures/*.json` (32) | Judge eval fixtures | Rename the input field key |

---

## Task A1: Add `is_post_acknowledge` flag to SpeakerInput

**Files:**
- Modify: `app/modules/interview_engine/models/speaker.py`
- Modify: `app/modules/interview_engine/speaker/input_builder.py`
- Test: `tests/interview_engine/speaker/test_input_builder.py` (create if absent; otherwise append)

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/speaker/test_input_builder.py` (create the file with the imports below if it does not exist):

```python
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.speaker.input_builder import build_speaker_input
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.queue import QuestionQueue


def _advance_judge_output() -> JudgeOutput:
    return JudgeOutput(
        reasoning="Test fixture: advancing after the candidate disclosed no experience.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"),
        turn_metadata=TurnMetadata(),
    )


def _queue_with_active() -> QuestionQueue:
    q = QuestionQueue.from_initial(questions=[
        {"question_id": "q1", "is_mandatory": True, "follow_ups": [], "signal_values": ["s1"]},
        {"question_id": "q2", "is_mandatory": True, "follow_ups": [], "signal_values": ["s2"]},
    ])
    q.advance_to("q2", at_turn=1)
    return q


def test_is_post_acknowledge_set_on_deliver_question():
    si = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_advance_judge_output(),
        active_question=None,
        queue=_queue_with_active(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Arjun",
        last_candidate_utterance="I don't know how to do that.",
        is_post_acknowledge=True,
    )
    assert si.is_post_acknowledge is True


def test_is_post_acknowledge_dropped_on_non_deliver_question():
    si = build_speaker_input(
        instruction_kind=InstructionKind.clarify,
        judge_output=_advance_judge_output(),
        active_question=None,
        queue=_queue_with_active(),
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Arjun",
        last_candidate_utterance="huh?",
        is_post_acknowledge=True,
    )
    assert si.is_post_acknowledge is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v`
Expected: FAIL — `build_speaker_input() got an unexpected keyword argument 'is_post_acknowledge'`.

- [ ] **Step 3: Add the field to `SpeakerInput`**

In `app/modules/interview_engine/models/speaker.py`, immediately after the `is_post_phase_transition` field block, add:

```python
    is_post_acknowledge: bool = Field(
        default=False,
        description=(
            "True when this deliver_question fires immediately after the "
            "candidate explicitly disclosed no experience / admitted they "
            "cannot answer (acknowledge_no_experience, meta_confession "
            "promotion, or the still-confused cap). The deliver_question "
            "scaffold opens with a warm 'fair enough, let me try something "
            "different' acknowledgment, then delivers the next question in "
            "the SAME turn (Option A — no separate acknowledge turn). "
            "Precedence: is_post_acknowledge > is_post_cap_advance > "
            "is_post_phase_transition. False on every other path."
        ),
    )
```

- [ ] **Step 4: Thread the flag through `build_speaker_input`**

In `app/modules/interview_engine/speaker/input_builder.py`:

(a) Add the parameter to the signature, immediately after `is_post_cap_advance: bool = False,`:

```python
    is_post_acknowledge: bool = False,
```

(b) After the existing `post_cap_payload = (...)` block, add:

```python
    # is_post_acknowledge mirrors is_post_cap_advance: only meaningful on
    # deliver_question (the next question delivered right after the candidate
    # bowed out of the previous one). Dropped on every other kind.
    post_acknowledge_payload = (
        is_post_acknowledge
        and instruction_kind == InstructionKind.deliver_question
    )
```

(c) In the `return SpeakerInput(...)` call, add the field (next to `is_post_cap_advance=post_cap_payload,`):

```python
        is_post_acknowledge=post_acknowledge_payload,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/speaker/test_input_builder.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine/models/speaker.py app/modules/interview_engine/speaker/input_builder.py tests/interview_engine/speaker/test_input_builder.py
git commit -m "feat(interview-engine): add is_post_acknowledge SpeakerInput flag"
```

---

## Task A2: `_acknowledge_and_advance` routing helper + reroute the `acknowledge_no_experience` action

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py`
- Test: `tests/interview_engine/state/test_acknowledge_advance.py` (create)

**Design:** A new private method `_acknowledge_and_advance(self, *, failed_signal_value, elapsed_ms, turn_id, warnings)` that:
1. Applies a synthetic `→failed` observation on `failed_signal_value` if its current coverage is not already `failed` (transition `partial→failed` or `none→failed`; if already `failed`, no-op).
2. Records a `KnockoutFailure` if `failed_signal_value` is a knockout signal.
3. Advances the queue to the next pending question. If one exists → returns `(InstructionKind.deliver_question, is_post_acknowledge=True, closing_disclosure_signal=None)`. If none → transitions lifecycle to `closing`, sets `last_outcome`, returns `(InstructionKind.polite_close, is_post_acknowledge=False, closing_disclosure_signal=failed_signal_value)`.

The existing `acknowledge_no_experience` action branch (the path that survives the `acknowledge_without_failure_obs` guard) calls this helper instead of setting `instruction = InstructionKind.acknowledge_no_experience`.

- [ ] **Step 1: Write the failing test**

Create `tests/interview_engine/state/test_acknowledge_advance.py`:

```python
"""ack_no_experience / meta_confession / stuck now advance the queue in one turn (Option A)."""
from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload, JudgeOutput, NextAction, Observation, TurnMetadata,
)
from app.modules.interview_engine.models.judge import CoverageTransition, CoverageQuality
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid: str, signal: str, *, mandatory: bool, position: int) -> QuestionConfig:
    return QuestionConfig(
        id=qid, position=position, text="A sufficiently long question about the topic.",
        signal_values=[signal], estimated_minutes=2.0, is_mandatory=mandatory,
        follow_ups=[], positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["rf-a", "rf-b"],
        rubric=QuestionRubric(
            excellent="A strong answer names concrete tools and tradeoffs here.",
            meets_bar="An acceptable answer names at least one concrete tool here.",
            below_bar="A weak answer stays generic with no specifics at all here.",
        ),
        evaluation_hint="Look for one concrete, specific example.",
        question_kind="technical_depth",
    )


def _session_config() -> SessionConfig:
    return SessionConfig(
        session_id="s1", job_id="j1", candidate_id="c1", job_title="Engineer",
        role_summary="Build integrations.", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(
            stage_id="st1", stage_type="ai_screening", name="Screen",
            duration_minutes=15, difficulty="medium",
            questions=[
                _q("q1", "sig_python", mandatory=True, position=0),
                _q("q2", "sig_rest", mandatory=True, position=1),
            ],
        ),
        signals=["sig_python", "sig_rest"],
        signal_metadata=[
            SignalMetadata(value="sig_python", type="competency", priority="required",
                           weight=2, knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_rest", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
        ],
    )


def _ack_output(failed_signal: str) -> JudgeOutput:
    return JudgeOutput(
        reasoning="Candidate explicitly disclosed no experience with the active signal.",
        observations=[Observation(
            signal_value=failed_signal, anchor_id=-1,
            evidence_quote="I have never used that.",
            coverage_transition=CoverageTransition.none_to_failed,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value=failed_signal),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )


def test_ack_no_experience_advances_to_next_question_in_one_turn():
    eng = StateEngine(session_config=_session_config(),
                      config=StateEngineConfig(knockout_policy="close_polite"))
    # Start: advance to q1 (synthetic session start).
    eng.process_judge_output(
        turn_id="t0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Candidate discloses no-experience on q1's (non-knockout) signal.
    decision = eng.process_judge_output(
        turn_id="t1", judge_output=_ack_output("sig_python"),
        candidate_utterance_text="I have never used Python.", elapsed_ms=1000,
    )
    # Option A: same turn delivers the NEXT question with the post-ack flag.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1  # advanced to q2
    assert eng.lifecycle_snapshot().state.value == "active"


def test_ack_on_last_question_politely_closes_with_disclosure_signal():
    cfg = _session_config()
    eng = StateEngine(session_config=cfg, config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(
        turn_id="t0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Advance to q2 (last mandatory) first via a clean advance.
    from app.modules.interview_engine.models.judge import AdvancePayload
    adv = JudgeOutput(
        reasoning="Candidate gave a concrete answer on q1; advancing to q2.",
        observations=[Observation(
            signal_value="sig_python", anchor_id=0, evidence_quote="I use Python daily.",
            coverage_transition=CoverageTransition.none_to_sufficient, quality=CoverageQuality.concrete,
        )],
        candidate_claims=[], next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"), turn_metadata=TurnMetadata(),
    )
    eng.process_judge_output(turn_id="t1", judge_output=adv,
                             candidate_utterance_text="I use Python daily.", elapsed_ms=1000)
    # Now ack no-experience on q2 (the last question) → polite_close.
    decision = eng.process_judge_output(
        turn_id="t2", judge_output=_ack_output("sig_rest"),
        candidate_utterance_text="I have never touched REST.", elapsed_ms=2000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert eng.lifecycle_snapshot().state.value == "closing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_acknowledge_advance.py -v`
Expected: FAIL — `test_ack_no_experience_advances_to_next_question_in_one_turn` asserts `deliver_question` but current code returns `acknowledge_no_experience`.

- [ ] **Step 3: Add the `_acknowledge_and_advance` helper**

In `app/modules/interview_engine/state/engine.py`, add this method to `StateEngine` (place it just below `_fallback_advance_to_next_pending`):

```python
    def _acknowledge_and_advance(
        self,
        *,
        failed_signal_value: str,
        elapsed_ms: int,
        turn_id: str,
        warnings: list[ValidationWarning],
    ) -> tuple[InstructionKind, bool, str | None]:
        """Option A: acknowledge the candidate bowing out AND move on in one turn.

        1. Record a synthetic ->failed observation on failed_signal_value
           (unless already failed). Record a KnockoutFailure if it is a
           knockout signal.
        2. Advance to the next pending question -> deliver_question with
           is_post_acknowledge=True; OR polite_close (with the disclosure
           signal) when no pending question remains.

        Returns (instruction_kind, is_post_acknowledge, closing_disclosure_signal).
        """
        current = self._ledger.snapshot().snapshots.get(failed_signal_value)
        current_state = current.coverage if current is not None else CoverageState.none
        if current_state != CoverageState.failed:
            transition = (
                CoverageTransition.partial_to_failed
                if current_state == CoverageState.partial
                else CoverageTransition.none_to_failed
            )
            try:
                self._ledger.apply_observation(
                    Observation(
                        signal_value=failed_signal_value,
                        anchor_id=-1,
                        evidence_quote="[acknowledge_and_advance: synthetic failure]",
                        coverage_transition=transition,
                    ),
                    turn_id=turn_id, recorded_at_ms=elapsed_ms,
                )
            except IllegalCoverageTransition:
                pass
        if failed_signal_value in self._knockout_signals:
            self._lifecycle.record_knockout(KnockoutFailure(
                question_id=self._queue.active_question_id() or "",
                reason=f"acknowledge_and_advance on {failed_signal_value!r}"[:200],
                signal_values=[failed_signal_value],
                occurred_at_ms=elapsed_ms,
            ))
        next_pending = self.next_pending_question()
        if next_pending is None:
            self._lifecycle.set_last_outcome(
                SessionOutcome.knockout_closed
                if self._lifecycle.snapshot().has_knockout()
                else SessionOutcome.completed
            )
            if self._lifecycle.snapshot().state.value == "active":
                self._lifecycle.transition_to_closing()
            return InstructionKind.polite_close, False, failed_signal_value
        next_id, _is_mandatory = next_pending
        try:
            self._queue.advance_to(next_id, at_turn=self._turn_count)
        except QueueError as exc:
            warnings.append(ValidationWarning(
                code="acknowledge_advance_failed",
                details={"target": next_id, "reason": str(exc)},
            ))
            return InstructionKind.polite_close, False, failed_signal_value
        return self._first_or_continuing_instruction(), True, None
```

- [ ] **Step 4: Reroute the `acknowledge_no_experience` action branch**

In `process_judge_output`, find the `elif action == NextAction.acknowledge_no_experience:` branch. It currently ends with `instruction = InstructionKind.acknowledge_no_experience` (in the `else` of the `acknowledge_without_failure_obs` guard). Replace that single line with the ack+advance routing. The branch becomes:

```python
        elif action == NextAction.acknowledge_no_experience:
            failure_obs_applied = any(
                o.coverage_transition.value.endswith("→failed")
                for o in applied_observations
            )
            if not failure_obs_applied:
                warnings.append(ValidationWarning(
                    code="acknowledge_without_failure_obs",
                    level="warning",
                    details={
                        "original_action": action.value,
                        "downgraded_to": "clarify",
                        "reason": (
                            "acknowledge_no_experience requires at least one "
                            "surviving →failed observation; none applied this "
                            "turn. Downgrading to clarify so the Speaker "
                            "rephrases the question."
                        ),
                    },
                ))
                instruction = InstructionKind.clarify
            else:
                # Option A: acknowledge AND advance in one turn.
                failed_payload = judge_output.next_action_payload
                assert isinstance(failed_payload, AcknowledgeNoExperiencePayload)
                instruction, is_post_acknowledge, closing_disclosure_signal = (
                    self._acknowledge_and_advance(
                        failed_signal_value=failed_payload.failed_signal_value,
                        elapsed_ms=elapsed_ms, turn_id=turn_id, warnings=warnings,
                    )
                )
```

Then, in the `_build_speaker_input(...)` call at the end of `process_judge_output`, add the new keyword (next to `is_post_cap_advance=is_post_cap_advance,`):

```python
            is_post_acknowledge=is_post_acknowledge,
```

And initialize the local near the other dispatch-locals (where `is_post_cap_advance: bool = False` is declared, around the top of step 5):

```python
        is_post_acknowledge: bool = False
```

- [ ] **Step 5: Thread `is_post_acknowledge` through `_build_speaker_input`**

In `app/modules/interview_engine/state/engine.py`, update the `_build_speaker_input` method signature to accept and forward the flag. Add the parameter (next to `is_post_cap_advance: bool = False,`):

```python
        is_post_acknowledge: bool = False,
```

And in its `return build_speaker_input(...)` call, add (next to `is_post_cap_advance=is_post_cap_advance,`):

```python
            is_post_acknowledge=is_post_acknowledge,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_acknowledge_advance.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Run the State Engine regression to catch breakage**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/ -v`
Expected: PASS. If `test_engine.py` has a test asserting `acknowledge_no_experience` is the resolved instruction on a surviving-failure ack, update that test's expectation to `deliver_question` (Option A) — the old two-turn behavior is intentionally gone.

- [ ] **Step 8: Commit**

```bash
git add app/modules/interview_engine/state/engine.py tests/interview_engine/state/test_acknowledge_advance.py
git commit -m "feat(interview-engine): ack_no_experience advances queue in one turn (Option A)"
```

---

## Task A3: Route meta_confession promotion through `_acknowledge_and_advance`

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py`
- Test: `tests/interview_engine/state/test_meta_confession_promotion.py` (append)

**Why:** Session `26c2efc3` turns 25/27 fired meta_confession promotion but stayed on the same question, because promotion only set `instruction = acknowledge_no_experience`. Promotion must now advance.

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/state/test_meta_confession_promotion.py`:

```python
def test_meta_confession_promotion_advances_queue_in_one_turn():
    """After promotion, the State Engine delivers the NEXT question with
    is_post_acknowledge (Option A) instead of staying on the same question."""
    from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
    from app.modules.interview_runtime.schemas import (
        CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
        SessionConfig, SignalMetadata, StageConfig,
    )
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, Observation, PushBackPayload, TurnMetadata,
        CoverageTransition, CoverageQuality,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    def _q(qid, sig, pos, mandatory):
        return QuestionConfig(
            id=qid, position=pos, text="A question about the active topic here.",
            signal_values=[sig], estimated_minutes=2.0, is_mandatory=mandatory,
            follow_ups=[], positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
            rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
            evaluation_hint="Look for specifics here.", question_kind="technical_depth",
        )

    cfg = SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng",
        role_summary="r", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty="medium",
                          questions=[_q("q1", "sig_a", 0, True), _q("q2", "sig_b", 1, True)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
        ],
    )
    eng = StateEngine(session_config=cfg, config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    # Give q1 one push_back so promotion's push_back_count>=1 guard is satisfied,
    # and exhaust probes (q1 has none) — coverage stays uncovered.
    pb = JudgeOutput(
        reasoning="Candidate engaged but the answer was thin; pushing for specifics.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="I would log.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata())
    eng.process_judge_output(turn_id="t1", judge_output=pb,
                             candidate_utterance_text="I would log.", elapsed_ms=1000)
    # Now meta_confession.
    mc = JudgeOutput(
        reasoning="Candidate admits they cannot answer THIS question after engaging earlier.",
        observations=[], candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(candidate_meta_confession=True))
    decision = eng.process_judge_output(turn_id="t2", judge_output=mc,
                                        candidate_utterance_text="I don't know how to answer this.",
                                        elapsed_ms=2000)
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_meta_confession_promotion.py::test_meta_confession_promotion_advances_queue_in_one_turn -v`
Expected: FAIL — current promotion sets `instruction = acknowledge_no_experience` and does not advance.

- [ ] **Step 3: Replace the promotion override block**

In `process_judge_output`, find the meta_confession promotion block (`if meta_warn is not None:`). It currently overrides `judge_output.next_action`, applies a synthetic obs, records a knockout, and sets `instruction = InstructionKind.acknowledge_no_experience`. Replace the synthetic-obs + knockout + instruction lines (everything after `failed_signal_value = str(meta_warn.details["failed_signal_value"])`) with a single call to the shared helper:

```python
            if meta_warn is not None:
                warnings.append(meta_warn)
                failed_signal_value = str(meta_warn.details["failed_signal_value"])
                judge_output.next_action = NextAction.acknowledge_no_experience
                judge_output.next_action_payload = AcknowledgeNoExperiencePayload(
                    failed_signal_value=failed_signal_value,
                )
                instruction, is_post_acknowledge, closing_disclosure_signal = (
                    self._acknowledge_and_advance(
                        failed_signal_value=failed_signal_value,
                        elapsed_ms=elapsed_ms, turn_id=turn_id, warnings=warnings,
                    )
                )
```

(The helper now owns the synthetic-failure-obs + knockout-record logic that used to be inline, so the deleted lines are not lost — they moved into `_acknowledge_and_advance` in Task A2.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_meta_confession_promotion.py -v`
Expected: PASS (existing promotion tests + the new advance test).

- [ ] **Step 5: Run State Engine regression**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/ -v`
Expected: PASS. The knockout_policy override block still runs after promotion; for a close_polite tenant with a knockout signal it will set lifecycle to closing BEFORE `_acknowledge_and_advance`'s advance — verify no double-transition error (the helper guards `if state == "active"`).

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine/state/engine.py tests/interview_engine/state/test_meta_confession_promotion.py
git commit -m "feat(interview-engine): meta_confession promotion advances queue (Option A)"
```

---

## Task A4: Speaker `deliver_question.txt` — POST-ACKNOWLEDGE branch

**Files:**
- Modify: `prompts/v2/engine/speaker/deliver_question.txt`
- Manual verification only (prompt change; no deterministic unit test).

- [ ] **Step 1: Add the POST-ACKNOWLEDGE section**

In `prompts/v2/engine/speaker/deliver_question.txt`, immediately after the `# POST-CAP ADVANCE (when is_post_cap_advance is true)` section and before `# POST-PHASE TRANSITION`, insert:

```
# POST-ACKNOWLEDGE (when is_post_acknowledge is true)
The candidate just explicitly told you they cannot answer / have no
experience with the previous question. Acknowledge that warmly and briefly,
then deliver the new question in the SAME turn:
  "Mm, fair enough — let me try something different. {new question}"
  "No worries — let us switch to another one. {new question}"
  "Got it — different angle then. {new question}"
The acknowledgment is ONE short clause. Never name what they failed, never
evaluate ("that's fine that you don't know"). Then the new question.
```

- [ ] **Step 2: Update the PRECEDENCE section**

Find the `# PRECEDENCE WITH is_post_cap_advance` section. Replace its heading and body with a three-way precedence:

```
# PRECEDENCE
If multiple flags are true at once, apply this order (highest first):
  1. is_post_acknowledge  — candidate bowed out; warm "fair enough, different one".
  2. is_post_cap_advance  — candidate couldn't give specifics; neutral topic shift.
  3. is_post_phase_transition — moving from background to technical; brief warm segue.
Use ONLY the highest-precedence flag's opener. We never celebrate depth that
was not there, and we never stack two segues.
```

- [ ] **Step 3: Update the REMINDER line**

Append to the final `# REMINDER` block: `If is_post_acknowledge, open with a brief warm acknowledgment then the new question — never name what they could not answer.`

- [ ] **Step 4: Verify the prompt still loads**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v` (and any speaker-prompt-loadable test). Expected: PASS — confirms no template-variable typos.

- [ ] **Step 5: Manual smoke (optional but recommended)**

Use the dev tool from the speaker work: `docker compose run --rm nexus python scripts/speak_one_off.py` with a `deliver_question` + `is_post_acknowledge=True` SpeakerInput, and confirm the utterance opens with a warm acknowledgment + the new question. Listen for TTS-friendliness (no glued em-dash like "fair—let").

- [ ] **Step 6: Commit**

```bash
git add prompts/v2/engine/speaker/deliver_question.txt
git commit -m "feat(interview-engine): deliver_question post-acknowledge segue branch"
```

---

## Task A5: Add `candidate_still_confused` flag to `TurnMetadata`

**Files:**
- Modify: `app/modules/interview_engine/models/judge.py`
- Test: `tests/interview_engine/judge/test_judge_models.py` (create or append)

- [ ] **Step 1: Write the failing test**

Append to (or create) `tests/interview_engine/judge/test_judge_models.py`:

```python
import pytest
from pydantic import ValidationError
from app.modules.interview_engine.models.judge import (
    ClarifyPayload, ClarifyKind, JudgeOutput, NextAction, RedirectPayload, TurnMetadata,
)


def test_still_confused_allowed_with_clarify():
    out = JudgeOutput(
        reasoning="Candidate is generically confused again after we already rephrased once.",
        observations=[], candidate_claims=[],
        next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
        turn_metadata=TurnMetadata(candidate_still_confused=True),
    )
    assert out.turn_metadata.candidate_still_confused is True


def test_still_confused_rejected_without_clarify():
    with pytest.raises(ValidationError):
        JudgeOutput(
            reasoning="Candidate is confused but we are emitting redirect — incoherent pairing.",
            observations=[], candidate_claims=[],
            next_action=NextAction.redirect,
            next_action_payload=RedirectPayload(),
            turn_metadata=TurnMetadata(candidate_still_confused=True),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_models.py -v`
Expected: FAIL — `TurnMetadata` has no field `candidate_still_confused`.

- [ ] **Step 3: Add the field + validator**

In `app/modules/interview_engine/models/judge.py`, add the field to `TurnMetadata` (after `candidate_meta_confession`):

```python
    # Candidate is GENERICALLY confused / not understanding the question —
    # the broad_rephrase-style "I still don't get it" pattern, distinct from
    # an engaged specific clarify (term_definition / use_case_anchor /
    # role_context). Set true when the candidate signals they cannot engage
    # with the question as posed and we have likely already tried to help.
    # The State Engine counts consecutive still-confused turns on a question
    # and escalates to acknowledge-and-advance after 2 attempts. Replaces the
    # retired _DONT_KNOW regex.
    candidate_still_confused: bool = False
```

Then add this `@model_validator(mode="after")` method to `JudgeOutput` (next to `_check_greeting_action_alignment`):

```python
    @model_validator(mode="after")
    def _check_still_confused_action_alignment(self) -> "JudgeOutput":
        """candidate_still_confused only makes sense paired with clarify.

        If the candidate is too confused to engage, the coherent action is to
        clarify (and the State Engine decides when to stop and move on). Any
        other action paired with the flag is the model misclassifying; reject
        so JudgeService falls back rather than confusing the stuck-counter."""
        if not self.turn_metadata.candidate_still_confused:
            return self
        if self.next_action != NextAction.clarify:
            raise ValueError(
                f"candidate_still_confused=true requires next_action=clarify; "
                f"got {self.next_action.value!r}."
            )
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_models.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/models/judge.py tests/interview_engine/judge/test_judge_models.py
git commit -m "feat(interview-engine): add candidate_still_confused TurnMetadata flag"
```

---

## Task A6: Rename `consecutive_dont_know_count` → `still_confused_count`

**Files:**
- Modify: `app/modules/interview_engine/models/queue.py`
- Modify: `app/modules/interview_engine/state/queue.py`
- Modify: `app/modules/interview_engine/judge/input_builder.py`
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `app/modules/interview_engine/state/engine.py` (call sites only — logic comes in A7)
- Modify: `tests/interview_engine/state/test_engine.py` (the 3 dont_know tests will be replaced in A7; here just fix references)
- Modify: 32 fixtures under `tests/interview_engine/judge/eval/fixtures/`

**Note:** This is a mechanical rename. Do it in one commit so no intermediate state has a half-renamed field.

- [ ] **Step 1: Rename the model field**

In `app/modules/interview_engine/models/queue.py`, rename `consecutive_dont_know_count` to `still_confused_count` and update its description to:

```python
    still_confused_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Consecutive turns on this question where the Judge set "
            "turn_metadata.candidate_still_confused=true (generic confusion "
            "/ cannot engage). Reset to 0 on any other turn or on advance. "
            "The State Engine escalates to acknowledge-and-advance once this "
            "reaches 2 (i.e. on the 3rd consecutive confusion). Surfaced to "
            "the Judge via JudgeInputPayload.active_question_still_confused_count."
        ),
    )
```

- [ ] **Step 2: Rename the queue mutators**

In `app/modules/interview_engine/state/queue.py`, rename:
- `increment_active_dont_know_count` → `increment_active_still_confused_count`
- `reset_active_dont_know_count` → `reset_active_still_confused_count`
- `active_dont_know_count` → `active_still_confused_count`

and inside each, change `active.consecutive_dont_know_count` → `active.still_confused_count`. Update their docstrings to reference `candidate_still_confused` instead of the regex.

- [ ] **Step 3: Rename the Judge input field**

In `app/modules/interview_engine/judge/input_builder.py`:
- Rename the `JudgeInputPayload` field `active_question_consecutive_dont_know_count` → `active_question_still_confused_count` (keep `int`, `ge=0`, default 0). Update its description to reference `candidate_still_confused` and the 2-attempt cap.
- Rename the `build_judge_input` parameter `active_question_consecutive_dont_know_count` → `active_question_still_confused_count` and the value assignment.

- [ ] **Step 4: Rename the orchestrator call site**

In `app/modules/interview_engine/orchestrator.py`, find `active_dont_know_count = (...)` in `_run_turn_body` and the `active_question_consecutive_dont_know_count=active_dont_know_count` kwarg. Rename the local to `active_still_confused_count`, change `.consecutive_dont_know_count` → `.still_confused_count`, and the kwarg to `active_question_still_confused_count=active_still_confused_count`.

- [ ] **Step 5: Fix the State Engine call sites (logic stays until A7)**

In `app/modules/interview_engine/state/engine.py`, the block at "4a" currently calls `increment_active_dont_know_count()` / `reset_active_dont_know_count()`. Leave the behavior for A7, but rename the method calls so the file imports/runs. (A7 replaces this block entirely, so a minimal rename here is fine.)

- [ ] **Step 6: Bulk-rename the fixture key**

The 32 eval fixtures embed the JudgeInputPayload key. Rename it across all fixtures:

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rl "active_question_consecutive_dont_know_count" tests/interview_engine/judge/eval/fixtures/ \
  | xargs sed -i 's/active_question_consecutive_dont_know_count/active_question_still_confused_count/g'
```

Verify zero stragglers:

```bash
grep -rn "consecutive_dont_know" app/ tests/ | grep -v __pycache__
```
Expected: only matches inside `tests/interview_engine/state/test_engine.py` (the 3 dont_know-specific tests, removed in A7).

- [ ] **Step 7: Run the broad regression**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -m "not prompt_quality" -v`
Expected: PASS except the 3 `test_dont_know_*` tests in `test_engine.py` (they reference the deleted regex; A7 replaces them). If any OTHER test fails on the rename, fix the reference.

- [ ] **Step 8: Commit**

```bash
git add app/modules/interview_engine/ tests/interview_engine/
git commit -m "refactor(interview-engine): rename consecutive_dont_know_count -> still_confused_count"
```

---

## Task A7: Delete the regex; drive the counter off `candidate_still_confused`; add the stuck-cap escalation

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py`
- Modify: `tests/interview_engine/state/test_engine.py` (replace the 3 dont_know tests)
- Test: `tests/interview_engine/state/test_still_confused_escalation.py` (create)

- [ ] **Step 1: Write the failing escalation test**

Create `tests/interview_engine/state/test_still_confused_escalation.py`:

```python
"""Stuck-candidate escalation: after 2 still-confused clarifies, the 3rd
escalates to acknowledge-and-advance (Option A). No regex involved."""
from app.modules.interview_engine.models.judge import (
    ClarifyKind, ClarifyPayload, JudgeOutput, NextAction, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


def _q(qid, sig, pos):
    return QuestionConfig(
        id=qid, position=pos, text="A question about the active topic here, please.",
        signal_values=[sig], estimated_minutes=2.0, is_mandatory=True, follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="Look for specifics here.", question_kind="technical_depth")


def _cfg():
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng", role_summary="r",
        seniority_level="mid", company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty="medium",
                          questions=[_q("q1", "sig_a", 0), _q("q2", "sig_b", 1)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
        ])


def _confused_clarify():
    return JudgeOutput(
        reasoning="Candidate expresses generic confusion and cannot engage with the question.",
        observations=[], candidate_claims=[], next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
        turn_metadata=TurnMetadata(candidate_still_confused=True))


def test_two_clarifies_then_escalate_to_ack_advance():
    eng = StateEngine(session_config=_cfg(), config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    # Confusion #1 -> clarify, count -> 1
    d1 = eng.process_judge_output(turn_id="t1", judge_output=_confused_clarify(),
                                  candidate_utterance_text="I didn't quite understand.", elapsed_ms=1000)
    assert d1.speaker_input.instruction_kind == InstructionKind.clarify
    assert eng.queue_snapshot().questions[0].still_confused_count == 1
    # Confusion #2 -> clarify, count -> 2
    d2 = eng.process_judge_output(turn_id="t2", judge_output=_confused_clarify(),
                                  candidate_utterance_text="Still not following, sorry.", elapsed_ms=2000)
    assert d2.speaker_input.instruction_kind == InstructionKind.clarify
    assert eng.queue_snapshot().questions[0].still_confused_count == 2
    # Confusion #3 -> escalate to ack+advance
    d3 = eng.process_judge_output(turn_id="t3", judge_output=_confused_clarify(),
                                  candidate_utterance_text="I really do not get it.", elapsed_ms=3000)
    assert d3.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert d3.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1


def test_still_confused_count_resets_on_non_confused_turn():
    eng = StateEngine(session_config=_cfg(), config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    eng.process_judge_output(turn_id="t1", judge_output=_confused_clarify(),
                             candidate_utterance_text="huh?", elapsed_ms=1000)
    assert eng.queue_snapshot().questions[0].still_confused_count == 1
    # A clarify WITHOUT the flag (engaged term_definition) resets the streak.
    engaged = JudgeOutput(
        reasoning="Candidate asks a specific, engaged question about a term in the prompt.",
        observations=[], candidate_claims=[], next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.term_definition),
        turn_metadata=TurnMetadata())
    eng.process_judge_output(turn_id="t2", judge_output=engaged,
                             candidate_utterance_text="What is an upsert?", elapsed_ms=2000)
    assert eng.queue_snapshot().questions[0].still_confused_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_still_confused_escalation.py -v`
Expected: FAIL — no escalation logic yet; the counter is still driven by the regex.

- [ ] **Step 3: Delete the regex helpers**

In `app/modules/interview_engine/state/engine.py`, delete the module-level constants and function: `_DONT_KNOW_HEAD_REGEX`, `_QUALIFYING_CONJUNCTIONS`, `_DONT_KNOW_MAX_LEN`, and `_is_dont_know_utterance(...)`. Also remove the now-unused `import re` if no other use remains (grep first: `grep -n "re\." app/modules/interview_engine/state/engine.py`).

- [ ] **Step 4: Add the stuck-cap escalation at the top of the clarify branch**

In `process_judge_output`, find `elif action == NextAction.clarify:` (currently a single line `instruction = InstructionKind.clarify`). Replace it with:

```python
        elif action == NextAction.clarify:
            still_confused = judge_output.turn_metadata.candidate_still_confused
            active_state = self._queue.active_state()
            prior_confused = active_state.still_confused_count if active_state else 0
            # Escalation: candidate has been generically confused on this
            # question twice already; the 3rd confusion (>= 2 prior) routes to
            # acknowledge-and-advance instead of a 3rd clarify (kills the
            # death-spiral). Two clarify attempts is the configured budget.
            if still_confused and prior_confused >= 2 and active_state is not None:
                warnings.append(ValidationWarning(
                    code="still_confused_cap_reached",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "still_confused_count": prior_confused,
                        "escalated_to": "acknowledge_and_advance",
                        "reason": (
                            "Candidate generically confused on this question "
                            "after 2 clarify attempts; acknowledging and moving "
                            "on rather than clarifying a 3rd time."
                        ),
                    },
                ))
                primary = self._primary_signal_value(active_state.question_id)
                instruction, is_post_acknowledge, closing_disclosure_signal = (
                    self._acknowledge_and_advance(
                        failed_signal_value=primary, elapsed_ms=elapsed_ms,
                        turn_id=turn_id, warnings=warnings,
                    )
                )
            else:
                instruction = InstructionKind.clarify
```

- [ ] **Step 5: Add the `_primary_signal_value` helper**

Add this method to `StateEngine` (next to `_acknowledge_and_advance`). It mirrors the primary-signal logic already inside `_maybe_promote_meta_confession` (highest-weight signal among the question's `signal_values`):

```python
    def _primary_signal_value(self, question_id: str) -> str:
        """Highest-weight signal among the question's signal_values.

        Falls back to the first signal_value, or empty string if none. Used by
        the still-confused escalation to pick which signal to mark failed."""
        q_cfg = next(
            (q for q in self._cfg.stage.questions if q.id == question_id), None,
        )
        if q_cfg is None or not q_cfg.signal_values:
            return ""
        meta = [m for m in self._cfg.signal_metadata if m.value in q_cfg.signal_values]
        if not meta:
            return q_cfg.signal_values[0]
        return max(meta, key=lambda s: s.weight).value
```

- [ ] **Step 6: Replace the counter increment/reset block ("4a")**

In `process_judge_output`, find the "4a" block (currently uses `_is_dont_know_utterance`). It runs early, before the action dispatch. Move the counter update to AFTER the action is known so it can key off the resolved flag, and drive it off `candidate_still_confused`. Delete the old "4a" block and instead, just before the final `_build_speaker_input(...)` call, add:

```python
        # Track consecutive still-confused turns on the active question.
        # Driven by the Judge flag (LLM classification), not a regex. Reset on
        # any turn that is not a still-confused clarify, and naturally reset
        # when the question advances (the new active question starts at 0).
        if self._queue.active_state() is not None:
            if (
                action == NextAction.clarify
                and judge_output.turn_metadata.candidate_still_confused
                and instruction == InstructionKind.clarify
            ):
                self._queue.increment_active_still_confused_count()
            else:
                self._queue.reset_active_still_confused_count()
```

(The `instruction == clarify` guard ensures the count does NOT increment on the escalation turn — that turn resolves to `deliver_question`, which should reset, not bump.)

- [ ] **Step 7: Replace the 3 obsolete dont_know tests**

In `tests/interview_engine/state/test_engine.py`, delete `test_dont_know_regex_matches_common_phrasings`, `test_dont_know_regex_does_not_match_substantive_answers`, and `test_dont_know_count_increments_on_match` (they test the deleted regex). Keep `test_dont_know_count_resets_on_substantive_answer` and `test_dont_know_count_threaded_into_orchestrator_judge_input` ONLY IF you adapt them to drive off `candidate_still_confused` and the renamed field; otherwise delete them too (the new behavior is covered by `test_still_confused_escalation.py`). Simplest: delete all five and rely on the new file.

- [ ] **Step 8: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_still_confused_escalation.py tests/interview_engine/state/test_engine.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/modules/interview_engine/state/engine.py tests/interview_engine/state/test_still_confused_escalation.py tests/interview_engine/state/test_engine.py
git commit -m "feat(interview-engine): replace dont-know regex with candidate_still_confused flag + 2-attempt cap"
```

---

## Task A8: Judge prompt — drop dont-know rules, add `candidate_still_confused` semantics

**Files:**
- Modify: `prompts/v2/engine/judge.system.txt`
- Manual verification + the loadable test.

- [ ] **Step 1: Remove the consecutive-dont-know escalation rules**

In `prompts/v2/engine/judge.system.txt`:
- In §1.3 CLARIFY, delete the sentence: *"The consecutive_dont_know_count >= 1 escalation still applies regardless of clarify_kind — at that count, emit acknowledge_no_experience."*
- In §2 INPUT FIELDS, replace the `active_question_consecutive_dont_know_count` bullet with:

```
- `active_question_still_confused_count` — number of consecutive turns on
  this question where you flagged candidate_still_confused. The State Engine
  acknowledges and moves on automatically once this reaches 2; you do NOT
  decide that escalation.
```

- In §4 CLARIFY, delete the "Hard rule: if active_question_consecutive_dont_know_count >= 1, do NOT emit clarify again..." line.
- In §4 ACKNOWLEDGE_NO_EXPERIENCE, delete the entire "CONSECUTIVE-DON'T-KNOW ESCALATION (load-bearing)" paragraph and the "FIRST 'I don't know' disambiguation (uses signal_metadata.type)" paragraph that references the count. Keep the explicit-no-experience definition (the "I've never used X" case is still a real ack trigger).

- [ ] **Step 2: Add the `candidate_still_confused` flag rule**

In §1 CLARIFY (sub-case d, broad_rephrase) and in the §4 CLARIFY entry, add:

```
SET turn_metadata.candidate_still_confused = true WHEN the candidate's
confusion is GENERIC and they cannot engage with the question as posed
("I don't understand", "I still don't get it", "I didn't quite follow")
— typically clarify_kind = broad_rephrase or probe_context. Do NOT set it
for ENGAGED, specific clarifies (term_definition, use_case_anchor,
role_context, concept_explanation) — those are productive questions, not
stuckness. The flag only pairs with next_action = clarify (validator-enforced).
The State Engine counts consecutive still-confused turns and, after 2,
acknowledges and moves the candidate to the next question for you.
```

- [ ] **Step 3: Verify prompt loads + grep for stragglers**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v`
Run: `grep -n "consecutive_dont_know\|dont_know" prompts/v2/engine/judge.system.txt` → expected: zero matches.

- [ ] **Step 4: Commit**

```bash
git add prompts/v2/engine/judge.system.txt
git commit -m "feat(interview-engine): judge prompt uses candidate_still_confused, drops dont-know rules"
```

---

## Task A9: Update Judge eval fixtures referencing the dont-know scenario

**Files:**
- Modify: `tests/interview_engine/judge/eval/fixtures/007_dont_know_followup_meta.json`, `024_recovery_after_meta_confession.json`, `026_dont_understand_question_clarify.json`, `029_retry_after_repeat_clarify.json` (any whose EXPECTED output asserts the old escalation behavior).

- [ ] **Step 1: Inspect the affected fixtures**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge -m prompt_quality -v` (the eval harness). For each fixture whose expected `next_action` / `turn_metadata` encoded the old "escalate to acknowledge_no_experience on 2nd dont-know" behavior, update the expected output to the new contract: a generically-confused candidate → `clarify` with `candidate_still_confused=true` (escalation is now the State Engine's job, not the Judge's).

- [ ] **Step 2: Update each fixture's expected block**

For `026_dont_understand_question_clarify.json` (and similar), ensure the expected output is:

```json
{
  "next_action": "clarify",
  "next_action_payload": {"kind": "clarify", "clarify_kind": "broad_rephrase"},
  "turn_metadata": {"candidate_still_confused": true}
}
```

(Match each fixture's existing JSON shape; only adjust `next_action` / `clarify_kind` / `turn_metadata` to the new contract. Do NOT invent new fixtures.)

- [ ] **Step 3: Run the eval harness**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge -m prompt_quality -v`
Expected: PASS (or within the harness's allowed tolerance). Note: prompt-quality evals hit the live LLM — run only when intentionally validating prompt behavior.

- [ ] **Step 4: Commit**

```bash
git add tests/interview_engine/judge/eval/fixtures/
git commit -m "test(interview-engine): update judge eval fixtures for candidate_still_confused contract"
```

---

## Task A10: Full regression + manual end-to-end smoke

- [ ] **Step 1: Run the full deterministic engine suite**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -m "not prompt_quality" -v`
Expected: PASS.

- [ ] **Step 2: Type-check the touched module**

Run: `docker compose run --rm nexus mypy app/modules/interview_engine/`
Expected: no new errors.

- [ ] **Step 3: Manual end-to-end session**

Run a live `docker compose up` session and reproduce the `26c2efc3` flow: answer Q1 well, then on the technical Q2 keep saying "I don't quite understand" / "I don't know how to do that." Verify:
- After 2 confused clarifies, the agent acknowledges and moves to a different question IN ONE TURN (no "let me ask something different" → silence → re-ask the same question).
- The agent does NOT loop the same broad_rephrase paragraph twice.

- [ ] **Step 4: Final commit (if any fixups)**

```bash
git add -A
git commit -m "fix(interview-engine): conversational-repair regression fixups"
```

---

## Self-Review checklist (run before handing off)

- **Spec coverage:** Option A (ack+advance) — Tasks A1–A4. Stuck detection (drop regex, Judge flag, 2-attempt cap) — Tasks A5–A9. ✓
- **Type consistency:** `is_post_acknowledge` (bool) used identically in A1/A2/A3/A4. `still_confused_count` (renamed) used identically in A6/A7. `_acknowledge_and_advance` returns `(InstructionKind, bool, str|None)` and is called the same way in A2/A3/A7. ✓
- **No placeholders:** every code/test step has concrete code. ✓
- **Open risk to watch during execution:** the knockout_policy override block (step "6") runs AFTER the ack/meta branches set `instruction`. For a `close_polite` tenant with a knockout signal, it forces `polite_close` and transitions lifecycle to closing; `_acknowledge_and_advance` guards `if state == "active"` before transitioning, so no double-transition. Verify in A3 Step 5.
