# Question-Bank Generation — Speed/Cost Rework + SOTA Prompt Rewrite

**Date:** 2026-06-11
**Status:** Approved design — ready for implementation plan
**Branch:** `feat/followups-governed-dimensions` (continues the governed-dimensions work)
**Related:** [[project_followups_governed_dimensions]], the QA analysis of bank `f13a4e23` (Workato "Jr. Forward Deployed Engineer"), `docs/superpowers/specs/2026-06-11-followups-as-governed-dimensions-design.md`

**Engineering mandate (applies to the whole implementation):** production-grade, no patches/workarounds/back-compat shims. This is pure dev mode — **delete all stale/dead code, schema, and UI** the rework obsoletes; leave no "phase"/"engine-v2"/"decision-D" remnants that could confuse. All tunable parameters are **config-driven** (`AIConfig` / `app/config.py`), never hardcoded.

---

## 1. Problem & Motivation

Recruiters will generate **hundreds of question banks per month**. Today's generator is **slow and expensive**, which makes "build your pipeline" a multi-minute (sometimes much longer) wait per stage. This does not scale.

### Root-cause diagnosis (from an end-to-end read of `app/modules/question_bank/`)

Per `ai_screening` bank, `_generate_one_bank` makes **two heavy reasoning LLM calls run strictly in series**, plus a cheap third:

1. **Behavioral call** — `gpt-5` @ `reasoning_effort=medium`, streamed via instructor `create_iterable`.
2. **Technical call** — `gpt-5` @ `medium` again. **Cannot start until the behavioral call fully finishes**, because it receives the behavioral questions in its prompt ("ALREADY-GENERATED BEHAVIORAL — DO NOT OVERLAP") to avoid duplicate follow-up dimensions.
3. **Keyterm extraction** — cheap `gpt-5.4-nano`. Minor.

Dominant cost/latency drivers, ranked:

1. **Model + effort.** The async generator runs **base `gpt-5` at `medium` effort** — the heaviest, oldest, priciest tier. The *real-time* engine already runs the faster/cheaper `gpt-5.4-mini`. The background batch job uses a more expensive model than the latency-critical live path — backwards.
2. **Forced serialization.** Behavioral → technical cannot overlap (dedup chaining). Wall-clock = sum of calls, not max.
3. **Per-question 7-point self-critique** baked into the prompt — a large hidden reasoning-token tax on a reasoning model.
4. **Large repeated context, caching unexploited.** ~12KB common header + full JD + full signal list sent on every call; the two calls per bank share most of it, but the signal set differs mid-prompt and there is no `prompt_cache_key`, so OpenAI automatic prefix caching barely engages.

### SOTA research (web, June 2026)

- **Reasoning effort is a cheap, huge dial.** `medium`→`low` ≈ **70% cost/latency cut**; `medium`→`high` is +41%. OpenAI guidance: evaluate `low`/`minimal` before assuming you need more. ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning))
- **For structured generation, "mini" tiers usually hold quality** and are ~5–10× cheaper; reserve flagships for hard reasoning. ([cost/perf](https://www.stackspend.app/resources/blog/gpt-4-vs-gpt-4o-vs-smaller-models-cost-performance))
- **Prompt caching:** automatic ≥1024 tokens; static-first / dynamic-last, identical prefix, pass `prompt_cache_key`; up to 80% lower TTFT and 90% lower input cost on the cached prefix. ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching))
- **Batch API** (50% off) was evaluated and **rejected** — generation must be interactive (recruiter watches questions stream in right after JD creation).

---

## 2. Goals & Non-Goals

**Goals**
1. Cut wall-clock latency, $ cost, and increase throughput for bank generation — a multiplicative win from fewer calls × cheaper model.
2. **Hold or improve** question quality (the QA bar), validated by a real-bank A/B.
3. Keep the live per-question streaming UX (recruiter sees each block appear).
4. Rewrite the generation prompts to a state-of-the-art standard, folding in the known quality fixes.
5. **Leave the module clean** — delete every artifact the two-phase model obsoletes (see §6).

**Non-Goals**
- Batch API / async non-interactive generation (rejected — UX requires interactivity).
- Time-budget/`estimated_minutes` realism rework (deferred this session).
- `phone_screen` *prompt* rewrite (it inherits the cheaper model via shared config; its prompt is not rewritten here).
- Cross-stage pipeline parallelization (most jobs have a single `ai_screening` stage).

> Note: unlike the first draft, this rework **does** include a DB migration and frontend changes — both are required to remove dead schema/UI cleanly (§6). "No back-compat" is explicit: we drop, we don't shim.

---

## 3. Architecture — One-Call Generation (Approach 1)

The two-phase (behavioral → technical) split collapses into **one streamed call** that emits all four question kinds in a single autoregressive pass.

### 3.1 Flow

