# Question Bank v3 — Measurement-Instrument Redesign

- **Date:** 2026-06-12
- **Branch:** `feat/followups-governed-dimensions`
- **Status:** Design — approved direction, pending spec review
- **Related:**
  - `docs/superpowers/specs/2026-06-11-followups-as-governed-dimensions-design.md` (governed probe dimensions)
  - `docs/superpowers/specs/2026-06-11-question-bank-generation-speedup-design.md` (one-call generation)
  - `docs/superpowers/specs/2026-06-09-rubric-aware-question-anchored-scoring-design.md` (report grader contract)

---

## 1. Why

The question bank is not content — it is a **measurement instrument**. It is the single
standardized template that drives all three downstream consumers:

- the **generator** (`question_bank/`) authors it,
- the **live engine** (`interview_engine/`) reads `text` + `follow_ups` to direct the
  session and `rubric`/`positive_evidence`/`red_flags` for in-flight grading,
- the **report** (`reporting/`) grades each answer against the rubric anchors at scale.

Because hundreds of candidates per JD are screened and scored against the SAME bank, the
**psychometric quality of the template is the ceiling on the product's predictive power**.
Structured interviews are the highest-validity hiring signal that exists, but their validity
is highly variable around the mean — the quality of the *structure* is what separates a
strong instrument from a coin flip.

The current bank (post the recent SOTA prompt rewrite + governed-dimensions work) is already
structurally strong: governed probe dimensions with `listen_for` observables + fire-once
dedup + cross-cutting singletons, behaviorally-described rubric bands, `question_kind`
taxonomy, `primary_signal` + `signal_values` coverage ledger, Indian-English spoken
constraints. This redesign closes the remaining validity gaps identified by the research and
adds a generation-time quality backstop.

### Goal

Raise the validity of the generated bank end-to-end **within stable engine/report
contracts** — the engine and report read any new fields opportunistically; neither requires
a code change to keep working.

### Non-goals (explicitly deferred)

- **Numeric BARS (0–3) rescoring.** The report grader (`prompts/v4/report_scorer/question_grade.txt`)
  already maps the three prose rubric anchors → a 4-level scale (`strong`/`solid`/`thin`/`absent`)
  with `listen_for_hits`, `red_flags_tripped`, `evidence_quotes` (the evidence span), a
  probe-dependence cap, and factual-gate handling. The prose anchors ARE the behavioral
  anchors. The lever is anchor *sharpness*, not anchor *type*.
- **Adaptive item-pool selection** (bank as a calibrated pool with dynamic
  information-gain-driven selection). Large re-architecture touching the engine resolver;
  competes with in-flight engine tuning.
- **Engine-side escalation behavior.** We encode escalation in the *seed probes* (their
  wording forces specifics + orthogonal angle); the engine still fires each dimension
  once, in order. Giving `project_deepdive` a larger engine probe budget is a future item.

---

## 2. Downstream contract analysis (verified, 2026-06-12)

Adding a `question_kind` value was verified safe across every consumer:

- `interview_engine/contracts.py:179` — `kind: str` (free string; comment lists the four).
- `interview_runtime/schemas.py:109` — `question_kind: str` (intentionally NOT a Literal;
  validated only at the DB CHECK).
- `interview_engine/brain/input_builder.py:137` — passes `q.question_kind` through; no
  literal branch.
- `reporting/scoring/constants.py:15` — `FACTUAL_QUESTION_KINDS = {experience_check,
  compliance_binary}` is the ONLY hard branch. `project_deepdive` falls outside it →
  automatically graded as **depth** against the rubric. Correct.
- `reporting/scoring/question_grade.py:75` — passes the kind as a text string into the
  prompt (`... or 'unknown'`); we add a teaching line so grading is intentional.

The bank `status` column has a CHECK constraint (`stage_question_banks_status_check`,
migration 0006) enumerating the 5 statuses — so a new `self_reviewing` status needs a
CHECK extension. Both CHECK changes live in one migration (`0057`).

