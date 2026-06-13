"""Pure, deterministic invariant checks for an AI-screening bank.

The LLM critic is unreliable at COUNTABLE invariants (it falsely claims compliance), so the
guarantee lives here in code. check_bank_invariants reports violations (for the critic re-pass
+ audit log); hard_repair unconditionally enforces the hard invariants. Both are pure (no DB,
no LLM) and operate on GeneratedQuestion objects.

Coverage is keyed on primary_signal (the SCORED denominator the report grades — see
coverage_planner.py), NOT signal_values (live-only). A must-have skill that is merely bundled
as a secondary is still 'uncovered' here, because it cannot register as a gap in the report.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.question_bank.coverage_planner import CoveragePlan
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
    plan: CoveragePlan | None,
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

    # Scored-coverage check: every must-cover skill the planner assigned a scored slot must
    # be SOME question's primary_signal. Not hard_repairable — code can't author a scenario,
    # so a miss drives the targeted critic re-pass.
    if plan is not None:
        covered = {q.primary_signal for q in questions}
        for sig in plan.required_primaries:
            if sig not in covered:
                out.append(Violation(
                    "uncovered_required_primary",
                    f"The must-have skill '{sig}' has no scored question — it must be some "
                    "question's primary_signal (a bundled secondary does NOT count, the report "
                    f"only grades primary_signal). Add or repurpose a technical_scenario whose "
                    f"primary_signal is exactly '{sig}'.",
                    False,
                ))
    return out


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
    questions: list[GeneratedQuestion],
    budget_minutes: int,
    required_primaries: set[str],
) -> list[GeneratedQuestion]:
    """Drop lowest-priority questions until within budget — coverage-aware.

    Never drops a question that is the SOLE primary cover of a required_primary. Drops
    non-mandatory, non-protected questions from the end first (optional padding / a redundant
    2nd question on an already-covered competency). Priority: drop non-required-primary
    questions before redundantly-covered required-primary ones. If only mandatory/protected
    questions remain over budget, stops (a must-cover is never sacrificed for the time budget
    — the planner already reconciled the must-cover set against the slot budget upstream).
    """
    qs = list(questions)

    def _is_sole_required_cover(idx: int) -> bool:
        sig = qs[idx].primary_signal
        if sig not in required_primaries:
            return False
        return sum(1 for q in qs if q.primary_signal == sig) == 1

    def _is_required_primary(idx: int) -> bool:
        return qs[idx].primary_signal in required_primaries

    while sum(float(q.estimated_minutes) for q in qs) > budget_minutes and len(qs) > 1:
        drop = None
        # Pass 1: prefer dropping non-mandatory questions whose primary_signal is NOT in
        # required_primaries (pure optional padding — safest drop).
        for i in range(len(qs) - 1, -1, -1):
            if not qs[i].is_mandatory and not _is_required_primary(i):
                drop = i
                break
        # Pass 2: fall back to non-mandatory redundant required-primary questions (already
        # covered by another question in the bank).
        if drop is None:
            for i in range(len(qs) - 1, -1, -1):
                if not qs[i].is_mandatory and not _is_sole_required_cover(i):
                    drop = i
                    break
        if drop is None:
            break  # nothing droppable without sacrificing a mandatory/must-cover
        qs.pop(drop)
    return qs


def hard_repair(
    questions: list[GeneratedQuestion],
    *,
    stage_type: str,
    stage_duration_minutes: int,
    required_primaries: set[str] | None = None,
) -> list[GeneratedQuestion]:
    """Unconditionally enforce the HARD AI-screen invariants (idempotent on a clean bank):
    drop forbidden kinds, cap project_deepdive/behavioral to one, coverage-aware trim to
    budget. Re-packs positions 0..N-1. Returns the questions UNCHANGED for non-ai_screening
    stages (their rules differ — e.g. phone_screen legitimately uses experience_check/
    compliance_binary). Pure."""
    if stage_type != "ai_screening":
        return questions
    qs = [q for q in questions if q.question_kind not in _FORBIDDEN_KINDS]
    qs = _cap_kind(qs, "project_deepdive", _MAX_PROJECT_DEEPDIVE)
    qs = _cap_kind(qs, "behavioral", _MAX_BEHAVIORAL)
    qs = _trim_to_budget(qs, stage_duration_minutes, required_primaries or set())
    for i, q in enumerate(qs):
        q.position = i
    return qs
