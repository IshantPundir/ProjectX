# Intro Prefetch + Cache Integrity + TTS Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the silent-agent disaster (Bug A — cache corruption from interrupted/empty Speaker calls), eliminate the persona intro from the repeat cache (Bug B — architectural mirror of opener prefetch for first-question), make TTS cache build resilient to transient DNS/network errors (Bug C), and tighten the first-question prompt to stop leaking rubric components (Bug D).

**Architecture:** Four sequenced phases shipped as 16 commits. Phase 1 splits `register_agent_utterance` into transcript-record (always) + cache-update (only on non-empty success). Phase 2 adds bounded retry-with-backoff to `_synthesize_variant`. Phase 3 adds a per-session intro `OpenerVariant` synthesized at agent entrypoint, routed through the existing parallel-dispatch path. Phase 4 hardens `deliver_first_question.txt` (≤ 20 words, anti-pattern example).

**Tech Stack:** Python 3.13, Pydantic v2, LiveKit Agents, OpenAI Responses API, pytest, asyncio.

**Spec:** `docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md`

---

## File Structure

**Files to modify:**

| Path | Phase | Change |
|---|---|---|
| `app/modules/interview_engine/state/engine.py` | 1 | Split `register_agent_utterance` into transcript-only; add new `register_agent_question_for_repeat` |
| `app/modules/interview_engine/orchestrator.py` | 1 | Update success path to call both register methods. Update interrupted/empty/recovery paths to call only the transcript-recorder. Remove misleading comment in `_handle_interrupted_speaker`. |
| `app/modules/interview_engine/orchestrator.py` | 3 | Add `intro_variant: OpenerVariant \| None = None` constructor param + routing in `_stream_speaker_and_say` |
| `app/modules/interview_engine/openers/cache.py` | 2 | Bounded retry-with-backoff in `_synthesize_variant` |
| `app/modules/interview_engine/openers/cache.py` | 3 | Factor `synth_one(text, tts) → list[Frame] \| None` helper |
| `app/modules/interview_engine/openers/__init__.py` | 3 | Re-export `synth_one` |
| `app/modules/interview_engine/audit_events.py` | 3 | Add `is_session_intro: bool = False` to `SpeakerOpenerPlayedPayload` |
| `app/modules/interview_engine/agent.py` | 3 | `_compose_intro_text` helper; entrypoint synthesizes intro and passes `intro_variant=` to orchestrator |
| `prompts/v1/engine/speaker/_preamble.txt` | 4 | Tighten ANTI-ENUMERATION bullet to mention conjunctions |
| `prompts/v1/engine/speaker/deliver_first_question.txt` | 4 | ≤ 20-word cap; assume `pre_spoken_opener` carries the intro; anti-pattern example |
| `tests/interview_engine/state/test_engine.py` | 1 | Tests for new register-agent contract |
| `tests/interview_engine/test_orchestrator.py` | 1, 3 | Tests for cache contract changes + intro_variant routing |
| `tests/interview_engine/test_orchestrator_composition.py` | 1, 3 | Composition tests for repeat-after-interrupt + first-question-repeat-replays-only-question |
| `tests/interview_engine/openers/test_cache.py` | 2, 3 | Tests for retry behavior + synth_one helper |
| `tests/interview_engine/audit/test_audit_events.py` (or wherever payloads are tested) | 3 | Test for new is_session_intro field |
| `tests/interview_engine/test_agent_intro.py` (NEW) | 3 | Test for `_compose_intro_text` shape |
| `tests/interview_engine/speaker/test_speaker_prompt_loadable.py` | 4 | New test for anti-pattern example |

---

## Tasks

### Task 1: Add `register_agent_question_for_repeat` method (Phase 1, Bug A)

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py`
- Modify: `tests/interview_engine/state/test_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/state/test_engine.py`:

```python
def test_register_agent_question_for_repeat_writes_for_question_kinds_with_non_empty_text(
    make_session_config, make_question,
):
    """Happy path: a question-bearing kind with non-empty text updates
    the repeat cache."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="What is your favorite tool?",
        instruction_kind=InstructionKind.deliver_question,
    )
    assert engine._question_utterances["t-1"] == "What is your favorite tool?"


def test_register_agent_question_for_repeat_skips_empty_text(
    make_session_config, make_question,
):
    """An empty text MUST NOT update the cache (Phase 9.9 contract).
    The interrupted/empty Speaker handlers depend on this — if they
    pollute the cache with empty entries, NextAction.repeat replays
    silence and the candidate hears nothing."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="",
        instruction_kind=InstructionKind.push_back,
    )
    assert "t-1" not in engine._question_utterances


def test_register_agent_question_for_repeat_skips_whitespace_only(
    make_session_config, make_question,
):
    """Whitespace-only counts as empty for cache purposes."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="   \n  ",
        instruction_kind=InstructionKind.deliver_question,
    )
    assert "t-1" not in engine._question_utterances


def test_register_agent_question_for_repeat_skips_non_question_kinds(
    make_session_config, make_question,
):
    """Non-question kinds (redirect, repeat, polite_close,
    acknowledge_no_experience) MUST NOT enter the repeat cache —
    same as today's contract on the underlying _QUESTION_KINDS filter."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    for non_q_kind in [
        InstructionKind.redirect, InstructionKind.polite_close,
        InstructionKind.acknowledge_no_experience,
    ]:
        engine.register_agent_question_for_repeat(
            turn_id=f"t-{non_q_kind.value}", text="something",
            instruction_kind=non_q_kind,
        )
        assert f"t-{non_q_kind.value}" not in engine._question_utterances
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose exec -T nexus pytest tests/interview_engine/state/test_engine.py -k "register_agent_question_for_repeat" -v 2>&1 | tail -10
```

Expected: 4 FAIL with `AttributeError: 'StateEngine' object has no attribute 'register_agent_question_for_repeat'`.

- [ ] **Step 3: Add the method**

In `app/modules/interview_engine/state/engine.py`, find `register_agent_utterance` (~line 800). Add a NEW method right after it:

```python
    def register_agent_question_for_repeat(
        self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
    ) -> None:
        """Update the repeat-cache. Only call this when the agent
        SUCCESSFULLY emitted a question-bearing utterance — empty text
        and non-question kinds are silently no-ops.

        The repeat cache (``_question_utterances``) holds the most
        recent good question text for ``NextAction.repeat`` resolution.
        Empty entries would cause silent-agent replays — strictly
        forbidden by the Phase 9.9 cache integrity contract.

        See spec ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
        §4.1 for the rationale.
        """
        if not text.strip():
            return
        if instruction_kind not in self._QUESTION_KINDS:
            return
        self._question_utterances[turn_id] = text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/test_engine.py -k "register_agent_question_for_repeat" -v 2>&1 | tail -10
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/interview_engine/state/engine.py \
        backend/nexus/tests/interview_engine/state/test_engine.py
git commit -m "$(cat <<'EOF'
feat(engine/state): add register_agent_question_for_repeat — Phase 9.9 cache contract

Splits the repeat-cache update intent from register_agent_utterance.
The new method skips empty text and non-question kinds explicitly,
restoring the invariant that _question_utterances never contains
silent (empty) entries. Bug A from session a998073a-3007-...

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Strip cache-write side effect from `register_agent_utterance` (Phase 1, Bug A)

**Files:**
- Modify: `app/modules/interview_engine/state/engine.py`
- Modify: `tests/interview_engine/state/test_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/state/test_engine.py`:

```python
def test_register_agent_utterance_no_longer_updates_cache(
    make_session_config, make_question,
):
    """Phase 9.9 contract: register_agent_utterance is transcript-only.
    The repeat cache update was hoisted into a separate method
    (register_agent_question_for_repeat). Confirm a single call to
    register_agent_utterance no longer touches _question_utterances."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_utterance(
        turn_id="t-1", text="A real agent question",
        instruction_kind=InstructionKind.deliver_question,
    )
    # Transcript IS appended.
    assert engine._transcript[-1].text == "A real agent question"
    # Cache is NOT touched.
    assert "t-1" not in engine._question_utterances


def test_register_agent_utterance_appends_empty_text_to_transcript(
    make_session_config, make_question,
):
    """Empty text is a valid transcript fact (the agent emitted nothing
    on this turn — recorded for forensic completeness alongside the
    speaker.interrupted / speaker.output.empty audit event)."""
    cfg = make_session_config(questions=[make_question(qid="q1", text="Q?")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_utterance(
        turn_id="t-1", text="",
        instruction_kind=InstructionKind.push_back,
    )
    assert engine._transcript[-1].text == ""
    assert "t-1" not in engine._question_utterances
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/test_engine.py -k "register_agent_utterance_no_longer_updates_cache or register_agent_utterance_appends_empty_text" -v 2>&1 | tail -10
```

