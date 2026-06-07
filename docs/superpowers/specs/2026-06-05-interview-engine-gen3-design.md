# Interview Engine — Gen-3 Design (consolidated spec)

> **Status:** design / research **complete enough to plan against; NO code yet.** This consolidates the
> running notes (`tmp/interview_engine_research/14-gen3-ears-and-agent-rewrite.md`, `15-gen3-prompts.md`)
> and the canonical schema artifacts (`evidence_contract.py`, `brain_input.py`, `directive.py` in that
> folder) into one build-from document. Where a thing is unsettled it is tagged:
> **`[VALIDATE]`** = needs a live talk-test / empirical tuning · **`[OPEN]`** = design decision not yet
> made · **`[ASSUMPTION]`** = unverified estimate.
>
> **Lineage:** gen-1 = Judge/Speaker/State-engine · gen-2 = the CURRENT `triage ∥ brain → mouth` engine
> in `app/modules/interview_engine/` (shipped from `tmp/interview_engine_research/DESIGN-SPEC.md`) ·
> **gen-3 = this**, built on what gen-2 did *in production*.
>
> **Scope:** a ground-up rewrite of the engine's **decision + conversation core**. The data model,
> JD→signals→bank pipeline, session/auth/scheduler infra, candidate UI, storage, and reporting module
> are reused/extended (see §13).

---

## 1. Why gen-3 (the mandate, grounded)

Forensic analysis of production session `5e004a4d-…-6438f6` (engine event-log + LiveKit OTel traces +
DB transcript) found three failures, all of which gen-3 targets:

1. **Turn-taking is the killer.** The text-only `MultilingualModel` turn detector gave a **median EOU
   probability ≈ 0.008 vs its 0.011 threshold** — it judged the candidate "not done" *most of the time*.
   Result: **5 turns held the full `max_delay`** (~9–10s) before the agent replied, and the hold-space
   **"Take your time" cue fired ~3s after the candidate had already finished** (a blind 3s timer whose
   "complete answers commit in ~1–2s" assumption is false). This was the **#1 risk gen-2's own spec
   named** (DESIGN-SPEC §15). *Correction (verified 2026-06-05):* part of the 10s hold was a config
   error — `max_delay` was set to **10** vs LiveKit's current default **3.0**; the deeper cause is
   text-only EOU misreading disfluent Indian-English endings.
2. **The agent under-probes.** Gen-2 deliberately biased to advance (soft cap ~1–2, lenient "concrete"
   grade). In production it advanced on hand-wavy answers and rarely dug in. **The report is only as good
   as the screening, so this caps report quality.**
3. **Coverage is narrow.** This JD has **24 signals but the bank has 8 questions** (1 `primary_signal`
   each) → 16 signals are only captured if the candidate volunteers them.

---

## 2. Goals & non-negotiables

- **Hyper-real, low-latency spoken screen** for **Indian candidates** — feels like a human interviewer,
  not a chatbot.
- **Collector, not judge.** The engine gathers evidence and decides what to ask next. It **never scores,
  ranks, or decides the hire** — the report module does that, post-session, from the engine's notes.
