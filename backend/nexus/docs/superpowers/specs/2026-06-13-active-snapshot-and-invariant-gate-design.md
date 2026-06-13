# Active-Snapshot Re-pin + Deterministic Post-Critic Invariant Gate

- **Date:** 2026-06-13
- **Branch:** `feat/followups-governed-dimensions`
- **Status:** Design — approved direction, pending spec review
- **Builds on:** `2026-06-12-question-bank-v3-measurement-instrument-design.md` (the critic), `2026-06-12-ai-screening-skills-test-design.md` (signal `purpose`), `2026-06-12-re-extract-signals-design.md` (re-extract)

---

## 1. Why

A live re-extraction of the Workato JD produced an excellent v2 signal snapshot (12 lean,
`purpose`-classified signals), but the regenerated bank consumed the **stale v1 snapshot** (24
signals, no `purpose`). Two coupled defects:

1. **Stale snapshot pin.** `ensure_bank_exists` pins a *new* bank to the latest confirmed
   snapshot, but an *existing* bank (e.g. one cleared by re-extraction) keeps its old
   `signal_snapshot_id`, and `_generate_one_bank` reads that pinned id. So regenerating after a
   re-extract never picks up the new signals — the entire signal-quality improvement (and the
   `purpose`-based eligibility filter) never reaches the bank.