Expected: 2 FAIL — the existing implementation still writes to `_question_utterances`.

- [ ] **Step 3: Strip the cache write**

In `app/modules/interview_engine/state/engine.py`, modify `register_agent_utterance` (~line 800). The new body:

```python
    def register_agent_utterance(
        self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
    ) -> None:
        """Record an agent utterance to the transcript. ALWAYS appends
        regardless of text length — empty text is a valid historical
        fact (the agent emitted nothing on this turn, e.g. interrupted
        before any output).

        Does NOT update the repeat-cache. Use
        ``register_agent_question_for_repeat`` for that — the two
        intents are deliberately separated (Phase 9.9, see spec
        ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
        §4.1) so an interrupted/empty turn cannot poison the repeat
        cache and cause silent-agent replay.
        """
        self._transcript.append(TranscriptEntry(
            role="agent", text=text, timestamp_ms=0,
            question_id=self._queue.active_question_id(),
        ))
```

Confirm the `if instruction_kind in self._QUESTION_KINDS: self._question_utterances[turn_id] = text` lines are GONE.

- [ ] **Step 4: Run the new tests**

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/test_engine.py -k "register_agent_utterance_no_longer_updates_cache or register_agent_utterance_appends_empty_text" -v 2>&1 | tail -10
```

Expected: 2 PASS.

- [ ] **Step 5: Run the full state engine suite — EXISTING TESTS WILL FAIL**

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/ -v 2>&1 | tail -30
```

Some pre-existing tests assert that `register_agent_utterance` updates `_question_utterances` — those are testing the OLD contract. Note the failures; they will be fixed in Task 3 by updating the orchestrator call sites + the assertions.

For now, document the expected failures in the commit message. Do NOT delete or modify the failing tests yet.

- [ ] **Step 6: Commit (with known temporarily-failing tests)**

```bash
git add backend/nexus/app/modules/interview_engine/state/engine.py \
        backend/nexus/tests/interview_engine/state/test_engine.py
git commit -m "$(cat <<'EOF'
refactor(engine/state): strip cache-write side effect from register_agent_utterance

Phase 9.9 contract — register_agent_utterance is now transcript-only.
The cache write hoisted to register_agent_question_for_repeat (Task 1).
Tasks 3 + 4 update the orchestrator call sites; until they land, the
existing tests asserting the OLD coupled behavior will fail. Documented
gap; do not deploy this commit alone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update orchestrator success path to call both register methods (Phase 1, Bug A)

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `tests/interview_engine/test_orchestrator.py`
- Modify: `tests/interview_engine/state/test_engine.py` (delete / adapt the now-stale tests from Task 2's failure list)

- [ ] **Step 1: Find the success-path call site**

In `app/modules/interview_engine/orchestrator.py`, find `_stream_speaker_and_say` (around line 690). Locate the success-path register call (around line 745–750):

```python
self._state.register_agent_utterance(
    turn_id=turn_id, text=final_text,
    instruction_kind=speaker_input.instruction_kind,
)
```

- [ ] **Step 2: Replace with two calls**

```python
# Phase 9.9 — register_agent_utterance is now transcript-only;
# register_agent_question_for_repeat does the cache update with
# the empty-text + non-question-kind guards.
self._state.register_agent_utterance(
    turn_id=turn_id, text=final_text,
    instruction_kind=speaker_input.instruction_kind,
)
self._state.register_agent_question_for_repeat(
    turn_id=turn_id, text=final_text,
    instruction_kind=speaker_input.instruction_kind,
)
```

- [ ] **Step 3: Adapt the failing tests from Task 2**

Re-run the state engine suite:

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/ -v 2>&1 | tail -20
```

Identify which tests fail because they were testing the OLD coupled contract (calling `register_agent_utterance` and asserting cache state). For each:

- If the test's intent was to verify `register_agent_utterance` writes to the cache: REPLACE the call with `register_agent_question_for_repeat` and keep the same assertion.
- If the test's intent was to verify the transcript was updated: keep `register_agent_utterance`, drop the cache assertion.

Edit each affected test individually with the right intent.

Then re-run:

```bash
docker compose exec -T nexus pytest tests/interview_engine/state/ -v 2>&1 | tail -10
```

Expected: all PASS.

- [ ] **Step 4: Run orchestrator + composition tests**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py tests/interview_engine/test_orchestrator_composition.py -v 2>&1 | tail -10
```

Expected: all PASS. The success-path now calls both methods; the cache is updated as before for valid utterances.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/orchestrator.py \
        backend/nexus/tests/interview_engine/state/test_engine.py
git commit -m "$(cat <<'EOF'
refactor(engine/orchestrator): success path calls register_agent_utterance + register_agent_question_for_repeat

Closes the gap from Task 2 — adapts state engine tests to the new
contract and updates the success path to call both methods. Cache
behavior is unchanged for valid (non-empty, question-kind) utterances.
The next task fixes the interrupted / empty / recovery paths to NOT
poison the cache.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Strip cache writes from interrupted / empty / recovery paths (Phase 1, Bug A)

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `tests/interview_engine/test_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_interrupted_speaker_does_not_pollute_repeat_cache(
    make_session_config, make_question,
):
    """Phase 9.9 — _handle_interrupted_speaker MUST NOT write empty
    text to _question_utterances. Bug A from session a998073a-3007-...:
    push_back interrupted by candidate → cache held "" → next repeat
    replayed silence.
    """
    from app.modules.interview_engine.openers import OpenerLibrary
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your tool?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    # Pre-seed the cache with a valid prior question.
    state_engine.register_agent_question_for_repeat(
        turn_id="t-prev", text="What is your favorite tool?",
        instruction_kind=InstructionKind.deliver_question,
    )

    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle(""))  # empty stream
    judge = MagicMock()
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
    )

    speaker_input = SpeakerInput(
        instruction_kind=InstructionKind.push_back,
        bank_text="What is your tool?",
        last_candidate_utterance="hmm",
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
        push_back_reason_code="vague_answer",
    )
    fake_agent = MagicMock()
    # Fake an INTERRUPTED handle (interrupted=True) so the orchestrator
    # routes to _handle_interrupted_speaker, not _handle_empty_speaker_output.
    interrupted_handle = MagicMock(interrupted=True)
    fake_agent.session.say = AsyncMock(return_value=interrupted_handle)

    await orch._stream_speaker_and_say(
        agent=fake_agent, turn_id="t-interrupted", speaker_input=speaker_input,
    )

    # Cache is UNCHANGED — still holds the prior valid question, not "".
    assert "t-interrupted" not in state_engine._question_utterances
    assert state_engine._question_utterances["t-prev"] == "What is your favorite tool?"


@pytest.mark.asyncio
async def test_empty_speaker_output_fallback_does_not_pollute_repeat_cache(
    make_session_config, make_question,
):
    """Phase 9.9 — _handle_empty_speaker_output plays a deterministic
    fallback ("Let me restate that. {bank_text}") but MUST NOT cache it.
    The fallback is a recovery utterance, not the agent's actual question."""
    from app.modules.interview_engine.openers import OpenerLibrary
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your tool?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    state_engine.register_agent_question_for_repeat(
        turn_id="t-prev", text="What is your favorite tool?",
        instruction_kind=InstructionKind.deliver_question,
    )

    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle(""))  # empty
    judge = MagicMock()
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
    )

    speaker_input = SpeakerInput(
        instruction_kind=InstructionKind.push_back,
        bank_text="What is your tool?",
        last_candidate_utterance="ok",
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
        push_back_reason_code="vague_answer",
    )
    fake_agent = MagicMock()
    not_interrupted_handle = MagicMock(interrupted=False)
    fake_agent.session.say = AsyncMock(return_value=not_interrupted_handle)

    await orch._stream_speaker_and_say(
        agent=fake_agent, turn_id="t-empty", speaker_input=speaker_input,
    )

    # Cache is unchanged. Fallback was played but not cached.
    assert "t-empty" not in state_engine._question_utterances
    assert state_engine._question_utterances["t-prev"] == "What is your favorite tool?"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -k "does_not_pollute_repeat_cache" -v 2>&1 | tail -10
