# Signal De-dup + Lead Situation-Distinctness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one scalable "same knowledge/work to demonstrate = same competency" test at two layers so the signal extractor merges near-synonym requirements (de-duplicating signals across separate JD lines) and the bank generator/critic reject two leads answerable from the same knowledge.

**Architecture:** Prompt-only. Three exact edits across three prompt files, all using the identical criterion + abstract illustrations. No code, schema, migration, or coverage-planner change — fewer upstream signals flow naturally through the existing planner.

**Tech Stack:** Plain `.txt` prompts read by `PromptLoader` (`prompts/v2/jd_signal_extraction.txt`, `prompts/v3/question_bank_ai_screening.txt`, `prompts/v3/question_bank_critic.txt`); pytest for regression only.

## Global Constraints

- Prompt-only. No code/schema/migration/coverage-planner change. No new prompt version (signal extraction stays `v2`, bank stays `v3`).
- The test is identical at both layers: "two items are the SAME competency if a candidate would demonstrate/answer both with the same body of knowledge or the same piece of work — even if worded differently or listed as separate JD bullets; distinct only if genuinely different knowledge/work; a pure umbrella/category term is not its own competency."
- Must NOT relax the shipped rules: substance-fidelity, PRESERVE-EVERY-MUST-HAVE (merged signal names each must-have at weight 3), weight-3 must-haves, concreteness, two-sentence ≤320 lead. This only ADDS a merge/duplicate test.
- No JD-specific text — illustrations stay abstract (`monitor`/`enforce`, `diagnose`/`harden`).
- Spec: `docs/superpowers/specs/2026-06-18-signal-and-lead-distinctness-design.md`.
- Prompts run in the lean `nexus-worker` (no hot-reload) → restart it after editing.

---

### Task 1: Add the "same competency" test at both layers (3 prompt edits)

**Files:**
- Modify: `prompts/v2/jd_signal_extraction.txt`
- Modify: `prompts/v3/question_bank_ai_screening.txt`
- Modify: `prompts/v3/question_bank_critic.txt`

**Interfaces:**
- Consumes: nothing new.
- Produces: prompts read by `PromptLoader` in `jd/actors.py` (signal extraction) and `question_bank/actors.py` (bank gen + critic). No code contract change.

- [ ] **Step 1: Insert the signal-merge section in `prompts/v2/jd_signal_extraction.txt`**

Immediately AFTER the CONSOLIDATE section's last line (`Do NOT over-combine genuinely distinct competencies; do NOT fragment one competency into many near-duplicates.`) and BEFORE the line `# Phrasing — name the competency, don't copy the sentence`, insert:

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

(Keep one blank line after the inserted block so it is separated from `# Phrasing`.)

- [ ] **Step 2: Strengthen lead distinctness in `prompts/v3/question_bank_ai_screening.txt`**

In the `# Within-bank distinctness (across the whole pass)` section, immediately after the
sentence `Each lead opens a DISTINCT skill or angle.`, append (same paragraph block):

```
The test for "same underlying thing": two leads are DUPLICATES if a candidate would answer
BOTH from the same body of knowledge or the same piece of work — even if their `primary_signal`s
differ and the surface situations look different. Two framings of one mechanism — "diagnose why
X broke" vs "harden X so it won't break" — are the SAME competency, not two. When two leads
collapse under this test, keep one and spend the freed slot on an uncovered skill (or drop it —
fewer, distinct scenarios are better).
```

- [ ] **Step 3: Strengthen check #4 in `prompts/v3/question_bank_critic.txt`**

Replace this exact block:
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

- [ ] **Step 4: Verify all three prompts still load**

Run:
```
docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; v2=PromptLoader(version='v2'); v3=PromptLoader(version='v3'); print('sig', len(v2.get('jd_signal_extraction'))); [print(n, len(v3.get(n))) for n in ('question_bank_ai_screening','question_bank_critic')]"
```
Expected: three positive char counts, no exception. (Also confirms the signal-merge text is present: the count for `jd_signal_extraction` is larger than before.)

- [ ] **Step 5: Run the regression subset**

Run: `docker compose run --rm nexus pytest tests/test_prompt_loader.py tests/test_jd_actor.py tests/test_jd_extraction_prompt_version.py tests/test_question_banks_actors.py tests/question_bank/ -q`
Expected: PASS. (All these mock the LLM seam, so prompt wording does not change code-path behavior; this confirms no accidental breakage.)

- [ ] **Step 6: Commit**

