# Interview Engine — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four naturalness issues exposed by session `09e8fc33-…`: (1) Speaker emits empty text intermittently, (2) end-of-utterance fires mid-sentence on candidate pauses, (3) clarify leaks rubric vocabulary on generic confusion, (4) probe scaffold robotically echoes candidate's prior words.

**Architecture:** Speaker model swap (`gpt-5.4-mini` reasoning → `gpt-5.3-chat-latest`), endpointing config tune (`max_delay 2.5→6s`, drop `unlikely_threshold` override), prompt rewrites for `clarify.txt` + `deliver_probe.txt` + small Judge-prompt §4 tweak.

**Tech Stack:** Python 3.13, Pydantic v2, OpenAI Responses API (chat-model streaming), LiveKit Agents (turn detector + endpointing).

**Spec reference:** `docs/superpowers/specs/2026-05-08-interview-engine-phase2-naturalness-design.md`

**Last failing-session reference:** `backend/nexus/engine-events/09e8fc33-8a4f-4e4d-b2a3-de5848e861fc.json`.

---

## File structure overview

| Path | Action | Notes |
|---|---|---|
| `app/config.py` | modify | `engine_speaker_model` default + `engine_endpointing_max_delay` default + drop `interview_turn_detector_unlikely_threshold` default override |
| `.env.example` | modify | Update defaults |
| `prompts/v1/engine/speaker/clarify.txt` | rewrite | Two-path scaffold: specific term vs generic confusion |
| `prompts/v1/engine/speaker/deliver_probe.txt` | rewrite | Default no recap; echo path only for terminology hooks |
| `prompts/v1/engine/judge.system.txt` | small tweak | §4 CLARIFY accepts generic confusion sub-case |
| `tests/interview_engine/speaker/test_speaker_prompt_loadable.py` | modify | Add anti-leak assertion on clarify |
| `tests/test_engine_settings.py` | modify | Update defaults |

---

## Phase 2 task sequence

### Task P2.1: Switch Speaker model to `gpt-5.3-chat-latest`

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`
- Modify: `backend/nexus/tests/test_engine_settings.py`

**Background:** OpenAI's official docs confirm reasoning models can return `incomplete` responses with no visible output when reasoning tokens consume the budget. The Speaker is a streaming-text task and must use a chat model. `gpt-5.3-chat-latest` is the same model already used for `interview_llm_model` (the realtime LLM in the audio pipeline) — proven in production for low-latency text streaming.

**Step 1:** Update `engine_speaker_model` in `app/config.py`. Find:

```python
engine_speaker_model: str = "gpt-5.4-mini-2026-03-17"
```

Change to:

```python
engine_speaker_model: str = "gpt-5.3-chat-latest"
```

**Step 2:** Search `.env.example` for `ENGINE_SPEAKER_MODEL`:

```
grep -n "ENGINE_SPEAKER_MODEL" backend/nexus/.env.example
```

If present, update its value to `gpt-5.3-chat-latest`.

**Step 3:** Update the settings test. Search:

```
grep -n "engine_speaker_model" backend/nexus/tests/test_engine_settings.py
```

If a test asserts the default value, update the expected to `"gpt-5.3-chat-latest"`.

**Step 4:** Run tests:

```
docker compose run --rm nexus pytest tests/test_engine_settings.py -v
docker compose run --rm nexus pytest tests/interview_engine/speaker/ -v
```

Expected: all PASS.

**Step 5:** Commit:

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example backend/nexus/tests/test_engine_settings.py
git commit -m "fix(engine): switch Speaker to gpt-5.3-chat-latest (Bug E)

Reasoning models can return incomplete responses with no visible output
when reasoning tokens consume the budget. The Speaker composes 1-2
sentences of natural English — a streaming-text task that belongs on a
chat model. gpt-5.3-chat-latest is the same model already used for the
realtime LLM in the audio pipeline; proven for low-latency text streaming.

Audit: speaker.output.empty fires from session 09e8fc33-... should drop
to ~zero after this swap. Phase 1's deterministic fallback stays as
defense in depth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task P2.2: Tune endpointing for thinking pauses

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/.env.example`
- Modify: `backend/nexus/tests/test_engine_settings.py` (if defaults are asserted)

**Background:** New session showed candidate pauses up to 22s and EOU delays up to 5.5s, both exceeding the current `max_delay=2.5s` cap. Per LiveKit source (`livekit-plugins-turn-detector/base.py`), `unlikely_threshold` is the **minimum EOU confidence** required to commit turn-end — higher = more patient. Per-language tuned defaults are ~0.3-0.5; our 0.15 override is more eager than default. Drop the override and bump max_delay.

