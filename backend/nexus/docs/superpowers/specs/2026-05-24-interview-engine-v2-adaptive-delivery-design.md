# Interview Engine v2 — Adaptive Spoken Delivery + Floor-Aware Clarify

**Status:** Approved (design) — 2026-05-24
**Author:** engine work (Claude + Ishant)
**Supersedes/extends:** the M5 brain + naturalness-triage architecture (see
`2026-05-24-interview-engine-v2-naturalness-triage-design.md`).
**Scope:** the live engine only (`app/modules/interview_engine_v2/`). The question-bank
generator (`app/modules/question_bank/`) is explicitly **out of scope**.

---

## 1. Motivation

Live talk-test `4137c1bb` (a qualified candidate, full 8-question bank, 15.2 min) ran clean
end-to-end (0 fallbacks, 0 timeouts, 0 policy violations, 0 leaks), but surfaced three quality
problems:

1. **HIGH — CLARIFY drifts to the wrong question after a PROBE.** When a probe (bank follow-up)
   is the line on the floor and the candidate asks "what do you mean?", the brain clarifies/re-poses
   its `active_question_id` (the *main* question), not the probe actually spoken. Two live instances:
   - `[47]` probe "retry/backoff on 5xx" → `[48]` "what do you mean?" → `[50]` brain re-explained the
     **REST rate-limit main question** (wrong target).
   - `[62]` probe "page through large result sets" → `[63]` "what do you mean by large result sets?" →
     `[67]` brain clarified "large result sets" **but re-posed the Java/Python/Ruby JSON-transform
     question** → candidate `[70]`: *"you are asking me two different questions at the same time."*
   Root cause: the brain tracks the main-question pointer but has **no notion of "the exact line
   last asked"** (which may be a probe). The mouth tracks that (`last_question`), but the brain
   doesn't see it.

