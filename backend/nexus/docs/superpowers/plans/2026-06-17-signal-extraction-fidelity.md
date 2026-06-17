# Signal Extraction Fidelity Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the signal-extraction prompt so a signal's substance is faithful to the JD (restate-only) while its classification stays inferred — eliminating the invented parentheticals, re-scoped experience bands, and scope/scale inflation seen in extraction output.

**Architecture:** Prompt-only change. Replace `prompts/v2/jd_signal_extraction.txt` in place with the spec's verbatim text (substance-vs-classification core rule + extract-then-classify method + consolidation fix + experience-fidelity rule + tightened provenance + company-profile-as-reference-only + self-check). No schema, actor, or config change; `jd_signal_extraction_prompt_version` stays `"v2"`.

**Tech Stack:** Plain `.txt` prompt file read by `app.ai.prompts.PromptLoader`; consumed by `app/modules/jd/actors.py::_run_signal_extraction`. Tests: pytest.

## Global Constraints

- Prompt-only. No change to `SignalItemV2`/`ExtractedSignals` schemas or validators, the actor, `_build_user_message`, or model/effort config.
- No new prompt version: `jd_signal_extraction_prompt_version` stays `"v2"` (asserted by `tests/test_jd_extraction_prompt_version.py`).
- Principle-based, scales to all JDs — no JD-specific text. The only illustrations are abstract (the API/Workato examples already in v2).
- Verbatim prompt text is the canonical copy in the spec: `docs/superpowers/specs/2026-06-17-signal-extraction-fidelity-design.md` (also inlined below).
- Runs in the lean `nexus-worker` (queue `jd_extraction`); `PromptLoader` caches in memory, no hot-reload → restart `nexus-worker` after editing.

---

### Task 1: Rewrite `jd_signal_extraction.txt`

**Files:**
- Modify (full replace): `prompts/v2/jd_signal_extraction.txt`
- Test (existing, must stay green): `tests/test_jd_extraction_prompt_version.py`, `tests/test_jd_actor.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a system prompt loaded via `prompt_loader.get("jd_signal_extraction")` (through `PromptLoader(version="v2")`) in `app/modules/jd/actors.py::_run_signal_extraction`. Output contract unchanged (`SignalExtractionOutput` → `ExtractedSignals`).

- [ ] **Step 1: Confirm the version guard test passes BEFORE editing**

Run: `docker compose run --rm nexus pytest tests/test_jd_extraction_prompt_version.py -v`
Expected: PASS (asserts the active version is `"v2"`). The rewrite must NOT change the active version, so this stays green.

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
    parentheses, not even when they are "typical" for the role.
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

  - weight 3 (critical): in the title, the "Must-Have" list, or flagged "must/essential".
  - weight 2 (important): a clear single requirement.
  - weight 1 (baseline): nice-to-have / good-to-have / implied.
  - knockout = true ONLY for a genuine non-negotiable gate (a hard "minimum X", a legally
    required license/clearance). Prefer putting knockout on the ELIGIBILITY signal (years,
    license), not on a skill — skills are assessed by depth, not a yes/no gate. Max 5
    knockouts.

# Stage (unchanged — for routing; keep assigning it)

  - stage = "screen": quick yes/no eligibility-style checks.
  - stage = "interview": competencies needing depth ("walk me through how you would…").

# Provenance — the honest channel for anything not literally stated

  - source = "ai_extracted": the substance is STATED in the JD. EVERY word of the value must
    be grounded in the JD text — if you would add any specific the JD does not contain, it is
    NOT ai_extracted. inference_basis MUST be null.
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

  - Every ai_extracted signal's value traces, word for word, to the JD. No invented
    specifics, parentheticals, tools, or scope.
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
git commit -m "feat(jd): fidelity-first signal extraction (substance faithful, classification inferred)

Substance must trace to the JD (restate-only): consolidation may not add
unstated parentheticals, general experience bands are not re-scoped onto a
skill, ai_extracted requires every word grounded, and scope/scale inference
is banned. Classification (type/priority/weight/stage/purpose/seniority)
stays inferred. Company profile demoted to reference-only. v2 in place.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Live faithfulness validation

No automated test — prompt-wording quality is validated by live runs and a manual
signal-vs-JD diff (project's manual-testing preference; no CI eval suite). Its own task
because a reviewer could approve the prompt text yet reject the observed live behavior.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker so it loads the new prompt**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: `nexus-worker` recreated and healthy (`docker compose ps nexus-worker`). Required because `PromptLoader` caches prompt files in memory and the worker has no hot-reload.

- [ ] **Step 2: Re-extract the EMM job and diff signals vs the JD**

In the recruiter app, click "Unlock & re-enrich" on the EMM Engineer job
(`/jobs/11650922-79c6-43f6-8f26-4638303e1fbf?tab=jd`). When the new signals land, confirm ALL of:
- the experience signal restates `3–5 years` generally — NOT "in enterprise mobility / MDM operations";
- the Intune signal carries NO `(device enrollment, profiles, app deployment)`;
- the ITSM/knowledge signal carries NO `(runbooks)` / "documentation/runbooks" invention;
- no `at scale` / `in production` / `fundamentals and best practices` anywhere;
- any `ai_inferred` signal has a real basis and adds no years/certs/scope/seniority;
- the list stays ~8–10, high-density (not padded).

> To read the stored signals directly (optional): `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -c "select jsonb_pretty(signals) from job_posting_signal_snapshots where job_posting_id='11650922-79c6-43f6-8f26-4638303e1fbf' order by version desc limit 1;"`

- [ ] **Step 3: Spot-check a rich JD and a thin JD**

Re-extract on a rich/detailed JD (expect faithful signals, not padded) and a deliberately
vague JD (expect a sparse list with at most a couple of clearly-flagged `ai_inferred`
signals, each with a basis). Confirm no invented substance in either.

- [ ] **Step 4: (Optional) Run the real-API prompt-quality eval**

If an OpenAI key is configured, run the opt-in eval as an extra behavioral check:
`docker compose exec nexus pytest tests/jd/prompt_evals -m prompt_quality -q`
Expected: PASS (lean ≤13 signals, eligibility facts classified as eligibility, core skills weighted). Note: requires a live API key + spends tokens; skip if not validating against the real API.

- [ ] **Step 5: Record the result**

If all checks pass, note completion. If any check fails, capture the offending signal value
and which fidelity rule it violates, and iterate on the prompt (back to Task 1).

---

## Self-Review

**Spec coverage:** Prompt rewrite (spec "Final prompt text") → Task 1 Step 2. All 7 changes (substance-vs-classification, method, consolidation fix, experience-fidelity, provenance tightening incl. company-profile inference fix, reference-only profile, self-check) are present in the inlined text. Defect→fix table → Task 2 Step 2 checklist maps each observed defect. `nexus-worker` restart → Task 2 Step 1. Manual three-JD validation → Task 2 Steps 2-3. Optional real-API eval → Task 2 Step 4. Non-goals (no schema/actor/config change, version stays v2) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Full prompt text inlined. The `<tool>` / `<skill>` tokens are intentional abstract placeholders inside the prompt copy (generic illustrations), not plan placeholders. Validation steps enumerate concrete pass/fail conditions.

**Type consistency:** No new types/signatures. Prompt name `jd_signal_extraction` and version `"v2"` used consistently; output contract (`SignalExtractionOutput`/`ExtractedSignals`) unchanged.
