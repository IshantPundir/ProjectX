# AI Screening as a Skills Test — Signal Extraction + Bank Consumption Redesign

- **Date:** 2026-06-12
- **Branch:** `feat/followups-governed-dimensions`
- **Status:** Design — approved direction, pending spec review
- **Builds on:** `2026-06-12-question-bank-v3-measurement-instrument-design.md` (the v3 bank pipeline this revises)

---

## 1. Why

A live v3 generation for the "Jr. Forward Deployed Engineer (Workato)" JD (job `ce6dad9a…`, stage `2ea4f4a3…`) exposed that the pipeline is working as designed — and the design is wrong for what an AI **screening** is supposed to do. Evidence from the generated bank (`prompt_version=v3`, critic ran):

- **28 min / 20 budget**, 11 questions — the critic *added* an education question and nothing trims to budget.
- **Behavioral-heavy**: 7 of 11 questions are claim/story/eligibility (`experience_check ×2`, `behavioral ×4`, `compliance ×1`); only 4 make the candidate reason.
- **Near-duplicate leads**: "tell me about a time you delivered one automation end-to-end" (behavioral) vs "tell me about a project you drove building an integration end-to-end" (project_deepdive). Our distinctness rule only dedups *follow-up dimensions*, not whole leads.
- **Claim-checks instead of skill tests**: "Can you work with Java, Python, or Ruby well enough to build integration logic?" — a yes/no, not a scenario that makes them *do* it.
- **An education question** ("your highest relevant degree") — manufactured by the critic to cover the `required` `BTech/BE` signal.

Root cause is two coupled problems:

1. **Signal extraction is noisy and mis-purposed.** 24 signals for a JD with 8 Must-Haves: pre-screen eligibility facts (4+ years, degree, certs) extracted as testable signals; every nice-to-have and soft responsibility inflated into a signal; core Must-Haves under-tagged (only `Workato`/`a language` are knockout). Garbage-in.

2. **The bank faithfully covers all of it**, and the v3 prompt actively **down-weights situational/scenario** (P1, inherited from job-performance-validity research) — exactly backwards for a *skills* screen where the recruiter has already pre-screened experience.

### Goal

Make the **AI screening stage a technical-skills test**: few high-impact, JD-accurate signals classified by purpose; the bank tests **only the skills**, via **scenarios** the candidate must reason through, within budget.

### Non-goals (explicitly deferred)

- Changing the JD enrichment or re-enrichment prompts (only signal extraction changes).
- Deprecating/replacing the existing `signal.stage` (screen/interview) field — it serves the engine; we add `purpose` alongside it and leave `stage` untouched (a future consolidation, out of scope).
- Other stage types' recipes (`phone_screen`, etc.) — only `ai_screening` is redesigned here. (A future `phone_screen` bank is the natural home for the eligibility/claim checks we're removing.)

---

## 2. The signal model — add `purpose` (no DB migration)

Signals are stored as JSONB (`job_posting_signal_snapshots.signals`), so this is a Pydantic-schema + prompt + consumer change, **not** a column migration.

Add to `SignalItemV2` (`app/ai/schemas.py`):

```python
SignalPurpose = Literal["skill", "eligibility"]
# on SignalItemV2:
purpose: SignalPurpose = "skill"   # default skill → old snapshots stay tested (no regression)
```

- **`skill`** — a technical/behavioral competency the AI screen *assesses* (Workato workflow design, REST API design, JSON transformation, DB reasoning under load, agent-workflow design).
- **`eligibility`** — a pre-screened fact the AI screen does **not** test: tenure ("4+ years"), a specific-tool *duration* ("1 year Workato" — the *duration* is eligibility; the *Workato skill* is a separate `skill` signal), degrees, certifications. Kept in the signal set (recruiter sees it, report/other stages may use it), excluded from AI-screen question generation.

Default `"skill"` means a legacy snapshot (no `purpose`) behaves exactly as today — every signal eligible for testing — so nothing regresses before re-extraction.

New `ExtractedSignals` validator: **≥1 `skill` signal** (the AI screen must have something to test).

---

## 3. Signal extraction rewrite → `prompts/v2/jd_signal_extraction.txt`

Bump to **v2** via a configurable version (preserve the EEOC audit trail; mirrors the v3 bank bump). Add `ai_config.jd_signal_extraction_prompt_version` (default `"v2"`); the extraction actor reads `PromptLoader(version=…).get("jd_signal_extraction")`; the stored snapshot `prompt_version` becomes the configured value. v1 stays as an immutable provenance artifact. (Enrichment/re-enrichment stay on the shared v1 loader — unchanged.)

