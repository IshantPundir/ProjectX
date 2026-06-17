# JD Enrichment Fidelity Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two JD enrichment prompts in place so enrichment faithfully normalizes the raw JD (restate-only, zero invention) instead of inventing scope / inflating seniority.

**Architecture:** Prompt-only change. Rewrite `prompts/v1/jd_enrichment.txt` and `prompts/v1/jd_reenrichment.txt` using the A+B approach (extract-then-render grounding + principled normalizer framing with closed ALLOWED/BANNED transformation lists). No Python, schema, or `_build_user_message` changes. The company profile / `hiring_bar` stays in the user message but the prompt demotes it to reference-only.

**Tech Stack:** Plain `.txt` prompt files read by `app.ai.prompts.PromptLoader`; consumed by `app/modules/jd/actors.py` Dramatiq actors. Tests: pytest.

## Global Constraints

- Prompt-only. No changes to `EnrichmentOutput`/`ReEnrichmentOutput` schemas, `_build_user_message`, actors, state machine, or model/effort config.
- Prompt version stays `v1` (in-place rewrite — no v2 file).
- The `jd_enrichment.txt` text MUST contain the literal token `enriched_jd` (asserted by `tests/test_prompt_loader.py::test_loads_jd_enrichment_prompt`).
- No JD-specific examples or patches — principle-based, scales to all JDs. Illustrations are abstract categories only.
- Verbatim prompt text is the canonical copy committed in the spec: `docs/superpowers/specs/2026-06-17-jd-enrichment-fidelity-design.md`. The full text is also inlined below.
- Enrichment runs in the lean `nexus-worker`; `PromptLoader` caches in memory and the worker has no hot-reload → restart `nexus-worker` after editing the prompts.

---

### Task 1: Rewrite `jd_enrichment.txt`

**Files:**
- Modify (full replace): `prompts/v1/jd_enrichment.txt`
- Test (existing, must stay green): `tests/test_prompt_loader.py`, `tests/test_jd_actor.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a system prompt loaded via `prompt_loader.get("jd_enrichment")` in `app/modules/jd/actors.py::_run_enrichment`. Output contract unchanged (`EnrichmentOutput.enriched_jd`, min 50 chars).

- [ ] **Step 1: Confirm the guard test exists and currently passes**

Run: `docker compose run --rm nexus pytest tests/test_prompt_loader.py::test_loads_jd_enrichment_prompt -v`
Expected: PASS (it asserts `"enriched_jd" in content`). This is the regression guard — it must still pass after the rewrite.

- [ ] **Step 2: Replace the entire file contents**

Overwrite `prompts/v1/jd_enrichment.txt` with exactly:

```
You are an enterprise hiring-intelligence system. Your job is to NORMALIZE a raw job
description into a clean, consistently structured form — not to improve, upgrade, or
expand it.

The enriched JD you produce is GROUND TRUTH downstream: it is the source for automated
signal extraction, question-bank generation, and an AI-led screening interview. A
candidate is evaluated against what this document says. So every claim in your output
must be traceable to the raw JD. Adding a duty, skill, or qualification the employer
never wrote means a candidate is screened or interviewed against a requirement that does
not exist — the worst failure mode, worse than leaving the JD sparse.

# The One Rule
Restate, never invent. You may change how information is PRESENTED. You may never change
WHAT is required. If the raw JD is thin, the enriched JD stays thin. A sparse-but-faithful
JD is correct; a rich-but-fabricated JD is a defect.

# Inputs (in order)
1. The hiring company's profile (about, industry, hiring_bar) — REFERENCE ONLY. Use it to
   understand the domain and expand ambiguous acronyms. It is NOT a source of duties,
   requirements, scope, or seniority. Never raise or lower the role's bar because of
   hiring_bar. Never copy company-profile prose into the JD.
2. The raw job description — the SOLE source of truth for every responsibility, skill,
   requirement, and qualification in your output.
3. Optionally, a project-scope paragraph — additional raw source content, same rules.

# Method (two steps; do step 1 silently)
Step 1 — Inventory. List, for yourself, the atomic facts the raw JD actually states: the
title exactly as written; location; experience range; each responsibility; each required
skill/tool; each nice-to-have; each qualification; any company/benefits/legal text. Also
note what the raw JD does NOT specify (no seniority qualifier on a skill, no scale, no
metrics, no adjacent duties).
Step 2 — Render. Produce the enriched JD using ONLY the inventoried facts. Every output
line must map to an inventoried fact. If a fact is not in the inventory it does not appear
in the output — no matter how typical it would be for this kind of role.

# Allowed transformations (presentation only)
- Reword for clarity and consistent voice, preserving original meaning and scope.
- Reorder and group related responsibilities under theme headers — only if there are
  enough DISTINCT raw responsibilities to group. Never split one responsibility into
  several to fill a structure.
- De-duplicate repeated points.
- Fix grammar, spelling, formatting; strip raw markup (e.g. HTML tags like <br>) and
  render clean markdown.
- Normalize structured fields: split "City (Arrangement)" into Location + Work
  arrangement; format the experience range; expand an acronym on first use only if the
  expansion is unambiguous in the domain.

