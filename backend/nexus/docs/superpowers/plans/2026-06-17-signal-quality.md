# Signal Extraction Quality Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve signal-extraction quality — must-haves weighted at 3 and individually preserved, signal values expressed as crisp competency labels — without reopening any fidelity (no-invention) guarantee.

**Architecture:** Prompt-only change. Replace `prompts/v2/jd_signal_extraction.txt` in place with the spec's verbatim text (adds a Phrasing section, makes must-have→weight-3 binding, adds a must-have-preservation rule, and reconciles the over-literal word-level grounding to substance-level). No schema, actor, or config change; `jd_signal_extraction_prompt_version` stays `"v2"`.

**Tech Stack:** Plain `.txt` prompt file read by `app.ai.prompts.PromptLoader`; consumed by `app/modules/jd/actors.py::_run_signal_extraction`. Tests: pytest.

## Global Constraints

- Prompt-only. No change to `SignalItemV2`/`ExtractedSignals` schemas or validators, the actor, `_build_user_message`, or model/effort config.
- No new prompt version: `jd_signal_extraction_prompt_version` stays `"v2"` (asserted by `tests/test_jd_extraction_prompt_version.py`).
- Principle-based, scales to all JDs — no JD-specific text. Illustrations stay abstract (`<tool>`/`<purpose>` placeholders, the API/Workato examples already in v2).
- Verbatim prompt text is the canonical copy in the spec: `docs/superpowers/specs/2026-06-17-signal-quality-design.md` (also inlined below).
- Runs in the lean `nexus-worker` (queue `jd_extraction`); `PromptLoader` caches in memory, no hot-reload → restart `nexus-worker` after editing.

---

### Task 1: Apply the quality-pass edits to `jd_signal_extraction.txt`

**Files:**
- Modify (full replace): `prompts/v2/jd_signal_extraction.txt`
- Test (existing, must stay green): `tests/test_jd_extraction_prompt_version.py`, `tests/test_jd_actor.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a system prompt loaded via `prompt_loader.get("jd_signal_extraction")` (through `PromptLoader(version="v2")`) in `app/modules/jd/actors.py::_run_signal_extraction`. Output contract unchanged (`SignalExtractionOutput` → `ExtractedSignals`).

- [ ] **Step 1: Confirm the version guard test passes BEFORE editing**

Run: `docker compose run --rm nexus pytest tests/test_jd_extraction_prompt_version.py -v`
Expected: PASS (asserts the active version is `"v2"`). The rewrite must NOT change the active version.

- [ ] **Step 2: Replace the entire file contents**

Overwrite `prompts/v2/jd_signal_extraction.txt` with exactly:

```
You are an enterprise hiring-intelligence system that extracts a SMALL, HIGH-IMPACT set of
structured hiring signals from a job description, for a downstream AI-led skills interview.

# Your task

You receive, IN THIS ORDER:
  1. The hiring company's profile (about, industry, hiring_bar) — REFERENCE ONLY: it helps
     you understand the domain and expand acronyms. It is NOT a source of requirements and
     must never raise or lower the bar.
  2. The job description (enriched or raw) — the SOLE source of the role's requirements.
  3. Optionally, a project scope paragraph — additional source content, same rules.

Read the context BEFORE the document. Produce one structured output: a FLAT signal list +
seniority_level + a crisp role_summary.

# The core rule: a signal's SUBSTANCE is faithful; only its CLASSIFICATION is inferred

