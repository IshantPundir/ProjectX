# The Interview Engine

> `app/modules/interview_engine/` — the live AI interviewer that conducts ProjectX's
> video screening calls.

This is the heart of the product: a real-time voice agent that joins a LiveKit video
room, talks to a candidate in natural Indian English, asks the role's questions, listens
and reads the answers as they come, decides what to ask next, and — when the screen is
over — writes a structured **evidence** record that the reporting module turns into an
evaluation.

If you only read one paragraph: **the engine splits "what to say" from "what to think,"
and never blocks the conversation on the thinking.** The instant the candidate stops, the
**mouth** speaks a short human beat (a "bridge") *in parallel* with the **brain** — the
control plane that reads the answer, tracks signal coverage, and decides the next move.
When the brain lands, the mouth voices its decision, continuing naturally from the bridge.
Two LLM tiers, one of which (the brain) never speaks, and one of which (the mouth) never
sees how the candidate is being assessed.

For a deep dive into how the agent reasons, see **[AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md)**.
This README is the map of the module: what each file does, how a session runs, and the
data contracts at the edges.

---

## 1. The big picture

```
                          ┌──────────────────────────────────────────────────┐
   Candidate speaks       │              THE INTERVIEW ENGINE                │
   (mic → LiveKit)        │                                                  │
        │                 │  LiveKit's NATIVE turn detector reads the live   │
        ▼                 │  STT stream and fires one committed turn:        │
   ┌──────────┐           │                                                  │
   │   STT    │           │   on_user_turn_completed(full final transcript)  │
   │ Deepgram │──text──▶ │            │                                      │
   └──────────┘           │            ▼  CommittedTurnSource (asyncio queue) │
                          │     ┌──────────────┐                              │
                          │     │ SessionDriver│  drive loop, one turn:        │
                          │     │  (run_turn)  │                              │
                          │     └──────┬───────┘                              │
                          │            │  fires BOTH at the same instant:     │
                          │     ┌──────┴───────────────────────┐             │
                          │     ▼                              ▼             │
                          │  ┌────────────┐            ┌────────────────┐    │
                          │  │ MOUTH       │ speaks    │     BRAIN       │    │
                          │  │ .bridge()   │ a beat NOW│  (control plane)│    │
                          │  └────────────┘ ~100-300ms │  grade+coverage │    │
                          │         │                  │  → ONE Directive│    │
                          │         │  brain lands     └───────┬─────────┘    │
                          │         ▼  (~2-3s, masked)         │             │
                          │  ┌────────────┐ ◀──────────────────┘             │
                          │  │ MOUTH       │  renders the Directive →          │
                          │  │ .real_line()│  one spoken line, in persona      │
                          │  └──────┬──────┘                                  │
                          └─────────┼───────────────────────────────────────┘
                                    ▼
                             ┌──────────┐
                             │   TTS    │──audio──▶ candidate
                             │  Sarvam  │
                             └──────────┘
```

