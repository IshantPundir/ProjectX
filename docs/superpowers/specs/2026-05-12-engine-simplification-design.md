# Engine simplification — pure turn-based interviewer on Deepgram Flux

**Status:** Draft for user review · **Date:** 2026-05-12

## Summary

The interview engine's orchestrator has accumulated five layered race-condition mitigations
(continuation coalescing, stale-turn drop-and-drain, post-Judge resumption gate, hard-stop, must-deliver
whitelist). Together they make `orchestrator.py` 1,560 lines and four conditions deep before any
candidate text reaches Judge. In a real demo session (`engine-events/96946611-…json`, 2026-05-12),
72% of turns dropped, 29% of Judge calls failed Pydantic validation, and the interview ended early
after only 4 questions because each validation failure forcibly advanced the question bank.

Root cause analysis (see prior turn) traced this to:

1. **STT/EOU fragmentation** — Deepgram nova-3 with default `endpointing=25ms` + `no_delay=True`
   delivered 8 separate `is_final` chunks for one continuous candidate answer. The MultilingualModel
   turn detector fired on each, producing 8 `user_turn` boundaries.
2. **Post-Judge resumption gate over-firing** — VAD oscillates faster (112 listening↔speaking
   transitions in 195s) than Judge can complete (2-9s), so the gate's "did the user resume speaking"
   check returns True almost every turn.
3. **Judge cross-field validator brittleness** — `_check_push_back_alignment` rejects any
   `push_back` paired with a `concrete`/`strong` observation; `synthesize_fallback` then advances the
   queue, force-walking the bank.

This spec rebuilds the orchestrator on the assumption that **a properly-configured upstream
turn-detection stack gives us correct turn boundaries**, eliminating the need for any
orchestrator-side mitigation. The pipeline collapses to one linear `Judge → State → Speaker` path
per turn. Mid-utterance interruption stays adaptive (with a slightly higher `min_duration` for
robustness). The Judge fallback path is hardened so a malformed model output asks for clarification
instead of skipping the question.

**STT/turn-detection choice — Sarvam + MultilingualModel.** The product's first customers are
based in India, so Sarvam STT (Indian-language tuned, code-mix capable) is the default — not
Deepgram Flux (English-only). Sarvam's STT plugin has no semantic EOU detection of its own
(only a `high_vad_sensitivity` flag), so we layer LiveKit's `MultilingualModel` turn detector on
top — it's the LK-blessed pattern for "any STT + sophisticated EOU" and supports Hindi + 13 other
languages. The previous demo session that fragmented so badly was actually running Deepgram nova-3
(an env override) with MultilingualModel — nova-3's aggressive `endpointing_ms=25` defaults
flooded MultilingualModel with micro-finals. Sarvam's chunking is gentler; combined with a more
patient `unlikely_threshold=0.5` on MultilingualModel and tighter `engine_endpointing_max_delay`,
turn boundaries should be clean enough that the orchestrator-side mitigations are no longer needed.
This is an explicit trade: we accept slightly less semantic-EOU awareness than Flux in exchange
for native Indian-English/Hindi/code-mix transcription quality.

## Non-goals

