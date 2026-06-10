# Turn Assembly — merging fragmented candidate turns before the brain

> Design spec. Status: **approved, pre-implementation.**
> Date: 2026-06-10. Module: `app/modules/interview_engine/`.
> Related: [[2026-06-05-interview-engine-gen3-design]], the 2026-06-10 turn-handling
> config consolidation, `interview_engine/AGENT_ARCHITECTURE.md`.

---

## 1. Summary

The interview engine sometimes receives a single spoken answer as **several
consecutive committed turns** ("fragments") because LiveKit's turn detector
ends the turn during a mid-answer think-pause. Today each fragment is processed
as a standalone turn — its own bridge, its own brain call, its own evidence
notes — so no fragment looks like a complete answer. The candidate feels
unheard, the brain over-probes, and the report scores the answer `thin`.

This spec adds **turn assembly**: a single new, livekit-free `TurnAssembler`
that buffers consecutive fragments and merges them into **one logical turn**
before the drive loop runs the bridge/brain. It uses LiveKit's VAD-driven
`user_state_changed` as the "candidate resumed" signal, so it adds **near-zero
latency on clean turns** and only waits a short grace beat near a pause. It also
supports **merge-back** of a continuation that arrives just after a flush, via a
single deterministic checkpoint — no preemption, no supersession of committed
state, none of the gen-2 race complexity that was removed on 2026-05-12.

The downstream orchestration core (`SessionDriver`, `loop.run_turn`, `brain/`,
`mouth/`, `NoteLog`, evidence) is **unchanged** except for one checkpoint and an
abort path.

---

## 2. Problem & context

**Root cause (forensic, session `RM_8oXPvEhZpvxo`, 2026-06-08):** premature
endpointing committed answers as fragments; the engine then ran a full
bridge+brain+real-line cycle per fragment (no coalescing — `driver.py`/`loop.py`)
→ thin/fragmented `SessionEvidence` → a Strong candidate scored Borderline. See
the 2026-06-10 turn-handling config consolidation for the endpointing fix.

**Why endpointing tuning alone is not enough.** The 2026-06-10 fix
(`unlikely_threshold=None`, `min_delay=1.5s`, `max_delay=4.0s`) sharply *reduces*
fragmentation but cannot eliminate it:
- `max_delay=4.0s` force-commits a turn after 4s even when the detector reads it
  as unfinished, so a >4s mid-answer think-pause still fragments.
- The EOU model is 87–96% accurate (per-language true-negative), so it will
  occasionally misjudge a mid-thought pause as a complete turn.

When fragmentation *does* happen, losing/splitting the start of an answer is
catastrophic for fairness. Assembly is the durable belt-and-suspenders.

**The gen-2 lesson (load-bearing constraint).** A prior "continuation
coalescing / stale-turn drop-and-drain / post-Judge resumption gate" stack was
**removed** on 2026-05-12 because it merged *after* the Judge ran and *after*
speaking — supersession races. **This design must never supersede committed
evidence or in-flight speech.** It assembles strictly *before* the brain commits
notes.

---

## 3. Goals / Non-goals

**Goals**
- Merge consecutive fragments of one spoken answer into one logical turn so the
  brain sees the complete answer and the report grades it once.
- Never discard or isolate the opening fragment of an answer.
- Near-zero added latency on clean (non-fragmented) turns.
- Support merge-back of a continuation that arrives just after a flush, with no
  preemption and no supersession of durable state.
- Keep the assembler core livekit-free and unit-testable with a fake clock.
- Leave `SessionDriver`/`loop`/`brain`/`mouth`/`NoteLog`/evidence structurally
  unchanged (one checkpoint + one abort path only).

**Non-goals**
- Re-introducing manual turn detection (`turn_detection="manual"`) — gen-3 keeps
  native turn detection.
- Cancelling an in-flight brain task mid-flight for faster merge-backs
  (preemption) — explicitly rejected for this iteration (see §7).
- Semantic "is this answer complete?" judgement (no extra LLM, no regex — project
  rules). Assembly is driven by VAD state + timing only.
- Changing the report scorer (it already expects one coherent answer per
  question).

---

## 4. Verified LiveKit mechanics (livekit-agents 1.5.7)

