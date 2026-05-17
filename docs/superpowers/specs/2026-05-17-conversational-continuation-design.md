# Conversational continuation — pre-Speaker cancellation with state snapshot

**Status:** Draft for implementation · **Date:** 2026-05-17

## Summary

The structured interview engine commits to a reply the moment the framework fires end-of-utterance (EOU). When a candidate pauses mid-thought longer than `engine_endpointing_max_delay` (currently 3.0s) and then resumes speaking, the orchestrator has already started Judge → State Engine → Speaker on the fragment. The continuation arrives as an orphan turn, and because the original fragment is thin in isolation, the Judge emits `push_back`. Two such pushbacks per question hit the State Engine's cap, force-advance fires, and after all mandatory questions advance via cap, the session ends in `polite_close`.

This spec makes the orchestrator refuse to commit a turn until the agent has actually started speaking. Until that commit point, if the candidate resumes speaking for ≥500ms, the in-flight Judge call is cancelled, any State Engine mutations are restored from a snapshot taken at turn start, the candidate's prior text is held in `_pending_continuation_text`, and the next EOU produces a single turn whose text is the prior fragment prepended to the new utterance.

Three things change in the orchestrator:

1. **Snapshot-and-restore on every turn.** The orchestrator takes a full State Engine snapshot at `on_user_turn_completed` entry. Mutations during the turn happen on the live State Engine. If the cancellation watcher fires before the commit point, the orchestrator restores from the snapshot — wiping the partial mutations cleanly.
2. **A cancellation watcher** that listens to `user_state_changed` events. When `new_state="speaking"` is sustained for ≥500ms during the turn, the watcher fires, the Judge task is cancelled, and the orchestrator aborts with `StopResponse`.
3. **A pending-continuation buffer** (`_pending_continuation_text: str | None`). On abort, the prior turn's candidate text is saved here. On the next `on_user_turn_completed`, the buffer is prepended to the new utterance and cleared.

The commit point is the moment the agent's TTS audio first plays — observable via `agent_state_changed: thinking → speaking`. After the commit point, the watcher disengages; anything the candidate says is a new turn handled by the framework's adaptive interruption.

Zone A endpointing is tuned in the same change: switch `endpointing.mode` from `"fixed"` to `"dynamic"`, raise `max_delay` from 3.0s to 4.5s, raise `min_delay` from 0.5s (LiveKit default) to 0.8s. Dynamic endpointing adapts within `[min_delay, max_delay]` based on session pause statistics, reducing how often the cancellation watcher needs to fire.

## The bug, with empirical evidence

Session `2115a63a-6074-4e67-8b03-d1f68afb5290` (2026-05-16, 18:46 UTC) is the canonical reproduction. The candidate `Punar` was answering Q2 (40–50 integration stabilization, position=1, mandatory) of the AI screening stage for the Sr. Integration Engineer JD at Workato. Audit envelope at `backend/nexus/engine-events/2115a63a-6074-4e67-8b03-d1f68afb5290.json`. LiveKit chat history at `tmp/p_3s4zw6vjp1z_RM_5zfwNL5QjTyg_chat_history.json` (item 21 is the orphan-causing utterance, item 23 is the orphan).

### The orphan timeline (audit event t_ms)

| t_ms | Event | What happened |
|---|---|---|
| 331855 | `audio.user.state` listening → speaking | Candidate starts Turn 10: "Hmm I built like dashboards…" |
| 336155 | `audio.user.state` speaking → listening | Candidate finishes phrase ending "…implemented in place" |
| 339156 | `turn.started` #10 | 3.001s endpointing fired → Turn 10 commits |
| 339505 | `audio.user.state` listening → speaking | **Candidate resumes 349ms after turn fired** — the orphan begins |
| 342465 | `judge.call` returns push_back | Judge has decided on the fragment alone |
| 342466 | `audio.speech.created` source=say | Agent TTS scheduled |
| 345408 | `audio.agent.state` listening → speaking | TTS audio starts playing — the candidate is still speaking |
| 347937 | `audio.stt.transcribed` is_final=True | "I'll watch for like metrics like MTTR, failure rates, P95 latencies, incident counts." — captured during agent's TTS |
| 352805 | `audio.agent.state` speaking → listening | Agent finishes its probe |
| 352862 | `turn.started` #11 | The orphan fragment is delivered as a separate turn, 9ms after Turn 10 completes |
| 355192 | `judge.call` returns push_back | Judge sees only the orphan fragment in isolation; it looks thin |
| 355192 | `judge.validation` push_back_cap_reached | Q2's push_back count hits the cap of 2 |
| 355192 | `judge.validation` no_advance_target reason="all mandatory complete" | State Engine fallback: no more mandatory pending |
| 361019 | `speaker.call` instruction_kind=polite_close | Session ends |

The candidate spoke ~3min 50s of a 15-minute stage. Three optional questions (positions 2, 3, 4) were never asked. The early-end IS a separate design concern (Issue 1) and is parked.

### The failure model

EOU is a statistical decision based on audio + transcript signals. Thinking pauses just over `max_delay=3.0s` are statistically indistinguishable from end-of-turn. Once EOU fires, the orchestrator's pipeline is **single-path forward**: Judge call → State Engine mutation → Speaker → TTS. There is no way for a resumed user utterance to interrupt or merge into the in-flight pipeline. The framework's adaptive interruption catches the resumed speech as a candidate-interrupt during TTS, but classifies it as `false` (resumed=True, agent keeps speaking). The STT transcript is captured anyway and queued as the next turn.

The result: thin orphan fragments become "user's answer" inputs to Judge, get classified as inadequate, accumulate push-backs, force-advance, polite-close. Two contributing causes; this spec addresses the upstream cause (orphan fragments reaching Judge). The downstream cap-and-polite-close cascade is Issue 1.

## Why prior approaches failed (and what we keep from them)

