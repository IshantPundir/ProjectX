# Engine Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip the orchestrator's five layered race-condition mitigations, tune the existing Sarvam STT + MultilingualModel turn-detection stack for Indian-English candidates, harden the Judge fallback path, and add Judge/Speaker/State-Engine input audit logging.

**Architecture:** Pure turn-based interviewer. `on_user_turn_completed` runs a linear `state.snapshot → judge.call → state.process_judge_output → speaker.input → speaker.stream` pipeline with no early-return branches and no buffer state. STT stays Sarvam (`saaras:v3`, `en-IN`); turn detection stays `MultilingualModel`, with `unlikely_threshold` raised to `0.5` for more patient EOU. Judge `validation_error` falls back to `clarify` (no queue mutation) instead of force-advancing.

**Tech Stack:** Python 3.13, FastAPI, LiveKit Agents SDK (Python), Sarvam STT plugin, MultilingualModel turn detector, Pydantic v2, asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`

---

## Phase ordering note

The plan has six phases. Phases 1-3 (logging, Judge fallback, State Engine inverse_quality_gate) are independent; Phases 4-5 (orchestrator strip + Flux cutover) are load-bearing for each other. Phase 6 wraps up docs + verification. Run in numerical order; each phase commits atomically so a partial run leaves the codebase in a working state.

The plan is implemented inside `backend/nexus/`. All paths below are relative to that directory unless prefixed with `docs/`.

---

## Phase 1 — Audit logging additions

Adds three new audit affordances. Independent of the strip-down; ships value alone.

### Task 1: Register two new event-kind constants

**Files:**
- Modify: `app/modules/interview_engine/event_kinds.py`

- [ ] **Step 1: Add SPEAKER_INPUT and STATE_SNAPSHOT constants**

Edit `app/modules/interview_engine/event_kinds.py`. After the line `SPEAKER_OUTPUT = "speaker.output"` add `SPEAKER_INPUT = "speaker.input"`. After the line `LIFECYCLE_TRANSITION = "lifecycle.transition"` add `STATE_SNAPSHOT = "state.snapshot"`.

Then add both constants to the `ALL_EVENT_KINDS` frozenset (anywhere — alphabetical-ish is fine).

- [ ] **Step 2: Run the registry consistency test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_event_kinds.py -v`

Expected: PASS. The test asserts no duplicates and that every constant defined in the module is also in `ALL_EVENT_KINDS`. If it fails saying SPEAKER_INPUT or STATE_SNAPSHOT is missing from the registry, add them.

- [ ] **Step 3: Commit**

```bash
git add app/modules/interview_engine/event_kinds.py
git commit -m "feat(engine/event_kinds): register speaker.input and state.snapshot kinds"
```

### Task 2: Add SpeakerInputPayload and StateSnapshotPayload models

**Files:**
- Modify: `app/modules/interview_engine/audit_events.py`
- Test: `tests/interview_engine/test_audit_events.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/test_audit_events.py`:

```python
def test_speaker_input_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import SpeakerInputPayload

    payload = SpeakerInputPayload(
        turn_id="t-1",
        speaker_input={
            "instruction_kind": "deliver_question",
            "bank_text": "tell me about a time you scaled a service",
            "persona_name": "Punar",
        },
    )
    dumped = payload.model_dump()
    assert dumped["turn_id"] == "t-1"
    assert dumped["speaker_input"]["instruction_kind"] == "deliver_question"


def test_state_snapshot_payload_round_trip() -> None:
    from app.modules.interview_engine.audit_events import StateSnapshotPayload

    payload = StateSnapshotPayload(
        turn_id="t-1",
        ledger={"snapshots": {}, "entries": [], "next_seq": 1},
        queue={"questions": [], "active_index": None},
        claims={"entries": []},
        lifecycle={"state": "active", "knockout_failures": [],
                   "time_budget_total_seconds": 1800.0,
                   "time_elapsed_seconds": 0.0, "last_outcome": None},
    )
    assert payload.model_dump()["lifecycle"]["state"] == "active"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_audit_events.py::test_speaker_input_payload_round_trip tests/interview_engine/test_audit_events.py::test_state_snapshot_payload_round_trip -v`

