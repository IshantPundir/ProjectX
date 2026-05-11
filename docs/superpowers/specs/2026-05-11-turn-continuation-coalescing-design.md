# Turn Continuation Coalescing — when the candidate keeps talking past an EOU

**Status:** Draft for user review · **Date:** 2026-05-11

## Summary

When a candidate's answer arrives as multiple short utterances separated by mid-thought pauses, the LiveKit framework's end-of-utterance (EOU) detector fires after each segment and the orchestrator processes each as an independent turn. In a recent noisy-office session, this produced a cascade of three back-to-back turns ("Right —", "OK —", "Hi there.") where only the openers played because each new turn arrived before the prior turn's Speaker could deliver its body. The candidate saw a confusing pile-up of acknowledgments and never received the actual probe the Judge had decided to send.

This spec introduces **Continuation Coalescing**: when a new user turn arrives within a configurable window of the prior turn's `turn.completed`, AND the prior turn's Speaker never successfully delivered its body, the orchestrator **prepends the prior turn's candidate text to the new turn's text before calling Judge**. The Judge sees the combined utterance as a single answer; the Speaker produces a single response. The prior turn's state mutations (claims, ledger observations, push_back_count) stay in place — coalescing operates on the candidate-utterance text only, not on the irreversible StateEngine side-effects.

Coalescing is layered with two `.env`-only tunings already applied: more patient endpointing (`min_delay=1.5s`, was 1.0s) and a higher EOU confidence floor (`unlikely_threshold=0.5`, was unset). Those reduce the number of EOU events that fire mid-thought; coalescing catches whatever still slips through.

## Non-goals

- **No StateEngine rollback.** The prior turn's claims, ledger observations, push_back_count increments, advance transitions, and quality-gate observations stay. Reversing those mutations would require versioned snapshots that don't exist today, and the existing mutations are still valid signals about the candidate's answer (the answer was incomplete; the count was correct in that moment).
- **No mid-Speaker interruption from outside.** The framework serializes `on_user_turn_completed`. A new turn cannot preempt an in-flight turn from outside that call. Coalescing operates at the entry of the *next* call, not by reaching into the previous one.
- **No retroactive "un-emit" of audit events.** `TURN_STARTED`, `JUDGE_CALL`, `SPEAKER_OPENER_PLAYED`, and `TURN_COMPLETED` for the prior turn all remain in the envelope. The `TURN_COALESCED` event makes the merge explicit for replay/audit consumers.
- **No change to the Levers 1+2 endpointing tuning.** Those already shipped via `.env`. They reduce EOU firing rate; coalescing handles the residual cases.

## Architecture

### The runtime guarantee we exploit

The LiveKit framework calls `on_user_turn_completed` as a coroutine and awaits it to completion before delivering the next EOU. The new turn's `on_user_turn_completed` does not run until the prior turn's call has returned. By the time the new turn enters the orchestrator, the prior turn's `TURN_COMPLETED` has been emitted and the prior turn's lifecycle is observable.

### The coalesce condition (all of the following)

1. **A prior turn exists.** `self._last_turn is not None` — i.e., this isn't the first turn of the session.
2. **The prior turn's Speaker did not successfully deliver its body.** Formally: `self._last_turn['speaker_emitted_content']` is `False`. The orchestrator computes this at end-of-turn as `not interrupted and bool(final_text.strip())`. A Speaker that produced empty output or was interrupted before/during TTS does not count as "delivered."
3. **The gap is within the coalesce window.** `(time.monotonic() - self._last_turn['completed_monotonic']) * 1000 < settings.engine_coalesce_window_ms`. The window is a safety net to prevent stale merging if the framework happens to queue a turn long after the prior one finished.
4. **The prior turn's `(instruction_kind, sub_context)` is in the coalescible set** (see classification below).
5. **The lifecycle is not closing/closed.** This is already enforced by the existing hard-stop at `orchestrator.py:333` and runs *before* the coalesce check.
6. **Coalescing is enabled.** `settings.engine_coalesce_enabled` is `True` (kill switch).

