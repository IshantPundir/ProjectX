# Bank Coverage & Distinctness Invariants — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee deterministically that an AI-screening bank's scored set is broad and non-redundant — no two scored questions share a skill, and the single deep-dive widens coverage when skills overflow the budget.

**Architecture:** Deterministic core in `invariants.py` (two new pure, unit-tested checks + a `hard_repair` dedupe step), riding the existing `check_bank_invariants → critic re-pass → hard_repair` machinery (no new call sites). Supporting prompt edits sharpen signal-merge and wire the generator/critic. No planner-logic, schema, or migration change.

**Tech Stack:** Pure Python (`app/modules/question_bank/invariants.py`), pytest (`tests/question_bank/test_invariants.py`); plain `.txt` prompts under `prompts/`.

## Global Constraints

- Deterministic invariants are the guarantee; prompts only help the LLM produce a compliant bank first-try ("the LLM critic can't be trusted to count").
- Hybrid deep-dive: its `primary_signal` must be an overflow (`secondary_only`) must-have skill when an uncovered one exists; otherwise unconstrained (project-agnostic).
- No change to `build_coverage_plan`, schema, migration, the actor's orchestration, or prompt versions (signal `v2`, bank `v3`).
- Don't relax prior rules (substance-fidelity, PRESERVE-EVERY-MUST-HAVE, weight-3, concreteness, two-sentence lead, same-competency merge). This ADDS distinctness invariants.
- No JD-specific text in prompts — structural rules only.
- Spec: `docs/superpowers/specs/2026-06-18-bank-coverage-distinctness-invariants-design.md`.
- `invariants.py` is pure (no DB/LLM). Prompts + actor run in the lean `nexus-worker` (no hot-reload) → restart after prompt changes.

---

### Task 1: Deterministic distinctness invariants (`invariants.py`) + tests

**Files:**
- Modify: `app/modules/question_bank/invariants.py`
- Test: `tests/question_bank/test_invariants.py`

**Interfaces:**
- Consumes: `GeneratedQuestion`, `QuestionRubric`, `FollowUpDimension`, `CoveragePlan`, and the existing `_q(...)` test factory in `test_invariants.py`.
- Produces: two new `Violation` codes from `check_bank_invariants` — `duplicate_scenario_primary` (hard_repairable=True) and `deepdive_primary_uncovered_overflow` (hard_repairable=False); `hard_repair` now dedupes duplicate scenario primaries.

- [ ] **Step 1: Write the failing tests**

Append to `tests/question_bank/test_invariants.py` (the `_q` factory + imports already exist at the top of that file):

```python
def test_duplicate_scenario_primary_flagged():
    qs = [_q("technical_scenario", signals=("API integration",), pos=0),
          _q("technical_scenario", signals=("API integration",), pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=None)
    assert any(v.code == "duplicate_scenario_primary" and v.hard_repairable for v in vs)


def test_distinct_scenario_primaries_no_violation():
    qs = [_q("technical_scenario", signals=("API integration",), pos=0),
          _q("technical_scenario", signals=("DB modeling",), pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=None)
    assert not any(v.code == "duplicate_scenario_primary" for v in vs)


def test_hard_repair_dedupes_duplicate_scenarios():
    qs = [_q("technical_scenario", signals=("API integration",), pos=0),
          _q("technical_scenario", signals=("API integration",), pos=1),
          _q("technical_scenario", signals=("DB modeling",), pos=2)]
    repaired = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20)
    prims = [q.primary_signal for q in repaired if q.question_kind == "technical_scenario"]
    assert prims.count("API integration") == 1
    assert "DB modeling" in prims
    assert [q.position for q in repaired] == list(range(len(repaired)))  # repacked 0..N-1


def test_deepdive_must_cover_uncovered_overflow():
    qs = [_q("technical_scenario", signals=("integration",), pos=0),
          _q("project_deepdive", signals=("integration",), pos=1)]
    plan = CoveragePlan(slot_budget=1, must_cover_count=3,
                        required_primaries=["integration"],
                        secondary_only=["language", "database"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    cov = [v for v in vs if v.code == "deepdive_primary_uncovered_overflow"]
    assert cov and cov[0].hard_repairable is False
    assert "language" in cov[0].description or "database" in cov[0].description


def test_deepdive_covering_overflow_no_violation():
    qs = [_q("technical_scenario", signals=("integration",), pos=0),
          _q("project_deepdive", signals=("language",), pos=1)]
    plan = CoveragePlan(slot_budget=1, must_cover_count=3,
                        required_primaries=["integration"],
                        secondary_only=["language", "database"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert not any(v.code == "deepdive_primary_uncovered_overflow" for v in vs)


def test_no_overflow_deepdive_unconstrained():
    qs = [_q("technical_scenario", signals=("integration",), pos=0),
          _q("project_deepdive", signals=("integration",), pos=1)]
    plan = CoveragePlan(slot_budget=6, must_cover_count=1,
                        required_primaries=["integration"])  # secondary_only defaults to []
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert not any(v.code == "deepdive_primary_uncovered_overflow" for v in vs)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -k "duplicate_scenario or dedupes_duplicate or overflow or deepdive_unconstrained" -v`
Expected: the `duplicate_scenario_primary`, `hard_repair dedupe`, and `deepdive` tests FAIL (codes/behavior don't exist yet); the two `no_violation` tests may pass trivially (the codes aren't produced). All targeted tests pass only after Step 3.