2. **MED — heavy clarification load.** The candidate asked for **setup** ~10 times ("are these
   tickets from Jira?", "what kind of rate limit?", "what are partial failures?", "what do you mean
   by large result sets?"). The engine handled every one correctly, but it stretched 8 questions to
   15 minutes. Most clarifications were requests for *orienting setup*, not "I couldn't parse your
   sentence."

3. **NEW — verbatim questions are hard for an Indian-English candidate.** Long written scenario
   questions, read aloud verbatim by TTS, are hard to parse. The interviewer should phrase questions
   the way a person speaks them.

These converge on one architectural change: **separate the question's *content* (what is asked,
graded, recorded) from its *spoken rendering* (how it's said).**

## 2. The invariant being relaxed (D2) and why it's safe

The v2 mouth was built on **D2 — "deliver the bank question AS WRITTEN."** That invariant does three
jobs: (1) **no-leak** (the mouth never sees the rubric, so a faithful question can't hint the
answer); (2) **fairness/consistency** (every candidate is graded on the same calibrated question);
(3) **auditability** (the recruiter reviews the exact question asked). This design **relaxes verbatim
delivery at the rendering layer only**, and preserves all three jobs:

- **No-leak preserved:** the mouth still never sees the rubric (structural). It may *rephrase*, never
  *invent* — the rendering contract + eval forbid added factual/solution content. The only new
  content, `spoken_setup`, is **brain-authored** (rubric-aware) and **policy-scrubbed** for leaks.
- **Fairness preserved:** every candidate still gets the same *semantic* question (same canonical
  spec, same grading). Only surface phrasing varies — standard for a voice interviewer.
- **Auditability preserved and improved:** the transcript now records **both** the canonical question
  (what was designed) and the **actual spoken rendering** (what the candidate heard).

This reverses the earlier "bank-text-as-is" rule from `[[feedback_speaker_constraints]]`, by explicit
decision, for spoken clarity. The companion rules from that memory — **anti-leak stays strict** and
**fix via prompt engineering only** (no brittle runtime text-matching) — are retained.

## 3. Architecture

```
brain (ControlPlane)                 mouth (ConversationPlane)
─────────────────────                ─────────────────────────
picks bank question (verbatim, by ref)
optionally authors spoken_setup  ──► Directive{ say (canonical, verbatim),
  (benign, rubric-aware, no-leak)              spoken_setup? }      ──► renders [setup?] + question
records _floor (last asked line)                                        as clear spoken Indian English
grades against ACTIVE QUESTION rubric                                   (preserves every specific term,
clarify/repeat/confirm target _floor                                     no new content, no 2nd question)
```

- **Brain owns CONTENT:** question selection (verbatim, unchanged) + an optional one-line
  `spoken_setup`. Grades the canonical question. Tracks the floor.
- **Mouth owns RENDERING:** turns `[spoken_setup?] + say` into natural spoken Indian English. Never
  adds content; preserves every specific term.
- **Canonical bank question** is unchanged, graded, and recorded.

## 4. Component changes

### 4.1 Brain — `spoken_setup` (content)

- `brain/decision.py` `BrainDecision`: add
  `spoken_setup: str | None = Field(default=None, ...)` — an optional benign orienting clause,
  authored for **`question_kind == "technical_scenario"`** asks (the abstract ones); leave null for
  `experience_check` / `compliance_binary` / `behavioral` and for any question already self-contained.
  SPOKEN words only; never rubric/evidence/solution. (Strict-mode safe: optional with default, like
  the other composed fields.)
- `brain/policy.py`: extend the no-leak scrub to `spoken_setup` — if `_leaks(spoken_setup)` fires,
  drop it (set to None) and record a `no_leak` violation; otherwise pass. The directive ctor's
  belt-and-suspenders validation also covers it.
- `brain/service.py` `_build_directive`:
  - On `advance`/`ask`, attach `spoken_setup` to the Directive **only when** the resolved target
    equals the brain's `bank_question_id` (i.e. no mandatory-first override). On override, drop it
    (`spoken_setup=None`) — best-effort setup, never mismatched to a different question.
  - On composed acts (clarify/redirect/…), `spoken_setup` is unused (those already compose `say`).
- `prompts/v3/engine/brain.system.txt`: instruct *when* to author `spoken_setup` — an abstract
  scenario question that benefits from orientation gets ONE plain situational clause (the
  "what/where", e.g. "Say tickets come in from a system like Jira"), **never** the "how/solution".
  Reuse the existing F1 clarify-setup discipline. Most questions need no setup → leave it null.

### 4.2 Mouth — spoken rendering contract (rendering)

- `directive.py` `Directive`: add `spoken_setup: str | None = None`.
- `mouth/input_builder.py` `build_mouth_messages`: when `spoken_setup` is present, surface it in the
  dynamic suffix as a lead-in instruction ("Open with this orienting line, then the question").
- `mouth/service.py` `build_turn_messages`: thread `spoken_setup` through.
- `prompts/v3/engine/mouth/*` act-blocks (ask, ack_advance, probe, clarify, redirect, confirm, hint,
  answer_meta): replace "deliver the question in `say` **AS WRITTEN**" with a **SPOKEN RENDERING
  contract**:
  > Render the line in `say` as you would *say* it aloud to a candidate in natural Indian English:
  > short sentences, one idea at a time, plain words. **Preserve every specific term exactly**
  > (product names, protocols, formats, the precise ask). Do **not** add any new fact, example, or
  > hint, do **not** change the difficulty, and ask **exactly one** question. If a `SPOKEN SETUP`
  > line is provided, say it first (one short clause), then the question.
  REPEAT replays the cached canonical question through the same rendering contract (so a re-asked
  question is voiced just as clearly). The double-opener `YOU ALREADY SAID` block (prior fix) is
  unchanged.
- `prompts/v3/engine/mouth/_persona.txt`: strengthen the Indian-English interviewer register —
  one idea per sentence, concrete and plain phrasing, define jargon in everyday terms when natural,
  warm and conversational. (Persona remains the byte-stable cache prefix.)

### 4.3 Floor State — fixes the HIGH clarify-drift bug

- `brain/service.py` `ControlPlane`: add `_floor: FloorRef | None` where
  `FloorRef = {canonical_text: str, kind: "main" | "probe" | "clarify", thread_question_id: str |
  None}`. Set it whenever a question-bearing directive is produced:
  - `advance`/`ask` → `{text=bank question text, kind="main", thread=target_id}`
  - `probe` → `{text=follow_up text, kind="probe", thread=active_question_id}`
  - `clarify`/`redirect` re-pose → `{text=composed say, kind="clarify", thread=active_question_id}`
  Seeded by `opener()` to the first ASK.
- `brain/input_builder.py` `build_brain_messages`: add an **`# ON THE FLOOR`** block —
  *"the exact question you last asked aloud; a clarify / repeat / confirm must address THIS line.
  Grade this turn's answer against the ACTIVE QUESTION rubric below."* When floor == active main
  question, they coincide (one line, no behavior change).
- Effect: a "what do you mean?" after a probe clarifies **the probe**; grading still uses the thread
  rubric. Kills the `[50]` mis-target and the `[67]/[70]` two-questions confusion.
- Note: the floor is the brain's **canonical** intent (not the mouth's exact spoken words) — correct
  for clarify, because the brain re-poses the canonical line and the mouth re-renders it.

### 4.4 Observability