If any condition fails, the turn proceeds normally without coalescing.

### Coalescible classification

The check is on the prior turn's `(instruction_kind, sub_context)` pair.

| `instruction_kind` | Sub-contexts that coalesce | Rationale |
|---|---|---|
| `deliver_first_question` | — | First turn; no prior text exists. Implicitly never coalesces because `self._last_turn` is `None`. |
| `deliver_question` | `default`, `post_cap_advance` | A fresh question was about to be asked; if the candidate's reply fragmented, the new fragments are continuation of the same answering attempt. |
| `deliver_probe` | `default` | A follow-up probe was about to be asked; same reasoning. |
| `push_back` | `vague_answer`, `deflection`, `missing_specifics`, `unanswered_subquestion` | The Judge wanted more specifics; if the candidate's "more specifics" arrived in two fragments, combine. |
| `clarify` | `default` | The Judge wanted to rephrase the question; if the candidate then started answering in fragments, combine. |
| `acknowledge_no_experience` | `default` | The Judge wanted to confirm "no experience" routing; if the candidate then added context in fragments, combine. |
| `repeat` | — | **Does not coalesce.** Cache replay is atomic — either the candidate heard the prior question or they didn't, and if they didn't, they'd ask "please repeat" again rather than continue. Speaker is treated as "successfully delivered" for cache replays, so condition 2 already excludes it. |
| `redirect` | `social_or_greeting` only | If the candidate said "hi how are you" and then started substantive content, treat as continuation. |
| `redirect` | `off_topic`, `abusive`, `injection` | **Does not coalesce.** These are explicit judgments about the candidate's behavior — combining wouldn't help and could mask abuse. |
| `polite_close` | — | **Does not coalesce.** Session is closing; any further utterance is post-close and is handled by `_handle_post_close_turn`. |
| `end_session` | — | Same — never reaches the Speaker as a regular kind. |

The coalescible set is a frozenset of tuples, declared in `orchestrator.py` near the top of the module, so the classification is testable in isolation and visible to anyone reading the file.

### The coalesce action

When the condition fires, in `on_user_turn_completed`, **after the lifecycle hard-stop check** at line 333 and **before** the `_turn_index += 1` at line 341:

1. Compute `combined_text = self._last_turn['candidate_text'] + " " + candidate_text` (single space separator; the Judge prompt handles punctuation/casing normalization).
2. Emit a `TURN_COALESCED` audit event with the payload described below.
3. Set `candidate_text = combined_text` and proceed normally. The new turn gets a fresh `turn_id`, a fresh `_turn_index` increment, a fresh `TURN_STARTED` event, a fresh Judge call, and a fresh Speaker call — only the *input text* is merged.
4. Clear `self._last_turn` after coalescing so a third consecutive fragment doesn't double-merge (the new turn's outcome will re-populate `self._last_turn` for future coalescing decisions).

The prior turn's `TURN_STARTED`/`JUDGE_CALL`/`SPEAKER_OPENER_PLAYED`/`TURN_COMPLETED` events remain in the audit envelope. The `TURN_COALESCED` event ties them to the new turn explicitly so replay tooling can render the merged view.

### Audit event — `turn.coalesced`

New event kind registered in `event_kinds.py` and added to `ALL_EVENT_KINDS`. Payload (`audit_events.py::TurnCoalescedPayload`):

```python
class TurnCoalescedPayload(BaseModel):
    prior_turn_id: str          # the previous turn whose text we're merging in
    current_turn_id: str        # the new turn doing the merging
    prior_text: str             # the prior turn's candidate utterance (for replay)
    current_text: str           # the new turn's candidate utterance, pre-merge
    combined_text: str          # what the Judge actually sees
    prior_instruction_kind: str # InstructionKind value as string
    prior_sub_context: str      # SubContext value as string ("default" if none)
    gap_ms: int                 # elapsed milliseconds between prior turn.completed and this turn.started
    coalesce_window_ms: int     # the configured window for forensic clarity
```

