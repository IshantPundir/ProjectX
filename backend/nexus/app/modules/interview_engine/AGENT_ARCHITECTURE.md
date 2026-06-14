# Agent Architecture — How the Interview Agent Thinks

> A deep dive into the LLM agent inside `app/modules/interview_engine/`: its two tiers,
> the turn detector, prompts, decision flow, and the safety machinery around it.
>
> Read **[README.md](./README.md)** first for the module map and the session lifecycle.
> This document is about the *mind* of the agent — how it decides what to say.

---

## Table of contents
1. [The core idea: think and speak are different jobs](#1-the-core-idea-think-and-speak-are-different-jobs)
2. [The two minds (and the turn detector)](#2-the-two-minds-and-the-turn-detector)
3. [The Directive — the one thing that crosses the wall](#3-the-directive--the-one-thing-that-crosses-the-wall)
4. [The brain's reasoning, step by step](#4-the-brains-reasoning-step-by-step)
5. [Coverage: how the agent tracks what it has heard](#5-coverage-how-the-agent-tracks-what-it-has-heard)
6. [The policy gates: the LLM proposes, the rules dispose](#6-the-policy-gates-the-llm-proposes-the-rules-dispose)
7. [The mouth's reasoning](#7-the-mouths-reasoning)
8. [Deterministic detection → the brain decides](#8-deterministic-detection--the-brain-decides)
9. [Prompt engineering: the stable-prefix pattern](#9-prompt-engineering-the-stable-prefix-pattern)
10. [The full per-turn timeline](#10-the-full-per-turn-timeline)
11. [Failure modes and safety nets](#11-failure-modes-and-safety-nets)
12. [Design principles (the "why")](#12-design-principles-the-why)

---

## 1. The core idea: think and speak are different jobs

A human interviewer does two things at once when you finish answering:

1. **Reacts instantly** — a nod, an "mm, okay," a beat that says *I heard you*. This
   happens in a fraction of a second, before they've actually evaluated anything.
2. **Thinks carefully** — silently reading your answer, deciding whether to dig deeper or
   move on. This takes a couple of seconds.

A naive AI interviewer collapses these into one slow step: it waits, thinks, then speaks —
and the candidate sits in dead air for two seconds every turn. It feels like a machine.

The interview engine **separates the two**:

- The **mouth's bridge** does the instant reaction — *in parallel* with the thinking.
- The **brain** does the careful thinking, masked behind that beat.
- The **mouth's real line** is the actual answer — and the mouth is deliberately kept
  *ignorant* of the evaluation, so it can never leak a judgment.

In one sentence: **the agent reacts and reasons at the same instant, and the part that
speaks never sees the part that judges.** Turn-taking — *when* the candidate is done — is
owned by LiveKit's native turn detector reading the live STT stream, not by any LLM.

---

## 2. The two minds (and the turn detector)

```
   candidate     │   t=0ms  one committed turn (native turn detector fires the FULL transcript)
   stops  ───────┼─▶ ┌─────────────┐                          ┌──────────────────────────────┐
                 │    │ MOUTH.bridge│  ~100-300ms              │ BRAIN  ~2-3s                 │
                 │    │             │──▶ speaks a beat NOW ──▶ │ read → coverage → policy →   │
                 │    └─────────────┘    (filler, intent-aware)│ ONE Directive                │
                 │                                              └───────────────┬──────────────┘
                 │                                  when it lands,               │
                 │                                  continue from the bridge     ▼
                 │                                              ┌──────────────────────────────┐
                 │                                              │ MOUTH.real_line  ~0.3-0.6s   │
                 │                                              │ render Directive → one       │
                 │                                              │ spoken line, in persona      │
                 │                                              └──────────────────────────────┘
```

### Turn detector — *when* is the candidate done?
- **Mechanism:** LiveKit `MultilingualModel` + dynamic endpointing, driven internally by the
  live Deepgram STT stream. It fires `on_user_turn_completed` with the **full final
  transcript**.
- **Key property:** EOU and the transcript are produced *together*. There is no manual
  commit and no separate EOU LLM — so the brain is never handed a partial/stale transcript,
  and there is no one-turn lag. (Decoupling EOU from STT was the gen-2 failure this design
  removed.)

### Brain — the control plane
- **Model:** `gpt-5.4-mini`, ~2–3s. Runs in parallel with the bridge, never on the path to
  first audio.
- **Sees:** the full role context, a compact question-bank index, the **active question's
  full rubric**, the running coverage map, a bounded transcript window, deterministic
  turn-hints, and the candidate's utterance (fenced as DATA).
- **Emits:** one lean `BrainTurnOutput` — `reasoning`, zero-or-more signal `observations`,
  one `move`, and the fields that move needs. `ControlPlane` turns it into one `Directive`.
- **Key insight:** the brain is the only tier that *understands* the interview. It reads the
  answer, attributes signals, and decides probe / advance / clarify / close. It does **not**
  score or decide the hire — it leaves notes; the report decides.

### Mouth — the conversation plane
- **Model:** `gpt-5.4-mini`, persona "Arjun". Two calls per turn:
  - **bridge** (~100–300ms) — an immediate, intent-aware beat. Sees only the candidate's
    words; commits to nothing about answer quality (it speaks before the brain decides).
  - **real line** (~0.3–0.6s) — renders the brain's `Directive`, continuing from the bridge
    (one acknowledgment per turn).
- **Sees:** the persona preamble, the per-act prompt block, and the directive payload.
  **Structurally never the rubric** — the `Directive` carries no rubric fields, so there is
  nothing to leak.
- **Key insight:** safety by construction. Because the mouth holds zero evaluation data, no
  prompt-injection or accident can make it reveal a score or coach the candidate.

---

## 3. The Directive — the one thing that crosses the wall

The **`Directive`** (`contracts.py`) is the only object that travels from brain to mouth.
This is the load-bearing boundary of the design. It carries speakable text + delivery
metadata — `act`, `say` (verbatim text or `None`), `tone`, `spoken_setup`, `is_terminal` —
and **never** a rubric, evidence, or grade.

### The closed act set (`DirectiveAct`)
The mouth has **no behavior outside this enum**:

| Act | What the mouth does | `say` source |
|---|---|---|
| `ask` | Deliver the next main question | **verbatim bank text** |
| `probe` | Deliver one follow-up | **brain-composed**, leak-scrubbed (composed within the unfired dimension's `intent` toward its `listen_for`; verbatim `seed_probe` as fallback) |
| `clarify` | Re-pose / explain the floor question more simply | brain-composed (scrubbed) |
| `redirect` | Bring an off-topic / injection turn back | brain-composed (scrubbed) |
| `reassure` | Calm a nervous candidate | brain-composed (scrubbed) |
| `hold` | "Take your time" while they think | brain-composed (scrubbed) |
| `confirm` | Reflect a garbled / misheard term back | brain-composed (scrubbed) |
| `answer_meta` | Answer a role/logistics question, deflect any fishing | brain-composed (scrubbed) |
| `repeat` | Re-pose the question on the floor | the tracked floor question |
| `close` | Warm close + next steps (terminal) | composed from the `close` act prompt |

(`intro` is delivered once at session start via `ConversationPlane.intro()`, not as a move.)

### The no-leak guarantee — two layers
1. **Structural:** the mouth is only ever handed a `Directive` + what was just said. The
   rubric lives in the brain's prompt and never enters any mouth message. `validate_no_leak`
   (`mouth/service.py`) asserts this in tests.
2. **Scrub:** any brain-*composed* `say` (probe / clarify / redirect / reassure / hold /
   confirm / answer_meta) passes through `scrub_composed_say` (`brain/policy.py`) — a literal
   known-rubric-string match (excellent / meets_bar / below_bar / positive_evidence /
   red_flags / evaluation_hint, length-gated) → safe fallback on any hit. This is a
   structural substring check, **not** intent classification (so the project's "no regex for
   intent" rule doesn't apply).

**Verbatim discipline:** for `ask`, the brain *selects* a bank question by reference (the
resolver returns a question id; the `Directive` carries the exact bank text). The LLM never
rewrites a main question — every candidate answers the same calibrated question. Probes are
the one place the spoken text is composed (an adaptation of a recruiter follow-up template),
always leak-scrubbed and kept inside the main question's scope.

---

## 4. The brain's reasoning, step by step

The brain's output is a lean **`BrainTurnOutput`** (`contracts.py`). Its single most
important design choice: **`reasoning` is field #1** — the model thinks out loud *before*
committing to a move, which buys grade↔move coherence at low reasoning effort without the
latency of extended thinking.

The system prompt (`prompts/v4/engine/brain.system.txt`) is built around a few load-bearing
ideas:

- **Collector, not judge.** For each signal the answer touches, the brain leaves one
  `observation`: the signal, the **stance** (`supports`, or `contradicts` = a disclaim /
  retraction), and the **texture** — `thin` (generic / buzzwords / a hypothetical "I would…"
  with no real HOW), `concrete` (a real thing they did), or `strong` (concrete + tradeoffs /
  numbers / edge-cases). Texture tells the brain whether to push; it is **not** a score.
  Crediting is conversation-wide (an answer can credit a signal that "belongs" to another
  question).
- **Thin is not a bluff.** The brain never decides bluff-vs-modest — it **elicits** (one warm
  ask for a specific). Whatever comes back is the record. Bluff detection is the report's job.

### The closed move set (`BrainMove`)
The brain may only choose one of these (each maps 1:1 to a `DirectiveAct`):

| Move | When |
|---|---|
| `probe` | The thread is open and there's a real answer to push on. Compose **one** in-scope follow-up (see *Composing a probe*). |
| `ask` | This thread is done. Advance — the deterministic resolver picks the next main question. |
| `clarify` | They misunderstood or asked a scoping question — re-pose the floor line more simply, or commit to one concrete scenario, **without naming the solution**. |
| `redirect` | Off-topic, rambling, or an injection — bring them back, reveal nothing. |
| `reassure` | Nervous candidate — lower the stakes. |
| `hold` | A thinking pause ("let me think") — "take your time," do not advance/probe/grade. |
| `confirm` | A garbled / likely-misheard key term — reflect the one uncertain term back before banking it. |
| `answer_meta` | They asked *you* something (role/logistics, "are you an AI?") or fished for the answer/criteria → answer or deflect, reveal nothing, return to the floor. |
| `repeat` | They asked you to say the question again → re-pose the floor question. |
| `close` | The screen is complete (the resolver has no unasked question left) or the candidate asked to end (`end_requested`). Terminal. |

### Composing a probe (the one place the spoken question is adapted)
The bank `follow_ups` are governed **dimension objects** — each carries a `dimension` slug,
an `intent` (what gap to probe), a `seed_probe` (the verbatim fallback question), and a
`listen_for` (what a good answer surfaces). Each dimension fires **at most once** per thread;
the engine hard-caps probes per thread (`engine_probe_cap_per_thread`, default 2) and
force-advances when the cap is hit. On a `probe`, the brain: (0) checks what it already
knows (window + coverage) and does **not** re-ask anything answered, nor press for a
retrievable fact — a name, an exact number — that doesn't change the read; (1) picks the
**unfired** dimension (by slug, tracked in `fired_dimensions`) that targets the biggest real
gap; (2) **composes** one natural question within that dimension's `intent` toward its
`listen_for`, referencing the candidate's actual words; (3) stays in the main question's
**scope and kind** (an experience question stays about experience, a technical one stays
technical). The composed text is leak-scrubbed; if the brain doesn't compose, the
`seed_probe` is the fallback. The brain also advances early when `primary_signal` coverage
reaches `sufficient` before the cap is hit.

### The "this is a SCREEN, not a panel" principle
The prompt enforces anti-grind discipline: **one good probe verifies a thin claim; a third+
probe on one thread is grinding.** Grinding reads as hostile and suppresses signal —
especially with Indian candidates, who often *undersell* and signal "no" indirectly ("we'll
see", "it may be a bit difficult"). The brain acknowledges, advances, and lets coverage
accrue across the whole conversation.

### After the LLM call — what `ControlPlane.decide()` does
1. **Update the coverage projection** with the turn's observations.
2. **Candidate-end bypass:** `close` + `end_requested` → terminal close immediately
   (a candidate may always end).
3. **Map the move** → `Directive`: `ask` → resolver's next bank question (verbatim);
   `probe` → coerced unfired dimension + composed/leak-scrubbed follow-up (or `seed_probe`
   fallback, or `ask` when the cap is hit); composed acts → scrubbed `composed_say`;
   `repeat` → the floor question; `close` → terminal (no `say`).
4. **Emit** the `BrainDecision` (directive + observations + reasoning + the resolved
   `next_question_id` / `probe_dimension`), and log an `engine.brain.decision` trace.

### Choosing the next question (`resolver.resolve_next`)
A deterministic picker the LLM **cannot override** — selection is **purely positional**.
It filters to the unasked questions and returns the **lowest-`position`** one; it honors the
brain's optional `preferred_next_signal` hint only when an unasked question matches that
signal. When every question has been asked it returns `None`, and the session closes.
(The question bank already sizes question *count* to the stage's time budget, so at runtime
the engine just walks its questions in order — there is no clock, tier, or overflow
scheduler.)

---

## 5. Coverage: how the agent tracks what it has heard

`CoverageProjection` (`brain/input_builder.py`) is the brain's ephemeral, in-memory read of
*what it has heard so far* — **signal-based, credited across the whole conversation**, not
per question. It is runtime steering only; the **durable** record is the append-only
`NoteLog`.

Each touched signal has a `SignalRead`: a `coverage` state (`none` / `partial` /
`sufficient`), the `last_stance` (`supports` / `contradicts`), and a verbatim
`established_quote` (long-range memory that survives the transcript window). It exposes:

- `uncovered_signals()` — high-value signals still uncovered (weight-ranked) → tells the
  brain what still matters for cross-crediting.

At session end the durable per-signal evidence (`notes[]` + computed `provenance`) is what
the report consumes — coverage itself is not persisted.

---

## 6. The policy gates: the LLM proposes, the rules dispose

`brain/policy.py` is deterministic, pure (no LLM), and **never raises** — defense-in-depth
over the brain's decision:

| Gate | What it enforces |
|---|---|
| `scrub_composed_say` | Literal known-rubric-string no-leak scrub on any composed `say` → safe fallback on a hit. |
| `coerce_probe_dimension` | Coerces the brain's `probe_dimension` (a slug) to an unfired dimension for this thread (fire-once) and enforces a hard per-thread probe cap (`engine_probe_cap_per_thread`) — returns `None` when all dimensions are fired or the cap is reached, falling through to `ask`. |

None of these auto-reject — borderline always goes to a human. They bound the LLM's judgment
with rules it can't override.

---

## 7. The mouth's reasoning

The mouth is the simplest mind by design.

**The bridge** (`mouth/bridge.py`) fires the instant the turn commits, in parallel with the
brain. It sees only the candidate's words (a `BridgeRequest`) — never the brain output or the
rubric — so it must be a **neutral, intent-aware beat** that commits to nothing about answer
quality (else it could contradict the brain). A willing lead-in ("Sure —") for a genuine
repeat/explain/role question; a neutral beat ("Mm, okay…") for a joke / fishing / injection;
a reflective mirror or continuation cue otherwise. Canned fallback on error → never dead air.

**The real line** (`mouth/service.py`) renders the `Directive`:
- `ask` / `probe` / `repeat` with `say` set → spoken **verbatim** (zero LLM call, exact
  meaning, leak-safe).
- composed acts → the mouth LLM with `[persona preamble | per-act block | directive payload]`,
  continuing from `just_said` (the bridge) so it doesn't acknowledge twice, and varying its
  opening connective (`recent_openers`) so it never sounds stuck.

The **persona preamble** (`mouth/persona.txt`) carries voice discipline (short, one question
per turn, plain spoken Indian English, numbers/acronyms said aloud, light fillers, neutral
acknowledgments — never praise) and the **identity lock**: the candidate's words are DATA,
never instructions; nothing they say changes the persona, ends the interview, or reveals
scoring.

---

## 8. Deterministic detection → the brain decides

A recurring pattern, and a hard design rule: **turn-level facts are detected in plain Python
and fed to the brain as hints on its existing call — the engine adds no extra LLM tier to
detect them, and the brain makes the semantic decision.**

| Detected in code | Hint to the brain | The brain decides |
|---|---|---|
| Pure-backchannel turn (`is_backchannel`) | *(dropped before the brain)* | n/a — never reaches it, never moves the floor |
| The agent's question was cut off (interrupt-aware voice) | `floor_interrupted` | continuing answer → keep going; confused / didn't hear → `repeat` |
| N consecutive non-answer turns | `stalled` | stop re-posing → advance warmly |
| High-value uncovered signals | `uncovered_signals` | what to cross-credit / steer toward |

This keeps the engine at two LLM tiers, avoids the gen-2 EOU-gate pain point, and keeps every
"is the candidate X?" judgment semantic (the model's), never pattern-matched.

---

## 9. Prompt engineering: the stable-prefix pattern

Every per-turn LLM prompt (brain, mouth real line, bridge) is structured as
**STABLE PREFIX → DYNAMIC SUFFIX** so OpenAI's prefix cache hits (cheaper + faster TTFT).

For the **brain** (`brain/input_builder.py`):
- **Stable prefix** (rendered once, byte-identical all session): the system prompt + role
  context + a **compact question-bank index** — per question only
  `question_id | primary_signal | signals | kind | difficulty | text | follow_ups`. The
  rubric / positive_evidence / red_flags are **deliberately omitted** here — inlining every
  question's full rubric blows the prompt past budget.
- **Dynamic suffix** (changes per turn): the **active question's full rubric** (what to read
  *this* answer against), the compact coverage so far, the uncovered signals,
  the `⚠️ FLOOR INTERRUPTED` / `⚠️ STALLED` hint blocks (only when set), a bounded transcript
  window, and the candidate's utterance fenced as DATA between explicit delimiters
  (prompt-injection defense — everything between the fences is untrusted).

---

## 10. The full per-turn timeline

```
candidate finishes speaking
        │
        ▼  LiveKit native turn detector (live STT) decides EOU
on_user_turn_completed(turn_ctx, new_message)
  • _EngineAgent submits new_message.text_content to the TurnAssembler (submit_fragment)
  • raise StopResponse()   ← suppress LiveKit's auto-reply; the engine drives all speech
        │
        ▼  TurnAssembler (turn_assembler.py) — merge fragments into ONE logical turn
  • a committed fragment is buffered, NOT immediately run. VAD user_state_changed
    (speaking/listening) + a short grace timer decide when the candidate has settled.
  • a pause-then-continue answer accumulates (its opening fragment is never processed
    alone); on settle it flushes ONE AssembledTurn (text + covering span) to CommittedTurnSource.
        │
        ▼  the drive loop: turn_source.get() → SessionDriver.handle_turn(turn=AssembledTurn)
  1. is_backchannel? → drop, do not run the brain, floor unchanged
  2. build BridgeRequest + BrainTurnInput (with the deterministic hints)
  3. run_turn(ctx):
        bridge_task = mouth.bridge(req)     ─┐  launched at the SAME instant,
        brain_task  = brain.decide(input)   ─┘  neither awaited first  (bridge skipped on a merge-back re-flush)
        • await bridge → voice.say(bridge)          (~100-300ms; canned fallback on error)
        • await brain  → BrainDecision               (~2-3s, masked by the bridge)
        • ── merge-back CHECKPOINT (point-of-no-return) ──
          if a continuation arrived (assembler.is_superseded()) → return ABORTED:
          NO notes committed, NO real line spoken; the driver pops the candidate
          transcript turn and the assembler re-flushes the merged answer.
        • for obs in decision.observations: notelog.append(...)   ← durable here
        • real = mouth.real_line(directive, just_said=bridge); voice.say(real)
  4. advance state from the decision:
        ask   → active question ← resolver.next_question_id; asked_ids += it
        probe → fired_dimensions += dimension slug
        update recent_openers; floor question (ask/probe only); floor_interrupted; stall count
  5. is_terminal? → exit the loop → finalize()
```

Turns are processed **strictly sequentially** — the consume loop awaits one
`handle_turn` before pulling the next, so only one turn is ever in flight. There is
**no mid-turn cancellation**: a barge-in during the agent's speech only sets
`_InterruptAwareVoice.last_interrupted` (read as `floor_interrupted` next turn); the
in-flight `run_turn` is NOT cancelled. The only way a turn produces zero durable trace
is the merge-back checkpoint above (before `notelog.append`). A continuation that
arrives *after* the real line is delivered is a genuine new turn.

---

## 11. Failure modes and safety nets

Every tier is built to **never crash the session**:

| What can go wrong | Safety net |
|---|---|
| Bridge LLM times out / errors | `CANNED_BRIDGE_FALLBACK` ("Mm, okay…") — never dead air. |
| Brain LLM errors | Graceful fallback in `ControlPlane`; the resolver only ever walks strictly forward (no infinite-Q1 loop), closing if the bank is exhausted. |
| Brain authors rubric into spoken text | `scrub_composed_say` no-leak scrub; the mouth structurally never holds the rubric. |
| Brain tries to reorder questions | `resolve_next` is positional — the `preferred_next_signal` hint is honored only for an unasked question; otherwise it walks by position. |
| A pure-backchannel turn | Dropped before the brain by `is_backchannel`. |
| Candidate goes silent | Per-turn inactivity timeout → graceful `unresponsive` close. |
| Candidate disconnects | `participant_disconnected` → prompt finalize as `candidate_ended`. |
| Engine subprocess crashes pre-run | `_handle_entrypoint_failure` → durable `state='error'` + best-effort room signal. |
| Long interview looks "dead" to the reaper | Liveness heartbeat pulses `last_engine_heartbeat_at`. |
| Externally terminated row (proctoring) | `record_session_evidence` attaches evidence idempotently without clobbering state. |

---

## 12. Design principles (the "why")

These are the convictions baked into the architecture (several are project-memory / CLAUDE.md
invariants):

1. **Quality before latency.** The brain is never rushed onto the critical path;
   `preemptive_generation` is OFF. The mouth's bridge masks latency instead of the brain
   sacrificing thinking.
2. **Separate thinking from speaking.** The mouth never sees the rubric — leak-proofing is
   structural, not prompt-pleading.
3. **The LLM proposes, deterministic code disposes.** Coverage, the positional next-question
   resolver, the probe-dimension gate, and the no-leak scrub are all deterministic. The LLM's
   judgment is bounded by rules it can't override.
4. **Deterministic detection → the brain decides.** Turn-level facts are computed in code and
   fed as hints; the engine adds no extra LLM call to detect them. No EOU gate (the gen-2
   pain point). No regex for intent — every "is the candidate X?" is a semantic judgment.
5. **Standardized bank.** Main questions are verbatim bank text in resolver order; rubrics
   and follow-ups (as governed dimension objects) come from the bank; the LLM never
   free-authors a question. Probes compose within an unfired dimension's intent, in scope,
   leak-scrubbed; each dimension fires at most once per thread.
6. **This is a screen, not an interrogation.** One probe verifies a claim; a third grinds.
   The agent reads Indian-English under-selling and indirect "no" correctly, and never
   re-asks what it already heard.
7. **Records, never rejects.** The agent collects signals and records a structured,
   append-only `SessionEvidence`; the score, the verdict, and bluff-vs-genuine are the
   report's job (and borderline is always a human's).
8. **Everything is auditable.** Every turn logs an `engine.brain.decision` trace, and the
   durable `SessionEvidence` carries the per-signal notes (stance, texture, verbatim quote,
   provenance) — the EEOC/bias defensibility trail.
