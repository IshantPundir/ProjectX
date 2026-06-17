# Signal De-duplication + Lead Situation-Distinctness

**Date:** 2026-06-18
**Status:** Design — approved, pending implementation
**Branch:** `feat/question-bank-quality`
**Scope:** Prompt-only. One unifying "same competency" test added at two layers — signal extraction (`jd_signal_extraction.txt`) and question-bank lead distinctness (`question_bank_ai_screening.txt` + `question_bank_critic.txt`). No code/schema/planner change.

---

## Problem

Deep QA of two generated banks found the EMM bank repetitive: of 7 questions, four converged
on the same competency (Intune compliance / conditional-access / mail-access). The clearest
pair, **Q2 ("diagnose why compliant-device mail access broke") ≈ Q4 ("tighten enforcement so
only compliant devices keep mail access")**, are answered from the *same* body of knowledge.

Root cause is upstream and general (not EMM-specific):
- The signal extractor emitted **two near-synonym signals** — "device compliance monitoring &
  conditional access" and "policy enforcement for secure device access" — because they appear
  as *separate JD lines* worded differently. The prompt says "don't emit two signals that
  restate the same competency" and "don't over-combine genuinely distinct competencies," but
  gives **no decidable test** for when two differently-worded requirements ARE the same
  competency. So it under-merges.
- The bank's lead-distinctness rule says "no two leads may probe the same underlying thing,"
  but likewise has **no test** — so Q2 and Q4 passed (their `primary_signal`s differ and the
  surface situations look different) while testing the same skill.

Because signals drive the bank AND the report, redundant signals produce a redundant bank and
redundant per-signal report scoring.

---

## Goal

One scalable, JD-agnostic criterion, applied identically at both layers:

> **Same knowledge/work to demonstrate = same competency.** Two requirements (or two question
> leads) are the same if a candidate would demonstrate/answer both with the same body of
> knowledge or the same piece of work — even if worded differently, listed as separate JD
> bullets, or framed as different situations. They are distinct only when each genuinely needs
> different knowledge or different work. A pure umbrella/category term that is just the sum of
> its specific skills is not a separate competency.

### Decisions (taken)
- **Merge at the signal layer** (highest leverage — fixes signals, bank, and report), with the
  merged signal **naming each source must-have** at weight 3 (no must-have lost) — this is
  already permitted by the existing PRESERVE-EVERY-MUST-HAVE rule.
- **Plus a lead situation-distinctness backstop** at the bank, using the identical test.

---

## Changes

### A. `prompts/v2/jd_signal_extraction.txt` — new "Merge same-competency requirements" section

Insert immediately AFTER the CONSOLIDATE section (before `# Phrasing`):

```
# Merge same-competency requirements — even across separate JD lines

CONSOLIDATE (above) groups sub-skills the JD lists together. This rule is stronger: it merges
requirements that are the SAME competency even when the JD states them as SEPARATE lines or
separate must-haves and words them differently.

The test: two requirements are the SAME competency — emit ONE signal whose value NAMES each —
if a candidate would demonstrate both with the SAME knowledge or the SAME piece of work.
Different wording, or appearing as two different JD bullets, does NOT make them distinct.
  - e.g. two lines that name one mechanism from different angles — "monitor X" and "enforce X"
    — are one competency; merge to a single signal that names both, even if both are must-haves.
Keep two requirements SEPARATE only when a candidate would need genuinely different knowledge
or different work to show each (do not over-merge truly distinct skills).

A pure UMBRELLA / category term — one that is just the sum of the specific skills already
present, a broad domain label with no distinct skill of its own — is NOT its own signal. Fold
it into the specific skills it umbrellas; never emit it as a separate catch-all.

This reconciles with PRESERVE EVERY MUST-HAVE (below): a merged signal NAMES each must-have it
covers and carries weight 3, so no must-have is lost — the signal set is simply de-duplicated.
```

### B. `prompts/v3/question_bank_ai_screening.txt` — strengthen lead distinctness

In the `# Within-bank distinctness (across the whole pass)` section, after the sentence
`Each lead opens a DISTINCT skill or angle.`, append:

```
The test for "same underlying thing": two leads are DUPLICATES if a candidate would answer
BOTH from the same body of knowledge or the same piece of work — even if their `primary_signal`s
differ and the surface situations look different. Two framings of one mechanism — "diagnose why
X broke" vs "harden X so it won't break" — are the SAME competency, not two. When two leads
collapse under this test, keep one and spend the freed slot on an uncovered skill (or drop it —
fewer, distinct scenarios are better).
```

### C. `prompts/v3/question_bank_critic.txt` — strengthen check #4

Replace check #4:
```
4. LEAD-LEVEL DISTINCTNESS. No two LEAD questions probe the same underlying thing (not just
   follow-up dimensions). Merge or replace near-duplicate leads (e.g. a behavioral and the
   project_deepdive both about "an integration you built end to end").
```
with:
```
4. LEAD-LEVEL DISTINCTNESS. No two LEAD questions probe the same underlying thing (not just
   follow-up dimensions). The test: two leads are duplicates if a candidate would answer BOTH
   from the same knowledge or the same piece of work -- even if their primary_signals differ
   and the situations look different (e.g. "diagnose why X broke" vs "harden X so it won't
   break" are the same competency). Merge or replace near-duplicate leads (including a
   behavioral and the project_deepdive both about "an integration you built end to end") and
   spend the freed slot on an uncovered skill.
```

---

## Why this is enough (and stays consistent)

- The signal-merge test removes the redundancy at the source, so the existing coverage planner
  (knapsack over `primary_signal`) naturally yields fewer, distinct scored scenarios — no
  planner change needed.
- The lead test is a backstop for the case where two genuinely-distinct signals still tempt
  two same-knowledge leads.
- Both layers use the SAME criterion and the SAME abstract illustration ("monitor/enforce" /
  "diagnose/harden"), so they cannot drift apart.

## Non-goals

- No code/schema/migration/coverage-planner change.
- No new prompt version (signal extraction stays `v2`, bank stays `v3`).
- No relaxation of the substance-fidelity, must-have-preservation, weight-3, or concreteness
  rules — this only adds a merge/duplicate TEST on top of them.
- No JD-specific tuning (illustrations are abstract).

## Operational notes

- Signal extraction + bank generation run in the lean `nexus-worker` (no hot-reload) → restart
  `nexus-worker` after editing the prompts.
- Seeing the effect on an existing job requires re-running extraction (recruiter "Unlock &
  re-enrich") and then regenerating the bank.

## Validation (manual — multiple JDs)

1. **EMM job** — re-extract: expect the compliance and policy-enforcement near-synonyms to
   merge into one signal that names both (fewer total signals); any pure umbrella term (e.g. a
   bare "EMM/MDM" catch-all) folded into specifics. Then regenerate the bank: expect the
   former Q2≈Q4 pair to collapse to one, freeing a slot for an uncovered skill — no two leads
   answerable from the same knowledge.
2. **A different-domain JD with genuinely distinct must-haves** (e.g. the Workato job) —
   confirm NO over-merge: distinct skills (workflow design, agent AI, integration, API/connector,
   language+DB) stay separate; the bank stays diverse. This guards against the test collapsing
   real distinctions.
3. Confirm must-haves are still all represented (named) at weight 3, and generation still
   completes (reliability fix holds).
```