`prior_text` and `current_text` carry candidate utterance content. Per the existing redaction contract, they're redacted to length+hash in `metadata` mode and preserved verbatim only in `full` mode. The redaction wrapper lives in `EventCollector` and is applied automatically by the existing field-level redaction logic when the payload model is recognized — implementation detail handled in the plan.

### State on the orchestrator

Two new fields on `InterviewOrchestrator.__init__`:

```python
self._last_turn: _PriorTurnSnapshot | None = None
```

Where `_PriorTurnSnapshot` is a private dataclass:

```python
@dataclass(frozen=True)
class _PriorTurnSnapshot:
    turn_id: str
    completed_monotonic: float       # time.monotonic() at TURN_COMPLETED
    candidate_text: str              # the prior turn's candidate utterance
    instruction_kind: str            # InstructionKind.value
    sub_context: str                 # SubContext.value
    speaker_emitted_content: bool    # not interrupted AND final_text.strip() is non-empty
```

Populated in `on_user_turn_completed` at the very end, right before `TURN_COMPLETED` is emitted. For the `repeat` cache-replay branch, `speaker_emitted_content` is set to `True` (the prior question replayed atomically). For `_handle_post_close_turn`, `_last_turn` is **not** updated (post-close turns don't participate in coalescing).

### Settings — two new fields, both in `Settings` + `AIConfig` accessor

```python
# Continuation coalescing — when a new turn arrives within this window of the
# prior turn's TURN_COMPLETED AND the prior turn's Speaker did not deliver its
# body, the new turn's candidate text is prepended with the prior turn's text
# before the Judge call. State mutations from the prior turn are not reverted;
# only the user-facing utterance text is merged.
engine_coalesce_enabled: bool = True
engine_coalesce_window_ms: int = 5000  # generous safety net; the primary gate
                                       # is speaker_emitted_content
```

Validator: `1 <= engine_coalesce_window_ms <= 30000` (1ms to 30s). Outside that range is almost certainly a misconfiguration.

The window default is intentionally generous (5s). The primary gate is `speaker_emitted_content`; the window is just a guard against stale merging. If the prior turn's Speaker didn't deliver and the new turn arrives 4 seconds later, the candidate is almost certainly still trying to complete the same answer.

### Interaction with Levers 1 + 2

| Lever | Effect | Coalescing interaction |
|---|---|---|
| 1 — `endpointing.min_delay=1.5s` (was 1.0s) | EOU requires 1.5s of silence before firing | Reduces number of EOU events. Coalescing fires less often because EOU fires less often. |
| 2 — `turn_detector_unlikely_threshold=0.5` (was unset) | Multilingual detector needs higher EOU confidence | Same — fewer EOU events. Stacks with Lever 1. |
| 3 — Continuation coalescing | Merges adjacent turns when prior Speaker didn't deliver | Catches the residual cases. |

The three layers are independent and cooperative. None of them depends on the others to function.

## Files to change

| File | Change |
|---|---|
| `backend/nexus/app/config.py` | Add `engine_coalesce_enabled: bool = True` and `engine_coalesce_window_ms: int = 5000` with `field_validator` for the range. |
| `backend/nexus/app/ai/config.py` | Add `engine_coalesce_enabled` and `engine_coalesce_window_ms` property accessors (mirroring existing engine settings). |
| `backend/nexus/app/modules/interview_engine/event_kinds.py` | Add `TURN_COALESCED = "turn.coalesced"` and add to `ALL_EVENT_KINDS`. |
| `backend/nexus/app/modules/interview_engine/audit_events.py` | Add `TurnCoalescedPayload` Pydantic model. |
| `backend/nexus/app/modules/interview_engine/orchestrator.py` | Add `_PriorTurnSnapshot` dataclass + `_COALESCIBLE_KINDS` frozenset + `_last_turn` field on `InterviewOrchestrator` + `_should_coalesce()` helper. Wire the check into `on_user_turn_completed` between the lifecycle hard-stop (line 333) and the turn-index increment (line 341). Populate `_last_turn` at end of turn. |
| `backend/nexus/tests/interview_engine/test_orchestrator_coalescing.py` *(new)* | Test matrix covering every coalescible/non-coalescible kind, window-expiry, kill switch, redaction, and a full happy-path E2E test. |
| `backend/nexus/tests/test_engine_settings.py` | Add tests for the new Settings fields + validator. |
| `backend/nexus/.env.example` | Document the new env vars under the engine knobs section. |
| `backend/nexus/CLAUDE.md` | One paragraph in the "Phase 3D.engine" section noting the coalescing seam exists. |

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Prior turn's state mutations stick even though we re-judge with combined text.** | This is by design (see Non-goals). The Judge's `recent_turns` window already includes the prior turn's transcript entry, so the Judge sees both utterances in conversational context regardless. Coalescing just promotes the prior text from "previous turn" to "current utterance" so the Judge applies its quality gate to the full answer. |
| **Double-merging across three consecutive fragments.** | After coalescing, `self._last_turn` is cleared. If the merged turn's own Speaker doesn't deliver and a *third* fragment arrives, the merged turn will become the new `_last_turn` candidate, but the prior fragment's text was already absorbed into it — no double-counting. |
| **The candidate hears the prior opener ("Right —") then the new combined response.** | Acceptable — the prior opener is a generic acknowledgment, and the new Speaker output addresses the full combined answer. The `_recent_openers` deque has the prior opener in it, so the new turn's opener picker avoids repeating it. |
| **A `redirect/off_topic` prior turn followed by a continuation.** | Excluded from `_COALESCIBLE_KINDS`. The candidate went off-topic; combining wouldn't help. The new turn is treated independently. |
| **A `polite_close` prior turn followed by a continuation.** | The new turn hits the lifecycle hard-stop (`closing/closed`) before reaching the coalesce check. Routes to `_handle_post_close_turn` as today. |
| **Audit replay tooling that assumes 1 user utterance = 1 turn.** | The `TURN_COALESCED` event makes the merge explicit. Replay tooling that reconstructs the candidate-perceived conversation should sum text across coalesced turns; existing tooling reading individual turns continues to work (the prior `TURN_STARTED` / `TURN_COMPLETED` events are unchanged). |
| **PII in the new `prior_text` / `current_text` / `combined_text` payload fields.** | Falls under the existing `ENGINE_EVENT_LOG_REDACTION` policy. In `metadata` mode, redacted to length+hash; in `full` mode (consent-gated audit replay), preserved verbatim. Pattern mirrors existing user-utterance payloads in `audit_events.py`. |
| **Race between `_handle_interrupted_speaker` setting state and `on_user_turn_completed` for the next turn reading it.** | Single-coroutine serialization: the framework awaits the prior `on_user_turn_completed` to completion before starting the next call. `self._last_turn` is set synchronously before the prior `on_user_turn_completed` returns. No race possible. |
| **Coalesce window too aggressive — merges across long pauses.** | The primary gate is `speaker_emitted_content`. If the prior Speaker delivered, coalescing never fires regardless of window. The window is a safety net for the rare case where Speaker didn't deliver AND a long pause separated the fragments. Default 5s is generous but bounded; configurable per deployment. |

## Validation

- **Unit tests** in `test_orchestrator_coalescing.py`: matrix of `(prior_instruction_kind, prior_sub_context, speaker_emitted_content, gap_ms)` → expected coalesce/no-coalesce verdict.
- **Audit-event tests**: `TURN_COALESCED` event appears in the envelope with correct fields; redaction respects `engine_event_log_redaction` mode.
- **Settings tests**: new fields exist with their defaults via `model_fields` introspection; validator rejects out-of-range values.
- **Integration smoke**: simulate two back-to-back `on_user_turn_completed` calls in a fake AgentSession; the first sets `speaker_emitted_content=False` via an injected interruption; assert the second turn's Judge input contains the combined utterance.
- **Real session re-test**: re-run the same noisy-office scenario after this lands. Expected: the candidate's two fragments ("First one, like, I would communicate…" + "They are trying to achieve…") result in **one** Judge call with combined input and **one** Speaker response containing the actual probe.

## Open questions

None at this point. The design has converged.