Prompt changes:

- **Lean, high-signal-density — target ~8–10 core signals.** Extract the JD's real Must-Haves + the few highest-impact responsibilities. Do **not** manufacture a signal from every keyword, nice-to-have, or boilerplate responsibility. Capture genuinely-differentiating Good-to-Haves sparingly at `weight=1`/`preferred`; skip the rest.
- **Consolidate the grain — one signal = one unit of assessment.** Combine closely-related sub-skills that you would test together into a single grouped signal (e.g. "REST APIs", "SOAP/XML", "JSON data formats" → one **"API integration & data transformation (REST/SOAP, JSON)"** signal). The grain rule: a signal should map to roughly **one scenario question + one rubric**. Don't over-combine distinct competencies; don't fragment one competency into many.
- **Classify `purpose`.** Durations/degrees/certs → `eligibility`. Competencies/skills/behaviors → `skill`. Where a JD line bundles tenure with a tool ("1 year Workato"), emit **both**: an `eligibility` duration signal **and** the distinct `skill` competency the duration refers to.
- **Accurate Must/weight/knockout mapped to the JD's own structure.** The JD's "Must-Have Skills" → high-weight `skill` signals; "Good-to-Have" → low-weight/`preferred`; non-negotiable eligibility (a hard legal/role gate) → `knockout` on the `eligibility` signal, not on skills.
- Keep existing discipline: provenance (`ai_extracted`/`ai_inferred` + `inference_basis`), the no-discriminatory-inference hard rules, `stage` assignment (unchanged, for the engine), max-5-knockout cap, role_summary, seniority.

---

## 4. Question-bank `ai_screening` recipe rewrite (v3 prompts + critic)

Edit the v3 ai_screening prompt + critic (no new version — v3 is current and unconfirmed banks regenerate):

- **Consume only `purpose=skill` signals.** The bank actor filters the snapshot signals to `purpose != "eligibility"` before building the user message for an `ai_screening` stage; eligibility signals are skipped entirely (no degree/tenure/cert questions). `signal_filter.include_types` stays as the coarse type filter; the `purpose` filter applies on top. (Legacy signals without `purpose` default to `skill` → still tested.)
- **Reverse the v3 P1 rule for `ai_screening`.** Situational/scenario is the **primary** format: `technical_scenario` (reason/design/debug aloud) dominates, plus exactly **one** `project_deepdive`. **Drop `experience_check`** for this stage — replace every "have you used X" with a scenario that makes them use it. **Drop `compliance_binary`/education** (eligibility is gone). The "format-by-seniority" framing is replaced for ai_screening by "format-by-purpose: skills are tested by scenarios."
- **≤1 behavioral**, only for a genuinely high-weight soft-skill (`type=behavioral`) signal — and even then prefer letting the `project_deepdive` surface ownership/collaboration organically.
- **Lead-level distinctness.** Extend the distinctness rule: no two **lead questions** may probe the same underlying thing (not just no two follow-up dimensions). The project_deepdive and any behavioral must not restate each other.
- **Budget discipline.** Generate fewer, deeper questions that fit the stage duration (~20 min ⇒ ~5–6 scenario questions at 3–4 min). The **critic trims to budget** (drops the lowest-priority question) instead of only logging a soft warning; the reconcile's soft-warning stays as a backstop.
- **Critic checklist updated** to enforce all of the above: scenario-dominance, zero eligibility/education/claim-check questions, ≤1 behavioral, lead-level dedup, fits-budget (trim if over), and the existing coverage/anchor-sharpness/tripwire checks (now over `skill` signals only).

---

## 5. Consumers to keep honest