Seniority is already available to the generator (`snapshot.seniority_level`, injected in
`question_bank/actors.py:_build_user_message`) — P1 below is a pure prompt rule.

---

## 3. Prompt redesign → `prompts/v3/question_bank_*`

Bump the bank prompt version **v2 → v3** (`question_bank_prompt_version` in config), so the
per-bank `prompt_version` (already on `BankResponse`) gives clean rollback + A/B.

**Why a new version dir, not an in-place edit (and why v2 is NOT dead code):** banks already
generated under v2 record `prompt_version="v2"`. The prompt that generated a bank is part of
the **scoring audit trail** the root CLAUDE.md mandates (EEOC / AI-bias: "Scoring audit trail
required"). Mutating v2 in place would silently break that provenance — an existing bank's
recorded `v2` would no longer match the file that produced it. So v2 is retained as an
**immutable provenance artifact**, exactly as `prompts/v1/` (JD) coexists with later versions
— this is the established versioned-prompt architecture (`PromptLoader` reads `v{N}/`), not
leftover scaffolding. Going forward, ALL generation reads v3; v2 is never loaded again but is
never mutated either. (The dead `v1` question-bank prompts were already deleted in the
speedup work — commit `67efb117` — so there is no actual stale QB prompt to clean up.)

Layer six principle-changes onto the current prompts (`question_bank_common.txt` +
`question_bank_ai_screening.txt`):

### P1 — Format by seniority (new rule, keyed on `snapshot.seniority_level`)

Encode as a rule, not a vibe:
- **Senior / lead** → behavior-description ("tell me about a time you actually…") +
  design-judgment (`technical_scenario`) + the project deep-dive (§4). **Down-weight
  situational** ("what would you do…") — its validity *decays* as role complexity rises.
- **Junior / entry** → situational is acceptable and easier to standardize; project
  deep-dive optional.

### P2 — Escalation ladders (reframe `follow_ups`)

Today `follow_ups` is a flat ordered list of distinct dimensions. Reframe it as an
**escalating depth ladder**:
- every rung's `seed_probe` demands a *falsifiable specific* — a number, a name, a
  sequence, a failure mode, or a tradeoff — never open "tell me more";
- at least one rung **re-approaches the same ground from an orthogonal angle** (genuine
  experience survives recursive "why X over Y / what broke / what would you change";
  fabrication degrades under it);
- order foundational-specific → deeper-specific → orthogonal/tradeoff-against.
- Keep the existing once-per-session dedup + cross-cutting operational singleton rules.

### P4 — Content-based bluffer tripwires (sharpen `red_flags`)

Make `red_flags` content tells, not delivery cues:
- claims framed around tools/buzzwords instead of impact and decisions;
- "we" with no recoverable "I did";
- inability to name a tradeoff *against* the choice they made;
- vagueness on peripheral detail a real practitioner would know cold.
Add the explicit warning that **generic probing increases faking** (candidates read probes
as a cue for what matters) — so every probe must force a verifiable specific, never
telegraph "what matters." Name the India-context adversary (AI-coached / live-proxy /
voice-clone) that orthogonal escalation partially defends against.

### P5 — Project / work deep-dive as the senior spine

Paired with the schema add (§4). For senior/experienced roles, REQUIRE exactly one
`project_deepdive`: the lead invites the candidate to pick a real project they drove; the
ladder escalates "what did *you* decide / chose over what / what broke / what would you
change." It is simultaneously the single most reliable seniority signal AND the strongest
bluffer test (a fabricated project disintegrates under orthogonal probing).

### P7 — Warmth + anchor sharpness

- **Warmth:** a casual conversational tone surfaces inconsistencies better than intense
  interrogation; the "what did *you* personally do" probe matters more against collectivist
  "we"-framing but must be asked warmly, never as interrogation.
- **Anchor sharpness:** every rubric band names *observable* spoken behavior (no "clear
  explanation" / "good depth") — the report's anchor→level mapping is only as reliable as
  the anchors are observable.

---

## 4. Schema + migration `0057`

### 4.1 New `question_kind = "project_deepdive"`

- `schemas.GeneratedQuestion.question_kind` Literal gains `"project_deepdive"`.
- Migration `0057` drops + recreates the `stage_questions.question_kind` CHECK to include it.
- `prompts/v4/report_scorer/question_grade.txt` gains a line teaching the grader to treat
  `project_deepdive` as a depth/behavioral grade (real owned project + decision ownership +
  surviving orthogonal probing). **No engine/report code change** — verified §2.
- Future (out of scope): give `project_deepdive` a larger engine probe budget.

### 4.2 New durable status `self_reviewing`

- `state_machine.BankStatus` Literal gains `"self_reviewing"`.
- The critic is a **permanent** part of the flow (not feature-flagged) — so generation
  ALWAYS routes `generating → self_reviewing → reviewing`. The direct `generating → reviewing`
  edge becomes unreachable and is **removed** from `LEGAL` (no dead transition left behind).
- `LEGAL` transitions: `generating → {self_reviewing, failed}`,
  `self_reviewing → {reviewing, failed}`.
- Add `transition_to_self_reviewing(bank)` (`generating → self_reviewing`) and **rename**
  `transition_to_reviewing_after_generation` → `transition_to_reviewing_after_critic`
  (`self_reviewing → reviewing`); update all call sites. The post-critic success path —
  including the critic-failure fallback (§5, step 4) — is the ONLY producer of `reviewing` from
  generation, so its single source state is `self_reviewing`.
- Migration `0057` extends `stage_question_banks_status_check` to include `self_reviewing`.
- `sse.py`: add `self_reviewing` to the non-terminal set (line ~277 `if bank.status in
  ("draft", "generating")`) and to the fast-cadence `any_generating` check (line ~395) so the
  stream neither prematurely completes nor drops to the idle poll cadence during the critic
  call.

### 4.3 Persist the critique log

Write the critic's findings to the **existing `coverage_notes`** column (no new column) —
the standardization/audit artifact (job-analysis → signal → question → critic-audited bank).

---

## 5. Generation flow → generate → self-critic → reviewing (2 calls, bounded)

`question_bank/actors.py` generation actor, after the single generation call produces the
full draft bank:

1. `transition_to_self_reviewing(bank)` + commit (SSE emits `bank.status_changed` →
   frontend shows the self-review animation).
2. **Critic call** — `prompts/v3/question_bank_critic.txt`, model from AIConfig
   (`question_bank_critic_model` — recommend a stronger model than the generator; it is the
   quality backstop and runs once). The critic receives the draft bank + the same pinned
   snapshot/context and audits against a fixed checklist:
   - every knockout + high-weight required signal covered;
   - format matches seniority (P1);
   - ≥1 `project_deepdive` present for senior roles;
   - no dimension repeated-in-meaning; cross-cutting concerns are singletons;
   - every rubric anchor sharp/observable;
   - every lead one-ask, self-contained, sayable;
   - `red_flags` are content tells.
   It returns the **corrected bank** (same `StageQuestionBankOutput` schema) **plus a short
   critique log**. Exactly 2 LLM calls total — no loop.
3. Persist the corrected questions (replacing the draft) + the critique log to
   `coverage_notes`, then `self_reviewing → reviewing`.
4. On critic failure: fall back to the un-critiqued draft, log the failure, still transition
   to `reviewing` (the critic is a quality enhancer, not a gate — never strand a bank in
   `self_reviewing`).

The single-question regen/refine flows (`refine.py`) are out of scope for the critic pass
(they already operate against a confirmed bank with recruiter context).

---

## 6. Frontend (`frontend/app`)

- Questions tab: render a **"🤖 AI is self-reviewing the bank…" animation** when the bank's
  `status === "self_reviewing"` (the existing SSE `bank.status_changed` already delivers it).
- Add a **`project_deepdive` label/badge** to the question kind rendering (the UI already
  renders `question_kind`).
- Frontend `BankStatus` type union gains `self_reviewing`.

---

## 7. Testing

- **Prompt-quality eval** (`pytest -m prompt_quality`, real OpenAI): extend to assert
  seniority-format fit, a `project_deepdive` present for a senior fixture, escalation-ladder
  shape (probes demand specifics + ≥1 orthogonal rung), anchor observability, and
  **critic-catches-a-planted-defect** (feed a draft with a duplicated dimension / vague
  anchor / missing knockout → critic flags + corrects it).
- **Migration `0057`** up/down test: both CHECK extensions apply and revert; existing rows
  unaffected.
- **State-machine** unit tests: `generating → self_reviewing → reviewing` legal;
  illegal sources rejected; critic-failure path still reaches `reviewing`.
- **SSE**: `self_reviewing` is non-terminal (stream stays open, fast cadence) and emits
  `bank.status_changed`.

---

## 8. Implementation surface (summary)

| Area | Files |
|---|---|
| Prompts | `prompts/v3/question_bank_common.txt`, `prompts/v3/question_bank_ai_screening.txt`, `prompts/v3/question_bank_critic.txt` (new); `config.py` `question_bank_prompt_version` → `v3` |
| Report prompt | `prompts/v4/report_scorer/question_grade.txt` (+1 line for `project_deepdive`) |
| Schema | `question_bank/schemas.py` (`GeneratedQuestion.question_kind` Literal); `state_machine.py` (`BankStatus`, `LEGAL`, transition helpers) |
| AIConfig | `app/ai/config.py` (`question_bank_critic_model` / effort) |
| Generation flow | `question_bank/actors.py` (generate → self_reviewing → critic → reviewing); critic call helper |
| Migration | `migrations/versions/0057_*.py` (two CHECK extensions: `question_kind` + bank `status`) + rollback |
| SSE | `question_bank/sse.py` (non-terminal set + cadence include `self_reviewing`) |
| Frontend | `frontend/app` questions tab (self-review animation + `project_deepdive` badge + status type) |
| Tests | prompt-quality eval, migration up/down, state-machine, SSE |

---

## 9. Code-quality mandate (production-grade, no hacks / no dead code)

This redesign is a clean rewrite, not a patch. Binding constraints on the implementation:

- **No feature-flag dual paths.** The critic is a permanent stage. There is exactly ONE
  generation route (`generating → self_reviewing → reviewing`); the now-unreachable
  `generating → reviewing` edge + the old helper name are **removed/renamed**, not left as a
  fallback branch (§4.2).
- **No dead code left behind.** Every rename propagates to all call sites in the same change;
  no orphaned helper, import, status literal, or branch. v2 prompts are retained *only* as an
  immutable provenance artifact (§3) — a documented, intentional retention, not stale code.
- **No silent fallbacks / swallowed errors.** The critic-failure path (§5, step 4) logs the
  failure with the correlation id + writes an audit/`coverage_notes` marker that the critic
  was skipped — it never silently strands a bank in `self_reviewing` and never pretends the
  critic ran. The bank still reaches `reviewing` (the critic is an enhancer, not a gate).
- **Contracts stay honest.** `question_kind` stays a free `str` at the engine/runtime
  boundary by design (documented at the call sites); the Literal lives only on the generation
  schema where it is authored. No widening of a typed contract into a stringly-typed one to
  dodge work.
- **Full migration down().** `0057` has a complete, tested rollback (both CHECK extensions),
  per the root CLAUDE.md migration rule. No data left in an inconsistent state on revert.
- **Atomic, idempotent actor.** The generation actor remains idempotent (re-run safe via the
  bank status guard) end-to-end across the new two-call flow — a retry after a crash between
  the two calls must not double-generate or strand state.
- **Tests are part of "done."** Every new branch (critic success, critic failure,
  `self_reviewing` SSE/non-terminal, migration up/down, state-machine edges) ships with a
  test in the same change — no "add tests later."