The engine runs as a separate process — the `nexus-engine` Docker Compose service — built
from the **same image** as the FastAPI backend, just started with
`python -m app.modules.interview_engine`. LiveKit dispatches one job (one spawned
subprocess) per interview. The FastAPI/nexus web process never loads LiveKit at all
(the heavy imports are lazy — see [§6](#6-why-the-fastapi-process-never-loads-livekit)).

---

## 2. The two tiers (and the turn detector)

| Piece | File(s) | Model | Sees the rubric? | Speaks? | Job |
|---|---|---|---|---|---|
| **Turn detector** | LiveKit `MultilingualModel` + dynamic endpointing | — | — | — | Reads the **live Deepgram STT stream** and decides end-of-turn, firing `on_user_turn_completed` with the FULL final transcript. EOU and the transcript are produced **together** — no commit/STT race, no one-turn lag. |
| **Brain** | `brain/` | `gpt-5.4-mini` | ✅ (the active question's rubric) | ❌ never | The control plane. Reads the answer, attributes signals, tracks coverage, runs deterministic policy gates, and emits exactly **one `Directive`** (the next move). Runs **in parallel** with the bridge — masked, never on the path to first audio. |
| **Mouth** | `mouth/` | `gpt-5.4-mini` (persona "Arjun") | ❌ never (by construction) | ✅ (everything the candidate hears) | Two calls per turn: the **bridge** (an immediate short beat fired in parallel with the brain) and the **real line** (renders the brain's `Directive` as one natural spoken line, continuing from the bridge). Holds zero rubric/evidence, so it *cannot* leak how the candidate is being scored. |

**Why this shape?**

- **Latency vs. quality.** A careful read-and-decide brain call takes ~2–3s. If the agent
  waited silently for it, every turn would feel laggy. The mouth's **bridge** speaks a
  human beat ("Mm, okay —") the instant the candidate stops, *masking* the brain's thinking
  time. The candidate never hears a silent wait. `preemptive_generation` is intentionally
  **OFF** (quality-before-latency lock).
- **EOU honesty.** Turn-taking is owned by LiveKit's native turn detector, which is driven
  internally by the live STT stream. There is **no separate EOU LLM call and no manual
  commit** — that decoupling was the gen-2 pain point. The transcript handed to the engine
  is always the final one.
- **Safety by construction.** The mouth is the only tier that speaks, and it is never handed
  the rubric, the evidence, or a grade. The only thing that crosses from brain to mouth is a
  `Directive` — speakable text plus delivery metadata. So the mouth is structurally
  *incapable* of telling the candidate "great answer" or "we're looking for X."
- **Deterministic detection → the brain decides.** Turn-level facts (a pure-backchannel
  turn, a cut-off floor, a stalled candidate, which knockouts are pending / already
  reflected) are computed in plain Python and fed to the brain as **boolean/ list hints** on
  its existing call. The engine adds **no extra LLM call** to detect them; the brain makes
  the semantic decision.

---

## 3. File-by-file map

### Orchestration (the LiveKit-facing layer)
| File | Lines | What it does |
|---|---|---|
| `agent.py` | ~650 | **The LiveKit entrypoint + worker bootstrap.** `prewarm` (Silero VAD), `entrypoint`/`_run_entrypoint` (load `SessionConfig`, bind structlog, durable-error funnel), `run` (build the `AgentSession`: Deepgram STT, mouth LLM, Sarvam TTS, Silero VAD, `MultilingualModel` turn detector, dynamic endpointing, VAD-mode barge-in, `preemptive_generation` OFF + the liveness heartbeat), `_EngineAgent` (the `Agent` subclass whose `on_user_turn_completed` submits each committed transcript to the `CommittedTurnSource` and raises `StopResponse`), `_InterruptAwareVoice` (the `say` surface that records interruption), and `_drive` (the per-session consume loop → `SessionDriver`). |
| `__main__.py` | 15 | `python -m app.modules.interview_engine` → boots the LiveKit agent CLI. |
| `__init__.py` | 38 | Module public API. Exports the pure `Directive`/`DirectiveAct`/`DirectiveTone` eagerly; exports the livekit-bearing `run`/`server` **lazily** (so importing the engine from FastAPI never pulls in LiveKit). |
| `turn_source.py` | 69 | `CommittedTurnSource` — a livekit-free `asyncio.Queue` wrapper. `on_user_turn_completed` calls `submit()`; the drive loop awaits `get()`. Drops empty/whitespace finals; `close()` unblocks `get()` with a sentinel for clean session end. |

### The session driver (`driver.py`)
| Symbol | What it does |
|---|---|
| `SessionDriver` | The conductor. `intro()` (greeting + 1-line role brief, non-interruptible) → `opener()` (first bank question) → `handle_turn()` per committed turn → `finalize()` at end. Owns the active-question pointer, `asked_ids`, `fired_dimensions`, the transcript window, `recent_openers`, the floor-question (for `repeat`), and the deterministic per-turn signals (backchannel drop, `floor_interrupted`, `stall_count`). Builds the `BridgeRequest` + `BrainTurnInput`, calls `run_turn`, then advances state from the returned `BrainDecision`. |
| `_BrainAdapter` / `_MouthAdapter` / `_CapturingVoice` | Thin adapters that satisfy the `loop.py` `Brain`/`Mouth`/`Voice` protocols over the real `ControlPlane`, `ConversationPlane` + `BridgeComposer`, and the interrupt-aware voice. |

### The drive loop (`loop.py` — pure, no LiveKit)
| Symbol | What it does |
|---|---|
| `run_turn` | One committed turn: fire `mouth.bridge` ∥ `brain.decide`, speak the bridge the instant it resolves (canned `CANNED_BRIDGE_FALLBACK = "Mm, okay…"` on error — never dead air), await the brain, append each observation to the `NoteLog`, render the real line via `mouth.real_line(just_said=bridge_text)`, speak it. Cancellation-safe (barge-in cancels both child tasks). |
| `TurnContext` | Immutable per-turn input bundle (utterance + span, question id, `BrainTurnInput`, `BridgeRequest`, recent openers). |

### The brain (control plane — `brain/`)
| File | What it does |
|---|---|
| `service.py` | `ControlPlane.decide()` — one structured LLM call per turn → update the coverage projection → derive a no-leak `Directive` (apply the candidate-end bypass, the brain-driven verified-knockout close + reflect backstop, the knockout gate, then map the move). Also `confirmed_knockout_signals()` (read at finalize). `build_control_plane()` assembles it from a `SessionConfig`. |
| `input_builder.py` | Pure prompt assembly + the `CoverageProjection` (ephemeral per-signal read state: `update`, `signal_reads`, `uncovered_signals`, `knockout_pending`). Builds the cache-stable prefix (system + role context + compact bank index) and the dynamic suffix (active rubric + coverage + uncovered/knockout-pending/already-reflected hints + transcript window + the candidate utterance, fenced as DATA). |
| `resolver.py` | `resolve_next()` — the deterministic next-**main**-question picker (bank position / mandatory-first when winding down / time budget / no-repeat; honors the brain's `preferred_next_signal` only when budget allows). Plus `compute_budget_phase`, `build_question_records`, `budget_config_from_ai_config`. The LLM never authors main questions. |
| `policy.py` | Deterministic, never-raising gates: `KnockoutTracker` (per-signal verified-knockout state), `gate_knockout` (blocks a premature blind close), `scrub_composed_say` (literal known-rubric-string no-leak scrub), `coerce_probe_dimension` (fire-once per dimension slug + hard per-thread probe cap). |

### The mouth (conversation plane — `mouth/`)
| File | What it does |
|---|---|
| `service.py` | `ConversationPlane` — `intro()` (greeting + role brief) and `real_line()` (renders the `Directive`; `ask`/`probe`/`repeat` with `say` set are spoken **verbatim** with zero LLM call; composed acts go through the mouth LLM with the per-act prompt). `validate_no_leak()` proves the message list carries no rubric. |
| `bridge.py` | `BridgeComposer.bridge()` — the immediate, intent-aware beat fired in parallel with the brain. Sees only the candidate's words (never the brain output or rubric); canned fallback on error. |
| `persona.py` | Renders the byte-stable persona preamble (identity lock + Indian-English voice discipline) once per session. |

### Shared core (pure — no LiveKit, no LLM)
| File | What it does |
|---|---|
| `contracts.py` | The canonical engine schemas: `BrainMove` / `BrainTurnOutput` (lean LLM output), the cache-split brain input (`BrainSessionContext` prefix + `BrainTurnInput` suffix, `ActiveQuestionRubric`, `SignalRead`, `SignalSpec`, `BankQuestionIndex`), the `Directive` / `DirectiveAct` / `DirectiveTone` brain→mouth contract, `MouthTurnInput` / `BridgeRequest`, and `BrainDecision` (the service's per-turn result). Imports shared value-types from `interview_runtime.evidence` — never redefines them. |
| `notes.py` | `NoteLog` — the append-only ledger of `EvidenceNote` (monotonic `seq`, retraction = a new linked note, full-utterance quote + span). `to_session_evidence()` packages the durable `SessionEvidence`. |
| `turn_taking.py` | `is_backchannel()` — a tight token allowlist (NOT regex/intent): a turn made entirely of "mm"/"yeah"/"haan"/… is engagement, dropped before the brain. "yes"/"no" are deliberately NOT backchannels. |

### Prompts
Live **outside** the module, under `prompts/v4/engine/`:
- `brain.system.txt` — the brain's full reasoning instructions.
- `mouth/_persona.txt` — the persona preamble (identity lock + voice).
- `mouth/bridge.txt` — the intent-aware bridge beat.
- `mouth/<act>.txt` — one per spoken `DirectiveAct` (`intro`, `ask`, `probe`, `clarify`,
  `redirect`, `reassure`, `hold`, `confirm`, `answer_meta`, `repeat`, `close`).

Prompts are read fresh per session by a versioned `PromptLoader` (in-memory cached).
After editing an engine prompt, restart with
`docker compose up -d --force-recreate nexus-engine`.

---

## 4. How a session runs (end-to-end)

### 4.1 Dispatch
1. The candidate hits `POST /start` on the session API. The session module mints a LiveKit
   room + token, then **dispatches** the engine agent into that room with job metadata
   (`session_id`, `tenant_id`, `correlation_id`).
2. LiveKit spawns a job subprocess and calls `entrypoint(ctx)` in `agent.py`.
3. `entrypoint` → `_run_entrypoint`: parse metadata, bind the structlog context, load the
   `SessionConfig` from Postgres via `build_session_config()` (walks session → assignment →
   candidate → job → stage → bank → questions → ancestry-walked company profile), then call
   `run(ctx, config, …)`. Any pre-run crash funnels into `_handle_entrypoint_failure`, which
   durably transitions the session row to `state='error'` and best-effort signals the room.

### 4.2 Session start (`run()`)
- Connects to the room, waits for the candidate to join.
- Assembles per-session **keyterms** (candidate first name + cached bank keyterms) for
  Deepgram STT biasing.
- Builds the `AgentSession`: Deepgram STT, the mouth LLM plugin, Sarvam TTS, Silero VAD
  (no server-side NC), **`turn_detection=MultilingualModel()`**, dynamic **endpointing**
  (`engine_endpointing_*`), VAD-mode barge-in, `preemptive_generation` **OFF**.
- Creates a `CommittedTurnSource` and starts the `_EngineAgent`.
- Kicks off the **liveness heartbeat** (pulses `sessions.last_engine_heartbeat_at` so the
  stuck-session reaper treats a long interview as alive) and hands off to `_drive`.

### 4.3 The drive loop (`_drive` → `SessionDriver`)
- `driver.intro()` speaks the greeting + one-line role brief **non-interruptible**, then
  `driver.opener()` speaks the first bank question — **no brain call** before the first
  answer.
- A `_consume_turns()` task then loops: `await turn_source.get()` → `driver.handle_turn()`.
- The loop exits on a terminal directive, a candidate disconnect
  (`participant_disconnected` → `candidate_ended`), or an **inactivity timeout** (per-turn
  reset, default 180s → `unresponsive`). On exit it calls `driver.finalize(reason)`.

### 4.4 Each committed turn (`SessionDriver.handle_turn`)
1. LiveKit's turn detector fires `on_user_turn_completed(new_message)`; `_EngineAgent`
   submits `new_message.text_content` to the `CommittedTurnSource` and raises
   `StopResponse()` (the engine owns all speech).
2. **Backchannel drop:** a pure-engagement turn (`is_backchannel`) is dropped before the
   brain — it never moves the floor.
3. Build the `BridgeRequest` (recent openers) and the `BrainTurnInput` (active rubric +
   coverage + the deterministic hints: `floor_interrupted`, `stalled`, `uncovered_signals`,
   `knockout_pending`, `knockout_reflected`).
4. `run_turn`: **bridge ∥ brain → real line**, appending each signal observation to the
   `NoteLog` (see [§3 · loop.py](#the-drive-loop-looppy--pure-no-livekit)).
5. Advance state from the `BrainDecision`: on `ask`, move the active-question pointer to the
   resolver's `next_question_id`; on `probe`, add the dimension slug to `fired_dimensions`;
   update `recent_openers`, the floor question (only `ask`/`probe` update it,
   so `repeat` re-poses the real question), `floor_interrupted`, and the stall counter.
6. **Barge-in:** if the candidate speaks again, VAD-mode interruption cancels the in-flight
   `run_turn` (clean — the AI only ever delivers at a turn boundary).

### 4.5 Close + record (`finalize`)
- Build per-signal `SignalEvidence`, compute `provenance` deterministically from the notes +
  question closures, build the `QuestionRecord` list, assemble `SessionMeta`, and record a
  `KnockoutOutcome` for any knockout signal the brain verified absent
  (`confirmed_knockout_signals()`).
- `NoteLog.to_session_evidence()` → `record_session_evidence()` persists the
  `SessionEvidence` to `sessions.session_evidence_json` and commits the completion (which
  enqueues report scoring). Idempotent + safe against an externally-terminated row.

---

## 5. Data contracts at the edges

### Input — `SessionConfig` (from `interview_runtime`)
Everything the engine needs, assembled before the call:
- **Role context:** `job_title`, `seniority_level`, `role_summary`, `company` (about /
  industry / hiring_bar).
- **Candidate:** `candidate.name`.
- **The question bank** (`stage.questions`): each `QuestionConfig` carries `id`, `position`,
  `text`, `signal_values`, `primary_signal`, `question_kind`, `difficulty`, `is_mandatory`,
  `follow_ups`, and the grading detail — `rubric` (excellent / meets_bar / below_bar),
  `positive_evidence`, `red_flags`, `evaluation_hint`.
- **Signals** + `signal_metadata` (weight / priority / **knockout** flags) and STT `keyterms`.

### Output — `SessionEvidence` (to `interview_runtime` → reporting)
Persisted by `record_session_evidence()` to `sessions.session_evidence_json`
(migration `0054`). **Append-only by design — the engine RECORDS; the report derives
coverage, score, and verdict.**
- `meta` — `SessionMeta` (completion reason, timing, counts).
- `signals[]` — one `SignalEvidence` per role signal: identity + weight + knockout flag +
  computed `provenance` (`not_reached` | `asked_directly` | `cross_credited` | `probed_absent`).
- `notes[]` — the append-only `EvidenceNote` ledger: per observation, the signal, stance
  (`supports`/`contradicts`), texture (`thin`/`concrete`/`strong`), verbatim quote, span,
  question on the floor, via-probe, and any retraction link.
- `questions[]` — `QuestionRecord` per asked question (closure, probes fired).
- `transcript[]` — role-tagged turns.
- `knockout` — a `KnockoutOutcome` if a mandatory signal was verified absent (else `null`).

The reporting module reads this structured evidence as the **spine** of the post-session
report — it projects the per-signal notes onto role signals rather than re-grading from
scratch.

---

## 6. Why the FastAPI process never loads LiveKit

The engine and the FastAPI backend share one Docker image and one virtualenv. But LiveKit
plugins (and their native deps) are heavy and only needed in the engine. Two mechanisms
keep them out of the web process:

1. **Lazy export.** `__init__.py` exports `run`/`server` via PEP-562 `__getattr__`, so
   importing pure artifacts (`Directive`) never imports `agent.py`.
2. **Blessed import site.** All vendor realtime SDKs (LiveKit STT/TTS/VAD/turn-detector
   plugins) are instantiated only in `app/ai/realtime.py`, with lazy imports inside each
   factory. The engine's `agent.py` calls those factories; nothing else touches the plugin
   packages.

One more subtlety, called out in `agent.py`: the Dramatiq broker is bound at the **top of
`agent.py`** (not `__main__.py`), because LiveKit runs each interview in a spawned
subprocess that imports `agent` but never executes `__main__`. Without it, the
report-scoring `.send()` in the job process would fall back to a default broker and fail.

---

## 7. Configuration

All model IDs, efforts, budgets, and prompt versions live in `AIConfig`
(`app/ai/config.py`) and `Settings` (`app/config.py`) — **never hardcoded**. The settings
the gen-3 engine actually reads:

| Setting | Meaning |
|---|---|
| `engine_brain_model` / `engine_mouth_model` | Both tiers on `gpt-5.4-mini`. |
| `engine_brain_effort` / `engine_mouth_effort` | `reasoning_effort` per tier (empty → faster). |
| `engine_brain_prompt_version` / `engine_mouth_prompt_version` | Which `prompts/vN/engine/` tree to read (`v4`). |
| `engine_brain_prompt_cache_key` / `engine_mouth_prompt_cache_key` | OpenAI prompt-cache keys. |
| `engine_bridge_timeout_s` | Hard cap on the bridge call → canned `"Mm, okay…"` fallback. |
| `engine_endpointing_mode` / `engine_endpointing_min_delay_s` / `engine_endpointing_max_delay_s` | Dynamic endpointing — how long the turn detector holds open for think-pauses. |
| `engine_v2_turn_detector_unlikely_threshold` | The turn detector's `unlikely_threshold` (default `None` = model default). |
| `engine_close_reserve_s` / `engine_winding_down_s` | Time-budget knobs for the resolver's `budget_phase`. |
| `engine_stall_reposes_before_advance` | Consecutive non-answer turns before the brain gets a `⚠️ STALLED` hint. |
| `engine_heartbeat_interval_seconds` | Liveness pulse cadence for the reaper. |
| `engine_agent_name` | The LiveKit fleet routing label / mouth persona fallback. |

> Naming note: a few settings keep the historical `engine_v2_*` prefix and the engine logs
> emit `engine.*` strings. There is **only one engine**; the `v2` token on
> `engine_v2_turn_detector_unlikely_threshold` is a vestigial label, not a version switch.

---

## 8. Testing

- **Unit tests** (`tests/interview_engine_v3/`) mock the LLM at the injectable seam
  (`llm_call` on `ControlPlane` / `BridgeComposer` / `ConversationPlane`) — the brain
  derivation, the resolver, the policy gates, the driver state machine, the drive loop, the
  note log, the turn source, and the backchannel gate are all tested deterministically.
- **Prompt behavior is validated by the live talk-test** (talking to the agent on LiveKit
  Cloud), not by unit tests — prompts change far faster than code and their quality is a
  spoken-conversation judgment.
- **Live testing** is by talking to the agent. Coverage in Docker needs the
  `python -m coverage run` workaround (a PyO3 ↔ Python-3.13 segfault in LiveKit's native
  deps breaks `pytest-cov` mid-collection) — see the backend `CLAUDE.md`.

Run the suite: `docker compose run --rm nexus pytest tests/interview_engine_v3 -q`.

---

## 9. Glossary

| Term | Meaning |
|---|---|
| **Bridge** | The immediate short mouth beat fired in parallel with the brain to mask its latency. Never a question, never rubric. |
| **Directive** | The single object brain → mouth. Speakable text + delivery metadata; never rubric. |
| **Coverage** | Per-signal evidence state (`none`/`partial`/`sufficient`), credited across the whole conversation (not per question). Ephemeral runtime steering; the durable record is the append-only notes. |
| **Knockout** | A signal flagged mandatory (`knockout=True`). When verified absent (disclaim → one reflect-back → close), the screen ends early. **Records, never rejects** — borderline is a human's decision. |
| **Floor** | The question currently on the floor (the last `ask`/`probe`). `repeat`/`clarify` re-pose *this*, even after a non-question turn. |
| **Provenance** | How each signal was reached, computed deterministically at close: `not_reached` / `asked_directly` / `cross_credited` / `probed_absent`. |
| **Deterministic hint** | A turn-level fact computed in code (backchannel, `floor_interrupted`, `stalled`, `knockout_pending`, `knockout_reflected`) and fed to the brain — the engine detects, the brain decides. No extra LLM call. |
