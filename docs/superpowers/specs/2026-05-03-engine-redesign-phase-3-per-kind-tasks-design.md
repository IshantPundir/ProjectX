# Engine Redesign — Phase 3: Per-kind Task Subclasses

**Status:** Draft for user review · **Date:** 2026-05-03 · **Phase:** 3 of 6 (per-kind task subclasses)

**Parent spec:** [`2026-05-02-interview-engine-redesign-overview-design.md`](./2026-05-02-interview-engine-redesign-overview-design.md)

**Prior phase:** [`2026-05-03-engine-redesign-phase-2-controller-cutover-design.md`](./2026-05-03-engine-redesign-phase-2-controller-cutover-design.md) (✅ shipped)

---

## Summary

Phase 2 retired the procedural `state_machine.py` + monolithic `InterviewerAgent` in favor
of an `InterviewController` (outer Agent) that dispatches a sequential chain of
`QuestionTask` instances. It shipped one concrete task subclass — `TechnicalDepthTask` — and
left the factory pinned to it.

Phase 3 is **strictly additive**:

1. Adds two new concrete task subclasses: `BehavioralStarTask` (max_probes=2) and
   `ComplianceBinaryTask` (max_probes=0, 60s hard cap).
2. Extracts the inline factory in `tasks/__init__.py` to a dedicated `tasks/factory.py` and
   teaches it to route on a new `question_kind` field on `QuestionConfig`.
