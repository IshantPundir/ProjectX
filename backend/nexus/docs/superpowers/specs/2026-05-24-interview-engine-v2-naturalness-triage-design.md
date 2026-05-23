# Interview Engine v2 — Conversational Naturalness via a Triage Tier

**Status:** Design approved (pending written-spec review) — 2026-05-24
**Author:** Ishant (+ Claude)
**Branch:** `feat/interview-engine-v2-m5`
**Supersedes/extends:** the M5 brain wiring (`agent.py`, `brain/`, `mouth/`). Finally implements the
deferred "#2 deliver-when-ready" latency lever (`project_engine_v2_plan` memory).

---

## 1. Context & problem

Three live talk-tests (`d9828b7b`, `ec11e237`, `9f581c21`) validated that the M5 brain genuinely
drives the interview (real grades, coverage, varied moves, knockout, probes, confirms). But the
**conversational delivery sounds robotic**, for two structural reasons:

1. **The masking filler is a canned, content-free phrase fired in isolation.** When the candidate
   finishes, `agent.py` fires one pre-baked `engine_v2_ack_messages` line via `session.say()` (TTS
   only — *not* the mouth LLM), then `await`s the brain (~3s), then the mouth voices the question.
   The candidate hears: *canned filler → ~2–3s dead air → an unrelated-sounding question.* The
   filler doesn't react to what they said, and the question doesn't connect back to the filler.

2. **The filler is decided before the brain classifies the turn**, so it can contradict the brain.
   If the candidate is *still thinking* (or gave an incomplete fragment that the EOU committed) and
   the brain decides `hold`, but a content-reflective filler already presumed an answer, the two
   conflict — and a spoken filler can never be un-said.

We also carry two **already-scoped bug fixes** from the same talk-tests (bundled here so the plan is
complete): **probe-repetition** (`9f581c21`: the same follow-up asked 3×) and two **soft concerns**
(an answer-hint leak in a redirect; an evasive "are you an AI?" answer).

### Why a "brain-aware mouth" alone can't fix it
Once the filler is spoken it's in the candidate's ears. The only way to avoid a *wrong* filler is to
**not fire it** — which means knowing the turn-type *before* speaking. Hence: pull the turn-type
classification out of the (slow) brain into a fast first step.

---

## 2. Goals / non-goals

**Goals**
- Replace the canned masking ack with a **fast, contextual, reactive filler** that links to the
  candidate's actual utterance, and a question that **continues naturally from that filler**.
- **Never** fire a filler that contradicts the brain's eventual move (esp. the "still thinking →
  hold" case).