Facts this design relies on, verified against the docs MCP and source:

1. **`Agent.on_user_turn_completed(turn_ctx, new_message)`** fires after the turn
   detector confirms EOU and *before* any reply. We already `raise StopResponse()`
   so **nothing is auto-spoken or auto-merged** — the engine owns all output.
   The hook receives **text only** (no EOU probability/delay), so fragment
   detection cannot use endpointing confidence here.
2. **`session.on("user_state_changed")`** emits `UserStateChangedEvent(old_state,
   new_state)`, `new_state ∈ {speaking, listening, away}`, driven by the VAD on
   the user's audio — independent of our turn handling. This is the
   "candidate resumed speaking" signal.
3. **The hook and `user_state_changed` run on the same asyncio event loop**, and
   the drive loop processes turns **strictly sequentially** (`agent.py` awaits
   `handle_turn` → `run_turn` inline; only one turn is in flight at a time). So
   the assembler's state is mutated on a single loop — **no locks, no data
   races**.
4. **No mid-turn cancellation exists today.** Barge-in only sets
   `_InterruptAwareVoice.last_interrupted`; `run_turn` runs to completion.
   (`AGENT_ARCHITECTURE.md §11` claims barge-in cancels `run_turn` — that is
   inaccurate and is corrected by this work; see §15.)
5. **Evidence becomes durable at exactly one line:** `loop.py:231`
   `notelog.append(...)`, which runs *after* the bridge is spoken and the brain
   returns, but *before* the real_line is spoken (L248–249). This is the
   point-of-no-return that makes checkpoint-abort clean.

---

## 5. Architecture & placement

One new unit sits between the LiveKit hook and the existing
`CommittedTurnSource`:

```
on_user_turn_completed(fragment text) ─┐
session "user_state_changed" (speaking/ ─┤→  TurnAssembler  ──flush: merged turn──▶  CommittedTurnSource ──▶ drive loop
                          listening)    ┘     (buffer + merge + grace timer)              (queue, unchanged)        (run_turn, ~unchanged)
                                                      ▲                                                                     │
                                                      └────────────── is_superseded? / confirm_committed ──────────────────┘
                                                                       (one checkpoint at loop.py:231)
```

- The assembler is the **only** new component, and the only piece that needs the
  VAD signal + a timer.
- `CommittedTurnSource` stays the livekit-free queue feeding the drive loop; its
  queued item type changes from `str` to a small **`AssembledTurn`** value object
  (so the merged span + `suppress_bridge` hint travel with the text instead of
  via a side-channel). The `close()`/`None`-sentinel contract is unchanged.
- The bridge automatically fires once per *assembled* turn (because the driver's
  `handle_turn` is called once per flush) — the talk-over symptom disappears for
  free.

**Why a separate unit** (chosen over folding into the source or the driver): the
source and driver remain pure and untouched; the assembler has one clear
purpose; its core is livekit-free and testable. This mirrors the existing
gen-3 pattern (`CommittedTurnSource` is livekit-free; `agent.py` wires it).

---

## 6. The `TurnAssembler`

New file: `app/modules/interview_engine/turn_assembler.py`. Livekit-free.

### 6.1 The `AssembledTurn` value object

The unit the assembler flushes into the source (replacing the bare `str`):

```python
@dataclass(frozen=True)
class AssembledTurn:
    text: str                 # merged fragments, joined with " "
    span: TimeSpan            # [first_fragment_at, last_fragment_at] (coarse wall clock)
    suppress_bridge: bool     # True on a merge-back re-flush (an ack already played)
    is_reflush: bool          # audit/observability: this turn was re-merged after an abort
```

### 6.2 Injected collaborators (for testability)

- `sink: CommittedTurnSource` — the assembler calls `sink.submit(AssembledTurn)`
  on flush and `sink.close()` on session close.
- `clock: () -> float` — monotonic seconds (injected; tests use a fake).
- `timer: TimerScheduler` — a tiny protocol: `schedule(delay_s, callback) ->
  Handle` and `Handle.cancel()`. Production impl wraps `asyncio.get_event_loop().
  call_later`; tests use a manual scheduler that fires on command. This keeps the
  grace-timer logic deterministic in unit tests without real time.
