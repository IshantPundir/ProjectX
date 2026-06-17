# Bank Coverage & Distinctness — Deterministic Invariants

**Date:** 2026-06-18
**Status:** Design — approved, pending implementation
**Branch:** `feat/question-bank-quality`
**Scope:** Deterministic code (`invariants.py` + unit tests) as the core, plus supporting prompt edits (`jd_signal_extraction.txt`, `question_bank_ai_screening.txt`, `question_bank_critic.txt`). No planner-logic, schema, or migration change.

---

## Problem

QA of two regenerated banks (after the signal de-dup pass) found two residuals the prompt-only
layer didn't close:

1. **Workato coverage regression:** two wt3 must-have skills — *programming language
   (Java/Python/Ruby)* and *database (RDBMS/NoSQL)* — were left UNSCORED, while the
   `integration` skill was covered twice (a `technical_scenario` AND the `project_deepdive`,
   both `primary_signal = Integration`, even the same Workday→Salesforce scenario).
2. **EMM redundancy:** Q1 ("diagnose why compliant-device Outlook access broke") ≈ Q2 ("enforce
   so noncompliant devices lose Outlook") — same competency, because the signals
   `compliance/CA` and `policy-enforcement` didn't merge.

### Root cause

- **Signal granularity feeds a fixed scored-slot budget.** The coverage planner gives one
  scored slot per must-cover skill up to `slot_budget ≈ floor(duration / min_per_slot)`.
  Over-split signals (Workato: 8 skills) overflow the budget → skills become `secondary_only`
  (probed live, not gap-scored). Under-merged signals (EMM) create redundant scored skills.
- **The deterministic gate is missing a distinctness invariant.** `check_bank_invariants`
  enforces *coverage* (`uncovered_required_primary`: each must-cover skill is SOME question's
  `primary_signal`) but nothing forbids **two scored questions from sharing a `primary_signal`**.
  So a scenario and the deep-dive can both be `primary = integration` — a wasted slot + a
  duplicate question — while language/DB go unscored. This is exactly the kind of countable
  invariant the codebase keeps in code ("the LLM critic can't be trusted to count").

---

## Goal

Guarantee — deterministically — that the scored set of an AI-screening bank is **broad and
non-redundant**: every scored question owns a distinct skill, and the single deep-dive is used
to widen coverage when skills overflow the budget. Scalable to any JD; structural rules, no
JD-specific text.

### Decision (taken): Hybrid deep-dive
The `project_deepdive`'s `primary_signal` MUST be an overflow (`secondary_only`) must-have skill
when one exists (breadth); when none exists (skill-poor JD, every must-cover already has a
scenario), it is unconstrained / project-agnostic.

---

## Design

### Part A — Signal-merge tightening (prompt: `prompts/v2/jd_signal_extraction.txt`)

Reinforce the existing "same knowledge/work = same competency" merge test on the two borderline
patterns it currently misses, kept principle-based (no JD-specific text). Add to the
"Merge same-competency requirements" section:

> **Facets of one skill are one competency.** Knowing a protocol/format vs building with it,
> monitoring a control vs enforcing it, designing vs operating the same system — these are the
> SAME competency (a candidate shows them with the same knowledge); emit ONE signal naming the
> facets, not one per facet. **A platform/tool/category that IS the role's primary instrument is
> not a separate skill from using it** — fold a bare "automation/middleware/platform" umbrella
> into the specific skill that exercises it, never a standalone catch-all.

This lowers over-subscription at the source. [LLM, best-effort — the deterministic gate is the guarantee.]

### Part B — Deterministic distinctness invariants (code: `app/modules/question_bank/invariants.py`) — CORE

Two new pure, unit-tested checks added to `check_bank_invariants` (ai_screening only),
following the established `Violation` → critic-re-pass → `hard_repair` pattern.

**B1 — `duplicate_scenario_primary` (hard-repairable):** no two `technical_scenario` questions
may share a `primary_signal`.
```
scenarios = [q for q in questions if q.question_kind == "technical_scenario"]
dup_primaries = {sig for sig in (q.primary_signal for q in scenarios)
                 if [q.primary_signal for q in scenarios].count(sig) > 1}
if dup_primaries:
    Violation("duplicate_scenario_primary",
      f"More than one technical_scenario shares these primary_signals: {sorted(dup_primaries)}. "
      "Each scenario must own a DISTINCT skill. Rewrite the duplicate to a skill not yet covered "
      "(prefer a secondary-only skill from the coverage plan), or drop it.",
      hard_repairable=True)
```
`hard_repair` last-resort: drop the later/non-mandatory duplicate scenario (dedupe by
`primary_signal`, keep the earliest / mandatory), re-pack positions. (Critic re-pass runs first
and is preferred — it rewrites to an uncovered skill rather than losing a slot.)

**B2 — `deepdive_primary_uncovered_overflow` (not hard-repairable → critic):** when the plan has
overflow skills, the deep-dive must score one of them.
```
if plan is not None and plan.secondary_only:
    scenario_primaries = {q.primary_signal for q in questions
                          if q.question_kind == "technical_scenario"}
    uncovered_overflow = [s for s in plan.secondary_only if s not in scenario_primaries]
    dd = [q for q in questions if q.question_kind == "project_deepdive"]
    if uncovered_overflow and dd and dd[0].primary_signal not in set(uncovered_overflow):
        Violation("deepdive_primary_uncovered_overflow",
          f"Overflow must-have skills are unscored ({uncovered_overflow}); the single "
          "project_deepdive must take ONE of them as its primary_signal so it is scored. "
          f"Set the deep-dive's primary_signal to one of: {uncovered_overflow}.",
          hard_repairable=False)
```
`uncovered_overflow` = overflow must-have skills no scenario already scored. Because the
scenarios normally cover the `required_primaries` (disjoint from `secondary_only`), forcing the
deep-dive's primary into this set also guarantees it differs from every scenario — so this single
rule kills the Workato scenario≈deep-dive duplication AND adds breadth. When there is no
uncovered overflow (feasible plan, or every overflow already scenario-covered), the deep-dive is
unconstrained (project-agnostic).

Not hard-repairable (code can't author a scenario for a new skill) — it drives the targeted
critic re-pass, same as the existing `uncovered_required_primary`.

### Part C — Generation + critic wiring (prompts)

The generator already receives `required_primaries` + `secondary_only` in the user message.
Make the rules explicit so the LLM produces a compliant bank first-try (code still guarantees it):

- `prompts/v3/question_bank_ai_screening.txt` — in the scored-scenario recipe: "Each scenario's
  `primary_signal` is UNIQUE across the bank — never two scenarios on the same skill." And for
  the deep-dive: "If the coverage plan lists secondary-only skills, the project_deepdive's
  `primary_signal` MUST be one of them (so an otherwise-unscored must-have gets scored — frame
  the deep-dive around a real project that exercised that skill); if there are none, let the
  candidate pick their most significant project."
- `prompts/v3/question_bank_critic.txt` — add matching audit checks: (a) no two scenarios share
  a `primary_signal`; (b) when secondary-only skills exist, the deep-dive's `primary_signal` is
  one of them. (Complements the lead-distinctness check #4 already present.)

---

## Wiring (no new call sites)

`check_bank_invariants` is already called in `actors.py` with `plan=coverage_plan`; the two new
`Violation`s flow through the existing **one targeted critic re-pass** (their `description`s tell
the critic exactly what to fix) and the final **`hard_repair`** (which gains the
`duplicate_scenario_primary` dedupe step). No new orchestration.

## Defect → fix

| Finding | Closed by |
|---|---|
| Workato language/DB unscored + integration doubled | B2 (deep-dive scores an overflow skill, can't duplicate a scenario) + A (fewer skills fit budget) |
| Workato Q2 (scenario) ≈ Q5 (deep-dive) same primary | B2 (deep-dive primary ∈ secondary_only ⇒ ≠ any scenario primary) |
| Two scenarios on one skill (general) | B1 (`duplicate_scenario_primary`) |
| EMM Q1 ≈ Q2 (compliance vs enforcement) | A (merge the two near-synonym signals upstream) + existing lead-distinctness critic |
| Genuine overflow (skills > slots after merge) | unchanged, correct: `secondary_only` + `coverage_feasibility` badge + "extend stage" (never silent) |

## Non-goals

- No change to `build_coverage_plan` logic (it already exposes `required_primaries` +
  `secondary_only`), schema, migration, or the actor's orchestration.
- No new prompt version (signal stays `v2`, bank stays `v3`).
- Not relaxing prior rules (substance-fidelity, PRESERVE-EVERY-MUST-HAVE, weight-3, concreteness,
  two-sentence lead, same-competency merge test) — this ADDS distinctness invariants on top.
- No deterministic semantic de-dup (embedding clustering) — semantic same-knowledge/different-
  primary cases stay with the merge prompt + LLM critic.

## Operational notes

- `invariants.py` is pure (no DB/LLM) — fully unit-testable (TDD). Existing tests:
  `tests/question_bank/test_invariants.py`.
- Prompts + the actor run in the lean `nexus-worker` (no hot-reload) → restart after changes.

## Validation (manual — multiple JDs)

1. **Unit (deterministic):** new `invariants.py` tests — duplicate scenario primaries flagged
   (and dropped by `hard_repair`); deep-dive flagged when its primary isn't a `secondary_only`
   skill while overflow exists; NOT flagged when `secondary_only` is empty.
2. **Workato (live):** re-extract + regenerate. Expect: every scenario a distinct skill; the
   deep-dive's primary is a secondary-only skill (e.g. language or DB) — not `integration`; no
   scenario≈deep-dive duplication; language/DB now scored (helped further by Part A merging
   8→~6 skills).
3. **EMM (live):** re-extract — `compliance/CA` and `policy-enforcement` merge to one signal;
   regenerate — the former Q1≈Q2 pair collapses; all must-haves still scored at weight 3.
4. **A skill-poor JD (guard):** confirm the deep-dive is allowed to be project-agnostic (no
   false `deepdive_primary_uncovered_overflow` when `secondary_only` is empty), and no
   false `duplicate_scenario_primary` when scenarios are genuinely distinct.
```