This is the third iteration on this problem. The history matters because each prior attempt fixed one thing and broke another.

### Iteration 1 — Turn continuation coalescing (deleted)

Spec: `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`. Shipped briefly, removed in iteration 2.

**What it did**: when a new turn arrived within `engine_coalesce_window_ms` of the prior `turn.completed` AND the prior turn's Speaker had not delivered content (`speaker_emitted_content=False`), the orchestrator prepended the prior turn's candidate text to the new turn's text before the Judge call. State Engine mutations from the prior turn stayed in place.

**What broke**:

- **Trigger was wrong for our actual failure mode.** Coalescing required `speaker_emitted_content=False` (the prior Speaker said nothing usable). Our orphan case is the opposite — the prior Speaker delivered a full probe. Coalescing fired 0 times in the demo session.
- **Five layered guards interacted unpredictably.** Coalescing was one of: continuation coalescing, stale-turn drop-and-drain, post-Judge resumption gate, must-deliver whitelist, lifecycle hard-stop. Any two firing together produced ambiguous state.
- **State mutations stayed after coalescing.** Push-back count from the prior fragment was already incremented; the merged-text Judge call inherited a one-off push-back that didn't match the merged content's quality.
- **Drop-and-drain queue rewrote chronology.** "Stale" fragments could replay later, making audit replay non-deterministic.

### Iteration 2 — Engine simplification (current state)

Spec: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`. Shipped 2026-05-12. Removed all five mitigations.

**Premise**: with Sarvam STT + MultilingualModel turn detector + `unlikely_threshold=0.5` + tighter endpointing, framework-level turn boundaries are clean enough that orchestrator-side mitigations are unnecessary.

**What broke**: the premise was empirically false for thoughtful Indian-English candidates. Sarvam delivers final STT chunks at the moment each phrase ends. The MultilingualModel hints "more to say" but ultimately a 3.32s thinking pause crosses `max_delay=3.0s` and EOU fires anyway. The orphan returns.

**What we keep from iteration 2**: the simplicity. The current orchestrator is ~916 lines (down from 1,560). The Judge → State → Speaker path is linear. We are not restoring the five-mitigation tangle.

### Iteration 3 — This spec

Builds on iteration 2's clean orchestrator. Adds one new mechanism (cancellation + snapshot/restore) with one piece of buffer state (`_pending_continuation_text`). Where the coalescing spec layered guards, this spec **prevents** the orphan from reaching State Engine in the first place — by holding the Judge work in a cancellable state until commit.

## Non-goals

- **Not a fix for Issue 1.** Issue 1 is the cascade where two mandatory questions hit push-back cap, the State Engine's `_fallback_advance_to_next_pending` consults only `next_pending_mandatory_id`, and the session polite-closes leaving optional questions unasked. Issue 1 will likely be moot once orphan fragments stop reaching Judge (the cap-hits in session `2115a63a` were driven by orphan fragments). It can be revisited when we have evidence; for now, intentionally out of scope.
- **Not a turn-detector swap.** Sarvam + MultilingualModel + `unlikely_threshold=0.5` stay. Switching to Deepgram Flux (English-only) was rejected for the Indian-English customer base in iteration 2 and that decision stands.
- **Not a `turn_detection="manual"` rebuild.** LiveKit supports `session.commit_user_turn()` / `clear_user_turn()` for manual EOU, but adopting it means rebuilding semantic EOU from scratch. The cancellation-watcher pattern in this spec gives us most of the benefit without the rip-and-replace.
- **Not a TTS-interruption fix.** When the candidate resumes speaking AFTER the agent's TTS has begun playing (post commit point), the framework's adaptive interruption handles it — the new utterance becomes the next turn. The Judge has `recent_turns` context for that case. No coalescing or stitching applies after commit.
- **Not a Judge prompt change.** The prompt at `prompts/v1/engine/judge.system.txt` is unchanged. The `recent_turns` slice the Judge already receives is sufficient for the post-commit case.
- **Not a `chat_ctx` truncation semantics change.** LiveKit auto-truncates `chat_ctx` to "what the user heard" on interrupt. We don't use `chat_ctx` for Judge input (our `llm_node` is no-op); we use the State Engine's own transcript. If post-commit interruption produces confusing Judge inputs, that is a separate prompt-design question and is deferred until we have evidence.
- **Not a State Engine API redesign.** We extend `EngineCheckpoint` with three fields (`turn_count`, `transcript`, `question_utterances`) so the existing snapshot/restore mechanism can round-trip the full mutable state. No new abstractions.
- **No new database migration.** All changes are in-process. `sessions.engine_checkpoint JSONB` already accepts the extended payload.

## Architecture

### Mental model

> **"While I'm thinking, I'm working on a scratch pad. If you keep talking, I throw the scratch pad away and listen. The moment I open my mouth to reply, my work is committed."**

### The two zones

| Zone | Definition | Handling |
|---|---|---|
| **Zone A** | Framework-side EOU decision (before `on_user_turn_completed` is called) | Tune `endpointing` to `dynamic` mode, widen `max_delay` to 4.5s. Reduces how often the watcher needs to fire. |
| **Zone B** | After `on_user_turn_completed` fires, before the commit point | **The cancellation watcher.** If the candidate resumes speaking for ≥500ms before the commit point, cancel the Judge task, restore State Engine snapshot, save text to `_pending_continuation_text`, raise `StopResponse`. |

The commit point is `agent_state_changed: thinking → speaking` — the first audible TTS frame. After that, the watcher disengages and any new candidate speech is handled by the framework's adaptive interruption as a new turn.

### State Engine snapshot/restore — the rollback mechanism

Implementation pattern: **mutate live state, snapshot at turn start, restore on abort.**

Why not "clone, mutate clone, swap on commit"? Two reasons. First, `StateEngine` instances carry wiring (config, resolvers, persona name, knockout policy) that doesn't survive a naive deep copy. Second, snapshot+restore re-uses the existing `EngineCheckpoint` mechanism that already handles serialization for crash recovery — there is one canonical "all the mutable state" definition, and we extend it once. Operationally identical semantics: mutations during the turn are throwaway if abort fires, permanent if commit fires.

#### State to snapshot

The orchestrator takes a snapshot via a new `StateEngine.snapshot_full() -> EngineCheckpoint` method at the top of `on_user_turn_completed`. The snapshot must round-trip the following fields (those that `process_judge_output` and surrounding code mutate):

| Field | File:line | Existing in `EngineCheckpoint`? |
|---|---|---|
| `_ledger` (SignalLedger) | `state/engine.py:122` class body | ✅ `EngineCheckpoint.ledger` |
| `_queue` (QuestionQueue) | `state/engine.py:122` class body | ✅ `EngineCheckpoint.queue` |
| `_claims_pool` (CandidateClaimsPool) | `state/engine.py:122` class body | ✅ `EngineCheckpoint.claims` |
| `_lifecycle` (SessionLifecycle) | `state/engine.py:122` class body | ✅ `EngineCheckpoint.lifecycle` |
| `_turn_count: int` | `state/engine.py:199` | ❌ **must add** |
| `_transcript: list[TranscriptEntry]` | `state/engine.py:198` | ❌ **must add** |
| `_question_utterances: dict[str, str]` | `state/engine.py:197` | ❌ **must add** |

Schema change to `app/modules/interview_engine/state/checkpoint.py`:

```python
class EngineCheckpoint(BaseModel):
    schema_version: int = Field(default=2, ge=1)  # bumped 1 → 2
    session_id: str
    ledger: SignalLedgerSnapshot
    queue: QuestionQueueSnapshot
    claims: ClaimsPoolSnapshot
    lifecycle: LifecycleSnapshot
    last_audit_seq_flushed: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)
    # New in v2 — required for in-turn rollback. Crash-recovery code paths
    # that load an older v1 checkpoint must default these fields safely:
    # turn_count=0, transcript=[], question_utterances={}.
    turn_count: int = Field(default=0, ge=0)
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    question_utterances: dict[str, str] = Field(default_factory=dict)
```

Schema version bump is for forward-compatibility on persisted checkpoints in `sessions.engine_checkpoint JSONB`. The Pydantic `Field(default=...)` declarations satisfy backward-load of v1 payloads.

#### New StateEngine methods

```python
class StateEngine:
    def snapshot_full(self) -> EngineCheckpoint:
        """Capture all mutable in-process state. Cheap O(n) where n = transcript length."""
        # Reuses existing _ledger.snapshot(), _queue.snapshot(), etc.
        # Adds _turn_count, copy.copy(_transcript), dict(_question_utterances).

    def restore_from(self, checkpoint: EngineCheckpoint) -> None:
        """Atomically replace all mutable state with the checkpoint's contents.
        After this call self is byte-identical to the state at snapshot time."""
        # Rebuilds _ledger, _queue, _claims_pool, _lifecycle from their snapshots
        # (existing from-snapshot constructors).
        # Re-assigns _turn_count, _transcript, _question_utterances.
