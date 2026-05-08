# Interview Engine — Phase 2: Naturalness & Empty-Output Fix

**Status:** Draft — pending implementation
**Date:** 2026-05-08
**Author:** Ishant Pundir (with Claude)
**Scope:** Speaker model selection, LiveKit endpointing, and two prompt rewrites
**Predecessor:** `2026-05-08-interview-engine-judge-speaker-redesign-design.md` (Phase 1)

---

## 1. Problem statement

Phase 1 fixed the four bugs from session `8317142f`. A new live test (session
`09e8fc33-8a4f-4e4d-b2a3-de5848e861fc`) confirmed all four are gone but
exposed **four new naturalness issues**:

### 1.1 Speaker LLM emits empty text (Bug E)

**Frequency**: 2 of 14 Speaker calls in the new session (~14%) returned empty
strings. The Phase 1 fallback (`speaker.output.empty` audit + deterministic
restate) prevents user-visible silence, but the underlying empty-stream
problem is recurring.

**Root cause** — confirmed via OpenAI's official Responses-API docs:

> "If the generated tokens reach the context window limit or the
> `max_output_tokens` value, the response will be `incomplete` with `reason`
> set to `max_output_tokens`. **This can happen before any visible output,
> leading to costs without a visible response.**"

`engine_speaker_model = "gpt-5.4-mini-2026-03-17"` is a **reasoning model**.
Reasoning models consume reasoning tokens *before* emitting visible
`response.output_text.delta` events. With our growing prompt + uncapped
transcript, the budget is consumed by reasoning before any text streams
back. The Speaker's job — composing 1-2 sentences of natural English — is a
visible-text task, not a reasoning task. Wrong tool for the job.

### 1.2 EOU fires mid-sentence on candidate pauses (Bug F)

Tuning summary from the new session:

| Metric | p50 | p95 | max |
|---|---|---|---|
| `end_of_utterance_delay_ms` | 2,432 | **5,527** | 5,527 |
| Candidate pauses | 399ms | **18,197ms** | **22,050ms** |

EOU delay is hitting (and exceeding) the configured `endpointing_max_delay`
of 2.5s on the long tail. Candidate pauses regularly run 5-22 seconds —
candidates pause to think mid-answer. The current settings don't accommodate
that.

Two configuration knobs are wrong:

1. **`unlikely_threshold = 0.15`** — set in
   `app/ai/realtime.py::build_turn_detector` and exposed via
   `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD`. Per the LiveKit source
   (`livekit-plugins-turn-detector/base.py`), this is the **minimum EOU
   probability** required to commit a turn-end. Higher = more patient.
   Per-language tuned defaults are ~0.3-0.5. Our 0.15 is **more eager** than
   default.

2. **`engine_endpointing_max_delay = 2.5s`** — set in `app/config.py`. Caps
   the wait period at 2.5 seconds. Any candidate pause longer than that
   triggers turn-end regardless of EOU model confidence.

### 1.3 `clarify` is too restrictive (rubric leak)

In session `09e8fc33`, the candidate said *"explain the question to me"* and
*"I didn't really fully understand the question"*. Both were correctly
classified as `clarify`, but `clarify.txt`'s scaffold says "Pick the ONE
most relevant term to explain". With no specific term in the candidate's
utterance, the Speaker hallucinated *"validators"* — a **rubric anchor** —
and explained it. Twice.

A real interviewer in the same situation rephrases the question in plainer
English, optionally giving a generic non-rubric example, but *never*
lectures on terminology the candidate didn't ask about.

### 1.4 Speaker echoes candidate's words (robotic acknowledgments)

Every probe in session `09e8fc33` started with *"You mentioned X..."*:

- *"You mentioned using validators to block a transition…"*
- *"You're talking about external API calls and the tradeoff with ScriptRunner…"*
- *"You mentioned using calendars and email to keep people informed…"*
- *"You'd add notifications and basic error handling…"*

The current `deliver_probe.txt` says "Reference what they said for
continuity, but do NOT quote them verbatim. Paraphrase or allude." — and
the LLM applies it on every turn. A real interviewer would just say *"And
how would you handle [next dimension]?"* — no recap. Recaps belong only
when there's a specific terminology hook worth tying to.

---

## 2. Design principles (additive to Phase 1)

### 2.1 Right tool for the right LLM call

- **Judge** = reasoning model (structured JSON, classification, careful
  rubric reasoning). Stays on `gpt-5.4-mini`.