**Step 1:** Update `app/config.py`:

```python
# Was: engine_endpointing_max_delay: float = 2.5
engine_endpointing_max_delay: float = 6.0

# Was: interview_turn_detector_unlikely_threshold: float | None = 0.15
interview_turn_detector_unlikely_threshold: float | None = None
```

`engine_endpointing_min_delay` stays at 1.0.

**Step 2:** Update `.env.example`:

```
grep -n "ENGINE_ENDPOINTING_MAX_DELAY\|INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD" backend/nexus/.env.example
```

For each:
- `ENGINE_ENDPOINTING_MAX_DELAY=6.0` (update value).
- `INTERVIEW_TURN_DETECTOR_UNLIKELY_THRESHOLD=` (leave empty, or comment out the line; the None default applies).

**Step 3:** Update `tests/test_engine_settings.py`. Search for asserted defaults:

```
grep -n "engine_endpointing_max_delay\|interview_turn_detector_unlikely_threshold" backend/nexus/tests/test_engine_settings.py
```

Update assertions:
- `engine_endpointing_max_delay == 2.5` → `engine_endpointing_max_delay == 6.0`
- `interview_turn_detector_unlikely_threshold == 0.15` → `interview_turn_detector_unlikely_threshold is None`

**Step 4:** Verify `app/ai/realtime.py::build_turn_detector` handles None correctly. The existing code:

```python
threshold = ai_config.interview_turn_detector_unlikely_threshold
if threshold is None:
    return MultilingualModel()
return MultilingualModel(unlikely_threshold=threshold)
```

This is correct as-is. No code change needed.

**Step 5:** Run tests:

```
docker compose run --rm nexus pytest tests/test_engine_settings.py -v
docker compose run --rm nexus pytest tests/interview_engine/ -v --ignore=tests/interview_engine/prompt_quality
```

Expected: all PASS (engine suite is unaffected by config-default changes).

**Step 6:** Commit:

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example backend/nexus/tests/test_engine_settings.py
git commit -m "fix(engine): tune endpointing for thinking pauses (Bug F)

- engine_endpointing_max_delay: 2.5 → 6.0 seconds. Candidates regularly
  pause 5-15s on non-trivial questions; the 2.5s cap was firing turn-end
  mid-thought. Session 09e8fc33-... showed eou_delay p95 of 5.5s and
  candidate pauses up to 22s.
- interview_turn_detector_unlikely_threshold: 0.15 → None. The override
  was more eager than the language-tuned default (~0.3-0.5). Letting
  the multilingual EOU model use its tuned threshold is more patient
  AND more accurate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task P2.3: Rewrite `clarify.txt` (rephrase + safe examples)

**Files:**
- Modify: `backend/nexus/prompts/v1/engine/speaker/clarify.txt` (REWRITE)
- Modify: `backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py`

**Background:** In session 09e8fc33, candidate said *"explain that question to me"* (no specific term). Current scaffold says "Pick the ONE most relevant term to explain" — Speaker hallucinated *"validators"*, a rubric anchor, and explained it. **This is a rubric leak** the anti-leak design forbids.

**Step 1:** Add the failing test first (TDD). In `tests/interview_engine/speaker/test_speaker_prompt_loadable.py`, append:

```python
def test_clarify_handles_generic_confusion_via_rephrase():
    """When candidate is generically confused (no specific term in their
    utterance), the clarify scaffold must rephrase the whole question, NOT
    pick a rubric term and explain it. Phase 2 anti-leak guard.
    """
    from app.ai.prompts import prompt_loader
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/clarify",
    )
    # Path B (generic confusion) must be explicit.
    assert "rephrase" in body.lower() or "rephrasing" in body.lower(), \
        "clarify.txt must instruct rephrasing for generic confusion"
    # The legacy unconditional pick-a-term phrase must be gone.
    assert "Pick the ONE most relevant term to explain" not in body, \
        "clarify.txt's old pick-a-term-on-your-own logic was the rubric-leak vector"
```

**Step 2:** Run the test to confirm it fails:

```
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py::test_clarify_handles_generic_confusion_via_rephrase -v
```

Expected: FAIL.

**Step 3:** Rewrite `prompts/v1/engine/speaker/clarify.txt` with the full content below. This replaces the current file body (do not append — rewrite end-to-end):