- Honor the **async / parallel / separate-clocks** principle (D3 / AsyncVoice): no tier is ever
  awaited in silence on the critical path. This finally retires the bounded-await (#2).
- Make **cheap turns fast**: holds / repeats / still-pending answers skip the 3s brain.
- Fix **probe-repetition** and the two **soft concerns**.

**Non-goals (explicitly deferred / out of scope)**
- Brain **prompt-trim** for the ~6s tail (separate latency lever; still deferred).
- A **prosody-based EOU** model (triage's completeness judge is text/semantic, not acoustic).
- Mapping the brain's recorded knockout into the typed `KnockoutFailure` list (known M5 follow-up).
- Any rubric/bank/coverage redesign.

---

## 3. Architecture — the three-tier turn lifecycle

A turn contains two *different kinds* of work with different speed needs:
- **"What kind of turn is this, and what do we say *now*?"** — fast; must precede any speech.
- **"Grade it, update coverage, probe/advance/clarify/knockout."** — slow; only for substantive
  answers.

Today both live in one ~3s brain call. We split the first into a fast **triage** tier.

**At turn-commit, two independent tasks are launched on separate clocks; neither awaits the other:**

```
candidate turn commits
   ├───────────────► TRIAGE task (fast / nano clock)
   │                    lands ~0.8s → SPEAK spoken_line (via session.say)
   │                      • route=HANDLED  → cancel the brain task; done this turn
   │                      • route=TO_BRAIN → leave the brain running
   │
   └───────────────► BRAIN task (slow / mini clock, ~3s)
                        lands (only used if TO_BRAIN) → MOUTH Pass-2 voices the
                        directive via deliver-when-ready (generate_reply from the
                        task's done-callback), continuing from the filler triage said
   barge-in ──────────► cancels BOTH tasks
```

Key invariants:
- `on_user_turn_completed` **launches both tasks and returns**; it does **not** `await`. It raises
  `StopResponse()` so LiveKit's auto-reply doesn't fire prematurely. (The #2 deliver-when-ready
  change.)
- **Triage gates the immediate voice; the brain gates the eventual question** — decoupled.
- **Ordering is naturally safe:** triage (nano) always lands before the brain (mini), so Pass-2
  always has the filler text to continue from.
- **Cost of separate clocks:** on a `HANDLED` turn we cancel a speculatively-launched brain task (a
  partial wasted call). Accepted — it keeps the brain off the critical path on answer turns (the
  common case). We do **not** re-serialize to save it.

---

## 4. Triage tier

New package `app/modules/interview_engine_v2/triage/` (mirrors `brain/`):
`decision.py` (schema), `input_builder.py` (cache-stable prompt assembly), `service.py`
(`TriagePlane` — the bounded LLM call), prompt at `prompts/v3/engine/triage.system.txt`.

### 4.1 Input
- The candidate's **accumulated utterance so far** for the current question (every fragment since the
  active question was posed/re-posed), fenced as DATA (`«…»`) — same spotlighting/identity-lock
  injection defense as the mouth.
- The **active question text** + the **last spoken question** (for repeat) + a 1–2 turn window.
- Persona/identity (so `spoken_line` is in "Arjun" voice).
- **Never the rubric** — triage doesn't grade, so no-leak holds by construction.

### 4.2 Output (`TriageDecision`, instructor structured output)
```python
class TriageKind(StrEnum):
    answering | repeat_request | clarification_request |
    job_question | off_topic | injection | no_experience | indirect_no |
    wants_to_end | nervous | backchannel
    # "still pending" is NOT a kind — it's `kind=answering` + `answer_complete=False`
    # (see §4.4). The model emits `route` directly; HANDLED iff repeat_request OR
    # (answering AND not answer_complete).

class TriageRoute(StrEnum): handled | to_brain

class TriageDecision:
    reasoning: str          # brief, reasoning-first (coherence; like BrainDecision)
    kind: TriageKind
    answer_complete: bool   # semantic completeness judgment for the current question
    route: TriageRoute
    spoken_line: str        # the immediate persona line to speak (filler / hold / continuation)
    replay_last_question: bool = False   # repeat case
```

### 4.3 Routing & filler decision

| `kind` / state | `route` | spoken now |
|---|---|---|
| `answering` + `answer_complete=False` (thinking, trailing off, only-first-part) | **HANDLED** | a **continuation cue** matched to the situation — *"Take your time…"* (thinking) / *"Mm-hmm…"* / *"Go on…"* (mid-production). **No brain.** Accumulate, re-triage next commit. |
| `repeat_request` | **HANDLED** | replay the cached last question verbatim |
| `answering` — complete & substantive | TO_BRAIN | **reflective** filler ("Mm — five years, mostly Python…") |
| `answering` — complete but thin, OR anything **ambiguous** | TO_BRAIN | **neutral** filler ("Mm, okay…") |
| `clarification_request` | TO_BRAIN | neutral lead-in ("Sure —") → brain composes the rephrase |
| `job_question` (meta) | TO_BRAIN | neutral filler → brain `answer_meta` (grounded) |
| `off_topic` / `injection` | TO_BRAIN | **neutral, non-engaging** filler → brain redirects |
| `no_experience` / `indirect_no` | TO_BRAIN | neutral filler → brain coverage/knockout |
| `wants_to_end` / `nervous` | TO_BRAIN | neutral filler → brain closes / reassures |

### 4.4 Semantic completeness ("still pending") — the flexible hold
Triage judges, from **{question + accumulated answer}**, whether the answer is *complete* or *still
pending* — semantically, **not** by keyword (honors the no-regex rule). An explicit "let me think"
is just one obvious pending case; *"So first I'd use connectors and…"* (clearly mid-build) is caught
too. **Guards:**
1. **Hold cap (`engine_triage_hold_cap`, default 2):** after N consecutive `still_pending` holds on
   the *same* answer, force `TO_BRAIN` — the brain evaluates what's there. No runaway "go on…".
2. **Silence:** a candidate who goes quiet after a hold is caught by the existing unresponsive
   ladder (7s/15s).
3. **Accumulation feeds the brain the *full* answer** (see §4.6) — also fixes the fragment-grading +
   brain-cancel-storm seen in `9f581c21`.

### 4.5 Safety rules (make the parallel launch robust)
1. **Reflective filler only when confident it's a complete, substantive answer.** Unsure → neutral
   filler (coherent before *any* eventual brain move, including a hold).
2. **`HANDLED` only for `still_pending` / `repeat_request`.** When unsure between "answer" and "still
   pending", bias to `TO_BRAIN` + neutral filler (never wrongly *stop* the brain).
3. **`spoken_line` never coaches, names answer/solution components, or engages injection content.**
4. **No sycophancy** ("great answer" banned); neutral, warm Indian-English register.

### 4.6 Answer accumulation (`agent.py` state)
- `self._pending_answer: list[str]` — candidate fragments since the active question was posed or
  re-posed.
- Each commit appends the fragment; triage sees the joined text.
- **Reset** when the active question changes (advance), or on clarify/repeat (fresh start on the
  re-posed question).
- When triage routes `TO_BRAIN`, the brain receives the **full accumulated answer** (not the last
  fragment).

### 4.7 Model & fallback
- `engine_triage_model` — nano class (`gpt-5.4-nano`), env-driven via `AIConfig`. Reasoning-first
  field for coherence; no `reasoning_effort` (same pattern as the brain).
- `engine_triage_total_budget_ms` (default ~1500). **On timeout/error → fallback:** a canned
  `engine_v2_ack_messages` line + `route=TO_BRAIN` (today's behavior; a turn never breaks).
- The brain receives triage's `kind` as a hint and may lighten its own INTENT step; the brain's
  `hold`/`repeat` moves become backstops (triage owns them).

---

## 5. Pass-2 — linking the question to the filler

The bank question is **verbatim by design** (D2 — the brain selects bank text, never rewrites). The
mouth already *persona-voices* it (not raw TTS), so it has latitude to add connective tissue.

**Change:** give Pass-2 (`mouth/llm_node`) the **filler text triage just spoke**, and have it compose
a short natural **bridge** from that filler into the directive — **while delivering the directive's
substance faithfully.**

```
triage spoke:   "Mm — five years, mostly Python…"           (Pass-1, trails open with … / —)
bank question:  "How many years hands-on with Workato in production?"
Pass-2 voices:  "…and with Workato specifically — how many years hands-on?"
                 └ bridge ┘   └────────── question substance preserved ──────────┘
```

**Fidelity rule:** filler-linking governs the **lead-in/transition only**. The question's meaning,
scenario details, and specific skill/term stay intact — Pass-2 may not drop or reshape the actual
ask. Same faithfulness bar the mouth holds today; now just filler-aware so it doesn't restart cold or
repeat the filler. Applies to **all** Pass-2 acts (a CLARIFY flows from "Sure —", a REDIRECT from a
neutral "Mm —", etc.).

**Mechanism (small, contained):**
- `agent.py` stores triage's `spoken_line` as `self._last_filler`.
- `mouth/input_builder.build_mouth_messages` gets one new dynamic-suffix field:
  `YOU JUST SAID: «…»` + a style note ("continue from that; don't repeat it; deliver the line below
  faithfully").
- **No-leak unchanged:** mouth still never sees the rubric; verbatim bank text is leak-safe; composed
  acts still pass the no-leak gate. Persona prefix + act block stay byte-stable (cache intact); only
  the dynamic suffix grows by the filler line.
- Triage's filler should **trail open** (`…` / `—`) so Pass-2 reads as a continuation across the two
  TTS utterances.

**Validated explicitly** (prompt-eval, the risky part): Pass-2 *preserves* the bank question while
flowing — bridges, never quietly rewrites/drops the ask.

---

## 6. Latency, models, deliver-when-ready

| Tier | Model | Budget | On failure |
|---|---|---|---|
| Triage | `engine_triage_model` (nano) | ~1.5s | canned ack + `route=TO_BRAIN` |
| Brain | `engine_brain_model` = `gpt-5.4-mini` | 6s | deterministic fallback directive, voiced by Pass-2 |
| Mouth Pass-2 | `engine_mouth_model` = `gpt-5.4-mini` | normal | log; rare |

**Felt timeline, answer turn (after ~1.5s EOU commit):**
```
t=0     commit → launch triage ∥ brain (separate clocks)
t≈0.8s  triage lands → reflective/neutral filler starts (≈ today's ack ~1.1s)
        filler plays ~2s ← longer/contextual than the 1s canned ack → covers more of the wait
t≈3.1s  brain lands → Pass-2 → question (continuing from the filler)
t≈3.8s  candidate hears the question
```
Net e2e ≈ today (~5–6s incl. EOU) but **less dead air** and it *sounds* like one turn. **HANDLED
turns respond ~1s, no brain** — a real win.

**Targets (talk-test):** filler perceived ≤ ~1.2s after commit; HANDLED ≤ ~1.5s; no tier awaited in
silence; brain felt-question ≈ today (masked).

**Mechanism (#2 deliver-when-ready):** `on_user_turn_completed` launches both tasks + raises
`StopResponse()`; triage's done-callback `session.say(spoken_line)`; brain's done-callback (if
`TO_BRAIN`) `session.generate_reply()` → Pass-2. ⚠️ **Verify the exact LiveKit 1.5.9 API for
"speak from a done-callback after StopResponse" against the LiveKit docs MCP at implementation time**
— do not rely on memorized APIs.

**Cost:** 3 LLM calls per answer turn (triage nano + brain mini + mouth mini) vs 2 today; triage is
cheap; plus occasional cancelled brain on HANDLED/pending turns. Accepted.

---

## 7. Error handling & cancellation across tiers
- **Triage** fail/timeout → canned ack + `TO_BRAIN`.
- **Brain** fail/timeout → existing deterministic fallback directive (Pass-2 voices it).
- **Barge-in** → cancel in-flight triage + brain (existing cancel machinery extended to triage); the
  controller's staleness/supersession discards any directive for the superseded turn; the new turn
  re-triages.
- **Hold-cap reached** → force `TO_BRAIN`.
- **Audit:** every triage decision (`kind`, `route`, `spoken_line`, `answer_complete`) and the
  **dev-time triage↔brain disagreement** (on HANDLED turns, the cancelled brain may be allowed to
  finish *only to log* a disagreement — never to change what's spoken) go to the audit envelope as
  new event kinds. Correlation-ID flows through all three tiers. Persisted *transcript* still
  captures the substantive turns (question + candidate); the filler lives in the envelope.

---

## 8. Bundled fixes (brain path — orthogonal to triage)

### 8.1 Probe-repetition (`9f581c21`: same follow-up asked 3×)
Root cause: `soft_probe_cap=2` is constructed but never consulted in the decision path; no follow-up
dedup; the brain is blind to its own probe count.
1. **Enforce the cap (deterministic backstop, `brain/service.py`/`policy.py`):** before honoring a
   `probe`, if `probe_count(active_question) >= soft_probe_cap`, downgrade to `advance`.
2. **Dedup follow-ups:** track used `bank_follow_up_index` per question; never re-select one — pick an
   unused follow-up or advance.
3. **Tell the brain:** surface probe-count / used-follow-ups in the brain prompt so it self-limits.

### 8.2 Soft concerns (`brain.system.txt`)
- **Redirect/clarify answer-leak** (the *"429s, retries, pagination"* slip): prompt rule — clarify
  rephrases the *question* (everyday example OK) but never names solution/answer components; redirect
  brings them back without coaching. Token-based no-leak gate can't catch domain terms → prompt
  constraint + prompt-eval.
- **"Are you an AI?" disclosure:** prompt rule for `answer_meta` — when *directly* asked, confirm
  plainly ("Yes — I'm an AI assistant running this screening"), don't dodge. AIVIA-aligned.

---

## 9. Reconciliation: acoustic hold-space cue vs triage completeness
The M3 *acoustic* silence-timer hold-space cue fires on a long pause **before** commit (the one whose
mid-reasoning misfire we just fixed via `brain_pending`). Triage's *semantic* completeness judge
fires **at** commit. They overlap. **Resolution:** the acoustic cue handles the pre-commit pause;
triage handles at-commit completeness; coordinate so we don't double-cue (e.g., suppress the acoustic
cue briefly after a triage `still_pending` hold, and vice-versa). Detailed coordination is an
implementation task; both must be aware of each other's recent cue.

---

## 10. Testing
- **Pure unit (TDD):** HANDLED-vs-TO_BRAIN routing dispatch; hold-cap counter; answer accumulation +
  reset rules; filler passed to Pass-2; **probe-cap backstop + follow-up dedup** in `service.py`.
  Triage/brain LLM calls mocked at the `app/ai` boundary (as brain tests do today).
- **Real-API prompt-evals (`@prompt_quality`, opt-in):** triage routing + completeness judgment
  (hold / repeat / answer / ambiguous→neutral / injection→neutral-non-engaging); `spoken_line`
  no-leak + anti-sycophancy; Pass-2 linking (continues from filler **and** preserves the bank
  question); the two soft-concern fixes.
- **Talk-test (primary, manual):** filler→question coherence; continuation cues; no double-cueing
  with the acoustic hold-space; latency feel; HANDLED snappiness; probe no longer repeats.

---

## 11. File touch-points (approximate)
- **New:** `triage/{__init__,decision,input_builder,service}.py`, `prompts/v3/engine/triage.system.txt`.
- `agent.py` — launch triage ∥ brain at commit; `StopResponse` + deliver-when-ready done-callbacks;
  `_pending_answer` accumulation + reset; hold-cap; `_last_filler` → Pass-2; cancel triage on
  barge-in; new audit events.
- `mouth/input_builder.py` — `YOU JUST SAID` filler field + style note.
- `brain/service.py`, `brain/policy.py` — probe-cap backstop + follow-up dedup; consume triage `kind`
  hint.
- `prompts/v3/engine/brain.system.txt` — probe-count surfacing; clarify/redirect no-answer-leak;
  `answer_meta` AI-disclosure.
- `config.py` / `ai/config.py` — `engine_triage_model`, `engine_triage_total_budget_ms`,
  `engine_triage_hold_cap`; (brain prompt cache key version bump if the prompt changes).

---

## 12. Open questions / risks
- **Triage accuracy on a nano model** — must reliably classify + judge completeness. Validate via
  prompt-evals + talk-test; if a nano model under-performs, bump `engine_triage_model` to mini (env
  change). Mis-route cost is bounded (§4.5; brain is authority for all `TO_BRAIN`; HANDLED is
  low-stakes + self-heals next turn).
- **Text-only completeness** — no prosody; a purely acoustic mid-word pause with no semantic
  incompleteness cue can still be misjudged. Bounded by the hold-cap + low-stakes-of-wrong-hold.
- **Pass-2 fidelity drift** — flowing from the filler must not rewrite the bank question; guarded by
  the fidelity rule + a dedicated prompt-eval.
- **Wasted brain calls on pending/HANDLED turns** — a tuning point (debounce vs. always-launch); start
  with always-launch (separate clocks) and measure.