- Config: `grace_s`, `max_duration_s`, `enabled`.

### 6.3 Public methods (called by `agent.py` wiring)

- `submit_fragment(text: str) -> None` — from `on_user_turn_completed`.
- `note_user_speaking() -> None` — from `user_state_changed → speaking`.
- `note_user_stopped() -> None` — from `user_state_changed → listening`.
- `is_superseded() -> bool` — the loop checkpoint reads this for the in-flight turn.
- `confirm_committed() -> None` — the driver calls this once the loop passes the
  checkpoint (point-of-no-return) so future resumes start a fresh turn.
- `close() -> None` — flush any buffered text, then `sink.close()`.

### 6.4 State machine

States: `IDLE`, `BUFFERING`, `IN_FLIGHT`. A boolean `superseded` is meaningful
only in `IN_FLIGHT`. `user_speaking` tracks the latest VAD state. The buffer is a
list of fragment strings; `first_fragment_at` / `last_fragment_at` track wall
clock for the merged span.

| State | Event | Transition / action |
|---|---|---|
| any | `enabled=False` | bypass: `submit_fragment` → `sink.submit(AssembledTurn(text, span, suppress_bridge=False, is_reflush=False))` directly (assembly off). |
| `IDLE` | `submit_fragment(t)` | buffer=[t]; → `BUFFERING`; if not `user_speaking` start grace timer; else hold (no timer). |
| `BUFFERING` | `submit_fragment(t)` | append t; reset grace timer (a 2nd commit *is* a continuation). |
| `BUFFERING` | `note_user_speaking` | `user_speaking=True`; cancel grace timer (continuation in progress). |
| `BUFFERING` | `note_user_stopped` | `user_speaking=False`; (re)start grace timer. |
| `BUFFERING` | grace timer fires | **flush**: build `AssembledTurn` (joined buffer, span, `suppress_bridge`/`is_reflush` per merge-back history) → `sink.submit(...)`; retain buffer+span; → `IN_FLIGHT`, `superseded=False`. |
| `BUFFERING` | buffered ≥ `max_duration_s` | safety force-flush (same as grace fire). |
| `IN_FLIGHT` | `note_user_speaking` | `superseded=True` (continuation detected for the turn the loop is processing). |
| `IN_FLIGHT` | `submit_fragment(t)` | `superseded=True`; move retained text + t into buffer; → `BUFFERING`; start grace logic. |
| `IN_FLIGHT` | `is_superseded()` (loop) | return `superseded`. |
| `IN_FLIGHT` | `confirm_committed()` (loop passed checkpoint, NOT superseded) | clear retained buffer; → `IDLE`. |
| `IN_FLIGHT` | abort signalled (loop saw superseded) | retained text already in `BUFFERING` (or moved there now); normal settle → re-flush merged. |

Notes:
- A `note_user_speaking` during `IN_FLIGHT` with **no** subsequent commit (false
  VAD resume) leaves the assembler in `BUFFERING` with the retained text and a
  grace timer → it re-flushes the *same* text once. Correct (no data loss),
  bounded by `max_duration_s`. The brain re-runs on identical text (rare, wasteful
  but safe).
- Backchannel handling is unchanged and stays **post-assembly** in the driver
  (`is_backchannel` on the assembled utterance). The assembler does not interpret
  text.

### 6.5 Merged turn timing

The hook gives text only, so the merged `TranscriptTurn` span is the coarse wall
clock `[first_fragment_at, last_fragment_at]` (clock-based), an improvement over
today's `now-1000ms..now` (`agent.py:291`). Word-level timing remains re-derivable
from aligned transcripts per the evidence contract. The assembler passes the span
to `handle_turn` (new optional arg) instead of the driver synthesising it.

---

## 7. Merge-back via checkpoint-abort

When the candidate resumes *after* a flush but *before* the brain commits notes,
we merge back through one deterministic checkpoint — no preemption.

Sequence (fragment-1 flushed, candidate resumes during the brain):

