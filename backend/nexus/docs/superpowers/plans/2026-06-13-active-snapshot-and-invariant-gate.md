# Active-Snapshot Re-pin + Invariant Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bank (re)generation consume the active (latest confirmed) signal snapshot, and guarantee the countable AI-screening invariants via a pure deterministic gate + one targeted critic re-pass + hard-repair fallback.

**Architecture:** A 2-line re-pin in the generation actor's Phase A (resolve the latest confirmed snapshot instead of the stale pinned id). A new pure `question_bank/invariants.py` (`check_bank_invariants` + `hard_repair`). `run_bank_critic` gains a `violations` param for the targeted re-pass. The actor wires: critic → gate → (re-pass if violations) → always hard_repair → persist.

**Tech Stack:** FastAPI, SQLAlchemy async, Dramatiq, OpenAI via instructor, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-06-13-active-snapshot-and-invariant-gate-design.md`

---

## Code-quality mandate
- `invariants.py` is PURE (no DB/LLM); the invariant guarantee lives in code, not a prompt.
- Re-pin reuses `get_latest_confirmed_snapshot` (no new resolver, no `is_active` column).
- `hard_repair` runs on the critic-failure fallback path too (invariants hold without the LLM).
- ≤ 2 critic calls (pass 1 + one re-pass, only on violation). Every new branch ships with a test.

---

## File Structure
- `app/modules/question_bank/actors.py` — Phase A re-pin; B3 gate/re-pass/hard_repair wiring.
- `app/modules/question_bank/invariants.py` (CREATE) — `Violation`, `check_bank_invariants`, `hard_repair`.
- `app/modules/question_bank/critic.py` — `run_bank_critic(..., violations=...)`.

---

## Task 1: Snapshot re-pin — generate from the active snapshot

**Files:**
- Modify: `app/modules/question_bank/actors.py` (`_generate_one_bank` Phase A, the snapshot load ~line 561-568).
- Test: `tests/question_bank/test_generation_quality.py` or `tests/test_question_banks_actors.py` (DB-backed).

- [ ] **Step 1: Write the failing test**

In the DB-backed actor test harness (`tests/test_question_banks_actors.py` — reuse its tenant/job/pipeline/bank seeding + `_patch_session`/`_patch_stream`/`_patch_critic_passthrough`), add a test: seed a job with TWO confirmed snapshots (v1 older, v2 newer) and a bank whose `signal_snapshot_id` points at the OLD v1. Run `_generate_one_bank` (stream + critic patched). Assert that after generation the bank's `signal_snapshot_id == v2.id` (re-pinned to latest confirmed) and `bank.is_stale is False`. Name it `test_generation_repins_to_latest_confirmed_snapshot`. (If the harness doesn't easily seed two snapshots, add a small builder that inserts a second `JobPostingSignalSnapshot` with a higher `version` + `confirmed_at` set.)

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k repins -q`
Expected: FAIL — the bank stays pinned to v1.

- [ ] **Step 3: Re-pin in Phase A**