- **Report (`reporting`)** — eligibility signals now have no AI-screen question. The report must **not** flag an un-questioned eligibility signal as a coverage gap or penalize the candidate for it (it's pre-screened, out of the AI screen's remit). Verify the per-signal rollup skips/neutralizes `purpose=eligibility` signals that have no `QuestionRecord`. (If the rollup already only scores signals that were asked, this may be a no-op — confirm.)
- **Knockouts shift off the AI screen.** Dropping `experience_check`/`compliance_binary` + filtering eligibility means the ai_screening bank will typically have **no binary knockout questions** and **no `is_mandatory` questions**: eligibility knockouts are pre-screened by the recruiter, and a *skill* "must-have" is now demonstrated (or not) inside a scenario, graded by its rubric — not a yes/no gate. Confirm the engine's verified-knockout gate (`brain/policy.gate_knockout`) and the report's knockout-gating both no-op gracefully when a bank carries zero knockout/mandatory questions (they should — knockouts are already optional per bank — but verify, since this makes "zero" the common case for ai_screening).
- **Engine (`interview_runtime`/`interview_engine`)** — still receives `signal.stage`; `purpose` is additive and need not reach the engine. No engine change expected; confirm the `SessionConfig` build tolerates the new key.
- **Recruiter Signal Inspector (`frontend/app`)** — surface `purpose` (skill vs eligibility) on each signal with an edit toggle, so the recruiter can re-classify. Show it where `type`/`weight`/`knockout` are shown.
- **`starter_pack.py`** — the `ai_screening` stage's `signal_filter.include_types` stays; no change required (purpose filter is applied in the bank recipe, not the stage config).

---

## 6. Implementation surface & sequencing

**One spec, implemented as two plans executed back-to-back** (reviewed together at the end):

**Plan 1 — Signals**
| Area | Change |
|---|---|
| Schema | `app/ai/schemas.py`: `SignalPurpose` + `purpose` on `SignalItemV2`; `≥1 skill` validator |
| Prompt | `prompts/v2/jd_signal_extraction.txt` (lean + consolidate + purpose + accurate Must) |
| Config | `ai_config.jd_signal_extraction_prompt_version` (default `v2`) + `app/config.py` setting |
| Actor | `jd/actors.py`: extraction call uses the versioned loader; stamps the configured prompt_version |
| Frontend | Signal Inspector `purpose` display + toggle |
| Tests | extraction prompt-quality eval (low noise/count, purpose accuracy, Must tagging, consolidation) |

**Plan 2 — Bank**
| Area | Change |
|---|---|
| Recipe | v3 ai_screening prompt: purpose=skill only, scenario-primary, drop experience_check/compliance, ≤1 behavioral, lead-dedup, budget-trim |
| Critic | `prompts/v3/question_bank_critic.txt` + checklist enforces the above (incl. trim-to-budget) |
| Actor | `question_bank/actors.py`: filter snapshot signals to `purpose != eligibility` for ai_screening |
| Report | confirm/adjust `reporting` tolerates un-questioned eligibility signals |
| Tests | bank evals: scenario-dominance, no eligibility/experience_check Q, ≤1 behavioral, fits budget, lead-dedup |

---

## 7. Testing

- **Extraction eval** (`-m prompt_quality`, real API): on the Workato JD fixture (and 2–3 others), assert signal count ≤ ~12, every eligibility-shaped requirement (years/degree/cert) is `purpose=eligibility`, every Must-Have skill is `purpose=skill` with weight ≥2, and no manufactured nice-to-have boilerplate signals.
- **Bank eval** (`-m prompt_quality`): on a `purpose`-classified signal set, assert the bank is scenario-dominant (≥70% `technical_scenario`+`project_deepdive`), **zero** `experience_check`/`compliance_binary`, ≤1 `behavioral`, total_minutes ≤ stage duration, and no two leads are near-duplicates (LLM-judge or dimension/primary-signal overlap heuristic).
- **Unit**: schema (`purpose` default + validator); bank actor filters eligibility signals; report neutralizes un-questioned eligibility signals.
- **Live smoke**: re-extract signals for the Workato JD (confirm ~8–10, correct purpose/weights), regenerate the bank (confirm scenario-dominant, ≤20 min, no education/duplicate/claim-check Q).

---

## 8. Code-quality mandate (production-grade, no hacks / no dead code)

- `purpose` defaults to `skill` so legacy snapshots never regress — a deliberate, documented backward-compat default, not a silent fallback.
- v1 extraction prompt retained as immutable provenance (EEOC audit trail), v2 is the new file — same discipline as the v3 bank bump; not stale code.
- No second overlapping classification: `purpose` is additive and orthogonal to `stage`; the distinction is documented at the schema. We do not duplicate `type=behavioral` into a third purpose value.
- The bank's eligibility filter is applied once, in the actor's user-message builder, not scattered.
- Every new branch (purpose filter, ≥1-skill validator, budget-trim, report neutralization) ships with a test in the same change.