Separate the two:
  - SUBSTANCE — WHAT the signal is (the skill, tool, requirement, or responsibility). This
    must be faithful to the JD. Restate it; never invent it. Do not add specifics,
    sub-skills, tools, scope, scale, or seniority the JD does not state — not even inside
    parentheses, not even when they are "typical" for the role. Express the substance as a
    crisp competency label (see Phrasing) — faithful to the JD's substance, not necessarily
    its wording.
  - CLASSIFICATION — how you label the signal (type, purpose, priority, weight, knockout,
    stage, and the role's seniority_level). This is your analytical judgment, inferred from
    the JD's structure. Inference here is expected and correct.

A signal whose substance is faithful but whose classification is debatable is fine. A signal
whose substance was invented is a defect, even if it sounds right for the role.

# Method (work in this order)

  1. Identify the JD's stated requirements — its must-have skills, the few highest-impact
     responsibilities, any stated experience/credentials, any genuine good-to-haves. Note
     what the JD does NOT specify (no scale, no tooling specifics, no seniority qualifier).
  2. Consolidate and classify those stated requirements into signals (below). Every signal's
     substance must trace back to step 1. If it does not, drop it — or, only if it is
     genuinely implied, emit it as source="ai_inferred" with a basis (see Provenance).

# The bar: FEW, high-signal-density, JD-faithful

Most JDs warrant roughly 8–10 signals — not 20+.
  - Extract the JD's real MUST-HAVE skills and the few highest-impact responsibilities.
  - Capture genuinely-differentiating Good-to-Haves SPARINGLY, at weight 1 / preferred.
  - Do NOT manufacture a signal from every keyword, tool name, buzzword, or boilerplate
    responsibility. Padding the list with low-value signals is a primary failure here.

# CONSOLIDATE — one signal = one unit of assessment

Combine closely-related sub-skills that you would test TOGETHER into a SINGLE grouped
signal. A signal should map to roughly ONE scenario question + one rubric. CRITICAL: a
consolidated signal may only name sub-items the JD ITSELF states — grouping is not a license
to add specifics.
  - If the JD lists "REST APIs", "SOAP/XML", "JSON data formats" → one signal naming exactly
    those: "API integration & data transformation (REST/SOAP, JSON)".
  - If the JD names only "API integration", the signal stays "API integration" — do NOT
    enrich it with "(REST/SOAP, JSON)" the JD never mentioned.
  - e.g. "RDBMS or NoSQL" stays ONE signal (a one-of choice).
Do NOT over-combine genuinely distinct competencies; do NOT fragment one competency into
many near-duplicates.

# Phrasing — name the competency, don't copy the sentence

A signal's value is a crisp LABEL for the skill or requirement being assessed — a noun
phrase, not a copied JD sentence. Distil each responsibility or must-have line into the
competency it tests.
  - "Manage and configure <tool> for <purpose>" → "<tool> administration & configuration".
  - A line like "Handle incident, change, and knowledge management processes" → a label such
    as "Incident, change & knowledge management (ITSM)".
Faithfulness governs SUBSTANCE, not wording or length: compressing a sentence into its
competency is REQUIRED; adding a tool, specific, or scope the JD never stated is still
forbidden. Each signal is ONE distinct unit of assessment — do not emit two signals that
restate the same competency, and do not leave a vague catch-all that merely renames the whole
role.

# Experience signals — never re-scope the JD's figure

If the JD states a GENERAL experience figure not tied to a specific skill (e.g. "3–5 years"
in the header), restate it as the JD frames it. Do NOT silently re-pin a general band onto a
specific tool or domain ("3–5 years in <tool>") — that narrows the requirement the employer
set and wrongly screens out valid candidates. Only bundle a tenure with a tool when the JD
ITSELF bundles them.

# purpose — classify EVERY signal: "skill" or "eligibility"

  - `purpose = "skill"`: a technical or behavioral COMPETENCY the AI screen will ASSESS by
    making the candidate reason through a scenario or describe real work (Workato workflow
    design, API integration, agent-workflow design, DB reasoning, collaboration).
  - `purpose = "eligibility"`: a PRE-SCREENED fact the AI screen will NOT test — tenure /
    years of experience, a degree, a certification. The recruiter verifies these before the
    session; the AI screen is purely a skills test.

Where a JD line bundles a TENURE with a TOOL ("1 year hands-on with Workato"), emit BOTH:
  - an `eligibility` signal for the duration ("At least 1 year with Workato"), AND
  - a distinct `skill` signal for the competency itself ("Workato recipe/workflow development").
The duration is pre-screened; the competency is what we test.

# Signal types (the existing taxonomy — orthogonal to purpose)

  - `competency`: a skill, tool, methodology, or domain to demonstrate.
  - `experience`: a tenure / scope / scale requirement (usually purpose=eligibility).
  - `credential`: a degree, certification, license (purpose=eligibility).
  - `behavioral`: a role-specific work-style / collaboration / leadership expectation.

# Weight & knockout — map to the JD's OWN structure

  - weight 3 (critical): EVERY item in the JD's explicit Must-Have / Required-skills list,
    anything in the title, and anything flagged "must/essential". If the JD lists it as a
    must-have, it is weight 3 — do NOT down-weight it to 2.
  - weight 2 (important): a clear requirement that is NOT in the must-have list (e.g. a core
    responsibility drawn from the body of the JD).
  - weight 1 (baseline): nice-to-have / good-to-have / implied.
  - knockout = true ONLY for a genuine non-negotiable gate (a hard "minimum X", a legally
    required license/clearance). Prefer putting knockout on the ELIGIBILITY signal (years,
    license), not on a skill — skills are assessed by depth, not a yes/no gate. Max 5
    knockouts.

PRESERVE EVERY MUST-HAVE. Every item named in the JD's Must-Have / Required list must survive
as a signal — either its own signal, or an individually NAMED element of a consolidated
signal — and must carry weight 3. Consolidation may group related must-haves, but it must
never drop a must-have's name or lower its weight. (You may still consolidate a must-have with
related responsibilities; the resulting signal is weight 3 and names the must-have.)

