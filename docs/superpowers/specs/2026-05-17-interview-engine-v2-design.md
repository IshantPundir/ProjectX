# Interview Engine v2 — Reliable, Auditable, World-Class Interviewer

**Status:** Draft for user review
**Date:** 2026-05-17
**Author:** Ishant Pundir (with Claude)
**Scope:** `backend/nexus/app/modules/interview_engine/`, `backend/nexus/prompts/v1/engine/` → `prompts/v2/engine/`, `backend/nexus/tests/interview_engine/`
**Predecessors:**
- `2026-05-07-interview-engine-structured-agent-design.md` (original structured agent)
- `2026-05-08-interview-engine-judge-speaker-redesign-design.md` (Judge/Speaker split)
- `2026-05-12-engine-simplification-design.md` (orchestrator strip-down; explicitly deferred Judge prompt revision as non-goal)

---

## 1. Problem statement

A deep diagnostic of the Judge prompt + a live session (`engine-events/70c126b4-1c5b-4d2d-bee0-2e1259f40a5d.json`, 2026-05-17) surfaced a structural set of issues across the Judge LLM, the Judge prompt, the Speaker prompts, and the Speaker input-builder. The architecture surrounding the Judge (schema-first output, post-LLM validators, fallback synthesizer, State Engine downgrades) matches current LLM-as-Judge best practice. The prompt and several smaller code surfaces have drifted away from it.

The most consequential gaps documented in the diagnostic:

1. **No free-form `reasoning` field in `JudgeOutput`.** Under strict JSON-schema decoding, this is documented to cost ~25 pp of reasoning quality on tasks of comparable complexity (Tam et al., "Let Me Speak Freely?", arXiv:2408.02442).
2. **Prompt-code drift on push_back + concrete observations.** Prompt §3 claims the validator rejects this; the validator was relaxed 2026-05-12. Models reading the prompt are told a lie.
3. **A literal schema rule references `signal_metadata.type`, which is not in the input.** The "first I don't know" disambiguation rule in §3 cannot be evaluated by the Judge from its inputs.
4. **`candidate_claims` field is structurally orphan.** No dedicated prompt section; zero claims emitted across the diagnostic session despite rich biographical content.
5. **No signal fan-out rule.** A single utterance touching 3+ signals consistently produces observations on only one signal.
6. **Bluff-catch behavior is undocumented and prompt-only.** The product needs probe-failure-on-mandatory to result in knockout. Today this happens because the Judge LLM coincidentally interprets meta-confessions as no-experience disclosures — exactly opposite to what the prompt §4 says. Behavior works for the wrong reason.
7. **Speaker prompts violate their own anti-enumeration and single-ask rules** when bank_text contains parallel verb lists (Sev 3-5 from the diagnostic).
8. **Speaker `input_builder.clarify` always passes the main `active_question.text`,** even when the candidate is clarifying a follow-up probe (Sev 2 from the diagnostic).
9. **No measurement layer.** Every prompt edit is a blind change. "When Better Prompts Hurt" (arXiv:2601.22025) documents how iterative prompt edits without held-out evals routinely degrade unrelated behaviors.
10. **`repeat` intent is handled by 30+ lines of literal-token rules in the Judge prompt** — a deterministic regex problem dressed as LLM classification.

This spec ships **interview-engine v2** as a single coordinated branch covering all six workstreams that address these issues.

---

## 2. Goals & non-goals

### Goals

1. **Reliability.** Eliminate the false-knockout / illegal-failure-observation / prompt-code-drift class of bugs by encoding invariants in code (validators, State Engine deterministic rules), not just prompt prose.
2. **Auditability.** Every Judge decision carries explicit reasoning, persisted in the audit envelope, end-to-end reviewable.
3. **Measurability.** A hand-curated eval harness gates every Judge/Speaker prompt change. No v2-era prompt edit ships without the suite passing.
4. **Conformance to LLM-as-Judge best practice (2025/2026).** Schema-first output with leading `reasoning` field, single-canonical-rule discipline, decision tree at top of prompt, binary/low-resolution categorical outputs, explicit termination conditions.
5. **Design-intent alignment.** The bluff-catch behavior is encoded deterministically in the State Engine (not interpretive prompt prose), with full audit visibility.

### Non-goals

- No change to the LiveKit audio pipeline (Sarvam + ai-coustics + MultilingualModel — covered by `2026-05-12-engine-simplification-design.md`).
- No change to the State Engine's existing downgrade logic (push_back+concrete → probe, knockout policy override, etc.) — only *additions* (meta_confession promotion).
- No change to `interview_runtime` config assembly or result recording (apart from projecting `signal.type` through `QuestionConfig.signal_metadata`).
- No change to the candidate-facing session UI.
- No change to the orchestrator's continuation-coalescing / continuation-watcher logic.
- No change to Judge model selection (`gpt-5.4-mini` stays).
- **No new vendor dependency.** No LangSmith, DeepEval, Promptfoo. Eval is pytest fixtures only.
- No change to RLS, tenant isolation, or audit-event discipline.

---

## 3. Architectural overview

The pipeline shape stays the same: `STT → Orchestrator → Judge LLM → State Engine → Speaker LLM → TTS`. Six layered changes:

```
                                          ┌──────────────────────────────────────┐
[NEW F] Orchestrator pre-filter           │  Workstream F (orchestrator/)        │
        - regex match "repeat"/"again"    │  Lightweight intent gate BEFORE       │
        - if matched → skip Judge,        │  Judge call. Returns synthetic         │
          synthesize repeat action        │  JudgeOutput for trivial cases.        │
                                          └──────────────────────────────────────┘

[NEW B] Judge schema additions            ┌──────────────────────────────────────┐
        - JudgeOutput.reasoning (first)   │  Workstream B (models/judge.py +     │
        - TurnMetadata.meta_confession    │  judge/input_builder.py)             │
        - ActiveSignalMeta.type           │  Pydantic-level changes only.        │
        - drop prompt-code drift on       │  No prompt prose required to use.    │
          push_back+concrete validator    └──────────────────────────────────────┘

[NEW C] Judge prompt v2                   ┌──────────────────────────────────────┐
        - prompts/v2/engine/judge.system  │  Workstream C (prompts/v2/)          │
        - ~350 lines (was 567)            │  Full rewrite: decision tree at top, │
        - decision tree at top            │  CANDIDATE-CLAIMS section, signal    │
        - dedicated claims section        │  fan-out rule, meta_confession rule, │
        - signal fan-out rule             │  empty-obs examples, single canonical│
        - meta_confession rule            │  statements, changelog externalized. │
        - empty-obs worked examples       └──────────────────────────────────────┘
        - external changelog file

[NEW D] Speaker prompts v2                ┌──────────────────────────────────────┐
        - prompts/v2/engine/speaker/*     │  Workstream D                         │
        - deliver_question.txt:           │  Targeted Sev 3-5 fixes (anti-enum,   │
          stronger anti-enumeration       │  single-ask). NO Speaker reasoning    │
        - clarify.txt: stronger anti-enum │  field (Speaker is constraint-driven, │
        - push_back.txt: single-ask hard  │  ~30 tokens out, not LMSF-sensitive). │
          enforcement                     └──────────────────────────────────────┘

[NEW E] Speaker input_builder fix         ┌──────────────────────────────────────┐
        - clarify-on-probe passes probe   │  Workstream E (speaker/input_builder)│
          text, not main question         │  Pure code change. ~10 lines.        │
                                          └──────────────────────────────────────┘

[NEW A] Eval harness                      ┌──────────────────────────────────────┐
        - tests/interview_engine/judge/   │  Workstream A                        │
          eval/fixtures/ (~50 JSON files) │  pytest -m prompt_quality, opt-in.   │
        - test_judge_eval.py runner       │  Hits real OpenAI. ~$0.50 per full   │
        - @pytest.mark.prompt_quality     │  run. Side-by-side v1 vs v2 diff.    │
        - dev tool, NOT CI gate           │  Bisect tool when behavior drifts.   │
                                          └──────────────────────────────────────┘

[NEW] State Engine addition (in B/C)      ┌──────────────────────────────────────┐
       - meta_confession promotion        │  meta_confession + mandatory +        │
         deterministic rule                │  push_back_count >= 1 + no path →    │
                                          │  acknowledge_no_experience with       │
                                          │  primary_signal_of(active_q).         │
                                          │  Audited as state.action_override.    │
                                          └──────────────────────────────────────┘
```