In `_generate_one_bank`, the Phase A block currently loads `snapshot` by `bank.signal_snapshot_id`:
```python
        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
        ).scalar_one()
```
Replace it with a re-pin to the active (latest confirmed) snapshot:
```python
        from app.modules.question_bank.service import get_latest_confirmed_snapshot
        snapshot = await get_latest_confirmed_snapshot(db, bank.job_posting_id)
        if snapshot is None:
            transition_to_failed(
                bank, error="No confirmed signal snapshot to generate from."
            )
            await db.commit()
            raise RuntimeError(
                f"Cannot generate bank {bank_id}: no confirmed signal snapshot."
            )
        # Re-pin to the active snapshot so a (re)generation after a re-extract uses the
        # latest confirmed signals, not the bank's stale pinned snapshot.
        bank.signal_snapshot_id = snapshot.id
        bank.is_stale = False
```
(Place this where the old `snapshot = ...` load was. `get_latest_confirmed_snapshot` is in `service.py`; import it. The `transition_to_failed` + `text`/`select`/`JobPostingSignalSnapshot` are already imported. The subsequent Phase-A code that captures `snapshot_id = snapshot.id` + `snapshot_signals = list(snapshot.signals)` now captures the ACTIVE snapshot's id/signals — verify those capture lines come AFTER this and use `snapshot`.)

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k repins -q`
Expected: PASS. Then the full actor suite: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -m "not prompt_quality" -q 2>&1 | tail -6` (existing tests that seed one confirmed snapshot still pass — re-pin is a no-op when the pinned snapshot already IS the latest confirmed).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/test_question_banks_actors.py
git commit -m "fix(question_bank): regeneration re-pins to the active (latest confirmed) snapshot"
```

---

## Task 2: `invariants.py` — `check_bank_invariants`

**Files:**
- Create: `app/modules/question_bank/invariants.py`
- Test: `tests/question_bank/test_invariants.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/question_bank/test_invariants.py
from app.modules.question_bank.invariants import check_bank_invariants, Violation
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric, FollowUpDimension


def _q(kind, mins=4.0, signals=("Workato workflow development",), pos=0, mand=False):
    return GeneratedQuestion(
        position=pos, text="Walk me through a Workato workflow you designed.",
        primary_signal=signals[0], signal_values=list(signals), estimated_minutes=mins,
        is_mandatory=mand,
        follow_ups=[FollowUpDimension(dimension="d", intent="i",
                    seed_probe="What did you choose it over?", listen_for=["a tradeoff"])],
        positive_evidence=["a", "b", "c"], red_flags=["says we", "no tradeoff"],
        rubric=QuestionRubric(excellent="x" * 20, meets_bar="y" * 20, below_bar="z" * 20),
        evaluation_hint="tests skill depth", question_kind=kind,
    )


def _sig(value, weight=3, purpose="skill"):
    return {"value": value, "weight": weight, "purpose": purpose, "type": "competency"}


def test_two_project_deepdives_flagged():
    qs = [_q("project_deepdive", pos=0), _q("project_deepdive", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "too_many_project_deepdive" and v.hard_repairable for v in vs)


def test_forbidden_kinds_flagged():
    qs = [_q("experience_check"), _q("compliance_binary")]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "forbidden_kind" and v.hard_repairable for v in vs)


def test_two_behavioral_flagged():
    qs = [_q("behavioral", pos=0), _q("behavioral", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "too_many_behavioral" for v in vs)


def test_over_budget_flagged():
    qs = [_q("technical_scenario", mins=15.0), _q("technical_scenario", mins=15.0)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "over_budget" for v in vs)


def test_uncovered_high_weight_skill_detected_not_repairable():
    qs = [_q("technical_scenario", signals=("Workato workflow development",))]
    signals = [_sig("Workato workflow development", 3), _sig("AI-driven workflows", 3)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=signals)
    cov = [v for v in vs if v.code == "uncovered_high_weight_skill"]
    assert cov and cov[0].hard_repairable is False
    assert "AI-driven workflows" in cov[0].description


def test_clean_ai_screen_has_no_violations():
    qs = [_q("technical_scenario", mins=4.0, signals=("Workato workflow development",)),
          _q("project_deepdive", mins=4.0, signals=("Workato workflow development",))]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20,
                               signals=[_sig("Workato workflow development", 3)])
    assert vs == []


def test_non_ai_screening_stage_no_rules():
    qs = [_q("project_deepdive"), _q("project_deepdive"), _q("experience_check")]
    vs = check_bank_invariants(qs, stage_type="phone_screen", stage_duration_minutes=10, signals=[])
    assert vs == []
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `check_bank_invariants`**

```python
# app/modules/question_bank/invariants.py
"""Pure, deterministic invariant checks + hard-repair for an AI-screening bank.

The LLM critic is unreliable at COUNTABLE invariants (it falsely claims compliance), so the
guarantee lives here in code. check_bank_invariants reports violations (for the critic re-pass
+ audit log); hard_repair unconditionally enforces the hard invariants. Both are pure (no DB,
no LLM) and operate on GeneratedQuestion objects."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.question_bank.schemas import GeneratedQuestion

_MAX_PROJECT_DEEPDIVE = 1
_MAX_BEHAVIORAL = 1
_FORBIDDEN_KINDS = ("experience_check", "compliance_binary")


@dataclass(frozen=True)
class Violation:
    code: str
    description: str       # concrete, fed to the critic re-pass + the audit note
    hard_repairable: bool


def check_bank_invariants(
    questions: list[GeneratedQuestion],
    *,
    stage_type: str,
    stage_duration_minutes: int,
    signals: list[dict],
) -> list[Violation]:
    """Countable invariants for an AI skills screen. Returns [] for other stage types."""
    if stage_type != "ai_screening":
        return []
    out: list[Violation] = []
    kinds = [q.question_kind for q in questions]

    n_dd = kinds.count("project_deepdive")
    if n_dd > _MAX_PROJECT_DEEPDIVE:
        out.append(Violation(
            "too_many_project_deepdive",
            f"There are {n_dd} project_deepdive questions; an AI skills screen must have "
            "EXACTLY ONE. Reduce to one and replace the extra(s) with technical_scenario "
            "questions that test an uncovered high-weight skill.",
            True,
        ))
    n_beh = kinds.count("behavioral")
    if n_beh > _MAX_BEHAVIORAL:
        out.append(Violation(
            "too_many_behavioral",
            f"There are {n_beh} behavioral questions; at most one is allowed. Convert the "
            "extra(s) to technical_scenario questions.",
            True,
        ))
    forbidden = sorted({k for k in kinds if k in _FORBIDDEN_KINDS})
    if forbidden:
        out.append(Violation(
            "forbidden_kind",
            f"These question kinds are not allowed in an AI skills screen: {forbidden}. "
            "Replace each with a technical_scenario that makes the candidate demonstrate the skill.",
            True,
        ))
    total = sum(float(q.estimated_minutes) for q in questions)
    if total > stage_duration_minutes:
        out.append(Violation(
            "over_budget",
            f"Total estimated time is {total:.0f} min, over the {stage_duration_minutes} min "
            "budget. Remove the lowest-priority question(s) so the bank fits.",
            True,
        ))
    tested = {v for q in questions for v in q.signal_values}
    for s in signals:
        if int(s.get("weight", 1)) == 3 and s.get("purpose", "skill") == "skill":
            if s.get("value") and s["value"] not in tested:
                out.append(Violation(
                    "uncovered_high_weight_skill",
                    f"The high-weight skill '{s['value']}' is not tested by any question. "
                    "Add a technical_scenario for it.",
                    False,
                ))
    return out
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -q`
Expected: PASS (7 tests; `hard_repair` tests come in Task 3 and will error on import until then — only run the check tests here with `-k "flagged or detected or clean or non_ai"` if needed, or accept Task 3 adds the missing function).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/invariants.py backend/nexus/tests/question_bank/test_invariants.py
git commit -m "feat(question_bank): check_bank_invariants — deterministic AI-screen invariant checks"
```

---

## Task 3: `invariants.py` — `hard_repair`

**Files:**
- Modify: `app/modules/question_bank/invariants.py`
- Test: `tests/question_bank/test_invariants.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/question_bank/test_invariants.py`:
```python
from app.modules.question_bank.invariants import hard_repair


def test_hard_repair_caps_project_deepdive_to_one_keeps_mandatory():
    qs = [_q("project_deepdive", pos=0, mand=False), _q("project_deepdive", pos=1, mand=True)]
    out = hard_repair(qs, stage_duration_minutes=20)
    dds = [q for q in out if q.question_kind == "project_deepdive"]
    assert len(dds) == 1 and dds[0].is_mandatory is True  # kept the mandatory one
    assert [q.position for q in out] == list(range(len(out)))  # re-packed


def test_hard_repair_drops_forbidden_kinds():
    qs = [_q("technical_scenario"), _q("experience_check"), _q("compliance_binary")]
    out = hard_repair(qs, stage_duration_minutes=20)
    assert all(q.question_kind not in ("experience_check", "compliance_binary") for q in out)


def test_hard_repair_trims_to_budget_keeps_mandatory():
    qs = [_q("technical_scenario", mins=8.0, pos=0, mand=True),
          _q("technical_scenario", mins=8.0, pos=1),
          _q("technical_scenario", mins=8.0, pos=2)]
    out = hard_repair(qs, stage_duration_minutes=20)
    assert sum(float(q.estimated_minutes) for q in out) <= 20
    assert any(q.is_mandatory for q in out)  # mandatory survived the trim


def test_hard_repair_idempotent_on_clean_bank():
    qs = [_q("technical_scenario", mins=4.0), _q("project_deepdive", mins=4.0)]
    out = hard_repair(qs, stage_duration_minutes=20)
    assert len(out) == 2
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -k hard_repair -q`
Expected: FAIL — `hard_repair` not defined.

- [ ] **Step 3: Implement `hard_repair` + helpers**

Append to `invariants.py`:
```python
def _cap_kind(
    questions: list[GeneratedQuestion], kind: str, n: int
) -> list[GeneratedQuestion]:
    idxs = [i for i, q in enumerate(questions) if q.question_kind == kind]
    if len(idxs) <= n:
        return questions
    # Keep `n`: mandatory first, then earliest position; drop the rest.
    keep = set(sorted(idxs, key=lambda i: (not questions[i].is_mandatory, questions[i].position))[:n])
    return [q for i, q in enumerate(questions) if q.question_kind != kind or i in keep]


def _trim_to_budget(
    questions: list[GeneratedQuestion], budget_minutes: int
) -> list[GeneratedQuestion]:
    qs = list(questions)
    while sum(float(q.estimated_minutes) for q in qs) > budget_minutes and len(qs) > 1:
        # Drop the last non-mandatory question (lowest priority); else the last one.
        drop = next((i for i in range(len(qs) - 1, -1, -1) if not qs[i].is_mandatory), len(qs) - 1)
        qs.pop(drop)
    return qs


def hard_repair(
    questions: list[GeneratedQuestion], *, stage_duration_minutes: int
) -> list[GeneratedQuestion]:
    """Unconditionally enforce the HARD AI-screen invariants (idempotent on a clean bank):
    drop forbidden kinds, cap project_deepdive/behavioral to one, trim to budget. Re-packs
    positions 0..N-1. Does NOT touch the (non-repairable) uncovered-skill case. Pure."""
    qs = [q for q in questions if q.question_kind not in _FORBIDDEN_KINDS]
    qs = _cap_kind(qs, "project_deepdive", _MAX_PROJECT_DEEPDIVE)
    qs = _cap_kind(qs, "behavioral", _MAX_BEHAVIORAL)
    qs = _trim_to_budget(qs, stage_duration_minutes)
    for i, q in enumerate(qs):
        q.position = i
    return qs
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -q`
Expected: PASS (all check + hard_repair tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/invariants.py backend/nexus/tests/question_bank/test_invariants.py
git commit -m "feat(question_bank): hard_repair — deterministic enforcement of AI-screen invariants"
```

---

## Task 4: `run_bank_critic` — `violations` re-pass param

**Files:**
- Modify: `app/modules/question_bank/critic.py` (`_build_critic_user_message` + `run_bank_critic`).
- Test: `tests/question_bank/test_bank_critic.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/question_bank/test_bank_critic.py`:
```python
def test_build_critic_message_includes_violations():
    from app.modules.question_bank.critic import _build_critic_user_message
    msg = _build_critic_user_message(
        draft=[_q()], seniority="mid", role_title="X", signals=[],
        stage_difficulty="hard", stage_duration=20,
        violations=["There are 2 project_deepdive questions; reduce to one."],
    )
    assert "MUST FIX" in msg
    assert "2 project_deepdive" in msg


def test_build_critic_message_no_violations_section_when_none():
    from app.modules.question_bank.critic import _build_critic_user_message
    msg = _build_critic_user_message(
        draft=[_q()], seniority="mid", role_title="X", signals=[],
        stage_difficulty="hard", stage_duration=20, violations=None,
    )
    assert "MUST FIX" not in msg
```
(`_q` is the helper already defined at the top of `test_bank_critic.py`.)

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_bank_critic.py -k violations -q`
Expected: FAIL — `_build_critic_user_message` doesn't accept `violations`.

- [ ] **Step 3: Add the `violations` param**

In `critic.py`, change `_build_critic_user_message` to accept `violations: list[str] | None = None` and append a section when present (before the final "Now return..." line):
```python
def _build_critic_user_message(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
    violations: list[str] | None = None,
) -> str:
    parts: list[str] = []
    # ... existing role / signals / draft sections unchanged ...
    if violations:
        parts.append("\n# YOU MUST FIX THESE SPECIFIC VIOLATIONS\n")
        parts.append(
            "A deterministic check found these. Do NOT claim they are already fixed — fix them:\n"
        )
        for v in violations:
            parts.append(f"  - {v}\n")
    parts.append("\n\nNow return a BankCritiqueOutput with the corrected bank.\n")
    return "".join(parts)
```
And thread it through `run_bank_critic` (add `violations: list[str] | None = None` to its signature and pass it to `_build_critic_user_message`):
```python
async def run_bank_critic(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
    violations: list[str] | None = None,
) -> tuple[list[GeneratedQuestion], str]:
    ...
    user_message = _build_critic_user_message(
        draft=draft, seniority=seniority, role_title=role_title, signals=signals,
        stage_difficulty=stage_difficulty, stage_duration=stage_duration, violations=violations,
    )
    ...
```
(Read the current `_build_critic_user_message` body first and insert the violations section at the right place — keep all existing sections. The existing "Now return..." trailing line should be emitted once; if the current code already appends it, move the violations section to just before it.)

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_bank_critic.py -q`
Expected: PASS (existing critic tests + the 2 new ones).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/critic.py backend/nexus/tests/question_bank/test_bank_critic.py
git commit -m "feat(question_bank): run_bank_critic accepts targeted violations for a re-pass"
```

---

## Task 5: Actor wiring — gate → re-pass → hard_repair

**Files:**
- Modify: `app/modules/question_bank/actors.py` (`_generate_one_bank` Phase B3, after `run_bank_critic`).
- Test: `tests/test_question_banks_actors.py` (extend)

- [ ] **Step 1: Write the failing test**

In `tests/test_question_banks_actors.py`, add a DB-backed test using the existing harness: monkeypatch `actors._stream_bank_questions` to persist a draft, and `actors.run_bank_critic` to return TWO `project_deepdive` questions (and never fix them across calls). Run `_generate_one_bank` for an `ai_screening` stage. Assert the FINAL persisted bank has exactly ONE `project_deepdive` (hard_repair guaranteed it even though the critic kept two). Name it `test_gate_guarantees_one_project_deepdive`. Add a second test where the first-pass critic returns a CLEAN bank (1 deepdive, fits budget) → assert `run_bank_critic` was called exactly ONCE (no re-pass). (Use a call-counter on the `run_bank_critic` monkeypatch.)

- [ ] **Step 2: Run to verify it FAILS**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k "gate or project_deepdive" -q`
Expected: FAIL — the bank persists 2 deepdives (no gate yet).

- [ ] **Step 3: Wire the gate into Phase B3**

In `_generate_one_bank`, the Phase B3 block currently calls `run_bank_critic` to get `corrected` (or keeps the draft on critic failure) then wipes + re-persists `corrected`. Restructure so the gate runs on the critic output, does one re-pass on violations, and ALWAYS hard-repairs before persisting. Replace the "corrected → wipe+re-persist" section with:
```python
        from app.modules.question_bank.invariants import check_bank_invariants, hard_repair

        # working = critic output, or the streamed draft if the critic failed.
        working = corrected if corrected is not None else draft_questions
        gate_codes: list[str] = []
        violations = check_bank_invariants(
            working, stage_type=stage_type, stage_duration_minutes=stage_duration,
            signals=snapshot_signals,
        )
        if violations:
            gate_codes = [v.code for v in violations]
            # One targeted re-pass — only if the critic is available (it produced `corrected`).
            if corrected is not None:
                try:
                    working, _repass_note = await run_bank_critic(
                        draft=working, seniority=seniority, role_title=role_title,
                        signals=snapshot_signals, stage_difficulty=stage_difficulty,
                        stage_duration=stage_duration, bank_id=bank_id, tenant_id=tenant_id,
                        job_id=job_id, violations=[v.description for v in violations],
                    )
                except Exception as repass_exc:
                    logger.warning(
                        "question_bank.critic.repass_failed",
                        bank_id=str(bank_id), error_type=type(repass_exc).__name__,
                    )
        # ALWAYS hard-repair (idempotent) so the hard invariants are guaranteed even if the
        # critic/re-pass didn't fix them.
        working = hard_repair(working, stage_duration_minutes=stage_duration)

        if gate_codes:
            critique_note = (
                f"{critique_note} | gate: {', '.join(sorted(set(gate_codes)))} "
                "(re-pass + hard-repair applied)."
            )

        # Replace the draft with the final (critic + gate + repaired) bank.
        async with get_bypass_session() as wdb:
            await wdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            wbank = (
                await wdb.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
            ).scalar_one()
            await wipe_ai_questions(wdb, bank=wbank)
            for pos, q in enumerate(working):
                await persist_one_question(
                    wdb, bank=wbank, question=q, source="ai_generated",
                    position=pos, stage_difficulty=stage_difficulty,
                )
            await wdb.commit()
```
IMPORTANT: read the CURRENT B3 block first. It already has the `corrected`/`critique_note` from the critic call, the `try/except` fallback, the `if corrected is not None:` wipe+re-persist, `role_title`/`seniority` loaded, `snapshot_signals`/`stage_difficulty`/`stage_duration` captured, and `wipe_ai_questions`/`persist_one_question` imported. Splice the gate logic in so the SINGLE wipe+re-persist now persists `working` (not `corrected`). Ensure the previous unconditional `if corrected is not None:` re-persist block is REPLACED (not duplicated) — there must be exactly one wipe+re-persist of `working`, covering both the critic-success and critic-failure paths.

- [ ] **Step 4: Run to verify it PASSES**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k "gate or project_deepdive" -q`
Expected: PASS. Then the full actor suite: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py tests/question_bank -m "not prompt_quality" -q 2>&1 | tail -6`.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/question_bank/actors.py backend/nexus/tests/test_question_banks_actors.py
git commit -m "feat(question_bank): wire invariant gate + re-pass + hard_repair into generation"
```

---

## Task 6: Full-suite verification + live smoke

**Files:** none (verification).

- [ ] **Step 1: Backend gate**

Run: `docker compose run --rm nexus pytest tests/question_bank tests/test_question_banks_actors.py tests/test_question_banks_service.py tests/reporting -m "not prompt_quality" -q 2>&1 | tail -6`
Expected: 0 failed.

- [ ] **Step 2: Restart worker (no hot-reload)**

Run: `docker compose up -d --force-recreate nexus-worker`

- [ ] **Step 3: Live smoke (user-run)**

Regenerate the Workato bank (`/jobs/ce6dad9a…/questions?stage=2ea4f4a3…`). Confirm: it builds from the v2 signals (12, purpose-tagged); exactly ONE project_deepdive; the AI-driven/agent-workflow weight-3 skill is now tested by a scenario; ≤ 20 min; the `coverage_notes` shows any gate action.

---

## Self-Review (plan vs spec)

- **Spec §2 (re-pin to active snapshot, is_stale cleared, guard on no-confirmed)** → Task 1. ✓
- **Spec §3 (`check_bank_invariants` invariants table; `hard_repair`)** → Tasks 2, 3. ✓
- **Spec §4 (`run_bank_critic` violations param; actor wiring critic→gate→re-pass→gate→hard_repair; fallback runs gate too)** → Tasks 4, 5. ✓
- **Spec §6 (tests: invariants unit, re-pass message, actor gate-guarantee + skip-when-clean, re-pin)** → Tasks 1,2,3,4,5; live smoke Task 6. ✓
- **Spec §7 (pure module, reuse resolver, gate on fallback path, bounded calls)** → enforced Tasks 1–5. ✓

**Type consistency:** `Violation(code, description, hard_repairable)` defined Task 2, used Tasks 2/5. `check_bank_invariants(questions, *, stage_type, stage_duration_minutes, signals)` defined Task 2, called Task 5. `hard_repair(questions, *, stage_duration_minutes)` defined Task 3, called Task 5. `run_bank_critic(..., violations=...)` extended Task 4, called Task 5. `get_latest_confirmed_snapshot(db, job_id)` (existing) used Task 1. Note: `hard_repair` signature drops the spec's `violations` param (it enforces unconditionally/idempotently — a cleaner equivalent of "apply the hard-repairable fixes"); the actor still computes violations for the re-pass + audit note.
