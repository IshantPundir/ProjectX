# Question Bank Scenario-Quality — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated screening questions drop the candidate into a concrete, specific, doer-only situation (number / named system / symptom / tension) instead of a generic theory-answerable shell.

**Architecture:** Prompt-quality rewrite of the v3 question-bank prompts + a 3-site schema length bump (lead `text` 240→320 so a two-sentence lead fits). No actor/flow/migration change. Generation reliability was already fixed on this branch (commit `53862e6d`, `max_retries` 1→4).

**Tech Stack:** Pydantic schemas (`app/modules/question_bank/schemas.py`); plain `.txt` prompts under `prompts/v3/` read by `PromptLoader`; pytest. The quality bar is the highest priority — the bank is the upstream source of truth for the live interview AND the report.

## Global Constraints

- Prompt + schema-length only. No change to the generation actor, coverage planner, flow, `question_kind` taxonomy, or any migration.
- No new prompt version (stays `v3`). No frontend change.
- Lead `text` cap becomes **320**; follow-up `seed_probe` stays **240**.
- Principle-based, scales to all JDs — no JD-specific text in prompts.
- Verbatim edit text is in the spec: `docs/superpowers/specs/2026-06-17-question-bank-scenario-quality-design.md`.
- Generation runs in the lean `nexus-worker` (queue `question_bank_generation`); `PromptLoader` caches in memory, no hot-reload → restart `nexus-worker` after editing prompts/schema.

---

### Task 1: Raise the lead-text length cap 240 → 320 (schema)

**Files:**
- Modify: `app/modules/question_bank/schemas.py` (3 sites)
- Test: `tests/test_question_banks_schemas.py`

**Interfaces:**
- Consumes: `GeneratedQuestion`, `QuestionRubric` from `app.modules.question_bank.schemas`.
- Produces: `GeneratedQuestion.text` / `SingleQuestionOutput.text` / `UpdateQuestionBody.text` now accept up to 320 chars (was 240).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_question_banks_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric


def _valid_generated_question(text: str) -> GeneratedQuestion:
    """Construct a fully-valid GeneratedQuestion with the given lead text."""
    return GeneratedQuestion(
        position=0,
        text=text,
        primary_signal="Microsoft Intune administration & configuration",
        signal_values=["Microsoft Intune administration & configuration"],
        estimated_minutes=4.0,
        is_mandatory=False,
        follow_ups=[
            {
                "dimension": "rollout_specifics",
                "intent": "Verify concrete rollout decisions",
                "seed_probe": "Which enrollment method did you pick, and why that one?",
                "listen_for": ["enrollment method", "tradeoff named"],
            }
        ],
        positive_evidence=[
            "Names a specific enrollment/compliance mechanism",
            "Describes a concrete failure they diagnosed",
            "Owns a decision with a stated tradeoff",
        ],
        red_flags=["Stays abstract with no specifics", "Says 'we' with no recoverable 'I'"],
        rubric=QuestionRubric(
            excellent="Names specific Intune mechanisms and a real tradeoff they owned.",
            meets_bar="Mentions one concrete mechanism and a structured approach.",
            below_bar="Vague, tutorial-level, no specifics.",
        ),
        evaluation_hint="Strong answer names concrete mechanisms and a verified outcome.",
        question_kind="technical_scenario",
    )


def test_generated_question_text_accepts_up_to_320_chars():
    text = "A" * 320
    q = _valid_generated_question(text)
    assert len(q.text) == 320