```

The existing `StateEngine.from_checkpoint` (`state/engine.py:939`) reconstructs a full StateEngine from a checkpoint. `restore_from` is the mutating equivalent — it modifies `self` in place, preserving the wiring (config, resolvers, persona).

### Cancellation watcher

A standalone async coroutine that listens to `agent.session` events and signals an `asyncio.Event` when the candidate's resumed-speaking sustains ≥500ms.

#### The 500ms threshold

This is a **noise filter**, not a conversational-timing parameter. The framework already uses similar filters (`interruption.min_duration=1.0s`, `interruption.min_words=2` on adaptive interruption). 500ms is below the framework's barge-in threshold because the watcher's purpose is different — it's an upstream cancel before commit, not a barge-in classifier. At 500ms, single coughs and clicks don't trigger; sustained speech (≥1 syllable) does.

The threshold is `engine_continuation_min_speech_duration_ms`, default 500. Tunable but not expected to need tuning. Not a pacing knob.

#### The mechanism

```python
async def _watch_for_user_resume(
    session: AgentSession,
    cancel_event: asyncio.Event,
    min_duration_ms: int,
) -> None:
    """Set cancel_event when user_state==speaking is sustained for min_duration_ms.

    Subscribes to the session's user_state_changed events. Tracks the
    monotonic timestamp of the last listening→speaking transition.
    When a sustain-timer fires while user is still speaking, sets
    cancel_event. Safe to cancel from outside.
    """
    speaking_since: float | None = None
    sustain_task: asyncio.Task | None = None

    def on_user_state(ev: UserStateChangedEvent) -> None:
        nonlocal speaking_since, sustain_task
        if ev.new_state == "speaking":
            speaking_since = time.monotonic()
            if sustain_task is not None:
                sustain_task.cancel()
            sustain_task = asyncio.create_task(_fire_after(min_duration_ms))
        elif ev.new_state == "listening":
            speaking_since = None
            if sustain_task is not None:
                sustain_task.cancel()
                sustain_task = None

    async def _fire_after(ms: int) -> None:
        try:
            await asyncio.sleep(ms / 1000.0)
            cancel_event.set()
        except asyncio.CancelledError:
            pass  # user stopped speaking before sustain elapsed

    session.on("user_state_changed", on_user_state)
    try:
        # Park until cancelled by caller (turn committed or aborted).
        await asyncio.Event().wait()
    finally:
        session.off("user_state_changed", on_user_state)
        if sustain_task is not None:
            sustain_task.cancel()