```

Expected: 2 FAIL — interrupted / empty paths still call `register_agent_utterance` which (post-Task-2) doesn't touch the cache, but the orchestrator may already pass — verify whether the test fails. If it passes, the contract change in Task 2 already covers this case (good). If it fails, continue to Step 3.

NOTE: depending on test mocks, this may pass after Task 2 alone. If both tests pass, skip to Step 5 to commit the test-only addition as a regression guard.

- [ ] **Step 3: If failing — clean up the misleading comment + ensure handlers do not call `register_agent_question_for_repeat`**

In `_handle_interrupted_speaker` (around line 798), the call is:

```python
self._state.register_agent_utterance(
    turn_id=turn_id, text="",
    instruction_kind=speaker_input.instruction_kind,
)
```

After Task 2, this no longer touches the cache (transcript-only). Verify the call is correct (it IS the right call now — empty agent transcript entry). DELETE the misleading comment block above it (the lines that say "Empty text with the instruction_kind preserved keeps the _question_utterances cache filter (in _QUESTION_KINDS) working correctly: an interrupted deliver_question still caches "" rather than the previous turn's question text"). Replace with:

```python
# Empty agent transcript entry preserves forensic completeness:
# downstream replay tools see an empty utterance next to the
# corresponding speaker.interrupted audit event.
# Cache is intentionally NOT updated here — Phase 9.9 contract
# (see register_agent_question_for_repeat docstring).
```

Same for `_handle_empty_speaker_output` (around line 818). The call is:

```python
self._state.register_agent_utterance(
    turn_id=turn_id, text=fallback,
    instruction_kind=speaker_input.instruction_kind,
)
```

Keep the call (transcript records the fallback was played) but add comment:

```python
# Cache is intentionally NOT updated here — the fallback is a
# recovery utterance ("Let me restate that. {bank_text}"), not
# the agent's actual question for repeat purposes.
```

Same for the recovery path in the outer except block (around line 821):

```python
self._state.register_agent_utterance(
    turn_id=turn_id, text=self._RECOVERY_TEXT,
    instruction_kind=speaker_input.instruction_kind,
)
```

Add comment: `# Cache intentionally NOT updated — RECOVERY_TEXT is not the question.`

- [ ] **Step 4: Run the new tests + full orchestrator suite**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -v 2>&1 | tail -15
```

Expected: ALL PASS, including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/orchestrator.py \
        backend/nexus/tests/interview_engine/test_orchestrator.py
git commit -m "$(cat <<'EOF'
fix(engine/orchestrator): stop polluting repeat cache from interrupted/empty/recovery paths

Phase 9.9 contract is now end-to-end: interrupted and empty Speaker
paths record empty / fallback text in the transcript but never update
_question_utterances. Bug A from session a998073a-3007-... is closed
— the silent-agent replay disaster cannot recur. Removes the
misleading comment in _handle_interrupted_speaker that documented
the old broken intent. Adds 2 regression tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Composition test — repeat after interrupted push_back replays prior question (Phase 1, Bug A)

**Files:**
- Modify: `tests/interview_engine/test_orchestrator_composition.py`

- [ ] **Step 1: Write the composition test**

Append to `tests/interview_engine/test_orchestrator_composition.py`:

```python
@pytest.mark.asyncio
async def test_repeat_after_interrupted_push_back_replays_prior_question_not_empty(
    make_session_config, make_question,
):
    """Phase 9.9 composition — drives a 3-turn session that reproduces
    the silent-agent disaster from session a998073a-3007-... and asserts
    the fix:
      Turn 1: deliver_first_question (success) → cache holds "Q1 text"
      Turn 2: push_back (interrupted before any output) → cache untouched
      Turn 3: NextAction.repeat → SPEAKER_CACHED replays "Q1 text", NOT ""
    """
    from app.modules.interview_engine.openers import OpenerLibrary
    from app.modules.interview_engine.event_kinds import SPEAKER_CACHED
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, RepeatPayload,
        TurnMetadata, Observation, CoverageTransition, CoverageQuality,
    )

    judge_outputs = [
        # Turn 1: candidate gives an answer → push_back/vague_answer
        JudgeOutput(
            observations=[
                Observation(
                    signal_value="S1", anchor_id=0,
                    evidence_quote="thin",
                    coverage_transition=CoverageTransition.partial_to_partial,
                    quality=CoverageQuality.thin,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(),
        ),
        # Turn 2: candidate asks repeat
        JudgeOutput(
            observations=[], candidate_claims=[],
            next_action=NextAction.repeat,
            next_action_payload=RepeatPayload(),
            turn_metadata=TurnMetadata(),
        ),
    ]

    speaker_outputs = [
        # on_enter: deliver_first_question
        "Walk me through your tool of choice.",
        # Turn 1 push_back: SIMULATE INTERRUPTED — empty stream
        "",
    ]

    orch, agent = _build_orch(
        make_session_config=make_session_config,
        make_question=make_question,
        scripted_judge_outputs=judge_outputs,
        scripted_speaker_outputs=speaker_outputs,
        knockout_signal="S1",
    )
    orch._opener_library = OpenerLibrary()
    # Make the second say() return interrupted=True for the push_back turn.
    # _build_orch should let us configure this — check its signature.
    # If not, monkeypatch the agent.session.say to track call count and
    # return interrupted handle on the 2nd matching call.

    await orch.on_enter(agent)
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("I use various tools"),
    )
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("Can you repeat?"),
    )

    state = orch._state

    # The first question's cached text is still the only entry in the cache.
    cache_values = list(state._question_utterances.values())
    assert len(cache_values) == 1, f"Expected 1 cache entry, got {len(cache_values)}: {cache_values}"
    assert cache_values[0] == "Walk me through your tool of choice."

    # The repeat turn fired a SPEAKER_CACHED event with the FIRST question text,
    # NOT empty.
    cached_events = [
        e for e in orch._collector.events
        if e.kind == SPEAKER_CACHED
    ]
    assert len(cached_events) == 1
    assert cached_events[0].payload["final_utterance"] == "Walk me through your tool of choice."
    assert cached_events[0].payload["final_utterance"] != ""
```

- [ ] **Step 2: Adapt `_build_orch` if needed**

If `_build_orch` doesn't expose a way to make `agent.session.say` return interrupted handles for specific turns, extend it. Check the helper at the top of `test_orchestrator_composition.py`. Add a `scripted_interrupted_turns: list[int] | None = None` parameter that the helper translates into per-call return values for `session.say`. For this test, pass `scripted_interrupted_turns=[1]` (the push_back turn).

- [ ] **Step 3: Run the test**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator_composition.py -k "repeat_after_interrupted_push_back" -v 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_orchestrator_composition.py
git commit -m "$(cat <<'EOF'
test(engine): composition test for repeat-after-interrupted-push_back

Reproduces the silent-agent disaster from session a998073a-3007-...:
3-turn session with push_back interrupted before output, then a repeat
request. Asserts cache holds the prior valid question (not empty) and
SPEAKER_CACHED replays the actual question text. Regression guard for
Phase 9.9 cache contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Composition test — repeat after empty Speaker output replays prior question (Phase 1, Bug A)

**Files:**
- Modify: `tests/interview_engine/test_orchestrator_composition.py`

- [ ] **Step 1: Write the composition test**

Same structure as Task 5 but exercises the `_handle_empty_speaker_output` path. The Speaker LLM returns empty WITHOUT being interrupted (model decided nothing to say). The orchestrator plays the deterministic fallback, but the fallback should NOT enter the repeat cache.

Append to `tests/interview_engine/test_orchestrator_composition.py`:

```python
@pytest.mark.asyncio
async def test_repeat_after_empty_speaker_output_replays_prior_question_not_fallback(
    make_session_config, make_question,
):
    """Phase 9.9 — when Speaker LLM returns empty without interruption
    (e.g., content filter, model gave up), the orchestrator plays the
    deterministic "Let me restate that. {bank_text}" fallback. The
    fallback MUST NOT enter the repeat cache — it's a recovery utterance,
    not THE agent's question. Subsequent NextAction.repeat must replay
    the LAST GOOD question, not the fallback."""
    from app.modules.interview_engine.openers import OpenerLibrary
    from app.modules.interview_engine.event_kinds import SPEAKER_CACHED
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, RepeatPayload,
        TurnMetadata, Observation, CoverageTransition, CoverageQuality,
    )

    judge_outputs = [
        JudgeOutput(
            observations=[
                Observation(
                    signal_value="S1", anchor_id=0, evidence_quote="vague",
                    coverage_transition=CoverageTransition.partial_to_partial,
                    quality=CoverageQuality.thin,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(),
        ),
        JudgeOutput(
            observations=[], candidate_claims=[],
            next_action=NextAction.repeat,
            next_action_payload=RepeatPayload(),
            turn_metadata=TurnMetadata(),
        ),
    ]
    speaker_outputs = [
        "Walk me through your tool of choice.",
        "",  # empty, but NOT interrupted
    ]

    orch, agent = _build_orch(
        make_session_config=make_session_config,
        make_question=make_question,
        scripted_judge_outputs=judge_outputs,
        scripted_speaker_outputs=speaker_outputs,
        knockout_signal="S1",
    )
    orch._opener_library = OpenerLibrary()

    await orch.on_enter(agent)
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("Some tools"),
    )
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("Can you repeat?"),
    )

    state = orch._state

    cache_values = list(state._question_utterances.values())
    assert len(cache_values) == 1
    assert cache_values[0] == "Walk me through your tool of choice."

    cached_events = [
        e for e in orch._collector.events
        if e.kind == SPEAKER_CACHED
    ]
    assert len(cached_events) == 1
    assert cached_events[0].payload["final_utterance"] == "Walk me through your tool of choice."
    # Importantly, NOT the fallback text:
    assert "Let me restate" not in cached_events[0].payload["final_utterance"]