`_generate_one_bank`:
- **Phase A (short session):** load rows, wipe AI questions, mark `generating` — unchanged.
- **Phase B (no held session):** **ONE** streamed generation call (via `create_iterable`), seeing the **full** signal set and the **full** stage duration. Each streamed question is validated + persisted + `BANK_QUESTION_ADDED`-published in its own short session — **streaming UX unchanged**.
- **Phase C (short session):** reconcile — the deterministic **`_apply_mandatory_correction_in_position_order`** safety net stays (knockout-gating guaranteed in code regardless of LLM output), re-pack positions, soft over-budget warning, **keyterm extraction stays as its own cheap `gpt-5.4-nano` call** (folding it into the main call would break question-streaming, since keyterms need all questions first), stamp metadata, transition to `reviewing`.

### 3.2 The single call must self-enforce (was structural)

The prompt (not the architecture) now guarantees, in one streamed pass:
- **Kind balance** — knockout verification (`experience_check`/`compliance_binary`) + STAR `behavioral` for behavioral-type signals + `technical_scenario` depth, no category crowded out.
- **Verification-before-depth ordering.**
- **`is_mandatory` = knockout-only** (code reconcile is the backstop).
- **Within-bank dimension distinctness** — enforced by the model attending to its own earlier questions (autoregressive; the followups spec already argued this is sound for a single streamed call).

### 3.3 Renames (kill the "phase" vocabulary)

- `_generate_questions_for_kind(...)` → **`_stream_bank_questions(...)`**; drop its `phase`, `prior_phase_questions`, `budget_minutes`-split params. Budget passed is simply the stage duration.
- All `phase=` / `question_phase` / `question_kind=phase` keys in logs, span attributes, and pubsub payloads are removed or replaced with bank-level identifiers.
- Strip every `engine-v2 M2` / `decision D2/D3/D6/D7` comment marker — describe the code as it is, not its history.

---

## 4. Prompt Rewrite — From Scratch, SOTA

Rewritten from scratch: `prompts/v2/question_bank_common.txt` + a new unified `prompts/v2/question_bank_ai_screening.txt` (replaces the behavioral + technical pair). `question_bank_regenerate_one.txt`, `question_refine_single.txt`, `question_create_single.txt` rewritten for consistency with the new header.

### 4.1 Pre-rewrite safeguard — constraint inventory

Before writing, extract an inventory of the **load-bearing constraints** in the current prompts so the clean rewrite keeps every rule that matters and drops only bloat: one-of/"or" handling (credit any valid option; never collapse "Java/Python/Ruby"); `is_mandatory`=knockout-only; copy signal `value` strings verbatim; spoken constraints (≤240 chars, ONE ask, numbers-in-words, conversational); `primary_signal` ∈ `signal_values`; evaluator-only fields describe a *spoken* answer with concrete observables; knockout-coverage completeness; required-before-preferred; `question_kind` taxonomy + per-question `difficulty`; FollowUpDimension shape (`dimension`/`intent`/`seed_probe`/non-empty `listen_for`).

### 4.2 SOTA principles applied

- **Prescriptive, not suggestive** (a `low`-effort smaller model has less thinking budget). Replace the per-question 7-point self-critique with a **clear authoring recipe** the model runs once:
  1. one mandatory knockout-verification question per knockout signal;
  2. a true STAR `behavioral` per behavioral-type required signal (must not be crowded out);
  3. `technical_scenario` depth probes for the highest-weight competencies first;
  4. stop when high-weight signals are covered — signal density over count.
- **Cache-friendly ordering:** static instructions first (stable prefix), dynamic context (JD, signals, prior questions) last; pass a `prompt_cache_key` (per job).
- **Trim the bloat:** tighten the 297-line common header to high-signal instruction.

### 4.3 Quality fixes folded in (raise the bar — from the QA analysis)

- **Self-contained, scenario-committed leads** — every verbatim-read lead answerable on first hearing; ban bare comparative framings lacking an inline scenario. Principle + why, **no replayed examples** ([[feedback_prompt_principles_not_examples]]).
- **Semantic distinctness** of follow-ups, not just distinct slugs.
- **Weight-aware coverage** — never spend budget on a w1 signal while a w3 competency is unprobed.

---

## 5. Model / Effort Change + A/B Sweep

All env-driven (`openai_question_bank_model`, `openai_question_bank_effort` in `app/config.py` / `AIConfig`) — no code change for the sweep itself. The runaway-stop `STREAM_QUESTION_CEILING` (today a module constant) **moves into config** (`AIConfig.question_bank_max_questions`) to honor the config-driven mandate.