- **No rubric leak** — the tier that talks never holds evaluation criteria (structural, not prompt-pleading).
- **Prompt-injection-proof** — candidate speech is DATA, never instructions.
- **Accurate brain** — the reasoning core is not starved of context (it's the quality lever).
- **Fast + cheap** — real-time conversation; aggressive prompt-caching; brain off the critical path.
- **Auditable** — every decision + the full evidence trail is recorded.

**Locked platform decisions:**
- **DROP LangGraph + LangChain.** No value for the broken part (turn-taking); LiveKit's `LLMAdapter`
  (graph-as-LLM) re-creates the gen-2 "seconds of silence" failure. A hand-rolled async control plane
  is fine. If ever used, brain-only, off the critical path.
- **KEEP the cascade (STT → LLM → TTS).** No end-to-end speech-to-speech (Moshi-class loses reasoning
  quality, is English-only, isn't auditable). All four research papers converge: cascade is right,
  turn-taking is the unsolved part.

---

## 3. Architecture — four layers

```
                 ┌──────────────────────────── one candidate turn ────────────────────────────┐
  candidate ──▶ EAR (non-LLM CPU): Silero VAD ─gate─▶ Smart Turn ∥ MultilingualModel ─▶ fuse
                 │        │ "done"                                                              │
                 │        ├──────────────▶ MOUTH:bridge   (immediate, context-aware, masks)     │
                 │        └──────────────▶ BRAIN (off path) ──Directive──▶ MOUTH:real line ─────┤──▶ TTS ──▶ candidate
                 │                              │ append-only notes + provenance                 │
                 │                              ▼                                                │
                 │                        EVIDENCE CONTRACT ───────────────────────────▶ REPORT │
                 └────────────────────────────────────────────────────────────────────────────┘
```

- **Ear** (§4) — non-LLM CPU models. Owns "done / thinking / barge-in" (took over gen-2 triage's
  `answer_complete` job — better, because it hears prosody).
- **Mouth** (§6) — the only tier the candidate hears; never sees a rubric. Called **twice/turn**: an
  immediate **bridge** (masks brain latency) and the **real line** (voices the brain's Directive).
- **Brain** (§5) — async control plane, off the critical path. Reads context, decides one move, leaves
  append-only evidence notes.
- **The triage LLM tier is DISSOLVED** — done-ness → Ear, classification → Brain, the instant beat →
  the Mouth bridge.

**Calls per turn:** ~1 brain + ~2 mouth (bridge + real line) + cheap CPU Ear. `[ASSUMPTION]` ≈ 2.5 LLM
calls/turn; cost dominated by cached prefixes → single-digit cents/session.

---

## 4. The Ear (turn-taking)

Replaces the single text-EOU-model + blind timer with a small stack of cheap **CPU, open-source** models
feeding a graded decision. **Listen to tone of voice, not just words.**

| Layer | Model | Notes (verified 2026-06-05) |
|---|---|---|
| voice/silence (the GATE) | **Silero VAD** | already in use; also drives barge-in |
| voice *sounded* finished? ⭐ | **Smart Turn v3** (Pipecat/Daily) | audio/prosody EOU; **8MB** Whisper-tiny int8 ONNX; **~12ms CPU**; **≤8s audio window**; **23 langs incl. Hindi 93.4%**; **requires VAD to gate**; NOT a native LiveKit detector. **I/O (confirmed):** `predict_endpoint(audio_16k_mono_≤8s) → {prediction: 0\|1, probability: 0-1}`; prediction=1 ⇒ complete; raw sigmoid `probability`, default threshold 0.5 — **we use the raw probability + our own tuned threshold in the fusion**. Audio zero-padded at the FRONT; Whisper feature-extractor normalization; run on the full turn audio when VAD detects silence. |
| words *sounded* finished? | **LiveKit MultilingualModel** | Qwen2.5-0.5B, 396MB, ~50–160ms CPU; Hindi TP 99.4%/TN 96.3%; **no `unlikely_threshold` knob** in current API; the text vote |
| the decision | **graded ladder** (custom, `turn_taking/eou.py`) | fuse the three → done / thinking / barge-in |

**Topology — PARALLEL, VAD-gated:**
```
Silero VAD (continuous) ──pause──▶ Smart Turn (audio, ≤8s) ∥ MultilingualModel (transcript) ──▶ fuse
```
VAD is the only sequence step (you evaluate "done?" only on a pause). The two EOU models run in
**parallel** (independent audio vs text inputs, both cheap; combining both > either alone — "Easy Turn"
arXiv:2509.23938).

**Fusion rule (graded ladder):**
- both complete → **commit** (~0.5s).
- both incomplete → **wait** + the gated hold cue.
- disagree → voice-finished-but-text-unsure → **lean commit** (the gen-2 rescue — text was the wrong
  signal on disfluent endings); text-complete-but-voice-going → **wait** (never cut off; real silence
  exists via VAD). **`[VALIDATE]`** exact thresholds + disagreement weights need a live talk-test
  against Indian-English audio.

**Mechanism = LiveKit `turn_detection="manual"` (DECIDED 2026-06-05).** Smart Turn isn't a native
LiveKit detector and LiveKit's `TurnDetector` interface only sees text — so our Ear owns the decision and
calls `session.commit_user_turn()` on "done" (which fires the bridge ∥ brain), holds + gates the cue on
"thinking", and uses `session.interrupt()` / VAD for barge-in. This replaces LiveKit's `max_delay`
endpointing (what held 10s).
- **`[VERIFY-at-build]`** invoking the `MultilingualModel` standalone (we call it ourselves in manual
  mode, on the transcript) — confirm the exact callable method. If awkward, **Smart-Turn-audio-only is an
  acceptable v1** (it's the corrective signal); add the text vote as a refinement.
- **Break-glass fallback** (only if manual mode is blocked): LiveKit auto turn-detection with `max_delay`
  10→~3 + Smart Turn in parallel to gate the cue + trigger an early `commit_user_turn()`. Less control;
  not the path.

**Hold cue + unresponsive ladder:** the "take your time" cue fires **only** when the ladder reads
mid-thought (not a blind timer) — this fixes the gen-2 mis-fire. Pre-answer silence escalates: gentle
nudge → "still there?" → close as `unresponsive`. **`[VALIDATE]`** timings.

**Placement:** fusion + ladder in `turn_taking/eou.py`; manual-mode orchestration + the ≤8s audio buffer
in `agent.py`; `build_smart_turn()` in `app/ai/realtime.py` (blessed vendor-SDK site). Telemetry
(per-turn done/thinking decisions + the two probabilities + pre-answer gaps) → `audio_tuning_summary`.
**Observability fix:** record every cue/bridge + the EOU decision to the event-log envelope (gen-2's
hold cue was `log.info` only → invisible).

---

## 5. The Brain (control plane)

Async, off the critical path. Reads context → decides one move → leaves append-only notes. Model:
`gpt-5.x-mini` class, **low reasoning_effort + a `reasoning` field first** (autoregressive grounding →
grade↔move coherence without the extended-thinking latency).

### 5.1 Input (`brain_input.py`) — accurate but cache-cheap
Three layers, static→dynamic (prompt-cache discipline):
- **`BrainSessionContext` (STABLE, cached):** job/role context + the **full signal set** (enables
  crediting any signal) + a **compact bank index** (id/signals/kind/difficulty/mandatory/tier/text/
  follow_ups — **no rubrics**, avoids the ~36KB bloat).
- **`BrainTurnInput` (DYNAMIC, per turn):** the **active question's full rubric** (the only rubric in
  the prompt) · `on_the_floor` (exact last line) · `candidate_utterance` (DATA) · `thread_turn_count` ·
  **`evidence_so_far`** (one `SignalRead` per touched signal: coverage + last_stance + a **verbatim** key
  quote — long-range memory, never a summary) · `transcript_window` (last K turns verbatim) ·
  `budget_phase` (on_track | winding_down) · `uncovered_signals` (weight-ranked focus) · `knockout_pending`.

`evidence_so_far.coverage` uses `CoverageState` (none/partial/sufficient) — the engine's **runtime**
projection (the brain's own prior reads folded forward). It steers the brain; it is **not** persisted as
a verdict (the report derives coverage from notes — §7).

### 5.2 Output (`BrainTurnOutput` in `evidence_contract.py`) — lean
`reasoning` (≤600 chars, audit-only) · `observations: list[SignalObservation]` (per signal touched:
`{signal, stance: supports|contradicts, texture: thin|concrete|strong, coverage_after, quote_span?}`) ·
`move` · `probe_index?` · `preferred_next_signal?` · `composed_say?`.

**Move set:** `probe · ask · clarify · redirect · reassure · answer_meta · repeat · close`.

### 5.3 Behavior (the interview quality)
- **Collector, not judge.** Texture is an *observation* that tells the brain whether to push — not a
  score. Length/confidence are not evidence. Credit a signal even from a different question (cross-credit).
- **Thin ≠ bluff → ELICIT.** When thin, the brain does NOT decide bluff-vs-modest — it warmly asks for
  one specific (a follow-up). The probe is the disambiguator: specifics appear (collected) or nothing
  comes after a fair ask (the absence is the record). **Same warm move either way → no hostility risk.**
  The bluff/modest *verdict* is the **report's** job (whole-session pattern + timing).
- **Anti-grind (SCREEN, not panel).** Advance as soon as ANY: primary signal well-supported (concrete/
  strong) OR one good elicitation given and no new specifics (tapped out) OR genuinely absent. One good
  probe beats three. `winding_down` → at most one quick elicitation. **`[VALIDATE]`** the elicitation
  count feel.
- **Indian candidates (load-bearing).** Hedges often = modesty, not evasion. Don't read thinness as a
  red flag; draw out gently. A plain "yes/haan" can mean "I'm following" — confirm a skill with one
  concrete ask.
- **Knockout (only when `knockout_pending`):** probe directly → check OR-alternatives → reflect-confirm
  → only a confirmed real "no" closes. **Records, never rejects** (borderline → human); close stays warm,
  never states a reason.

### 5.4 Question/probe selection (the split)
- **probe-vs-advance** = brain (`move`).
- **which probe/follow-up** = brain, context-aware (`probe_index` into the active question's bank
  `follow_ups`).
- **which next MAIN question** = the deterministic **resolver** (§8) — guarantees mandatory coverage
  (fixes the gen-2 "LLM leapfrogged a mandatory question" bug), budget, and no-repeat. The brain only
  *hints* via optional `preferred_next_signal`, honored when budget allows + coverage isn't threatened.
- Context-awareness also lives in HOW a question is pursued (probe deeper sooner if a topic was already
  touched), not WHICH main question is chosen.

---

## 6. The Mouth (conversation plane) + the bridge

The only tier the candidate hears. **Never receives a rubric** (no-leak by construction). Called twice
per turn (`directive.py`):

### 6.1 The bridge (immediate beat, ∥ brain)
The instant the Ear commits, the mouth speaks a **context-aware gist-mirror** bridge — `BridgeRequest`
(sees ONLY the candidate's words). It masks the brain's ~2–3s. **Replaces the canned reflex catalog**
(a canned beat is instant OR long-enough-to-mask, never both; context-awareness breaks that limit).
- **Hard constraint:** neutral gist-mirror landing on a thinking pause — commits to NOTHING about
  quality or the next move (it fires before the brain decides → else it risks contradicting the brain,
  the AsyncVoice self-contradiction trap). e.g. *"Mm, five years mostly Python, okay…"* ✓ ·
  *"Okay, that's solid—"* ✗.
- **Robustness:** a tiny pre-rendered **canned beat is the FALLBACK** if the bridge call errors/times out
  → never dead air.
- **`[VALIDATE]`** first-audio latency (~1s LLM TTFT + TTS TTFB vs ~0ms canned). **`[OPEN]` enhancement:**
  an instant pre-rendered micro-beat ("Mm—") that the LLM bridge continues — instant + rich, at the cost
  of chaining 3 utterances. Ship bridge-only first.

### 6.2 The real line
When the brain's `Directive` lands, the mouth voices it via `MouthTurnInput` (`directive` + `just_said`
= the bridge it continues from, so **one ack/turn, no repeat** + `recent_openers` to vary connectives).

### 6.3 `Directive` (brain → mouth real line, `directive.py`)
`act` (ask/probe/clarify/redirect/reassure/answer_meta/repeat/close) · `say` (verbatim bank text for
ask/probe, leak-scrubbed `composed_say` for the rest, None → compose from act prompt for close) · `tone`
· `spoken_setup?` · `is_terminal`. Derived by the engine from `BrainTurnOutput` (move→act; engine
resolves `say`). Validated leak-free.

### 6.4 Persona & voice (full prompt in §11 / `15-gen3-prompts.md`)
Warm Indian-English ("Arjun"); ≤2 short sentences, one question, spoken numbers/acronyms, 2–4 light
Indian-English fillers ("mm/achha/ya/na/right/so", never American "um/like"); **anti-sycophancy** (never
gush); **identity lock** + DATA boundary (injection); holds no facts it wasn't given.

---

## 7. The engine→report evidence contract (`evidence_contract.py`) — APPEND-ONLY

The engine hands the report a structured, provenance-rich, append-only artifact so the report scores
from real evidence instead of re-grading the raw transcript. **Reviving the abandoned gen-2
`SignalLedger` (better, aligned to collector-not-judge).**

- **`SessionEvidence`** = `meta` (`SessionMeta`) + `signals: list[SignalEvidence]` + `notes:
  list[EvidenceNote]` + `questions: list[QuestionRecord]` + `transcript: list[TranscriptTurn]` +
  `knockout: KnockoutOutcome | None`.
- **`notes` = the append-only source of truth.** One immutable `EvidenceNote` per (utterance × signal):
  `{seq, turn_ref, signal, stance, texture, quote, span, from_question_id, via_probe, retracts_seq?}`.
  - **Accumulation has NO merge rules** — just append. **Retraction = append a `contradicts` note with
    `retracts_seq → the prior note`; both kept** (honest-correction-vs-flip-flop is the report's call).
  - **Cross-credit never skips a question** — it only adds a note; the dedicated question still runs.
- **`SignalEvidence` is THIN** — identity (signal/type/weight/priority/knockout) + **derived
  `provenance`** only. **No coverage verdict** (the report derives coverage from the notes — robust:
  can't be misled by a bad engine rollup).
- **`provenance`** (the killer field) = `not_reached | asked_directly | cross_credited | probed_absent`.
- **`QuestionRecord`** = `{question_id, primary_signal, tier (core|coverage), outcome (asked|not_reached),
  closure?, asked_at_turn?, probes_used, probes_available, time_spent_s}`.
- **`closure`** (engine-inferred, only when asked) = `satisfied | tapped_out | absent | truncated` — the
  honesty valve distinguishing a fair elicitation from a time-cut.
- **`TranscriptTurn`** carries word timing + **`pre_turn_gap_ms`** (think-time) — the report derives
  flatline/copilot patterns from it (engine carries timing, never labels it).
- **Brain stays lean** — rich evidence (quotes already in notes; specificity/bluff/communication/
  narrative) is the report's post-session job. The only thing the contract must preserve that the
  transcript loses is **timing** (already-captured engine metadata).

### 7.1 Provenance computation (deterministic, at session close, per signal S)
`own_questions(S)` = questions whose `primary_signal == S`; `asked_fairly(Q)` = `outcome==asked AND
closure != truncated`. First match wins:
1. supporting note from S's own question → **`asked_directly`**
2. supporting notes only from OTHER questions → **`cross_credited`**
3. an own_question `asked_fairly` + zero supporting notes → **`probed_absent`** (real negative)
4. else → **`not_reached`** (no data — never penalize)

Falls out cleanly: has-supports ⟺ {asked_directly, cross_credited}; no-supports ⟺ {probed_absent,
not_reached} — provenance is what tells the report "real negative" vs "no data".

### 7.2 Report-side changes `[OPEN]`
The report becomes provenance-aware and verifies engine evidence against quotes instead of re-deriving
(lighter recheck). The exact reporting refactor is a follow-up — `app/modules/reporting/` rewrite of its
input contract (the user is open to fully rewriting it). **`[OPEN]`** scope of that rewrite.

---

## 8. Time-budget + question resolver (deterministic)

The time budget decides which questions get asked, which sets provenance — one mechanism.

- **Two-tier bank** (`question_bank` extended): a **core** set (fits the budget, guarantees all mandatory
  + top-weight signals) + a pre-written, weight-ranked **coverage/overflow** pool for the remaining
  signals. Schema add: `tier` (core|coverage) + priority.
- **Resolver (deterministic, on each advance):** uncovered CORE question remains → ask it (mandatory
  never dropped) → else if `budget_left > close_reserve + est_time(best overflow)` and uncovered signals
  remain → highest-weight uncovered overflow → else close. Respects "AI never interrupts" (truncation
  waits for the current answer to finish).
- **Brain's only time input** = the coarse `budget_phase` (on_track | winding_down) hint (arithmetic
  stays in the resolver). The brain never reasons over raw seconds.
- **`[VALIDATE]/[ASSUMPTION]`** `close_reserve ≈ 45s`, the `winding_down` threshold, and the bank's
  `estimated_minutes` accuracy — tune against real session lengths.

---

## 9. Caching & latency discipline

3-layer static→dynamic stack (OpenAI cache matches a prefix; static first, dynamic last):
```
[ SYSTEM PROMPT ]   ← job-AGNOSTIC, byte-identical for EVERY session+tenant → fleet-wide cache reuse
[ SESSION CONTEXT ] ← BrainSessionContext (role + signals + bank index); stable for the session
[ DYNAMIC SUFFIX ]  ← BrainTurnInput (per turn) — the only new tokens
```
- `prompt_cache_key = "brain:v4" / "mouth:v4"` per surface. Target ≥1024-token prefix (cache-eligible).
- **System prompts are cached → completeness there is cheap; the discipline is keeping the dynamic
  suffix tight.** Mouth output held to 1–2 spoken sentences (voice prompts 60–70% shorter).
- Brain off the critical path; the bridge masks its latency. **`[ASSUMPTION]`** perceived latency target
  ≈ the bridge's ~1s first audio + flowing speech; real reply masked.

---

## 10. Schemas (canonical = the `.py` artifacts)

Canonical, runnable, validated on pydantic 2.13.4:
- `tmp/interview_engine_research/evidence_contract.py` — `BrainTurnOutput`, `SessionEvidence` + parts.
- `tmp/interview_engine_research/brain_input.py` — `BrainSessionContext`, `BrainTurnInput` + parts.
- `tmp/interview_engine_research/directive.py` — `Directive`, `MouthTurnInput`, `BridgeRequest`.

**`[OPEN]`** on build these promote to a module (e.g. `app/modules/interview_engine/contracts/` or
extend `interview_runtime`); enums (`CoverageState`, `EvidenceStance`, `EvidenceTexture`, `Provenance`,
`BrainMove`, `DirectiveAct`, …) are shared across all three.

---

## 11. Prompts (full text = `15-gen3-prompts.md`)

- **Brain system prompt** — outcome-framed, `reasoning`-first, collector-not-judge + thin≠bluff→elicit,
  one coherent anti-grind DONE rule (no contradictions), DATA boundary, Indian under-sell handling,
  closed move set, knockout records-never-rejects. **Job-agnostic** (→ fleet-wide cache).
- **Mouth** — persona preamble (cached) + tiny per-act blocks (ask/probe/clarify/reassure/answer_meta/
  close) + the **bridge** block (gist-mirror, commit-to-nothing) + dynamic suffix (`SAY`/`TONE`/`JUST
  SAID`/`RECENT OPENERS`/`SPOKEN SETUP`). Indian-English, voice-tight, anti-sycophancy, identity lock.
- **`[VALIDATE]`** a prompt eval set (doc 13: 20+ cases — happy path, injection, indirect-no, garbled
  STT) before trusting them.

---

## 12. Module / placement map

| Concern | Location |
|---|---|
| Ear fusion + ladder + cues | `app/modules/interview_engine/turn_taking/{eou,pacing,floor}.py` (rewrite) |
| Ear orchestration (manual turn control, ≤8s audio buffer, the per-turn loop) | `app/modules/interview_engine/agent.py` (rewrite) |
| Smart Turn / VAD / turn-detector plugin instantiation | `app/ai/realtime.py` (`build_smart_turn()` next to `build_vad`/`build_turn_detector`) |
| Brain / Mouth / Directive / contracts | `app/modules/interview_engine/{brain,mouth}/`, `contracts/` (new) |
| Config in / telemetry out (EOU calibration, evidence contract persistence, `audio_tuning_summary`) | `app/modules/interview_runtime/` (extend) |
| Two-tier bank generation (core + coverage, schema add) | `app/modules/question_bank/` (extend) + a migration |
| Report consuming the new contract | `app/modules/reporting/` (rewrite input) `[OPEN]` |
| Prompts | `prompts/v4/engine/` (new) |

---

## 13. Rewrite vs reuse

| Disposition | Components |
|---|---|
| **REWRITE (fresh)** | Ear (turn-taking, manual mode, Smart Turn), the per-turn loop, Brain, Mouth (+bridge), the Directive, the evidence contract, the resolver. All engine prompts (v4). |
| **EXTEND** | `question_bank` (two-tier core/coverage + schema/migration); `interview_runtime` (new contract + EOU calibration + telemetry); `app/ai/realtime` (Smart Turn); `reporting` (consume the new contract). |
| **REUSE AS-IS** | Auth/RLS, scheduler/invite, session `/start`+OTP+single-use token, LiveKit room provisioning, `frontend/session` candidate surface, STT keyterm extraction, storage, the module/DB/tenant architecture. |

---

## 14. Open questions & unclear items (consolidated)

- **`[VALIDATE]`** Ear fusion thresholds + disagreement weights (Smart Turn vs text) on real
  Indian-English audio — the single highest-risk tuning.
- **`[VALIDATE]`** bridge first-audio latency (~1s) acceptability; whether the instant micro-beat hybrid
  is needed.
- **`[VALIDATE]`** brain elicitation feel (does "dig on suspicion" read as warm, not hostile, for
  undersellers?); prompt eval set (20+ cases).
- **`[VALIDATE]/[ASSUMPTION]`** time-budget constants (`close_reserve`, `winding_down`), bank
  `estimated_minutes` accuracy; unresponsive-ladder timings.
- ~~`[OPEN]` Ear mechanism~~ **RESOLVED 2026-06-05: manual turn control (primary).** Build-verify: the
  standalone `MultilingualModel` invocation; Smart-Turn-only is an acceptable v1 if that's awkward.
- **`[OPEN]`** scope of the `reporting/` input rewrite (provenance-aware scoring, lighter recheck).
- **`[OPEN]`** where the schemas/enums live on build (new `contracts/` module vs extend `interview_runtime`).
- **`[OPEN]`** cutover/migration sequencing from gen-2 (the implementation plan's job).
- **`[ASSUMPTION]`** cost/latency model (~2.5 LLM calls/turn, cents/session) — confirm with metrics.
- ~~`[OPEN]` Smart Turn v3 output contract~~ **RESOLVED 2026-06-05:** `predict_endpoint(audio_16k_mono_≤8s)
  → {prediction: 0|1, probability: 0-1}` (sigmoid, default thr 0.5); use the raw probability + our tuned
  threshold in the fusion. See §4.

---

## 15. Build sequencing (high-level — a full plan is a separate doc)

1. **Schemas + module skeleton** (contracts, the four-layer wiring, manual turn control).
2. **Ear** (Silero + Smart Turn + MultilingualModel + fusion) → talk-test the ladder. *(highest risk first)*
3. **Bridge ∥ brain ∥ mouth** loop (Directive, append-only notes) → talk-test naturalness.
4. **Brain prompt + behavior** (elicitation, anti-grind, knockout) → prompt evals.
5. **Two-tier bank + resolver + time-budget**.
6. **Evidence contract + provenance** → **report input rewrite**.
7. **Eval harness + tuning pass.**

---

## 16. Source artifacts

- Running design notes: `tmp/interview_engine_research/14-gen3-ears-and-agent-rewrite.md`
- Prompts: `tmp/interview_engine_research/15-gen3-prompts.md`
- Schemas: `tmp/interview_engine_research/{evidence_contract,brain_input,directive}.py`
- Prior research (gen-2): `tmp/interview_engine_research/{00-13,DESIGN-SPEC}.md` + `papers/`
- v1 forensic diagnosis: session `5e004a4d-…-6438f6` (engine-events + `tmp/LiveKit-logs/`)