def test_generated_question_text_rejects_over_320_chars():
    with pytest.raises(ValidationError):
        _valid_generated_question("A" * 321)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_schemas.py -k "text_accepts_up_to_320 or text_rejects_over_320" -v`
Expected: `test_generated_question_text_accepts_up_to_320_chars` FAILS (current cap 240 rejects a 320-char text); the `rejects_over_320` test passes for the wrong reason (240 also rejects 321). Both pass only after the cap is raised.

- [ ] **Step 3: Raise the cap at the three lead-text sites**

In `app/modules/question_bank/schemas.py`:

(a) `GeneratedQuestion.text` — change `min_length=10, max_length=240,` to `min_length=10, max_length=320,` and update its description first line from `"SHORT, single-focus, SPOKEN lead question (~200 chars). One ask — no "` to `"SHORT, single-focus, SPOKEN lead — up to TWO sentences (a concrete situation, then one ask), ~250 chars. One ask — no "`.

(b) `SingleQuestionOutput.text` — change `text: str = Field(..., min_length=10, max_length=240)` to `text: str = Field(..., min_length=10, max_length=320)`.

(c) `UpdateQuestionBody.text` — change `text: str | None = Field(default=None, min_length=10, max_length=240)` to `text: str | None = Field(default=None, min_length=10, max_length=320)`.

> Do NOT change the `seed_probe` cap (line ~70, stays `max_length=240`) or the `evaluation_hint` caps (stay `max_length=200`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_schemas.py tests/question_bank/test_schemas_v3.py -v`
Expected: PASS (new length tests pass; no existing schema test asserted the old 240 cap).

- [ ] **Step 5: Commit**

```bash
git add app/modules/question_bank/schemas.py tests/test_question_banks_schemas.py
git commit -m "feat(question_bank): raise lead-text cap 240 -> 320 for two-sentence scenarios

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Concreteness + two-sentence rules in the v3 prompts

**Files:**
- Modify: `prompts/v3/question_bank_common.txt`
- Modify: `prompts/v3/question_bank_ai_screening.txt`
- Modify: `prompts/v3/question_bank_critic.txt`

**Interfaces:**
- Consumes: nothing new.
- Produces: prompt files read by `PromptLoader(version="v3")` in `question_bank/actors.py`. No code contract change.

- [ ] **Step 1: Rewrite the "Sayable" bar in `question_bank_common.txt`**

Replace this exact block:
```
3. Sayable. About 200 characters, one or two short sentences a person says in one breath
   (hard cap 240). Spell numbers in words ("five years", not "5 yrs") — TTS speaks it.
   Conversational register: how a sharp human screener actually phrases it out loud.
   Warm and conversational, never an interrogation — a casual register surfaces
   inconsistencies better than a grilling, and the "what did YOU personally do" probe
   (which matters more where 'we'-framing is cultural) must still sound friendly.
```
with:
```
3. Sayable, in up to TWO short sentences — one sets a CONCRETE situation, then one asks
   the SINGLE question (see "Make the situation CONCRETE" below). Target ~250 characters,
   hard cap 320: two sentences a person can say and a listener can hold on first hearing.
   Spell numbers in words ("three thousand", not "3,000") — TTS speaks it. Conversational
   register: how a sharp human screener actually phrases it out loud. Warm and
   conversational, never an interrogation — a casual register surfaces inconsistencies
   better than a grilling, and the "what did YOU personally do" probe (which matters more
   where 'we'-framing is cultural) must still sound friendly.
```

- [ ] **Step 2: Insert the "Make the situation CONCRETE" section in `question_bank_common.txt`**

Immediately BEFORE the line `# Signals (the role's criteria — bind every question to them)`, insert:
```
# Make the situation CONCRETE — specifics, not a generic shell

A scenario screens for real skill only when it drops the candidate into a SPECIFIC,
realistic situation. Every scenario lead must carry at least one concrete anchor: a number
(a fleet size, a volume, a latency, a count), a named system or tool the role uses, a
specific SYMPTOM ("intermittent failures although the logs show success"), or a real
tension/stake ("releases are slipping"). Generic shells with no specifics — "a customer
wants X", "a company wants X", "in an integration, how would you…" — are a REJECT: they
read as a textbook prompt, and a candidate with only book knowledge answers them as well
as a real practitioner.

You MAY invent these specifics. The JD rarely states a fleet size or a failure symptom, so
author a plausible, realistic one for this role — that invention is exactly what makes the
question vivid and hard to fake. The one hard rule: invent the SITUATION, never the
CRITERION. The skill being tested and scored is the pinned `primary_signal`; any invented
tool or system must be one this role would plausibly use, and the rubric must grade the
real skill — never whether the candidate recognises your made-up scenario.

Prefer situations only someone who has DONE the work can navigate: a concrete failure to
diagnose (symptom → hypothesis → what they would verify), a real tradeoff the situation
forces, or a decision they had to own. Down-weight abstract "how would you design X from
scratch" leads — those reward fluent theory. Knowing the rules of a thing is not the same
as having done it; write the question only a doer can answer well.

```