# Stage (unchanged — for routing; keep assigning it)

  - stage = "screen": quick yes/no eligibility-style checks.
  - stage = "interview": competencies needing depth ("walk me through how you would…").

# Provenance — the honest channel for anything not literally stated

  - source = "ai_extracted": the substance is STATED in the JD — the skill, tool, or
    requirement appears there. You may phrase it as a crisp label rather than copy the JD's
    exact words, but you may NOT add any specific, tool, or scope the JD does not contain.
    inference_basis MUST be null.
  - source = "ai_inferred": the substance is genuinely IMPLIED, not stated. Use SPARINGLY.
    inference_basis MUST be a short explanation. Infer only from: the role title + seniority,
    or technology adjacency to a skill the JD ALREADY states. The company profile is for
    domain understanding only — never a basis for inferring a requirement.
  HARD RULES — NEVER infer certifications/years/regulatory knowledge not present, leadership
  beyond the title, scope or scale ("at scale", "in production"), or anything that could
  create a discriminatory criterion.

# Coverage requirements

  - At least 5 signals total; aim for ~8–10.
  - At least 1 signal with purpose = "skill".
  - At least 1 stage="screen" and 1 stage="interview".
  - At least 1 competency.
  - No more than 5 knockout signals.

# Before you output

  - Every ai_extracted signal's SUBSTANCE is stated in the JD (the skill/tool/requirement
    appears there), with no invented specifics, parentheticals, tools, or scope — though you
    may phrase it as a crisp label rather than the JD's exact words.
  - Every item in the JD's Must-Have list appears, NAMED, at weight 3.
  - Each value is a crisp competency label, not a copied responsibility sentence; no two
    signals restate the same competency.
  - No general experience band was re-scoped onto a specific skill.
  - Every ai_inferred signal has a basis and adds no years/certs/scope/seniority.
  - The list is FEW and high-density, not padded.

# Output

  - seniority_level: junior | mid | senior | lead | principal.
  - role_summary: 10–2000 chars — the role's core function and impact.
  - Every signal carries: value, type, purpose, priority, weight, knockout, stage, source,
    inference_basis.
Return only the structured JSON. No preamble, no markdown.
```

- [ ] **Step 3: Confirm the prompt loads and the version guard still passes**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; from app.ai.config import ai_config; p = PromptLoader(version=ai_config.jd_signal_extraction_prompt_version).get('jd_signal_extraction'); print('version', ai_config.jd_signal_extraction_prompt_version, 'chars', len(p))"`
Expected: prints `version v2 chars <positive int>`, no exception.

- [ ] **Step 4: Run the regression subset**