- **Not a Judge prompt revision.** The `social_or_greeting` sticky-flag drift observed in the demo
  (Judge keeps the flag set across turns even after the candidate's content becomes substantive) is
  a prompt issue, separate from the orchestrator strip-down. Tracked separately.
- **Not a VAD provider switch.** LK docs recommend Silero; we use ai-coustics' built-in VAD adapter
  (sharing inference with noise cancellation). Switching is deferred until we have post-strip-down
  empirical data showing whether ai-coustics is contributing to false interrupts.
- **Not a Flux switch.** Earlier drafts of this spec called for Deepgram Flux (`STTv2(flux-general-en)`)
  + `turn_detection="stt"`. Reversed because Flux is English-only with a US-English bias and the
  product's first customers are based in India (Indian English / Hindi / code-mix). Sarvam STT +
  MultilingualModel is the chosen path. The Deepgram and Flux factories stay in `realtime.py` as
  named alternates if a non-Indian deployment ever needs them.
- **Not a TTS provider change.** The 3 sarvam.tts errors observed in the demo session are tracked
  separately; the recovery path (`_RECOVERY_TEXT`) is unchanged.
- **Not a database migration.** Zero schema changes. No alembic revision required.

## Architecture

### What gets removed from the orchestrator

The five mitigation layers and all their tracking state come out:

| Removed surface | Why it can go |
|---|---|
| Continuation Coalescing — `_should_coalesce`, `_PriorTurnSnapshot`, `_COALESCIBLE_KINDS`, `_capture_prior_turn_snapshot`, `_derive_sub_context`, `turn.coalesced` event emission | Fired 0× in the demo session; with single-utterance turn boundaries from Flux it remains 0×. The `2026-05-11-turn-continuation-coalescing-design.md` spec it implements is superseded. |
| Stale-turn drop-and-drain — `_buffer_dropped_text`, `_drain_stale_buffer`, `_stale_buffer`, `_is_stale_turn`, `turn.dropped`/`turn.drain_replayed` event emission | The "stale fragment arrives out-of-order" condition was a workaround for STT-side asynchrony with nova-3's aggressive `endpointing=25ms`. Flux's STT-based EOU produces ordered, complete turn boundaries. |
| Post-Judge resumption gate — `_user_resumed_speaking_after`, `_resumed_speaking_at`, `_MUST_DELIVER_JUDGE_ACTIONS`, the entire `if not is_must_deliver and self._user_resumed_speaking_after(...): ... return` block in `on_user_turn_completed` | The condition it protected against — "user produced a new utterance during Judge processing" — doesn't happen when EOU is correct. The gate's 200ms epsilon was 540× smaller than the actual rate of VAD oscillation observed (~one transition every 1.7s). |
| Tracking state: `_last_turn`, `_last_user_speech_end_monotonic`, `_last_user_speech_end_wall`, `_resumed_speaking_at`, `_stale_buffer` | Only consumed by the removed paths. |
| Config knobs: `engine_coalesce_enabled`, `engine_coalesce_window_ms`, `engine_stale_turn_threshold_ms`, `engine_stale_buffer_max`, `engine_post_judge_resumption_epsilon_ms` | No corresponding code path remains. |

What stays:

- **Hard-stop after `lifecycle.state in (closing, closed)`** — `_handle_post_close_turn`. Not a race
  mitigation; protects against the candidate continuing to talk after the agent has already started
  the polite_close. Independent concern.
- **Speaker error recovery** — `_RECOVERY_TEXT` + `speaker.error` event. Unchanged.
- **Speaker interrupted handling** — `_handle_interrupted_speaker`. The framework cancels the
  in-flight Speaker stream when adaptive interruption fires; we record an empty agent transcript
  and a `speaker.interrupted` audit event. Unchanged.
- **`observe_user_state`** — kept as a thin emitter for the `audio.user.state` audit event. The
  wall-clock recording for the post-Judge gate is dropped; just the audit emission remains.

### The new per-turn pipeline

```
on_user_turn_completed(new_message)
  ├─ candidate_text = new_message.text_content
  ├─ if not candidate_text: return                  # framework-side empty
  ├─ if lifecycle.state in (closing, closed):
  │    handle_post_close_turn()                     # canned terminal message
  │    return
  ├─ turn_id = uuid4(); turn_index += 1
  ├─ APPEND turn.started
  ├─ APPEND state.snapshot                          ← NEW (pre-Judge audit)
  ├─ judge_input = build_judge_input(...)
  ├─ APPEND judge.call WITH input_summary=judge_input.model_dump()  ← FIXED (was {})
  ├─ result = await judge.call(...)
  ├─ decision = state.process_judge_output(...)
  ├─ APPEND judge.validation warnings
  ├─ APPEND speaker.input WITH speaker_input.model_dump()           ← NEW
  ├─ outcome = await stream_speaker_and_say(...)    # blocks on TTS
  ├─ state.set_time_elapsed(elapsed_ms / 1000.0)
  ├─ publish_attributes(...)
  ├─ APPEND turn.completed
  └─ if lifecycle.state == "closing": schedule_shutdown()
```

The pipeline has no early-return branches except the two intentional ones (empty text, lifecycle
closing). No buffer state. No condition is checked twice. The orchestrator becomes ~700 lines
(down from 1,560).

### The trust contract

Three guarantees this design depends on:

1. **The LK framework awaits `on_user_turn_completed` before processing the next user turn.**
   Per the LK pipeline-nodes docs (`/agents/logic/nodes/`), `on_user_turn_completed` is "called
   when the user's turn has ended, before the agent's reply" and the framework awaits its
   completion before adding `new_message` to the chat context. The next callback cannot fire
   until ours returns. This is what makes the strip-down safe — we never have two Judge calls in
   flight, so we never need to gate the second.
2. **Deepgram Flux's STT-based EOU produces one boundary per real turn.** Per LK docs, Flux is "a
   custom phrase endpointing model that uses both acoustic and semantic cues" and is "designed for
   turn-based conversational audio." The MultilingualModel layered on top of nova-3 was firing 109
   EOU detections in 195s; Flux's combined acoustic+semantic decision should fire once per real
   utterance.
3. **Adaptive interruption handles mid-utterance barge-in cleanly.** When the candidate interrupts
   while Speaker is mid-stream, the framework cancels the Speaker stream FIRST, then fires the next
   `on_user_turn_completed` with the candidate's text. We never observe the "two callbacks in
   flight" race.

### Failure modes the strip-down accepts

- **If Flux ever DOES split one continuous answer into two boundaries** (model error or genuinely
  ambiguous pause), they become two separate Judge turns. Judge sees them as two separate utterances;
  State Engine accumulates signals across both into the ledger; the candidate hears two agent
  responses for one logical answer. Worse UX than the previous drain merge — but the audit trail
  shows clearly "Judge fired twice", whereas drain hid the fragmentation. This is an *explicit
  trade*: simplicity + observability over hidden mitigation.
- **If `engine_endpointing_max_delay` (3.0s) is too short for genuine think-pauses**, Flux + max_delay
  could fire EOU in the middle of a thoughtful candidate's pause. We tune up post-launch if observed.

### EOU/STT tuning (Sarvam path)

The session that fragmented so badly (96946611) was actually running Deepgram nova-3 (an env
override of the in-code Sarvam default). With nova-3's aggressive `endpointing_ms=25` +
`no_delay=True`, MultilingualModel was being asked to make EOU decisions on a flood of micro-
finals. The fix is therefore a combination: revert to Sarvam STT (the in-code default), keep
MultilingualModel as the turn detector, and tune three knobs to be more patient.

**STT — Sarvam (no code change to provider; just confirm `INTERVIEW_STT_PROVIDER=sarvam` is the
deployed env value).**

Sarvam STT plugin signature (verified against `livekit.plugins.sarvam.STT`):

```
sarvam.STT(
    language="en-IN",
    model="saaras:v3",
    mode="transcribe",
    high_vad_sensitivity=None,    # left unset; would race ai-coustics VAD
    sample_rate=16000,
)
```

`saaras:v3` is the recommended model for advanced mode control + broader language support.
Sarvam STT exposes NO endpointing / EOU semantics — its only EOU-adjacent knob is
`high_vad_sensitivity`, which we leave unset to avoid racing ai-coustics VAD. The turn-end
decision therefore comes from the layer above: `MultilingualModel`.

**Turn detector — MultilingualModel (kept), `unlikely_threshold` raised None → 0.5.**

`MultilingualModel` is LK's open-weights turn-detection model that consumes STT text + VAD
signals and decides whether the user has finished their thought. It supports English and 13
other languages including Hindi (the model relies on the STT to report the language; Sarvam
reports `en-IN` / `hi-IN` / etc. faithfully).

The `unlikely_threshold: float | None` parameter raises the EOU confidence floor — only fire
end-of-turn when the model is *confidently sure* the user is done. Today this is `None` (plugin
default). Set to `0.5` to be more patient, since Indian-English candidates (the demo profile)
tend to pause mid-thought more than US English speakers and we'd rather wait too long than too
short.

**Endpointing — `max_delay` lowered 6.0s → 3.0s** (LK's documented default). This caps the
upper bound when MultilingualModel doesn't fire on its own. Composed with `min_delay=1.0s` (kept).

**Interruption — `min_duration` raised 0.5s → 1.0s.** Adaptive interruption otherwise unchanged
(`mode="adaptive"`, `min_words=2`, `false_interruption_timeout=2.0`,
`resume_false_interruption=True`). The 1.0s minimum filters more incidental noise.

**Provider alternates kept** — `interview_stt_provider` Literal stays `["sarvam", "deepgram"]`.
The `_build_stt_deepgram` factory (uses `deepgram.STT(model="nova-3")` with MultilingualModel
on top) is preserved as the documented switch-back path. **No Flux factory is added.**

**`build_turn_detector()` is KEPT** — the `MultilingualModel` factory continues to be needed.
The `livekit.plugins.turn_detector.multilingual` prewarm import in `agent.py` is KEPT (the
multilingual model file needs to be downloaded at container build via `agent.py download-files`).

### Judge fallback hardening

Today the path `Judge LLM emits push_back + concrete observation → JudgeOutput Pydantic
validator raises ValidationError → JudgeService catches it and calls synthesize_fallback →
synthesize_fallback returns advance to next pending mandatory` quietly walks the question bank
when the model is borderline confused about specificity. In the demo session this fired 5 times
out of 17 Judge calls.

Two changes:

**1. Soften the Pydantic validator (move enforcement to State Engine).**

In `models/judge.py::_check_push_back_alignment`, the `quality='thin'` rule no longer raises
ValidationError. The validator becomes a no-op (or logs informationally). The structural
invariants (`_check_discriminator_alignment`, `_check_no_experience_action_alignment`) stay
strict — those are non-recoverable.

**2. Add `inverse_quality_gate` in State Engine.**

In `state/engine.py::process_judge_output`, when `action == NextAction.push_back`, BEFORE
incrementing `push_back_count`:

```
if any(obs.quality in (CoverageQuality.concrete, CoverageQuality.strong)
       for obs in judge_output.observations):
    warnings.append(ValidationWarning(
        code="inverse_quality_gate",
        level="warning",
        details={
            "reason": "push_back paired with concrete/strong observation; downgrading",
            "observations": [{"signal": o.signal_value, "quality": o.quality.value}
                             for o in judge_output.observations],
        },
    ))
    if active_q_state.probes_remaining_ids:
        # Pick the first remaining probe id (matches existing
        # _fallback_to_first_unused_probe pattern).
        first_probe_id = active_q_state.probes_remaining_ids[0]
        self._queue.apply_probe(probe_id=first_probe_id, at_turn=self._turn_count)
        instruction = InstructionKind.deliver_probe
    else:
        instruction = self._fallback_advance_to_next_pending(warnings)
```

This mirrors the existing `quality_gated_advance` pattern (advance + all-thin → push_back).

**3. Change `synthesize_fallback` for `validation_error` only.**

In `judge/fallback.py`, when `reason == FallbackReason.validation_error`, return a synthesized
JudgeOutput with `next_action=clarify` (no observations, no claims, payload `{"kind":"clarify"}`).
Other reasons (`timeout`, `parse_error`, `no_advance_target`) keep current behavior.

The State Engine handles `clarify` without queue mutation. Speaker rephrases the active question.
Candidate gets another swing.

### Logging additions

Three new audit affordances, all in `_ENGINE_PASSTHROUGH_KINDS` (never redacted):

**1. `judge.call.input_summary` — populated.**
Replace the hardcoded `{}` at `orchestrator.py:1512`:
```
self._append(JUDGE_CALL, JudgeCallPayload(
    turn_id=turn_id, model=result.model_used,
    prompt_hash="sha256:judge",
    input_summary=judge_input.model_dump(mode="json"),  # was {}
    output=result.judge_output.model_dump(mode="json"),
    latency_ms=result.latency_ms,
    usage=result.usage,
).model_dump())
```
The `JudgeInputPayload` already has all the fields (active_question, signal_coverage,
candidate_claims, recent_turns, push_back_count, dont_know_count, remaining_probes,
time_remaining, candidate_utterance). Just need to plumb `judge_input` into `_append_judge_event`.

**2. `speaker.input` — new event.**
New event kind `SPEAKER_INPUT = "speaker.input"`. New payload `SpeakerInputPayload` containing
`turn_id` + the dict of `SpeakerInput.model_dump()` fields. Emitted by the orchestrator
immediately before `_stream_speaker_and_say`. Lets us audit anti-leak after-the-fact and
reproduce exactly why Speaker said what it said.

**3. `state.snapshot` — new event.**
New event kind `STATE_SNAPSHOT = "state.snapshot"`. New payload `StateSnapshotPayload` containing
`turn_id` + dumps of `ledger_snapshot()`, `queue_snapshot()`, `claims_snapshot()`,
`lifecycle_snapshot()`. Emitted by the orchestrator BEFORE `process_judge_output` runs. With this,
replay tools can deterministically reconstruct any turn's input state.

The `turn.dropped` discriminator that was originally proposed (to distinguish stale-drop from
post-Judge-gate-drop) is N/A under the strip-down — neither path fires. The event kind is retained
in the registry for parsing back-compat with historical envelopes.

## Component changes (file-by-file)

### `app/modules/interview_engine/orchestrator.py`
**Net: ~1,560 → ~700 lines.**

Delete:
- `_PriorTurnSnapshot`, `_SpeakerStreamOutcome.body_started_wall_at`, `_CoalesceDecision`,
  `_should_coalesce`, `_COALESCIBLE_KINDS`, `_MUST_DELIVER_JUDGE_ACTIONS`, `_derive_sub_context`,
  `_capture_prior_turn_snapshot`, `_maybe_coalesce`
- `_is_stale_turn`, `_buffer_dropped_text`, `_drain_stale_buffer`, `_user_resumed_speaking_after`
- `OrchestratorConfig` fields: `coalesce_enabled`, `coalesce_window_ms`,
  `stale_turn_threshold_ms`, `stale_buffer_max`, `post_judge_resumption_epsilon_ms`
- `InterviewOrchestrator` fields: `_last_turn`, `_last_user_speech_end_monotonic`,
  `_last_user_speech_end_wall`, `_stale_buffer`, `_resumed_speaking_at`
- The `original_callback_wall = time.time()` capture and `current_user_stopped_speaking_at`
  extraction at the top of `on_user_turn_completed`

Add:
- `_append_state_snapshot(turn_id)` — emits `state.snapshot` before `process_judge_output`.
- `_append_speaker_input(turn_id, speaker_input)` — emits `speaker.input` before
  `_stream_speaker_and_say`.
- Updated `_append_judge_event` — accepts and populates `input_summary` from the actual
  `JudgeInputPayload`.

Modify:
- `on_user_turn_completed` — collapses to the linear pipeline shown above.
- `observe_user_state` — keeps the `audio.user.state` audit emission only; drops the wall-clock
  recording.

### `app/modules/interview_engine/judge/service.py`
No structural change. The `validation_error` branch in `call()` already routes through `_fallback`;
the change is in `judge/fallback.py::synthesize_fallback`.

### `app/modules/interview_engine/judge/fallback.py`
`synthesize_fallback` splits into two cases:
- `reason == FallbackReason.validation_error` → return a JudgeOutput with `next_action=clarify`,
  empty observations, empty claims, payload `ClarifyPayload()`, default TurnMetadata.
- All other reasons → unchanged (advance to next pending mandatory, or polite_close).

### `app/modules/interview_engine/models/judge.py`
- `_check_push_back_alignment` validator becomes a no-op (or removed).
- `_check_discriminator_alignment` and `_check_no_experience_action_alignment` stay strict.

### `app/modules/interview_engine/state/engine.py`
New branch in `process_judge_output` for `action == NextAction.push_back` — the
`inverse_quality_gate` check described above. Mirrors the existing `quality_gated_advance` pattern.

The existing `push_back_cap_reached` branch (count >= 2 → downgrade to advance with
`is_post_cap_advance=True`) is unchanged and runs AFTER the inverse_quality_gate check.

### `app/ai/realtime.py`
- `build_stt_plugin()`: NO change to provider list. Sarvam stays the default branch; Deepgram
  stays the alternate. **Flux factory NOT added** (Indian customer focus).
- `build_turn_detector()`: KEPT (still needed for MultilingualModel). No code change.
- `build_interruption_options()`: change `min_duration: 0.5 → 1.0`. Other adaptive defaults
  unchanged.

### `app/modules/interview_engine/agent.py`
- KEEP `from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual`
  (the model file still needs to be downloaded for prewarm).
- KEEP `turn_detection=build_turn_detector()` in `TurnHandlingOptions`. No change to agent.py
  for the turn-detection path — only the OrchestratorConfig argument list shrinks (Phase 4
  task deletions).

### `app/config.py`
- Delete: `engine_coalesce_enabled`, `engine_coalesce_window_ms`, `engine_stale_turn_threshold_ms`,
  `engine_stale_buffer_max`, `engine_post_judge_resumption_epsilon_ms` (and their validators).
- Change: `engine_endpointing_max_delay: 6.0 → 3.0`.

### `app/ai/config.py` (AIConfig)
- Change: `interview_turn_detector_unlikely_threshold: float | None = None → 0.5` (more patient
  EOU; combines with Sarvam's gentler chunking to keep Judge from running on partial fragments).
- KEEP `interview_stt_provider: Literal["sarvam", "deepgram"] = "sarvam"` (already correct
  default; production env should not override).
- KEEP `interview_stt_model: str = "saaras:v3"`.
- **Not added**: any Flux config knobs.

### `app/modules/interview_engine/audit_events.py`
- Add `SpeakerInputPayload` (turn_id + dict).
- Add `StateSnapshotPayload` (turn_id + ledger/queue/claims/lifecycle dumps).
- KEEP `TurnCoalescedPayload`, `TurnDroppedPayload`, `TurnDrainReplayedPayload` classes (no longer
  emitted, but historical envelopes still parse).

### `app/modules/interview_engine/event_kinds.py`
- Add `SPEAKER_INPUT = "speaker.input"`, `STATE_SNAPSHOT = "state.snapshot"` constants and to
  `KNOWN_KINDS`.
- KEEP `TURN_COALESCED`, `TURN_DROPPED`, `TURN_DRAIN_REPLAYED` constants (parsing back-compat).

### `app/modules/interview_engine/event_log/redaction.py`
- Add `speaker.input` and `state.snapshot` to `_ENGINE_PASSTHROUGH_KINDS` (forensic completeness;
  these never carry PII the audit hasn't already captured).

## Edge cases & error handling

| Failure mode | Behavior |
|---|---|
| Flux misses an EOU boundary (model error) | Framework's endpointing layer fires after `engine_endpointing_max_delay` (3.0s) of silence. Worst case: candidate waits up to 3s for the agent to start responding. |
| Judge `timeout` / `parse_error` / `no_advance_target` | Unchanged — `synthesize_fallback` produces `advance` to next pending mandatory. |
| Judge `validation_error` | NEW — synthesize `clarify` (no queue mutation). |
| Judge emits `push_back` + `concrete`/`strong` observation | State Engine `inverse_quality_gate` downgrades to `probe` (or `advance` if probes exhausted). |
| Judge emits `push_back` + all `thin` observations | Unchanged — push_back fires normally. |
| Push_back hits cap=2 | Unchanged — `push_back_cap_reached` warning + `is_post_cap_advance=True`. |
| Speaker streaming raises | Unchanged — `_RECOVERY_TEXT` + `speaker.error` event. |
| Speaker interrupted by candidate | Unchanged — framework cancels Speaker stream first, fires next `on_user_turn_completed` cleanly. |
| `on_user_turn_completed` fires while Speaker still streaming | Cannot happen via independent path; framework serializes. Only happens via interruption (handled above). |
| `INTERVIEW_STT_PROVIDER=flux` but plugin missing | `_build_stt_flux()` raises ImportError at session start with a clear message. |
| Existing env files have `ENGINE_COALESCE_ENABLED=true` etc. | Pydantic-settings ignores unknown fields by default. Document removal in `.env.example`. |

## Migration

- **No feature flag.** Single deployment swap; rollback is `git revert`.
- **No database migrations.** Zero schema changes.
- **Audit envelope compatibility.** Existing `engine-events/*.json` files still parse (deleted
  event-kind payload classes retained, just no longer emitted).
- **`tenant_settings.engine_knockout_policy`** — untouched. The close-polite override still works
  because `process_judge_output` keeps that branch.
- **`.env.example`** — confirm `INTERVIEW_STT_PROVIDER=sarvam` (or omit; the in-code default is
  `sarvam`). Set `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=0.5` and
  `ENGINE_ENDPOINTING_MAX_DELAY=3.0`. Remove `ENGINE_COALESCE_*`, `ENGINE_STALE_*`,
  `ENGINE_POST_JUDGE_RESUMPTION_*` entries. Production env files that previously set
  `INTERVIEW_STT_PROVIDER=deepgram` (the override that ran in the broken demo session) must
  be reverted to `sarvam`.
- **`backend/nexus/CLAUDE.md`** — update the Phase 3D entry to reflect the strip-down + Flux
  cutover. Drop the lengthy coalescing description from Phase 3D.coalescing. Add a new line
  noting that the simplification supersedes the 2026-05-11 coalescing spec.

## Testing strategy

Per the user's solo-dev preference (manual testing for AI agents over automated agent eval suites)
and the existing test discipline:

### Unit tests (must-have, blocking merge)

- **`tests/interview_engine/state/test_inverse_quality_gate.py`** — push_back + concrete →
  downgrade to probe; push_back + all-thin → keep push_back; push_back + concrete + no probes
  remaining → advance. Pure State Engine, no LLM mock.
- **`tests/interview_engine/judge/test_validation_error_clarifies.py`** — JudgeService receives
  malformed JSON output → `synthesize_fallback` produces a `clarify` JudgeOutput; State Engine
  consumes it without queue mutation; `next_pending_mandatory_id` is unchanged.
- **`tests/interview_engine/test_orchestrator_audit_shape.py`** — process one synthetic turn end-
  to-end with mocked Judge/Speaker; assert the audit envelope contains in order: `turn.started`,
  `state.snapshot`, `judge.call (with non-empty input_summary)`, `judge.validation` (if any),
  `speaker.input`, `speaker.call`, `speaker.output`, `turn.completed`.
- **`tests/interview_engine/test_orchestrator_strip.py`** — AST-walk test asserting source code
  no longer references the removed symbols (`_should_coalesce`, `_buffer_dropped_text`,
  `_user_resumed_speaking_after`, etc.). Regression gate against accidental re-introduction.

### Update existing tests

- Delete coalescing/drop/drain/post-Judge-gate test files outright — the logic no longer exists.
- Update any orchestrator test that mocks `_user_resumed_speaking_after` or sets
  `OrchestratorConfig(coalesce_enabled=...)` to drop those mocks.

### Composition test (recommended)

- **`tests/interview_engine/test_e2e_simple_flow.py`** — wires real `JudgeService` (with a stub
  OpenAI client returning canned JSON), real `SpeakerService` (stub OpenAI client returning canned
  text), real `StateEngine`, real `InterviewOrchestrator`. Drives 5-6 synthetic candidate turns
  and asserts: question advances correctly, ledger accumulates observations, audit envelope is
  well-formed, no turn drops, no fallback advances on validation_error.

### Manual smoke test (post-merge, performed by user)

- Run a real interview session via `frontend/session`. Verify:
  - Number of `turn.started` events ≈ number of real candidate utterances (1 per answer, NOT 8).
  - Judge `latency_ms` 2-9s per turn, fired once per real answer.
  - `judge.call.input_summary` is populated; `speaker.input` and `state.snapshot` events appear
    for every turn.
  - `engine-events/<session_id>.json` is readable end-to-end without inflated content.
- Test the Judge-fallback path: deliberately give a long substantive answer likely to trip the
  model into push_back+concrete; verify it downgrades to probe instead of force-advancing.

## Supersedes / replaces

- `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md` — the entire
  coalescing mechanism is removed. The two `.env`-only tunings that spec introduced have
  different fates:
  - `engine_endpointing_min_delay` (1.0s) — **kept**. Gives the candidate a small grace pause
    after MultilingualModel fires its EOU.
  - `interview_turn_detector_unlikely_threshold` — **kept and bumped from None → 0.5**. With
    MultilingualModel staying as the turn detector (Sarvam path), the knob is still relevant;
    raising it makes EOU more conservative, helping with Indian-English candidates who pause
    mid-thought.
- `engine_endpointing_max_delay` (6.0s → 3.0s) — tightened. The 6.0s ceiling was compensating
  for MultilingualModel's wide-band uncertainty against nova-3's micro-finals; with Sarvam +
  bumped `unlikely_threshold` the upper bound can return to LK's documented default.

## Open questions / risks

- **Sarvam STT's chunking behavior under MultilingualModel is not empirically validated.** The
  fragmented demo session ran Deepgram nova-3 (env override). Reverting to Sarvam should
  produce gentler chunks because Sarvam has no aggressive `endpointing_ms=25` equivalent —
  but we don't have a baseline session yet. The first post-merge session is the test. If
  Sarvam still fragments, next-step options: (a) raise `unlikely_threshold` further (to 0.7),
  (b) re-evaluate Flux for English-only deployments, (c) explore Sarvam's `flush_signal` and
  `high_vad_sensitivity` knobs.
- **MultilingualModel is less semantically aware than Flux's purpose-built EOU.** Flux uses
  the transcript content itself ("does this look like a complete thought?") as a signal;
  MultilingualModel uses the transcript + VAD silence patterns. The trade is acceptable
  given the Indian-English/Hindi/code-mix transcription quality requirement. Empirical
  validation is pending.
- **`unlikely_threshold=0.5` is a guess.** The LK plugin docs don't publish the model's
  internal probability distribution; 0.5 is a reasonable midpoint for "more conservative
  than default." Tune from real session data.
- **The `_handle_post_close_turn` hard-stop relies on `lifecycle.state in (closing, closed)`
  being correctly set BEFORE the next `on_user_turn_completed` fires.** This is unchanged from
  today, but worth noting as a load-bearing pre-condition the simplified pipeline preserves.
