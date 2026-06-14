# Interview Engine Simplification — Delete the Session Clock + Knockout Subsystems

**Date:** 2026-06-14
**Status:** Design approved — pending spec review, then implementation plan
**Module:** `backend/nexus/app/modules/interview_engine/` (+ `interview_runtime`, `reporting`, `tenant_settings`, a DB migration)
**Spec type:** Cleanup / deletion. **Spec 1 of 2** (Spec 2 = screening-capability upgrade, separate cycle).

---

## 1. Summary

Remove two whole subsystems from the live interview engine and their full vertical
footprint, leaving **zero dead/stale code**, a smaller engine surface, and a
fully-green test suite:

1. **The session clock / time-budget subsystem** — and, as a direct consequence,
   the now-dead resolver tier/overflow scheduler and mandatory-first guarantee.
2. **The knockout early-close subsystem** — full vertical: engine runtime →
   durable `SessionEvidence` contract → reporting's knockout gate →
   `tenant_settings` → DB columns.

This is a **deliberate simplification**, not a refactor. Both features are being
deleted cleanly now so that the next effort (Spec 2 — make the brain *use* the
bank's rich per-question fields like `positive_evidence` / `red_flags` and resolve
`difficulty`) starts on a clean base.

### Why now

- **Clock is redundant.** The question-bank generator already sizes the question
  *count* to the stage's time budget. At runtime the engine should simply ask the
  questions it was given, in order — no live clock, no budget phase, no
  time-driven early close.
- **No knockout signals in play**, and a better knockout design is planned as a
  future, separate effort. Carrying the current verified-knockout state machine
  (engine + durable contract + reporting gate + DB columns) is complexity with no
  current payoff.

### Enterprise bar

The product owner's standing rule applies: **no hacks, no patches, no workarounds,
no dead or stale code.** Where a deletion orphans a downstream field, the field is
removed too (or, for JD *data* attributes, explicitly retained with its remaining
consumers named).

---

## 2. Goals / Non-goals

### Goals
- Delete the clock + resolver-tier subsystems entirely.
- Delete the knockout subsystem entirely, full-vertical, including a DB migration.
- Keep the test suite green at two checkpoints (after Phase 1a, after Phase 1b).
- Preserve all unrelated engine behavior (turn-taking, assembly, bridge/mouth,
  STT/TTS/VAD, recording, proctoring, reel).

### Non-goals (explicitly out of scope)
- **No new screening behavior.** `positive_evidence` / `red_flags` steering and
  wiring `difficulty` are **Spec 2**.
- No change to turn detection, `TurnAssembler`, the merge-back checkpoint, the
  bridge ∥ brain → mouth loop, anti-stall, or backchannel handling.
- No redesign of the report's scoring model beyond removing the knockout gate.
- No re-introduction of a clock or knockout under a new shape (future, separate).

### Critical distinction — behavior vs. data
The JD/signal `knockout: bool` **data attribute** (which signals are hard
requirements) is **retained everywhere it describes a signal**:
`jd/schemas.py` (`SignalItemInput`/`SignalItemResponse`), `interview_runtime`
`SignalMetadata.knockout`, engine `SignalSpec.knockout`, and
`interview_runtime/evidence.py` `SignalEvidence.knockout`. Only the knockout
*behavior* (the verified-absence early-close flow and everything it feeds) is
deleted. The report continues to use `signal.knockout` for must-have
identification and the must-have-met ceiling.

---

## 3. Kept / Deleted ledger