```

The watcher is a long-running task that the orchestrator spawns at turn start and cancels at commit or completion. Its only side effect is setting `cancel_event`.

### The commit point

The commit point is `agent_state_changed: thinking → speaking`. This is when the first TTS audio frame plays to the candidate — the moment "the agent has said something."

The orchestrator detects this by listening for one-shot to the `agent_state_changed` event and resolving an `asyncio.Future[bool]` when the transition fires. The future is awaited in parallel with the Speaker stream's natural completion.

```python
async def _wait_for_commit_or_completion(
    speaker_task: asyncio.Task,
    agent_state_to_speaking: asyncio.Future[None],
) -> Literal["committed", "completed_pre_commit"]:
    """Resolve when either the Speaker fully completes OR TTS first plays.

    If Speaker completes before any audio plays (empty output, error,
    framework-level cancel), returns 'completed_pre_commit' — the
    cancellation watcher should still be honored (no audio was heard).

    If TTS plays first (agent_state_to_speaking resolves), returns
    'committed' — we have made an audible commitment; further user
    speech is a new turn handled by the framework's adaptive interruption.
    """
```

After the commit-point fires, the orchestrator cancels the watcher. Any subsequent candidate speech is the framework's responsibility.

### The new on_user_turn_completed flow

```
on_user_turn_completed(new_message):
  1. candidate_text := new_message.text_content
  2. if candidate_text is empty: return
  3. lifecycle hard-stop check (existing): if closing/closed → handle_post_close_turn, return

  4. PENDING-CONTINUATION STITCH
     if self._pending_continuation_text is not None:
         candidate_text = self._pending_continuation_text + " " + candidate_text
         emit TURN_STITCHED_CONTINUATION audit event
         self._pending_continuation_text = None

  5. LOOP GUARD
     if self._consecutive_aborts >= 3:
         emit TURN_LOOP_GUARD_FIRED audit event
         # commit no-matter-what; skip the watcher this turn
         skip_watcher = True
     else:
         skip_watcher = False

  6. SNAPSHOT
     state_snapshot := self._state.snapshot_full()

  7. WATCH + RUN
     cancel_event := asyncio.Event()
     watcher := asyncio.create_task(_watch_for_user_resume(...)) if not skip_watcher else None

     try:
         # Existing Judge → State → Speaker pipeline runs here.
         # The pipeline must be cancellable: the Judge `await` and
         # Speaker `await` must propagate asyncio.CancelledError.
         turn_task := asyncio.create_task(self._run_turn_pipeline(candidate_text))
         cancel_task := asyncio.create_task(cancel_event.wait())

         done, pending = await asyncio.wait(
             {turn_task, cancel_task} - {None},
             return_when=asyncio.FIRST_COMPLETED,
         )

         if cancel_event.is_set() and not _commit_point_reached():
             # ABORT PATH
             turn_task.cancel()
             try:
                 await turn_task  # drain CancelledError
             except (asyncio.CancelledError, Exception):
                 pass
             session.interrupt()  # cancel any TTS that may have started but not committed audibly
             self._state.restore_from(state_snapshot)
             self._pending_continuation_text = candidate_text
             self._consecutive_aborts += 1
             emit TURN_ABORTED_FOR_CONTINUATION audit event (phase, elapsed_ms, text_chars, consecutive_aborts)
             emit STATE_SNAPSHOT_RESTORED audit event
             raise StopResponse()

         # COMMIT PATH
         await turn_task  # propagate any exception
         self._consecutive_aborts = 0
         emit STATE_SNAPSHOT_COMMITTED audit event (no-op marker for forensics)
     finally:
         if watcher is not None:
             watcher.cancel()
         for t in pending: t.cancel()
```

`_commit_point_reached()` reads a flag set inside `_run_turn_pipeline` when `agent_state_changed: thinking → speaking` fires. The flag is `self._tts_committed_for_current_turn`.

### Pipeline cancellability

The orchestrator's existing `_run_turn_pipeline` (whatever the actual private name is in `orchestrator.py`) must propagate `asyncio.CancelledError` cleanly through:

1. **Judge call** (`await self._judge.call(...)`). The Judge service uses `openai.AsyncOpenAI.responses.create`. The OpenAI SDK propagates `CancelledError` through the HTTP request and closes the connection. No code change needed — verify behavior with a test that cancels mid-call.

2. **State Engine mutation** (`self._state.process_judge_output(...)`). Synchronous. Cancellation can fire before or after but not during. If it fires before, no mutation happens; if after, the mutations are part of the live state and will be reverted by `restore_from`. Either way correct.

3. **Speaker stream** (`await self._stream_speaker_and_say(...)`). The Speaker uses `session.say(...)`. If cancelled before audio plays, `session.interrupt()` cleans up any pending TTS without the candidate hearing anything. If cancelled after audio plays, `_commit_point_reached()` returns True and we don't take the abort path.

Audit events emitted during the turn (`state.snapshot`, `judge.call`, `speaker.input`, etc.) are NOT rolled back. They are forensic records of what was attempted. On abort, the additional `TURN_ABORTED_FOR_CONTINUATION` event makes it clear the attempt didn't commit. Replay tooling reading the envelope can identify aborted turns by the presence of this event.

### Pending-continuation buffer

`InterviewOrchestrator._pending_continuation_text: str | None = None`. Initialized in `__init__`. Set on abort. Read-and-cleared at the top of `on_user_turn_completed`.

Stitch format: `prior_text + " " + new_text`. Single space separator. The Judge prompt expects natural English; punctuation/casing normalization is the model's responsibility, not the orchestrator's.

If multiple aborts happen in sequence (consecutive_aborts < 3), the buffer accumulates: each abort overwrites the buffer with the (already-stitched) text from this aborted turn. So after three aborts, the buffer contains the merge of all three prior fragments. Loop guard at 3 forces commit.

### Loop guard

`InterviewOrchestrator._consecutive_aborts: int = 0`. Initialized in `__init__`. Incremented on each abort. Reset to 0 on each commit.

When `_consecutive_aborts >= 3`, the watcher is skipped for the current turn. The Judge → State → Speaker pipeline runs unmonitored, commits, and resets the counter.

3 is a safety bound, not a tuning knob. A candidate who fragments 3 times in a row deserves a reply.

### Zone A — endpointing tuning

In `agent.py:_run_entrypoint` (around line 456 where `TurnHandlingOptions` is constructed):

```python
session = AgentSession(
    # ...
    turn_handling=TurnHandlingOptions(
        turn_detection=build_turn_detector(),
        preemptive_generation={"enabled": False},
        endpointing={
            "mode": settings.engine_endpointing_mode,           # new: "dynamic" by default
            "min_delay": settings.engine_endpointing_min_delay,  # 1.0 → 0.8
            "max_delay": settings.engine_endpointing_max_delay,  # 3.0 → 4.5
        },
        interruption=build_interruption_options(),
    ),
)
```

Settings deltas:

| Setting | Current | New | Why |
|---|---|---|---|
| `engine_endpointing_mode` | (didn't exist; `fixed` hardcoded) | `"dynamic"` | Adapts within `[min_delay, max_delay]` based on session pause statistics. Fast talkers get snappier replies; thinkers get more headroom. Python-only feature; supported by `MultilingualModel` turn detector. |
| `engine_endpointing_min_delay` | 1.0 | 0.8 | Slight tightening for fast turns; dynamic mode pulls toward `min_delay` on average. |
| `engine_endpointing_max_delay` | 3.0 | 4.5 | Headroom for thinking pauses. Empirically the orphan in `2115a63a` had a 3.32s pause; 4.5 swallows it without hitting EOU. |

Dynamic endpointing reference: `https://docs.livekit.io/reference/agents/turn-handling-options/#endpointingoptions` (Python-only, requires `MultilingualModel` or VAD).

