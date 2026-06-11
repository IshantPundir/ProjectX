# Question-Bank Generation — Speed/Cost Rework + SOTA Prompt Rewrite

**Date:** 2026-06-11
**Status:** Approved design — ready for implementation plan
**Branch:** `feat/followups-governed-dimensions` (continues the governed-dimensions work)
**Related:** [[project_followups_governed_dimensions]], the QA analysis of bank `f13a4e23` (Workato "Jr. Forward Deployed Engineer"), `docs/superpowers/specs/2026-06-11-followups-as-governed-dimensions-design.md`

---

## 1. Problem & Motivation

Recruiters will generate **hundreds of question banks per month**. Today's generator is **slow and expensive**, which makes "build your pipeline" a multi-minute (sometimes much longer) wait per stage. This does not scale.

### Root-cause diagnosis (from an end-to-end read of `app/modules/question_bank/`)

Per `ai_screening` bank, `_generate_one_bank` makes **two heavy reasoning LLM calls run strictly in series**, plus a cheap third:

1. **Behavioral call** — `gpt-5` @ `reasoning_effort=medium`, streamed via instructor `create_iterable`.
2. **Technical call** — `gpt-5` @ `medium` again. **Cannot start until the behavioral call fully finishes**, because it receives the behavioral questions in its prompt ("ALREADY-GENERATED BEHAVIORAL — DO NOT OVERLAP") to avoid duplicate follow-up dimensions.
3. **Keyterm extraction** — cheap `gpt-5.4-nano`. Minor.

The dominant cost/latency drivers, ranked:

1. **Model + effort.** The async generator runs **base `gpt-5` at `medium` effort** — the heaviest, oldest, priciest tier. The *real-time* engine already runs the faster/cheaper `gpt-5.4-mini`. The background batch job uses a more expensive model than the latency-critical live path — backwards.
2. **Forced serialization.** Behavioral → technical cannot overlap (dedup chaining); pipeline stages cannot overlap (cross-stage dedup). Wall-clock = sum of calls, not max.
3. **Per-question 7-point self-critique** baked into the prompt — a large hidden reasoning-token tax on a reasoning model.
4. **Large repeated context, caching unexploited.** ~12KB common header + full JD + full signal list sent on every call; the two calls per bank share most of it, but the signal set differs mid-prompt and there is no `prompt_cache_key`, so OpenAI automatic prefix caching barely engages.

### SOTA research (web, June 2026)