Expected: FAIL with `ImportError` (classes don't exist yet).

- [ ] **Step 3: Add the two payload models**

Edit `app/modules/interview_engine/audit_events.py`. After the existing `SpeakerOutputPayload` class, add:

```python
class SpeakerInputPayload(BaseModel):
    """Audit payload capturing what the Speaker LLM saw on this turn.

    Lets replay tools reproduce the exact prompt + payload Speaker received
    and verify the anti-leak invariants (no rubric / anchors / coverage in
    the payload). The ``speaker_input`` dict is the model_dump of
    ``SpeakerInput`` — kept loose-typed so adding a SpeakerInput field
    later doesn't require a schema migration here.
    """
    turn_id: str
    speaker_input: dict[str, Any]


class StateSnapshotPayload(BaseModel):
    """Audit payload capturing State Engine snapshots BEFORE process_judge_output.

    With this, replay tools can deterministically reconstruct any turn's
    inputs to the State Engine. The four fields are the model_dump of
    ``ledger_snapshot()``, ``queue_snapshot()``, ``claims_snapshot()``,
    ``lifecycle_snapshot()`` — kept loose-typed for the same reason as
    SpeakerInputPayload.
    """
    turn_id: str
    ledger: dict[str, Any]
    queue: dict[str, Any]
    claims: dict[str, Any]
    lifecycle: dict[str, Any]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_audit_events.py::test_speaker_input_payload_round_trip tests/interview_engine/test_audit_events.py::test_state_snapshot_payload_round_trip -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/audit_events.py tests/interview_engine/test_audit_events.py
git commit -m "feat(engine/audit_events): add SpeakerInputPayload + StateSnapshotPayload"
```

### Task 3: Mark new kinds as redaction-passthrough

**Files:**
- Modify: `app/modules/interview_engine/event_log/redaction.py`

- [ ] **Step 1: Add to passthrough set**

Edit `app/modules/interview_engine/event_log/redaction.py`. In `_ENGINE_PASSTHROUGH_KINDS`, add `"speaker.input"` and `"state.snapshot"` entries (anywhere in the frozenset).

- [ ] **Step 2: Verify redaction tests still pass**

Run: `docker compose run --rm nexus pytest tests/interview_engine/event_log/ -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/modules/interview_engine/event_log/redaction.py
git commit -m "feat(engine/redaction): pass speaker.input and state.snapshot through"
```

### Task 4: Populate `judge.call.input_summary` with full JudgeInputPayload

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py:685-905` (the `on_user_turn_completed` method that builds judge_input) and `:1497-1516` (the `_append_judge_event` method)
- Test: `tests/interview_engine/test_orchestrator_composition.py`

- [ ] **Step 1: Find the existing judge_input plumbing**

The orchestrator builds `judge_input = build_judge_input(...)` at around line 841, then calls `result = await self._judge.call(input_payload=judge_input, ...)` at around line 853, then `self._append_judge_event(turn_id=turn_id, result=result)` at line 858. The `_append_judge_event` at line 1497 currently hardcodes `input_summary={}`.

- [ ] **Step 2: Change `_append_judge_event` to accept the input payload**

In `app/modules/interview_engine/orchestrator.py`, replace the existing `_append_judge_event` method (around lines 1497-1516) with:

```python
def _append_judge_event(
    self, *, turn_id: str, result: Any, input_payload: Any,
) -> None:
    """Emit JUDGE_FALLBACK or JUDGE_CALL with the full input payload.

    ``input_payload`` is the JudgeInputPayload that was sent to the
    LLM — its ``model_dump(mode='json')`` populates ``input_summary``
    so replay tools can reproduce why the Judge made a given decision.
    """
    from app.modules.interview_engine.event_kinds import JUDGE_CALL, JUDGE_FALLBACK
    from app.modules.interview_engine.audit_events import (
        JudgeCallPayload, JudgeFallbackPayload,
    )
    if result.is_fallback:
        self._append(JUDGE_FALLBACK, JudgeFallbackPayload(
            turn_id=turn_id, reason=result.fallback_reason.value,
            original_failure_context=result.original_failure_context or {},
            synthesized_output=result.judge_output.model_dump(mode="json"),
        ).model_dump())
    else:
        self._append(JUDGE_CALL, JudgeCallPayload(
            turn_id=turn_id, model=result.model_used,
            prompt_hash="sha256:judge",
            input_summary=input_payload.model_dump(mode="json"),
            output=result.judge_output.model_dump(mode="json"),
            latency_ms=result.latency_ms,
            usage=result.usage,
        ).model_dump())
```

- [ ] **Step 3: Update the call site to pass input_payload**

In `app/modules/interview_engine/orchestrator.py` around line 858, change:

```python
self._append_judge_event(turn_id=turn_id, result=result)
```

to:

```python
self._append_judge_event(turn_id=turn_id, result=result, input_payload=judge_input)
```

- [ ] **Step 4: Run the existing composition test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_orchestrator_composition.py -v`

Expected: PASS (no behavior change beyond the audit payload contents).

- [ ] **Step 5: Add a test that input_summary is populated**

Append to `tests/interview_engine/test_orchestrator_composition.py` (or create the file if it would only be a test for this one assertion — check what's there first):

```python
def test_judge_call_audit_carries_full_input_summary(
    minimal_orchestrator,  # use existing fixture from conftest
) -> None:
    """Run one turn end-to-end and assert the judge.call audit event
    contains a non-empty input_summary that reflects the JudgeInputPayload.
    """
    # ... use the existing fixture pattern in this file. The fixture
    # should already mock Judge to return a synthetic JudgeOutput.
    # After driving on_user_turn_completed once with candidate_text
    # "I led a team of five engineers", inspect the collector events:
    judge_call_events = [
        e for e in minimal_orchestrator._collector.events
        if e.kind == "judge.call"
    ]
    assert len(judge_call_events) == 1
    payload = judge_call_events[0].payload
    assert payload["input_summary"] != {}
    assert "candidate_utterance" in payload["input_summary"]
    assert payload["input_summary"]["candidate_utterance"] == \
        "I led a team of five engineers"
```

If the fixture pattern doesn't exist or differs in this file, look at how existing composition tests construct an orchestrator (check `tests/interview_engine/conftest.py`) and adapt.

- [ ] **Step 6: Run the new test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_orchestrator_composition.py::test_judge_call_audit_carries_full_input_summary -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py tests/interview_engine/test_orchestrator_composition.py
git commit -m "feat(engine/orchestrator): populate judge.call.input_summary with JudgeInputPayload"
```

### Task 5: Emit `state.snapshot` audit event before process_judge_output

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py` (insert call inside `on_user_turn_completed`, add `_append_state_snapshot` method)

- [ ] **Step 1: Add the helper method**

In `app/modules/interview_engine/orchestrator.py`, after `_append_judge_event` (around line 1516), add:

```python
def _append_state_snapshot(self, *, turn_id: str) -> None:
    """Emit a state.snapshot audit event capturing State Engine state
    BEFORE process_judge_output mutates it.

    Lets replay tools reconstruct any turn's input state to the State
    Engine: the queue (active question, push_back/dont_know counts,
    probes_remaining_ids), the ledger (per-signal coverage), the
    claims pool, and the lifecycle (state, knockout_failures, time
    remaining).
    """
    from app.modules.interview_engine.event_kinds import STATE_SNAPSHOT
    from app.modules.interview_engine.audit_events import StateSnapshotPayload
    self._append(STATE_SNAPSHOT, StateSnapshotPayload(
        turn_id=turn_id,
        ledger=self._state.ledger_snapshot().model_dump(mode="json"),
        queue=self._state.queue_snapshot().model_dump(mode="json"),
        claims=self._state.claims_snapshot().model_dump(mode="json"),
        lifecycle=self._state.lifecycle_snapshot().model_dump(mode="json"),
    ).model_dump())
```

- [ ] **Step 2: Call it in `on_user_turn_completed`**

In `on_user_turn_completed` (around line 777, immediately AFTER the `self._append(TURN_STARTED, ...)` call), insert:

```python
self._append_state_snapshot(turn_id=turn_id)
```

The ordering is: `turn.started → state.snapshot → build_judge_input → judge.call → process_judge_output`.

- [ ] **Step 3: Add test**

Append to `tests/interview_engine/test_orchestrator_composition.py`:

```python
def test_state_snapshot_emitted_before_judge_call(
    minimal_orchestrator,
) -> None:
    """Drive one turn; assert state.snapshot event appears before
    judge.call in the collector sequence and contains the four
    expected sub-snapshots."""
    # ... drive on_user_turn_completed once via the fixture ...
    events = minimal_orchestrator._collector.events
    state_idx = next(i for i, e in enumerate(events) if e.kind == "state.snapshot")
    judge_idx = next(i for i, e in enumerate(events) if e.kind == "judge.call")
    assert state_idx < judge_idx
    snapshot = events[state_idx].payload
    assert set(snapshot.keys()) >= {"turn_id", "ledger", "queue", "claims", "lifecycle"}
    assert snapshot["lifecycle"]["state"] in ("pre_start", "active", "closing", "closed")
```

- [ ] **Step 4: Run the test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_orchestrator_composition.py::test_state_snapshot_emitted_before_judge_call -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py tests/interview_engine/test_orchestrator_composition.py
git commit -m "feat(engine/orchestrator): emit state.snapshot before process_judge_output"
```

### Task 6: Emit `speaker.input` audit event before _stream_speaker_and_say

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`

- [ ] **Step 1: Add the helper method**

In `app/modules/interview_engine/orchestrator.py`, after `_append_state_snapshot`, add:

```python
def _append_speaker_input(
    self, *, turn_id: str, speaker_input: Any,
) -> None:
    """Emit a speaker.input audit event capturing exactly what the
    Speaker LLM is about to receive.

    Lets us audit anti-leak after-the-fact (no rubric / anchors /
    coverage / signal_metadata in the payload) and reproduce why the
    Speaker said what it said.
    """
    from app.modules.interview_engine.event_kinds import SPEAKER_INPUT
    from app.modules.interview_engine.audit_events import SpeakerInputPayload
    self._append(SPEAKER_INPUT, SpeakerInputPayload(
        turn_id=turn_id,
        speaker_input=speaker_input.model_dump(mode="json"),
    ).model_dump())
```

- [ ] **Step 2: Call it in `on_user_turn_completed` and `on_enter`**

In `on_user_turn_completed`, just BEFORE the final `outcome = await self._stream_speaker_and_say(agent=agent, turn_id=turn_id, speaker_input=decision.speaker_input)` line (currently around line 936), insert:

```python
self._append_speaker_input(turn_id=turn_id, speaker_input=decision.speaker_input)
```

In `on_enter`, just BEFORE `await self._stream_speaker_and_say(agent=agent, turn_id=turn_id, speaker_input=decision.speaker_input)` (currently around line 671), insert the same line.

In the `repeat` branch of `on_user_turn_completed` (around line 912-934), DO NOT add this — repeat replays the cached utterance and bypasses the Speaker LLM entirely. The existing `SPEAKER_CACHED` audit event already covers that path.

- [ ] **Step 3: Add test**

Append to `tests/interview_engine/test_orchestrator_composition.py`:

```python
def test_speaker_input_emitted_before_speaker_call(
    minimal_orchestrator,
) -> None:
    """Drive one turn; assert speaker.input event appears before
    speaker.call in the collector sequence and contains the
    instruction_kind / bank_text shape."""
    # ... drive on_user_turn_completed once via the fixture ...
    events = minimal_orchestrator._collector.events
    speaker_input_idx = next(
        (i for i, e in enumerate(events) if e.kind == "speaker.input"),
        None,
    )
    assert speaker_input_idx is not None, "speaker.input was not emitted"
    speaker_call_idx = next(
        i for i, e in enumerate(events) if e.kind == "speaker.call"
    )
    assert speaker_input_idx < speaker_call_idx
    payload = events[speaker_input_idx].payload
    assert "speaker_input" in payload
    assert "instruction_kind" in payload["speaker_input"]
```

- [ ] **Step 4: Run the test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_orchestrator_composition.py::test_speaker_input_emitted_before_speaker_call -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py tests/interview_engine/test_orchestrator_composition.py
git commit -m "feat(engine/orchestrator): emit speaker.input before speaker.stream"
```

---

## Phase 2 — Judge fallback hardening

Three small surgical changes that together prevent the Judge model's quirks from force-walking the question bank.

### Task 7: Soften the push_back+quality validator in JudgeOutput

**Files:**
- Modify: `app/modules/interview_engine/models/judge.py`
- Test: `tests/interview_engine/judge/test_judge_output_validation.py` (likely exists; check first)

- [ ] **Step 1: Inspect the current validator**

In `app/modules/interview_engine/models/judge.py` around lines 226-265, the `_check_push_back_alignment` validator raises ValueError when `push_back` is paired with non-thin observations.

- [ ] **Step 2: Soften the validator**

Replace the body of `_check_push_back_alignment` (lines 248-265) with:

```python
@model_validator(mode="after")
def _check_push_back_alignment(self) -> "JudgeOutput":
    """Coupling between push_back action and observation quality.

    Two consistency rules tied to push_back:

    1. push_back is incompatible with no-experience disclosure.
       Acknowledge or polite_close, never push_back. **STILL STRICT** —
       this is structural and the State Engine cannot recover.

    2. Observations emitted alongside push_back ideally carry
       ``quality=thin``. Newer Judge models occasionally emit
       ``concrete``/``strong`` paired with ``push_back`` when the
       answer is on-topic but the model still wants more depth. The
       Pydantic validator no longer raises on this case — the State
       Engine's ``inverse_quality_gate`` handles it (see
       state/engine.py: push_back path) by downgrading to ``probe`` (or
       ``advance`` if probes exhausted) in-place. Raising here used to
       trigger the validation_error fallback path and force-advance the
       queue (root cause of the early-end bug observed 2026-05-12).
    """
    if self.next_action != NextAction.push_back:
        return self
    if self.turn_metadata.candidate_disclosed_no_experience:
        raise ValueError(
            "push_back is incompatible with "
            "candidate_disclosed_no_experience=true; use "
            "acknowledge_no_experience instead."
        )
    return self
```

- [ ] **Step 3: Find existing tests for this validator**

Run: `grep -rln "push_back requires quality" tests/ app/`

Expected: matches in the production validator (now softened) and possibly in test files. If a test asserts that `push_back+concrete` raises ValidationError, that test now needs updating (the validator no longer raises). Find and update those tests.

Specifically, look for tests in `tests/interview_engine/` that construct a `JudgeOutput` with `next_action=push_back` + a `concrete` observation and expect a ValidationError. Replace `with pytest.raises(ValidationError):` with `JudgeOutput.model_validate(...)` and assert it succeeds.

- [ ] **Step 4: Add a new test asserting the soft path**

Append to `tests/interview_engine/judge/test_judge_output_validation.py` (create the file with proper imports if it doesn't exist):

```python
def test_push_back_with_concrete_observation_does_not_raise() -> None:
    """Regression test for the 2026-05-12 force-advance bug.

    The validator must NOT raise when push_back is paired with a
    concrete observation. The State Engine's inverse_quality_gate
    handles this case by downgrading to probe.
    """
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, Observation,
        CoverageQuality, CoverageTransition,
    )

    # This used to raise ValidationError; must succeed now.
    output = JudgeOutput.model_validate({
        "observations": [{
            "signal_value": "react_experience",
            "anchor_id": 0,
            "evidence_quote": "I built an enterprise operations platform",
            "coverage_transition": "none→partial",
            "quality": "concrete",
        }],
        "candidate_claims": [],
        "next_action": "push_back",
        "next_action_payload": {"kind": "push_back", "reason_code": "missing_specifics"},
        "turn_metadata": {},
    })
    assert output.next_action == NextAction.push_back
    assert output.observations[0].quality == CoverageQuality.concrete


def test_push_back_with_no_experience_disclosure_still_raises() -> None:
    """The structural rule (push_back vs no-experience) stays strict."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        from app.modules.interview_engine.models.judge import JudgeOutput
        JudgeOutput.model_validate({
            "observations": [],
            "candidate_claims": [],
            "next_action": "push_back",
            "next_action_payload": {"kind": "push_back", "reason_code": "vague_answer"},
            "turn_metadata": {"candidate_disclosed_no_experience": True},
        })
```

- [ ] **Step 5: Run the new tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_output_validation.py -v`

Expected: PASS.

- [ ] **Step 6: Run the broader judge test suite to catch regressions**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/ -v`

Expected: PASS. If a previously-passing test now fails because it expected the validator to raise on push_back+concrete, update that test (per Step 3) — that's the intended behavior change.

- [ ] **Step 7: Commit**

```bash
git add app/modules/interview_engine/models/judge.py tests/interview_engine/judge/
git commit -m "feat(engine/judge): soften push_back+concrete validator; State Engine handles inversion"
```

### Task 8: Switch synthesize_fallback to clarify on validation_error

**Files:**
- Modify: `app/modules/interview_engine/judge/fallback.py`
- Test: `tests/interview_engine/judge/test_fallback.py` (check exists first)

- [ ] **Step 1: Write failing test**

Create or append to `tests/interview_engine/judge/test_fallback.py`:

```python
def test_validation_error_synthesizes_clarify_not_advance() -> None:
    """Regression test for force-advance bug: validation_error must
    synthesize a clarify (no queue mutation), not an advance."""
    from app.modules.interview_engine.judge.fallback import (
        FallbackReason, synthesize_fallback,
    )
    from app.modules.interview_engine.models.judge import (
        NextAction, ClarifyPayload,
    )

    output = synthesize_fallback(
        reason=FallbackReason.validation_error,
        next_pending_mandatory_id="q-2",
    )
    assert output.next_action == NextAction.clarify
    assert isinstance(output.next_action_payload, ClarifyPayload)
    assert output.observations == []
    assert output.candidate_claims == []


def test_timeout_still_synthesizes_advance() -> None:
    """Sanity: non-validation_error reasons keep their existing
    advance-or-polite_close behavior."""
    from app.modules.interview_engine.judge.fallback import (
        FallbackReason, synthesize_fallback,
    )
    from app.modules.interview_engine.models.judge import (
        NextAction, AdvancePayload, PoliteClosePayload,
    )

    out = synthesize_fallback(
        reason=FallbackReason.timeout, next_pending_mandatory_id="q-3",
    )
    assert out.next_action == NextAction.advance
    assert isinstance(out.next_action_payload, AdvancePayload)
    assert out.next_action_payload.target_question_id == "q-3"

    out2 = synthesize_fallback(
        reason=FallbackReason.timeout, next_pending_mandatory_id=None,
    )
    assert out2.next_action == NextAction.polite_close
    assert isinstance(out2.next_action_payload, PoliteClosePayload)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_fallback.py::test_validation_error_synthesizes_clarify_not_advance -v`

Expected: FAIL — `synthesize_fallback(reason=validation_error, ...)` currently returns advance/polite_close, not clarify.

- [ ] **Step 3: Modify synthesize_fallback**

In `app/modules/interview_engine/judge/fallback.py`, replace the body of `synthesize_fallback` (currently the whole function after the docstring) with:

```python
def synthesize_fallback(
    *,
    reason: FallbackReason,
    next_pending_mandatory_id: str | None,
) -> JudgeOutput:
    # Phase 9.5 (2026-05-12): validation_error synthesizes clarify
    # instead of advance. The original behavior force-walked the queue
    # whenever the Judge model produced a malformed output (e.g. the
    # push_back+concrete cross-field combo); this killed the interview
    # early. Clarify is no-op on the queue and asks the candidate to
    # elaborate — better fallback for "model produced something but it
    # didn't validate" than skipping the question entirely.
    if reason == FallbackReason.validation_error:
        from app.modules.interview_engine.models.judge import ClarifyPayload
        return JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
            turn_metadata=TurnMetadata(),
        )
    if next_pending_mandatory_id is None:
        return JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.polite_close,
            next_action_payload=PoliteClosePayload(),
            turn_metadata=TurnMetadata(),
        )
    return JudgeOutput(
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(
            target_question_id=next_pending_mandatory_id,
        ),
        turn_metadata=TurnMetadata(),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/interview_engine/judge/test_fallback.py -v`

Expected: PASS for all three tests.

- [ ] **Step 5: Commit**

```bash
git add app/modules/interview_engine/judge/fallback.py tests/interview_engine/judge/test_fallback.py
git commit -m "feat(engine/judge): validation_error synthesizes clarify instead of advance"
```

---

## Phase 3 — State Engine inverse_quality_gate

The deterministic backstop for push_back+concrete output. Mirror of the existing `quality_gated_advance` pattern.

### Task 9: Add `inverse_quality_gate` to StateEngine.process_judge_output

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py:527-571` (the `elif action == NextAction.push_back:` branch)
- Test: `tests/interview_engine/state/test_inverse_quality_gate.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/interview_engine/state/test_inverse_quality_gate.py`:

```python
"""Inverse quality gate — push_back + concrete/strong observation
downgrades to probe (or advance if probes exhausted).

Mirror of state/engine.py's existing quality_gated_advance (advance +
all-thin → push_back). Closes the gap that previously caused the
JudgeOutput validator to raise ValidationError → fallback synthesizes
advance → question bank force-walked.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def session_config_two_questions(make_session_config):
    """Use the existing make_session_config fixture from conftest.

    Builds a SessionConfig with 2 mandatory questions, each with 2
    follow-up probes. Adapt to whatever the existing conftest exposes.
    """
    return make_session_config(
        n_questions=2,
        follow_ups_per_question=2,
        mandatory=True,
    )