| Subsystem | DELETE | KEEP |
|---|---|---|
| **Clock** | `BudgetPhase`, `BudgetConfig`, `compute_budget_phase`, `budget_config_from_ai_config`; all budget/time params in `resolve_next`; `BrainTurnInput.budget_phase` + its prompt render block; driver `_time_remaining_s` / `_budget_cfg` / time wiring + `_BrainAdapter.time_remaining_s`; `engine_close_reserve_s` / `engine_winding_down_s` (AIConfig + `config.py`); winding-down language in `brain.system.txt` | `SessionMeta.duration_s` (elapsed wall-clock), `started_at` / `ended_at`; the anti-stall counter (`_stall_*`, unrelated) |
| **Resolver tier/overflow** (dead once the clock is gone) | `ResolverQuestion.tier` / `weight` / `estimated_minutes` / `is_mandatory` consumption; overflow-by-weight + mandatory-first branches; `covered_signals` param; `QuestionTier`; `QuestionRecord.tier`; `SessionMeta.questions_core_total` / `questions_overflow_asked` | `preferred_next_signal` (brain naturalness hint, now honored unconditionally); `resolve_next` collapses to "next unasked by position" |
| **Knockout — engine** | `BrainTurnOutput.knockout_confirmed`; `BrainTurnInput.knockout_pending` / `knockout_reflected`; `CoverageProjection.knockout_pending()`; `gate_knockout` + `KnockoutTracker` / `KnockoutStep`; `_steer_knockout` / `confirmed_knockout_signals` / `_knockout_reflect_offered` / `_KNOCKOUT_REFLECT_LINE`; KNOCKOUT prompt section + render blocks | `end_requested` (candidate may always end); `move=close` / `confirm`; `scrub_composed_say`; `coerce_probe_dimension` |
| **Knockout — durable + report + DB** | `KnockoutOutcome`; `SessionEvidence.knockout`; `CompletionReason.knockout_close`; `KnockoutFailure` + `SessionResult.knockout_failures` + orphaned `_scrub_pii`/regexes; report `is_knockout_close` / `knockout_signal` gate (`evidence_adapter`, `holistic`, verdict); `"knockout_close"` response key; `tenant_settings.engine_knockout_policy` + `KnockoutPolicy`; migration dropping `sessions.knockout_failures` + `tenant_settings.engine_knockout_policy` | report must-have logic driven by `signal.knockout` (identification, must-have-met ceiling, narrative tagging) |
| **Bank metadata** | engine-wire `QuestionConfig.estimated_minutes` + `QuestionConfig.is_mandatory` + their `build_session_config` projection (the engine was their sole consumer); `build_session_config` question ordering → `position` only | `stage_questions.estimated_minutes` + `is_mandatory` **DB columns** — the reporting actor (`reporting/actors.py`) reads them off `StageQuestion` directly |

---

## 4. Approach — two phased sweeps (Approach A)

Sequence the deletion so the suite stays green at every checkpoint and each diff is
coherent and bisectable.

- **Phase 1a — Clock + resolver-tier deletion.** Engine-local (plus the small,
  unavoidable durable-contract touch for tier-derived fields). Lower risk,
  self-contained. Ends green + talk-test before anything durable/report/DB.
- **Phase 1b — Knockout full-vertical deletion.** Engine → durable contract →
  reporting → settings → DB migration → tests.

Within each phase, work in dependency order: prompts + brain logic → contract
fields → resolver/driver wiring → durable evidence → reporting → DB migration →
delete/rewrite tests last.

---

## 5. Phase 1a — Clock + resolver-tier deletion

### 5.1 The new `resolve_next`

```python
def resolve_next(*, questions, asked_ids, preferred_next_signal=None) -> ResolverQuestion | None:
    unasked = [q for q in questions if q.question_id not in asked_ids]
    if not unasked:
        return None                       # bank exhausted → close
    if preferred_next_signal is not None:
        pref = next((q for q in unasked if q.primary_signal == preferred_next_signal), None)
        if pref is not None:
            return pref                    # honor the brain's flow hint, unconditionally
    return min(unasked, key=lambda q: q.position)   # else next by position
```

`ResolverQuestion` collapses to `{question_id, primary_signal, position}`. Removed:
`tier`, `weight`, `estimated_minutes`, `is_mandatory`, the `covered_signals` param,
`time_remaining_s`, `cfg`.

### 5.2 File-by-file

- **`brain/resolver.py`** — delete `BudgetConfig`, `compute_budget_phase`,
  `budget_config_from_ai_config`, all `BudgetPhase` usage; rewrite `resolve_next`;
  slim `ResolverQuestion`; `build_question_records` drops `tier` (+ `QuestionTier`
  import).
- **`contracts.py`** — delete `BudgetPhase` enum + `BrainTurnInput.budget_phase`.
- **`brain/input_builder.py`** — drop `budget_phase` param from `build_turn_input`;
  delete the `## Budget Phase` render block in `render_suffix`.
- **`brain/service.py`** — drop `BudgetConfig` / factory imports;
  `ControlPlane.decide` + `_BrainAdapter` stop passing `time_remaining_s` / `cfg`;
  resolver call simplified.
- **`driver.py`** — delete `_time_remaining_s`, `_budget_cfg`,
  `_BrainAdapter.time_remaining_s`; build `ResolverQuestion` with
  `{id, primary_signal, position}`; `finalize` stops computing
  `questions_core_total` / `questions_overflow_asked`.
