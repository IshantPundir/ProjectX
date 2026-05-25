# Report Scoring Engine — Design Spec

- **Date:** 2026-05-25
- **Status:** Approved (design); ready for implementation plan
- **Author:** Ishant + Claude
- **Scope:** Backend offline scoring engine for the candidate evaluation report (root CLAUDE.md Phase 3D `reporting`). This is **sub-project A** of three (A: report engine → B: session recording → C: highlight reel). This spec covers A's **backend core only**; the recruiter-facing report UI is a fast follow-up spec (A2).
- **Engine scope:** **v2 sessions only.** Interview engine v1 is deprecated and will be deleted; no v1 code paths, no version-aware reader, no backward-compatibility burden. No real users yet (testing/development phase) — fresh v2 sessions are acceptable for validation.

---

## 1. Background & strategic framing

The `reporting` module is currently a stub (`GET /api/reports/{session_id}` → `{"status":"not_implemented"}`). The `analysis` module (real-time scoring/probe selection) was **superseded by the v2 brain** and is dead; reporting will not depend on it (flagged for later removal).

Report generation is the core of the product: it turns a completed interview into a defensible, auditable candidate evaluation a recruiter can trust in seconds and defend under scrutiny.

### Design philosophy (locked)

1. **The interviewing AI agent only COLLECTS signals; it does NOT judge. The report module IS the scorer/judge.** The v2 brain's in-session grades/coverage are *collection telemetry*, not the score of record. The report re-scores the frozen transcript, offline. (This matches LLM-as-judge best practice: a separate offline judge, not the conversational model grading itself.)

2. **Dual frame: candidate score + evidence-confidence**, where **confidence = *opportunity*** ("did we give the candidate a fair shot to demonstrate this signal?"), **not** answer-quality. This is the single most important concept; all four research tracks converged on it independently.
   - **Low opportunity** (signal never asked, or asked then cut off with no probe) → `not_assessed`; excluded from the score, never penalized. Protects a strong candidate the interview fumbled.
   - **High opportunity** (asked + probed/clarified) + thin/evasive/red-flag answers → `below_bar`, scored low, **confidence high**. Catches the bluffer who got every chance. (The 2026-05-25 test session `e4072361` was exactly this: a genuinely weak candidate given 11 probes + 6 clarifies → a confident reject is defensible.)
   - Consistent with the existing engine principle that a bare "I don't know" *to an on-target question* = no experience.

3. **Scores are integers (0–100) that roll up to a banded verdict.** Integers enable tiering/ranking; the **band is the primary recruiter signal**; small integer gaps never drive an auto-decision (no false precision). Show the Overall integer, the per-dimension integers, **and** the per-signal breakdown ("show your work" is the defensible move).

4. **Knockout-gated + weighted-compensatory roll-up.** A confidently-failed must-have caps the verdict regardless of other strengths; past the hurdles, signals combine by weight.

5. **AI recommends; a human decides.** The human decision is a separate, logged field. Borderline can never be auto-resolved (root CLAUDE.md invariant) — it is the human gate, equivalent to the LLM-judge "Trust-or-Escalate" low-confidence → human escalation.

### Research grounding (summary)

