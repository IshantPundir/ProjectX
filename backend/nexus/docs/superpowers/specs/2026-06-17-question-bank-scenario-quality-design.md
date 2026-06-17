# Question Bank — Scenario Quality (concrete, hands-on questions)

**Date:** 2026-06-17
**Status:** Design — approved, pending implementation
**Branch:** `feat/question-bank-quality`
**Scope:** Question-bank generation **prompt-quality** rewrite + a 3-site schema length bump. No actor/flow/migration change. (The separate generation-reliability bug — `max_retries=1`→4 — is already fixed on this branch, commit `53862e6d`.)

---

## Problem

Generated questions read like a quiz, not a real screen. Two issues (recruiter QA):

1. **No concrete context.** Questions are generic scenario shells — *"A customer wants an
   AI-driven approval workflow in Workato. How would you design the recipe so it scales?"*
   / *"In an integration, how would you store and read the records to keep systems in
   sync?"* — with no specifics. The ideal is concrete and quantified: *"A telecom app has
   over 3,000 automated tests in Selenium and Cypress; execution went from 2 hours to 8
   and releases are slipping. How would you find the bottlenecks and optimize the
   framework?"*
2. **Theory-answerable.** Many questions can be answered from book knowledge — they don't
   verify the candidate has actually *done* the work. (The F1 analogy: knowing everything
   about F1 ≠ being able to drive one.)

### Root cause

The v3 generation prompt is already scenario-oriented and bluff-aware, but:
- Its **"Sayable" bar** optimizes hard for spoken brevity (*"~200 chars, one breath, hard
  cap 240"*), which pushes the model toward short **generic** frames and strips the
  concrete detail (numbers, symptoms, named systems) that makes a question vivid and
  un-fakeable.
- It demands a scenario be **self-contained** but never demands it be **concrete/specific**
  — so "a customer wants X" passes.
- It says "make them DO X" but doesn't steer away from abstract "design X from scratch"
  leads that reward fluent theory.

---

## Goal

Questions that put the candidate inside a **specific, realistic situation** (a number, a
named system, a concrete symptom, a real tension) and that **only a practitioner can
navigate well**. Scales to any JD; principle-based, no JD-specific text.

### Decisions (taken)

- **Invent scenario context freely; the scored criterion stays grounded.** The generator
  may invent realistic situational specifics (fleet size, failure symptom, etc.) — that's
  what makes a scenario vivid. Hard rule: invent the SITUATION, never the CRITERION; the
  skill tested/scored is the pinned `primary_signal`, and invented tools must be plausible
  for the role.
- **Two-sentence lead.** Allow the lead to be two short sentences — one sets the concrete
  situation, one asks the single question. Raise the lead `text` cap `240 → 320`.
  Follow-up `seed_probe`s stay one tight ask (≤240).

---

## Changes

### A. Schema (`app/modules/question_bank/schemas.py`) — raise lead text cap 240 → 320

Three sites (all the LEAD question text; `seed_probe` and `evaluation_hint` unchanged):
- `GeneratedQuestion.text` (line ~84): `max_length=240` → `max_length=320`; update the
  description to "SHORT, single-focus, SPOKEN lead — up to TWO sentences (a concrete
  situation, then one ask)".
- `SingleQuestionOutput.text` (line ~222): `max_length=240` → `320`.
- `UpdateQuestionBody.text` (line ~239): `max_length=240` → `320`.

### B. `prompts/v3/question_bank_common.txt`

**B1 — rewrite the "Sayable" bar (#3).** Replace:
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

**B2 — new section** inserted immediately after the three-bar block (before "# Signals"):
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

**B3 — fix the `seed_probe` cross-reference** in the `follow_ups` section so it no longer
inherits the lead's two-sentence allowance. Replace:
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

### C. `prompts/v3/question_bank_ai_screening.txt` — concreteness in the scored-scenario recipe

Append to recipe item 1 ("SCORED SCENARIOS"), after "make them DO X out loud. One
self-contained spoken scenario per lead; depth ladders into the escalating follow-ups.":
```
   Open each scenario with a CONCRETE, realistic situation — a number, a named system, or a
   specific symptom (invent a plausible one for this role; see the common header's
   "Make the situation CONCRETE" rule). Never a generic shell like "a customer wants X".
```

### D. `prompts/v3/question_bank_critic.txt` — enforce concreteness + the new length

**D1** — header line: replace `Keep each lead a single spoken ask (<=240 chars)` with
`Keep each lead a self-contained spoken ask (up to two sentences, <=320 chars)`.

**D2** — check #9 SPOKEN HYGIENE: replace `each lead is ONE self-contained spoken ask,
<=240 chars.` with `each lead is a self-contained spoken ask — up to two sentences (a
concrete situation + one ask), <=320 chars; one ask only.`

**D3** — add check #10:
```
10. CONCRETE SITUATION -- every scenario lead carries a concrete specific (a number, a
    named system/tool, a symptom, or a real tension). Rewrite generic shells ("a customer
    wants X", "a company wants X", "in an integration, how would you...") into a specific,
    realistic situation. You MAY invent plausible specifics; the scored skill stays the
    primary_signal and the rubric grades the real skill, not the invented scenario.
```

---

## Defect → fix

| Issue | Closed by |
|---|---|
| Generic scenario shells (no context) | B2 (concreteness reject rule), C, D3 |
| Brevity rule strips concreteness | B1 (two-sentence lead, cap 320) + A (schema cap) |
| Theory-answerable questions | B2 (prefer doer-only situations; down-weight abstract design) |
| seed_probe length ambiguity after cap change | B3 |
| Critic doesn't catch generic/over-length | D1–D3 |

---

## Non-goals

- No change to the generation actor, coverage planner, flow, or `question_kind` taxonomy.
- No new prompt version (stays `v3`) and no migration.
- No frontend change. (The recruiter UI already surfaces `primary_signal` per question; any
  grouping/labelling polish is out of scope.)
- No JD-specific tuning.

## Operational notes

- Generation runs in the lean `nexus-worker` (queue `question_bank_generation`);
  `PromptLoader` caches in memory, no hot-reload → **restart `nexus-worker` after editing
  prompts**. Schema change is picked up on worker restart too.

## Validation (manual)

Regenerate the AI-screening bank on the EMM job (stage `e537d1aa…`) and a second,
different-domain job. Confirm:
- each scenario lead opens with a concrete specific (number / named system / symptom /
  tension), not a generic "a customer/company wants X";
- leads may be two sentences but stay sayable (≤320);
- scenarios prefer doer-navigable situations (diagnose a symptom, own a tradeoff/decision)
  over abstract "design X from scratch";
- the scored `primary_signal` is still a real snapshot signal (invented flavor is only
  situational);
- generation still completes (reliability fix holds) and the critic pass doesn't strip the
  new concreteness.
```