- **`interview_runtime/schemas.py` + `interview_runtime/service.py`** — remove
  `QuestionConfig.estimated_minutes` + `QuestionConfig.is_mandatory` (the engine
  was the sole consumer); `build_session_config` stops projecting them and orders
  questions by `position` only. The `stage_questions.estimated_minutes` /
  `is_mandatory` **DB columns stay** — `reporting/actors.py` reads them off
  `StageQuestion` directly.
- **`app/ai/config.py`** + **`app/config.py`** — delete `engine_close_reserve_s`,
  `engine_winding_down_s`.
- **`prompts/v4/engine/brain.system.txt`** — delete the winding-down sentence;
  reword "WHEN A THREAD IS DONE" so advancing is coverage-driven, not time-driven.

### 5.3 Durable-contract touch (kept in 1a so the suite stays green)

`SessionMeta` loses `questions_core_total` / `questions_overflow_asked`;
`QuestionRecord` loses `tier`; `QuestionTier` enum deleted. These move with the
resolver because `build_question_records` derives them from `ResolverQuestion`.
Any reporting consumer of `QuestionRecord.tier` is updated in this phase (verified
in the inventory: no reporting consumer reads the two `SessionMeta` counts today).

### 5.4 Checkpoint

Suite green + a manual talk-test: the screen opens, advances through all bank
questions by position, probes (fire-once + per-thread cap intact), and closes on
bank exhaustion. No durable/report/DB changes yet.

---

## 6. Phase 1b — Knockout full-vertical deletion

Dependency order: engine → durable → reporting → settings → DB → tests.

### 6.1 Engine runtime
- **`contracts.py`** — delete `BrainTurnOutput.knockout_confirmed`,
  `BrainTurnInput.knockout_pending`, `BrainTurnInput.knockout_reflected`.
  **Keep** `end_requested` (verified separable from knockout) and
  `SignalSpec.knockout` (data).
- **`brain/policy.py`** — delete `gate_knockout`, `KnockoutTracker`, `KnockoutStep`.
  **Keep** `scrub_composed_say` + `coerce_probe_dimension`.
- **`brain/input_builder.py`** — delete `CoverageProjection.knockout_pending()` and
  the `## KNOCKOUT PENDING` / `## KNOCKOUT ALREADY REFLECTED` render blocks.
- **`brain/service.py`** — delete `_KNOCKOUT_REFLECT_LINE`, `knockout_tracker`,
  `_knockout_reflect_offered`, the reflect-pending logic in `decide`, the knockout
  gate block in `_derive_directive`, `_steer_knockout`,
  `confirmed_knockout_signals`; drop the tracker from `build_control_plane`.
- **`driver.py`** — delete the "Record a KnockoutOutcome" block in `finalize` + the
  `confirmed_knockout_signals()` call; drop the `to_session_evidence(knockout=...)`
  argument; completion stays `completed` (never `knockout_close`).
- **`prompts/v4/engine/brain.system.txt`** — delete the whole KNOCKOUT section + the
  "ABSENCE IS DIFFERENT FOR A KNOCKOUT" lines; proofread for coherence. `confirm`
  stays as a general STT-mishearing move.

### 6.2 Durable contract
- **`interview_runtime/evidence.py`** — delete `KnockoutOutcome`,
  `SessionEvidence.knockout`, `CompletionReason.knockout_close`.
- **`interview_runtime/schemas.py`** — delete `KnockoutFailure`,
  `SessionResult.knockout_failures`, and the now-orphaned `_scrub_pii` /
  `_EMAIL_RE` / `_PHONE_RE` (after confirming no other user).
- **`interview_runtime/service.py`** — `record_session_result` stops writing
  `knockout_failures`.

### 6.3 Reporting (remove the gate, keep must-have awareness)
- **`scoring/evidence_adapter.py`** — delete `is_knockout_close` + `knockout_signal`
  properties.
- **`scoring/holistic.py`** + verdict resolver — drop `is_knockout_close` /
  `knockout_signal` params and their branches; **keep** must-have identification +
  the must-have-met ceiling driven by `signal.knockout`.
- **`reporting/service.py`** — delete the `"knockout_close"` response key; keep
  `must_have` / must-have-set logic.
- **`prompts/v4/report_scorer/*`** — strip any knockout_close instructions.

### 6.4 Settings + DB
- **`tenant_settings/{models,schemas}.py`** — delete `engine_knockout_policy` +
  `KnockoutPolicy`.