## Settings

Add to `app/config.py` (in the `Settings` class, near the existing `engine_endpointing_*` block at line 304):

```python
# Conversational continuation — pre-Speaker cancellation
engine_continuation_enabled: bool = True
engine_continuation_min_speech_duration_ms: int = 500
engine_continuation_consecutive_abort_cap: int = 3

# Zone A endpointing tuning
engine_endpointing_mode: Literal["fixed", "dynamic"] = "dynamic"
engine_endpointing_min_delay: float = 0.8   # was 1.0
engine_endpointing_max_delay: float = 4.5   # was 3.0
```

`engine_continuation_enabled` is the kill switch. When `False`, the orchestrator skips the snapshot/watcher logic and behaves identically to the current iteration-2 code. Reverting a misbehaving deployment is one env-var change.

Mirror in `.env.example` with comments explaining each.

## Audit events

Add four new event kinds to `app/modules/interview_engine/event_kinds.py`. All must be added to `ALL_EVENT_KINDS`:

```python
# Turn-level continuation control (new in 2026-05-17)
TURN_STITCHED_CONTINUATION = "turn.stitched_continuation"
TURN_ABORTED_FOR_CONTINUATION = "turn.aborted_for_continuation"
TURN_LOOP_GUARD_FIRED = "turn.loop_guard_fired"

# State snapshot/restore lifecycle (new in 2026-05-17)
STATE_SNAPSHOT_TAKEN = "state.snapshot.taken"
STATE_SNAPSHOT_RESTORED = "state.snapshot.restored"
STATE_SNAPSHOT_COMMITTED = "state.snapshot.committed"
```

### Payload shapes (Pydantic models in `audit_events.py`)

```python
class TurnStitchedContinuationPayload(BaseModel):
    turn_id: str
    prior_chars: int
    current_chars: int
    combined_chars: int
    gap_ms: int  # monotonic ms between prior abort and this turn.started

class TurnAbortedForContinuationPayload(BaseModel):
    turn_id: str
    phase: Literal["judge", "pre_speaker", "speaker_pre_commit"]
    elapsed_ms: int  # from on_user_turn_completed entry to abort
    text_chars: int  # of the candidate text saved to _pending_continuation_text
    consecutive_aborts: int  # after this abort

class TurnLoopGuardFiredPayload(BaseModel):
    turn_id: str
    consecutive_aborts: int  # at the time of fire (will be ≥3)

class StateSnapshotTakenPayload(BaseModel):
    turn_id: str
    transcript_entries: int
    queue_active_index: int | None

class StateSnapshotRestoredPayload(BaseModel):
    turn_id: str

class StateSnapshotCommittedPayload(BaseModel):
    turn_id: str
```

Field-level redaction follows the existing pattern in `EventCollector` — text-bearing fields (none in these payloads except via integer chars) are safe in both `metadata` and `full` modes.

### Session-summary aggregate

Extend the `audio.tuning_summary` envelope-close event (computed in `agent.py:_compute_audio_tuning_summary`) with a new `continuation` block:

```python
audio_tuning_summary["continuation"] = {
    "aborts_total": int,
    "stitches_total": int,
    "loop_guard_fires": int,
    "commit_point_reached_count": int,  # = total committed turns
}
```

This gives per-session aggregate metrics. Healthy session: 0–2 aborts. Pathological session: >5 aborts in 15min → something else is wrong (mic, fragmented speech pattern, watcher misconfiguration).

## Code touchpoints

### Files to modify