```

- [ ] **Step 2: Run the test**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator_composition.py -k "repeat_after_empty_speaker_output" -v 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_orchestrator_composition.py
git commit -m "$(cat <<'EOF'
test(engine): composition test for repeat-after-empty-speaker-output

Phase 9.9 — Speaker emits nothing without interruption, orchestrator
plays "Let me restate that. {bank_text}" fallback. The fallback MUST
NOT enter the repeat cache; subsequent repeat replays the last GOOD
question. Regression guard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: TTS retry-with-backoff in `_synthesize_variant` (Phase 2, Bug C)

**Files:**
- Modify: `app/modules/interview_engine/openers/cache.py`
- Modify: `tests/interview_engine/openers/test_cache.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/interview_engine/openers/test_cache.py`:

```python
@pytest.mark.asyncio
async def test_synthesize_variant_retries_on_oserror():
    """Bug C — DNS failures (httpcore.ConnectError, OSError subclass)
    should be retried with exponential backoff, not surfaced after the
    first attempt."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts: list[int] = []

    class FlakeyTTS:
        async def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise OSError("[Errno -5] No address associated with hostname")
            # Second attempt: succeed.
            return _FakeStream([_FakeFrame()])

    variant = OpenerVariant(text="hello")
    result_variant, exc = await _synthesize_variant(variant, FlakeyTTS())
    assert exc is None
    assert variant.audio_frames is not None
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_synthesize_variant_retries_on_timeout():
    """asyncio.TimeoutError is also retryable."""
    import asyncio
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts: list[int] = []

    class TimeoutThenSuccessTTS:
        async def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise asyncio.TimeoutError("synthesis timeout")
            return _FakeStream([_FakeFrame()])

    variant = OpenerVariant(text="hello")
    result_variant, exc = await _synthesize_variant(variant, TimeoutThenSuccessTTS())
    assert exc is None
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_synthesize_variant_does_not_retry_on_non_transient_error():
    """4xx-style errors (BadRequest, auth) MUST NOT be retried — they
    won't change on retry and we'd waste budget hiding the real problem."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts: list[int] = []

    class FatalTTS:
        async def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            raise ValueError("invalid voice 'foo'")

    variant = OpenerVariant(text="hello")
    result_variant, exc = await _synthesize_variant(variant, FatalTTS())
    assert exc is not None
    assert isinstance(exc, ValueError)
    assert len(attempts) == 1   # NO retry