```
assembler flush(frag1) → sink → drive loop → handle_turn(frag1)
  loop.run_turn:
    bridge plays (content-free filler — safe to have played)   ~100–300ms
    brain runs                                                  ~2–3s
    ── CHECKPOINT (loop.py, immediately before notelog.append) ──
       ctx.is_superseded()?  ← assembler.is_superseded()
         True  → ABORT: no notes, no real_line; return ABORTED
         False → confirm_committed(); append notes; speak real_line   ← point of no return
```

- On **ABORT**, `handle_turn` pops the single candidate `TranscriptTurn` it
  appended at entry (deterministic unwind — `NoteLog` was never touched), and
  returns an `aborted` outcome. The consume loop treats it as a no-op (does not
  advance question/probe state). The assembler, which already moved
  `frag1 + frag2` into `BUFFERING`, re-flushes the merged turn when it settles.
- A resume that arrives **after** the checkpoint is a genuine new turn:
  frag1's evidence stands; frag2 is a new turn carrying frag1 in its
  `transcript_window` and `evidence_so_far` (the brain still has full context).

**Why wait for the brain instead of cancelling it (rejected option):**
cancelling the in-flight brain task on `note_user_speaking` would shave ~2–3s on
a merge-back but requires running `run_turn` as a cancellable task with external
cancellation — preemption that reintroduces gen-2-style races. Per the
quality-before-latency lock, checkpoint-abort (wait for the brain, then abort) is
chosen. The wasted brain call on an aborted fragment is acceptable.

**Bridge-on-re-flush polish (included):** on a re-flushed (merge-back) turn the
assembler sets a `suppress_bridge` hint so the driver skips the second
content-free ack (one ack already played). Implemented as a flag on the flushed
turn; the driver passes it through to `run_turn` (skip the bridge call, real_line
only). Optional but cheap; keeps the agent from saying "Mm, okay" twice.

---

## 8. Integration points

| File | Change |
|---|---|
| `turn_assembler.py` **(new)** | the assembler core + `TimerScheduler` protocol + an asyncio-backed production scheduler. |
| `agent.py` | construct the assembler (wrapping the existing `CommittedTurnSource`); wire `on_user_turn_completed` → `submit_fragment`; register `session.on("user_state_changed")` → `note_user_speaking`/`note_user_stopped`; inactivity timer resets on every fragment; pass the assembler to `_drive` so the driver can call `is_superseded`/`confirm_committed`. |
| `loop.py` | add a `supersession_check: Callable[[], bool] \| None` to `TurnContext`; at the point-of-no-return (immediately before `notelog.append`, L231) `if supersession_check and supersession_check(): return ABORTED`. Add a `suppress_bridge: bool` to `TurnContext`; skip the bridge task when set. |
| `driver.py` | `handle_turn` accepts the `AssembledTurn` (merged text + span + `suppress_bridge`); on `ABORTED` from `run_turn`, pop the just-appended candidate `TranscriptTurn`, do not advance state, and return a sentinel so the consume loop no-ops; on success call `assembler.confirm_committed()` (injected callable). |
| `turn_source.py` | `CommittedTurnSource` queued item type `str` → `AssembledTurn`; `submit()` signature + the empty-drop guard (now `not turn.text.strip()`) updated; `close()`/`None`-sentinel contract unchanged. |
| `brain/*`, `mouth/*`, `notes.py`, `interview_runtime/evidence.py` | **unchanged.** |

`run_turn`'s return type gains an `ABORTED` sentinel (e.g. return `None` or a
typed `_Aborted`) distinct from a `BrainDecision`; the driver/consume loop branch
on it.

---

## 9. Configuration

Added to the single-source-of-truth turn-handling block in `config.py` (and
documented, commented-out, in `.env.example`):

```python
# Turn assembly — merge fragmented answers before the brain (see the
# 2026-06-10 turn-assembly design spec).
engine_assembly_enabled: bool = True          # kill switch (rollback without code change)
engine_assembly_grace_s: float = 0.5          # wait after a fragment (no VAD resume) before flushing
engine_assembly_max_duration_s: float = 45.0  # safety force-flush ceiling for one assembled turn
```

`grace_s` is the only behaviourally-sensitive knob; default 0.5s, validated on a
talk-test. `enabled=False` makes the assembler a pass-through (`submit_fragment` →
`sink.submit`), exactly today's behaviour — the rollback path.

---

## 10. Edge cases