Two invariants from v1 are preserved verbatim: (i) anti-leak architecture (Speaker never sees rubric content via the `speaker/input_builder.py` projection); (ii) RLS / tenant isolation / audit-event discipline.

---

## 4. Workstream A — Judge eval harness

**Goal:** A regression-gating eval harness that lets every subsequent prompt/schema edit be measured, not guessed at.

### 4.1 Location & shape

```
backend/nexus/tests/interview_engine/judge/eval/
├── fixtures/
│   ├── 001_bare_greeting.json
│   ├── 002_what_is_term.json
│   ├── 003_strong_multi_signal_answer.json
│   ├── 004_thin_answer.json
│   ├── 005_probe_failure_mandatory.json         # bluff catch
│   ├── 006_explicit_no_experience.json
│   ├── 007_dont_know_followup_meta.json         # meta_confession
│   ├── 008_injection_attempt.json
│   ├── 009_off_topic_salary_question.json
│   ├── 010_strong_with_tradeoffs.json
│   ├── ... ~30 at v2 ship, grow to ~50 in production
│   └── README.md                                # corpus growth playbook
├── corpus.py                                    # loads fixtures, expected assertions
├── runner.py                                    # calls real OpenAI via JudgeService
└── test_judge_eval.py                           # pytest entry point
```

### 4.2 Fixture file shape

```json
{
  "id": "005_probe_failure_mandatory",
  "description": "Candidate engaged earlier but cannot answer a concrete probe on a mandatory signal — bluff-catch scenario from session 70c126b4 turn 8.",
  "tags": ["bluff_catch", "meta_confession", "mandatory_signal"],
  "judge_input": { /* full JudgeInputPayload */ },
  "expected": {
    "next_action": "push_back",
    "turn_metadata": {
      "candidate_meta_confession": true
    },
    "observations_min_count": 0,
    "observations_max_count": 1,
    "forbidden_failure_observations": true,
    "expected_reasoning_substrings": ["meta", "push_back"]
  },
  "source": "session_70c126b4_turn8",
  "labeled_by": "ishant",
  "labeled_at": "2026-05-17"
}
```

### 4.3 Assertion framework (in `corpus.py`)

- `next_action` — exact match (hard assertion).
- `turn_metadata` — subset match (only assert flags the fixture cares about; ignore others).
- `observations` — `min_count`, `max_count`, `expected_signals_subset` (must hit these signal_values), `forbidden_failure_observations` (no `→failed` with `anchor_id ≥ 0`).
- `expected_reasoning_substrings` — soft check (warning, not failure) — reasoning field should contain these keywords.
- `forbidden_actions` — exclusionary check (e.g., "MUST NOT be acknowledge_no_experience").
- `forbidden_meta_flags` — exclusionary check on turn_metadata.

### 4.4 The runner

```python
# tests/interview_engine/judge/eval/runner.py

async def run_fixture(
    fixture: EvalFixture,
    *,
    prompt_version: Literal["v1", "v2"],
) -> EvalResult:
    """Loads fixture's JudgeInputPayload, calls real JudgeService with the
    configured prompt version, collects output + latency + cost, runs
    assertions, returns EvalResult.
    """
```

Driven by `JUDGE_PROMPT_VERSION` env var (default `v2`). Supports `--compare` mode in `test_judge_eval.py` for side-by-side v1 vs v2 diffs.

### 4.5 The pytest entry point

```python
# tests/interview_engine/judge/eval/test_judge_eval.py

@pytest.mark.prompt_quality
@pytest.mark.parametrize("fixture", load_all_fixtures(), ids=lambda f: f.id)
async def test_judge_decision_matches_expected(fixture: EvalFixture):
    result = await run_fixture(
        fixture,
        prompt_version=os.getenv("JUDGE_PROMPT_VERSION", "v2"),
    )
    assert result.passed, format_failure(result)
```

**Marker discipline:** `prompt_quality` is registered in `pyproject.toml` and skipped by default. Opt-in via `pytest -m prompt_quality`. Per user preference (`feedback_manual_agent_testing.md`): dev tool, not CI gate.

### 4.6 Initial corpus

- ~20 fixtures extracted from the 3 existing `engine-events/*.json` audit logs (`70c126b4`, `2115a63a`, `7970e91c` — 6-15 Judge calls each).
- ~10 LLM-synthesized fixtures for action classes that don't appear in real sessions yet (`end_session`, `repeat`, abusive `redirect`).
- Total: ~30 at v2 ship.

### 4.7 Corpus growth playbook (`fixtures/README.md`)

> When a Judge decision in a real session surprises you:
> 1. Copy the audit envelope's `judge.call.input_summary` into a new fixture.
> 2. Label the expected output explicitly (next_action, turn_metadata flags, observation shape).
> 3. Commit. The corpus grows from your own real testing.

Target: ~50 fixtures by end of first month of v2 in production.

### 4.8 Cost

50 fixtures × ~10k input tokens × ~$1.25/1M (gpt-5.4-mini input) + ~500 output × ~$5/1M ≈ **$0.65 per full run**. A/B (v1+v2) ≈ $1.30. Negligible.

### 4.9 Exit criteria

- ~30 fixtures at v2 ship, all of which pass on v2 prompt.
- A/B run (`JUDGE_PROMPT_VERSION=v1` vs `=v2`) on same fixtures produces a reviewable diff with no v2 regressions vs v1.
- Pre-merge gate documented (see §10).