@pytest.mark.asyncio
async def test_synthesize_variant_exhausts_retries_then_returns_last_error():
    """All 3 attempts fail → returns the last error after the bounded budget."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts: list[int] = []

    class AlwaysFailTTS:
        async def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            raise OSError("permanent network outage")

    variant = OpenerVariant(text="hello")
    result_variant, exc = await _synthesize_variant(variant, AlwaysFailTTS())
    assert exc is not None
    assert isinstance(exc, OSError)
    assert len(attempts) == 3   # exact retry budget
```

If `_FakeStream` and `_FakeFrame` don't exist in the test file already, add minimal implementations:

```python
class _FakeFrame:
    pass

class _FakeStream:
    def __init__(self, frames):
        self._frames = frames
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        pass
    def __aiter__(self):
        async def gen():
            for f in self._frames:
                yield type("Ev", (), {"frame": f})()
        return gen()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/openers/test_cache.py -k "retry or exhausts or non_transient" -v 2>&1 | tail -15
```

Expected: 4 FAIL — current `_synthesize_variant` has no retry.

- [ ] **Step 3: Add retry-with-backoff**

In `app/modules/interview_engine/openers/cache.py`, replace `_synthesize_variant`:

```python
import asyncio

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 0.2


async def _synthesize_variant(
    variant: OpenerVariant, tts: TTS,
) -> tuple[OpenerVariant, Exception | None]:
    """Synthesize one variant with bounded exponential-backoff retry on
    transient errors. Returns (variant, None) on success or (variant,
    last_error) after all retries exhausted.

    Retried errors: ``asyncio.TimeoutError`` and ``OSError`` (DNS
    failures bubble up via httpcore as OSError subclasses; TCP resets
    likewise). Non-retried: every other exception (4xx auth/validation
    errors, content-filter rejections, schema errors — these will not
    change on retry and we'd just hide the real problem).

    Bounded budget: 3 attempts with exponential backoff (200ms, 400ms,
    800ms = 1.4s max wait), tested in
    ``test_synthesize_variant_exhausts_retries_then_returns_last_error``.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            frames: list = []
            async with tts.synthesize(variant.text) as stream:
                async for ev in stream:
                    frame = getattr(ev, "frame", None)
                    if frame is not None:
                        frames.append(frame)
            if not frames:
                return variant, RuntimeError("empty audio stream")
            variant.audio_frames = frames
            return variant, None
        except (asyncio.TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                backoff_s = _RETRY_BASE_DELAY_S * (2 ** attempt)
                log.warning(
                    "openers.cache.synth.retry",
                    variant_text=variant.text[:40],
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    backoff_ms=int(backoff_s * 1000),
                )
                await asyncio.sleep(backoff_s)
                continue
            return variant, exc
        except Exception as exc:  # noqa: BLE001
            return variant, exc
    return variant, last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/openers/test_cache.py -v 2>&1 | tail -15
```

Expected: ALL PASS, including the 4 new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/openers/cache.py \
        backend/nexus/tests/interview_engine/openers/test_cache.py
git commit -m "$(cat <<'EOF'
feat(engine/openers/cache): bounded retry-with-backoff on transient TTS errors

Bug C — session a998073a-3007-... boot hit a transient DNS resolution
failure (httpcore.ConnectError) on one cache-build variant; the
variant was permanently degraded for the worker process lifetime.
Adds 3-attempt exponential backoff (200/400/800ms) on
asyncio.TimeoutError + OSError. Non-transient errors (4xx, content
filter, schema) are NOT retried.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Factor `synth_one` helper from `build_opener_cache` (Phase 3, Bug B)

**Files:**
- Modify: `app/modules/interview_engine/openers/cache.py`
- Modify: `app/modules/interview_engine/openers/__init__.py`
- Modify: `tests/interview_engine/openers/test_cache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/openers/test_cache.py`:

```python
@pytest.mark.asyncio
async def test_synth_one_returns_audio_frames_on_success():
    """synth_one is the per-text helper used by both build_opener_cache
    and the engine entrypoint's per-session intro synthesis."""
    from app.modules.interview_engine.openers import synth_one

    class GoodTTS:
        async def synthesize(self, text):
            return _FakeStream([_FakeFrame(), _FakeFrame()])

    frames = await synth_one(text="Hi, I'm Sam.", tts=GoodTTS())
    assert frames is not None
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_synth_one_returns_none_on_permanent_failure():
    """When all retries exhausted, synth_one returns None (caller
    falls back to text-only TTS)."""
    from app.modules.interview_engine.openers import synth_one

    class AlwaysFailTTS:
        async def synthesize(self, text):
            raise OSError("permanent")

    frames = await synth_one(text="Hi, I'm Sam.", tts=AlwaysFailTTS())
    assert frames is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/openers/test_cache.py -k "synth_one" -v 2>&1 | tail -10
```

Expected: 2 FAIL — `synth_one` not exported.

- [ ] **Step 3: Add `synth_one`**

In `app/modules/interview_engine/openers/cache.py`:

```python
async def synth_one(*, text: str, tts: TTS) -> list | None:
    """Synthesize a single text into audio frames using the same retry
    policy as build_opener_cache. Returns None on permanent failure.

    Used by build_opener_cache (one call per variant) AND by the engine
    entrypoint for the per-session persona intro (which can't be
    pre-synthesized at engine boot because persona_name is per-tenant).
    See spec ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
    §4.3 for the rationale.
    """
    variant = OpenerVariant(text=text)
    _, exc = await _synthesize_variant(variant, tts)
    if exc is not None:
        return None
    return variant.audio_frames
```

In `app/modules/interview_engine/openers/__init__.py`, add to imports + `__all__`:

```python
from app.modules.interview_engine.openers.cache import (
    BuildReport,
    build_opener_cache,
    synth_one,
)
...
__all__ = [
    "BuildReport",
    "OpenerLibrary",
    "OpenerSelection",
    "OpenerVariant",
    "SubContext",
    "build_opener_cache",
    "synth_one",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/openers/test_cache.py -v 2>&1 | tail -10
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/openers/cache.py \
        backend/nexus/app/modules/interview_engine/openers/__init__.py \
        backend/nexus/tests/interview_engine/openers/test_cache.py
git commit -m "$(cat <<'EOF'
feat(engine/openers): factor synth_one helper from build_opener_cache

synth_one synthesizes a single text into audio frames using the same
retry policy. Used by the cache builder for each variant AND by the
engine entrypoint for the per-session persona intro (Phase 3, Bug B).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Add `_compose_intro_text` helper at agent.py module level (Phase 3, Bug B)

**Files:**
- Modify: `app/modules/interview_engine/agent.py`
- Create: `tests/interview_engine/test_agent_intro.py`

- [ ] **Step 1: Write the failing test**

Create `tests/interview_engine/test_agent_intro.py`:

```python
"""Tests for the agent.py per-session intro helpers."""
from app.modules.interview_engine.agent import _compose_intro_text


def test_compose_intro_text_uses_persona_name():
    assert _compose_intro_text(persona_name="Sam") == "Hi, I'm Sam. To start —"


def test_compose_intro_text_handles_unicode_persona_name():
    assert _compose_intro_text(persona_name="Maya") == "Hi, I'm Maya. To start —"


def test_compose_intro_text_handles_empty_persona_name():
    """Edge case — defensive only. An empty persona name shouldn't
    happen in practice (resolve_persona_name has its own fallback),
    but the function must not crash."""
    assert _compose_intro_text(persona_name="") == "Hi, I'm . To start —"


def test_compose_intro_text_is_short():
    """Locked invariant: the intro must stay short (it's spoken before
    every first question). Add a hard cap so future edits don't drift
    into a multi-sentence intro that defeats the purpose."""
    text = _compose_intro_text(persona_name="Sam")
    assert len(text) < 50
    # Single sentence + dash continuation (the ` — ` prepares the LLM
    # output to flow into the question naturally).
    assert text.count(".") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_agent_intro.py -v 2>&1 | tail -10
```

Expected: 4 FAIL with `ImportError: cannot import name '_compose_intro_text'`.

- [ ] **Step 3: Add the helper**

In `app/modules/interview_engine/agent.py`, near the top (after the existing module-level helpers like `_get_or_build_opener_library`), add:

```python
def _compose_intro_text(*, persona_name: str) -> str:
    """The persona intro spoken before the FIRST question of every
    session. Pre-synthesized at agent entrypoint and played in parallel
    with the first-question Speaker LLM call (Phase 3 of
    ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``).

    Kept deliberately short — the question is the substance, the intro
    just sets pacing. The trailing ``—`` cues the next sentence to
    flow naturally from the intro (matching how speakers actually pause).
    """
    return f"Hi, I'm {persona_name}. To start —"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_agent_intro.py -v 2>&1 | tail -10
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py \
        backend/nexus/tests/interview_engine/test_agent_intro.py
git commit -m "$(cat <<'EOF'
feat(engine/agent): add _compose_intro_text helper for per-session intro

Module-level helper at agent.py — composes the persona intro spoken
before every first question. Phase 3 of the intro-prefetch architecture
(see spec). Kept short (< 50 chars) and tested for the persona-name
substitution shape.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Add `intro_variant` parameter to InterviewOrchestrator constructor (Phase 3, Bug B)

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `tests/interview_engine/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/test_orchestrator.py`:

```python
def test_orchestrator_constructor_accepts_intro_variant_default_none(
    make_session_config, make_question,
):
    """Backward compatibility — existing callers don't have to pass
    intro_variant; default is None and the orchestrator behaves as
    before (Speaker LLM produces greeting + question for first turn)."""
    from app.modules.interview_engine.openers import OpenerLibrary
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Q?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=MagicMock(), speaker=MagicMock(),
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
        # intro_variant=None  ← default
    )
    assert orch._intro_variant is None


def test_orchestrator_constructor_accepts_intro_variant_when_set(
    make_session_config, make_question,
):
    from app.modules.interview_engine.openers import OpenerLibrary, OpenerVariant
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Q?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))
    intro = OpenerVariant(text="Hi, I'm Sam. To start —")
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=MagicMock(), speaker=MagicMock(),
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
        intro_variant=intro,
    )
    assert orch._intro_variant is intro
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -k "constructor_accepts_intro_variant" -v 2>&1 | tail -10
```

Expected: 2 FAIL — constructor doesn't accept `intro_variant`.

- [ ] **Step 3: Add the parameter**

In `app/modules/interview_engine/orchestrator.py`, find `InterviewOrchestrator.__init__`. Add `intro_variant: OpenerVariant | None = None` to the signature (after `opener_library: OpenerLibrary`). Add `self._intro_variant = intro_variant` to the body.

Also add the import: `from app.modules.interview_engine.openers import OpenerVariant` if not already present (it should be — OpenerLibrary already imported from the same module).

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -v 2>&1 | tail -10
```

Expected: ALL PASS, including the 2 new ones AND all existing tests.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/orchestrator.py \
        backend/nexus/tests/interview_engine/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(engine/orchestrator): add intro_variant parameter (default None)

Phase 3 — InterviewOrchestrator now accepts an optional
intro_variant: OpenerVariant. When None (default), behaves as before
(Speaker LLM produces greeting + question for first turn). When set,
the next task's routing change uses it for deliver_first_question.

Backward-compat preserved: 36 existing orchestrator tests still pass
without modification.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Route `deliver_first_question` through `intro_variant` in `_stream_speaker_and_say` (Phase 3, Bug B)

**Files:**
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `tests/interview_engine/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_uses_intro_variant_for_deliver_first_question(
    make_session_config, make_question,
):
    """When intro_variant is set, deliver_first_question routes through
    it (not the OpenerLibrary which has no entry for this kind). The
    intro plays as opener, Speaker generates only the question, and the
    cache holds only the question."""
    from app.modules.interview_engine.openers import OpenerLibrary, OpenerVariant
    from app.modules.interview_engine.event_kinds import SPEAKER_OPENER_PLAYED
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Q?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    state_engine.process_judge_output(
        turn_id="t-0",
        judge_output=state_engine.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle(
        "Walk me through your tool.",
    ))
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))

    intro = OpenerVariant(
        text="Hi, I'm Sam. To start —",
        # No audio_frames → orchestrator falls back to text-only TTS
        # (the test agent mock doesn't actually need audio).
    )
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=MagicMock(), speaker=speaker,
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
        intro_variant=intro,
    )

    speaker_input = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your tool?",
        last_candidate_utterance=None,
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
    )

    fake_agent = MagicMock()
    fake_agent.session.say = AsyncMock(return_value=MagicMock(interrupted=False))

    final_text = await orch._stream_speaker_and_say(
        agent=fake_agent, turn_id="t-1", speaker_input=speaker_input,
    )

    # Speaker content (only the question) is what's returned and cached.
    assert final_text == "Walk me through your tool."
    cache = state_engine._question_utterances.get("t-1")
    assert cache == "Walk me through your tool."
    assert "Hi, I'm Sam" not in cache  # intro is NOT in the cache

    # session.say called twice: once for intro, once for content.
    assert fake_agent.session.say.await_count == 2
    first_call_kwargs = fake_agent.session.say.call_args_list[0].kwargs
    assert first_call_kwargs["text"] == "Hi, I'm Sam. To start —"

    # SPEAKER_OPENER_PLAYED audit fires for the intro turn with
    # is_session_intro=True (added in Task 12).
    opener_events = [e for e in orch._collector.events if e.kind == SPEAKER_OPENER_PLAYED]
    assert len(opener_events) == 1
    assert opener_events[0].payload["instruction_kind"] == "deliver_first_question"


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_library_for_other_kinds_when_intro_variant_set(
    make_session_config, make_question,
):
    """Setting intro_variant only affects deliver_first_question. Other
    kinds still go through OpenerLibrary.pick as before."""
    from app.modules.interview_engine.openers import OpenerLibrary, OpenerVariant
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Q?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle(
        "Which validators would you configure?",
    ))
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))

    intro = OpenerVariant(text="Hi, I'm Sam. To start —")
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=MagicMock(), speaker=speaker,
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
        intro_variant=intro,
    )

    speaker_input = SpeakerInput(
        instruction_kind=InstructionKind.push_back,
        bank_text="What is your tool?",
        last_candidate_utterance="vague",
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
        push_back_reason_code="vague_answer",
    )
    fake_agent = MagicMock()
    fake_agent.session.say = AsyncMock(return_value=MagicMock(interrupted=False))

    await orch._stream_speaker_and_say(
        agent=fake_agent, turn_id="t-pb", speaker_input=speaker_input,
    )

    # First say call is the LIBRARY opener for push_back/vague_answer,
    # NOT the intro.
    first_call = fake_agent.session.say.call_args_list[0].kwargs
    assert first_call["text"] != "Hi, I'm Sam. To start —"
    # Should be one of the push_back/vague_answer variants.
    assert first_call["text"] in {
        "Got it.", "OK.", "Right —", "Mhm —", "Hmm —",
        "OK, let me press on that —",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -k "uses_intro_variant_for_deliver_first_question or falls_back_to_library_for_other_kinds" -v 2>&1 | tail -10
```