Deep research across four tracks; full findings in conversation, key sources at the end of this doc.
- **I-O psychology:** structured interviews are top-tier predictors; **BARS** (behaviorally anchored rating scales) are the defensibility backbone; score per-answer then roll up; **mechanical aggregation (.44) ≫ holistic (.28)**; "insufficient evidence" must be distinct from "low score."
- **Compliance (LL144 / EEOC / AIVIA / EU AI Act / GDPR):** per-decision audit trail, evidence linkage, meaningful human override (SCHUFA: a rubber-stamp doesn't count), reproducibility, retention; score only **observable, job-related, verbal-content** behaviors — never facial/affect/demographic (the HireVue cautionary tale).
- **LLM-as-judge SOTA:** explicit anchored low-cardinality rubric + reasoning-before-score; **mandatory evidence-span grounding, programmatically verified**; separate offline judge (different model than the generator); selective self-consistency for confidence; a full scoring manifest for reproducibility.
- **Recruiter UX:** banded verdict (never a decimal), per-competency breakdown where **every score shows its evidence**, explicit "insufficient evidence" states, AI-as-recommendation with a separate human decision.

---

## 2. The scoring substrate that already exists

The question bank and signal schema already encode most of what a defensible score needs (verified against the live test job `ce6dad9a-…`, AI stage `2ea4f4a3-…`, 8-question bank):

- **Per-question BARS rubric** (`excellent / meets_bar / below_bar`) + `positive_evidence` + `red_flags` + `evaluation_hint` + `follow_ups`.
- **Per-question:** `signal_values`, `is_mandatory`, `question_kind` (experience_check / technical_scenario / behavioral / …), `difficulty`.
- **Per-signal metadata:** `type` (`experience / competency / behavioral / credential`), `weight` (1–3), `knockout` (bool), `priority` (required / preferred).
- **Stage:** `pass_criteria: {type: "all_knockouts_pass"}`, `signal_filter.include_types`.

The signal `type` maps directly onto the score dimensions; `weight` is the mechanical aggregation weight; `knockout` is the non-compensatory hurdle.

---

## 3. Architecture & data flow

Scoring runs **offline and async** — never on the candidate's or recruiter's request path.

```
record_session_result()  (interview_runtime/service.py — exists)
  session: active → completed ──► after commit: enqueue Dramatiq
                                  score_session_report(session_id, tenant_id, correlation_id)
                                       │  (v2 + AI-stage only; idempotent)
                                       ▼
                          ┌─ Report Scorer (new) ────────────────────────┐
  INPUTS                  │ 1. segment transcript ↔ envelope             │
  • sessions.transcript   │ 2. opportunity per question/signal           │ ──► session_reports row
    (agent Qs + cand text │ 3. per-answer BARS rating (LLM, evidence)     │     + report JSON
     + timestamps)        │ 4. evidence-grounding verification           │     + scoring manifest
  • audit envelope JSON   │ 5. per-signal → per-dimension (pure code)     │     + frozen rubric snapshot
    (turn.decision: grade,│ 6. knockout gate → Overall → tier → verdict  │
     probes, attr signals)│ 7. summary + evidence (LLM, grounded)        │
  • the bank (rubric,     └───────────────────────────────────────────────┘
    weights, types, knockout, pass_criteria)
```

### Inputs (three sources)

1. **`sessions.transcript`** — primary scoring input. The **only** place the agent's spoken question text lives.
2. **Audit envelope** (`engine-events/<session_id>.json`, via `sessions.raw_result_json.audit_envelope_ref`) — the **opportunity** signal: per-turn `turn.decision` (`grade`, `attributed_signals`, `move`), `directive.delivered` acts (ASK/PROBE/ACK_ADVANCE/CLARIFY/CLOSE), and counts of probes/clarifies. Also `turn.captured` (word counts).
3. **The bank** — rubric, `positive_evidence`, `red_flags`, `signal_values`, per-signal `type/weight/knockout/priority`, stage `pass_criteria`. Frozen into the report's `rubric_snapshot` for reproducibility.

### The transcript ↔ envelope join (first-class, tested)

There is no single object linking "question Q → candidate answer → opportunity." `scoring/transcript.py` reconstructs it by joining transcript turns (by `timestamp_ms`) with envelope decisions (by `turn_ref`), producing a list of **scored units**: `{question_id, question_text, candidate_answer_text, answer_quote_spans, probes_fired, clarifies, candidate_engaged}`. This is the backbone everything scores off and is the most bug-prone step → exhaustively tested against the real `e4072361` fixture.

---

## 4. The scoring model (the defensible core)

All thresholds are **configurable**, defaulting as below, anchored to `org_unit.hiring_bar` where available.

### 4.1 Level → integer anchors

The LLM judge picks a discrete **BARS level**; code maps to points. The judge never emits a number.

| Level | Points |
|---|---|
| `excellent` | 100 |
| `meets_bar` | 70 (the pass line) |
| `below_bar` | 30 |
| `not_assessed` | excluded from all math |

### 4.2 Opportunity (code, from envelope) — the not_assessed vs below_bar decision

Per scored unit / signal:
- `full` — asked **and** (≥1 probe fired **or** candidate gave a substantive answer)
- `partial` — asked but candidate barely engaged and no probe followed
- `none` — never asked, or instant non-answer with zero probe

A "substantive answer" = the candidate's accumulated answer to that question clears a small word-count floor (default 8 words) **and** was not classified by triage as `no_experience`/`off_topic`/`backchannel`. (Floor is a tunable in §11.) A bare on-target "I don't know" is therefore `full` opportunity if a probe fired or the question was clearly delivered — i.e. it counts as evidence of no experience, not as `not_assessed`.

**Rule:** a `below_bar` rating counts as a real low score **only when opportunity = `full`**. With `partial`/`none` opportunity, the signal is `not_assessed`.

### 4.3 Per-answer rating (LLM judge, `scoring/judge.py`)

For each **delivered** question, **one narrow judge call** grades the candidate's answer against *that question's* BARS rubric (the per-question rubric is the single criterion). Structured output via `instructor` (project convention: `Mode.TOOLS_STRICT`, `response_model=`), with **evidence/justification fields ordered before the score** (field order = output order, so the model commits evidence first):
```
JudgeVerdict (Pydantic, strict):
  evidence_quotes: list[str]      # verbatim spans from the transcript  (FIRST)
  red_flags_hit:   list[str]
  justification:   str            # maps evidence → rubric anchor
  level:           Literal["below_bar","meets_bar","excellent"]   # bounded enum (LAST)
```
- **Model & params:** pinned via `AIConfig.report_scorer_model` — a **strong reasoning model** (OpenAI eval guidance: o-series/GPT-5 class excel at rubric grading), distinct from the realtime mini the brain uses. `reasoning_effort` = `medium` (gated per the AIConfig effort contract), `verbosity` = `low`. **Do NOT send `temperature`/`top_p`/`seed`** — reasoning models reject them (HTTP 400). **Do NOT add "think step by step" prose** — the model reasons internally; we externalize only the structured evidence/justification fields above.
- **Reproducibility** (since temp/seed are unavailable) comes from: a **frozen, versioned prompt + rubric snapshot**, a **strict enum-bounded schema**, a **byte-stable prompt prefix**, and **selective N-sample** aggregation.
- **Selective self-consistency:** single pass for clear cases; **N-sample (median/majority)** only when the answer is near a knockout or evidence-grounding is weak → a confidence band where it matters; keeps cost within the ≤30s p50 / ≤60s p95 async SLA.
- Prompt construction (stable prefix / dynamic suffix / caching) is specified in **§4.9**.

### 4.4 Evidence grounding (`scoring/grounding.py`)

Every `evidence_quote` must verify as a real substring (fuzzy-normalized for STT/whitespace) of the transcript. A quote that does not ground → the score is flagged `evidence_unverified` and excluded from "confident" status. Kills hallucinated competence; this verification result is stored.

### 4.5 Per-signal rating (pure code, `scoring/aggregate.py`)

A signal touched by ≥1 question → one signal level over its engaged questions:
- `not_assessed` if no question hit it with `full`/`partial` opportunity;
- `below_bar` if the best answer is below bar, **or** red_flags hit with nothing reaching meets_bar;
- `excellent` if ≥1 grounded `excellent` and no unresolved red_flag;
- `meets_bar` otherwise.

Per-signal record: `{value, type, weight, knockout, level, score, opportunity, evidence:[{quote, timestamp_ms, question_id}], covered_by:[question_ids]}`.

### 4.6 Dimensions & Overall (pure code)

Weighted mean over **assessed** signals (weights = signal `weight`):
- **Technical** = mean over assessed `competency`+`experience`+`credential` signals.
- **Behavioral** = mean over assessed `behavioral` signals.
- **Communication** = content-level delivery (§4.8), shown separately as its own dimension; **informational — NOT included in the Overall score by default** (a screen shouldn't reject a strong engineer for being soft-spoken). Configurable to fold in at a low weight later (§11).
- **Overall** = weighted mean over all assessed JD signals (Technical + Behavioral pool).

Each dimension also reports **coverage = assessed weight ÷ total weight**, and a derived confidence (`high/medium/low`). Low coverage = low confidence.

`dimension_score = Σ(weight_s · score_s) / Σ(weight_s)` over assessed `s` in the dimension. Empty (all `not_assessed`) → `null` score, coverage 0, flagged.

### 4.7 Knockout gate → verdict (non-compensatory, pure code)

Per knockout signal: `PASSED` (meets_bar+ at adequate opportunity) / `FAILED` (`below_bar` at `full` opportunity) / `INSUFFICIENT` (low opportunity or `not_assessed`).

Verdict resolution:
1. any knockout **FAILED** → verdict = **reject** (Overall still computed + shown; reason: "failed must-have: `<signal>`" + evidence).
2. else any knockout **INSUFFICIENT** → verdict = **borderline → human review** (reason: "couldn't confirm must-have: `<signal>`").
3. else (all knockouts PASSED) → verdict from the Overall tier:
   - **advance** ≥ 75 · **borderline** 55–74 · **reject** < 55
   - **Coverage override:** if Overall ≥ 75 but `overall_coverage` < `min_coverage_for_advance` (default 0.6) → forced **borderline** ("not enough assessed to advance confidently").

Note: experience/years knockouts pass on the candidate's verbal claim (a screen can't disprove "5 years"); bluffing is caught at the **competency** knockouts and the Technical dimension, where demonstration — not claim — is graded. (Validated on `e4072361`: the programming-proficiency knockout fails on a dismissive thin answer → confident reject pointing at the quote.)

### 4.8 Communication (content-only, until recording exists)

No audio yet → judge from transcript **content** only: structure/coherence, relevance (on-topic vs ramble/deflect), specificity vs buzzwords. Explicitly **does not** penalize obvious STT garble (e.g. "5xx" → "five x"). 3 levels → 100/70/30. Labeled *"content-only; full communication scoring pending session recording (sub-project B)."*

### 4.9 Prompt engineering & prompt caching (judge prompt construction)

Follow the project's established stable-prefix / dynamic-suffix pattern (as `interview_engine_v2/brain/input_builder.py` does) so OpenAI **automatic prefix caching** maximizes hit-rate — at 500+ sessions/job this is a large cost win, because every candidate answering the same question shares the same cached prefix.

**Prompt = stable PREFIX (cacheable, byte-frozen) then variable SUFFIX (last):**
- **Prefix** (developer message, version-frozen): grader persona + task framing; `<rubric>` (excellent/meets_bar/below_bar for this question); `<criteria>` with `<positive_evidence>`/`<red_flags>`; optional `<exemplars>` (one anchored example per BARS level — static, so it caches once per TTL window); `<output_spec>` (cite verbatim quotes; grade **only on provided evidence**, no outside knowledge / no assumed competence; evidence before score; never auto-resolve borderline). Use **XML-style delimiters** (GPT-5 instruction-adherence guidance).
- **Suffix** (user message, the ONLY variable part, placed LAST): `<transcript>…the candidate's answer + relevant exchange…</transcript>`.
- The `instructor`/Pydantic **JSON schema is part of the cacheable prefix** → pin `instructor` + `pydantic` versions and keep field order/descriptions frozen, or the schema serialization shifts and silently breaks the cache.

**Caching mechanics & ops:**
- Caching is automatic for prompts ≥ **1024 tokens**, matching the **longest common prefix**; keep all per-candidate/dynamic values (names, ids, timestamps, the answer) in the trailing suffix — anything dynamic placed early poisons the whole downstream prefix.
- Set `prompt_cache_key = "judge:{prompt_version}:{bank_id|rubric_version}:{question_id}:{model}"` — shared across all candidates grading that question, **never per-candidate** (that defeats reuse). Keep < ~15 req/min/key.
- On gpt-5.5+, set `prompt_cache_retention="24h"` (offline/batch judge benefits from extended retention).
- **Measure** `usage.prompt_tokens_details.cached_tokens / prompt_tokens` via `create_with_completion(...)`; record it in the manifest and log it (alert if hit-rate drops → a prefix-stability regression, e.g. a rubric/schema change). The brain already logs `cached_tokens` this way.
- **Audit prompt+rubric for contradictions** — on reasoning models, conflicting instructions waste reasoning tokens and degrade output.

Judge prompts are versioned `.txt` under `prompts/v{n}/report_scorer/` loaded via `PromptLoader` (project convention); the version is pinned in `AIConfig.report_scorer_prompt_version` and snapshotted into the manifest.

---

## 5. Data model

### Table `session_reports` (new, tenant-scoped, RLS)

One current report per session; a regenerate **replaces in place** and bumps a `version` counter (multi-version history deferred — see §9). The `scoring_manifest` + frozen `rubric_snapshot` keep each report self-auditable and re-derivable.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `tenant_id` | uuid FK clients | RLS key |
| `session_id` | uuid FK sessions | **unique** (one current report/session) |
| `assignment_id` | uuid FK candidate_job_assignments | for candidate/job lookup |
| `version` | int default 1 | increments on regenerate |
| `status` | text | `pending / generating / ready / failed` (CHECK) |
| `generation_error` | text null | user-safe error on failure |
| `engine_version` | text | "v2" |
| `verdict` | text | `advance / borderline / reject` (CHECK) |
| `verdict_reason` | text | e.g. "failed must-have: …" |
| `overall_score` | int | 0–100 |
| `overall_coverage` | numeric | 0–1 |
| `overall_confidence` | text | `high / medium / low` |
| `dimension_scores` | jsonb | `{technical,behavioral,communication: {score,coverage,confidence}}` |
| `knockout_results` | jsonb | `[{signal,status,reason,evidence}]` |
| `signal_scorecards` | jsonb | per-signal (see §4.5) |
| `question_scorecards` | jsonb | `[{question_id,level,evidence,red_flags_hit,probes_fired,opportunity}]` |
| `summary` | jsonb | `{headline,strengths[],gaps[],rationale}` |
| `rubric_snapshot` | jsonb | frozen bank+rubric+signal-meta used |
| `scoring_manifest` | jsonb | `{scorer_model,reasoning_effort,verbosity,prompt_version,prompt_cache_key,scorer_code_version,bank_id,signal_snapshot_id,n_samples,cache_hit_rate,evidence_grounding_summary,generated_at,correlation_id}` |
| `human_decision` | jsonb null | `{decided_by,decision,rationale,decided_at}` — separate from AI verdict |
| `generated_at` | timestamptz null | |
| `created_at`,`updated_at` | timestamptz | |

**Migration `0047_session_reports.py`:** create table; canonical RLS policy pair (`tenant_isolation` USING+WITH CHECK on `tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid`; `service_bypass` on `app.bypass_rls`); indexes on `session_id` (unique), `assignment_id`, `(tenant_id, verdict)`. **Rollback script required** (per migration rule). `_assert_rls_completeness` must pass at boot.

---

## 6. API (`reporting/router.py`, prefix `/api/reports`)

All RBAC-guarded (recruiter / hiring-manager / admin, tenant-scoped) and rate-limited per the root standard (authenticated class: 600/min per-IP, 10k/min per-tenant).

| Method + path | Purpose |
|---|---|
| `GET /api/reports/session/{session_id}` | current report; `202` while pending/generating, `404` if none |
| `GET /api/reports/{report_id}` | a report by id |
| `POST /api/reports/session/{session_id}/regenerate` | admin/senior; re-score (bumps version) |
| `POST /api/reports/{report_id}/decision` | record human decision; writes `audit_log`; Borderline cannot be auto-resolved |

Response types live co-located in `reporting/schemas.py` (rebuilt; the thin stub `SessionReport`/`QuestionScorecard` are replaced).

---

## 7. Trigger & async

- In `record_session_result`, **after** the commit setting `completed`, if engine is v2 and stage is AI-driven, enqueue `score_session_report.send(session_id, tenant_id, correlation_id)` (existing Dramatiq broker).
- Actor `reporting/tasks.py::score_session_report`: loads inputs, runs the scorer, persists the report. **Idempotent** — skips if a `ready` report exists unless `force` (regenerate). On failure → `status=failed` + `generation_error`, retriable.
- Target: ≤30s p50 / ≤60s p95 (async-LLM SLA).

---

## 8. Auditability, compliance, observability

- **Reproducibility:** `scoring_manifest` (model, reasoning_effort, prompt+code versions, ids) + frozen `rubric_snapshot` ⇒ any score re-derivable and explainable months later (the EEOC / EU-AI-Act / NYC LL144 audit artifact).
- **Observability via OpenTelemetry** (the codebase dropped Langfuse for OTel — see `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`; root CLAUDE.md still says "Langfuse" and is stale on this). Trace every scoring run with the `app/ai/tracing.py::set_llm_span_attributes` pattern (prompt_name, prompt_version, model, tenant_id, session_id, correlation_id). Candidate evaluation data must not be exported to any third-party backend without a sub-processor agreement.
- **`audit_log`** entries on report generation and on human decision (`actor_id, tenant_id, action, resource_type=session_report, resource_id, correlation_id`).
- **PII discipline:** evidence quotes (the candidate's own words) live in the tenant DB report as the evaluation evidence — expected — but **never in logs/Sentry**. Log ids and `<n> chars`, never quotes; knockout reasons are PII-scrubbed at construction.
- **Job-relatedness:** every score traces to a JD-derived signal + its rubric anchor. No facial/affect/appearance/demographic scoring, ever.
- **Compliance deferral (model not precluded):** the report stores verdict+score+tier+job+timestamp so joining demographics later enables LL144 impact-ratio computation. Bias-audit *computation* + public summary are **explicitly deferred** to a compliance phase.

---

## 9. Scope boundaries

**In:** transcript↔envelope segmentation, opportunity, LLM per-answer judge, evidence grounding, deterministic aggregation (signal → dimension → knockout gate → Overall → tier → verdict), `session_reports` table + migration + RLS, Dramatiq trigger + idempotency, the four API endpoints, scoring manifest + frozen rubric snapshot + Langfuse trace + audit_log, tests. **v2 sessions only.**

**Deferred (named):**
- Recruiter report **UI** (sub-project A2 — next spec).
- Session **recording** + full playback + deep-linked evidence-to-video (sub-project B).
- **Highlight reel** (sub-project C — needs A + B).
- **Multi-version report history** (one row/session for now; manifest keeps each report auditable).
- LL144 **bias-audit computation** + public summary.
- v1-engine session support (v1 deprecated, to be deleted).
- PDF export.

---

## 10. Testing (hits the mandated gates)

- **New tenant-scoped table → cross-tenant read returns 0 rows** (mandatory RLS test).
- **`scoring/aggregate.py` is pure** → exhaustive golden tests: (level, opportunity) inputs → exact signal/dimension/Overall/tier/verdict; knockout PASSED/FAILED/INSUFFICIENT → correct caps; coverage-override → borderline; `not_assessed` vs `below_bar` boundary.
- **Opportunity** classification from envelope fixtures.
- **Evidence grounding:** a quote absent from the transcript is flagged.
- **transcript↔envelope segmentation** tested against the real `e4072361` fixture; expected end-to-end (with recorded/mocked judge outputs): `verdict=reject`, failed programming-proficiency knockout, evidence quote present.
- **LLM judge mocked at the `app/ai` boundary** (project convention — no real LLM in tests).
- **Stable-prefix snapshot test:** the rendered judge prefix is asserted byte-stable across two calls with different answers (guards the prompt-cache investment — catches accidental prefix drift from a rubric/schema edit).
- Migration up/down; idempotent re-enqueue (no duplicate); `_assert_rls_completeness` passes.

---

## 11. Open tunables (defaults set; revisit with real data)

- Level anchors 100/70/30; tier bands 75 / 55; `min_coverage_for_advance` 0.6.
- N-sample count + when to trigger.
- Whether Communication ever folds into Overall (default: no).
- `report_scorer_model` choice + `reasoning_effort` (default `medium`; bump to `high` if accuracy needs it). Self-preference is largely moot since the brain doesn't score; a non-OpenAI-family judge remains a future option.
- New `AIConfig` knobs (env-driven, no hardcoding): `report_scorer_model`, `report_scorer_effort` (gated, may be empty), `report_scorer_verbosity`, `report_scorer_prompt_version`, `report_scorer_n_samples`. Note: if a `seed`/`temperature`-reproducible judge is ever required, that forces a **non-reasoning** model (gpt-4o/4.1 class) — a deliberate trade against grading quality.

---

## 12. Key sources

- I-O psych: Sackett et al. 2022 (JAP); Campion, Palmer & Campion 1997 (15 components of structure); Kuncel et al. 2013 (mechanical > clinical); OPM *Structured Interviews: A Practical Guide*; SIOP *Principles* (2018) + SIOP AI-assessment considerations (2023).
- Compliance: NYC Local Law 144 / DCWP AEDT rules; EEOC EEOC-NVTA-2023-2; IL AIVIA (820 ILCS 42) + IL HB 3773; EU AI Act Annex III/Art. 26/86; GDPR Art. 22 + CJEU SCHUFA C-634/21.
- LLM-as-judge: G-Eval; MT-Bench (Zheng 2023); "Trust or Escalate" (ICLR 2025); evidence-attribution / faithfulness≠correctness work; Braintrust/Langfuse versioning guidance.
- UX: HireVue (current product + 2021 facial-analysis withdrawal), BrightHire, Metaview, Greenhouse scorecards (banded color ratings), Microsoft Research on communicating confidence (banded > decimals).