- [ ] **Step 3: Implement the two invariants + the dedupe repair**

In `app/modules/question_bank/invariants.py`:

(a) In `check_bank_invariants`, immediately AFTER the `over_budget` block (the one that appends `Violation("over_budget", …)`) and BEFORE the `if plan is not None:` block, insert:
```python
    # Distinctness: no two technical_scenario questions may share a primary_signal (a wasted
    # scored slot + a duplicate question). The deep-dive is exempt here — its sharing is
    # governed by the hybrid overflow rule below.
    scenario_primaries = [
        q.primary_signal for q in questions if q.question_kind == "technical_scenario"
    ]
    dup_primaries = sorted({p for p in scenario_primaries if scenario_primaries.count(p) > 1})
    if dup_primaries:
        out.append(Violation(
            "duplicate_scenario_primary",
            f"More than one technical_scenario shares these primary_signals: {dup_primaries}. "
            "Each scenario must own a DISTINCT skill. Rewrite the duplicate(s) onto a skill not "
            "yet covered (prefer a secondary-only skill from the coverage plan), or drop it.",
            True,
        ))
```

(b) Inside the existing `if plan is not None:` block, AFTER the `for sig in plan.required_primaries:` loop that appends `uncovered_required_primary`, insert:
```python
        # Hybrid deep-dive coverage: when overflow must-have skills exist that no scenario
        # scored, the single project_deepdive must score ONE of them (breadth). With no
        # uncovered overflow, the deep-dive is unconstrained (project-agnostic).
        if plan.secondary_only:
            _scenario_primary_set = {
                q.primary_signal for q in questions
                if q.question_kind == "technical_scenario"
            }
            uncovered_overflow = [
                s for s in plan.secondary_only if s not in _scenario_primary_set
            ]
            deepdives = [q for q in questions if q.question_kind == "project_deepdive"]
            if (
                uncovered_overflow
                and deepdives
                and deepdives[0].primary_signal not in set(uncovered_overflow)
            ):
                out.append(Violation(
                    "deepdive_primary_uncovered_overflow",
                    f"Overflow must-have skills are unscored ({uncovered_overflow}); the single "
                    "project_deepdive must take ONE of them as its primary_signal so it is "
                    f"scored. Set the deep-dive's primary_signal to one of: {uncovered_overflow}.",
                    False,
                ))
```

(c) Add a dedupe helper near `_cap_kind`:
```python
def _dedupe_scenario_primaries(
    questions: list[GeneratedQuestion],
) -> list[GeneratedQuestion]:
    """Keep at most one technical_scenario per primary_signal (mandatory first, then earliest
    position wins). Non-scenario kinds are never dropped here — the deep-dive may legitimately
    share a primary in a skill-poor bank. Pure."""
    seen: set[str] = set()
    drop: set[int] = set()
    order = sorted(
        range(len(questions)),
        key=lambda i: (not questions[i].is_mandatory, questions[i].position),
    )
    for i in order:
        q = questions[i]
        if q.question_kind != "technical_scenario":
            continue
        if q.primary_signal in seen:
            drop.add(i)
        else:
            seen.add(q.primary_signal)
    return [q for i, q in enumerate(questions) if i not in drop]
```

(d) In `hard_repair`, call the dedupe AFTER the `_cap_kind(..., "behavioral", ...)` line and BEFORE `_trim_to_budget(...)`:
```python
    qs = _dedupe_scenario_primaries(qs)
```