---

## 5. Workstream B — Judge schema + code changes

### 5.1 Scope

Pydantic-level additions to enable Workstream C (prompt rewrite) and the meta_confession State Engine rule. All changes are backwards-compatible with v1-era audit envelopes (new fields default to safe values on load).

### 5.2 Files touched

- `app/modules/interview_engine/models/judge.py`
- `app/modules/interview_engine/judge/input_builder.py`
- `app/modules/interview_runtime/schemas.py` (project `signal.type` through to `QuestionConfig.signal_metadata`)
- `app/modules/interview_engine/state/engine.py` (the deterministic promotion — see §6)

### 5.3 Change 1 — `JudgeOutput.reasoning` (first field)

```python
class JudgeOutput(BaseModel):
    # Free-form analysis. Written BEFORE every structured field —
    # autoregressively grounds the decisions that follow. Per
    # arXiv:2408.02442 ("Let Me Speak Freely"), this defends against
    # the ~25 pp reasoning-quality drop strict JSON schema otherwise
    # imposes. Persisted in audit envelope. NEVER shown to candidate.
    reasoning: str = Field(min_length=20, max_length=2000)

    observations: list[Observation] = Field(default_factory=list, max_length=10)
    candidate_claims: list[ClaimEntry] = Field(default_factory=list, max_length=5)
    next_action: NextAction
    next_action_payload: NextActionPayload
    turn_metadata: TurnMetadata = Field(default_factory=TurnMetadata)
```

Backwards compat: when loading historical audit envelopes that pre-date this field, the eval harness's fixture loader fills `reasoning=""`. The Pydantic strict-schema rewriter in `judge/service.py` (`_judge_output_text_format`) will include the field automatically.

### 5.4 Change 2 — `TurnMetadata.candidate_meta_confession`

```python
class TurnMetadata(BaseModel):
    candidate_disclosed_no_experience: bool = False
    candidate_disclosed_knockout: bool = False
    candidate_off_topic: bool = False
    candidate_abusive: bool = False
    candidate_attempted_injection: bool = False
    candidate_wants_to_end: bool = False
    candidate_social_or_greeting: bool = False
    # NEW: candidate admitted they cannot answer THIS question (not the
    # SIGNAL). Distinct from candidate_disclosed_no_experience. The
    # State Engine deterministically promotes to acknowledge_no_experience
    # when conditions warrant (mandatory + push_back_count >= 1 + no path).
    candidate_meta_confession: bool = False
```

### 5.5 Change 3 — `ActiveSignalMeta.type`

```python
class ActiveSignalMeta(BaseModel):
    value: str
    type: Literal["experience", "credential", "competency", "behavioral"]  # NEW
    knockout: bool
    priority: Literal["required", "preferred"]
```

Requires plumbing the `type` field from `signal_snapshot.signals[*].type` (already present in DB) through `QuestionConfig.signal_metadata` in `interview_runtime/schemas.py`. Mechanical change, no semantic risk.

### 5.6 Change 4 — Push-back + concrete drift reconciliation