Expected: 2 FAIL — orchestrator doesn't yet route through `intro_variant`.

- [ ] **Step 3: Add routing in `_stream_speaker_and_say`**

In `app/modules/interview_engine/orchestrator.py`, find `_stream_speaker_and_say` (~line 670). Locate the line that picks the opener (~line 683):

```python
opener = self._opener_library.pick(
    kind=speaker_input.instruction_kind,
    sub_context=sub_ctx,
    recent_openers=self._recent_openers,
)
```

Replace with:

```python
# Phase 3 — per-session persona intro routing.
# deliver_first_question + intro_variant set → use the per-session
# pre-synthesized intro (composed at agent entrypoint with the
# tenant's persona_name). All other kinds + sub_contexts route
# through the static OpenerLibrary as today.
if (
    speaker_input.instruction_kind == InstructionKind.deliver_first_question
    and self._intro_variant is not None
):
    audio_iter_factory = (
        (lambda: iter(self._intro_variant.audio_frames))
        if self._intro_variant.audio_frames is not None
        else None
    )
    opener = OpenerSelection(
        text=self._intro_variant.text,
        audio_iter=audio_iter_factory,
    )
else:
    opener = self._opener_library.pick(
        kind=speaker_input.instruction_kind,
        sub_context=sub_ctx,
        recent_openers=self._recent_openers,
    )
```

Make sure `OpenerSelection` and `InstructionKind` are imported at the top of orchestrator.py (likely already are — verify).

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py -v 2>&1 | tail -15
```

Expected: ALL PASS, including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/orchestrator.py \
        backend/nexus/tests/interview_engine/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(engine/orchestrator): route deliver_first_question through intro_variant

Phase 3 — when intro_variant is set, _stream_speaker_and_say uses it
as the OpenerSelection for deliver_first_question (instead of the
empty pick() result the library returns for that kind). Reuses the
existing parallel-dispatch path verbatim. Cache stores only the
Speaker content (the question); intro is played but not cached.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Add `is_session_intro` field to `SpeakerOpenerPlayedPayload` (Phase 3, observability)

**Files:**
- Modify: `app/modules/interview_engine/audit_events.py`
- Modify: `app/modules/interview_engine/orchestrator.py`
- Modify: `tests/interview_engine/audit_events/test_audit_events.py` (or wherever payload tests live — find via grep for `SpeakerOpenerPlayedPayload`)

- [ ] **Step 1: Write the failing test**

Append to the appropriate audit-events test file:

```python
def test_speaker_opener_played_payload_default_is_session_intro_false():
    """Backward compatibility — existing emitters that don't pass
    is_session_intro get False by default."""
    from app.modules.interview_engine.audit_events import SpeakerOpenerPlayedPayload
    p = SpeakerOpenerPlayedPayload(
        turn_id="t-1",
        instruction_kind="push_back",
        sub_context="vague_answer",
        opener_text="Got it.",
        cache_hit=True,
    )
    assert p.is_session_intro is False


def test_speaker_opener_played_payload_accepts_is_session_intro_true():
    from app.modules.interview_engine.audit_events import SpeakerOpenerPlayedPayload
    p = SpeakerOpenerPlayedPayload(
        turn_id="t-0",
        instruction_kind="deliver_first_question",
        sub_context="default",
        opener_text="Hi, I'm Sam. To start —",
        cache_hit=True,
        is_session_intro=True,
    )
    assert p.is_session_intro is True
