# Signal Extraction — Fidelity-First Rewrite

**Date:** 2026-06-17
**Status:** Design — approved, pending implementation
**Branch:** `feat/jd-enrichment-fidelity` (continues the JD-pipeline fidelity work)
**Scope:** Prompt-only. Rewrites `prompts/v2/jd_signal_extraction.txt` in place (config `jd_signal_extraction_prompt_version` stays `"v2"`). No code or schema change.

---

## Problem

After the enrichment prompt was fixed (`2026-06-17-jd-enrichment-fidelity-design.md`), the
enriched JD became faithful — but the **signal-extraction** step still inflates. On the EMM
Engineer job, extraction (from the now-faithful enriched JD) produced signals that invent
substance the JD never states, all labeled `source="ai_extracted"`:

1. **Experience band re-scoped** — `"3–5 years of experience in enterprise mobility / MDM
   (Intune/EMM) operations"` when the JD states only a general `Experience: 3–5 years`.
   Narrows the requirement and wrongly screens out valid candidates (the D3 defect, one
   layer downstream).
2. **Invented specifics inside competency values** —
   `"Microsoft Intune administration & configuration (device enrollment, profiles, app
   deployment)"` and `"ITSM operations … (documentation/runbooks)"`. None of
   enrollment/profiles/app-deployment/runbooks is in the JD. Mirrors the D5/D7 inventions.
3. **Added qualifiers** — `"… fundamentals and best practices"`, iOS/Android `"(… profiles …)"`.

These signals drive the question bank and the live interview, so the inflation still reaches
the candidate — just one stage past where enrichment was fixed.

### Root cause (in `prompts/v2/jd_signal_extraction.txt`)

The v2 prompt is structurally sound (FEW/high-density bar, consolidation, `purpose`
classification, provenance) but does not enforce fidelity at the level that matters — the
signal's **value (substance)**:

- The **CONSOLIDATE** examples teach writing parenthetical specifics
  (`"API integration & data transformation (REST/SOAP, JSON)"`). The model extends this to
  invent specifics not in the JD.
- The **tenure+tool bundling** guidance (`"1 year hands-on with Workato"`) leads the model
  to *fabricate* the bundling — pinning a general experience band onto a domain.
- **Provenance** says `ai_extracted` = "stated in the JD", but nothing makes clear that
  *every token* of an `ai_extracted` value must be JD-grounded, so invented substance gets
  mislabeled as extracted.

---

## Goal

Signal **substance** (the skill/requirement itself) must be faithful to the JD — restate,
never invent. Signal **classification** (type, priority, weight, stage, purpose, knockout,
seniority_level) remains inferred analytical judgment — that is the legitimate job of
extraction. Scales to any JD; no JD-specific text. Provenance stays the honest channel for
genuinely-implied substance (sparse, with a basis), per the approved policy
**"faithful substance + sparse, flagged inference."**

---

## Approach

Targeted rewrite of `prompts/v2/jd_signal_extraction.txt` — inject the enrichment fix's
spine (A: principled framing + ALLOWED/BANNED; B: extract-then-classify grounding) while
preserving v2's working domain guidance. Replace in place; `jd_signal_extraction_prompt_version`
stays `"v2"`. No schema change — the provenance + coverage validators in
`SignalItemV2`/`ExtractedSignals` already exist and are unchanged.

### Changes from current v2

1. **New core rule — substance vs classification.** A signal's substance must trace to the
   JD (restate, never invent — no specifics/sub-skills/tools/scope/scale/seniority the JD
   omits, not even in parentheses). Classification is inferred. Invented substance is a
   defect even if it sounds right.
2. **New Method (extract-then-classify = grounding B).** First identify the JD's stated
   requirements and note what it does NOT specify; then consolidate+classify only those. If
   substance doesn't trace back, drop it — or emit `ai_inferred` with a basis.
3. **Consolidation fixed.** A grouped signal may only name sub-items the JD itself states;
   added the negative example (JD says only "API integration" → signal stays "API
   integration", do not add "(REST/SOAP, JSON)").
4. **Experience-fidelity rule.** A general experience figure is restated as framed; never
   re-pinned onto a tool/domain. Bundle tenure+tool only when the JD itself bundles them.
5. **Provenance tightened.** `ai_extracted` = every word of the value grounded in the JD;
   `ai_inferred` = sparse implied substance + basis. HARD RULES extended to ban inferring
   scope/scale ("at scale", "in production").
6. **Company profile → reference-only** (domain understanding / acronym expansion; never a
   source of requirements or of the bar).
7. **New "Before you output" self-check** (traceability, no re-scoping, inferred-has-basis,
   FEW-not-padded).

**Preserved verbatim:** FEW/high-density bar, `purpose` skill/eligibility classification,
signal types, weight & knockout mapping, stage routing, coverage requirements, output
format.

### Defect coverage

| Defect (observed signal) | Closed by |
|---|---|
| Experience band re-scoped to EMM/MDM | Experience-fidelity rule (#4) + substance rule (#1) |
| Invented `(device enrollment, profiles, app deployment)` / `(runbooks)` | Consolidation fix (#3) + `ai_extracted` "every word grounded" (#5) + substance rule (#1) |
| Added `fundamentals and best practices`, `(profiles)` | Substance rule (#1) + self-check (#7) |
| (general) any "at scale"/"in production" inference | HARD RULES extended (#5) |

---

## Final prompt text — `prompts/v2/jd_signal_extraction.txt`

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

---

## Non-goals

- No change to `SignalItemV2` / `ExtractedSignals` schemas or their validators (provenance +
  coverage gates already exist and are unchanged).
- No change to the actor, `_build_user_message`, or model/effort config.
- No new prompt version (`jd_signal_extraction_prompt_version` stays `"v2"`).
- No JD-specific tuning.

## Operational notes

- Signal extraction runs in the lean `nexus-worker` (queue `jd_extraction`); `PromptLoader`
  caches in memory and the worker has no hot-reload → **restart `nexus-worker` after editing
  the prompt** (`docker compose up -d --force-recreate nexus-worker`).
- The active version is `v2` (`config.py: jd_signal_extraction_prompt_version = "v2"`); this
  file is what the actor loads.

## Validation (manual — matches the manual-testing preference)

Re-run extraction (via "Unlock & re-enrich") on the EMM job and confirm the new signals:
- experience signal restates `3–5 years` generally (NOT "in EMM/MDM operations");
- the Intune signal carries no `(device enrollment, profiles, app deployment)`;
- the ITSM signal carries no `(runbooks)`;
- no `at scale` / `in production` anywhere;
- any `ai_inferred` signal has a real basis and adds no years/scope/seniority;
- the list stays ~8–10, high-density.
Also spot-check a rich JD (signals faithful, not padded) and a thin JD (sparse, with at
most a couple of clearly-flagged `ai_inferred` signals).
```