The validator `_check_push_back_alignment` was relaxed 2026-05-12 to permit `push_back` paired with `concrete`/`strong` observations (the State Engine's `inverse_quality_gate` handles policy). The Judge prompt §3 still threatens rejection. **Resolution:** delete the misleading sentence from `v2` Judge prompt. No code change. Documented inline in the v2 prompt CHANGELOG.

### 5.7 Change 5 — New validator `_check_meta_confession_consistency`

```python
@model_validator(mode="after")
def _check_meta_confession_consistency(self) -> "JudgeOutput":
    """meta_confession is a CLASSIFICATION flag. Judge does NOT decide
    knockout; State Engine does. Forbidden when meta_confession=true:
    acknowledge_no_experience and polite_close — those are State Engine
    overrides, not Judge calls. Every other action (push_back, advance,
    probe, clarify, redirect, repeat, end_session) is permitted."""
    if not self.turn_metadata.candidate_meta_confession:
        return self
    forbidden = {NextAction.acknowledge_no_experience, NextAction.polite_close}
    if self.next_action in forbidden:
        raise ValueError(
            f"candidate_meta_confession=true is a CLASSIFICATION flag; "
            f"do NOT decide knockout. State Engine promotes when warranted. "
            f"Got {self.next_action.value!r}. Use push_back (typical), or "
            f"any non-knockout action."
        )
    return self
```

### 5.8 Change 6 — New validator `_check_greeting_action_alignment`

Fixes session-evidence finding (Turn 2 had `social_or_greeting=true` + `clarify` — should have been `redirect`).

```python
@model_validator(mode="after")
def _check_greeting_action_alignment(self) -> "JudgeOutput":
    """candidate_social_or_greeting=true requires next_action=redirect.
    Greetings are never clarify per judge prompt §3 redirect canonical rule."""
    if not self.turn_metadata.candidate_social_or_greeting:
        return self
    if self.next_action != NextAction.redirect:
        raise ValueError(
            f"candidate_social_or_greeting=true requires next_action=redirect; "
            f"got {self.next_action.value!r}."
        )
    return self
```

### 5.9 Change 7 — Drop the `uncertain` action proposal

Recommendation, no code. The existing `clarify` + `redirect` + `push_back` actions span the ambiguity space adequately. Adding a 10th NextAction would increase State Engine surface for marginal benefit. Reopen if v2 produces obviously force-fit Judge outputs (see §13, O5).

### 5.10 Exit criteria

- Schema changes land with all 5 model validators (3 existing + 2 new) covered by unit tests (positive + negative cases each).
- `ActiveSignalMeta.type` plumbed through and surfaced in the audit envelope.
- Strict-mode `oneOf → anyOf` patch in `judge/service.py` unaffected.

---

## 6. State Engine — `meta_confession` deterministic promotion

### 6.1 Scope

A single new function in `app/modules/interview_engine/state/engine.py`, wired into the existing override pipeline. Runs AFTER the Judge call validates and BEFORE the Speaker call.

### 6.2 The rule

```python
def _maybe_promote_meta_confession(
    *,
    judge_output: JudgeOutput,
    active_question: QuestionConfig,
    question_state: QuestionState,
    remaining_probes: dict[str, str],
    ledger: SignalLedger,
) -> ActionOverride | None:
    """Bluff-catch promotion.

    Trigger: candidate_meta_confession=true (Judge classified) AND
    active question is mandatory AND push_back_count >= 1 AND no
    remaining probes on this question AND the question's primary
    signal (highest weight) is uncovered (coverage in {none, partial}).
    """
    if not judge_output.turn_metadata.candidate_meta_confession:
        return None
    if not active_question.is_mandatory:
        return None
    if question_state.push_back_count < 1:
        return None
    if remaining_probes:
        return None  # let probes run first
    primary_signal = max(
        active_question.signal_metadata, key=lambda s: s.weight,
    )
    cov = ledger.snapshots.get(primary_signal.value)
    if cov and cov.coverage == CoverageState.sufficient:
        return None  # already proven; don't reverse-rule it
    return ActionOverride(
        kind="meta_confession_knockout",
        new_action=NextAction.acknowledge_no_experience,
        new_payload=AcknowledgeNoExperiencePayload(
            failed_signal_value=primary_signal.value,
        ),
        reason=(
            f"meta_confession + mandatory + "
            f"push_back_count={question_state.push_back_count} + "
            f"no_probes_remain + primary_signal_uncovered"
        ),
    )
```

### 6.3 Audit

Emits a `state.action_override` event with `kind="meta_confession_knockout"` and the full reason. The audit envelope shows BOTH Judge classification (e.g., `push_back`) and State Engine override (`→ acknowledge_no_experience`). Fully reviewable.

### 6.4 Interaction with existing overrides

- Runs AFTER `inverse_quality_gate` (push_back+concrete → probe). If `inverse_quality_gate` downgrades to `probe`, that means probes remain and the meta_confession check would short-circuit on `remaining_probes` anyway. No conflict.
- Runs BEFORE `knockout_policy` override. If `engine_knockout_policy=close_polite`, the promoted `acknowledge_no_experience` chains to `polite_close` per existing rules. If `record_only`, the failure is recorded and the next mandatory is dispatched.

### 6.5 Exit criteria

- Unit tests for all 5 condition combinations: meta_confession ✓/✗, mandatory ✓/✗, push_back_count 0/1/2, remaining_probes empty/non-empty, primary_signal coverage none/partial/sufficient.
- One integration test demonstrating the full audit envelope shape on a meta_confession promotion.

---

## 7. Workstream C — Judge prompt v2

### 7.1 Location

`backend/nexus/prompts/v2/engine/judge.system.txt` (new file). v1 stays at `prompts/v1/engine/judge.system.txt` for emergency rollback.

### 7.2 Target

~350 lines (was 567, ~38% cut via deduplication, not feature removal).

### 7.3 New structure

```
JUDGE — INTERVIEW DECISION ENGINE (v2)

§0  ROLE + OUTPUT CONTRACT          (10 lines — what you are, what you emit)
§1  DECISION TREE                   (35 lines — IN-ORDER priority list, moved
                                     from old §8 to TOP.)
§2  INPUT FIELDS                    (45 lines — what you receive, including
                                     ActiveSignalMeta.type and the new
                                     meta_confession field semantics.)
§3  REASONING FIELD                 (20 lines — NEW. How to write reasoning:
                                     2-4 sentences, what was said, which
                                     signals/anchors hit, why this action.
                                     150-300 token target.)
§4  NEXT ACTIONS                    (90 lines — ONE entry per action, single
                                     canonical statement. No re-derivations.
                                     Includes new meta_confession rule and
                                     simplified acknowledge_no_experience.)
§5  OBSERVATIONS                    (60 lines — quality grading + fan-out
                                     rule (NEW) + failure-obs rule ONCE
                                     (was 5×) + anchor_id discipline.)
§6  CANDIDATE CLAIMS                (35 lines — NEW dedicated section.
                                     When to extract, claim_topic conventions,
                                     2 worked examples.)
§7  ANTI-LEAK                       (15 lines — canonical single statement.)
§8  WORKED EXAMPLES                 (40 lines — 6 examples covering:
                                     advance/probe/clarify/push_back/
                                     meta_confession/empty-obs greeting.)

CHANGELOG (separate file)           prompts/v2/engine/CHANGELOG.md
                                    "session XYZ caused rule Y" notes moved
                                    here — not in the system prompt body.
```

### 7.4 Key content changes (relative to v1)

1. **§1 Decision tree at top.** Priority list moves from line 555 to ~30-60. Reasoning models attend strongly to early prompt content. Includes tiebreaker: "when in doubt, prefer the more conservative transition; default observation to `partial→partial` not `partial→sufficient`."

   *Source: OpenAI GPT-5.1 guide + Eugene Yan's LLM-evaluator structure guidance — early prompt content is where reasoning-class models commit their search space.*

2. **§3 Reasoning field — new section.** Tells the Judge how to write the new `reasoning` field: 2-4 sentences, name the candidate's utterance shape, name which signals/anchors were touched, name the action choice + brief why. Forbids restating the rubric. Caps at ~300 tokens to bound latency.

3. **§4 NEXT ACTIONS — single canonical statement per action.** Each action gets one entry. `push_back` appears once (was 4×). `acknowledge_no_experience` appears once (was 5×). The "first I don't know disambiguation" sub-rule that referenced the non-existent `signal_metadata.type` is rewritten to use the new field that Workstream B plumbs through.

4. **NEW: `meta_confession` rule under §4.**
   ```
   META-CONFESSION (sets candidate_meta_confession flag):
   - Trigger: candidate uses "I don't know how to answer this question",
     "I'm not sure how to answer this", "I can't think of a specific example",
     OR similar — about the QUESTION itself, after engaging earlier.
   - Distinct from no_experience (about the SIGNAL).
   - Set turn_metadata.candidate_meta_confession = true.
   - Emit next_action = push_back with the appropriate reason_code
     (typically `missing_specifics` if the candidate engaged with the
     topic but couldn't produce a concrete instance; `vague_answer` if
     they only echoed the question back). The State Engine
     deterministically decides whether to promote to knockout based on
     mandatory + push_back_count + remaining_probes. Do NOT decide
     knockout yourself.
   - FORBIDDEN: setting meta_confession=true AND emitting
     acknowledge_no_experience or polite_close. Validator will reject.
   ```

5. **§5 Observations — signal fan-out rule (NEW).**
   ```
   FAN-OUT: One utterance often touches multiple signals. Emit one
   observation per signal touched, up to the active question's full
   signal list. Example: a candidate describing "MuleSoft + DataWeave +
   exponential backoff + idempotency on order IDs" touches at least 4
   signals (iPaaS, JSON/XML transformation, reliability, validation).
   Emit all 4. Do NOT collapse multi-signal answers into one observation
   on a single signal.
   ```

6. **§5 Failure-obs rule stated ONCE.** Was 5× repetition in v1 that did not prevent the bug. Schema validators are the gate, not prose.

7. **§6 CANDIDATE CLAIMS — new dedicated section.**
   - Extract a claim when the candidate names a tool/stack/employer/years/responsibility scope/team size.
   - `claim_topic` conventions: `primary_stack`, `years_experience`, `current_role`, `responsibility_scope`, `team_context`, `domain_experience`.
   - Emit 1-3 per turn when warranted; 0 is fine if no biographical content.
   - 2 worked examples (one from a Q1 strong answer, one from a deflection).

8. **§7 ANTI-LEAK — single canonical statement.** Was repeated in 4 places. Schema enforces architecturally; prompt states the rule once.

9. **§8 Worked examples — 6 total, with positional balance.**
   - A: strong multi-signal answer → advance, ≥2 observations, fan-out demonstrated.
   - B: explicit no-experience → `acknowledge_no_experience` with `anchor=-1`.
   - C: meta_confession on a follow-up → `push_back` + meta_confession flag (NEW).
   - D: thin answer → `push_back`, `missing_specifics`, quality=thin.
   - E: bare greeting → `redirect`, EMPTY observations + EMPTY claims (defeats position-bias toward always-emitting-observations — 4 of 5 v1 examples emitted observations; this rebalances).
   - F: prompt injection → `redirect` with injection flag.

10. **CHANGELOG externalized** to `prompts/v2/engine/CHANGELOG.md`. Session-reference notes that v1 sprinkled in the system prompt body move to a file the model never sees. Frees ~30-50 lines per call.

11. **Absolutes audit.** Every `NEVER` / `MUST` / `DO NOT` re-tagged as either (a) true invariant (kept) or (b) judgment call (downgraded to `prefer` / `by default`). Target: cut absolutes ~50%.

12. **Drop dead references.** `evaluation_hint` either used (added to §3 reasoning section as "consider when grading observation quality") or removed from input. `time_remaining_seconds ≤ floor` floor specified as 60s.

### 7.5 Exit criteria

- v2 file lands in `prompts/v2/engine/judge.system.txt` at target length (~350 lines).
- `AIConfig.judge_prompt_version` env var added (default `v2`).
- Eval harness ≥ 95% pass on v2.
- A/B vs v1 on same fixtures shows no regressions.

---

## 8. Workstream D — Speaker prompts v2

### 8.1 Location

`backend/nexus/prompts/v2/engine/speaker/*` — all 9 files copied from v1. 3 substantively edited, 6 mechanically updated. `AIConfig.speaker_prompt_version` env var added (default `v2`).

### 8.2 Files edited substantively

**`deliver_question.txt`** — anti-enumeration tightening:
- Add explicit ANTI-PATTERN section with the Q2 violation from session 70c126b4: bank_text `"assess, stabilize, and standardize"` → wrong output preserves all three verbs; right output picks ONE.
- Strengthen rule: "if bank_text contains a 3-verb conjunction (X, Y, AND Z) describing the ask, pick the BROADEST single verb (usually the first). The candidate will naturally cover the others; do NOT seed them."
- Same word cap (≤25), same single-sentence rule.

**`clarify.txt`** — anti-enumeration tightening + accept probe context:
- Add ANTI-PATTERN with the Turn 5 violation: clarify rephrased "assess risks, stop the bleeding, bring to consistent approach" — three rubric criteria.
- Strengthen rule: when rephrasing, pick ONE concrete scenario + ONE final ask. The Speaker MUST NOT enumerate three approach verbs even if bank_text contained them.
- NEW: handle probe-context clarify (the Workstream E fix means bank_text on clarify-after-probe is now the probe text, not the main question). Add: "if bank_text is short (<30 words) and reads as a follow-up question rather than a main question, rephrase it as the follow-up — do NOT widen back to the main topic."

**`push_back.txt`** — single-ask hard enforcement:
- Add ANTI-PATTERN with the Turn 7 violation: "Can you walk through one integration you'd stabilize first, including specific checks and rollback steps?" stacks 3 asks.
- Tighten: "ONE specific ask per turn. If `push_back_reason_code` is `missing_specifics`, ask for ONE concrete instance. If `unanswered_subquestion`, ask for ONE missed sub-part — not 'and also X and Y'."
- Add WORKED RIGHT example for the missing_specifics → "Walk me through one integration you'd stabilize first." (full stop, no rollback ask).

### 8.3 Files mechanically updated

- `_preamble.txt` (no logic changes; persona/anti-leak section)
- `deliver_first_question.txt`
- `deliver_probe.txt`
- `redirect.txt`
- `polite_close.txt`
- `acknowledge_no_experience.txt`
- `repeat.txt` (already correct per 2026-05-08 spec)

### 8.4 No Speaker reasoning field

The Speaker is a 30-token-out scaffold-driven LLM call with strong word caps. Adding a reasoning field would 5× the output tokens for marginal benefit. LMSF effect is most pronounced when output schema is complex; Speaker's output is a single string. Recommendation only; no code.

### 8.5 Exit criteria

- All 9 v2 Speaker files land in `prompts/v2/engine/speaker/`.
- `AIConfig.speaker_prompt_version` env var added (default `v2`).
- Speaker prompt-loadable test (`tests/interview_engine/speaker/test_speaker_prompt_loadable.py`) extended to cover v2 files.

---

## 9. Workstream E — Speaker `input_builder` clarify-on-probe fix

### 9.1 Scope

Pure code change in `app/modules/interview_engine/speaker/input_builder.py`. ~10 lines. Fixes Sev 2 from the diagnostic.

### 9.2 The change

Today, `build_speaker_input` for `instruction_kind == InstructionKind.clarify` always sets `bank_text = active_question.text`. The fix: when the most-recent agent utterance was a probe, pass the probe text instead.

```python
elif instruction_kind == InstructionKind.clarify:
    # NEW: if the candidate is clarifying a probe, rephrase the PROBE,
    # not the main question. The Speaker's clarify.txt v2 handles short
    # bank_text as "rephrase the follow-up" — do not widen back to main.
    active_state = queue.active_state()
    if active_question and active_state and active_state.probes_asked_ids:
        last_probe_id = active_state.probes_asked_ids[-1]
        idx = int(last_probe_id)
        if 0 <= idx < len(active_question.follow_ups):
            bank_text = active_question.follow_ups[idx]
    if bank_text is None:
        bank_text = active_question.text if active_question else None
```

### 9.3 Exit criteria

- Unit tests in `tests/interview_engine/speaker/test_input_builder.py`: clarify-after-probe passes probe text; clarify-not-after-probe passes main question text.

---

## 10. Workstream F — Orchestrator regex pre-filter for `repeat`

### 10.1 Scope

A small `_maybe_synthesize_repeat()` helper added to `app/modules/interview_engine/orchestrator.py`, called BEFORE the Judge call. ~40 lines including pattern + negative guards.

### 10.2 Why

Judge prompt §3 currently does literal-token pattern-matching ("contains the literal word 'repeat' OR 'again' in a question shape") — a regex problem dressed as classification. A deterministic pre-filter is faster (0 ms instead of ~3 s), cheaper (no LLM call), more reliable, and frees Judge attention for actual judgment.

### 10.3 The function

```python
_REPEAT_PATTERN = re.compile(
    r"\b("
    r"repeat (that|it|the question|please)"
    r"|say (that|it) again"
    r"|what (was|did you say) (that|the question) again"
    r"|one more time"
    r"|sorry,? again\??"
    r"|come again\??"
    r")\b",
    re.IGNORECASE,
)

def _maybe_synthesize_repeat(utterance: str) -> JudgeOutput | None:
    """Pre-filter for `repeat` intent.

    Returns a synthetic JudgeOutput when the candidate clearly asked
    to hear the last agent utterance again. Returns None for everything
    else, in which case the Judge runs normally.

    Negative guards (return None even on match):
    - utterance also contains "explain", "rephrase", "what do you mean",
      "I don't understand" → that's clarify, not repeat (Judge prompt §3
      tie-breaker preserved).
    - utterance is >40 words → unlikely a pure repeat request; let Judge see it.
    """
    if len(utterance.split()) > 40:
        return None
    if re.search(
        r"\b(explain|rephrase|what do you mean|i don't understand)\b",
        utterance,
        re.IGNORECASE,
    ):
        return None
    if not _REPEAT_PATTERN.search(utterance):
        return None
    return JudgeOutput(
        reasoning=(
            "Pre-filter: candidate asked to hear the last turn again. "
            "Deterministic intent classification — Judge skipped."
        ),
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )
```

### 10.4 Wire-up

Before the Judge call site in the orchestrator, check `_maybe_synthesize_repeat(utterance)`. If non-None, skip the Judge call, emit `judge.synthetic` audit event with `reason="pre_filter_repeat"`, proceed to the State Engine + Speaker with the synthesized output.

### 10.5 Judge prompt §4 `repeat` entry shrinks

```
REPEAT
- Most repeat intents are handled by the orchestrator pre-filter; you
  receive `repeat`-shaped utterances only when the pre-filter declined
  (long utterance or mixed-intent).
- When you do see them, emit next_action=repeat per the existing rules.
```

### 10.6 Exit criteria

- 4 eval fixtures exercise the pre-filter boundary (positive: "repeat that", "one more time"; negative: "can you repeat AND explain?", long utterance with "again" inside).
- Unit tests in `tests/interview_engine/test_orchestrator.py` cover positive, negative-guard, and empty-utterance cases.

---

## 11. Data flow & audit trail

```
candidate utterance (STT final)
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ Orchestrator                                                │
│   ├─ build TranscriptEntry, write llm.message.added         │
│   ├─ [NEW F] _maybe_synthesize_repeat(utterance)            │
│   │    ├─ matched → emit judge.synthetic audit, skip Judge  │
│   │    └─ no match → continue                               │
│   └─ build JudgeInputPayload (now includes signal.type)     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼ (skipped if pre-filter fired)
┌─────────────────────────────────────────────────────────────┐
│ Judge LLM call (gpt-5.4-mini, v2 prompt)                    │
│   emits: { reasoning (NEW), observations, claims,           │
│            next_action, payload, turn_metadata              │
│              (now includes meta_confession) }               │
│   audit: judge.call.input_summary + judge.call.output       │
│   validators: schema + 5 cross-field (3 existing + 2 new):  │
│     - discriminator alignment                               │
│     - no_experience ↔ action coupling                       │
│     - push_back ↔ no_experience incompatibility             │
│     - meta_confession ↔ forbidden actions (NEW)             │
│     - social_or_greeting ↔ redirect alignment (NEW)         │
│   on validation_error → fallback synthesizes `clarify`      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ State Engine                                                │
│   apply observations to ledger                              │
│   existing overrides:                                       │
│     - inverse_quality_gate (push_back+concrete → probe)     │
│     - knockout_policy (close_polite)                        │
│   [NEW] _maybe_promote_meta_confession()                    │
│     mandatory + push_back_count≥1 + no_probes_remain        │
│     + primary_signal_uncovered → ack_no_experience          │
│   audit: state.snapshot + state.action_override (if fired)  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Speaker LLM call (gpt-5.4-nano, v2 prompts)                 │
│   input includes: kind, bank_text, last_utt, recent_turns,  │
│   claims_pool, persona, candidate_name, failed_signal,      │
│   turn_metadata, push_back_reason_code, recent_reply_starts,│
│   is_post_cap_advance                                       │
│   bank_text on clarify-after-probe = PROBE text (NEW E)     │
│   audit: speaker.input + speaker.call + speaker.output      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
                          TTS → candidate
```

**Audit envelope changes:**

- `judge.call.output.reasoning` — new field, fully audited. Reviewers can see WHY the Judge chose what it did.
- `judge.call.output.turn_metadata.candidate_meta_confession` — new flag in the audit shape.
- `judge.synthetic` events now carry `reason="pre_filter_repeat"` (was only used for fallbacks).
- `state.action_override` events get a new `kind` value: `meta_confession_knockout` (alongside existing `inverse_quality_gate`, `knockout_policy`, etc.).

**Backwards compatibility for old engine-events files:** the eval harness fixture loader fills `reasoning=""` for v1-era audit envelopes when constructing JudgeInputPayloads. v1-era files have no `meta_confession` field — defaults to `False` on Pydantic load.

---

## 12. Testing strategy

| Layer | Tests | When run |
|---|---|---|
| Unit — `models/judge.py` | All 5 validators (3 existing + 2 new). Each has positive + negative cases. `JudgeOutput.reasoning` length bounds. | Default suite |
| Unit — `judge/input_builder.py` | `ActiveSignalMeta.type` propagates correctly from `QuestionConfig`. Stable field ordering preserved. | Default suite |
| Unit — `state/engine.py` | `_maybe_promote_meta_confession()` — all 5 condition combinations (mandatory ✓/✗, push_back_count 0/1/2, remaining_probes empty/non-empty, primary_signal coverage none/partial/sufficient, meta_confession ✓/✗). | Default suite |
| Unit — `orchestrator.py` | `_maybe_synthesize_repeat()` — positive matches (each pattern), negative matches (mixed-intent, long utterance), empty utterance. | Default suite |
| Unit — `speaker/input_builder.py` | Clarify-after-probe passes probe text; clarify-not-after-probe passes main question text. | Default suite |
| Integration — `tests/interview_engine/judge/test_service.py` | `JudgeOutput.reasoning` round-trips through Responses API strict mode (mocked). Validator failure path returns synthesized fallback. | Default suite |
| Integration — `tests/interview_engine/test_orchestrator_composition.py` | Pre-filter → State Engine path produces correct audit shape (no `judge.call` event, `judge.synthetic` event present, `state.snapshot` follows). | Default suite |
| Eval (NEW) — `tests/interview_engine/judge/eval/test_judge_eval.py` | ~30 fixture-based real OpenAI calls at v2 ship; ~50 within a month. Field-level assertions per fixture. | Opt-in: `pytest -m prompt_quality` |
| Eval A/B (NEW) | `JUDGE_PROMPT_VERSION=v1 pytest -m prompt_quality` and `=v2` — diff results via `--compare` mode. | On-demand (pre-merge of v2; periodic on prompt edits) |
| Replay (NEW) — small CLI | `python -m app.modules.interview_engine.tools.replay <session_id>` re-runs every Judge call in a past `engine-events/*.json` with current v2 prompt, prints diff vs. historical. | On-demand dev tool |

### 12.1 Pre-merge gate for v2 cutover

1. Default suite passes (`docker compose run nexus pytest`).
2. Eval suite passes ≥ 95% of fixtures on v2 (`JUDGE_PROMPT_VERSION=v2 pytest -m prompt_quality`).
3. Eval A/B shows v2 strictly better or equal on every fixture (no regressions vs v1) — manual review of the diff output.
4. Manual session — at least one live agent session run end-to-end on v2 via `docker compose up` + frontend session app.

---

## 13. Big-bang cutover plan

Per user decision: all 6 workstreams ship together on one branch (`feature/interview-engine-v2`).

### 13.1 Branch layout

Single feature branch: `feature/interview-engine-v2` (already cut from main at `001b472`).

Six logical commit clusters within the branch, **in dependency order** (later clusters consume earlier ones):

1. `schema: Judge reasoning field + meta_confession + signal type + validators` (Workstream B — must land first; eval corpus references the new fields)
2. `state: meta_confession promotion + new override audit event` (Workstream B continuation — same module surface, separated for review hygiene)
3. `eval: add Judge eval harness + initial corpus` (Workstream A — corpus uses v2 schema shapes)
4. `prompts(v2): Judge prompt rewrite + Speaker prompts v2 + CHANGELOG` (Workstreams C + D — eval harness validates each)
5. `code: Speaker input_builder clarify-on-probe fix` (Workstream E)
6. `code: orchestrator regex pre-filter for repeat intent` (Workstream F)

### 13.2 Cutover sequence

1. Land all 6 commit clusters on the branch in the order above.
2. Run the default test suite — must pass.
3. Run the eval suite on v2 — must hit ≥ 95% pass.
4. Run eval A/B (v1 vs v2) on the same fixtures — manually inspect diff, no regressions allowed.
5. Run at least one live session — verify audit envelope shape is correct, no crashes, agent behavior subjectively sane.
6. Merge branch to `main`. `AIConfig.judge_prompt_version` defaults to `v2`; `speaker_prompt_version` defaults to `v2`.
7. **Rollback path (revised 2026-05-17):** The originally-planned "env-var flip to v1" rollback is no longer available — the v1 engine prompts (`prompts/v1/engine/`) were deleted at merge time because v1 is structurally incompatible with v2 schema (the new required `JudgeOutput.reasoning` field causes v1 Judge outputs to fail Pydantic validation, with the fallback synthesizer firing on every turn). If v2 ships a regression that requires rollback, the path is `git revert <v2-merge-commit>` on `main` and redeploy. The JD pipeline + question-bank generation v1 prompts (`prompts/v1/jd_*.txt`, `prompts/v1/question_bank_*.txt`, etc.) are unrelated and remain in place.

### 13.3 No DB migration

All persistence (sessions, knockout_failures, audio_tuning_summary) is JSONB; new audit fields just appear.

---

## 14. Risks & open questions

### 14.1 Risks

1. **Latency growth.** Adding `reasoning` (~100-300 output tokens) is realistically expected to add **500-2000ms** to Judge p95 — `gpt-5.4-mini` typically streams at 40-150 tokens/sec depending on prompt + reasoning load. Current Judge budget is 10s wall-clock (5s per attempt × 2 + 250ms wait); v2 still fits with headroom. **But:** the real-time AI budget per CLAUDE.md is 1200ms P50 / 1500ms P95 *end-to-end*, of which Judge is one segment. If the new reasoning field pushes the Judge call past ~1500ms p95, the end-to-end budget will be missed. Mitigations, in order: (a) tighten `reasoning` max_length 2000 → 800 in v2 §3 prompt guidance; (b) drop `reasoning_effort` from default `medium` → `low` (per OpenAI-5.1 guidance: most workflows succeed at `low`); (c) accept the latency cost — `auditability` is a goal of v2, and the per-segment p95 was already slack in v1. Decision deferred to first production measurement (O1).

2. **Eval fixture quality.** ~30 hand-labeled fixtures at v2 ship is a small corpus; false-positive eval passes are possible. Mitigation: corpus growth playbook (every surprising real-session decision becomes a fixture); minimum target of 50 by end of first month of v2 in production.

3. **Cross-validator surface.** Going from 3 to 5 model validators on `JudgeOutput` increases the chance that a legitimate Judge output trips a validator and triggers the `clarify` fallback. Mitigation: each new validator has both positive and negative unit tests covering the edge of its check; eval harness surfaces production regressions.

4. **State Engine `meta_confession_knockout` is a NEW way to end a session early.** A false-positive promotion ends an interview unfairly. Mitigation: 4 conjunctive conditions (mandatory + push_back_count≥1 + no_probes_remain + primary_signal_uncovered) — all must hold. False-positive surface is narrow. Eval fixtures #005, #007, and a counter-example fixture explicitly cover the boundary.

5. **Pre-filter regex false negatives.** A repeat-intent utterance the regex misses gets handled by the Judge (slower but correct — graceful degradation). False positives are riskier: a non-repeat utterance flagged as repeat would replay the wrong content. Mitigation: negative guards in the pre-filter (explicit "explain"/"rephrase" exclusions, word-count cap) + 4 eval fixtures specifically exercising the boundary.

6. **Big-bang cutover risk** (user's explicit acceptance). Mitigation: eval harness as the bisect tool; 6 logical commit clusters on the branch (so `git log --oneline` on the merge shows the boundaries); env-var rollback path.

7. **Validator-rejection loses Judge observations.** When either new validator (meta_confession-action coupling, greeting-action coupling) rejects, the existing fallback (`judge/fallback.py::synthesize_fallback(validation_error)`) emits an empty `clarify` JudgeOutput. Any observations / claims the Judge had already extracted on that same turn are lost. The candidate's answer-quality evidence on that turn is forfeit. Mitigation today: this only happens when the Judge's output is internally inconsistent (a buggy turn), so losing the observations is arguably the right call. Mitigation later (post-v2 if observed in production): teach the fallback synthesizer to PRESERVE the observations + claims and only override the offending field (e.g., flip `social_or_greeting=false` and re-validate). Out of scope for v2.

### 14.2 Open questions (resolve in implementation, not design)

- **O1. `reasoning` field length.** Spec says `min_length=20, max_length=2000` chars. Realistic target is probably 150-500 chars. Tighten after first 100 real session reasoning outputs are inspected.

- **O2. `meta_confession` worded triggers.** The spec lists a few example phrasings. The v2 prompt §4 needs to land the canonical list — curate from real session logs once the corpus grows.

- **O3. Speaker `recent_reply_starts` payload size.** Currently passes ~3-4 strings. With anti-repetition tightening, consider 5-6. Decide during Workstream D.

- **O4. Fixture corpus split between extracted-from-sessions and LLM-synthesized.** Spec targets ~20 extracted + ~10 synthesized. Refine if extraction yields fewer good candidates than expected (the 3 sessions we have are all from the same job, so cross-domain coverage is weak — may need more LLM synth).

- **O5. `uncertain` action revisit.** Currently rejected in §5.9. If after 2 weeks of v2 we see Judge outputs that obviously force-fit ambiguous cases into `clarify`/`redirect`, reopen.

- **O6. `evaluation_hint` resolution.** v1 input field is dead. v2 either uses it (add to Judge prompt §3 reasoning section as "consider when grading observation quality") or removes it from `QuestionConfig`. Recommend: USE in v2.

---

## 15. Sources & references

### LLM-as-Judge research applied to this design

- Tam et al. (2024), "Let Me Speak Freely? A Study on the Impact of Format Restrictions on Performance of Large Language Models." arXiv:2408.02442. Motivates §5.3 (`reasoning` field).
- Zheng et al. (2023), "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." NeurIPS 2023, arXiv:2306.05685. Position-bias / verbosity-bias defenses applied in §7.4 (worked example E balances position bias) and §4.3 (observation quality grading defends verbosity bias).
- Liu et al. (2023), "G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment." EMNLP 2023, arXiv:2303.16634. Reason-before-score pattern applied in §5.3.
- Gu et al. (2024), "A Survey on LLM-as-a-Judge." arXiv:2411.15594. General methodology.
- "When 'Better' Prompts Hurt: Evaluation-Driven Iteration." arXiv:2601.22025. Motivates §4 (eval harness as gate for any prompt edit).
- OpenAI GPT-5.1 Prompting Guide (cookbook.openai.com/examples/gpt-5/gpt-5-1_prompting_guide). Motivates §7.4 absolutes audit + decision-tree-at-top.

### Prior in-repo specs

- `docs/superpowers/specs/2026-05-07-interview-engine-structured-agent-design.md`
- `docs/superpowers/specs/2026-05-08-interview-engine-judge-speaker-redesign-design.md`
- `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`
- `docs/superpowers/specs/2026-05-17-conversational-continuation-design.md`

### Diagnostic source

The findings driving this spec come from the diagnostic conducted 2026-05-17 on session `engine-events/70c126b4-1c5b-4d2d-bee0-2e1259f40a5d.json` (job: Sr. Integration Engineer / Workato, candidate: Punar). Six Judge calls, three confirmed defects (illegal failure observation, narrow signal focus, false anti-pattern routing).

---

## 16. Cluster G — Non-mandatory question selection + reverse-rule guard (added 2026-05-17)

After v2's first live session (commit `e000ce5`, Punar), two product gaps surfaced:

1. The engine stopped after Q2 (last mandatory) with 11 minutes of stage budget unused and 3 non-mandatory questions never asked. Bank-generator emits non-mandatory questions; runtime never consumed them.
2. Edge case: explicit no-experience disclosure on a knockout signal already proven on a mandatory question would reverse-rule and close the session.

### 16.1 Non-mandatory selection

`QuestionQueue` gains `next_pending_question_id(*, signal_coverage)` returning `(id, is_mandatory)` or `None`. Selection rule:
1. Mandatory pending in position order (unchanged for mandatory).
2. When mandatory queue empty, non-mandatory pending in position order, skipping any whose signals are all already at `coverage=sufficient`.
3. Return `None` → polite_close fires.

No per-question time gate. Bank-generator owns time planning at generation; runtime trusts the bank. Existing `time_remaining_seconds ≤ 60 + partial → polite_close` rule handles in-flight termination.

`QuestionState` gains `signal_values: list[str]` (default empty, backward-compatible). `QuestionQueue.from_initial` extracts this from the input dict alongside `question_id`, `is_mandatory`, `follow_ups`.

`StateEngine` gains `next_pending_question() -> tuple[str, bool] | None` public accessor (delegates to `self._queue.next_pending_question_id(signal_coverage=self._ledger.snapshot().snapshots)`). The legacy `next_pending_mandatory_id()` is kept with a deprecation notice for internal fallback use.

`JudgeInputPayload.next_pending_mandatory_question_id` renamed → `next_pending_question_id`. New field `next_pending_question_is_mandatory: bool | None` added for audit clarity. Both fields updated in the orchestrator, fallback synthesizer, and all 30 eval fixtures.

### 16.2 Reverse-rule guard

`state/engine.py` explicit-no-experience knockout_policy override now:
1. Captures `pre_turn_signal_snapshots` at the TOP of `process_judge_output`, before any observations are applied.
2. Filters `knockout_failures_this_turn` into `actionable_failures` (those where no signal was already `sufficient` in `pre_turn_signal_snapshots`).
3. If all failures are on already-proven signals, records `knockout_policy_reverse_rule_skipped` audit warning and does NOT close.
4. If `actionable_failures` is non-empty, fires `knockout_policy_override` and closes as before.

The pre-turn snapshot is required because the ledger applies `sufficient→failed` transitions before the knockout guard runs — reading the post-turn snapshot would always see `failed`, never catching the reverse-rule case.

Matches the equivalent guard in `_maybe_promote_meta_confession` (which already had this guard at the `meta_confession_knockout` level).

### 16.3 Files changed

- `app/modules/interview_engine/models/queue.py` — `QuestionState.signal_values` field
- `app/modules/interview_engine/state/queue.py` — `from_initial` dict parsing + `next_pending_question_id` method
- `app/modules/interview_engine/state/engine.py` — `signal_values` plumbed through, `next_pending_question()` accessor, pre-turn snapshot capture, reverse-rule guard
- `app/modules/interview_engine/judge/input_builder.py` — field rename + new `is_mandatory` field
- `app/modules/interview_engine/judge/service.py` — `next_pending_question_resolver` param rename
- `app/modules/interview_engine/judge/fallback.py` — `next_pending_question` param rename
- `app/modules/interview_engine/orchestrator.py` — call site updated
- `app/modules/interview_engine/agent.py` — resolver call site updated
- `prompts/v2/engine/judge.system.txt` — §1 step 7+8, §2 INPUT FIELDS, §4 ADVANCE + POLITE_CLOSE updated
- `tests/interview_engine/judge/eval/fixtures/*.json` — all 30 existing rebased + 2 new (031, 032)
- `tests/interview_engine/judge/eval/runner.py` — resolver lambda updated
- `tests/interview_engine/judge/test_input_builder.py` — updated for renamed param + new field
- `tests/interview_engine/judge/test_service.py` — resolver param rename
- `tests/interview_engine/judge/test_fallback.py` — `next_pending_question` param rename
- `tests/interview_engine/state/test_queue.py` — 8 new tests for `next_pending_question_id`
- `tests/interview_engine/state/test_meta_confession_promotion.py` — 2 new reverse-rule guard tests
