# JD Enrichment — Fidelity-First Rewrite

**Date:** 2026-06-17
**Status:** Design — approved, pending implementation
**Scope:** Prompt-only. Rewrites `prompts/v1/jd_enrichment.txt` and `prompts/v1/jd_reenrichment.txt` in place (no new version). No code, schema, or `_build_user_message` changes.

---

## Problem

QA (recruiting-domain lens) found that the current enrichment pass diverges substantially
from the raw JD — it invents scope, inflates seniority, and mishandles source fidelity.
The enriched JD is **downstream ground truth**: it is the source for signal extraction →
question-bank generation → the AI-led screening interview. So every fabricated line becomes
a criterion a candidate is graded against — a candidate who perfectly matches the *raw* JD
can be screened or interviewed against requirements the employer never set.

### Defects found (raw vs enriched, on a 3–5yr "EMM Engineer" JD)

Severity-tagged; each is an instance of one of three failure moves — **invent scope**,
**inflate seniority**, or **mishandle source fidelity**.

**Critical — seniority / experience mismatch (changes who is screened in/out):**
- **D1** — "at scale" fabricated on iOS *and* Android (raw: bare skill names). Senior signal.
- **D2** — "design" added to Intune scope (raw: "manage and configure"). Architect-level.
- **D3** — the overall `3-5 years` band re-pinned exclusively to EMM/MDM *and* "in
  production environments" added. Narrows + inflates the filter.
- **D4** — must-have SET no longer maps 1:1 to the raw: `EMM`+`MDM` merged into one line
  while new requirements ("…in production", "Ability to produce/maintain documentation")
  appeared. Distorts screening/knockout and loses raw granularity.

**High — fabricated responsibilities (each becomes an interview probe):**
- **D5** — entire device-enrollment lifecycle invented (enrollment, onboarding, retire/wipe).
- **D6** — "remediate non-compliant states **in collaboration with stakeholders**" invented.
- **D7** — "knowledge articles/**runbooks** … reduce recurring incidents" invented (raw: bare
  "knowledge management").
- **D8** — RCA ownership invented ("root-cause identification and follow-through to closure").
- **D9** — invented posture in Role summary ("audit-ready", "reliable", "own").

**Medium — integrity / faithfulness:**
- **D10** — title altered: `Test – EMM Engineer` → `… (Microsoft Intune)`. Requisition title
  is a keyed field; the AI must never rewrite it.
- **D11** — internal placeholder published: `Qualifications: As per requirement` preserved
  verbatim (inconsistently — `Good to Have: As per requirement` was correctly omitted).
- **D12** — "Conditional Access **tied to device compliance**" — added technical assertion.
- **D13** — ownership-framing shift ("Responsible for managing" → "you will **own**").

**Low:**
- **D14** — raw HTML (`<br />`) leaked verbatim into the markdown output.
- **D15** — *(acceptable, target behavior)* `Noida (Hybrid)` → `Location: Noida, India` +
  `Work arrangement: Hybrid`. Faithful normalization — the kind of enrichment we want.

### Root cause

The prompt's mental model is **"raw JD is a rough draft to improve into a polished
posting."** Specific lines drive the defects:
- `ENRICH … verifiable thresholds: "5+ years hands-on in X"` — a fabrication engine when
  the raw states no threshold; the model manufactures one (→ D1–D3).
- `RESTRUCTURE … group under 3–4 theme headers, concrete active-voice bullets` — forces
  padding when raw content is thin (→ D5–D8).
- `NEVER fabricate benefits, compensation, company mission, equal-opportunity language` —
  the only anti-fabrication rule, and it **excludes** the signal-bearing sections that
  actually drifted.
- `Enrich the Role using company profile` / (sibling) `use hiring_bar to calibrate language
  intensity` — turns reference context into a content/intensity source (→ D9).
- No rule protecting the title (→ D10), handling placeholders (→ D11), or normalizing markup
  (→ D14).

---

## Goal

Reframe enrichment from *improve into a polished posting* to **faithfully normalize**.
The model may change how information is **presented**; it may never change **what is
required**. A sparse-but-faithful JD is correct; a rich-but-fabricated JD is a defect.