```

Append a regression to `tests/interview_engine/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_orchestrator_emits_is_session_intro_true_for_intro_path(
    make_session_config, make_question,
):
    """The SPEAKER_OPENER_PLAYED audit fired by the intro path has
    is_session_intro=True; the same audit fired for opener-library
    openers stays False."""
    from app.modules.interview_engine.openers import OpenerLibrary, OpenerVariant
    from app.modules.interview_engine.event_kinds import SPEAKER_OPENER_PLAYED
    from app.modules.interview_engine.models.speaker import (
        InstructionKind, SpeakerInput,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Q?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    state_engine.process_judge_output(
        turn_id="t-0",
        judge_output=state_engine.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("Walk me through your tool."))
    pub = AttributePublisher(room=MagicMock(local_participant=MagicMock(set_attributes=AsyncMock())))

    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=MagicMock(), speaker=speaker,
        attr_publisher=pub, event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
        intro_variant=OpenerVariant(text="Hi, I'm Sam. To start —"),
    )

    speaker_input = SpeakerInput(
        instruction_kind=InstructionKind.deliver_first_question,
        bank_text="What is your tool?",
        last_candidate_utterance=None,
        recent_turns=[], claims_pool_snapshot=[],
        persona_name="Sam",
    )
    fake_agent = MagicMock()
    fake_agent.session.say = AsyncMock(return_value=MagicMock(interrupted=False))

    await orch._stream_speaker_and_say(
        agent=fake_agent, turn_id="t-intro", speaker_input=speaker_input,
    )

    opener_events = [e for e in orch._collector.events if e.kind == SPEAKER_OPENER_PLAYED]
    assert len(opener_events) == 1
    assert opener_events[0].payload["is_session_intro"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine -k "is_session_intro" -v 2>&1 | tail -10
```

Expected: FAIL — field doesn't exist.

- [ ] **Step 3: Add the field**

In `app/modules/interview_engine/audit_events.py`, find `SpeakerOpenerPlayedPayload`. Add:

```python
class SpeakerOpenerPlayedPayload(BaseModel):
    """... (existing docstring) ...

    Phase 3 (Bug B fix, 2026-05-10) — when the orchestrator routes
    deliver_first_question through the per-session persona intro
    (instead of the static OpenerLibrary), the audit event fires with
    ``is_session_intro=True``. Forensic queries can filter on this to
    locate the intro turn within an envelope.
    """
    turn_id: str
    instruction_kind: str
    sub_context: str
    opener_text: str
    cache_hit: bool
    is_session_intro: bool = False
```

In `app/modules/interview_engine/orchestrator.py`, find the `SPEAKER_OPENER_PLAYED` audit emit (in `_stream_speaker_and_say`):

```python
self._append(SPEAKER_OPENER_PLAYED, SpeakerOpenerPlayedPayload(
    turn_id=turn_id,
    instruction_kind=speaker_input.instruction_kind.value,
    sub_context=sub_ctx.value,
    opener_text=opener.text,
    cache_hit=cache_hit,
).model_dump())
```

Modify to set `is_session_intro` based on whether we routed through the intro path. The cleanest way: track a local boolean set during the routing branch (Step 3 of Task 11):

```python
is_session_intro = (
    speaker_input.instruction_kind == InstructionKind.deliver_first_question
    and self._intro_variant is not None
)
if is_session_intro:
    audio_iter_factory = ...  # from Task 11
    opener = OpenerSelection(...)
else:
    opener = self._opener_library.pick(...)
```

And in the audit emit:

```python
self._append(SPEAKER_OPENER_PLAYED, SpeakerOpenerPlayedPayload(
    turn_id=turn_id,
    instruction_kind=speaker_input.instruction_kind.value,
    sub_context=sub_ctx.value,
    opener_text=opener.text,
    cache_hit=cache_hit,
    is_session_intro=is_session_intro,
).model_dump())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec -T nexus pytest tests/interview_engine -k "is_session_intro" -v 2>&1 | tail -10
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/audit_events.py \
        backend/nexus/app/modules/interview_engine/orchestrator.py \
        backend/nexus/tests/interview_engine/
git commit -m "$(cat <<'EOF'
feat(engine/audit): is_session_intro field on SpeakerOpenerPlayedPayload

Phase 3 observability — when the orchestrator plays the per-session
persona intro (instead of a static library opener), the audit event
fires with is_session_intro=True. Default False preserves backward
compatibility with the existing 6 opener kinds.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Wire intro synth into agent.py entrypoint (Phase 3, Bug B)

**Files:**
- Modify: `app/modules/interview_engine/agent.py`

- [ ] **Step 1: Update entrypoint**

In `app/modules/interview_engine/agent.py`, find `entrypoint`. Locate the section where `tts_plugin = build_tts_plugin()` and `opener_library = await _get_or_build_opener_library(tts=tts_plugin)` are called (~line 414, before the `InterviewOrchestrator(...)` construction).

Add the imports at the top:

```python
from app.modules.interview_engine.openers import OpenerVariant, synth_one
```

After the persona name resolution (`state_engine.set_persona_name(...)`) and after the opener library is built, add:

```python
intro_text = _compose_intro_text(persona_name=state_engine.persona_name)
intro_audio = await synth_one(text=intro_text, tts=tts_plugin)
intro_variant = OpenerVariant(text=intro_text, audio_frames=intro_audio)
log.info(
    "engine.intro.built",
    persona_name=state_engine.persona_name,
    cache_hit=intro_audio is not None,
    text_len=len(intro_text),
)
```

Then pass to the orchestrator:

```python
orchestrator = InterviewOrchestrator(
    ...
    opener_library=opener_library,
    intro_variant=intro_variant,
)
```

If `state_engine.persona_name` doesn't exist as an attribute, check `state_engine` for the right access (probably via a getter or a `.persona_name` property — check the state engine API).

- [ ] **Step 2: Smoke check — module imports cleanly**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose exec -T nexus python -c "from app.modules.interview_engine import agent; print('ok')" 2>&1 | tail -5
```

Expected: `ok`.

- [ ] **Step 3: Run engine + composition tests**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator.py tests/interview_engine/test_orchestrator_composition.py -v 2>&1 | tail -10
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(engine/agent): synthesize per-session intro at entrypoint

Phase 3 wiring — after state_engine.set_persona_name(...), the
entrypoint builds an intro OpenerVariant via synth_one(text=...) and
passes it to InterviewOrchestrator. When TTS synth fails permanently
(after retry budget), intro_variant.audio_frames is None and the
orchestrator falls back to live TTS for the intro on the first turn
(degraded by ~1.4s on that turn only).

engine.intro.built log line records persona_name, cache_hit, text_len
per session for forensic completeness.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Composition test — first-question repeat replays only the question (Phase 3, Bug B)

**Files:**
- Modify: `tests/interview_engine/test_orchestrator_composition.py`

- [ ] **Step 1: Write the composition test**

Append to `tests/interview_engine/test_orchestrator_composition.py`:

```python
@pytest.mark.asyncio
async def test_first_question_repeat_replays_only_question_no_intro(
    make_session_config, make_question,
):
    """Phase 3 composition test — drives a 2-turn session:
      Turn 1: deliver_first_question (intro pre-spoken, Speaker emits
              the question content only)
      Turn 2: NextAction.repeat → SPEAKER_CACHED replays ONLY the
              question, NOT the intro
    Bug B from session a998073a-3007-... — the intro got replayed on
    every repeat, sounding robotic and confusing the candidate.
    """
    from app.modules.interview_engine.openers import OpenerLibrary, OpenerVariant
    from app.modules.interview_engine.event_kinds import SPEAKER_CACHED
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, RepeatPayload, TurnMetadata,
    )

    judge_outputs = [
        JudgeOutput(
            observations=[], candidate_claims=[],
            next_action=NextAction.repeat,
            next_action_payload=RepeatPayload(),
            turn_metadata=TurnMetadata(),
        ),
    ]
    speaker_outputs = [
        # on_enter: deliver_first_question — Speaker emits ONLY the question
        "Walk me through your tool of choice.",
    ]

    orch, agent = _build_orch(
        make_session_config=make_session_config,
        make_question=make_question,
        scripted_judge_outputs=judge_outputs,
        scripted_speaker_outputs=speaker_outputs,
        knockout_signal="S1",
    )
    orch._opener_library = OpenerLibrary()
    orch._intro_variant = OpenerVariant(text="Hi, I'm Sam. To start —")

    await orch.on_enter(agent)
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("Can you repeat that question?"),
    )

    state = orch._state

    # The cache holds ONLY the question text — intro never enters the cache.
    cache_values = list(state._question_utterances.values())
    assert len(cache_values) == 1
    assert cache_values[0] == "Walk me through your tool of choice."
    assert "Hi, I'm Sam" not in cache_values[0]

    # The repeat turn fired SPEAKER_CACHED with ONLY the question text.
    cached_events = [
        e for e in orch._collector.events
        if e.kind == SPEAKER_CACHED
    ]
    assert len(cached_events) == 1
    assert cached_events[0].payload["final_utterance"] == "Walk me through your tool of choice."
    assert "Hi, I'm Sam" not in cached_events[0].payload["final_utterance"]
```

- [ ] **Step 2: Run the test**

```bash
docker compose exec -T nexus pytest tests/interview_engine/test_orchestrator_composition.py -k "first_question_repeat_replays_only_question_no_intro" -v 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_orchestrator_composition.py
git commit -m "$(cat <<'EOF'
test(engine): composition test for first-question repeat replays only question

Phase 3 regression guard — drives a 2-turn session:
deliver_first_question (intro pre-spoken via intro_variant) → repeat.
Asserts cache holds ONLY the question text (no "Hi, I'm Sam" intro)
and the repeat replay matches. Bug B from session a998073a-3007-...
is closed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Tighten `deliver_first_question.txt` and `_preamble.txt` (Phase 4, Bug D)

**Files:**
- Modify: `prompts/v1/engine/speaker/_preamble.txt`
- Modify: `prompts/v1/engine/speaker/deliver_first_question.txt`
- Modify: `tests/interview_engine/speaker/test_speaker_prompt_loadable.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/interview_engine/speaker/test_speaker_prompt_loadable.py`:

```python
def test_deliver_first_question_documents_anti_pattern_example():
    """Phase 4 — the prompt MUST contain an explicit ANTI-PATTERN
    example that names what NOT to emit (rubric component lists)."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.get("engine/speaker/deliver_first_question")
    assert "ANTI-PATTERN" in body
    # The example should explicitly call out enumeration.
    assert "design or refactor" in body or "comma-separated" in body


def test_deliver_first_question_assumes_pre_spoken_intro():
    """Phase 4 — once intro_variant pre-cache is live, the prompt
    no longer asks the LLM to emit the greeting; it assumes
    pre_spoken_opener carries it (mirroring all other Phase 9.8 prompts)."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.get("engine/speaker/deliver_first_question")
    # The new prompt must reference pre_spoken_opener.
    assert "pre_spoken_opener" in body
    # And must NOT instruct the LLM to start with "Hi, I'm".
    assert "Hi, I'm" not in body or "DO NOT" in body  # explicit prohibition


def test_deliver_first_question_word_cap_dropped_to_20():
    """Phase 4 — hard cap is 20 words now."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.get("engine/speaker/deliver_first_question")
    assert "20 words" in body or "≤ 20" in body


def test_preamble_anti_enumeration_mentions_conjunctions():
    """Phase 4 — the preamble's ANTI-ENUMERATION rule must explicitly
    forbid 'X or Y' verb/object lists (the failure mode in session
    a998073a-3007-...)."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.get("engine/speaker/_preamble")
    assert "or " in body and (
        "pick one" in body.lower() or "pick the broadest" in body.lower()
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py -k "anti_pattern or pre_spoken_intro or word_cap or anti_enumeration_mentions" -v 2>&1 | tail -10
```

Expected: 4 FAIL.

- [ ] **Step 3: Update `deliver_first_question.txt`**

Rewrite the file to assume the intro is pre-spoken via `pre_spoken_opener` and emit ONLY the question:

```
TASK
The persona intro ("Hi, I'm {persona_name}. To start —") has ALREADY
been spoken to the candidate as pre-cached audio (see PRE-SPOKEN OPENER
in the preamble — pre_spoken_opener carries it). Compose ONLY the
substantive question, rephrased from {bank_text} into the shortest,
broadest open-ended form that still names the topic.

OUTPUT (HARD CONSTRAINTS — non-negotiable)
- ONE sentence. ≤ 20 words.
- Pick up where the intro left off — go straight into the question.
  Do NOT include another greeting; the candidate already heard one.
- Use the broadest verb (design, configure, build, scale, refactor,
  walk-through). Use the broadest object (the workflow, the deployment,
  the schema). NEVER preserve "X or Y" verb/object lists from bank_text
  — pick ONE.
- NEVER enumerate sub-components, sub-systems, or evaluation criteria
  from bank_text — those are for the candidate to surface, not for you
  to seed (see ANTI-ENUMERATION in preamble).

ANTI-PATTERN — DO NOT emit anything like this
Input: bank_text mentions "JIRA workflows including custom statuses,
       transition rules, validators and post-functions"
Wrong output: "Walk me through how you would design or refactor a JIRA
              project to match a client workflow, and how you'd package
              it as a reusable template."
Why wrong: enumerated criteria ("design OR refactor", "package as
           reusable template"), 31 words, two verbs, two objects.

EXAMPLES (illustrative — compose from actual inputs; do not copy)

EXAMPLE 1
Input:
"""
pre_spoken_opener: "Hi, I'm Sam. To start —"
bank_text: "Walk me through how you've configured JIRA workflows in a past role, including custom statuses, transition rules, validators and post-functions."
"""
Output:
walk me through a JIRA workflow you configured in a past role.

EXAMPLE 2
Input:
"""
pre_spoken_opener: "Hi, I'm Maya. To start —"
bank_text: "Describe a time you scaled a Postgres database from a few thousand to a few million rows. What did you measure?"
"""
Output:
tell me about a time you scaled a Postgres database into the millions of rows.

EXAMPLE 3
Input:
"""
pre_spoken_opener: "Hi, I'm Sam. To start —"
bank_text: "Tell me about your approach to designing a Kubernetes deployment for a high-availability service."
"""
Output:
talk me through how you'd design a Kubernetes deployment for a high-availability service.

REMINDER: ≤ 20 words. ONE sentence. NO greeting. NO enumeration.
Compose from the broadest form of {bank_text}.
```

- [ ] **Step 4: Update `_preamble.txt` ANTI-ENUMERATION rule**

In `prompts/v1/engine/speaker/_preamble.txt`, find the ANTI-LEAK section (which contains the ANTI-ENUMERATION bullet from the previous prompt-tightening commit). Modify the bullet to also mention conjunctions:

```
- ANTI-ENUMERATION: bank_text often lists the sub-components or
  sub-criteria the candidate is being evaluated on (e.g. "validators,
  conditions, post-functions, screens, fields, automation, reusable
  templates, performance"). NEVER copy that list into your output.
  Naming the criteria the candidate must touch is rubric leakage —
  the candidate is supposed to surface those themselves. Pick the
  broadest open form of the topic and stop there. This applies to
  conjunction lists too: "X or Y" verb/object pairs ("design or
  refactor", "build or scale", "the workflow or the schema") are
  enumeration in disguise — pick ONE.
```

- [ ] **Step 5: Run prompt-loadable tests**

```bash
docker compose exec -T nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py -v 2>&1 | tail -25
```

Expected: ALL PASS, including the 4 new ones.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/prompts/v1/engine/speaker/_preamble.txt \
        backend/nexus/prompts/v1/engine/speaker/deliver_first_question.txt \
        backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py
git commit -m "$(cat <<'EOF'
fix(prompts/speaker): deliver_first_question assumes pre-spoken intro; ≤ 20 words

Phase 4 — once intro_variant pre-cache is live (Phase 3), the Speaker
LLM no longer needs to greet. Prompt now:
  * assumes pre_spoken_opener carries "Hi, I'm Sam. To start —"
  * caps output at 20 words / 1 sentence
  * adds an ANTI-PATTERN example showing the rubric-leak failure mode
    from session a998073a-3007-... (31 words, two verbs, two objects)

_preamble.txt's ANTI-ENUMERATION bullet now explicitly forbids
"X or Y" conjunction lists from bank_text (the residual failure mode
the prior tightening missed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Final full-suite verification + engine restart + manual session

**Files:** none (verification only)

- [ ] **Step 1: Run full engine suite**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose exec -T nexus pytest tests/interview_engine -q --tb=short -m "not prompt_quality" --ignore=tests/interview_engine/test_replay_failing_session.py 2>&1 | tail -10
```

Expected: All passed. Note the count — should be ≥ post-Phase-9.8 count plus the new Phase 9.9 tests (Tasks 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15 add tests).

- [ ] **Step 2: Restart engine**

```bash
docker compose restart nexus-engine
sleep 6
docker compose logs --tail 15 nexus-engine 2>&1 | tail -15
```

Expected: clean restart, no import errors. Look for `engine.intro.built` log line on first session dispatch (which won't fire until a session runs).

- [ ] **Step 3: Manual session test**

User runs a fresh interview session covering:
- First-question delivery with intro audible BEFORE the question
- First-question repeat replays ONLY the question (no "Hi, I'm Sam")
- Push_back + immediate interrupt + repeat → repeat replays the prior question (NOT silence)
- First-question word count ≤ 20 (verify in audit envelope's `speaker.call.final_utterance`)

- [ ] **Step 4: Inspect new envelope**

```bash
ls -t engine-events/*.json | head -1 | xargs jq '.events | map(select(.kind == "speaker.opener.played" or .kind == "speaker.cached" or .kind == "speaker.call")) | map({kind, instr: .payload.instruction_kind, opener: .payload.opener_text, content: .payload.final_utterance, is_session_intro: .payload.is_session_intro, ttft_ms: .payload.latency_ms_first_token})'
```

Verify:
- One `speaker.opener.played` event with `is_session_intro=true` for turn 0
- `speaker.cached` events have non-empty `final_utterance`
- First-question `speaker.call.final_utterance` is ≤ 20 words and contains no enumeration

- [ ] **Step 5: No commit if verification clean**

If anything is off, file a follow-up; otherwise this is the final task.

---

## Self-Review

**Spec coverage:**
- Phase 1 (Bug A — cache integrity): Tasks 1, 2, 3, 4, 5, 6 ✓
- Phase 2 (Bug C — TTS retry): Task 7 ✓
- Phase 3 (Bug B — intro pre-cache): Tasks 8, 9, 10, 11, 12, 13, 14 ✓
- Phase 4 (Bug D — brevity prompt): Task 15 ✓
- Final verification: Task 16 ✓

**Type consistency:**
- `OpenerVariant`, `OpenerSelection`, `OpenerLibrary`, `SubContext`, `synth_one`, `BuildReport` all referenced consistently against the current code shapes (verified via grep before plan was written).
- New `intro_variant: OpenerVariant | None = None` orchestrator constructor parameter is backward-compatible.
- `is_session_intro: bool = False` audit field is backward-compatible.
- New `register_agent_question_for_repeat` method is additive; old `register_agent_utterance` shrinks (transcript-only) but signature unchanged.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" placeholders.

**Sequencing dependencies:**
- Task 1 → Task 2 → Task 3 (Phase 1 cache contract requires the new method, then strips the old behavior, then updates orchestrator success path)
- Task 4 (orchestrator handler call sites + comment cleanup) depends on Task 2's strip
- Tasks 5, 6 (composition tests) depend on Task 4 being correct
- Task 7 (Phase 2) is independent — can ship anytime after Task 6
- Task 8 (synth_one helper) depends on Task 7 (uses the retry behavior)
- Task 9 (`_compose_intro_text`) is independent of all others
- Tasks 10, 11, 12 (orchestrator routing + audit field) depend on Task 8
- Task 13 (entrypoint wiring) depends on Tasks 8, 9, 10
- Task 14 (composition test) depends on Tasks 11, 12
- Task 15 (prompt) depends on Phase 3 being live (the prompt assumes pre_spoken_opener carries the intro)
- Task 16 (verification) depends on all preceding

If executed via subagent-driven-development, dispatch tasks in the order above. Tasks within a phase can pause for a reviewer cycle without blocking later phases.

If the implementation surfaces unexpected divergence from this plan (test that fails for an unforeseen reason, API quirk in the OpenAI SDK), the executing agent should pause, surface the issue, and let the human decide before proceeding.