- [ ] **Step 4: Run the new tests + the full invariants suite**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -v`
Expected: PASS — the 6 new tests AND all pre-existing invariants tests (the new checks don't fire on the existing fixtures: `duplicate_scenario_primary` needs ≥2 same-primary scenarios; `deepdive_primary_uncovered_overflow` needs a non-empty `plan.secondary_only`).

- [ ] **Step 5: Run the broader question_bank suite for regressions**

Run: `docker compose run --rm nexus pytest tests/question_bank/ tests/test_question_banks_actors.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/modules/question_bank/invariants.py tests/question_bank/test_invariants.py
git commit -m "feat(question_bank): deterministic distinctness invariants

duplicate_scenario_primary (no two scenarios share a primary_signal;
hard_repair dedupes) + deepdive_primary_uncovered_overflow (hybrid deep-dive
must score an overflow skill when one exists). Guarantees a broad,
non-redundant scored set in code, not LLM judgment.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Supporting prompt edits (signal-merge sharpening + generator/critic wiring)

**Files:**
- Modify: `prompts/v2/jd_signal_extraction.txt`
- Modify: `prompts/v3/question_bank_ai_screening.txt`
- Modify: `prompts/v3/question_bank_critic.txt`

**Interfaces:** Consumes/produces nothing in code — prompt text read by `PromptLoader`.

- [ ] **Step 1: Sharpen the signal-merge test (`jd_signal_extraction.txt`)**

In the `# Merge same-competency requirements — even across separate JD lines` section, immediately AFTER the line `Keep two requirements SEPARATE only when a candidate would need genuinely different knowledge` / `or different work to show each (do not over-merge truly distinct skills).`, insert a new paragraph:
```
Operational test — ONE scenario, ONE signal: before emitting two signals, ask "could a single
realistic scenario question test both?" If yes, they are ONE competency — merge them, naming
each. Managing a control and enforcing it, knowing a format/protocol and building with it, and
designing and operating the same system each fail this test as two: one scenario tests each
pair, so each pair is one signal.
```

- [ ] **Step 2: Add the distinct-primary + hybrid-deep-dive rules to `question_bank_ai_screening.txt`**

(a) In recipe item `1. SCORED SCENARIOS`, immediately AFTER the existing final sentence `… never a generic shell like "a customer wants X".`, append:
```
   Each scenario's `primary_signal` is UNIQUE across the bank — never two scenarios on the same
   skill (a duplicate is a wasted scored slot; spend it on an uncovered skill).
```

(b) In recipe item `2. ONE PROJECT DEEP-DIVE`, immediately AFTER its final sentence `… so you usually need no separate behavioral question.`, append:
```
   COVERAGE ROLE: if the coverage plan lists SECONDARY-ONLY skills (must-haves that overflow the
   scored-slot budget), the deep-dive's `primary_signal` MUST be one of them — frame it around a
   real project that exercised that skill, so an otherwise-unscored must-have gets scored. Its
   primary_signal must differ from every scenario's. If there are NO secondary-only skills, let
   the candidate pick their most significant project.
```

- [ ] **Step 3: Add matching critic checks (`question_bank_critic.txt`)**

Immediately AFTER check `10. CONCRETE SITUATION` (its last line) and BEFORE the `# Output` header, insert:
```
11. DISTINCT SCENARIO PRIMARIES -- no two technical_scenario questions share a primary_signal.
    Rewrite a duplicate scenario onto an uncovered skill (prefer a secondary-only skill from the
    plan), or drop it.
12. DEEP-DIVE COVERS OVERFLOW -- when the plan lists secondary-only skills not covered by any
    scenario, the single project_deepdive's primary_signal must be ONE of them (so the
    otherwise-unscored must-have is scored), and must differ from every scenario's primary. When
    there are none, the deep-dive may be the candidate's own most significant project.
```

- [ ] **Step 4: Verify prompts load + regression**

Run:
```
docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; v2=PromptLoader(version='v2'); v3=PromptLoader(version='v3'); print('sig', len(v2.get('jd_signal_extraction'))); [print(n, len(v3.get(n))) for n in ('question_bank_ai_screening','question_bank_critic')]"
docker compose run --rm nexus pytest tests/test_prompt_loader.py tests/test_jd_actor.py tests/question_bank/ -q
```
Expected: three positive char counts (no exception) and tests PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/v2/jd_signal_extraction.txt prompts/v3/question_bank_ai_screening.txt prompts/v3/question_bank_critic.txt
git commit -m "feat(question_bank): wire distinct-primary + hybrid deep-dive into prompts