def test_push_back_with_concrete_obs_downgrades_to_probe(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + concrete → State Engine consumes the
    next probe instead of incrementing push_back_count."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    # Advance to the first question (synthesizes the session-start
    # JudgeOutput then applies it).
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    judge_output = JudgeOutput(
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="I built an enterprise operations platform",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-1", judge_output=judge_output,
        candidate_utterance_text="long substantive answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    # push_back_count should NOT have been incremented (we downgraded)
    queue = engine.queue_snapshot()
    assert queue.questions[0].push_back_count == 0
    # warning recorded
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" in codes


def test_push_back_with_thin_obs_keeps_push_back(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + thin → push_back fires normally,
    push_back_count increments."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    judge_output = JudgeOutput(
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="I would add validation",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.thin,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="vague_answer"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-1", judge_output=judge_output,
        candidate_utterance_text="thin answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.push_back
    queue = engine.queue_snapshot()
    assert queue.questions[0].push_back_count == 1
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" not in codes


def test_push_back_concrete_no_probes_left_advances(
    session_config_two_questions,
) -> None:
    """Judge emits push_back + concrete and all probes consumed →
    falls back to advance to next pending mandatory."""
    from app.modules.interview_engine.state.engine import StateEngine
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, ProbePayload, Observation,
        CoverageQuality, CoverageTransition, TurnMetadata, AdvancePayload,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    engine = StateEngine(session_config=session_config_two_questions)
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t-0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Consume both probes via two probe actions so probes_remaining_ids is empty.
    for i, probe_id in enumerate(["0", "1"], start=1):
        engine.process_judge_output(
            turn_id=f"t-{i}",
            judge_output=JudgeOutput(
                observations=[],
                candidate_claims=[],
                next_action=NextAction.probe,
                next_action_payload=ProbePayload(probe_id=probe_id),
                turn_metadata=TurnMetadata(),
            ),
            candidate_utterance_text="answer", elapsed_ms=i * 1000,
        )

    judge_output = JudgeOutput(
        observations=[Observation(
            signal_value=session_config_two_questions.signal_metadata[0].value,
            anchor_id=0,
            evidence_quote="concrete claim",
            coverage_transition=CoverageTransition.partial_to_partial,
            quality=CoverageQuality.concrete,
        )],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    decision = engine.process_judge_output(
        turn_id="t-3", judge_output=judge_output,
        candidate_utterance_text="another answer", elapsed_ms=4000,
    )
    # Probes exhausted → fallback advance picks deliver_question on next q.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    codes = [w.code for w in decision.validation_warnings]
    assert "inverse_quality_gate" in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_inverse_quality_gate.py -v`

Expected: FAIL — `inverse_quality_gate` is not yet implemented.

- [ ] **Step 3: Implement inverse_quality_gate in StateEngine**

In `app/modules/interview_engine/state/engine.py`, in `process_judge_output`, find the `elif action == NextAction.push_back:` branch (around lines 527-571). Insert the new gate at the TOP of that branch, BEFORE the existing `current_count = self._queue.active_push_back_count()` check:

```python
elif action == NextAction.push_back:
    # Phase 9.5 (2026-05-12) — inverse quality gate. Mirror of
    # the existing quality_gated_advance check above. When the Judge
    # emits push_back paired with at least one `concrete`/`strong`
    # observation, the model is internally inconsistent: push_back
    # asks for more depth, but a concrete observation says depth
    # was already produced. Downgrade in-place rather than letting
    # this fall through to the (now-softened) JudgeOutput validator.
    has_concrete_obs = any(
        o.quality in (CoverageQuality.concrete, CoverageQuality.strong)
        for o in applied_observations
    )
    if has_concrete_obs and self._queue.active_state() is not None:
        active_q_state = self._queue.active_state()
        warnings.append(ValidationWarning(
            code="inverse_quality_gate",
            level="warning",
            details={
                "active_question_id": self._queue.active_question_id(),
                "original_action": "push_back",
                "downgraded_to": (
                    "deliver_probe"
                    if active_q_state.probes_remaining_ids
                    else "advance"
                ),
                "concrete_observations": [
                    {"signal": o.signal_value, "quality": o.quality.value}
                    for o in applied_observations
                    if o.quality in (CoverageQuality.concrete, CoverageQuality.strong)
                ],
                "reason": (
                    "push_back is incoherent when paired with concrete/strong "
                    "observations (model produced depth but asked for more). "
                    "Downgraded to probe (or advance if probes exhausted) to "
                    "honor the evidence the model already extracted."
                ),
            },
        ))
        if active_q_state.probes_remaining_ids:
            first_probe_id = active_q_state.probes_remaining_ids[0]
            self._queue.apply_probe(probe_id=first_probe_id, at_turn=self._turn_count)
            instruction = InstructionKind.deliver_probe
        else:
            instruction = self._fallback_advance_to_next_pending(warnings)
    else:
        # Fall through to the existing push_back path (cap check,
        # increment, etc.). The existing code below stays untouched.
        current_count = self._queue.active_push_back_count()
        if current_count >= 2 and self._queue.active_state() is not None:
            # ... existing cap logic ...
        elif self._queue.active_state() is None:
            # ... existing defensive branch ...
        else:
            self._queue.increment_active_push_back_count()
            instruction = InstructionKind.push_back
```

NOTE: The structural change is to wrap the existing push_back logic in an `else:` block under the new `if has_concrete_obs and self._queue.active_state() is not None:` check. Make sure the existing warnings + branches inside the else block stay intact — copy them verbatim from the current source rather than retyping.

- [ ] **Step 4: Run the new tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/test_inverse_quality_gate.py -v`

Expected: PASS for all three tests.

- [ ] **Step 5: Run the broader state-engine test suite**

Run: `docker compose run --rm nexus pytest tests/interview_engine/state/ tests/interview_engine/test_orchestrator.py -v`

Expected: PASS. If a previously-passing test fails because it expected push_back+concrete to keep the push_back action, that test is asserting the old behavior — update it.

- [ ] **Step 6: Commit**

```bash
git add app/modules/interview_engine/state/engine.py tests/interview_engine/state/test_inverse_quality_gate.py
git commit -m "feat(engine/state): inverse_quality_gate downgrades push_back+concrete to probe"
```

---

## Phase 4 — Orchestrator strip-down

Removes the five mitigation layers in three commits, one mechanism at a time, so each diff is reviewable independently.

### Task 10: Delete continuation coalescing

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py` (delete _PriorTurnSnapshot, _SpeakerStreamOutcome.body_started_wall_at, _CoalesceDecision, _should_coalesce, _COALESCIBLE_KINDS, _derive_sub_context, _capture_prior_turn_snapshot, _maybe_coalesce; remove `coalesce_enabled`/`coalesce_window_ms` from OrchestratorConfig; remove `self._last_turn` field; stop emitting TURN_COALESCED)
- Delete: `tests/interview_engine/test_orchestrator_coalescing.py`
- Modify: `app/config.py` (remove `engine_coalesce_enabled`, `engine_coalesce_window_ms` fields and validators)
- Modify: `app/modules/interview_engine/agent.py` (remove `coalesce_enabled=settings.engine_coalesce_enabled` and `coalesce_window_ms=settings.engine_coalesce_window_ms` from OrchestratorConfig construction)

- [ ] **Step 1: Find all coalescing references**

Run: `grep -rn "coalesce\|_PriorTurnSnapshot\|_should_coalesce\|_COALESCIBLE_KINDS\|_capture_prior_turn_snapshot\|_maybe_coalesce\|_derive_sub_context\|TURN_COALESCED\|TurnCoalescedPayload\|_last_turn\|body_started_wall_at\|_resumed_speaking_at\|_last_user_speech_end" app/modules/interview_engine/ app/config.py 2>&1 | head -80`

Expected: Many matches across orchestrator.py, agent.py, config.py. Use this list to drive deletions.

- [ ] **Step 2: Delete from orchestrator.py**

In `app/modules/interview_engine/orchestrator.py`:

- Delete the `_PriorTurnSnapshot` dataclass (lines around 147-169).
- In `_SpeakerStreamOutcome` (lines around 172-184), DELETE the `body_started_wall_at: float | None = None` field. Update any code that reads it.
- Delete `_CoalesceDecision` (lines around 236-239).
- Delete `_should_coalesce` (lines around 243-330) entirely.
- Delete `_COALESCIBLE_KINDS` (lines around 215-233).
- Delete `_derive_sub_context` (lines around 77-108).
- Delete `_capture_prior_turn_snapshot` method (search for the method definition).
- Delete `_maybe_coalesce` method (search for the method definition).
- In `OrchestratorConfig` (around line 333), delete the `coalesce_enabled` and `coalesce_window_ms` fields and their docstring lines.
- In `InterviewOrchestrator.__init__`, delete `self._last_turn: _PriorTurnSnapshot | None = None` (around line 402).
- In `on_user_turn_completed`, delete the call to `self._maybe_coalesce(...)` (around line 769) — replace with passing `candidate_text` straight through.
- In `on_user_turn_completed`, delete the call to `self._capture_prior_turn_snapshot(...)` (around line 959).
- In `_SpeakerStreamOutcome` construction sites, remove `body_started_wall_at` arguments.

- [ ] **Step 3: Delete the coalesce test file**

```bash
git rm tests/interview_engine/test_orchestrator_coalescing.py
```

- [ ] **Step 4: Strip from app/config.py**

In `app/config.py`, delete the `engine_coalesce_enabled` and `engine_coalesce_window_ms` fields and the `engine_coalesce_window_ms` validator (around lines 307-327). The block beginning with `# Continuation coalescing` and ending after the validator should be entirely removed.

- [ ] **Step 5: Strip from agent.py**

In `app/modules/interview_engine/agent.py` around line 398-409, remove `coalesce_enabled=settings.engine_coalesce_enabled` and `coalesce_window_ms=settings.engine_coalesce_window_ms` from the `OrchestratorConfig(...)` constructor call.

- [ ] **Step 6: Run remaining engine tests to catch anything I missed**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -v 2>&1 | tail -50`

Expected: PASS or specific import errors pointing at additional references to deleted symbols. Fix any leftover references — typical culprits are tests in `test_orchestrator.py` or `test_orchestrator_composition.py` that import `_PriorTurnSnapshot` or `_should_coalesce` for direct testing.

If a test asserts specific coalesce behavior (e.g. `test_orchestrator_composition.py::test_coalesce_path_*`), delete that test too.

- [ ] **Step 7: Run the linter**

Run: `docker compose run --rm nexus ruff check app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py`

Expected: PASS. Common issues: unused imports (e.g. `time` if `_capture_prior_turn_snapshot` was the only consumer of `time.monotonic()`), unused dataclass imports.

- [ ] **Step 8: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py tests/interview_engine/
git commit -m "refactor(engine): remove continuation coalescing (fired 0x in real sessions)"
```

### Task 11: Delete stale-turn drop-and-drain

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py` (delete `_buffer_dropped_text`, `_drain_stale_buffer`, `_is_stale_turn`, `_stale_buffer` field; remove drop+drain emissions; remove from on_user_turn_completed)
- Delete: `tests/interview_engine/test_orchestrator_stale_drop.py`
- Modify: `app/config.py` (remove `engine_stale_turn_threshold_ms`, `engine_stale_buffer_max` fields and validators)
- Modify: `app/modules/interview_engine/agent.py` (remove the matching OrchestratorConfig args)

- [ ] **Step 1: Delete from orchestrator.py**

In `app/modules/interview_engine/orchestrator.py`:

- Delete `_is_stale_turn` method (around line 527-560).
- Delete `_buffer_dropped_text` method (around line 581-606).
- Delete `_drain_stale_buffer` method (around line 608-635).
- In `__init__`, delete `self._stale_buffer: list[str] = []` (around line 422).
- In `OrchestratorConfig`, delete `stale_turn_threshold_ms` and `stale_buffer_max` fields.
- In `on_user_turn_completed`, delete the entire stale-turn check block (around lines 743-755) — the `if self._is_stale_turn(...): ... return` block.
- In `on_user_turn_completed`, delete the call to `self._drain_stale_buffer(...)` (around line 764-767) — replace with passing `candidate_text` straight through.
- In `observe_user_state` (around line 481-525), keep the AUDIO_USER_STATE audit emission only. Remove the `_last_user_speech_end_monotonic` and `_last_user_speech_end_wall` recording (those were consumed only by drop+drain). The method should be reduced to just the `audio.user.state` collector.append logic.
  - In `__init__`, also delete `self._last_user_speech_end_monotonic` and `self._last_user_speech_end_wall` fields (around lines 411-417).

- [ ] **Step 2: Delete the stale-drop test file**

```bash
git rm tests/interview_engine/test_orchestrator_stale_drop.py
```

- [ ] **Step 3: Strip from app/config.py**

Delete `engine_stale_turn_threshold_ms` and `engine_stale_buffer_max` fields and their validators (around lines 329-358).

- [ ] **Step 4: Strip from agent.py**

In `app/modules/interview_engine/agent.py`, remove `stale_turn_threshold_ms=settings.engine_stale_turn_threshold_ms` and `stale_buffer_max=settings.engine_stale_buffer_max` from the OrchestratorConfig call.

- [ ] **Step 5: Run tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -v 2>&1 | tail -30`

Expected: PASS or specific import errors. Fix any leftover references.

- [ ] **Step 6: Run linter**

Run: `docker compose run --rm nexus ruff check app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py tests/interview_engine/
git commit -m "refactor(engine): remove stale-turn drop-and-drain (Flux EOU eliminates need)"
```

### Task 12: Delete post-Judge resumption gate + must-deliver whitelist

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py` (delete `_user_resumed_speaking_after`, `_resumed_speaking_at` field, `_MUST_DELIVER_JUDGE_ACTIONS` set, the post-Judge gate block in `on_user_turn_completed`)
- Modify: `app/config.py` (remove `engine_post_judge_resumption_epsilon_ms` field + validator)
- Modify: `app/modules/interview_engine/agent.py` (remove the matching OrchestratorConfig arg)

- [ ] **Step 1: Delete from orchestrator.py**

In `app/modules/interview_engine/orchestrator.py`:

- Delete `_user_resumed_speaking_after` method (around line 562-579).
- Delete `_MUST_DELIVER_JUDGE_ACTIONS` frozenset (around line 203-208).
- In `__init__`, delete `self._resumed_speaking_at: float | None = None`.
- In `OrchestratorConfig`, delete `post_judge_resumption_epsilon_ms` field.
- In `on_user_turn_completed`, delete the entire post-Judge resumption gate block (around lines 860-900) — the `judge_action = result.judge_output.next_action / is_must_deliver = ... / if (not is_must_deliver and self._user_resumed_speaking_after(...)): ... return` block.
- Also delete the `original_callback_wall = time.time()` capture line at the top of `on_user_turn_completed` (around line 720).
- In `on_user_turn_completed`, delete the `current_user_stopped_speaking_at = getattr(...)` extraction (around lines 730-734) — was only consumed by drop+drain (Task 11) and pre-body coalesce (Task 10), both gone.

- [ ] **Step 2: Strip from app/config.py**

Delete `engine_post_judge_resumption_epsilon_ms` field and its validator (around lines 342-367).

- [ ] **Step 3: Strip from agent.py**

In `app/modules/interview_engine/agent.py`, remove `post_judge_resumption_epsilon_ms=settings.engine_post_judge_resumption_epsilon_ms` from the OrchestratorConfig call.

- [ ] **Step 4: Run tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -v 2>&1 | tail -30`

Expected: PASS. Some test files in `tests/interview_engine/test_orchestrator.py` may have asserted post-Judge gate behavior (e.g. `test_orchestrator_post_judge_*`). Delete those tests outright — the mechanism no longer exists.

- [ ] **Step 5: Inspect orchestrator.py size**

Run: `wc -l app/modules/interview_engine/orchestrator.py`

Expected: ~700 lines (down from 1,560). If it's still over 1,000 there's leftover dead code worth investigating.

- [ ] **Step 6: Run linter**

Run: `docker compose run --rm nexus ruff check app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/modules/interview_engine/orchestrator.py app/config.py app/modules/interview_engine/agent.py tests/interview_engine/
git commit -m "refactor(engine): remove post-Judge resumption gate + must-deliver whitelist"
```

### Task 13: Add the strip-regression AST test

**Files:**
- Create: `tests/interview_engine/test_orchestrator_strip.py`

- [ ] **Step 1: Write the test**

Create `tests/interview_engine/test_orchestrator_strip.py`:

```python
"""Regression gate: assert the deleted orchestrator-side mitigation
mechanisms are NOT re-introduced into orchestrator.py.

Each name below was deleted in 2026-05-12 simplification (PR ref). If
this test fails because someone re-added one of these, read the
2026-05-12-engine-simplification-design.md spec and discuss before
merging.
"""
from __future__ import annotations

import ast
import pathlib

ORCHESTRATOR_PATH = pathlib.Path(__file__).resolve().parents[2] / (
    "app/modules/interview_engine/orchestrator.py"
)

# These names were intentionally removed. Re-introducing any of them
# means we re-introduced a layered mitigation we agreed to delete.
FORBIDDEN_NAMES: frozenset[str] = frozenset({
    "_PriorTurnSnapshot",
    "_CoalesceDecision",
    "_should_coalesce",
    "_COALESCIBLE_KINDS",
    "_capture_prior_turn_snapshot",
    "_maybe_coalesce",
    "_derive_sub_context",
    "_is_stale_turn",
    "_buffer_dropped_text",
    "_drain_stale_buffer",
    "_user_resumed_speaking_after",
    "_MUST_DELIVER_JUDGE_ACTIONS",
})


def test_orchestrator_does_not_reintroduce_deleted_mitigations() -> None:
    source = ORCHESTRATOR_PATH.read_text()
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in FORBIDDEN_NAMES:
                found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in FORBIDDEN_NAMES:
                    found.add(target.id)
    assert not found, (
        f"Reintroduced removed mitigation symbol(s): {sorted(found)}. "
        "See docs/superpowers/specs/2026-05-12-engine-simplification-design.md."
    )
```

- [ ] **Step 2: Run the test**

Run: `docker compose run --rm nexus pytest tests/interview_engine/test_orchestrator_strip.py -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/interview_engine/test_orchestrator_strip.py
git commit -m "test(engine): regression-gate the deleted orchestrator mitigations"
```

### Task 14: Final orchestrator cleanup pass

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`

- [ ] **Step 1: Inspect on_user_turn_completed end-to-end**

Read `app/modules/interview_engine/orchestrator.py:on_user_turn_completed` end-to-end. The body should now be the linear pipeline shown in the spec: empty-text return, lifecycle hard-stop, turn.started, state.snapshot, build_judge_input, judge.call, process_judge_output, judge.validation, speaker.input, _stream_speaker_and_say, set_time_elapsed, publish_attributes, turn.completed, optional shutdown.

If any leftover branches reference deleted state, remove them.

- [ ] **Step 2: Inspect OrchestratorConfig**

Confirm `OrchestratorConfig` only contains: `checkpoint_turns`, `checkpoint_seconds`, `session_ended_message`. The five removed knobs should be gone.

- [ ] **Step 3: Inspect __init__**

Confirm `InterviewOrchestrator.__init__` no longer initializes `_last_turn`, `_last_user_speech_end_monotonic`, `_last_user_speech_end_wall`, `_stale_buffer`, `_resumed_speaking_at`. Only the surviving fields should remain.

- [ ] **Step 4: Run full engine test suite**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ -v 2>&1 | tail -20`

Expected: PASS.

- [ ] **Step 5: Run mypy on engine**

Run: `docker compose run --rm nexus mypy app/modules/interview_engine/orchestrator.py app/modules/interview_engine/state/engine.py app/modules/interview_engine/judge/`

Expected: PASS or pre-existing-only errors. Fix any new errors introduced by deletions (typical: unused imports, no-longer-needed `Any` types).

- [ ] **Step 6: Commit (if any cleanup landed)**

```bash
git add app/modules/interview_engine/orchestrator.py
git diff --cached --quiet || git commit -m "refactor(engine/orchestrator): cleanup pass after strip-down"
```

---

## Phase 5 — EOU/STT tuning (Sarvam path)

The product's first customers are based in India. Sarvam STT (saaras:v3, en-IN) stays the
default. MultilingualModel stays as the turn detector. The fix is three targeted tunings —
no provider switch, no Flux factory, no agent.py import changes.

### Task 15: Bump `interview_turn_detector_unlikely_threshold` default 0.5

**Files:**
- Modify: `app/ai/config.py` (in the `AIConfig` class, NOT `app/config.py`)

- [ ] **Step 1: Locate the field**

Run: `grep -n "interview_turn_detector_unlikely_threshold" app/ai/config.py`

Expected: a single match showing `interview_turn_detector_unlikely_threshold: float | None = None`.

- [ ] **Step 2: Change the default**

Edit `app/ai/config.py`. Change:

```python
interview_turn_detector_unlikely_threshold: float | None = None
```

to:

```python
# Phase 9.5 (2026-05-12): bumped from None -> 0.5 for the Sarvam +
# MultilingualModel path. The product's first candidates are
# Indian-English speakers who tend to pause mid-thought; a more
# conservative EOU floor (only fire end-of-turn when the model is
# confidently sure) reduces premature turn closures. Tune empirically
# from real session audio.tuning_summary data.
interview_turn_detector_unlikely_threshold: float | None = 0.5
```

- [ ] **Step 3: Verify the factory still passes the value through**

Open `app/ai/realtime.py` and confirm `build_turn_detector()` reads the field via
`ai_config.interview_turn_detector_unlikely_threshold` and passes it to
`MultilingualModel(unlikely_threshold=threshold)` only when non-None. NO code changes here —
this is just a verification step. The factory was authored to consume this field already.

- [ ] **Step 4: Run config + factory tests**

Run: `docker compose run --rm nexus pytest tests/test_engine_settings.py tests/interview_engine/test_stt_factory.py -v 2>&1 | tail -15`

Expected: PASS. If a test asserts the old `None` default, update it to `0.5`.

- [ ] **Step 5: Commit**

```bash
git add app/ai/config.py tests/
git diff --cached --quiet || git commit -m "feat(ai/config): bump turn_detector unlikely_threshold None -> 0.5 (Sarvam path)"
```

### Task 16: Confirm Sarvam is the deployed STT provider

**Files:**
- Inspect: `.env`, `.env.example`, `docker-compose.yml`
- Modify: `.env.example` (handled in Task 19)

- [ ] **Step 1: Check current deployed provider**

Run: `grep -n "INTERVIEW_STT_PROVIDER\|INTERVIEW_STT_MODEL" .env .env.example docker-compose.yml 2>/dev/null`

Expected: surfaces any env override. If a non-`.env.example` file has
`INTERVIEW_STT_PROVIDER=deepgram` set, that's the override that triggered the demo session's
nova-3 fragmentation. The fix is to remove the override (or set it explicitly to `sarvam`).

- [ ] **Step 2: Remove or correct the override**

Edit any local `.env` (NOT committed) or `docker-compose.yml` to set
`INTERVIEW_STT_PROVIDER=sarvam` (or remove the line — the in-code default is `sarvam`).

If `.env` is the only place it's set, the user's local file change is what matters; mention
this in the commit message of Task 19 (the `.env.example` update).

- [ ] **Step 3: No code commit needed for this task**

This task is environment-config awareness only. Move to Task 17.

### Task 17: Tune adaptive interruption min_duration to 1.0s

**Files:**
- Modify: `app/ai/realtime.py:241-255` (`build_interruption_options`)

- [ ] **Step 1: Edit min_duration**

In `app/ai/realtime.py`, in `build_interruption_options`, change `"min_duration": 0.5` to `"min_duration": 1.0`. Keep `"min_words": 2`, `"false_interruption_timeout": 2.0`, `"resume_false_interruption": True` unchanged.

Update the docstring to reflect "1.0s minimum to filter incidental noise; backchannel filtering still handled by the adaptive classifier and min_words=2".

- [ ] **Step 2: Run tests**

Run: `docker compose run --rm nexus pytest tests/interview_engine/ tests/test_audio_hints.py -v 2>&1 | tail -10`

Expected: PASS. Tests asserting the literal value 0.5 should be updated to 1.0.

- [ ] **Step 3: Commit**

```bash
git add app/ai/realtime.py tests/
git diff --cached --quiet || git commit -m "feat(ai/realtime): raise adaptive interruption min_duration 0.5s -> 1.0s"
```

### Task 18: Lower engine_endpointing_max_delay default to 3.0s

**Files:**
- Modify: `app/config.py` (line 248: change `engine_endpointing_max_delay: float = 6.0` → `3.0`)

- [ ] **Step 1: Edit the default**

In `app/config.py`, change `engine_endpointing_max_delay: float = 6.0` to `engine_endpointing_max_delay: float = 3.0`.

- [ ] **Step 2: Run config validator tests**

Run: `docker compose run --rm nexus pytest tests/test_config_validators.py tests/test_engine_settings.py -v 2>&1 | tail -10`

Expected: PASS. If a test asserts the literal 6.0 default, update it to 3.0.

- [ ] **Step 3: Commit**

```bash
git add app/config.py tests/
git diff --cached --quiet || git commit -m "feat(config): lower engine_endpointing_max_delay default 6.0s -> 3.0s (LK default)"
```

---

## Phase 6 — Documentation, .env.example, and verification

### Task 19: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Inspect current .env.example**

Run: `grep -n "ENGINE_COALESCE\|ENGINE_STALE\|ENGINE_POST_JUDGE\|INTERVIEW_STT_PROVIDER\|INTERVIEW_STT_MODEL\|ENGINE_ENDPOINTING_MAX_DELAY\|INTERVIEW_TURN_DETECTOR" .env.example`

- [ ] **Step 2: Remove the deleted keys**

Open `.env.example`. Delete any lines (and adjacent comments) for `ENGINE_COALESCE_ENABLED`, `ENGINE_COALESCE_WINDOW_MS`, `ENGINE_STALE_TURN_THRESHOLD_MS`, `ENGINE_STALE_BUFFER_MAX`, `ENGINE_POST_JUDGE_RESUMPTION_EPSILON_MS`. Keep `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD` if present (just update the example value to `0.5`).

- [ ] **Step 3: Add / update the keys for Sarvam path**

Ensure the interview-engine section of `.env.example` reflects:

```ini
# Interview STT — Sarvam (Indian-language tuned, code-mix capable).
# saaras:v3 is the recommended model (advanced mode control + broadest
# language support). Sarvam STT has no semantic EOU; turn detection is
# handled by MultilingualModel (the LK turn detector model) layered on
# top, configured via INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD below.
INTERVIEW_STT_PROVIDER=sarvam
INTERVIEW_STT_MODEL=saaras:v3
INTERVIEW_STT_LANGUAGE=en-IN
INTERVIEW_STT_MODE=transcribe

# MultilingualModel EOU confidence floor. None = plugin default; 0.5 is
# more conservative (only fire EOT when the model is confidently sure
# the user is done). Indian-English candidates pause mid-thought more
# than US-English speakers — a higher floor helps reduce premature
# turn closures.
INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=0.5

# Endpointing upper bound. Composes with MultilingualModel's EOU.
ENGINE_ENDPOINTING_MAX_DELAY=3.0

# Adaptive interruption (set on the LK plugin, not env-tunable today).
# Min duration was raised 0.5s -> 1.0s in this PR to filter incidental
# noise; min_words stays at 2 for backchannel filtering.
```

If you previously had `INTERVIEW_STT_PROVIDER=deepgram` set in your local `.env` (the override
that triggered the demo session's fragmentation), revert it to `sarvam` or remove the line
entirely (Sarvam is the in-code default).

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "docs(env): document Sarvam + MultilingualModel STT/EOU config (Indian customer focus)"
```

### Task 20: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the backend/nexus one)
- Modify: `/home/ishant/Projects/ProjectX/CLAUDE.md` (root)

- [ ] **Step 1: Locate Phase 3D entries**

Run: `grep -n "Phase 3D\|coalescing\|continuation coalesc" CLAUDE.md /home/ishant/Projects/ProjectX/CLAUDE.md`

- [ ] **Step 2: Update backend/nexus/CLAUDE.md**

In `backend/nexus/CLAUDE.md`, find the **Phase 3D.coalescing (2026-05-11)** entry and replace it with:

```markdown
- **Phase 3D.simplification (2026-05-12)** — done. Stripped the orchestrator's five layered race-condition mitigations (continuation coalescing, stale-turn drop-and-drain, post-Judge resumption gate, must-deliver whitelist, and supporting timestamp tracking). STT stays Sarvam (`saaras:v3`, `en-IN`) — Indian-customer focus rules out Deepgram Flux (English-only). Turn detection stays `MultilingualModel` with `unlikely_threshold` raised None → 0.5 for more patient EOU. Hardened the Judge fallback path: the `push_back+concrete` cross-field check no longer raises (the State Engine's new `inverse_quality_gate` downgrades to `probe`), and `validation_error` synthesizes `clarify` instead of force-advancing the queue. Added `state.snapshot` and `speaker.input` audit events; populated `judge.call.input_summary` with the full `JudgeInputPayload` (was hardcoded empty). `engine_endpointing_max_delay` lowered 6.0s → 3.0s (LK default); adaptive interruption `min_duration` raised 0.5s → 1.0s. Supersedes `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`. Spec: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`.
```

- [ ] **Step 3: Update root CLAUDE.md**

In `/home/ishant/Projects/ProjectX/CLAUDE.md` find the Current Phase Status table row for Phase 3D / Phase 3D.coalescing. Update the 3D row to reflect:

- "Audio pipeline tuning (LK Cloud + Sarvam STT + MultilingualModel turn detector with unlikely_threshold=0.5 + adaptive interruption + ai-coustics built-in VAD) shipped 2026-05-12. Orchestrator simplified (no race-condition mitigations); Judge fallback hardened. Real-time `analysis` (scoring, probe selection) + `reporting` (post-session report) still pending."

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md /home/ishant/Projects/ProjectX/CLAUDE.md
git commit -m "docs(CLAUDE): note 2026-05-12 engine simplification + Flux cutover"
```

### Task 21: Final verification

- [ ] **Step 1: Full test suite**

Run: `docker compose run --rm nexus pytest -q 2>&1 | tail -30`

Expected: All passing. The interview_engine subtree carries 100s of tests; the run takes a few minutes. If tests fail, address each one. Common fixers:

- A test imports a deleted symbol → delete that test or update its imports.
- A test expects the old default value (e.g. 6.0 max_delay) → update the literal.
- A test asserts a removed audit kind fires → drop the assertion.

- [ ] **Step 2: Lint**

Run: `docker compose run --rm nexus ruff check app/`

Expected: PASS or only pre-existing warnings.

- [ ] **Step 3: Mypy on engine**

Run: `docker compose run --rm nexus mypy app/modules/interview_engine app/ai/realtime.py app/ai/config.py app/config.py`

Expected: PASS or pre-existing-only errors.

- [ ] **Step 4: Read final orchestrator size**

Run: `wc -l app/modules/interview_engine/orchestrator.py`

Expected: ~700 lines (down from 1,560).

- [ ] **Step 5: Read final spec coverage**

Open `docs/superpowers/specs/2026-05-12-engine-simplification-design.md` and walk each section. Confirm every removed surface, every new field, every new event kind, every config change in the spec was actually executed in the codebase. Run `git log --oneline -25` to review the commit sequence.

- [ ] **Step 6: Manual smoke test prep**

Document in this checklist (no commit needed): what to check during the next live demo session:
- [ ] Number of `turn.started` events in `engine-events/<session>.json` ≈ number of real candidate utterances (1 per answer, NOT 8).
- [ ] Judge `latency_ms` 2-9s per turn, fired ONCE per real answer.
- [ ] `judge.call.input_summary` is populated (not `{}`).
- [ ] `speaker.input` and `state.snapshot` events appear for every turn.
- [ ] No `judge.fallback` events with `validation_error` reason that force-advanced the queue.
- [ ] Audio playback feels turn-based without race-condition glitches.

- [ ] **Step 7: (Optional) Final cleanup commit**

```bash
git status
# If nothing pending, you're done.
```

---

## Out of scope (per spec, deferred)

- Judge prompt revision for `social_or_greeting` sticky flag drift
- VAD provider switch (ai-coustics → Silero)
- Deepgram Flux factory (English-only; rejected because customers are Indian)
- TTS provider error recovery investigation (3 sarvam.tts errors observed)
- Tuning `unlikely_threshold` further or exploring Sarvam's `flush_signal` /
  `high_vad_sensitivity` knobs based on post-merge session data