**Sequencing (isolate variables):**
1. **First** land the one-call architecture + SOTA prompt **on the current model** (`gpt-5`/`medium`); QA-tester A/B vs the current bank → confirms the new *prompt* holds/raises quality (the baseline).
2. **Then** hold the prompt fixed and sweep model/effort down, QA-A/B each on the Workato bank + 1–2 other real JDs:
   - `gpt-5.4-mini` / `low` (expected sweet spot)
   - `gpt-5.4-mini` / `medium` (if `low` drops quality)
   - `gpt-5.4` / `low` (if mini isn't enough)
   - Pick the **cheapest config that holds quality**; update `app/config.py` defaults to the winner (last, reversible step).

Single-question regenerate + refine/draft inherit the winning model via shared config.

---

## 6. Dead Code / Schema / UI to DELETE (clean-slate mandate)

The two-phase model leaves a large dead surface. **All of it is deleted — no shims, no back-compat.**

### 6.1 Backend — the entire `regenerate_kind` feature (now redundant)

With one fast call, "regenerate one phase" is meaningless and is fully covered by the existing full-stage regenerate. Delete:
- `RegenerateKindBody` (`schemas.py`)
- `regenerate_kind` router endpoint (`router.py`) + its imports
- `regenerate_kind_actor` (`actors.py`, ~280 lines)
- `wipe_ai_questions_of_phase` (`service.py`) + its `__all__` export
- `PHASE_QUESTION_KINDS` (`actors.py`)

### 6.2 Backend — `generation_status_by_kind` (redundant with `bank.status`)

One call has no per-phase partial state, so this column is fully subsumed by the `BankStatus` state machine. Delete:
- Model column (`models.py`)
- `BankResponse.generation_status_by_kind` (`schemas.py`) + the `router.py` mapping
- All writes in `actors.py`
- **New Alembic migration** (head currently `0055`) dropping `stage_question_banks.generation_status_by_kind`, with a rollback that re-adds it (column drop only; no data preserved — dev mode).

### 6.3 Backend — stale two-phase scaffolding (`actors.py`)

Delete: `STAGE_TYPE_TO_BEHAVIORAL_PROMPT`, `_filter_behavioral_eligible`, `BEHAVIORAL_BUDGET_MIN`, the test-only aliases `_load_pipeline_context` / `_load_prior_stages_questions`, and the `prior_phase_questions` + behavioral-chaining block in `_build_user_message`. Fix the tests that imported the aliases to use the real names.

### 6.4 Prompts

- Delete `prompts/v2/question_bank_ai_screening_behavioral.txt` (retired).
- **Audit + delete dead `prompts/v1/question_bank_*`** files: the module loads v2 exclusively (`PromptLoader(version=question_bank_prompt_version="v2")`). Verify no caller resolves v1 for question_bank, then delete the dead v1 set (`question_bank_*`, `question_create_single`, `question_refine_single`, etc.). Any file with a live caller stays.

### 6.5 Frontend (`frontend/app`)

The per-phase "sections" UI dies with the phase concept. Delete / rework:
- `components/dashboard/question-bank/SectionStatus.tsx` (per-phase status pill) — delete.
- `components/dashboard/question-bank/QuestionList.tsx` — stop grouping by generation phase / reading `bank.generation_status_by_kind`; render a **flat list** (cosmetic grouping by each question's persisted `question_kind` is allowed, but driven by `question_kind`, not by a generation phase or bank-level status).
- `lib/api/question-banks.ts` — remove the `generation_status_by_kind` field from the type.
- Remove any "regenerate behavioral/technical" affordance; recruiters use the (now fast) full-stage regenerate.
- Update/delete `tests/components/QuestionListSections.test.tsx` accordingly.

---

## 7. Quality Gate — QA-Tester A/B on Real Banks

Primary gate is a **manual QA-tester analysis** (same depth as on bank `f13a4e23`): regenerate 2–3 real JDs (Workato + others) across the configs above and compare head-to-head vs the current bank on: knockout coverage complete + mandatory-gated; leads self-contained; follow-up dimensions semantically distinct with concrete `listen_for`; rubric/evidence specific, spoken, tool-named; weight-aware required-signal coverage. A config passes only if it **holds or beats** the current bank. The opt-in `pytest -m prompt_quality` real-API eval stays green as a cheap automated backstop, but the manual A/B decides.

---

## 8. Testing

- **Unit (`tests/question_bank/`):** rewrite for the one-call shape — single `_stream_bank_questions` call, no behavioral-chaining, no `generation_status_by_kind`, no `regenerate_kind`. **Keep** the deterministic mandatory-correction tests (safety net unchanged). Delete tests covering deleted code.
- **Frontend (`frontend/app/tests`):** update/remove the QuestionList sections tests.
- **prompt_quality eval:** keep green.
- **Primary:** the QA-tester A/B (§7).

---

## 9. Risks & Rollout

- **Top risk:** the single call dropping a whole category (e.g. behavioral STAR) under a downgraded model. **Detection:** QA-A/B. **Mitigation:** add Approach-2 rigid section-labeling ("## VERIFICATION / ## BEHAVIORAL / ## DEPTH") only if data shows a dropped category — start clean.
- **Constraint-loss risk** (from-scratch rewrite): mitigated by the §4.1 inventory.
- **v1-prompt deletion risk:** mitigated by the §6.4 caller-audit before deleting.
- **Rollout order:**
  1. §6 deletions + renames (clean the module first, on the current model — behavior preserved, just two→one call wiring) + migration dropping `generation_status_by_kind`.
  2. Constraint inventory → SOTA prompt rewrite (`common` + unified `ai_screening` + `regenerate_one` + `refine`/`create`).
  3. QA-A/B on current model (`gpt-5`/`medium`).
  4. Env sweep model/effort down; QA-A/B each; pick winner; update `app/config.py` defaults.
  5. Restart `nexus-worker` (no hot-reload) so the actor runs new code/config.
- Branch: `feat/followups-governed-dimensions`. The model flip is the last, fully reversible step.