```bash
git add prompts/v2/jd_signal_extraction.txt prompts/v3/question_bank_ai_screening.txt prompts/v3/question_bank_critic.txt
git commit -m "feat(question_bank): same-competency merge test for signals + lead distinctness

Signal extraction merges same-competency requirements across separate JD
lines (names each must-have, weight 3; umbrella terms folded in). Bank
lead-distinctness + critic check #4 gain the identical test: two leads
answerable from the same knowledge are duplicates even if primary_signals
differ. One scalable, JD-agnostic criterion at both layers.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Live validation across two JDs (de-dup works, no over-merge)

No automated test — prompt quality is proven live. Its own task because a reviewer could
approve the prompt text yet reject the observed behavior, and the central risk (over-merging
genuinely distinct skills) can only be seen on a multi-skill JD.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker so it loads the new prompts**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: `nexus-worker` recreated, healthy (`docker compose ps nexus-worker`).

- [ ] **Step 2: EMM job — re-extract and check de-duplication**

In the recruiter app, "Unlock & re-enrich" the EMM job
(`/jobs/11650922-79c6-43f6-8f26-4638303e1fbf?tab=jd`). When signals land, confirm:
- the former near-synonyms — "device compliance / conditional access" and "policy enforcement
  for secure device access" — are now ONE signal whose value NAMES both (not two separate
  signals);
- any pure umbrella catch-all (a bare "EMM/MDM" with no distinct skill) is folded into the
  specific skills, not emitted alone;
- every original must-have is still represented (named) in some signal at weight 3 (nothing
  dropped);
- the total signal count is lower than the prior snapshot.

> Read signals: `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -c "select jsonb_pretty(signals) from job_posting_signal_snapshots where job_posting_id='11650922-79c6-43f6-8f26-4638303e1fbf' order by version desc limit 1;"`

- [ ] **Step 3: EMM job — regenerate bank and check lead distinctness**

Confirm signals, then regenerate the AI-screening bank (stage `e537d1aa-…`). Confirm:
- the former Q2≈Q4 pair (compliance-troubleshoot vs enforcement-tighten) has collapsed to a
  single lead — no two leads answerable from the same knowledge;
- the freed slot is spent on an uncovered skill (or the bank is simply leaner);
- generation completes (no `generation_error`).

> Read questions: `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -F'||' -c "select position, primary_signal, text from stage_questions q join stage_question_banks b on b.id=q.bank_id where b.stage_id='e537d1aa-bbb1-4642-8f70-6e6b633d59c6' order by position;"`

- [ ] **Step 4: Workato job — re-extract + regenerate, confirm NO over-merge (critical guard)**

"Unlock & re-enrich" the Workato job (`ce6dad9a-…`) and regenerate its AI-screening bank
(stage `2ea4f4a3-…`). Confirm the genuinely-distinct skills stay SEPARATE — workflow design,
agent-based AI, integration project, API/connector (REST/SOAP/JSON), language+DB — i.e. the
signal set and the bank are still diverse (~6 distinct scenarios), NOT collapsed. This proves
the merge test only removes true synonyms and does not erase real distinctions.

> Read signals: `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -c "select jsonb_pretty(signals) from job_posting_signal_snapshots where job_posting_id='ce6dad9a-8903-4396-8f29-8e36da9bd2a3' order by version desc limit 1;"`

- [ ] **Step 5: Record the result**

If EMM de-duplicates AND Workato stays diverse AND all must-haves remain represented at
weight 3, note completion. If EMM still has the duplicate pair, or Workato over-merged, capture
the offending signals/leads and which rule failed, and iterate on the prompt (back to Task 1).

---

## Self-Review

**Spec coverage:** Signal-merge section (spec A) → Task 1 Step 1. Bank lead-distinctness (spec B) → Step 2. Critic check #4 (spec C) → Step 3. "Reconciles with PRESERVE / no relaxation of prior rules" → Global Constraints. EMM de-dup + Workato no-over-merge + must-have-preservation validation (spec Validation) → Task 2 Steps 2-4. Operational restart → Task 2 Step 1. Non-goals (no code/schema/planner/version change) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO/"handle edge cases". All three edits give exact insert/replace blocks. The `X` in examples is the intended abstract placeholder inside the prompt copy. Validation steps enumerate concrete pass/fail conditions + exact SQL.

**Type consistency:** No new types/signatures. Prompt names (`jd_signal_extraction`, `question_bank_ai_screening`, `question_bank_critic`) and versions (`v2`/`v3`) used consistently; the merge criterion is stated identically in all three edits (Steps 1-3).