Principle-based and scalable to all JDs — **no JD-specific examples or patches**. The only
illustrations in the prompt are abstract *categories* (e.g. "at scale"/"in production" named
as the *class* of seniority qualifier), never the EMM JD.

---

## Approach — A + B (combined)

- **B — Extract-then-render grounding (SOTA anti-hallucination):** the model first silently
  inventories the atomic facts the raw JD states (including noting what is *unspecified* —
  no scale, no seniority qualifier, no adjacent duties), then renders using **only** that
  inventory. Every output line must map to an inventoried fact. This makes invention
  structurally hard by binding the render step to an explicit source inventory.
- **A — Principled faithful-normalizer framing:** a one-line role redefinition + the
  reason it matters (downstream ground truth) + a closed **ALLOWED** transformation list
  (presentation only) + a closed **BANNED** transformation list (invention) + conditional/
  omittable sections + a final self-check against the Step-1 inventory.

A is the framing and guardrails; B is the discipline that makes them bite. A's banned-list
alone still leaves room for "helpful" drift; B's grounding closes it.

### Company profile / `hiring_bar` — demoted to reference-only

Still passed in the user message (cheap context; aids domain comprehension + acronym
expansion), context-before-document ordering preserved. But the prompt scopes it to
**comprehension only — never a source of duties, scope, or intensity**, and explicitly bars
using `hiring_bar` to raise/lower the role's bar. No `_build_user_message` change needed.

### Defect coverage

| Defect | Closed by |
|---|---|
| D1, D2, D3 | Banned: seniority/scale/maturity qualifiers; banned re-scoping a general experience range onto a skill |
| D4 | Banned: promoting responsibility→requirement / nice-to-have↔must-have; "keep the requirement set the same size and shape as the raw" |
| D5–D8 | "Common ≠ stated"; render bound to Step-1 inventory; "same count of distinct duties; no padding" |
| D9 | "The Role … no added posture or quality claims"; company profile reference-only |
| D10 | Header rule: "title (verbatim)"; banned: changing the title incl. appending a specialization/tool |
| D11 | Placeholders section: omit any section whose only content is a placeholder; never publish it |
| D12 | Banned: adding a technology/assertion not in the raw |
| D13 | "No added posture" (ownership framing) |
| D14 | Allowed: strip raw markup (e.g. `<br>`), render clean markdown |
| D15 | Allowed: normalize structured fields (location / experience split) — preserved as desired behavior |

---

## Final prompt text

### `prompts/v1/jd_enrichment.txt`

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

### `prompts/v1/jd_reenrichment.txt`

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

---

## Non-goals / out of scope

- No change to `EnrichmentOutput`/`ReEnrichmentOutput` schemas, `_build_user_message`,
  actors, state machine, or model/effort config.
- No structured-section schema or code-enforced faithfulness gate (prompt-only by request;
  could be a future hardening pass if drift persists).
- No JD-specific tuning.

## Operational notes

- Enrichment runs in the lean `nexus-worker` (queue `jd_enrichment`); re-enrichment on
  queue `jd_reenrichment`. `PromptLoader` caches prompt files in memory and the worker has
  **no hot-reload** → **restart `nexus-worker` after editing the prompts**
  (`docker compose up -d --force-recreate nexus-worker`).
- Prompt version logged/traced stays `v1` (in-place rewrite).

## Validation (manual — no CI eval suite)

Re-run enrichment on three JDs and diff enriched-vs-raw for any line that does not trace to
a raw fact:
1. **Thin** — the EMM Engineer JD. Expect: title verbatim; no "at scale"/"in
   production"/"design"; the `3–5yr` band not re-scoped onto a skill; no invented
   enrollment/runbooks/RCA/stakeholder duties; `Qualifications: As per requirement` section
   omitted; `<br>` stripped.
2. **Rich / well-written** — expect minimal change (formatting/grouping only), no added
   scope.
3. **Deliberately vague** — expect a sparse-but-faithful output, not a padded one.

Also re-run re-enrichment after a signal edit (add / remove / required↔preferred) and
confirm the prose reflects exactly the edit with no strengthening.
```