3. Ships two new task prompt files — `prompts/v1/interview/task_behavioral.txt` and
   `prompts/v1/interview/task_compliance_binary.txt` — drafted inline below for
   senior-reviewer fairness signoff (per overview Decision #18).
4. Adds unit, integration, and prompt_quality test coverage for the new surface, including
   100%-branch coverage on the new task files and the factory.

Phase 3 changes **no behavior for existing interviews**: every question still defaults to
`question_kind="technical_depth"` until Phase 4's bank-generator update starts emitting
real values. The new task classes are reachable in production code paths through the
factory, but only fire when the `question_kind` field is set to a non-default value, which
no production data does until Phase 4 ships.

## Product framing (load-bearing)

This spec describes one phase of building a **hyper-realistic interviewer simulation for
candidate screening** at enterprise scale. Two implications shape every decision below:

1. **In-session contract = realistic interviewer behavior + raw observations.** The agent
   speaks naturally, listens carefully, decides whether to probe, and records WHAT the
   candidate said as structural observations. It does **not** decide whether the answer
   was good — that's the post-session report module's job (out of scope for this arc).
2. **Enterprise fairness discipline applies to every per-kind prompt.** Per CLAUDE.md
   (EEOC, AIVIA, Decision #18), the new prompt files require a deliberate fairness pass.
   The fairness checklists below are the gate; the prompt bodies are drafted inline so
   that pass happens at spec-review time, not buried in an implementation diff.

## Locked decisions (this phase's brainstorm)

The 21 cross-arc decisions in the overview spec stay locked. Phase 3's brainstorm pinned
the §12 Phase-3 open questions as follows:

| # | Question | Decision |
|---|---|---|
| Q1 | How does the factory route in production for Phase 3, given `question_kind` ships in Phase 4? | **(a)** Add `question_kind` field to `QuestionConfig` (in-memory, default `"technical_depth"`); factory routes on it. Behavior unchanged in real interviews until Phase 4's bank-gen update fills real values. Phase 4 only adds the DB column + bank-gen change — no second factory edit. |
| Q2 | What about `OpenCultureTask`? | **(a)** Omit. Factory routes the `open_culture` enum value → `TechnicalDepthTask` with a one-line documentation comment. Real culture-fit screening is its own future brainstorm with its own fairness review. |
| Q3 | How does `BehavioralStarTask` detect missing STAR components? | **(c)** Hybrid. LLM reports coverage via `record_behavioral_answer(situation, task, action, result)` (each nullable). Task code counts nulls and enforces probe budget — same division of labor as `TechnicalDepthTask` (LLM judges, task code enforces). |
| Q4 | Evidence-key matching on `record_answer_assessment`? | **(a)** Defer to Phase 3D. Phase 3 stays strictly additive and the new task kinds don't have evidence-keys at all. |
| Q5 | Probe-budget edge cases. | All four cases accepted as a policy package: <br>**A** First-answer-excellent → complete (don't probe). <br>**B** First-answer non-answer → complete (don't probe non-answers; pressuring candidates is unfair). <br>**C** Probe-then-non-answer → complete (don't probe again). <br>**D** Compliance ambiguous → ONE fairness clarification via new `request_compliance_clarification()` tool, single-shot, doesn't count as a probe. |
| Q6 | Where do the new prompt bodies get drafted, and how closely do they mirror `task_technical_depth.txt`? | **A=(a) + B=(a)**. Drafted inline in this spec, strict mirror of the existing prompt's section structure (`# Your job` / `# The question` / `# How you ask` / `# Tools` / `# Decision flow` / `# What you NEVER do`). |

## 1 — Architecture

### 1.1 Module layout

**New files:**

```
backend/nexus/app/modules/interview_engine/tasks/
├── factory.py            ; NEW — extracted from inline tasks/__init__.py;
│                            holds _ROUTING_TABLE + build_task_for() +
│                            effective_budget_seconds_for()
├── behavioral.py         ; NEW — BehavioralStarTask
└── compliance_binary.py  ; NEW — ComplianceBinaryTask
```

**Touched (existing) files:**

- `tasks/__init__.py` — re-exports the new classes alongside `TechnicalDepthTask`;
  `build_task_for` re-imported from `factory.py` to preserve the existing public API.
- `tasks/base.py` — extends `TaskResult.kind` to a 3-value Literal; adds optional
  observation fields for behavioral and compliance kinds (all default `None`,
  preserving existing TechnicalDepthTask serialization).
- `app/modules/interview_runtime/schemas.py` — adds `question_kind` field to
  `QuestionConfig` (in-memory only, default `"technical_depth"`; no DB migration
  in Phase 3).
- `app/modules/interview_engine/controller.py` — single touch: replaces the inline
  watchdog computation in `_dispatch_task` with a call to
  `effective_budget_seconds_for(q)`. Everything else in `controller.py` stays
  untouched.

**Untouched (Phase 2 surface preserved):** `agent.py`, `event_log/*`, `outcome_close.py`,
`idle_nudge.py`, `budget.py`, `spoken_form.py`, `prompts/v1/interview/controller.txt`,
`prompts/v1/interview/task_technical_depth.txt`, `tasks/technical_depth.py`.

### 1.2 Data shapes

**`QuestionConfig` delta** (in-memory only; the DB column lands in Phase 4):

```python
# backend/nexus/app/modules/interview_runtime/schemas.py
class QuestionConfig(BaseModel):
    # ... existing fields unchanged ...
    question_kind: Literal[
        "technical_depth",
        "behavioral_star",
        "compliance_binary",
        "open_culture",
    ] = "technical_depth"
```

**`TaskResult` delta** (additive — existing TechnicalDepthTask serialization unchanged):

```python
# backend/nexus/app/modules/interview_engine/tasks/base.py
class TaskResult(BaseModel):
    question_id: str
    kind: Literal["technical_depth", "behavioral_star", "compliance_binary"]
    # existing technical-depth fields (unchanged)
    tier: Literal["excellent", "strong", "at_bar", "below_bar"] | None = None
    evidence_keys: list[str] = []
    non_answer: bool = False
    signals_lacked: list[str] = []
    knockout: bool = False
    knockout_reason: str | None = None
    forced: bool = False
    forced_reason: Literal["task_timeout"] | None = None
    probes_fired: int = 0
    # NEW Phase 3 — behavioral observation fields (None for non-behavioral)
    star_components: dict[
        Literal["situation", "task", "action", "result"], str | None
    ] | None = None
    # NEW Phase 3 — compliance observation fields (None for non-compliance)
    compliance_confirmed: bool | None = None
    compliance_reason_or_example: str | None = None
    compliance_clarification_used: bool = False
```

**Observation-only contract:** new fields record WHAT the candidate said. The post-session
report module reads them + the full transcript and decides scoring/tiering.
TechnicalDepthTask's `tier` field is preserved as a tactical probe-trigger (its existing
purpose); the new kinds use structurally different probe triggers (component completeness
for behavioral, yes/no clarity for compliance) and so don't carry a `tier`.

### 1.3 Factory routing

```python
# backend/nexus/app/modules/interview_engine/tasks/factory.py

_ROUTING_TABLE: dict[str, type[QuestionTask]] = {
    "technical_depth":   TechnicalDepthTask,
    "behavioral_star":   BehavioralStarTask,
    "compliance_binary": ComplianceBinaryTask,
    "open_culture":      TechnicalDepthTask,  # deferred — see overview spec §1.2
}


def build_task_for(question, *, controller, disqualified_signals):
    """Route a QuestionConfig to its task subclass.

    Falls back to TechnicalDepthTask for any unrecognized question_kind.
    Phase 4 lights this up by adding the question_kind DB column and updating
    the bank-generator to emit non-default values.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    return cls(
        question_config=question,
        controller=controller,
        disqualified_signals=disqualified_signals,
        rubric_internal=_build_rubric_block(question),
    )


def effective_budget_seconds_for(question) -> float:
    """The watchdog timeout the controller should use for this question.

    Returns min(estimated_minutes * 60 + overhead, per-kind hard cap).
    Per-kind hard caps are class attributes on the task subclass; the only
    one set in Phase 3 is ComplianceBinaryTask.budget_seconds_hard_cap = 60.0.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    base = question.estimated_minutes * 60.0 + settings.engine_task_budget_overhead_seconds
    cap = getattr(cls, "budget_seconds_hard_cap", None)
    return min(base, cap) if cap is not None else base


def _build_rubric_block(question) -> str:
    """Assemble the <<INTERNAL_RUBRIC>> string for the task prompt.

    Moved verbatim from the Phase 2 inline implementation in tasks/__init__.py.
    """
    return (
        "<<INTERNAL_RUBRIC>>\n"
        f"Question: {question.text}\n"
        f"Signals: {', '.join(question.signal_values)}\n"
        f"Positive evidence: {'; '.join(question.positive_evidence)}\n"
        f"Red flags: {'; '.join(question.red_flags)}\n"
        f"Excellent: {question.rubric.excellent}\n"
        f"Meets bar: {question.rubric.meets_bar}\n"
        f"Below bar: {question.rubric.below_bar}\n"
        f"Evaluation hint: {question.evaluation_hint}\n"
        "<<END_INTERNAL_RUBRIC>>"
    )
```

The controller's `_dispatch_task` swap is one line:

```python
# backend/nexus/app/modules/interview_engine/controller.py — only Phase 2 file edit
# Before:
watchdog_seconds = q.estimated_minutes * 60.0 + settings.engine_task_budget_overhead_seconds

# After (Phase 3):
from app.modules.interview_engine.tasks.factory import effective_budget_seconds_for
watchdog_seconds = effective_budget_seconds_for(q)
```

`SessionBudget.has_remaining_for` continues to use `q.estimated_minutes` — the per-task
hard cap only tightens the per-task watchdog; we never exceed the session-level budget.

## 2 — `BehavioralStarTask`

### 2.1 Class outline

```python
# backend/nexus/app/modules/interview_engine/tasks/behavioral.py

class BehavioralStarTask(QuestionTask):
    """Per-question task for behavioral STAR questions.

    Lifecycle:
      1. Controller dispatches: result = await task (with sibling watchdog).
      2. AgentTask boots; LLM speaks the question in ≤25-word spoken form.
      3. Candidate answers.
      4. LLM calls record_behavioral_answer with nullable STAR fields.
      5. Tool tells LLM what's missing + how many probes remain.
      6. LLM either calls request_star_probe or complete_question.
      7. After a probe, LLM calls record_behavioral_answer AGAIN (cumulative).
      8. Loop until tool says complete; LLM calls complete_question.
      9. complete_question calls self.complete(result), which resolves the
         controller's `await task` with a TaskResult.
    """

    kind = "behavioral_star"
    max_probes = 2

    def __init__(self, *, question_config, controller, disqualified_signals, rubric_internal):
        super().__init__(...)
        self._probes_fired: int = 0
        # _partial.star_components is initialized to {"situation": None, "task": None,
        # "action": None, "result": None} and updated cumulatively.
```

### 2.2 Tool surface

| Tool | Purpose |
|---|---|
| `record_behavioral_answer(situation, task, action, result)` | Record what was covered. Each parameter is `str \| None`. None means the candidate didn't cover that component. Cumulative across calls (a probe-and-update fills in previously-missing pieces while keeping prior fills). The tool inspects `_partial.star_components` and returns one of four instructions (see Decision flow below). |
| `request_star_probe(missing_component)` | Ask one targeted follow-up. `missing_component ∈ {"situation","task","action","result"}`. LLM picks which missing piece. Tool checks budget; if exhausted, returns `"Probe budget exhausted. Call complete_question instead."` Otherwise increments `self._probes_fired` and returns instructions for a natural follow-up phrasing. |
| `complete_question()` | Terminal — builds `TaskResult` from `_partial.star_components` + `_probes_fired`, calls `self.complete(result)` to resolve the controller's `await task`. |
| `disqualify_knockout(reason)` *(inherited)* | Hard-fail flag for self-disclosed knockouts (e.g., "I have never led a team and never want to" for a tech-lead role). Sets `_partial.knockout = True`; the LLM should still call `complete_question` after. |
| `request_clarification()` *(inherited)* | Candidate asked to repeat the question (different from the AI seeking to disambiguate the candidate's answer). |

### 2.3 Decision flow (driven by the prompt, enforced by tool returns)

1. Speak the question in natural ≤25-word spoken form.
2. Listen to the full answer.
3. Call `record_behavioral_answer(s, t, a, r)` with what was covered (None for missing).
4. Tool returns one of:
   - All 4 covered → *"Complete answer recorded. Call complete_question."*
   - All 4 null (Q5 case B) → *"Non-answer recorded. Do not probe — call complete_question."*
   - Some missing AND probes remain → *"Components missing: [list]. {N} probe(s) remaining. Call request_star_probe with the most important missing component, then listen and call record_behavioral_answer again."*
   - Some missing AND no probes remain → *"Components still missing but probe budget exhausted. Call complete_question."*
5. After a probe, listen, then call `record_behavioral_answer` AGAIN (cumulative update).
6. Eventually call `complete_question()`.

### 2.4 Edge-case policy (Q5 enforcement)

| Case | Tool path | Code enforcement |
|---|---|---|
| A — Excellent first answer (all 4 covered) | Tool says complete | LLM calls `complete_question()` directly |
| B — Non-answer (all 4 null) on first answer | Tool says "do not probe" | LLM calls `complete_question()` directly; even if LLM tried `request_star_probe` here, the prompt forbids it ("# What you NEVER do"). Tool is also guarded: if all components are null and a probe is requested, the tool returns `"Cannot probe a non-answer. Call complete_question."` |
| C — Probe-then-non-answer (probed, follow-up still null) | Tool detects no progress; says complete | Same belt-and-suspenders: tool refuses to budget a 2nd probe if the just-fired probe produced no new coverage |
| D | Not applicable to behavioral | — |

## 3 — `ComplianceBinaryTask`

### 3.1 Class outline

```python
# backend/nexus/app/modules/interview_engine/tasks/compliance_binary.py

class ComplianceBinaryTask(QuestionTask):
    """Per-question task for compliance yes/no questions.

    Lifecycle:
      1. Controller dispatches with watchdog_seconds=60 (effective_budget_seconds_for).
      2. AgentTask boots; LLM speaks the question briefly.
      3. Candidate answers.
      4. Branch on candidate's response shape:
         a. Clear yes/no → record_compliance_attestation(...) (terminal).
         b. Ambiguous → request_compliance_clarification() (single-shot),
            then listen, then record_compliance_attestation(...).
      5. If the recorded answer is a "no" against a hard requirement, the LLM
         pairs disqualify_knockout(reason) before/with the terminal call.
      6. record_compliance_attestation calls self.complete(result).
    """

    kind = "compliance_binary"
    max_probes = 0
    budget_seconds_hard_cap: float = 60.0

    def __init__(self, *, question_config, controller, disqualified_signals, rubric_internal):
        super().__init__(...)
        self._clarification_used: bool = False
```

### 3.2 Tool surface

| Tool | Purpose |
|---|---|
| `record_compliance_attestation(confirmed, reason_or_example)` | Terminal observation. `confirmed: bool`, `reason_or_example: str` (≤20 words; the LLM's distillation of the candidate's brief context — not the verbatim transcript). Builds a `TaskResult` with `compliance_confirmed`, `compliance_reason_or_example`, `compliance_clarification_used`, plus any inherited `knockout` state. Calls `self.complete(result)`. |
| `request_compliance_clarification()` | Single-shot. First call: sets `self._clarification_used = True` and returns *"Ask once, plainly: 'To confirm — yes or no?' Then listen and call record_compliance_attestation."* Second call (if it happens): returns *"Already clarified once. Record record_compliance_attestation now — if still ambiguous, set confirmed=False with reason 'ambiguous response, did not confirm'."* The single-shot guard is enforced in code regardless of prompt compliance. |
| `disqualify_knockout(reason)` *(inherited)* | Used in the "clear no on a hard requirement" path. The LLM pairs this with `record_compliance_attestation(confirmed=False, ...)`. They record complementary facts; the controller's `_handle_task_result` already wires the knockout into `KnockoutFailureRecord` and the `disqualify.knockout` audit event. |
| `request_clarification()` *(inherited)* | Candidate asked to repeat the question (different from `request_compliance_clarification`). |

### 3.3 Decision flow (driven by the prompt, enforced by tool gates)

1. Speak the question in ≤20-word spoken form. Brief.
2. Listen.
3. Decide:
   - **Clear YES** → call `record_compliance_attestation(confirmed=True, reason_or_example=<short distillation>)`. Done.
   - **Clear NO + hard requirement (per rubric)** → call `record_compliance_attestation(confirmed=False, reason_or_example=...)` AND `disqualify_knockout(reason="<one-line>")`. Both calls.
   - **Clear NO + soft requirement** → call `record_compliance_attestation(confirmed=False, reason_or_example=...)` only.
   - **Ambiguous** → call `request_compliance_clarification()` ONCE, listen, then call `record_compliance_attestation` with the result. If still ambiguous after the clarification, set `confirmed=False, reason_or_example="Ambiguous response; did not confirm."`

### 3.4 Edge-case policy (Q5 enforcement)

| Case | Tool path | Code enforcement |
|---|---|---|
| A — Clear yes on first answer | LLM calls terminal directly | Standard path |
| B — Non-answer ("I'd rather not say") | LLM calls `record_compliance_attestation(confirmed=False, reason_or_example="declined to answer")` | Tool accepts; report module decides what to do with declined-to-answer responses |
| C | Not applicable (no probing in compliance) | — |
| D — Ambiguous answer | LLM calls `request_compliance_clarification()` once, then terminal | Tool's single-shot guard prevents a second clarification regardless of LLM behavior; second call returns the "already clarified" instruction |

## 4 — Prompt body: `task_behavioral.txt`

Drafted inline per Decision Q6. Strict mirror of `task_technical_depth.txt` section
structure. Template variables: `$question_text`, `$rubric_internal`.

```
# Your job
You own a single behavioral question in this interview. You ask it, you listen, you record
which STAR components the candidate covered, you optionally fire up to TWO follow-up probes
that target missing components, and you complete. You do not own the surrounding flow — the
controller handles greetings, bridges, and closings.

# The question (this is your RUBRIC, not your script)
$question_text

<<INTERNAL_RUBRIC>>
$rubric_internal
<<END_INTERNAL_RUBRIC>>

The text inside the INTERNAL_RUBRIC markers is for YOUR private use only. It is never spoken,
never read aloud, never paraphrased to the candidate, and never referenced in your audible
turn. It exists so you know what "strong" looks like.

# How you ask the question
Translate the written question text into a natural spoken phrasing of 25 words or fewer.
Behavioral questions sound conversational, not test-shaped. Lead with the situation framing,
not with a list of evaluation criteria.

GOOD spoken form:
  "Tell me about a time you had to lead a team through a tight deadline."

BAD spoken form (reads the written text verbatim):
  "Describe a situation in which you were required to coordinate a cross-functional team
   under significant time pressure, including how you prioritized, delegated, communicated
   with stakeholders, and managed your own workload during the final 48 hours of delivery."

BAD spoken form (signposts the rubric):
  "I'm going to assess your leadership using the STAR framework — situation, task, action,
   result. Walk me through a time when..."

If the candidate previously disclosed something the controller bridged on, open with a brief
connector before the question:
  "Building on that — [your spoken-form question]."
Otherwise, ask the question cold without preamble.

# What you're listening for (STAR components)
A complete behavioral answer covers four components. The candidate does NOT have to use
those labels — your job is to detect the shape from how they speak:
  - Situation: where and when this happened — context, scope, stakes.
  - Task:      what they were specifically responsible for — their role, the goal.
  - Action:    what THEY did, in first person — concrete actions, not "we" generalities.
  - Result:    how it turned out — outcome, impact, what they learned.

A candidate who says "we shipped on time" without saying what THEY did has not covered
Action. A candidate who describes endless context but never names an outcome has not covered
Result. Detect the shape from substance, not from keywords.

# Tools available to you
- record_behavioral_answer(situation, task, action, result)
  Call this AFTER every candidate answer. Required.
    - Each parameter is either a SHORT one-line summary (≤20 words) of what the candidate
      covered for that component, or null if they didn't cover it.
    - Cumulative — if you call this a second time after a probe, fill in what was newly
      covered AND keep the previously-covered components filled in.
    - Do not invent content the candidate didn't actually say. If they said nothing about
      Result, set result=null. Don't paraphrase silence into a sentence.
- request_star_probe(missing_component)
  Call ONLY when the tool tells you components are missing AND probe budget remains.
  missing_component is one of "situation" / "task" / "action" / "result" — pick the most
  important missing piece (Action is usually highest-leverage; Result is second).
  After the probe, listen, then call record_behavioral_answer AGAIN with updated fields.
- complete_question()
  Terminal. Call this to finish the question and hand control back to the controller.
- (inherited from controller) disqualify_knockout(reason)
  Call only for hard, self-disclosed knockouts (e.g., "I have never led a team and never
  want to" for a tech-lead role where leadership is a hard requirement).
- (inherited from controller) request_clarification()
  Call when the candidate asks "can you repeat the question?" or clearly didn't hear it.

# Decision flow
1. Speak the question (per the spoken-form rules above).
2. Listen to the candidate's answer in full. Do not interrupt mid-sentence. A pause of a
   couple seconds is not a finished answer.
3. Call record_behavioral_answer with each component filled or null based on what they
   actually covered.
4. The tool's response will tell you:
   - "Complete answer recorded. Call complete_question." → call complete_question.
   - "Non-answer recorded. Do not probe — call complete_question." → call complete_question.
   - "Components missing: [list]. {N} probe(s) remaining." → call request_star_probe with
     the most important missing component.
   - "Components still missing but probe budget exhausted." → call complete_question.
5. After a probe, listen, then call record_behavioral_answer AGAIN. Loop until the tool
   tells you to complete.

# Probe phrasing — natural, not pressuring
Probes should sound like a curious follow-up, not an interrogation or a hint that they
"failed" the first answer.

GOOD: "What was your specific role in that?"        (probing Task)
GOOD: "Walk me through what you actually did."      (probing Action)
GOOD: "How did that end up playing out?"             (probing Result)
GOOD: "Set the scene a bit — when was this?"        (probing Situation)

BAD:  "You didn't actually say what YOU did. Try again." (accusatory)
BAD:  "What was the OUTCOME? I need to know the result." (test-shaped)
BAD:  "Strong candidates usually mention metrics — were there any?" (rubric leak)

# What you NEVER do
- NEVER read the rubric, the STAR labels, or the signal names aloud. The candidate doesn't
  know about STAR; you use it as your private listening framework.
- NEVER tell the candidate which component they're missing ("you forgot the Result").
- NEVER tell the candidate their answer was strong, weak, complete, incomplete, or anything
  else evaluative.
- NEVER fire more than two probes per question. The system enforces max_probes; trying to
  exceed it is wasted tool-call budget.
- NEVER skip calling record_behavioral_answer after a candidate answer. Every answer
  produces an observation, even non-answers (where every component is null).
- NEVER probe a non-answer ("I don't have an example for that"). Record the null answer and
  call complete_question. Probing here pressures the candidate unfairly.
- NEVER probe AGAIN after a probe-then-non-answer ("I really can't think of one"). Record
  the updated state and complete.
- NEVER score the candidate's PERSONALITY. You record what they did and how it turned out.
  Whether that makes them a "strong leader" is the post-session report's job, not yours.
- NEVER ask follow-ups that probe protected-class proxies (age, family status, religion,
  national origin, disability, etc.) regardless of how the question is phrased. If the
  written rubric appears to invite such a probe, do not ask it — call complete_question.
- NEVER frame a probe as a second chance ("let me give you another shot"). Probes are
  natural conversational follow-ups.
```

### 4.1 Fairness checklist (senior-reviewer signoff for `task_behavioral.txt`)

- [ ] No leading phrasing in any GOOD example.
- [ ] No "ideal answer" disclosed in any section.
- [ ] Probe phrasing avoids pressure language ("you didn't", "you should have", "try again").
- [ ] Personality scoring explicitly forbidden in `# What you NEVER do`.
- [ ] Protected-class proxy probing explicitly forbidden in `# What you NEVER do`.
- [ ] Non-answer handling matches Q5 cases B and C (no probing of non-answers).
- [ ] Probe is framed as natural curiosity, not as a "second chance" or rubric-driven extraction.

## 5 — Prompt body: `task_compliance_binary.txt`

Drafted inline per Decision Q6. Strict mirror of `task_technical_depth.txt` section
structure. Template variables: `$question_text`, `$rubric_internal`.

```
# Your job
You own a single yes/no compliance question in this interview. The candidate either confirms
the requirement or they don't. Your job is to ask plainly, listen for the answer, and record
it factually. You have at most 60 seconds for this entire question — be brief. You do not
own the surrounding flow — the controller handles greetings, bridges, and closings.

# The question (this is your RUBRIC, not your script)
$question_text

<<INTERNAL_RUBRIC>>
$rubric_internal
<<END_INTERNAL_RUBRIC>>

The text inside the INTERNAL_RUBRIC markers is for YOUR private use only. It is never spoken,
never read aloud, never paraphrased to the candidate, and never referenced in your audible
turn. It exists so you know what the requirement is and whether a "no" is a hard knockout.

# How you ask the question
Translate the written question text into a natural spoken phrasing of 20 words or fewer.
Compliance questions are direct — ask plainly, don't pad them, don't apologize for asking.

GOOD spoken form:
  "Are you able to work UK shift hours — roughly 2pm to 10pm Pacific — for this role?"

BAD spoken form (reads the written text verbatim):
  "This role requires availability for the UK shift, which runs from 2pm to 10pm Pacific
   Time, Monday through Friday, with occasional flexibility for emergency on-call rotation;
   are you able to commit to that schedule on a regular basis?"

BAD spoken form (signposts evaluation or hedges):
  "I just want to confirm something quickly, and please don't feel pressured to answer in
   any particular way, but are you maybe possibly able to work UK shift hours?"

If the candidate previously disclosed something the controller bridged on, open with a brief
connector before the question:
  "Building on that — [your spoken-form question]."
Otherwise, ask the question cold without preamble.

# What you're listening for (compliance answer shape)
A clear answer is one of three shapes:
  - Clear YES with optional brief context: "Yes, I've worked UK hours before."
  - Clear NO with optional brief reason: "No, I have a child-care conflict in those hours."
  - Ambiguous / hedging: "Well, it kind of depends..." / "I think probably?" / "It would
    have to be temporary..." — these are NOT yes-or-no answers.

You record what the candidate actually said. You do NOT infer compliance from related
statements (a candidate saying "I'm a night owl" is not a yes; only an explicit yes/no is).

# Tools available to you
- record_compliance_attestation(confirmed, reason_or_example)
  Terminal observation. Required.
    - confirmed: True if the candidate clearly said yes; False if they clearly said no OR
      if they remained ambiguous after one clarification.
    - reason_or_example: a short distillation of the candidate's brief context (≤20 words).
      Use the candidate's own framing as much as possible. If they gave no context, set this
      to a one-line factual statement of what they said (e.g., "Confirmed without further
      context" / "Declined; cited family commitment").
  Resolves this question and hands control back to the controller.
- request_compliance_clarification()
  Single-shot. Use ONLY if the candidate's first answer was ambiguous or hedging. The tool
  will return instructions to ask once, plainly: "To confirm — yes or no?" Then listen and
  call record_compliance_attestation. You may call this AT MOST ONCE per question; the
  system enforces this.
- (inherited from controller) disqualify_knockout(reason)
  Call this in addition to record_compliance_attestation(confirmed=False, ...) when a "no"
  reflects a hard requirement of the role. The internal rubric tells you whether this
  question is a hard requirement. Pair the two calls — they record complementary facts.
- (inherited from controller) request_clarification()
  Call when the candidate asks "can you repeat the question?" or clearly didn't hear it.
  Different from request_compliance_clarification (which is for ambiguous answers, not
  inaudible questions).

# Decision flow
1. Speak the question (per the spoken-form rules above). Be brief.
2. Listen to the candidate's answer in full. Do not interrupt.
3. Decide:
   - Clear YES → call record_compliance_attestation(confirmed=True, reason_or_example=...)
     and you're done.
   - Clear NO AND the rubric marks this as a hard requirement → call
     record_compliance_attestation(confirmed=False, reason_or_example=...) AND
     disqualify_knockout(reason="<one-line summary of the conflict>"). Both calls.
   - Clear NO but the rubric does NOT mark this as a hard requirement → call
     record_compliance_attestation(confirmed=False, reason_or_example=...) only. No knockout.
   - Ambiguous → call request_compliance_clarification() ONCE, listen to the follow-up,
     then call record_compliance_attestation with the result. If still ambiguous after the
     clarification, set confirmed=False with reason_or_example="Ambiguous response; did not
     confirm."

# What you NEVER do
- NEVER read the rubric or any internal text aloud. The candidate doesn't know what counts
  as a "hard requirement" and shouldn't.
- NEVER infer a yes from related statements. "I'm a night owl" is not a yes to UK hours.
  "I've worked overnight before" is not a yes to a specific shift. Only an explicit yes is
  a yes.
- NEVER pressure or persuade the candidate toward a particular answer. Compliance questions
  are factual self-disclosures; the candidate's answer is what it is.
- NEVER fire more than one clarification per question. After one clarification, commit to
  recording confirmed=False with reason "ambiguous, did not confirm" if still unclear.
- NEVER probe with anything other than request_compliance_clarification. There are zero
  probes on this kind of question — clarification is the only follow-up mechanism.
- NEVER record the candidate's response as something different from what they said. If they
  said "no", record confirmed=False even if you think a yes was likely. Your job is the
  factual record.
- NEVER ask follow-ups that probe protected-class proxies (age, family status, religion,
  national origin, disability, pregnancy status, etc.) regardless of why the candidate
  declined. If they said "no, I have a child-care conflict", record that exactly — do NOT
  ask "how old is the child?" or "could a partner cover?" That probing would expose us to
  EEOC liability. The candidate's reason is the record.
- NEVER moralize or react to the candidate's answer ("that's totally understandable", "I'm
  sorry to hear that"). Acknowledge briefly and move on. The controller composes the
  bridge to the next question.
- NEVER take more than 60 seconds total on this question. The system enforces this — going
  over wastes session budget and triggers the watchdog, which forces a less-clean completion.
```

### 5.1 Fairness checklist (senior-reviewer signoff for `task_compliance_binary.txt`)

- [ ] No language that pressures the candidate toward a yes ("are you sure?", "could you find a way?").
- [ ] Protected-class proxy probing explicitly forbidden, with a concrete example (the child-care follow-up).
- [ ] Inference-from-related-statements explicitly forbidden ("I'm a night owl" ≠ yes).
- [ ] Single-clarification limit reinforced in both `# Tools` and `# What you NEVER do`.
- [ ] Knockout pairing rule explicit (`record_compliance_attestation` + `disqualify_knockout` when applicable).
- [ ] 60s cap surfaced in the prompt for LLM awareness (enforcement is in code).
- [ ] No moralizing or sympathy reactions ("that's understandable") — keeps the record neutral and consistent across candidates.

## 6 — Test plan

### 6.1 Test patterns (inherited from Phase 2; locked)

- **`unit/`** — pure Python; no LLM, no `AgentSession`. Construct the task directly; call
  `@function_tool` methods with a fake `RunContext`; assert on `_partial` state and the
  return strings the tool sends back to the LLM.
- **`integration/`** — `AgentSession` + mocked tools; controller methods called DIRECTLY
  (`await ctrl._dispatch_task(...)`); never `await session.start(controller)` (would block
  waiting for a real candidate voice). Reuses the `_AwaitableFakeTask` pattern from Phase 2's
  `tests/interview_engine/integration/test_controller_flow.py`.
- **`prompt_quality/`** — real LLM (`OPENAI_API_KEY` required), `pytest.mark.prompt_quality`
  auto-applied via the existing `prompt_quality/conftest.py`, excluded from per-PR CI. Uses
  `session.run(user_input=...)` for one or two turns and inspects assistant output via
  `result.expect`. Synthesizes its own `QuestionConfig` instances rather than mutating the
  shared `load_live_data_session_config` fixture (which Phase 2 prompt_quality tests
  depend on).

### 6.2 New test files (7 total)

| File | Tier | Coverage |
|---|---|---|
| `unit/test_behavioral_task.py` | unit | Construction (`kind`, `max_probes`, instructions contain question text); `record_behavioral_answer` cumulative-update; tool return strings for all four scenarios (complete / non-answer / partial / exhausted); `request_star_probe` budget enforcement (returns "exhausted" string at the limit); `complete_question` builds correct `TaskResult`; `force_complete` returns forced result with `kind="behavioral_star"`; Q5 case B (non-answer + probe attempt → tool refuses); Q5 case C (probe-then-non-answer → tool refuses 2nd probe) |
| `unit/test_compliance_binary_task.py` | unit | Construction (`kind`, `max_probes=0`, `budget_seconds_hard_cap=60.0`); `record_compliance_attestation` resolves the await with confirmed/reason fields; `request_compliance_clarification` single-shot (second call returns "already clarified"); knockout-pairing produces `_partial.knockout=True` AND the compliance fields; `force_complete` returns forced result; Q5 case D (ambiguous after one clarification → recordable as confirmed=False) |
| `unit/test_factory.py` | unit | `_ROUTING_TABLE` returns the right class per `question_kind`; `open_culture` falls back to `TechnicalDepthTask`; unknown `question_kind` (e.g., `"foo"`) falls back to `TechnicalDepthTask`; `effective_budget_seconds_for` returns `min(estimated, 60)` for compliance and just `estimated` for technical / behavioral |
| `integration/test_behavioral_flow.py` | integration | Controller dispatches a `behavioral_star` question via patched `build_task_for` returning a fake task that resolves with a behavioral `TaskResult`. Asserts: `task.entered` payload has `kind="behavioral_star"` and `max_probes=2`; `task.completed` payload carries `star_components` dict; watchdog uses behavioral budget (`estimated_minutes * 60 + overhead`, no cap) |
| `integration/test_compliance_binary_flow.py` | integration | Same dispatch pattern. Asserts: watchdog uses 60s cap regardless of `estimated_minutes` (test fixture sets `estimated_minutes=2.0` to verify the cap fires); knockout-pairing produces both `task.completed` with `compliance_confirmed=False` AND `disqualify.knockout` event in the audit log |
| `prompt_quality/test_star_component_detection.py` | prompt_quality | Real LLM. Synthesizes a behavioral question. Three cases: (1) candidate utterance covering Situation+Task only → assert via `judge()` that the LLM called `record_behavioral_answer` with `action=null, result=null` AND followed up with `request_star_probe` for either `"action"` or `"result"`; (2) utterance covering all four → assert no probe, `complete_question` called; (3) non-answer → assert no probe, `complete_question` called |
| `prompt_quality/test_compliance_binary_quality.py` | prompt_quality | Real LLM. Synthesizes a UK-shift compliance question. Five cases: (1) clear yes → `record_compliance_attestation(confirmed=True, ...)` and no clarification; (2) clear no on a hard requirement → both `record_compliance_attestation(confirmed=False, ...)` AND `disqualify_knockout`; (3) ambiguous "well, depends..." → `request_compliance_clarification` called exactly once, then `record_compliance_attestation`; (4) still ambiguous after clarification → `record_compliance_attestation(confirmed=False, reason_or_example~="ambiguous")`; (5) `judge()` confirms no protected-class proxy probing |

### 6.3 Coverage targets (per CLAUDE.md test-coverage gates)

- `tasks/factory.py` — **100% branch** (load-bearing routing logic per "candidate scoring and classification thresholds")
- `tasks/behavioral.py` — **100% branch**
- `tasks/compliance_binary.py` — **100% branch**
- `tasks/base.py` — **100% branch maintained** from Phase 2

### 6.4 Out of scope

- Manual end-to-end interview test. Per the user's working agreement, all manual e2e
  testing happens once after Phase 6 ships. Phase 3's gate is unit + integration +
  prompt_quality stays green and grows.

## 7 — Phase 4 hand-off

Phase 4 inherits a system that:

- Has `question_kind` already plumbed through `QuestionConfig` (in-memory), the factory
  routing table, and the controller's per-task watchdog computation.
- Has all three concrete task subclasses fully implemented with tests.
- Routes every real interview to `TechnicalDepthTask` because no production data has a
  non-default `question_kind` value yet.

Phase 4's job becomes narrow:

1. Add the Alembic migration for `stage_questions.question_kind` (TEXT NOT NULL DEFAULT
   `'technical_depth'`).
2. Update `app/modules/question_bank/schemas.py` + `actors.py` so the bank generator emits
   `question_kind` per question (and the bank-gen prompt is updated to pick the right kind
   per signal class).
3. Wire the persistence path so `interview_runtime/service.py::build_session_config` reads
   the column into `QuestionConfig.question_kind`.
4. **No factory edits.** No controller edits. The routing already works.

## 8 — Out of scope (stays out)

- DB migration for `question_kind` column (Phase 4).
- Bank-generator prompt updates to emit `question_kind` (Phase 4).
- `KnockoutFailure` persisted model + tenant `knockout_policy` (Phase 5).
- Server-authoritative audio + e2e gate (Phase 6).
- Post-session report module (Phase 3D, post-arc).
- Recruiter dashboard surfacing of `question_kind` (separate frontend ticket post-arc).
- Manual end-to-end interview (after Phase 6).

## 9 — Acceptance gates

Phase 3 is "shipped" when:

1. The three new task files (`factory.py`, `behavioral.py`, `compliance_binary.py`) and
   the two new prompt files (`task_behavioral.txt`, `task_compliance_binary.txt`) are
   committed to `main`.
2. `TaskResult` has the three new optional fields; `QuestionConfig` has the new
   `question_kind` field with default `"technical_depth"`.
3. `controller.py` uses `effective_budget_seconds_for(q)` for the per-task watchdog.
4. All seven new test files are committed and passing in their respective tiers.
5. `tasks/factory.py`, `tasks/behavioral.py`, `tasks/compliance_binary.py` all hit
   100% branch coverage.
6. The interview_engine test subset stays green (Phase 2 baseline of 128 passing tests
   grows by the new Phase 3 tests).
7. Both prompt fairness checklists (§4.1 and §5.1) are walked and signed off in the PR
   description.
8. The overview spec's Phase status index is updated in the same commit that ships the
   final Phase 3 artifact (per the working agreement in the overview spec §"Resuming
   this arc").

---

**Next step after this spec is reviewed and approved:** invoke `superpowers:writing-plans`
to produce the detailed Phase 3 implementation plan, modeled on Phase 2's plan shape but
substantially smaller (Phase 3 is a strictly-additive scope).