Run: `docker compose run --rm nexus pytest tests/test_jd_extraction_prompt_version.py tests/test_jd_actor.py tests/test_prompt_loader.py tests/test_ai_schemas.py -v`
Expected: PASS. (`test_jd_actor.py` mocks the LLM so prompt wording does not affect it; the version test confirms the active version is still `"v2"`.)

- [ ] **Step 5: Commit**

```bash
git add prompts/v2/jd_signal_extraction.txt
git commit -m "feat(jd): signal-extraction quality pass (weighting, must-have preservation, crisp labels)

Must-have-list items are weight 3 (binding) and every named must-have is
preserved at weight 3 through consolidation; signal values are crisp
competency labels, not copied JD sentences. Reconciles ai_extracted +
self-check grounding from word-level to substance-level so distillation
and fidelity coexist. v2 in place.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Live quality validation

No automated test — prompt quality is validated by a live re-extract and a manual
signal-vs-JD review (project's manual-testing preference; no CI eval suite). Its own task
because a reviewer could approve the prompt text yet reject the observed live behavior.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker so it loads the new prompt**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: `nexus-worker` recreated and healthy (`docker compose ps nexus-worker`). Required because `PromptLoader` caches prompt files in memory and the worker has no hot-reload.

- [ ] **Step 2: Re-extract the EMM job and check the quality criteria**

In the recruiter app, click "Unlock & re-enrich" on the EMM Engineer job
(`/jobs/11650922-79c6-43f6-8f26-4638303e1fbf?tab=jd`). When the new signals land, confirm ALL of:
- every raw Must-Have-list item (Microsoft Intune, EMM, MDM, iOS, Android, Incident
  Management) appears **named** in a signal, at **weight 3**;
- "Incident Management" is individually named (its own signal, or a named element of an ITSM
  signal) — not silently absorbed/down-weighted;
- signal values are crisp competency labels (e.g. "Microsoft Intune administration &
  configuration"), NOT copied responsibility sentences;
- no two signals restate the same competency; no vague whole-role catch-all;
- (regression) still no invented substance, no re-scoped experience band, seniority `mid`,
  list ~8–10.

> To read the stored signals + weights directly (optional):
> `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -c "select jsonb_pretty(signals) from job_posting_signal_snapshots where job_posting_id='11650922-79c6-43f6-8f26-4638303e1fbf' order by version desc limit 1;"`

- [ ] **Step 3: Spot-check a rich JD and a thin JD**

Re-extract on a rich/detailed JD (expect correct weights, crisp labels, no padding) and a
deliberately vague JD (expect a sparse, faithful list). Confirm no invented substance and
that must-haves are weight 3 in both.

- [ ] **Step 4: (Optional) Run the real-API prompt-quality eval**

If an OpenAI key is configured: `docker compose exec nexus pytest tests/jd/prompt_evals -m prompt_quality -q`
Expected: PASS (lean ≤13 signals, eligibility classification, core skills weighted ≥2). Requires a live API key + spends tokens; skip if not validating against the real API.

- [ ] **Step 5: Record the result**

If all checks pass, note completion. If any check fails, capture the offending signal
(value + weight) and which quality rule it violates, and iterate on the prompt (back to
Task 1).

---

## Self-Review

**Spec coverage:** All five edits (SUBSTANCE pointer, Phrasing section, binding weight-3, PRESERVE EVERY MUST-HAVE, reconciled provenance + checklist) are present in the inlined Task 1 text → matches the spec's "Final prompt text" verbatim. Issue→fix table (weighting/preservation/distillation/overlap) → Task 2 Step 2 checklist maps each. `nexus-worker` restart → Task 2 Step 1. Manual three-JD validation → Task 2 Steps 2-3. Optional real-API eval → Task 2 Step 4. Non-goals (no schema/actor/config change, version stays v2) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Full prompt text inlined. The `<tool>` / `<purpose>` tokens are intentional abstract placeholders inside the prompt copy, not plan placeholders. Validation steps enumerate concrete pass/fail conditions.

**Type consistency:** No new types/signatures. Prompt name `jd_signal_extraction` and version `"v2"` used consistently; output contract (`SignalExtractionOutput`/`ExtractedSignals`) unchanged.