- **New migration `0059_drop_knockout`** (current head is
  `0058_bank_coverage_feasibility`) — drop
  `tenant_settings.engine_knockout_policy` (+ its check constraint) and
  `sessions.knockout_failures`; full `downgrade()` restoring both. Migrations
  `0027` (added both columns) / `0030` (changed the policy default) remain as
  historical record.

### 6.5 Tests
Delete the knockout test files/cases: `test_policy.py` knockout classes,
`test_brain_service.py` knockout cases, `input_builder` knockout-pending tests,
driver knockout tests, the three `knockout_failures` test files. Refactor
verdict/scoring tests to drop knockout branches.

---

## 7. Behavior preservation (regression guardrails)

After both phases these invariants MUST still hold:

- Screen runs end-to-end: intro → opener → advance through every bank question **by
  position** → probe (dimension fire-once + per-thread cap via
  `coerce_probe_dimension`) → close on bank exhaustion.
- A candidate can still end anytime (`end_requested` → `close`).
- `confirm` (STT-mishearing reflect-back) works as a general move, independent of
  knockout.
- No-leak stays structural; `scrub_composed_say` intact.
- Turn assembly, bridge ∥ brain → mouth, merge-back checkpoint, anti-stall,
  backchannel drop — all untouched.
- A missing must-have still hurts the candidate via **normal scoring** in the
  report (→ borderline/reject, human-reviewed). "Records never rejects; borderline
  always human" preserved.

### Verdict-behavior delta (recorded explicitly)
Previously, an engine-verified must-have absence produced `completion=knockout_close`,
which drove a ceiling cap + reject-leaning verdict. That path is removed. A
candidate who would have hit `knockout_close` now flows through normal scoring: a
must-have with no/contradicting evidence still tanks the relevant dimension and
lands the candidate at borderline/reject by score, under human review. There is no
longer a dedicated engine-driven auto-gate for must-have absence. This is the
intended behavior until the future knockout redesign.

---

## 8. Verification & testing

- **Unit/contract:** rewrite resolver tests to the new signature; add a grep-based
  guard test asserting `BudgetPhase` / `KnockoutOutcome` / `knockout_close` /
  `KnockoutFailure` / `engine_knockout_policy` import nowhere (enforces the
  "no stale code" bar).
- **Engine suite green** at the 1a checkpoint and again after 1b. Run via the
  in-container `coverage` path (CLAUDE.md → "Coverage in Docker") to avoid the
  PyO3 / Python-3.13 segfault in the livekit dep tree.
- **Talk-test** after 1a (screen still flows) and after 1b (no knockout language,
  clean close).
- **Reporting:** run the report scorer against a saved `SessionEvidence` fixture to
  confirm verdict + ceiling still resolve with the knockout branch gone.
- **Migration:** `alembic upgrade head` then `downgrade` on a scratch DB; boot the
  app so `_assert_rls_completeness` passes (no RLS change, but confirms the dropped
  columns don't break startup).

---

## 9. Compliance & operational notes

- **Human-review-required surfaces touched** (CLAUDE.md): the session state machine
  (`CompletionReason`) and report verdict/scoring. The solo developer is the
  reviewer of record; this spec captures the rationale and the verdict delta (§7).
- **Migration ships with a rollback** (`downgrade`), per CLAUDE.md migration policy.
- **Worker restart** required after the backend changes so the `score_session_report`
  actor runs new code (`nexus-worker` has no hot-reload).
- **Engine restart** (`docker compose up -d --force-recreate nexus-engine`) after
  prompt/engine edits — a per-session `PromptLoader` reads fresh, but agent.py /
  engine code changes need the restart.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| A "kept" feature (e.g. `confirm`, `end_requested`) is silently entangled with deleted knockout code | Inventory verified separability; the talk-test + suite catch a regression at the 1a/1b checkpoints |
| Durable-contract field removal breaks a report/reel/analytics reader not found in the inventory | grep-guard test + report-scorer fixture run; the migration `downgrade` allows rollback |
| Verdict behavior change surprises later | Delta documented in §7; the report still penalizes must-have absence via normal scoring |
| Orphaned helpers left behind (`_scrub_pii`, regexes) | Explicit "after confirming no other user" check in 6.2; grep-guard test |

---

## 11. Follow-on (Spec 2 — not this spec)

On the clean base: make the brain *use* the bank's rich per-question fields
(`positive_evidence`, `red_flags`, `evaluation_hint`) to steer probing/elicitation,
and resolve `difficulty` (wire it to behavior or delete it). Separate spec → plan →
implementation cycle.