- [ ] **Step 3: Fix the `seed_probe` cross-reference in `question_bank_common.txt`**

Replace this exact block:
```
  - `seed_probe` — a single-ask spoken question the interviewer says aloud, under the same
    spoken constraints as `text` (one ask, ≤240 chars, conversational).
```
with:
```
  - `seed_probe` — a single-ask spoken question the interviewer says aloud: ONE tight ask,
    ≤240 chars, conversational (the lead's two-sentence allowance does NOT extend to
    probes — a probe is one clean question).
```

- [ ] **Step 4: Add the concreteness line to `question_bank_ai_screening.txt`**

In recipe item `1. SCORED SCENARIOS`, replace this exact block:
```
   approaches. NOT "have you used X" — make them DO X out loud. One self-contained spoken
   scenario per lead; depth ladders into the escalating follow-ups.
```
with:
```
   approaches. NOT "have you used X" — make them DO X out loud. One self-contained spoken
   scenario per lead; depth ladders into the escalating follow-ups. Open each scenario with
   a CONCRETE, realistic situation — a number, a named system, or a specific symptom (invent
   a plausible one for this role; see the common header's "Make the situation CONCRETE"
   rule) — never a generic shell like "a customer wants X".
```

- [ ] **Step 5: Update `question_bank_critic.txt` — header line, check #9, add check #10**

(a) Replace `Keep each lead a single spoken ask (<=240 chars), and never let evaluator-only phrasing leak into spoken fields.` with `Keep each lead a self-contained spoken ask (up to two sentences, <=320 chars), and never let evaluator-only phrasing leak into spoken fields.`

(b) Replace check #9:
```
9. SPOKEN HYGIENE -- each lead is ONE self-contained spoken ask, <=240 chars.
```
with:
```
9. SPOKEN HYGIENE -- each lead is a self-contained spoken ask: up to two sentences (a
   concrete situation + one ask), <=320 chars; one ask only.
```

(c) Immediately after check #9, add:
```
10. CONCRETE SITUATION -- every scenario lead carries a concrete specific (a number, a
    named system/tool, a symptom, or a real tension). Rewrite generic shells ("a customer
    wants X", "a company wants X", "in an integration, how would you...") into a specific,
    realistic situation. You MAY invent plausible specifics; the scored skill stays the
    primary_signal and the rubric grades the real skill, not the invented scenario.
```

- [ ] **Step 6: Verify all three prompts still load**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; L=PromptLoader(version='v3'); [print(n, len(L.get(n))) for n in ('question_bank_common','question_bank_ai_screening','question_bank_critic')]"`
Expected: three names with positive char counts, no exception.

- [ ] **Step 7: Run prompt-loader + question_bank unit tests for regressions**