| File | Change |
|---|---|
| `backend/nexus/app/modules/interview_engine/orchestrator.py` | Main pipeline change. New buffer fields, snapshot/restore wrapping `on_user_turn_completed`, `_watch_for_user_resume` helper, commit-point detection via `agent_state_changed`. |
| `backend/nexus/app/modules/interview_engine/state/engine.py` | New `snapshot_full()` method (extends `checkpoint()` with the three new fields). New `restore_from(checkpoint)` method. |
| `backend/nexus/app/modules/interview_engine/state/checkpoint.py` | Bump `schema_version` to 2. Add `turn_count`, `transcript`, `question_utterances` fields with safe defaults for v1 backward-load. |
| `backend/nexus/app/modules/interview_engine/event_kinds.py` | Add six new event kind constants to module-level + `ALL_EVENT_KINDS`. |
| `backend/nexus/app/modules/interview_engine/audit_events.py` | Add six new payload Pydantic models. |
| `backend/nexus/app/modules/interview_engine/agent.py` | Wire endpointing settings (mode/min/max). Extend `_compute_audio_tuning_summary` with `continuation` block. No other changes to this file. |
| `backend/nexus/app/config.py` | Add four new settings, update defaults on existing two endpointing settings. |
| `backend/nexus/.env.example` | Document the four new env vars. |

### Files NOT to modify

- `prompts/v1/engine/judge.system.txt` — Judge prompt is unchanged. The merged-text Judge call uses the same prompt and the same `recent_turns` slice as a normal turn.
- `prompts/v1/engine/speaker/*` — Speaker prompts are unchanged.
- `backend/nexus/app/modules/interview_engine/judge/service.py` — Judge service is unchanged. It uses the OpenAI Responses API which is natively cancellable via `asyncio.CancelledError`.
- `backend/nexus/app/modules/interview_engine/speaker/service.py` — Speaker service is unchanged.
- `backend/nexus/app/modules/interview_runtime/*` — Runtime context builder + result recorder are unchanged.
- All migrations — no schema change needed. `sessions.engine_checkpoint JSONB` accepts the extended payload.

## Test plan

Three layers of test coverage. All run under `docker compose run nexus pytest tests/interview_engine/`.

### Unit tests — orchestrator logic

`tests/interview_engine/test_continuation.py` (new file).

1. **`test_settle_window_aborts_when_user_resumes`**: Mock the framework's `user_state_changed` events to emit speaking after 200ms (well under 500ms threshold). Drive `on_user_turn_completed` and assert that the watcher does NOT fire (turn commits normally).
2. **`test_settle_window_aborts_when_user_speaks_sustained`**: Mock the framework to emit speaking and keep speaking for ≥600ms. Assert the watcher fires, Judge is cancelled, `_pending_continuation_text` is set, `StopResponse` is raised, State Engine is unmodified.
3. **`test_stitch_prepends_pending_continuation`**: Pre-populate `_pending_continuation_text="prior text"`. Drive `on_user_turn_completed` with `new_message.text_content="new text"`. Assert the Judge call receives `candidate_text="prior text new text"` and `_pending_continuation_text` is cleared.
4. **`test_loop_guard_commits_after_3_aborts`**: Drive three consecutive aborts. Assert that the 4th turn skips the watcher and commits unconditionally.
5. **`test_snapshot_round_trips`**: For each pair of (mutation, expected delta), call `snapshot_full()` → mutate → `restore_from(snapshot)` → assert State Engine is byte-identical to pre-mutation. Cover ledger appends, queue advances, queue probe consumption, claims appends, lifecycle transitions, turn_count increments, transcript appends, question_utterances inserts.
6. **`test_abort_during_judge_does_not_mutate_state`**: Configure the Judge mock to await indefinitely. Drive on_user_turn_completed. Trigger watcher mid-Judge. Assert State Engine is byte-identical to entry state.
7. **`test_commit_after_tts_disengages_watcher`**: Mock agent_state_changed to fire `thinking → speaking` 100ms into the Speaker stream. Then emit user_state_changed `listening → speaking` (sustained). Assert the watcher does NOT fire (commit has already happened). The candidate utterance becomes the next turn normally.

### Replay test — past session

`tests/interview_engine/test_continuation_replay.py` (new file).

Load `backend/nexus/engine-events/2115a63a-6074-4e67-8b03-d1f68afb5290.json`. Build a replayer that drives a mock LiveKit session emitting the exact `audio.user.state`, `user_input_transcribed`, and `agent_state_changed` events from the envelope, with their original `t_ms` timing (or compressed for test speed). Wire the orchestrator with the new continuation logic enabled.