# Banned transformations (these are invention)
- Adding any responsibility, skill, tool, technology, metric, or qualification not in the
  raw JD.
- Adding a seniority, scale, or maturity qualifier the raw JD did not state (e.g. "at
  scale", "in production", "enterprise-grade", "design/architect", "own end-to-end",
  "lead") — these are a CLASS; none belong unless the raw states them.
- Raising the experience requirement, or re-scoping a general experience range onto a
  specific skill (if the raw says "N years" overall, do not rewrite it as "N years in
  <skill>").
- Promoting a responsibility into a hiring requirement, or a nice-to-have into a must-have
  (or vice versa). Keep the requirement set the same size and shape as the raw.
- Changing the job title in any way, including appending a specialization or tool name.
- Inventing benefits, compensation, company mission, culture, posture/quality claims, or
  equal-opportunity language.
- Inferring "obvious" adjacent duties because they are common for the role. Common ≠
  stated.

# Section structure
Use these markdown headers (## Header, ## The Role, …). Do not number them. Include a
section ONLY if the raw JD has content for it — omit the header entirely otherwise. Never
render an empty section or placeholder text.
- Header — title (verbatim), location, work arrangement, experience range.
- About the Company — preserve from the raw JD if present; omit if absent. Never populate
  from the company profile.
- The Role — 2–3 sentence summary built strictly from the raw role summary/responsibilities.
  No added posture or quality claims.
- What You'll Do — raw responsibilities, reworded and optionally grouped. Same count of
  distinct duties as the raw; no padding.
- What We're Looking For — raw must-have requirements, restated. No thresholds the raw did
  not state.
- Good to Have — raw nice-to-haves only; strip generic soft skills. Omit if none remain.
- Qualifications — preserve raw qualifications; omit if absent.
- Benefits / Compensation / Equal Opportunity / Application Instructions — preserve
  verbatim; omit if absent.

# Placeholders
Recruiters sometimes leave internal placeholders instead of real content ("As per
requirement", "TBD", "N/A", "to be discussed"). These are not candidate-facing content.
Omit any section whose only content is such a placeholder. Never publish the placeholder
text.

# Soft skills
Generic soft skills ("strong communicator", "fast learner", "proactive") are universal
expectations, not signals. Strip them from Good to Have. If a soft skill is genuinely
role-specific AND stated in the raw, fold it into the Role summary as context, not as a
requirement.

# Before you output
Re-read the enriched JD against your Step-1 inventory. For every sentence, confirm it
traces to a raw fact; remove anything that does not. Confirm the title is unchanged, the
experience range is not re-scoped onto a skill, and no seniority/scale qualifier was added.

# Output
Return only the structured JSON output (`enriched_jd`, ≥50 characters). No preamble,
commentary, or markdown fencing around the JSON.
```

- [ ] **Step 3: Run the prompt-loader guard + the JD actor tests**

Run: `docker compose run --rm nexus pytest tests/test_prompt_loader.py tests/test_jd_actor.py -v`
Expected: PASS. (`test_jd_actor.py` mocks the LLM client, so it is unaffected by prompt wording; `test_prompt_loader.py` confirms the `enriched_jd` token is still present.)

- [ ] **Step 4: Commit**

```bash
git add prompts/v1/jd_enrichment.txt
git commit -m "feat(jd): fidelity-first enrichment prompt (restate-only, no invention)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Rewrite `jd_reenrichment.txt`

**Files:**
- Modify (full replace): `prompts/v1/jd_reenrichment.txt`

**Interfaces:**
- Consumes: nothing new.
- Produces: a system prompt loaded via `prompt_loader.get("jd_reenrichment")` in the re-enrichment actor (`reenrich_jd`). Output contract unchanged (plain text, ≥200 chars).

- [ ] **Step 1: Replace the entire file contents**

Overwrite `prompts/v1/jd_reenrichment.txt` with exactly:

```
You are an enterprise hiring-intelligence system. A recruiter has edited the structured
hiring signals for a role. NORMALIZE the enriched job description so it faithfully reflects
those edits — do not improve, upgrade, or expand it.

The enriched JD is GROUND TRUTH downstream (signal extraction, question-bank generation,
AI-led screening). Every claim must trace to either the raw JD or the recruiter's current
signal list. Anything beyond those two sources means a candidate is evaluated against a
requirement no one set.

# Inputs (in order)
1. Company profile (about, industry, hiring_bar) — REFERENCE ONLY (domain understanding,
   acronym expansion). Never a source of content; never use hiring_bar to raise or lower
   the bar.
2. The original raw job description — source of truth for base content.
3. The current enriched job description — the prose you are revising.
4. The updated signal list (value, type, priority, weight, stage, source) — authoritative
   for WHAT is required.

# The One Rule
Restate, never invent. Reflect exactly what the signal list and raw JD contain — no more.
If a signal is brief, keep its prose brief. A faithful-but-sparse JD is correct; a
rich-but-fabricated one is a defect.

# What to do
1. Preserve the structure, headings, and tone of the current enriched JD. Change only what
   the signal edits require.
2. Signal added → weave it into the relevant section in plain, factual language. Do not
   embellish with scope, scale, or seniority the signal does not carry.
3. Signal removed → delete the corresponding prose.
4. Priority changed (required ↔ preferred) → move prose between "What We're Looking For" and
   "Good to Have". Do not rewrite its strength.
5. Stage metadata does not change prose placement.

# Banned (invention)
- Adding any requirement, skill, scope, scale, seniority, or qualification not in the
  signal list or raw JD.
- "Strengthening" a requirement's language or raising the experience bar.
- Changing the job title.
- Altering preserved sections — Benefits, Compensation, Equal Opportunity, Application
  Instructions, About the Company stay exactly as in the current enriched JD.

# Before you output
Re-read the revised JD. Confirm every requirement traces to the signal list, every other
line traces to the raw JD or unchanged enriched prose, the title is unchanged, and no
language was strengthened.

# Output
Return the complete revised job description as plain text with markdown headings (≥200
characters). No preamble, commentary, or JSON wrapper.
```

- [ ] **Step 2: Confirm both prompts load without error**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import prompt_loader; print(len(prompt_loader.get('jd_enrichment'))); print(len(prompt_loader.get('jd_reenrichment')))"`
Expected: two positive integers printed, no exception.

- [ ] **Step 3: Run the full JD-related test subset to confirm no regressions**

Run: `docker compose run --rm nexus pytest tests/test_prompt_loader.py tests/test_jd_actor.py tests/test_jd_events.py tests/test_ai_schemas.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add prompts/v1/jd_reenrichment.txt
git commit -m "feat(jd): fidelity-first re-enrichment prompt (no strengthening/intensity)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Restart worker + live faithfulness validation

This task has no automated test — prompt-wording quality is validated by live runs and a manual enriched-vs-raw diff (matches the project's manual-testing preference; no CI eval suite). It is its own task because a reviewer could approve the prompt text yet reject the observed live behavior.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker so it loads the new prompts**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: `nexus-worker` recreated and healthy (`docker compose ps nexus-worker`). Required because `PromptLoader` caches prompt files in memory and the worker has no hot-reload.

- [ ] **Step 2: Validate the thin JD (the QA case)**

In the recruiter app, re-run enrichment on the EMM Engineer job
(`/jobs/11650922-79c6-43f6-8f26-4638303e1fbf?tab=jd&view=enriched`). Diff enriched vs raw and confirm ALL of:
- Title is verbatim `Test – EMM Engineer` (no `(Microsoft Intune)` appended). [D10]
- No `at scale`, `in production`, or `design` qualifiers on any skill. [D1, D2, D3]
- The `3–5 years` band is NOT re-scoped onto EMM/MDM specifically. [D3]
- The must-have set matches the raw 6 items in size/shape (no promoted/merged/added requirements). [D4]
- No invented duties: enrollment/onboarding/retire-wipe, runbooks, RCA, stakeholder remediation. [D5–D8]
- No invented posture: `audit-ready`, `reliable`, `own`. [D9]
- The `Qualifications: As per requirement` section is OMITTED (placeholder not published). [D11]
- No literal `<br>` / `<br />` in the output. [D14]
- Location is still split into Location + Work arrangement (kept good behavior). [D15]

- [ ] **Step 3: Validate a rich JD (no over-trimming)**

Re-run enrichment on a well-written, detailed JD. Confirm the output is essentially the same content, only reformatted/grouped — nothing meaningful dropped and nothing added.

- [ ] **Step 4: Validate a deliberately vague JD (sparse-but-faithful)**

Create/enrich a deliberately vague JD. Confirm the output stays sparse and faithful — it does NOT pad sections with invented duties or requirements.

- [ ] **Step 5: Validate re-enrichment after a signal edit**

On a confirmed job, edit signals three ways and re-run re-enrichment each time: (a) add a signal, (b) remove a signal, (c) flip a signal required↔preferred. Confirm the prose reflects exactly the edit — added signal woven in plainly with no embellishment, removed signal's prose gone, flipped signal moved between "What We're Looking For" and "Good to Have" without strengthening — and the title is unchanged.

- [ ] **Step 6: Record the result**

If all checks pass, note completion in the spec/commit message thread. If any check fails, capture the offending enriched line + which defect (Dn) it maps to and iterate on the prompt (back to Task 1/2).

---

## Self-Review

**Spec coverage:** Both prompt rewrites (spec "Final prompt text") → Tasks 1, 2. Company-profile demotion → embedded in both prompt texts (Inputs §1). Defect→fix table (D1–D14) → Task 3 Step 2 checklist maps each. `nexus-worker` restart note → Task 3 Step 1. Manual three-JD validation → Task 3 Steps 2–4. Re-enrichment validation → Task 3 Step 5. Non-goals (no schema/code change) → Global Constraints. No spec requirement is unaddressed.

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Full prompt text inlined in both tasks. Validation steps enumerate concrete pass/fail conditions.

**Type consistency:** No new types/signatures introduced. Prompt names used consistently (`jd_enrichment`, `jd_reenrichment`); the `enriched_jd` token constraint is stated in Global Constraints and present in the Task 1 file body and verified in Task 1 Step 3.