2. **The LLM critic doesn't reliably enforce countable invariants.** The generated bank had
   **two `project_deepdive` questions** that were near-duplicates ("a Workato workflow you built
   end-to-end" vs "an integration project you owned"), while the recipe says *exactly one* — and
   the critic's own log falsely claimed "includes exactly one project deep-dive." The duplicate
   also crowded out the JD's #1 weight-3 skill (AI-driven / agent-based workflows), which went
   untested. An LLM critic asked to "ensure exactly one" pattern-matches "I checked" without
   counting; structural invariants must be enforced by code.

### Goal

(a) Every bank (re)generation consumes the **active** (latest confirmed) signal snapshot; (b) a
**deterministic gate** guarantees the countable invariants the critic misses, with one targeted
critic re-pass to fill any gap the repair would otherwise leave.

### Non-goals

- An explicit `is_active` snapshot column (we keep "active = latest confirmed", derived).
- Auto-confirming a re-extracted snapshot (the recruiter still reviews + confirms it; it becomes
  active automatically *on confirm* because it's the newest confirmed).
- Semantic embedding-based lead-dedup (deferred; the present duplication is the *countable*
  two-`project_deepdive` case, which the gate fixes).

---

## 2. Snapshot re-pin — generate from the active snapshot

"Active snapshot" = the latest snapshot with `confirmed_at` set, resolved by the existing
`get_latest_confirmed_snapshot(db, job_id)` (`question_bank/service.py`).

In `_generate_one_bank` **Phase A** (`question_bank/actors.py`), before generation:
- Resolve `active = await get_latest_confirmed_snapshot(db, job_id)`.
- If `active is None`: keep the existing failure (generation must not run before signals are
  confirmed).
- Set `bank.signal_snapshot_id = active.id` and `bank.is_stale = False`, and use `active` as the
  snapshot whose signals drive generation (the captured `snapshot_id` / `snapshot_signals`
  primitives come from `active`, not the bank's old pin). Commit in Phase A.

Effect: every (re)generation re-pins to the current active signals. After a re-extract +
confirm, the bank now consumes the v2 `purpose`-tagged signals → the eligibility filter
(`_signals_for_generation`) actually drops eligibility signals (it was a no-op on the untagged v1
set), so the "1 year Workato" deep-dive anchor disappears — likely collapsing the duplicate on
its own. The gate (§3–4) is the guarantee regardless.

---

## 3. Deterministic invariant gate — pure, unit-testable

New module `app/modules/question_bank/invariants.py`:

```
@dataclass(frozen=True)
class Violation:
    code: str            # e.g. "too_many_project_deepdive"
    description: str      # concrete, for the critic re-pass + audit log
    hard_repairable: bool

def check_bank_invariants(
    questions: list[GeneratedQuestion], *, stage_type: str,
    stage_duration_minutes: int, signals: list[dict],
) -> list[Violation]: ...

def hard_repair(
    questions: list[GeneratedQuestion], violations: list[Violation], *,
    stage_duration_minutes: int,
) -> list[GeneratedQuestion]: ...
```

For `stage_type == "ai_screening"`, `check_bank_invariants` enforces:

| Invariant | Violation when | hard_repairable | Hard repair |
|---|---|---|---|
| ≤ 1 `project_deepdive` | count > 1 | yes | keep the first/`is_mandatory` one, drop the rest |
| 0 `experience_check` | any present | yes | drop them |
| 0 `compliance_binary` | any present | yes | drop them |
| ≤ 1 `behavioral` | count > 1 | yes | keep one, drop the rest |
| Σ `estimated_minutes` ≤ duration | sum > duration | yes | drop lowest-`primary_signal`-weight (then highest-minutes) until it fits |
| every weight-3 `skill` signal tested | a weight-3 `purpose=skill` signal value appears in no question's `signal_values` | **no** | (re-pass only — code can't synthesize a question) |

`hard_repair` applies only the `hard_repairable` fixes; it re-packs `position` 0..N-1 afterward.
For non-`ai_screening` stages it returns the questions unchanged (no ai-screening rules). The
weight-3-coverage check reads `signals` (the active snapshot's signal dicts, which carry
`weight`/`purpose`/`value`).

---

## 4. Critic re-pass + actor wiring

`run_bank_critic` (`question_bank/critic.py`) gains an optional `violations: list[str] | None`.
When provided, `_build_critic_user_message` appends a section:

```
# YOU MUST FIX THESE SPECIFIC VIOLATIONS (a deterministic check found them; do not claim
# they are already fixed):
#   - <violation.description>
#   - ...
```

`_generate_one_bank` flow becomes (bounded at ≤ 2 critic calls):

1. Phase B — stream draft.
2. Phase B2 — `self_reviewing`.
3. Phase B3a — **critic pass 1** (`run_bank_critic(draft, ...)`) → corrected.
4. **gate** — `violations = check_bank_invariants(corrected, ...)`.
5. If `violations`: **critic re-pass** — `run_bank_critic(corrected, ..., violations=[v.description for v in violations])` → corrected2; else `corrected2 = corrected`.
6. **gate again** — `remaining = check_bank_invariants(corrected2, ...)`.
7. **`final = hard_repair(corrected2, remaining, ...)`** — guarantees the hard invariants.
8. Persist `final` + append to `coverage_notes` a one-line audit of what the gate caught/repaired
   ("gate: dropped 1 duplicate project_deepdive; AI-workflow skill still uncovered after re-pass").
9. Phase C reconcile → `reviewing`.

The critic-failure fallback (§ existing) is unchanged: if `run_bank_critic` raises, keep the
streamed draft, but the gate + `hard_repair` STILL run on the draft (so the invariants hold even
when the critic is unavailable).

---

## 5. Consumers / edges

- The gate runs only for `ai_screening` (keyed on `stage_type`, already captured in Phase A).
- `hard_repair` operating on `GeneratedQuestion` objects keeps them schema-valid (it only drops
  whole questions + re-packs positions — never mutates a question into an invalid shape).
- After re-pin, validation (`validate_streamed_question`) still runs against the active snapshot's
  full signal set (the existing capture), so corrected/repaired questions validate against v2.
- `is_stale` is cleared at re-pin (the bank now matches the active snapshot).

---

## 6. Testing

- **`invariants.py` unit:** 2 deep-dives → one `too_many_project_deepdive` violation; `hard_repair`
  leaves exactly 1 + re-packs positions. Over-budget → trims to ≤ duration. `experience_check`/
  `compliance_binary` present → flagged + dropped. 2 behaviorals → 1. Uncovered weight-3 skill →
  detected, `hard_repairable=False` (not dropped). A clean ai_screening bank → no violations.
  Non-ai_screening stage → no violations (rules don't apply).
- **critic re-pass:** `run_bank_critic(..., violations=[...])` includes the violations section in
  the user message (assert on the built message); without it, unchanged.
- **actor (DB-backed, critic mocked):** a first-pass bank with 2 deep-dives triggers a re-pass
  (mock returns 1) → persisted bank has exactly 1; a first-pass clean bank → no re-pass; a
  critic that keeps 2 deep-dives across both passes → `hard_repair` still yields 1 (guarantee).
- **snapshot re-pin (DB-backed):** a bank pinned to an older snapshot, with a newer confirmed
  snapshot present, regenerates against the newer one (`bank.signal_snapshot_id` updated,
  `is_stale=False`).
- **live smoke (user):** regenerate the Workato bank → exactly one project deep-dive, the
  AI-workflow skill tested, ≤ 20 min, built from the v2 signals.

---

## 7. Code-quality mandate

- `invariants.py` is pure (no DB/LLM) and fully unit-tested — the guarantee lives in code, not a
  prompt.
- Re-pin reuses the existing `get_latest_confirmed_snapshot` resolver (no new resolver, no
  `is_active` column).
- The gate + `hard_repair` run even on the critic-failure fallback path (invariants hold without
  the LLM).
- Bounded LLM cost: at most one extra critic call, only on violation.
- Every new branch (each invariant, each hard-repair, the re-pass, the re-pin) ships with a test.