Assert:
- Turn 10 in the original envelope is aborted (the candidate resumed at t_ms+349).
- `_pending_continuation_text` holds the Turn-10 text after abort.
- The next EOU (synthesized at original Turn 11's t_ms) produces a single stitched turn with text = `"Hmm I built like dashboards… I'll watch for like metrics MTTR P95 latencies incident counts."`.
- The Judge call on the stitched turn does NOT emit `push_back` (the merged content is concrete enough).
- Total turns processed: 10 (one fewer than the original — the orphan is gone).
- Total `polite_close` instructions emitted: 0 (the session would continue past Turn 10 with concrete observations).

This is the closed-loop validation that the design fixes the original bug on the original data.

### Manual smoke test

Three live-candidate sessions, run by the implementer:

1. **Fast crisp answers**. Candidate answers 3–4 questions with no thinking pauses. Verify: no aborts, no stitches, end-to-end latency unchanged vs. current behavior.
2. **Thoughtful pauses mid-sentence**. Candidate answers with deliberate 4–6s pauses inside a sentence. Verify: aborts fire, stitches produce coherent merged text, Judge emits sensible observations on merged text.
3. **Continued-after-agent-spoke**. Candidate finishes an answer, agent asks a probe, candidate keeps adding to their prior answer mid-probe. Verify: framework's adaptive interruption handles it (this is post-commit, watcher is disengaged); Judge sees prior turn's question + new text in `recent_turns` and decides accordingly.

After each session, inspect the audit envelope at `backend/nexus/engine-events/<session_id>.json`. The `audio.tuning_summary.continuation` block reports aggregate counts.

### Verification commands

```bash
# Run new unit tests
docker compose run nexus pytest tests/interview_engine/test_continuation.py -v

# Run replay test
docker compose run nexus pytest tests/interview_engine/test_continuation_replay.py -v

# Full engine suite + branch coverage
docker compose up -d nexus
docker compose exec nexus python -m coverage run --branch \
    --source=app/modules/interview_engine \
    -m pytest tests/interview_engine -m "not prompt_quality" -q
docker compose exec nexus python -m coverage report --show-missing

# Type check
docker compose run nexus mypy app/modules/interview_engine/

# Lint
docker compose run nexus ruff check app/modules/interview_engine/
```

## Build sequence

Two PRs, independently revertable, in order:

### PR 1 — Infrastructure (no behavior change)

- Add `EngineCheckpoint` v2 fields with safe defaults for v1 backward-load.
- Add `StateEngine.snapshot_full()` and `StateEngine.restore_from()`.
- Add the six new event kind constants and Pydantic payloads.
- Add the four new settings (kill switch defaults `engine_continuation_enabled=False`).
- Unit test `test_snapshot_round_trips`.

This PR introduces all the plumbing but leaves the orchestrator unchanged. Merging is risk-free because the new methods aren't called from anywhere.

### PR 2 — Orchestrator wiring (behavior change)

- Add `_pending_continuation_text` and `_consecutive_aborts` to `InterviewOrchestrator.__init__`.
- Add `_watch_for_user_resume` async helper.
- Add commit-point detection (`agent_state_changed` listener inside `_run_turn_pipeline`).
- Wrap `on_user_turn_completed` with the snapshot + watcher + abort logic.
- Flip `engine_continuation_enabled` default to `True`.
- Flip `engine_endpointing_mode` default to `"dynamic"`, `min_delay` 1.0 → 0.8, `max_delay` 3.0 → 4.5.
- Extend `_compute_audio_tuning_summary` with the `continuation` block.
- All unit tests except `test_snapshot_round_trips`.
- Replay test on session `2115a63a`.

This PR turns on the new behavior. If anything misbehaves in production, set `engine_continuation_enabled=False` to revert (no code rollback needed for the runtime change; endpointing remains tuned).

## Trade-offs and known limitations

**One wasted Judge call per abort.** If the watcher fires after the Judge has already returned but before commit, the Judge's output is discarded and we eat its cost (~3¢ at GPT-5.4-mini rates). Acceptable — aborts are rare (target <2 per session) and the cost is dwarfed by the savings from not entering Issue-1's polite-close cascade.

**500ms detection latency on abort.** Between the candidate's first speaking frame and the watcher firing, 500ms elapses. During those 500ms, the Judge call is racing the watcher. If the Judge happens to be very fast (<500ms — rare), the Judge output and State Engine mutation will already be in flight when the abort fires. The `restore_from` handles this case correctly.

**No abort during TTS.** Once the candidate hears any agent audio, the turn commits. If they resume speaking immediately after, that's a new turn handled by adaptive interruption. The Judge has `recent_turns` context including the prior agent utterance; it can semantically reconcile but doesn't perform any explicit "merge with prior" logic. If empirical sessions show this case produces low-quality Judge decisions (we'll know from the audit envelope), the follow-up is a small Judge prompt update — not a re-design.

**Audit envelope shows aborted turns.** A session with 2 aborts will show 13 `turn.started` events but only 11 `turn.completed` events. Replay tooling needs to handle `turn.aborted_for_continuation` payloads. Existing tooling that assumes `turn.started → turn.completed` will need a small update.

**The 500ms threshold is a parameter.** Although classified as a noise filter (analogous to adaptive interruption's `min_duration`), it is technically tunable. If a future candidate population produces consistent false-positive aborts at 500ms, raising to 700ms is a one-line config change. Not expected to need tuning.

## What we deliberately defer

- **Issue 1 (cap-forced advance → mandatory-only fallback).** Likely subsumed by this fix; revisit with empirical data.
- **Truncated vs. full agent utterance in `recent_turns`.** Today we capture the Speaker's accumulated streamed text at interrupt time. Whether this matches "what user heard" exactly or includes some pre-buffered tokens is unclear without code-diving the Speaker / TTS interaction. Defer until we have evidence of Judge mis-decisions on post-interruption turns.
- **Judge prompt awareness of continuation.** The Judge prompt has no explicit "if your prior utterance was truncated, treat next user text as continuation" guidance. Today's `recent_turns` slice is the only signal. If post-commit continuation produces poor Judge decisions, add a clause. Not before evidence.
- **Min consecutive speech delay.** LiveKit's `min_consecutive_speech_delay` (Python-only, between consecutive agent utterances) is unset. Useful for "breath gap" between back-to-back probe and follow-up. Out of scope.
- **TTS preemption + re-call Judge ("Approach C" from the design discussion).** Most invasive; smallest empirical justification today. If post-commit continuation problems persist after this spec ships, revisit.

## References

- LiveKit `TurnHandlingOptions` reference: `https://docs.livekit.io/reference/agents/turn-handling-options/`
- LiveKit turn detector / endpointing modes: `https://docs.livekit.io/agents/logic/turns/turn-detector/`
- LiveKit adaptive interruption: `https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/`
- LiveKit pipeline nodes & `on_user_turn_completed` semantics: `https://docs.livekit.io/agents/logic/nodes/`
- LiveKit `StopResponse` usage: same page, "On user turn completed" section
- LiveKit MCP server for future doc lookups: `https://docs.livekit.io/mcp`
- Deleted iteration-1 spec: `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`
- Iteration-2 simplification spec: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`
- Canonical failure session: `backend/nexus/engine-events/2115a63a-6074-4e67-8b03-d1f68afb5290.json`
- Root + backend conventions: `CLAUDE.md`, `backend/nexus/CLAUDE.md`

## Acceptance criteria

The fix is complete when:

1. All unit tests in `tests/interview_engine/test_continuation.py` pass.
2. The replay test against session `2115a63a` shows: 0 orphan turns, 0 `polite_close` instructions, ≥1 successful stitch event.
3. Three manual smoke sessions produce sensible behavior (per the manual smoke test section above).
4. `mypy app/modules/interview_engine/` and `ruff check app/modules/interview_engine/` pass.
5. The kill switch `engine_continuation_enabled=False` reverts to iteration-2 behavior verified by replay test.
6. The audit envelope for a fresh session includes the `continuation` aggregate block in `audio.tuning_summary`.

---

# Addendum — 2026-05-17 post-first-session revision (Option C)

**Status:** Shipped · **Date:** 2026-05-17 (same day as the original spec)

The first real-session test (`engine-events/7970e91c-e7ac-4919-a964-7c727b781c75.json`) exposed two serious bugs in the original implementation. This addendum documents them and the Option C remediation.

## Bug A — Judge service swallowed `asyncio.CancelledError`

**Symptom**: The watcher correctly fired `cancel_event` at `t=253810`, the orchestrator called `turn_task.cancel()`, but the turn body ran to full completion at `t=263169` (the Judge → Speaker → TTS pipeline did its entire work). The abort path then emitted `state.snapshot.restored` and `turn.aborted_for_continuation` at `t=263179` — 10 seconds AFTER the candidate had already heard the agent's reply. Across three consecutive aborted turns in this session, the candidate heard four back-to-back agent responses to what was conceptually one user input.

**Root cause** (`judge/service.py:171`):

```python
except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
    last_exc = exc
    attempt_text = None
```

The Judge's retry-on-failure path caught `CancelledError` as if it were a timeout. The retry then ran successfully, returned a normal response, and the turn continued through Speaker → TTS to completion.

**Fix**: removed `asyncio.CancelledError` from the except clause. In Python 3.13 `CancelledError` inherits from `BaseException` (not `Exception`), so the bare `except Exception` clauses elsewhere in the same method do not catch it either. Cancellation now propagates out of `JudgeService.call` cleanly.

**Test**: `tests/interview_engine/judge/test_service.py::test_judge_call_propagates_cancelled_error` — schedules a slow Judge call, cancels the task, asserts `CancelledError` surfaces.

## Bug B — VAD-based trigger fired on non-continuations

**Symptom**: The watcher used `user_state_changed` to detect resumed speech, with a sustained-duration filter (default 500ms). In Turn 9, the candidate said "Uh huh" (which became Turn 9's input). While Turn 9 was processing, the candidate said "Okay Uh, what is an ERP?" — a new question, not a continuation of "Uh huh". The VAD-based watcher couldn't tell them apart and aborted the turn.

**Root cause**: VAD reports speech presence. It cannot distinguish:
- Real speech content vs. cough, throat clearing, room noise
- A continuation of the prior utterance vs. a new, independent utterance

Combined with the 500ms threshold, the watcher aborted whenever the candidate produced any sustained voiced sound during the agent's processing window.

**Fix**: switched the trigger from VAD-based to STT-based. The watcher now subscribes to `user_input_transcribed` and fires only when:
1. `is_final=True` (STT has committed a transcript chunk — confirmed words spoken, not background noise)
2. transcript word count ≥ `engine_continuation_min_word_count` (default 2, matches LiveKit's adaptive-interruption `min_words` convention)

The sustain timer is removed entirely — STT is itself the noise filter.

The renamed setting is `engine_continuation_min_word_count` (replaces `engine_continuation_min_speech_duration_ms`).

**Trade-off acknowledged**: STT-final arrives ~500-1500ms after speech ends. The watcher is slower to fire than VAD, but the noise-rejection gain is far more valuable than the latency. A new utterance below 2 words ("uh okay") no longer triggers spurious aborts.

**Tests updated**:
- `test_watcher_does_not_fire_for_short_transcript` — single 2-word filler doesn't trigger when threshold is 3
- `test_watcher_does_not_fire_for_interim_transcripts` — `is_final=False` events are ignored entirely
- `test_watcher_fires_for_stt_final_with_real_content` — substantive STT-final during Judge does trigger abort
- All other continuation tests updated to use `fire_user_input_transcribed` instead of `fire_user_state`

## What this addendum does NOT change

- **Clone-and-commit semantics** — unchanged. Snapshot at turn start, restore on abort, no rollback of audible commits.
- **Audit event kinds** — unchanged. All six events from the original spec remain.
- **Loop guard at 3 strikes** — unchanged. The cap-based skip-the-watcher path is identical.
- **Continuation aggregate in `audio.tuning_summary`** — unchanged.
- **Zone A endpointing tuning** (dynamic mode, min/max delays) — unchanged.

## Open observation — chat history ordering (informational)

LiveKit's `chat_history.json` orders items by **insertion** (the order events finish processing on the asyncio loop), not by **speaking time** (`started_speaking_at`). When the agent's TTS plays after a brief gap (e.g. polite_close starting 5s after the user's prior utterance stops), and the user speaks AGAIN before the TTS plays, the new user utterance lands AFTER the TTS message in the array even though it was spoken first.

This is a LiveKit framework characteristic, not a bug in our orchestrator. Cross-reference `started_speaking_at` / `stopped_speaking_at` (chronological) with array index (insertion-order) when reading the file.

## Deferred to future evidence

- **Bug 2 nuance**: STT-final still cannot semantically distinguish "continuation of prior answer" from "new independent question." If a candidate finishes their answer, the agent starts processing, and the candidate spontaneously asks a new question (≥2 words), the watcher still aborts. We accept this for now: the merged-text Judge call has the prior turn's context in `recent_turns` and can reason about both utterances semantically. If post-Option-C sessions show this producing poor Judge decisions, we revisit (likely by adding a Judge prompt clause acknowledging stitched-continuation context).
- **Bug 3 (chat_history ordering)**: documentation note only. No code change. If consumers of `chat_history.json` need chronological order, they should sort by `started_speaking_at`.
