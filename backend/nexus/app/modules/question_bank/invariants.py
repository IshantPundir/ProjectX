"""Pure, deterministic invariant checks for an AI-screening bank.

The LLM critic is unreliable at COUNTABLE invariants (it falsely claims compliance), so the
guarantee lives here in code. check_bank_invariants reports violations (for the critic re-pass
+ audit log); hard_repair (next task) unconditionally enforces the hard invariants. Both are
pure (no DB, no LLM) and operate on GeneratedQuestion objects.
"""
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
    questions: list[GeneratedQuestion], *, stage_type: str, stage_duration_minutes: int
) -> list[GeneratedQuestion]:
    """Unconditionally enforce the HARD AI-screen invariants (idempotent on a clean bank):
    drop forbidden kinds, cap project_deepdive/behavioral to one, trim to budget. Re-packs
    positions 0..N-1. Returns the questions UNCHANGED for non-ai_screening stages (their rules
    differ — e.g. phone_screen legitimately uses experience_check/compliance_binary). Pure."""
    if stage_type != "ai_screening":
        return questions
    qs = [q for q in questions if q.question_kind not in _FORBIDDEN_KINDS]
    qs = _cap_kind(qs, "project_deepdive", _MAX_PROJECT_DEEPDIVE)
    qs = _cap_kind(qs, "behavioral", _MAX_BEHAVIORAL)
    qs = _trim_to_budget(qs, stage_duration_minutes)
    for i, q in enumerate(qs):
        q.position = i
    return qs