- **Speaker** = chat model (visible text generation, low-latency streaming,
  no reasoning budget consumption). Switches to `gpt-5.3-chat-latest`.

This pairing also matches LiveKit's `interview_llm_model` (the realtime LLM
in the audio pipeline) which is already on `gpt-5.3-chat-latest` — same
model family.

### 2.2 Real-interviewer behavior across the board

Phase 1 made the Speaker prompts *generative not template-matching*. Phase 2
extends that: Speaker prompts must replicate how a **real interviewer
actually behaves** in each scenario. Where Phase 1's scaffolds err on
restrictive (to prevent rubric leaks), Phase 2 loosens them where the
restrictive shape misfires:

- Real interviewers **rephrase** when candidates don't understand. They give
  generic non-rubric examples to anchor understanding.
- Real interviewers **don't recap** every utterance they heard before
  asking the next question. Recaps are for specific terminology hooks, not
  conversational glue.

The anti-leak rules (no rubric content, no anchor enumeration, no scoring
hints) remain non-negotiable.

### 2.3 Endpointing tuned for thinking time

Real interviews have long pauses. Candidates think 5-15 seconds about
non-trivial questions. The system must be patient enough to accommodate that
without firing premature turn-ends. Per LiveKit docs:

- `mode = dynamic` adapts within `[min_delay, max_delay]` based on session
  pause statistics. We keep this.
- `max_delay = 6s` gives candidates a hard 6-second pause budget without
  cut-offs.
- `unlikely_threshold = None` (drop the override) lets the language-tuned
  default apply (~0.3-0.5 for English) — more conservative than 0.15.

---

## 3. Changes

### 3.1 Speaker model swap (fixes Bug E)

`backend/nexus/app/config.py`:

```python
engine_speaker_model: str = "gpt-5.3-chat-latest"   # was: "gpt-5.4-mini-2026-03-17"
```

No code changes elsewhere. The SpeakerService's `responses.stream(...)` call
is already chat-model-compatible:
- `instructions=...` parameter is accepted by chat models per OpenAI's
  Prompt Engineering guide.
- Streaming event `response.output_text.delta` is emitted by chat models
  per OpenAI's Streaming Responses TypeScript event-type list.
- `reasoning_effort` is NOT passed by SpeakerService (verified — see
  `speaker/service.py`); chat models would reject it with HTTP 400 if it
  were.

The audit envelope's `model_versions["speaker"]` will record the new model
ID. Past envelopes still validate (the field is `str`).

### 3.2 Endpointing config (fixes Bug F)

`backend/nexus/app/config.py`:

```python
engine_endpointing_max_delay: float = 6.0           # was: 2.5
# engine_endpointing_min_delay stays at 1.0
interview_turn_detector_unlikely_threshold: float | None = None  # was: 0.15
```

`backend/nexus/.env.example`:

- Update `ENGINE_ENDPOINTING_MAX_DELAY=6.0`.
- Remove or comment out `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=` so the
  None default applies.

`build_turn_detector()` in `app/ai/realtime.py` already handles `None` by
falling through to `MultilingualModel()` (no threshold override). No code
change needed there.

The `audio.tuning_summary` event will record the new values automatically
(it reads from settings at close time).

### 3.3 `clarify.txt` rewrite (fixes Bug 1.3)

The new scaffold uses **two paths** the model picks based on the candidate's
utterance:

**Path A — specific term asked**: candidate said *"what does X mean"* /
*"what's a Y"* about a literal term in the active question. Behavior:
explain that term plainly, then restate the question.

**Path B — generic confusion**: candidate said *"explain that question"* /
*"I don't understand"* / *"can you rephrase"* / similar non-specific. The
candidate didn't name a term to explain. Behavior:
- **Rephrase the question in plainer English**. Use shorter sentences,
  drop jargon.
- **Optionally** give a generic non-rubric example to anchor understanding
  ("for example, walk me through a project you'd actually deliver"). The
  example must NOT name any rubric anchor — no validators, no specific
  workflow concepts, no scoring hints.
- Then restate the question.

Anti-leak rule (load-bearing): if no specific term is in the candidate's
utterance, the Speaker MUST NOT pick a term from `bank_text` to explain on
its own initiative. That's how rubric leaked in session `09e8fc33`.

### 3.4 `deliver_probe.txt` rewrite (fixes Bug 1.4)

Default behavior changes from "always reference what they said" to "ask
the next question naturally; only echo when it adds clarity". Specifically:

- **Default path**: One short transition phrase + the probe. No recap.
  Example: *"And how would you handle migration with minimal disruption?"*
- **Echo path** (rare): Used only when there's a *specific term* the
  candidate used that the probe directly extends. Example: *"On the
  validators specifically — what does the user see when one fails?"*

The prompt makes "no recap" the default; "echo" requires explicit
justification (a specific terminology hook).

### 3.5 Judge prompt tweak (supports 3.3)

`prompts/v1/engine/judge.system.txt` §4 (CLARIFY):

Current rule allows `clarify` for *"what does X mean"* literal asks only.
Generic confusion ("I don't understand", "explain the question") is
ambiguous — the new session's Judge classified those as `clarify`, which is
arguably correct (the candidate is asking for help with the question, not
trying to deflect). Phase 2 keeps that classification but adds clarification
to the prompt:

> `clarify` covers TWO sub-cases:
> 1. Candidate asked about a specific term (*"what does validators mean?"*)
> 2. Candidate is generically confused (*"I don't understand"*, *"can you
>    rephrase"*, *"explain the question"*)
>
> The Speaker scaffold handles both — for case (2) it rephrases the whole
> question rather than picking a term to explain.

If the candidate is clearly stalling rather than confused (*"can you give
me a hint?"*, *"what are you looking for?"*), still emit `redirect` per §4
REDIRECT.

---

## 4. Testing approach

Phase 2 is mostly configuration + prompt changes. Tests:

### 4.1 Update existing prompt-loadable tests

`tests/interview_engine/speaker/test_speaker_prompt_loadable.py` already
asserts each per-action body composes correctly. The rewritten `clarify.txt`
and `deliver_probe.txt` must still pass the existing assertions
(`OUTPUT DISCIPLINE`, `TASK`, `EXAMPLES`, length > 200).

Add one assertion to `test_speaker_prompt_loadable.py`:

```python
def test_clarify_does_not_pick_rubric_anchors_unprompted():
    """The clarify scaffold must instruct the model to rephrase the
    question when no specific term is asked, NOT to pick a term and
    explain it. This is the Phase 2 anti-leak guard."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble", "engine/speaker/clarify",
    )
    # Path B (generic confusion) must be explicit.
    assert "rephrase" in body.lower() or "rephrasing" in body.lower()
    # The Pick-a-term-to-explain instruction must be GUARDED, not unconditional.
    # Anti-pattern: "Pick the ONE most relevant term to explain"
    assert "Pick the ONE most relevant term to explain" not in body
```

### 4.2 Update settings test

`backend/nexus/tests/test_engine_settings.py` — update the asserted default
for `engine_speaker_model` and `engine_endpointing_max_delay` to match the
new values. Drop the test of `interview_turn_detector_unlikely_threshold`
default if one exists, or update it to `None`.

### 4.3 Manual session re-run

Re-run a session against the same Jira-admin job. Verify:

| Behavior | Expected |
|---|---|
| Candidate asks *"explain the question"* | Speaker rephrases the question, doesn't lecture on validators |
| Candidate gives partial answer, Speaker probes | Probe doesn't start with *"You mentioned..."* |
| Candidate pauses 4-5s mid-thought | Agent doesn't fire EOU; waits for them to finish |
| Speaker streams a normal response | No `speaker.output.empty` audit events fire |

If any of the four behaviors don't hold, capture the new envelope for
forensic comparison.

---

## 5. Non-goals

- **No code changes to SpeakerService streaming logic.** The model swap
  alone fixes Bug E.
- **No new audit kinds.** Phase 1's `speaker.output.empty` stays as the
  defense-in-depth fallback in case the new chat model ever streams empty
  (it shouldn't).
- **No Judge prompt restructure.** Phase 2 only adds 1-2 sentences to §4
  to clarify routing.
- **No latency tuning beyond endpointing.** Judge call latency (~2-4s)
  remains the baseline.

---

## 6. Files touched (summary)

### Modified

```
backend/nexus/app/config.py
backend/nexus/.env.example
backend/nexus/prompts/v1/engine/speaker/clarify.txt
backend/nexus/prompts/v1/engine/speaker/deliver_probe.txt
backend/nexus/prompts/v1/engine/judge.system.txt           (small §4 tweak)
backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py
backend/nexus/tests/test_engine_settings.py                (default values)
```

No created files. No deleted files.

---

## 7. Open questions

None at design time. All decisions captured above.