Run: `docker compose run --rm nexus pytest tests/test_prompt_loader.py tests/test_question_banks_actors.py tests/question_bank/ -q`
Expected: PASS (prompt edits don't touch code paths the unit tests exercise; they mock the LLM seam).

- [ ] **Step 8: Commit**

```bash
git add prompts/v3/question_bank_common.txt prompts/v3/question_bank_ai_screening.txt prompts/v3/question_bank_critic.txt
git commit -m "feat(question_bank): concrete, doer-only scenarios + two-sentence lead

Common prompt: rewrite the 'Sayable' bar to allow a two-sentence lead (concrete
situation + one ask, cap 320) and add a 'Make the situation CONCRETE' section
(specific number/system/symptom/tension; invent the situation, never the
criterion; prefer doer-navigable situations over abstract design). ai_screening:
each scored scenario opens concrete. critic: enforce concreteness + new length.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Live quality validation (multiple diverse JDs)

No automated test — prompt quality is proven by live generations + manual review. Its own
task because a reviewer could approve the prompt text yet reject the observed output. Because
the bank drives both the interview and the report, validate on MORE than one role.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker so it loads the new prompts + schema**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: `nexus-worker` recreated, healthy (`docker compose ps nexus-worker`).

- [ ] **Step 2: Regenerate the EMM AI-screening bank and review**

In the recruiter app, regenerate the bank for the EMM job stage
(`/jobs/11650922-79c6-43f6-8f26-4638303e1fbf/questions?stage=e537d1aa-bbb1-4642-8f70-6e6b633d59c6`).
Read the questions (UI, or the SQL below) and confirm EACH scenario lead:
- opens with a concrete specific (number / named system / symptom / tension) — NOT "a
  customer/company wants X" or a bare "in an integration…";
- is at most two sentences and sayable (≤320 chars);
- is doer-navigable (diagnose a symptom, own a tradeoff/decision) rather than abstract
  "design X from scratch";
- has `primary_signal` that is a real snapshot signal (invented detail is only situational).

> Read questions: `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -F'||' -c "select position, question_kind, length(text), text from stage_questions q join stage_question_banks b on b.id=q.bank_id where b.stage_id='e537d1aa-bbb1-4642-8f70-6e6b633d59c6' order by position;"`

- [ ] **Step 3: Regenerate a second, different-domain bank and review**

Pick a different job in a different domain (e.g. the Workato integration job `ce6dad9a-…`)
and regenerate its AI-screening bank. Apply the same checklist from Step 2. This guards
against the prompt over-fitting one role and confirms concreteness generalizes.

- [ ] **Step 4: Confirm generation reliability + critic both hold**

For both banks, confirm generation reached a terminal state (`reviewing`/`generated`) with
NO `generation_error`, and that the critic pass did not strip the new concreteness (compare
question text before/after self-review if visible). If a bank fails generation, capture the
error and stop (regression in the reliability fix).

> `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -F'|' -c "select stage_id, status, coalesce(left(generation_error,200),'(none)') from stage_question_banks where stage_id in ('e537d1aa-bbb1-4642-8f70-6e6b633d59c6','2ea4f4a3-4199-4403-9e2b-744284c8233f');"`

- [ ] **Step 5: Record the result**

If both banks pass the checklist, note completion. If any question is still a generic shell,
theory-answerable, or over-length, capture the offending lead + which rule it violates and
iterate on the prompt (back to Task 2).

---

## Self-Review

**Spec coverage:** Schema cap 240→320 at 3 sites (spec A) → Task 1. "Sayable" rewrite (B1) → Task 2 Step 1. "Make the situation CONCRETE" section (B2) → Step 2. seed_probe cross-ref fix (B3) → Step 3. ai_screening concreteness (C) → Step 4. critic D1/D2/D3 → Step 5. Defect→fix table → Task 3 checklist. Operational restart note → Task 3 Step 1. Multi-JD validation (per the user's "highest quality / drives interview + report") → Task 3 Steps 2-4. Non-goals (no actor/flow/migration/version/frontend change) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Task 1 test is complete and runnable (full valid GeneratedQuestion construction). All prompt edits give exact old→new blocks. Validation steps enumerate concrete pass/fail conditions + the exact SQL to read output.

**Type consistency:** No new types/signatures. `GeneratedQuestion`/`QuestionRubric` used as imported; field names (`text`, `seed_probe`, `primary_signal`) consistent; lead cap 320 / probe cap 240 stated identically in schema (Task 1) and prompts (Task 2). Prompt names and version `v3` consistent throughout.