```
TASK
The candidate signaled they don't fully understand the active question.
They might have asked about a specific term, or they might just be
generically confused. Pick the right path and respond like a real
interviewer — rephrase, give a generic example if it helps, never
lecture on rubric vocabulary they didn't ask about.

DECISION
Look at the candidate's last utterance:

  Path A — SPECIFIC TERM ASKED
    The candidate said something like "what does X mean" / "what's a Y"
    where X or Y is a literal term FROM the active question.
    Behavior: Briefly explain THAT term in plain English, then restate
    the question.

  Path B — GENERIC CONFUSION
    The candidate said something like "explain the question", "I don't
    understand", "can you rephrase that", or trailed off without naming
    a specific term.
    Behavior: REPHRASE the whole question in plainer English. Use
    shorter sentences. Drop jargon. Optionally give a generic
    non-rubric example to anchor understanding.

OUTPUT
- 1-3 short sentences total.
- For Path A: definition + restate.
- For Path B: brief acknowledgment ("Sure, let me rephrase") + plainer
  rewording + optional generic example. Do NOT explain a term they
  didn't ask about.

LOAD-BEARING RULE (anti-leak)
If the candidate did NOT name a specific term, you must NOT pick one
from {bank_text} on your own initiative and explain it. That leaks
rubric vocabulary the candidate hadn't engaged with. When in doubt,
default to Path B (rephrase).

WHAT NOT TO EXPLAIN
- Anchors / scoring criteria / rubric.
- What categories of answer would score well.
- Hints about the right approach.

EXAMPLES (illustrative — compose from actual inputs; do not copy)

EXAMPLE A1 — specific term asked
Input:
"""
last_candidate_utterance: "What do you mean by validators?"
bank_text: "Walk me through how you've configured JIRA workflows, including validators."
"""
Output:
By validators I mean checks that block a transition unless certain
fields are filled in. With that in mind, walk me through how you've
configured a JIRA workflow.

EXAMPLE A2 — specific term asked
Input:
"""
last_candidate_utterance: "Sorry, what's a partial index?"
bank_text: "Have you worked with partial indexes in Postgres?"
"""
Output:
A partial index is one that only covers rows matching a condition —
useful when most queries hit a small slice of a big table. Have you
worked with one?

EXAMPLE B1 — generic confusion, rephrase
Input:
"""
last_candidate_utterance: "Can you explain that question to me?"
bank_text: "Walk me through how you'd design or refactor a Jira project to fit a client workflow."
"""
Output:
Sure, let me put it differently. Imagine a new client comes in with
their own way of working — how would you set up a Jira project to
match what they need?

EXAMPLE B2 — generic confusion, rephrase + generic example
Input:
"""
last_candidate_utterance: "I didn't really fully understand the question."
bank_text: "Talk through how you'd design a rate limiter for a public API."
"""
Output:
No problem, let me try again. Say a public API needs to keep any single
caller from overwhelming it — how would you build the system that
decides who gets through and who has to wait?

EXAMPLE B3 — generic confusion, rephrase only
Input:
"""
last_candidate_utterance: "Can you rephrase that?"
bank_text: "Describe a time you scaled a Postgres database from a few thousand to a few million rows."
"""
Output:
Of course. Tell me about a time you took a Postgres database from
small — a few thousand rows — and grew it to handle millions.

REMINDER
Choose Path A only when the candidate literally named a term. Default
to Path B (rephrase) for everything else. Never lecture on rubric
vocabulary they didn't ask about. Compose from inputs; do not paste
an example.
```

**Step 4:** Re-run the loadable test:

```
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py -v
```

Expected: all PASS, including the new `test_clarify_handles_generic_confusion_via_rephrase`.

**Step 5:** Run full engine suite:

```
docker compose run --rm nexus pytest tests/interview_engine/ -v --ignore=tests/interview_engine/prompt_quality
```

Expected: all PASS.

**Step 6:** Commit:

```bash
git add backend/nexus/prompts/v1/engine/speaker/clarify.txt \
        backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py
git commit -m "fix(prompts): clarify rephrases on generic confusion, no rubric leak

Phase 1's clarify.txt said 'Pick the ONE most relevant term to explain'.
When the candidate has no specific term in mind ('explain that question',
'I don't understand'), the model picked a rubric anchor (validators) and
explained it — a rubric leak observed in session 09e8fc33-... twice.

The rewrite splits clarify into two paths:
- Path A (specific term): explain the term, restate the question.
- Path B (generic confusion): rephrase the whole question in plainer
  English, optionally with a generic non-rubric example. Never pick a
  term the candidate didn't ask about.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task P2.4: Rewrite `deliver_probe.txt` (de-robot)

**Files:**
- Modify: `backend/nexus/prompts/v1/engine/speaker/deliver_probe.txt` (REWRITE)
- Modify: `backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py`

**Background:** Session 09e8fc33 had every probe starting with *"You mentioned X..."*. Real interviewers transition naturally: *"And how would you handle [next dimension]?"* — recaps belong only when there's a specific terminology hook.

**Step 1:** Add the failing test:

```python
def test_deliver_probe_default_no_recap():
    """The probe scaffold must default to no-recap (just ask the next
    question). Echoing the candidate's prior utterance must be the
    EXCEPTION (only when there's a specific terminology hook), not the
    default."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/deliver_probe",
    )
    body_lower = body.lower()
    # The new prompt must explicitly say "default" + "no recap" or equivalent.
    assert "default" in body_lower
    # Recap/echo must be conditional, not unconditional.
    assert "rare" in body_lower or "exception" in body_lower or "only" in body_lower, \
        "deliver_probe.txt must mark echo as a rare/exception path, not the default"
```

**Step 2:** Run to confirm failure:

```
docker compose run --rm nexus pytest tests/interview_engine/speaker/test_speaker_prompt_loadable.py::test_deliver_probe_default_no_recap -v
```

Expected: FAIL.

**Step 3:** Rewrite `prompts/v1/engine/speaker/deliver_probe.txt`:

```
TASK
The candidate's last answer was on-topic but didn't fully cover the
question. Deliver the probe from {bank_text} naturally — the way a
real interviewer would transition to a follow-up. Don't recap what
they just said unless it adds clarity.

OUTPUT
- 1 short sentence asking the probe. Often that's the whole turn.
- Optionally 1 brief transition phrase ("And...", "Going further...",
  "Let's stay with that...") + the probe.
- Rephrase bank_text into spoken English; don't read verbatim.

DEFAULT BEHAVIOR — NO RECAP
The default is: just ask the probe. Don't reference what the candidate
just said. Real interviewers don't say "You mentioned X..." every turn —
they trust the candidate remembers what they said.

ECHO PATH (RARE — use only when justified)
Echo what the candidate said ONLY when:
  (a) the candidate used a specific term that the probe directly
      extends ("on those validators specifically — what does the
      user see when one fails?"), AND
  (b) without the echo, the probe would feel disconnected.

If neither is true, default to no recap.

WHAT NOT TO DO
- Don't paraphrase the candidate's full answer back to them ("So you
  mentioned validators, conditions, and post-functions...").
- Don't quote them verbatim.
- Don't open with "You mentioned" / "You're talking about" / "You said"
  more than once every several turns.

EXAMPLES (illustrative — compose; do not copy)

EXAMPLE 1 — default, no recap
Input:
"""
last_candidate_utterance: "I usually start by mapping the client's process and identifying issue types."
bank_text: "How would you migrate existing projects onto that new workflow with minimal disruption?"
"""
Output:
And how would you migrate existing projects onto that new workflow
with minimal disruption?

EXAMPLE 2 — default, no recap
Input:
"""
last_candidate_utterance: "I'd add notifications and basic error handling."
bank_text: "What would you log and how would you monitor this rule in production?"
"""
Output:
What would you log, and how would you monitor this rule in production?

EXAMPLE 3 — default, no recap
Input:
"""
last_candidate_utterance: "I lean on Postgres' EXPLAIN to understand query plans."
bank_text: "Have you worked with partial indexes? In what scenario?"
"""
Output:
Have you worked with partial indexes? If so, in what scenario?

EXAMPLE 4 — echo path (rare, justified by specific terminology)
Input:
"""
last_candidate_utterance: "We had validators on the In-Review-to-Done transition."
bank_text: "What does the user see when a validator fails?"
"""
Output:
On those validators — what does the user actually see when one fails?

EXAMPLE 5 — default, no recap (even though candidate gave a long answer)
Input:
"""
last_candidate_utterance: "I'd do staging tests, then a pilot rollout, then batches with rollback checkpoints."
bank_text: "Describe the largest production environment you've administered end to end."
"""
Output:
Now describe the largest production environment you've administered
end to end.

REMINDER
Default = no recap, just ask. Use the echo path only when there's a
specific term the probe directly extends. Don't open every probe with
"You mentioned..." — that's the robotic pattern this scaffold replaces.
```

**Step 4:** Run tests:

```
docker compose run --rm nexus pytest tests/interview_engine/speaker/ -v
docker compose run --rm nexus pytest tests/interview_engine/ -v --ignore=tests/interview_engine/prompt_quality
```

Expected: all PASS, including the new `test_deliver_probe_default_no_recap`.

**Step 5:** Commit:

```bash
git add backend/nexus/prompts/v1/engine/speaker/deliver_probe.txt \
        backend/nexus/tests/interview_engine/speaker/test_speaker_prompt_loadable.py
git commit -m "fix(prompts): deliver_probe defaults to no recap (de-robot)

Session 09e8fc33-... had every probe starting with 'You mentioned X...' —
the LLM over-applied Phase 1's 'reference what they said for continuity'
rule on every turn. Real interviewers don't recap; they just ask the
next question.

The rewrite makes 'no recap' the default and 'echo what they said' the
rare exception, used only when the candidate's specific term directly
extends the probe.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task P2.5: Judge prompt §4 — clarify accepts generic confusion

**Files:**
- Modify: `backend/nexus/prompts/v1/engine/judge.system.txt` (small section addition)

**Background:** The Judge in session 09e8fc33 correctly classified *"explain that question"* as `clarify`. The current Judge prompt §4 says "`clarify` is RESERVED for legitimate 'what does X mean?' questions about a term that appears in the active question text." That's slightly tighter than the actual desired routing. Update to acknowledge two sub-cases.

**Step 1:** Read the current `prompts/v1/engine/judge.system.txt` §4 section. Locate the paragraph:

```
`clarify` is RESERVED for legitimate "what does X mean?" questions about a
term that appears in the active question text. A greeting or social opener
is NOT a clarify turn — it's a redirect.
```

**Step 2:** Replace that paragraph with:

```
`clarify` covers TWO sub-cases:

  1. The candidate asked about a specific term in the active question
     ("what do you mean by validators?", "what's a partial index?").
  2. The candidate is generically confused ("I don't understand", "can
     you rephrase", "explain the question").

In BOTH cases the Speaker handles the response — for case 2 it rephrases
the whole question in plainer English rather than picking a term to
explain. You only classify; the Speaker scaffold takes care of the rest.

A greeting or social opener is NOT clarify — it's redirect (§4 above).
A candidate stalling or fishing for hints ("can you give me an example?",
"what are you looking for?") is also redirect, not clarify.
```

**Step 3:** Run prompt-loadable tests:

```
docker compose run --rm nexus pytest tests/interview_engine/judge/test_judge_prompt_loadable.py -v
```

Expected: all PASS (no test asserts on the legacy paragraph wording).

Run full engine suite:

```
docker compose run --rm nexus pytest tests/interview_engine/ -v --ignore=tests/interview_engine/prompt_quality
```

Expected: all PASS.

**Step 4:** Commit:

```bash
git add backend/nexus/prompts/v1/engine/judge.system.txt
git commit -m "fix(prompts): Judge clarify accepts generic confusion sub-case

Pairs with the rewritten clarify.txt (Task P2.3). The Judge already
routed 'explain the question' to clarify in session 09e8fc33-...; this
codifies the two-sub-case shape so the prompt and Speaker scaffold are
in sync.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task P2.6: Manual session re-run (user-driven)

After Tasks P2.1–P2.5 are committed, the user runs a fresh session against
the same Jira-admin job and verifies:

| Behavior | Expected |
|---|---|
| Speaker streams a normal response on every turn | No `speaker.output.empty` audit events |
| Candidate pauses 4-6s mid-thought | Agent waits; no premature turn-end fires |
| Candidate says *"explain the question"* / *"I don't understand"* | Speaker rephrases the whole question; doesn't lecture on validators or any rubric anchor |
| Candidate gives a partial answer; Speaker probes | Probe asks the next question without *"You mentioned..."* |

If any behavior regresses, capture the new envelope at `backend/nexus/engine-events/<session_id>.json` and triage.

---

## Self-review (post-write check)

- **Spec coverage:** all four issues from the spec map to a task.
  - Bug E (empty Speaker) → P2.1
  - Bug F (EOU mid-sentence) → P2.2
  - clarify rubric leak → P2.3 + P2.5
  - Probe robotic recap → P2.4
- **Placeholder scan:** no TBDs, no "implement later", no vague requirements.
- **Type consistency:** the new `test_clarify_handles_generic_confusion_via_rephrase` and `test_deliver_probe_default_no_recap` use `prompt_loader.load_pair` consistent with Task 12 of Phase 1.
- **No undefined references:** all file paths and exact text edits are spelled out inline.