- `agent.py` `_MouthAgent.llm_node`: accumulate the streamed mouth output and record the **spoken
  rendering** into `_result_transcript` (today only the canonical `directive.say` is stored). Record
  both: canonical in the audit/turn record, spoken in the transcript the recruiter reads. Closes the
  gap that hid the double-opener from log analysis.
- Cache instrumentation: at the brain call site (`brain/service.py::_call_brain`) and the triage call
  site (`triage/service.py`), log `prompt_tokens` / `prompt_cached_tokens` from the response `usage`
  (structlog + optional audit event), so brain/triage prompt-cache effectiveness is finally
  measurable. (Mouth caching is a no-op by design — its prompt is < 1024 tokens, below OpenAI's cache
  threshold — documented, not changed.)

## 5. Data flow (one scenario-ask turn)

1. Candidate finishes → triage ∥ brain launch (unchanged).
2. Brain decides `advance`, `bank_question_id=Q`, `spoken_setup="Say tickets arrive from a system
   like Jira."` (benign, no-leak-scrubbed). `_resolve_advance_target` returns Q (no override) →
   setup kept. `_floor = {text=Q.text, kind="main", thread=Q}`.
3. Directive `{say=Q.text (verbatim), spoken_setup=...}` staged.
4. Mouth `llm_node` renders: *"Okay — say tickets come in from something like Jira. How would you set
   up the recipe so the AI's decision routes each ticket reliably?"* (every term preserved; no
   solution). Spoken form recorded.
5. Next turn, if the candidate asks "what do you mean by routing?", `# ON THE FLOOR` carries Q's text
   → clarify targets Q (not a different question).

## 6. Testing

Contract + **offline LLM-graded evals** (`@prompt_quality`, real API) — no runtime fidelity check
(honors no-regex; the canonical question is what's graded, so phrasing drift can't corrupt scoring):

- **Mouth rendering fidelity** (`test_mouth_evals.py`): given a canonical `say` (and optional
  `spoken_setup`), the rendering (a) contains every specific term — *exact substring* assertion, not
  regex-intent; (b) adds no solution component (no-leak token check); (c) asks exactly one question;
  (d) is not longer than the source. LLM-graded for "natural spoken Indian English / one idea per
  sentence".
- **Brain `spoken_setup` no-leak** (`test_brain_evals.py`): on a scenario ask, any `spoken_setup` is
  benign (no `FORBIDDEN_RUBRIC_TOKENS`, no solution components like "retry/backoff/pagination" for the
  rate-limit question).
- **Clarify-on-floor** (`test_brain_evals.py`): with a probe on the floor, a "what do you mean?"
  produces a clarify whose `say` addresses the **probe** line, not the parent/next question.

Unit tests:
- floor-state tracking across advance/probe/clarify/opener;
- `spoken_setup` plumbing (decision → directive → mouth messages) and **override-discard** (resolved
  target ≠ `bank_question_id` ⇒ `spoken_setup` dropped);
- policy no-leak scrub on `spoken_setup`;
- spoken-rendering capture into `_result_transcript`.

Regression gate: full v2 unit + boundary + runtime suite green; ruff clean; mouth/brain prompt-evals
green (incl. the prior double-opener guards).

## 7. Scope / non-goals

- **Bank-gen unchanged** — questions stay as authored; clarity comes from rendering + brain setup.
- **No runtime fidelity check** — evals only.
- **Triage logic unchanged** — only cache logging added.
- **Latency:** `spoken_setup` is one short clause; the brain output is already async/masked by the
  triage filler. No critical-path regression expected; verify in the talk-test
  (`[[feedback_quality_before_latency]]` — quality first, latency deferred).

## 8. Risks & mitigations

- **Meaning drift in rendering** → contract + fidelity eval (term-preservation, no-2nd-question);
  canonical question is graded, so drift can't corrupt scoring.
- **Mouth leak via rephrase** → mouth never sees the rubric; contract forbids new content; eval
  checks no-solution. Setup is brain-authored + policy-scrubbed.
- **Setup/override mismatch** → setup dropped on mandatory-first override (best-effort).
- **Unobservable by log alone (bridge/rendering naturalness)** → spoken-rendering now recorded; final
  judgment still needs the user's ear in a talk-test (`[[feedback_manual_agent_testing]]`).

## 9. Acceptance

- Clarify after a probe addresses the probe (no "two questions" confusion).
- Scenario questions delivered as natural spoken Indian English; specific terms intact; no leak.
- Proactive `spoken_setup` reduces clarification requests vs `4137c1bb`.
- Spoken rendering recorded in the transcript; brain/triage cache hit-rate visible in logs.
- All evals + unit/boundary/runtime tests green. Final gate: the user's live talk-test.
