# Behavioral Layer & Intro / Warmup — Design

**Status:** Approved, ready for implementation planning
**Date:** 2026-05-19
**Owner:** Ishant Pundir (solo developer)

---

## Context

The current `ai_screening` stage runs technical-depth scenario questions only. The candidate clicks Start and hears the first scenario question cold, with no greeting, role framing, or background check. Two consequences:

1. **No warm framing.** Candidates are dropped into a hard scenario question with no anxiety runway. Research shows anxious candidates are systematically rated lower even at equal capability ([BPS Research Digest](https://www.bps.org.uk/research-digest/why-interviewers-rate-anxious-candidates-harshly-and-what-you-can-do-about-it); Schneider 2019, *IJSA*).
2. **No experience verification gate.** Candidates who don't actually have the knockout-required experience (e.g., 5+ years of MuleSoft/TIBCO/Boomi for the Workato JD) burn the full 15-minute technical session before the existing `acknowledge_no_experience` path fires mid-flow. Wasted candidate time, wasted compute, awkward UX.

This work introduces a **behavioral verification phase** as the gate before technical depth, plus an **intro/warmup turn** that frames the role. Both reuse the existing engine architecture — no new Phase FSM, no Judge prompt rewrite, no State Engine changes to the invariants that already work.

**The key architectural insight:** behavioral and technical aren't different phases — they're different `question_kind` values within the same question bank. Migration 0026 already added the `stage_questions.question_kind` column with `behavioral_star` as a valid value; the bank generator emits it today but the engine ignores it. We make the engine read it in exactly one place (Section 4) and otherwise let the existing Judge/State/Speaker machinery process behavioral and technical questions identically.

---

## Goals

After this work lands:

- **Behavioral verification works as a knockout gate.** If the candidate explicitly discloses no experience with a knockout signal during behavioral questions, the existing `close_polite` policy fires and the session ends without burning technical scenarios.
- **The candidate hears a warm intro before any question.** Persona-anchored greeting + role brief in Arjun's voice, ~25–35s of agent speech before the first question.
- **The candidate's stage timer reflects their actual budget.** Lifecycle timer pauses during intro; ticks down only after the candidate first speaks.
- **Perceived startup latency drops to near-zero.** Candidate-side loading state hides the engine boot + first-LLM-call window behind a "Preparing your interviewer…" UI affordance.
- **The recruiter edits behavioral and technical questions in one bank.** Same editor, same model, same workflows. Per-kind retry available when one generation call fails.
- **All existing engine invariants stay intact.** Anti-leak firewall, push_back cap, knockout policy, fallback synthesis, audit envelope, persona invariant — none change.

---

## Non-goals

We are explicitly **not** building these, and the spec should be re-read before any of them gets reintroduced:

| Non-goal | Why excluded |
|---|---|
| Intro audio prefetch / Redis cache / early engine dispatch | Frontend loader (Section 3) achieves the same UX outcome with zero backend complexity. Rejected after design iteration. |
| `Phase` enum on lifecycle / phase-aware Judge prompt / phase-aware push_back cap | The `question_kind` ordering in the queue makes phase emergent. No FSM, no Judge variant, no engine config field needed. |
| Cross-claim contradiction detection in behavioral | Useful capability; deferred to v2. Existing `candidate_claims` architecture supports it but requires a Judge prompt extension that isn't load-bearing for v1. |
| Per-question `push_back_cap` configuration | Cap = 2 works for both kinds. Differentiating is a tuning question after we have data. |
| Exposing `question_kind` to the Judge LLM input | The Judge stays kind-agnostic. It judges what's in front of it. The orchestrator owns kind-aware behavior. |
| Recruiter UI changes for behavioral-question grouping/labeling | Frontend `frontend/app/` concern, separate work. Existing bank editor handles per-question editing fine. |
| Skip-behavioral toggle on tenant or stage | YAGNI. If a recruiter wants to skip behavioral for a specific role, they delete those questions from the bank. |
| New Speaker per-action prompts (`clarify`, `redirect`, `push_back`, etc.) for behavioral | Existing prompts work as-is — they're driven by `bank_text` content. Only `deliver_question.txt` needs a small new branch (Section 4). |

---

## Architecture overview

```
JD PIPELINE (Phase 2C.2 — bank generation)
  Recruiter confirms signals on the JD
        ↓
  Bank generator (Dramatiq actor) runs PER STAGE:
        ├─ Call 1: behavioral_star questions     → 1-2 questions, position 0-1
        │     prompts/v1/question_bank_ai_screening_behavioral.txt
        │     input: knockouts (type ∈ {experience, behavioral})
        │     budget: 3 min mandatory
        │
        └─ Call 2: technical_depth questions     → existing N questions, position 2+
              prompts/v1/question_bank_ai_screening.txt (existing, minor edits)
              input: full signal set
              budget: stage.duration_minutes - behavioral_total

  Concatenated bank stored in stage_question_banks + stage_questions
  Per-kind status tracked on stage_question_banks.generation_status_by_kind (new column)


CANDIDATE SESSION (Phase 3C onward — engine runtime)
  Candidate completes pre_check → consent → OTP verify
        ↓
  Candidate clicks "Start Interview"  →  POST /start
  /start mints LK token, creates room, dispatches engine — unchanged
        ↓
  ┌─────────────────────────────────────────────────────────────────┐
  │ FRONTEND: shows loader ("Preparing your interviewer…")          │
  │ Dismissed when agent state first transitions to "speaking"      │
  └─────────────────────────────────────────────────────────────────┘
        ↓
  Engine on_enter():
    1. Speaker call (instruction_kind=intro_brief)
       — greeting + role brief, ~25-35s, persona-anchored
       — NO Judge call, NO state mutation, NO timer ticking
    2. State Engine initialize_for_session_start()
       — synthesizes JudgeOutput(advance, target=questions[0])
       — questions[0] is the first BEHAVIORAL_STAR question
    3. deliver_first_question Speaker call fires
       — bank_text = "Kindly walk me through your background…"
        ↓
  Candidate speaks for the first time  →  on_user_turn_completed
    — Lifecycle timer STARTS HERE (session.timer_started event)
    — Existing Judge → State → Speaker pipeline runs
        ↓
  Behavioral verification loop (existing pipeline, behavioral questions in queue):
    — Judge fan-out extracts observations on knockout signals
    — push_back / probe / clarify as needed
    — Knockout signal "→failed" → existing knockout_policy fires → close_polite
    — Otherwise: queue advances to next question
        ↓
  Queue advance from behavioral_star → technical_depth:
    — Orchestrator detects kind transition (Section 4)
    — Sets is_post_phase_transition=True on the SpeakerInput
    — deliver_question.txt prompt picks the warm-segue branch
        ↓
  Technical phase runs (existing flow, unchanged)
        ↓
  Closing: existing polite_close / time_expired / knockout_closed / candidate_ended
```

The key property: **the State Engine, Judge, and Speaker per-action prompts (except for the small `deliver_question.txt` addition in Section 4) are unchanged.** Behavioral questions flow through the existing pipeline because they are shaped identically to technical questions — same Pydantic schema, same anchors/follow_ups/rubric structure, just different content and a different `question_kind` tag.

---

## Section 1 — Bank generator: two-call architecture

### Decision summary

- Two LLM calls per stage instead of one.
- Both calls produce the same `StageQuestionBankOutput` Pydantic schema (unchanged).
- Behavioral first (positions 0-1), technical after (positions 2+).
- Per-kind status tracked; per-kind retry endpoint supported.
- Graceful behavior when behavioral has nothing to verify (no eligible knockouts).

### Schema

**Shared output schema** — no changes. `GeneratedQuestion` in `app/modules/question_bank/schemas.py` already supports `question_kind: Literal["technical_depth", "behavioral_star", "compliance_binary"]`. Each call's LLM populates this field correctly via its per-kind prompt.

**New DB column** (migration 0029):

```sql
ALTER TABLE stage_question_banks
ADD COLUMN generation_status_by_kind JSONB NOT NULL DEFAULT '{}';

-- shape:
-- {
--   "behavioral_star":  "reviewing" | "failed" | "skipped_no_eligible_signals",
--   "technical_depth":  "reviewing" | "failed"
-- }
```

`bank.status` (the existing single canonical status) remains authoritative. Its value reflects the union:

- `"reviewing"` if at least one kind reached `reviewing`
- `"failed"` only if all attempted kinds failed (or if the technical call alone failed and there were no behavioral questions to ship)

### Behavioral signal filter

```python
def _filter_behavioral_eligible(signals: list[dict]) -> list[dict]:
    """Knockout signals of types we voice-verify."""
    return [
        s for s in signals
        if s.get("knockout") is True
        and s.get("type") in ("experience", "behavioral")
    ]
```

Excluded by design:

- `credential` knockouts (degree, certs) — better verified by ATS / resume pre-filter than by voice
- `competency` knockouts (rare) — depth-flavored, belong to technical

When the filter returns 0 signals: **behavioral call is skipped entirely.** `generation_status_by_kind["behavioral_star"] = "skipped_no_eligible_signals"`. Technical call runs with full budget. Bank is technical-only. Engine handles this fine — `on_enter` plays intro, queue advances to position 0 (a technical_depth question), `is_post_phase_transition` stays False (no prior question kind exists to differ from).

### Prompt files

```
prompts/v1/
├── question_bank_common.txt                            (existing, unchanged — output schema, anti-leak)
├── question_bank_ai_screening.txt                      (existing — minor wording edit to clarify
│                                                       it now produces technical_depth only)
└── question_bank_ai_screening_behavioral.txt           NEW
```

Actor `STAGE_TYPE_TO_PROMPT` map evolves from `stage_type → prompt_name` to `(stage_type, question_kind) → prompt_name`:

```python
STAGE_TYPE_TO_PROMPT = {
    ("ai_screening", "technical_depth"):    "question_bank_ai_screening",
    ("ai_screening", "behavioral_star"):    "question_bank_ai_screening_behavioral",
    ("phone_screen", "technical_depth"):    "question_bank_phone_screen",
    # behavioral for phone_screen: future work
}
```

The behavioral prompt's body specializes the existing common prompt:

- Number of questions: "Generate 1 open-ended question by default. Generate a 2nd ONLY when knockout signals span unrelated domains (e.g., language requirement + management experience) that one question cannot reasonably cover. Bounded at 2 max."
- Question shape: open-ended claim-verification ("Kindly walk me through your background — total years, the iPaaS platforms you've used, and where you've focused most"), NOT scenario design
- `positive_evidence` anchors: claim shapes ("States total years in integration work (≥8 for sufficient)", "Names MuleSoft/TIBCO/Boomi as a platform used in production")
- `red_flags`: claim weakness patterns ("Vague years claim with no employer or scope context")
- `follow_ups`: sharp drill-downs that bluff-catch ("On MuleSoft — Mule 3 or 4, CloudHub or Runtime Fabric?")
- `rubric`: claim-quality bands, not design-quality bands
- `is_mandatory`: always `true` (they're the gate)
- `estimated_minutes`: ~1.5-2 per question (vs ~4 for technical)
- Budget allotment communicated explicitly: "You have at most 3 minutes of mandatory budget."

### Generator control flow

`_generate_one_bank()` in `app/modules/question_bank/actors.py` becomes two sequential LLM calls + concatenation. Pseudocode:

```python
async def _generate_one_bank(db, *, bank, stage, instance, job, snapshot, started_by):
    eligible_behavioral_signals = _filter_behavioral_eligible(snapshot.signals)

    behavioral_questions: list = []
    behavioral_status: str
    if not eligible_behavioral_signals:
        behavioral_status = "skipped_no_eligible_signals"
    else:
        try:
            behavioral_result = await _generate_questions_for_kind(
                db, bank=bank, stage=stage, instance=instance, job=job,
                snapshot=snapshot,
                kind="behavioral_star",
                eligible_signals=eligible_behavioral_signals,
                budget_minutes=BEHAVIORAL_BUDGET_MIN,  # = 3
            )
            behavioral_questions = behavioral_result.questions
            behavioral_status = "reviewing"
        except (BudgetExceededError, Exception) as exc:
            logger.error("question_bank.behavioral_call_failed", ...)
            behavioral_status = "failed"
            behavioral_questions = []  # graceful: continue to technical

    behavioral_total = sum(q.estimated_minutes for q in behavioral_questions)
    technical_mandatory_cap = stage.duration_minutes - behavioral_total

    try:
        technical_result = await _generate_questions_for_kind(
            db, bank=bank, stage=stage, instance=instance, job=job,
            snapshot=snapshot,
            kind="technical_depth",
            eligible_signals=snapshot.signals,  # full set
            budget_minutes=technical_mandatory_cap,
        )
        technical_questions = technical_result.questions
        technical_status = "reviewing"
    except (BudgetExceededError, Exception) as exc:
        logger.error("question_bank.technical_call_failed", ...)
        technical_status = "failed"
        technical_questions = []

    # Write per-kind status
    bank.generation_status_by_kind = {
        "behavioral_star": behavioral_status,
        "technical_depth": technical_status,
    }

    # Concatenate with explicit positions
    validated: list = []
    for i, q in enumerate(behavioral_questions):
        q.position = i
        validated.append(q)
    for j, q in enumerate(technical_questions, start=len(behavioral_questions)):
        q.position = j
        validated.append(q)

    # Decide bank.status
    if technical_status == "failed" and behavioral_status != "reviewing":
        # Nothing usable to ship
        transition_to_failed(bank, error="all generation calls failed")
        return
    if technical_status == "failed" and not validated:
        transition_to_failed(bank, error="technical call failed, no behavioral to ship")
        return

    # At least one kind shipped — transition to reviewing
    await write_generated_questions(db, bank=bank, questions=validated, source="ai_generated")
    bank.pipeline_version_at_generation = instance.pipeline_version
    bank.stage_config_snapshot = {"signal_filter": stage.signal_filter, "difficulty": stage.difficulty}
    bank.is_stale = False
    transition_to_reviewing_after_generation(bank, user_id=started_by)
```

`_generate_questions_for_kind(...)` is the existing single-call logic (LLM call + budget validation + retry) wrapped to take a kind/eligible-signals/budget input. Existing `validate_llm_output_against_snapshot()` is reused per call with the per-kind budget.

### Per-kind retry endpoint

```
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/banks/regenerate-kind
  body: { "kind": "behavioral_star" | "technical_depth" }
```

Wipes only questions with `question_kind == kind AND source ∈ {ai_generated, ai_regenerated}` (recruiter-edited rows preserved), re-runs that kind's LLM call, writes new questions back, updates `generation_status_by_kind[kind]`, re-stamps `pipeline_version_at_generation` and `stage_config_snapshot`.

This endpoint uses the existing question_bank state machine — invalidates `confirmed` back to `reviewing` via `auto_revert_on_edit` since one slice of the bank is regenerating.

---

## Section 2 — Engine: intro_brief Speaker turn + lifecycle timer pause

### Decision summary

- One new `InstructionKind`: `intro_brief`.
- One new Speaker prompt: `prompts/v2/engine/speaker/intro_brief.txt`.
- One Speaker call before `initialize_for_session_start()` in `on_enter`.
- Lifecycle timer starts on first candidate utterance, not at `on_enter`.
- No new Judge prompt, no State Engine changes.

### Schema additions

```python
# app/modules/interview_engine/models/speaker.py
class InstructionKind(StrEnum):
    intro_brief = "intro_brief"                       # NEW
    deliver_first_question = "deliver_first_question"
    # ... existing kinds unchanged

class SpeakerInput(BaseModel):
    # ... existing fields ...

    # Populated ONLY when instruction_kind == intro_brief; None for every other kind
    job_title: str | None = None
    hiring_company_name: str | None = None
    role_summary: str | None = None
    session_duration_minutes: int | None = None
    question_count: int | None = None
```

`persona_name` and `candidate_name` are already on `SpeakerInput`. The new fields carry public role context. Anti-leak invariant unchanged — role facts are not rubric content.

**Important — `hiring_company_name` is the HIRING company, not the ProjectX tenant.** For the Workato JD example: the tenant is `BinQle` (the recruiting agency hosting the role), but the candidate is interviewing FOR `Workato` (BinQle's client). The candidate's mental model is "I'm being interviewed for Workato," not BinQle. The field is populated from `company_profile.org_unit_name` — equivalently, the org_unit at depth 0 in `org_unit_ancestry` (the unit closest to the JD). In-house tenants (where the recruiter and hiring company are the same legal entity) still work correctly because the closest org_unit IS the company. `build_session_config` already walks ancestry for `company_profile`; threading the org_unit_name through is a small addition.

### Speaker prompt — `intro_brief.txt`

Sketch — final wording lives in the implementation plan:

```
# TASK
The interview just started. Greet the candidate and brief them on the role.
ONE utterance — they should hear all of it before saying anything.

# ARJUN'S SHAPE
- Open with "Hi {candidate_name}" — first-greeting bypasses the discourse-marker
  rotation. Subsequent turns rotate normally.
- Three beats, smoothly connected:
   1. Greet + self-identify ("I'm {persona_name}, Senior Engineering Manager.")
   2. Frame the role ({job_title} at {hiring_company_name}) using role_summary —
      summarize in ≤2 sentences. Name the platforms/tech from role_summary
      when they're concrete; otherwise stay abstract.
   3. Set expectations: "{session_duration_minutes} minutes,
      {question_count} questions" — quick, not laborious.
- Soft target: 45–70 words. Hard cap: 90 words.
- NEVER name signals, anchors, rubric criteria, or what we're "looking for."
- End cleanly. Do NOT ask "tell me about yourself" — the next turn handles that.
- NO sycophantic openers ("Great to meet you!", "Welcome!").

# SPECIFIC FORBIDDENS
- "I'll be evaluating your fit on …"
- "We're looking for someone with …"
- "Get ready for some technical questions"

# EXAMPLES
[3 exemplars showing the shape, in Arjun's voice, drawn from real
 SessionConfig data]

# REMINDER
One utterance. ≤90 words. Three beats. Persona's voice.
```

Uses the shared `_preamble.txt` per existing Speaker composition — persona invariant carries through automatically.

### Orchestrator `on_enter` changes

```python
# app/modules/interview_engine/orchestrator.py
async def on_enter(self, agent: Any) -> None:
    # Do NOT set _session_started_monotonic here.
    # Timer starts on first candidate utterance — see on_user_turn_completed.
    turn_id = str(uuid.uuid4())
    self._turn_index += 1
    intro_started_at = time.monotonic()

    self._append(TURN_STARTED, TurnStartedPayload(
        turn_id=turn_id, turn_index=self._turn_index,
        stt_text_raw=None, stt_text_used=None,
    ).model_dump())

    # NEW: intro_brief Speaker turn
    intro_speaker_input = self._build_intro_speaker_input(turn_id)
    self._append_speaker_input(turn_id=turn_id, speaker_input=intro_speaker_input)
    await self._stream_speaker_and_say(
        agent=agent, turn_id=turn_id, speaker_input=intro_speaker_input,
    )

    self._append(TURN_COMPLETED, TurnCompletedPayload(
        turn_id=turn_id, turn_index=self._turn_index,
        duration_ms=int((time.monotonic() - intro_started_at) * 1000),
    ).model_dump())

    # EXISTING: synthetic JudgeOutput → deliver_first_question (now the
    # first behavioral_star question). Fires immediately after intro_brief
    # completes — the candidate hears intro + first question as one
    # continuous opening, no awkward pause.
    turn_id = str(uuid.uuid4())
    self._turn_index += 1
    synthetic = self._state.initialize_for_session_start()
    # ... existing flow unchanged ...
```

`_build_intro_speaker_input()` is a new helper that constructs `SpeakerInput(instruction_kind=intro_brief, ...)` from `self._cfg` (SessionConfig) + persona.

### Lifecycle timer pause

```python
# orchestrator.py — change in on_enter
# OLD:
#   self._session_started_monotonic = time.monotonic()
# NEW:
#   self._session_started_monotonic stays None until first candidate utterance

# orchestrator.py — addition at top of on_user_turn_completed
async def on_user_turn_completed(self, agent, turn_ctx, new_message) -> None:
    if self._session_started_monotonic is None:
        self._session_started_monotonic = time.monotonic()
        self._append(SESSION_TIMER_STARTED, {
            "wall_ms": int(time.time() * 1000),
        })
    # ... existing body ...
```

New event kind `SESSION_TIMER_STARTED = "session.timer_started"` added to `event_kinds.py` and `ALL_EVENT_KINDS`.

`_elapsed_ms()` already handles `None` defensively (returns 0), so attribute publishes during intro show the full budget — the candidate's frontend timer reflects what they actually get to use.

**Note on `audio_tuning_summary` and the timer pause** — the audio-tuning-summary aggregation in `_wire_session_observability` uses its own monotonic baseline (`state["t0_monotonic"]`, set on the first wired event), independent of `_session_started_monotonic`. So audio metrics during `intro_brief` (TTS TTFB, agent_state transitions, etc.) DO factor into the per-session percentile calculations. This is the right behavior — we want intro TTS performance visible in the same tuning-summary the technical phase contributes to. The pause only affects the candidate-facing `time_remaining_seconds` attribute and the lifecycle's `time_exhausted()` check.

### Speaker failure paths for intro_brief

The existing `_handle_empty_speaker_output` and `_handle_interrupted_speaker` paths use generic `PersonaSpec.fallback_*` strings that don't make sense as an intro ("Mm — could you take it from the top?"). Small refinement:

```python
# orchestrator.py — _handle_empty_speaker_output and exception path
if speaker_input.instruction_kind == InstructionKind.intro_brief:
    # Hard-coded persona-voiced intro fallback for the failure path
    fallback = (
        f"Hi {self._cfg.candidate.name or 'there'} — I'm "
        f"{self._cfg.persona_name or 'Arjun'}. Got a few questions for you "
        f"about your background and experience. Right, let's begin."
    )
else:
    # Existing generic fallback
    fallback = self._compose_empty_output_fallback(speaker_input)
```

Same fallback string used in both the empty-output and exception paths when `instruction_kind == intro_brief`. Implementation detail; not a new persona scaffolding concept.

---

## Section 3 — Frontend: pre-speech loading state

### Decision summary

- Zero backend code changes from Section 2.
- Candidate frontend (`frontend/session/`) shows a loading state from `/start` API success until the agent's first audio frame plays.
- Dismiss signal: LK participant attribute `lk.agent.state` first transitions to `"speaking"`.
- 15s timeout transitions to error state.

### Mechanism

```
Candidate clicks "Start Interview"
  ↓
POST /start → returns LK token + audio_processing_hints
  ↓
Mount LK room view in "loading" mode:
  - Show: spinner + "Preparing your interviewer…" (or similar Arjun-voiced copy)
  - Hide: agent visualizer, control bar, transcript, timer
  ↓
LK room connects, candidate joins room (~200-500ms)
Engine joins room, runs intro_brief Speaker LLM + TTS (~2-3s)
  ↓
Listen for: agent participant's lk.agent.state attribute transitions to "speaking"
  (This attribute is published automatically by LiveKit's AgentSession when
   agent_state_changed fires — no backend wiring required)
  ↓
Dismiss loader → full interview UI visible
Agent is mid-greeting — candidate sees the visualizer animating in sync
```

### Edge cases

| Case | Behavior |
|---|---|
| Agent state never reaches "speaking" within 15s | Transition loader to error state with retry affordance. Hits existing `session_outcome="error"` attribute publish if engine failed (via `_handle_entrypoint_failure`). |
| Microphone permission denied before /start | Pre-existing pre-check flow gates on mic permission. Loader is reached only after mic is granted. |
| /start returns 502 (AGENT_DISPATCH_FAILED) | Loader never mounts. Error surfaced before LK room entry. |
| LK room join fails / times out | LK SDK fires `disconnected`. Loader transitions to retry. |

### Observability

- Frontend logs: `intro_loader.shown_at`, `intro_loader.dismissed_at` (timestamps for measuring perceived latency)
- Engine-side: existing `audio.tuning_summary.latency.tts_ttfb_ms` already captures the technical latency that the loader hides
- Target: p95 perceived wait ≤ 5s; alert if loader times out >1% of sessions

Copy is in-character (Arjun-persona), not generic "Loading…". Final string is a frontend implementation detail.

---

## Section 4 — Post-phase-transition segue

### Decision summary

- Orchestrator detects the kind transition from behavioral_star → technical_depth.
- New `is_post_phase_transition: bool` flag on `SpeakerInput`.
- New branch in `deliver_question.txt` for the warm-segue framing.
- Precedence rule with `is_post_cap_advance`: cap takes priority.

### Schema additions

```python
# app/modules/interview_runtime/schemas.py
class QuestionConfig(BaseModel):
    # ... existing fields ...
    question_kind: Literal[
        "technical_depth", "behavioral_star", "compliance_binary"
    ]

# app/modules/interview_engine/models/speaker.py
class SpeakerInput(BaseModel):
    # ... existing fields ...
    is_post_phase_transition: bool = False
```

`build_session_config` populates `question_kind` from the DB column (already exists per migration 0026, populated by the bank generator).

### Orchestrator detection logic

```python
# orchestrator.py — instance state init
self._prev_active_question_kind: str | None = None

# orchestrator.py — _run_turn_body, after process_judge_output returns
decision = self._state.process_judge_output(...)
speaker_input = decision.speaker_input

if speaker_input.instruction_kind in (
    InstructionKind.deliver_question,
    InstructionKind.deliver_first_question,
):
    active_idx = self._state.queue_snapshot().active_index
    if active_idx is not None:
        current_kind = self._cfg.stage.questions[active_idx].question_kind
        if (self._prev_active_question_kind is not None
            and current_kind != self._prev_active_question_kind):
            speaker_input = speaker_input.model_copy(
                update={"is_post_phase_transition": True},
            )
        self._prev_active_question_kind = current_kind

await self._stream_speaker_and_say(
    agent=agent, turn_id=turn_id, speaker_input=speaker_input,
)
```

**Why orchestrator-side, not State Engine:** the State Engine is deliberately phase-agnostic. It enforces cap, knockout, validation — not phase boundaries. Adding `question_kind` reads to the State Engine couples it to a concept it doesn't enforce. The orchestrator already weaves SpeakerInput flags (it owns `_prior_speaker_output` for naturalness, for instance). Same conceptual layer.

**When the flag fires and when it doesn't:**

- **Does NOT fire** on the very first question delivery in the session (`_prev_active_question_kind` is `None`; the conditional skips). The intro_brief turn handles the warm framing for the first question; no segue is needed because the candidate hasn't heard a prior question to transition FROM.
- **Does NOT fire** on within-kind advances. Behavioral Q1 → Behavioral Q2 (when the generator emitted 2 behavioral questions) doesn't trip the flag because both are `behavioral_star`. Same for technical Q1 → Q2. The flag fires ONLY at the boundary where `current_kind != prev_kind`, which for v1 means only on the behavioral → technical transition.
- **DOES fire** on the behavioral → technical advance. This is the single transition the v1 design cares about.
- **DOES fire** on any future kind transition (e.g., if `compliance_binary` questions are added later, the technical → compliance boundary would also trigger it). The detection logic is kind-agnostic — it just compares kinds.

### Speaker prompt — `deliver_question.txt` addition

New branch added under the existing `# POST-CAP ADVANCE` section:

```
# POST-PHASE TRANSITION (when is_post_phase_transition is true AND
#                       is_post_cap_advance is false)
The previous question was a background-verification question; we're now
moving into a technical scenario. Open with a brief warm acknowledgement
of what the candidate JUST said, then a soft segue, then the question:
  "Got it — {≤6-word paraphrase of what they covered}. Right —"
  "Solid background — let's dive in."
  "Thanks for the context. Now —"

The acknowledge phrase MUST be ≤6 words and reference a SPECIFIC fact
they mentioned (years, platform, employer). Generic "thanks for that"
is forbidden — sounds rote.

# PRECEDENCE WITH is_post_cap_advance
If is_post_cap_advance is ALSO true (candidate failed to give specifics
on the behavioral question; we're moving on regretfully), that takes
precedence. Use the post-cap segue ("Mm — different scenario now")
instead. We don't celebrate background depth that wasn't there.

[2-3 exemplars]
```

This is the **only** place in the engine that reads `question_kind`. Confirms the architectural property: kind is forensic/scheduling metadata, NOT something the Judge or State Engine acts on.

---

## Section 5 — Failure modes, budget arithmetic, observability

### Failure mode matrix

| Failure | Detection | Behavior |
|---|---|---|
| **Behavioral LLM call fails** during bank generation | Existing `_generate_one_bank` try/except + `BudgetExceededError` | `generation_status_by_kind["behavioral_star"] = "failed"`. Technical call still runs. Bank ships in `reviewing` if technical succeeded. Recruiter retries via per-kind endpoint. |
| **Technical LLM call fails** | Same | `generation_status_by_kind["technical_depth"] = "failed"`. Bank's overall `status` is `failed` (technical is the meat of the screening — behavioral-only doesn't constitute a complete bank). |
| **Both calls fail** | Same | Both `failed`. Existing `transition_to_failed(bank, error=...)` path. |
| **No eligible knockouts in signal_metadata** | `_filter_behavioral_eligible` returns empty | Behavioral call skipped. `generation_status_by_kind["behavioral_star"] = "skipped_no_eligible_signals"`. Bank is technical-only. Engine handles fine. |
| **Engine intro_brief Speaker LLM fails** | Existing `_stream_speaker_and_say` exception | Hard-coded persona-voiced fallback string (see Section 2) — not the generic `fallback_recovery` (would sound odd as an intro). |
| **Engine intro_brief produces empty output** | Existing `_handle_empty_speaker_output` | Same hard-coded persona-voiced fallback. |
| **Frontend loader never dismisses (15s timeout)** | Frontend timer | Show error state with retry. If engine failed, hits existing `session_outcome="error"` attribute path. |
| **Candidate disconnects during intro_brief** | LK `participant_disconnected` | Existing `_wire_participant_disconnect` marks `candidate_disconnected`. `session.timer_started` event never fires. Lifecycle elapsed is 0. |
| **Candidate clicks Start but engine dispatch fails (502)** | Existing `AgentDispatchFailedError` | /start returns 502 before LK room entry. Loader never mounts. Existing path. |
| **Behavioral knockout signal fails mid-interview** | Existing `KnockoutFailure` + `close_polite` policy override | Existing `polite_close` runs. Outcome = `knockout_closed`. No technical questions asked. Forensically clean in audit envelope. |
| **Behavioral question takes longer than budget** | Lifecycle timer | Behavioral and technical share the budget — overrun eats technical time. Acceptable if candidate is engaged; push_back cap kicks in if stalling. |

### Budget arithmetic

```
Stage duration_minutes = 15  (Workato example)

BEHAVIORAL_BUDGET_MIN = 3  (constant for v1; could become per-stage config later)

Behavioral generation budget:
  behavioral_total ≤ 3 min  (sum of estimated_minutes across all behavioral_star
                              questions, 1-2 of them)
  All behavioral questions are mandatory by construction; no optional buffer.

Technical generation budget:
  technical_mandatory_cap = duration_minutes - behavioral_total
                          = 15 - behavioral_total
  technical_total_cap     = technical_mandatory_cap + OPTIONAL_BUDGET_MARGIN_MIN
                          = (15 - behavioral_total) + 5
  Existing validate_llm_output_against_snapshot enforces both per-call.

Frontend timer:
  Shows full 15 min throughout intro + interview.
  Starts ticking on first candidate utterance (session.timer_started event).
  intro_brief speaking time is FREE (~25-35s, doesn't count).
  Behavioral and technical share the 15-min candidate-speaking budget.
```

### Observability additions

| Surface | What | Where |
|---|---|---|
| **Audit event kind** | `session.timer_started` — fires once when first candidate utterance arrives | `orchestrator.py::on_user_turn_completed` (Section 2 lifecycle pause) |
| **DB column** | `stage_question_banks.generation_status_by_kind JSONB` | Migration 0029 |
| **Existing audit payload** | `speaker.call`/`speaker.output` events carry `instruction_kind = "intro_brief"` for intro turn | No new kind needed |
| **Frontend metrics** | `intro_loader.{shown_at, dismissed_at}` for measuring perceived latency | Frontend instrumentation |
| **Existing engine metric** | `audio.tuning_summary.latency.tts_ttfb_ms` already captures intro TTS latency | Existing |

---

## Migrations

### `0029_bank_generation_status_by_kind`

```sql
ALTER TABLE stage_question_banks
ADD COLUMN generation_status_by_kind JSONB NOT NULL DEFAULT '{}';

COMMENT ON COLUMN stage_question_banks.generation_status_by_kind IS
  'Per-question-kind generation status. Shape: {"behavioral_star": status, "technical_depth": status} '
  'where status ∈ {"reviewing", "failed", "skipped_no_eligible_signals"}. '
  'The single canonical bank.status reflects the union (see Section 1 of '
  'docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md).';
```

Backfill is `'{}'` for existing rows; the canonical `bank.status` already conveys the prior state for legacy banks.

No other migrations needed — `stage_questions.question_kind` already exists from migration 0026.

---

## Files touched

### Backend (`backend/nexus/`)

**New:**

- `migrations/versions/0029_bank_generation_status_by_kind.py`
- `prompts/v1/question_bank_ai_screening_behavioral.txt`
- `prompts/v2/engine/speaker/intro_brief.txt`

**Modified:**

- `app/modules/question_bank/actors.py` — `_generate_one_bank` refactor into two calls + `_generate_questions_for_kind` helper + `_filter_behavioral_eligible` + per-kind status writing; `STAGE_TYPE_TO_PROMPT` map keyed by `(stage_type, question_kind)`
- `app/modules/question_bank/models.py` — add `generation_status_by_kind` to `StageQuestionBank` ORM
- `app/modules/question_bank/schemas.py` — add `generation_status_by_kind` to `BankResponse`
- `app/modules/question_bank/router.py` — add `POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/banks/regenerate-kind` endpoint
- `app/modules/question_bank/service.py` — helper for per-kind question wipe + regeneration trigger
- `prompts/v1/question_bank_ai_screening.txt` — minor wording edit (clarify it's now technical-only)
- `prompts/v1/question_bank_common.txt` — no changes anticipated, verify schema discipline is kind-agnostic
- `app/modules/interview_runtime/schemas.py` — add `question_kind` to `QuestionConfig`
- `app/modules/interview_runtime/service.py` — populate `question_kind` in `build_session_config`
- `app/modules/interview_engine/models/speaker.py` — add `intro_brief` to `InstructionKind`; add intro fields + `is_post_phase_transition` to `SpeakerInput`
- `app/modules/interview_engine/speaker/input_builder.py` — populate intro fields when `instruction_kind == intro_brief`
- `app/modules/interview_engine/orchestrator.py` — `on_enter` runs intro_brief Speaker call; lifecycle timer starts in `on_user_turn_completed`; track `_prev_active_question_kind` and set `is_post_phase_transition` on kind transition; hard-coded intro fallback for empty/exception paths
- `app/modules/interview_engine/event_kinds.py` — add `SESSION_TIMER_STARTED`
- `app/modules/interview_engine/audit_events.py` — payload model for `SESSION_TIMER_STARTED` (simple — just wall_ms)
- `prompts/v2/engine/speaker/deliver_question.txt` — add `POST-PHASE TRANSITION` branch + precedence rule
- `prompts/v2/engine/CHANGELOG.md` — log the v2 prompt evolution

### Frontend (`frontend/session/`)

- New loading state in the LK room view (`app/interview/[token]/`), dismissed on `lk.agent.state == "speaking"`
- 15s timeout with error state + retry
- In-character spinner copy
- Frontend logs for `intro_loader.shown_at` / `dismissed_at`

### Tests

- `backend/nexus/tests/question_bank/` — two-call generation, per-kind status writing, per-kind retry endpoint, skip-no-eligible-signals path
- `backend/nexus/tests/interview_engine/` — intro_brief turn in on_enter, lifecycle timer pause, is_post_phase_transition flag set on kind transition, hard-coded intro fallback paths, post-cap precedence over post-phase
- `frontend/session/tests/` — loader shown/dismissed on agent state transitions, 15s timeout path

---

## Testing strategy

### Backend unit tests (per the existing patterns in `tests/`)

- **Bank generator** (`tests/question_bank/`):
  - `test_two_call_generation_happy_path` — both kinds succeed, status shipping
  - `test_behavioral_skipped_when_no_eligible_signals` — JD with no `experience`/`behavioral` knockouts
  - `test_behavioral_failed_technical_succeeds` — bank ships as reviewing with per-kind status reflecting partial failure
  - `test_technical_failed_alone_fails_bank` — bank goes to failed
  - `test_per_kind_retry_endpoint` — wipes only the targeted kind, preserves recruiter-edited
  - `test_budget_split_arithmetic` — technical cap correctly reduced by behavioral total
- **Engine intro/lifecycle** (`tests/interview_engine/`):
  - `test_on_enter_fires_intro_brief_then_first_question` — Speaker called with `intro_brief`, then synthetic JudgeOutput
  - `test_lifecycle_timer_pauses_until_first_utterance` — `_session_started_monotonic` is None during intro
  - `test_session_timer_started_event_emitted_once` — event fires exactly once
  - `test_intro_brief_empty_output_uses_hardcoded_fallback` — not the generic fallback
  - `test_intro_brief_exception_uses_hardcoded_fallback`
- **Engine post-phase-transition** (`tests/interview_engine/`):
  - `test_is_post_phase_transition_flag_set_on_kind_change` — orchestrator detects behavioral→technical
  - `test_is_post_phase_transition_not_set_within_same_kind`
  - `test_is_post_cap_advance_takes_precedence_over_post_phase` — both flags true, prompt selects cap branch

### Integration tests

- Replay-style: synthesize a SessionConfig with 1 behavioral + 2 technical questions, candidate-simulator gives a "solid background" answer to behavioral Q1, verify queue advances to technical Q1, verify `is_post_phase_transition=True` on that turn
- Knockout path: candidate explicitly disclaims experience on behavioral Q1, verify existing `close_polite` policy fires and outcome is `knockout_closed`
- Skipped behavioral: bank has no behavioral_star questions, verify engine plays intro then jumps to technical Q1 with `is_post_phase_transition=False`

### Manual smoke (per `user_solo_dev.md` memory — user IS the gate)

- Real session against the Workato JD context: hear the intro, give a behavioral answer, hear the segue, get the first technical question, full interview
- Real session where candidate disclaims experience in behavioral
- Real session with frontend loader visible

---

## Open questions / future work

These are NOT v1; recording them so they don't get scoped in accidentally:

- **Cross-claim contradiction detection** — Judge prompt extension to flag when a behavioral claim contradicts an earlier claim. Architecture supports it (claims pool is read by the Judge); requires prompt addition + possibly a `ClarifyKind.claim_reconcile` for the Speaker.
- **Per-kind push_back cap calibration** — if data shows behavioral needs a tighter cap (e.g., 1 instead of 2), add `push_back_cap` to `QuestionConfig`.
- **Behavioral question support for `phone_screen` stage** — currently only `ai_screening` gets behavioral. Phone screen prompts could follow the same pattern.
- **Recruiter UI affordance for behavioral vs technical grouping** — frontend work in `frontend/app/`; can be done separately once this lands.
- **Tenant-level disable for behavioral phase** — YAGNI for v1; revisit if a tenant explicitly requests it.
- **`is_post_phase_transition` extension to compliance_binary kind** — same mechanism would apply if/when compliance questions land. No change needed to detect; the existing logic is `current_kind != prev_kind` regardless of which two kinds.

---

## References

- `CLAUDE.md` (root) — enterprise operating standards, anti-leak invariants
- `backend/nexus/CLAUDE.md` — engine module structure, plugin discipline (`app.ai.realtime` as the blessed import site)
- `backend/nexus/prompts/v2/engine/CHANGELOG.md` — v2 prompt history; this work will add a new entry
- `docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md` — the v2 engine baseline this work extends
- `docs/superpowers/specs/2026-05-17-speaker-realism-design.md` — PersonaSpec + voice anchoring used by intro_brief
- `docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md` — clarify_kind dispatch in Speaker (precedent for `is_post_phase_transition` mechanism)
- Migration `0026_question_kind_column` — already added `stage_questions.question_kind`, populated but unused; this work consumes it.