Signal merge gains a ONE-scenario-ONE-signal operational test; ai_screening +
critic instruct unique scenario primaries and the deep-dive covering an
overflow skill (mirrors the deterministic invariants).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Live validation (Workato, EMM, skill-poor guard)

No automated test — prompt/LLM behavior is proven live; the deterministic invariants are unit-tested in Task 1. Own task: a reviewer could pass the code yet reject observed banks.

**Files:** none (operational + validation only).

- [ ] **Step 1: Restart the worker**

Run: `docker compose up -d --force-recreate nexus-worker`
Expected: recreated, healthy (`docker compose ps nexus-worker`).

- [ ] **Step 2: Workato — re-extract + regenerate, confirm coverage + no duplication**

"Unlock & re-enrich" the Workato job (`ce6dad9a-…`), then regenerate the AI-screening bank
(stage `2ea4f4a3-…`). Confirm:
- every `technical_scenario` has a distinct `primary_signal` (no two the same);
- the `project_deepdive`'s `primary_signal` is a secondary-only skill (e.g. language or database) — NOT `integration`, and NOT a primary already used by a scenario;
- programming-language and database skills are now scored (helped by the merge sharpening shrinking the skill set);
- generation completes (no `generation_error`).

> Read: `docker exec supabase_db_backend psql -U postgres -d postgres -t -A -F'||' -c "select position, question_kind, primary_signal, text from stage_questions q join stage_question_banks b on b.id=q.bank_id where b.stage_id='2ea4f4a3-4199-4403-9e2b-744284c8233f' order by position;"`

- [ ] **Step 3: EMM — re-extract + regenerate, confirm de-dup + no redundant pair**

"Unlock & re-enrich" the EMM job (`11650922-…`), regenerate (stage `e537d1aa-…`). Confirm:
- `compliance/CA` and `policy-enforcement` merged to one signal (the ONE-scenario test); all must-haves still scored at weight 3;
- no two questions answerable from the same Intune compliance/conditional-access knowledge (the former Q1≈Q2 pair is gone);
- distinct scenario primaries; generation completes.

- [ ] **Step 4: Skill-poor guard**

On a JD with few distinct skills (or by inspecting a small bank), confirm NO false
`deepdive_primary_uncovered_overflow` (deep-dive allowed to be project-agnostic when
`secondary_only` is empty) and NO false `duplicate_scenario_primary` when scenarios are
genuinely distinct.

- [ ] **Step 5: Record the result**

If Workato covers language+DB with distinct primaries, EMM de-dups, and the skill-poor guard
holds, note completion. Otherwise capture the offending bank + which invariant should have
fired and iterate (Task 1 for code, Task 2 for prompts).

---

## Self-Review

**Spec coverage:** Part A (signal-merge sharpening) → Task 2 Step 1. Part B1 (`duplicate_scenario_primary`) → Task 1 Step 3a + dedupe 3c/3d. Part B2 (`deepdive_primary_uncovered_overflow`, hybrid) → Task 1 Step 3b. Part C (ai_screening + critic wiring) → Task 2 Steps 2-3. Wiring (no new call sites) → Global Constraints + Task 1 (plugs into existing `check_bank_invariants`/`hard_repair`). Defect→fix (Workato coverage/duplication, EMM redundancy, overflow correctness, skill-poor) → Task 3 Steps 2-4. Non-goals (no planner/schema/version change) → Global Constraints. Full coverage.

**Placeholder scan:** No TBD/TODO/"handle edge cases". Task 1 gives complete test + implementation code with exact insertion anchors; Task 2 gives exact insert text + anchors. The `X` in prompt text is the intended abstract placeholder. Validation steps enumerate concrete pass/fail + SQL.

**Type consistency:** `Violation(code, description, hard_repairable)` matches the existing dataclass. `CoveragePlan(slot_budget, must_cover_count, required_primaries, secondary_only=…)` matches its fields. `_q(kind, mins, signals, pos, mand)` matches the existing factory. New codes `duplicate_scenario_primary` / `deepdive_primary_uncovered_overflow` used identically in tests, impl, and (by description) the critic.