- **Clean turn:** stop → no resume within grace → flush ~0.5s. Near-zero tax.
- **Pause-then-continue (pre-flush):** VAD speaking within grace (or a 2nd commit)
  → never flushed, never spoke → accumulate → flush once settled. Start never lost.
- **Continuation just after flush (merge-back):** §7 checkpoint-abort.
- **False VAD resume in flight:** §6.4 — re-flush same text once; bounded.
- **Rambling/endless candidate:** `max_duration_s` force-flush; the brain's
  anti-grind + the resolver budget still apply downstream. (Optionally pair with
  LiveKit `user_turn_limit` later; out of scope here.)
- **Pure backchannel assembled turn:** dropped by the existing `is_backchannel`
  gate in the driver (post-assembly).
- **Candidate disconnects / session close mid-buffer:** `close()` flushes buffered
  text (best-effort) then closes the sink; finalize proceeds normally.
- **Intro/opener:** untouched — the assembler only mediates candidate turns; the
  agent's opening speech path is unchanged.
- **`enabled=False`:** pass-through; identical to pre-spec behaviour.

---

## 11. Observability

- `engine.assembly.fragment_buffered` (fragment_len, buffer_count, state)
- `engine.assembly.flushed` (fragment_count, merged_len, buffered_ms, reason ∈
  {grace, max_duration, second_commit, close})
- `engine.assembly.merge_back` (frag_count_before, frag_count_after) when a
  checkpoint-abort merges a post-flush continuation.
- `engine.assembly.force_flush` on `max_duration_s`.
No raw transcript text in production logs beyond the existing dev-only
`engine.driver.turn_trace` discipline (lengths/counts only at prod redaction).

---

## 12. Testing strategy (TDD, livekit-free)

The assembler core is fully unit-testable with a fake clock + manual timer:
- single fragment → flush after grace; assert merged text + span.
- two fragments within grace → one flush, merged text, single brain feed.
- VAD `speaking` cancels the grace timer; `listening` restarts it.
- `max_duration_s` force-flush.
- `IN_FLIGHT` + `note_user_speaking` → `is_superseded()` True; `confirm_committed`
  clears; abort path re-buffers and re-flushes the merged text.
- false VAD resume in flight → re-flush same text once.
- `enabled=False` pass-through.

Loop/driver focused tests (fake assembler / fake supersession check):
- checkpoint returns `ABORTED` when superseded → no `notelog.append`, no real_line,
  candidate `TranscriptTurn` popped, state not advanced.
- not superseded → notes appended, real_line spoken, `confirm_committed` called.
- `suppress_bridge` skips the bridge call.

All without importing LiveKit. Run under the documented coverage-in-docker path.
Per project rule, prompt behaviour is unaffected (no prompt changes); validation
is a real talk-test after merge.

---

## 13. Rollout & risk

- Ship behind `engine_assembly_enabled` (default True; flip to False for instant
  rollback). Restart `nexus-engine` to load.
- Primary validation: a **live talk-test** (the standing rule) — verify a
  deliberate mid-answer pause assembles into one turn and the report grades it as
  one answer; verify clean turns still feel responsive.
- Risk: VAD `user_state_changed` reliability on the self-hosted path — validated
  in the talk-test; the grace timer + `max_duration_s` bound any VAD miss so a
  fragment is never lost (worst case: it flushes on the grace timer as today).

---

## 14. Out of scope / future

- Cancelling the in-flight brain on continuation (preemptive merge-back) — only
  if talk-tests show the ~2–3s merge-back wait is felt.
- Sub-fragment timing metadata in the evidence contract (we chose one assembled
  turn with a covering span; revisit only if reel/proctoring timing needs it).
- `user_turn_limit` integration for the truly-endless monologue.

---

## 15. Doc fixes bundled with this work

- `interview_engine/AGENT_ARCHITECTURE.md §11`: correct the inaccurate claim that
  barge-in cancels the in-flight `run_turn`; document the assembler + the
  point-of-no-return at note-commit.
- `interview_engine/AGENT_ARCHITECTURE.md` + `backend/nexus/CLAUDE.md`: add the
  `TurnAssembler` to the file map and the turn flow (LiveKit → assembler → source
  → driver).