- **Reasoning effort is a cheap, huge dial.** `medium`→`low` ≈ **70% cost/latency cut**; `medium`→`high` is +41%. OpenAI guidance: evaluate `low`/`minimal` before assuming you need more. ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning))
- **For structured generation, "mini" tiers usually hold quality** and are ~5–10× cheaper; reserve flagships for hard reasoning. ([cost/perf](https://www.stackspend.app/resources/blog/gpt-4-vs-gpt-4o-vs-smaller-models-cost-performance))
- **Prompt caching:** automatic ≥1024 tokens; static-first / dynamic-last, identical prefix, pass `prompt_cache_key`; up to 80% lower TTFT and 90% lower input cost on the cached prefix. ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching))
- **Batch API** (50% off) was evaluated and **rejected** — generation must be interactive (recruiter watches questions stream in right after JD creation; sitting an hour is the exact pain we're fixing).

---

## 2. Goals & Non-Goals

**Goals**
1. Cut wall-clock latency, $ cost, and increase throughput for bank generation — substantially (target: a multiplicative win from fewer calls × cheaper model).
2. **Hold or improve** question quality (the QA bar), validated by a real-bank A/B.
3. Keep the live per-question streaming UX (recruiter sees each block appear).
4. Rewrite the generation prompts to a state-of-the-art standard, folding in the known quality fixes.

**Non-Goals**
- Batch API / async non-interactive generation (rejected — UX requires interactivity).
- Time-budget/`estimated_minutes` realism rework (explicitly deferred this session).
- `phone_screen` prompt rewrite (it inherits the cheaper model via shared config, but its prompt is not rewritten here).
- Cross-stage pipeline parallelization (most jobs have a single `ai_screening` stage; out of scope).
- DB schema migration (no new columns; only a cosmetic `generation_status_by_kind` shape change).

---

## 3. Architecture — One-Call Generation (Approach 1)

The two-phase (behavioral → technical) split collapses into **one streamed call** that emits all four question kinds in a single autoregressive pass.

### 3.1 Flow

`_generate_one_bank`:
- **Phase A (short session):** load rows, wipe AI questions, mark `generating` — unchanged.
- **Phase B (no held session):** **ONE** `_generate_questions_for_kind` call (streamed via `create_iterable`), seeing the **full** signal set and the **full** stage duration. Each streamed question is validated + persisted + `BANK_QUESTION_ADDED`-published in its own short session — **streaming UX unchanged**.
- **Phase C (short session):** reconcile — the deterministic **`_apply_mandatory_correction_in_position_order`** safety net stays (knockout-gating guaranteed in code regardless of LLM output), re-pack positions, soft over-budget warning, **keyterm extraction stays as its own cheap `gpt-5.4-nano` call** (folding it into the main call would break question-streaming, since keyterms need all questions first), stamp metadata, transition to `reviewing`.

### 3.2 What is removed

- The second heavy call and the behavioral→technical serial dependency.
- `STAGE_TYPE_TO_BEHAVIORAL_PROMPT`, the `phase` partition (`PHASE_QUESTION_KINDS`), `_filter_behavioral_eligible`, the `prior_phase_questions` chaining block + its "ALREADY-GENERATED BEHAVIORAL — DO NOT OVERLAP" rendering, and the `budget_minutes` behavioral/technical split.
- `generation_status_by_kind` collapses from `{behavioral, technical}` to a single bank-level status (cosmetic schema + UI touch).

### 3.3 What the single call must now self-enforce (was structural)

The prompt (not the architecture) now guarantees:
- **Kind balance** — knockout verification (`experience_check`/`compliance_binary`) + STAR `behavioral` for behavioral-type signals + `technical_scenario` depth, without any category being crowded out.
- **Verification-before-depth ordering.**
- **`is_mandatory` = knockout-only** (with the code reconcile as backstop).
- **Within-bank dimension distinctness** — enforced by the model attending to its own earlier questions (autoregressive; the followups spec already argued this is sound for a single streamed call).

---

## 4. Prompt Rewrite — From Scratch, SOTA

Two files rewritten from scratch: `prompts/v2/question_bank_common.txt` and a new unified `prompts/v2/question_bank_ai_screening.txt` (replacing the behavioral + technical pair). The old `question_bank_ai_screening_behavioral.txt` is retired.

### 4.1 Pre-rewrite safeguard — constraint inventory

Before writing, extract an inventory of the **load-bearing constraints** embedded in the current prompts so the clean rewrite keeps every rule that matters and drops only the bloat. At minimum:
- One-of / "or" requirement handling (never collapse "Java, Python, or Ruby" to one option anywhere; credit any valid option).
- `is_mandatory` = knockout-only.
- Copy signal `value` strings **verbatim** from the snapshot.
- Spoken constraints: ≤240 chars, ONE ask, spell numbers in words, conversational register.
- `primary_signal` must be one of `signal_values`.
- Evaluator-only fields (`rubric`/`positive_evidence`/`red_flags`/`evaluation_hint`) describe a **spoken** answer; concrete observables, not vague qualities.
- Knockout coverage completeness; required-before-preferred.
- `question_kind` taxonomy + per-question `difficulty` calibration.
- FollowUpDimension shape: `dimension` (stable slug) / `intent` / `seed_probe` / non-empty `listen_for`.

### 4.2 SOTA principles applied

- **Prescriptive, not suggestive** (a `low`-effort smaller model has less thinking budget). Replace the per-question 7-point self-critique with a **clear authoring recipe** the model runs once:
  1. one mandatory knockout-verification question per knockout signal;
  2. a true STAR `behavioral` per behavioral-type required signal (must not be crowded out);
  3. `technical_scenario` depth probes for the highest-weight competencies first;
  4. stop when high-weight signals are covered — signal density over count.
- **Cache-friendly ordering:** static instructions first (stable prefix), dynamic context (JD, signals, prior questions) last; pass a `prompt_cache_key` (per job).
- **Trim the bloat:** tighten the 297-line common header to high-signal instruction.

### 4.3 Quality fixes folded in (raise the bar — from the QA analysis)

- **Self-contained, scenario-committed leads** — every verbatim-read lead answerable on first hearing; ban bare comparative framings ("which would you pick?") lacking an inline scenario. Principle + why, **no replayed examples** (per [[feedback_prompt_principles_not_examples]]).
- **Semantic distinctness** of follow-ups, not just distinct slugs — two follow-ups may not probe the same underlying thing (kills the observability/idempotency near-collisions found in the QA pass).
- **Weight-aware coverage** — never spend budget on a w1 signal while a w3 competency is unprobed.

### 4.4 Other in-scope surfaces

- **Single-question regenerate** (`question_bank_regenerate_one.txt`): rewritten to match the new `common` header + follow-up/quality rules (already receives sibling questions for dedup; add semantic-distinctness).
- **Recruiter refine/draft** (`question_refine_single.txt` / `question_create_single.txt`): rewritten for consistency with the new `common` header. Interactive — benefits from the model/latency win too.

---

## 5. Model / Effort Change + A/B Sweep

All env-driven (`openai_question_bank_model`, `openai_question_bank_effort` in `app/config.py` / `AIConfig`) — no code change for the sweep itself.

**Sequencing (isolate variables):**
1. **First** land the one-call architecture + SOTA prompt **on the current model** (`gpt-5`/`medium`); QA-tester A/B vs the current bank → confirms the new *prompt* holds/raises quality (the baseline).
2. **Then** hold the prompt fixed and sweep model/effort down, QA-A/B each on the Workato bank + 1–2 other real JDs:
   - `gpt-5.4-mini` / `low` (expected sweet spot)
   - `gpt-5.4-mini` / `medium` (if `low` drops quality)
   - `gpt-5.4` / `low` (if mini isn't enough)
   - Pick the **cheapest config that holds quality**; update `app/config.py` defaults to the winner (last, reversible step).

The single-question regenerate + refine/draft surfaces inherit the winning model via shared config.

---

## 6. Quality Gate — QA-Tester A/B on Real Banks

The primary gate is a **manual QA-tester analysis** (the same deep, context-grounded analysis used on bank `f13a4e23`): regenerate 2–3 real JDs (Workato + others) across the configs above and compare head-to-head against the current bank on:
- knockout coverage complete + correctly mandatory-gated;
- leads self-contained / scenario-committed;
- follow-up dimensions distinct (semantically, not just slugs) with concrete `listen_for`;
- rubric/evidence specific, spoken, tool-named;
- weight-aware coverage of required signals.

A config passes only if it **holds or beats** the current bank on these. The opt-in `pytest -m prompt_quality` real-API eval is kept green as a cheap automated backstop (distinct dimensions, non-empty `listen_for`), but the manual A/B is the decision-maker.

---

## 7. Testing

- **Unit (`tests/question_bank/`):** update for the one-call shape — `_generate_questions_for_kind` called once, removed behavioral-chaining, collapsed `generation_status_by_kind`. **Keep** the deterministic mandatory-correction tests (safety net unchanged).
- **prompt_quality eval:** keep green.
- **Primary:** the QA-tester A/B (Section 6).

---

## 8. Risks & Rollout

- **Top risk:** the single call dropping a whole category (e.g. behavioral STAR) under a downgraded model. **Detection:** the QA-A/B. **Mitigation:** borrow Approach 2's rigid section-labeling ("## VERIFICATION / ## BEHAVIORAL / ## DEPTH") only if the data shows a dropped category — start clean, add rigidity if needed.
- **Constraint-loss risk** (from-scratch rewrite): mitigated by the §4.1 constraint inventory.
- **Rollout:** on `feat/followups-governed-dimensions`. No DB migration (cosmetic `generation_status_by_kind` shape only). Order: (1) one-call + SOTA prompt on current model → QA; (2) model/effort sweep → QA → flip defaults. Model flip is the last, fully reversible step.

---

## 9. Rollout Order (summary)

1. Constraint inventory → rewrite `common` + unified `ai_screening` + `regenerate_one` + `refine`/`create` prompts.
2. Refactor `_generate_one_bank` to one call; update tests; QA-A/B on current model (`gpt-5`/`medium`).
3. Env sweep model/effort down; QA-A/B each; pick winner; update `app/config.py` defaults.
4. Worker restart (no hot-reload) so the actor runs new code/config.
