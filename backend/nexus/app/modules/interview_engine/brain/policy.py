"""Deterministic policy gates over a BrainDecision (pure — no livekit, no LLM).

Defense-in-depth (doc 05 / DESIGN-SPEC §12): the LLM proposes, the policy disposes. Every gate
either passes (recorded in `checks`) or fires (recorded in `violations`) and DOWNGRADES the move
to a safe non-terminal alternative — it NEVER crashes the session and NEVER auto-rejects
(borderline → human). The headline gate is the b99d8cc6 fix: knockout_close requires ALL
OR-alternatives checked AND reflect-to-confirm. The no-leak pre-check scans composed_say BEFORE
the Directive is constructed (the Directive ctor also validates — belt + suspenders) so a leak
downgrades cleanly instead of raising RubricLeakError mid-turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.interview_engine.brain.decision import BrainDecision, BrainMove
from app.modules.interview_engine.directive import FORBIDDEN_RUBRIC_TOKENS


@dataclass
class PolicyResult:
    ok: bool
    effective_move: BrainMove          # the move to actually execute (possibly downgraded)
    sanitized_say: str | None   # composed_say after no-leak scrub (None if leaked or was None)
    sanitized_setup: str | None = None   # spoken_setup after no-leak scrub (None if leaked/absent)
    checks: list[str] = field(default_factory=list)       # gates that passed
    violations: list[str] = field(default_factory=list)   # gates that fired (→ downgrade)


def _leaks(text: str | None) -> bool:
    if not text:
        return False
    hay = text.lower()
    return any(tok in hay for tok in FORBIDDEN_RUBRIC_TOKENS)


def evaluate_policy(decision: BrainDecision) -> PolicyResult:
    """Run every gate; return the effective (possibly downgraded) move + scrubbed say."""
    checks: list[str] = []
    violations: list[str] = []
    move = decision.move
    say = decision.composed_say

    # --- Gate 1: verified knockout (the b99d8cc6 guard) -------------------
    if move is BrainMove.knockout_close:
        if len(decision.or_alternatives) > 1 and not decision.or_alternatives_checked:
            violations.append("knockout_or_unverified")
            move = BrainMove.probe                       # keep probing the untested alternatives
        elif not decision.reflect_confirmed:
            violations.append("knockout_unconfirmed")
            move = BrainMove.confirm                     # reflect-to-confirm before any close
        else:
            checks.append("knockout_or_verified")

    # --- Gate 2: grade↔move coherence (doc 09 §2) -------------------------
    # Never probe-for-more when the targeted signal is already graded sufficient/strong.
    if move is BrainMove.probe:
        target = decision.target_signal
        sufficient = target is not None and decision.coverage_map().get(target) == "sufficient"
        if decision.move is BrainMove.probe and decision.grade is None:
            # Nothing to probe: grade=null means this turn had no gradeable answer (a clarification
            # request / meta turn). Probing a non-answer fires a HARDER question at a candidate who
            # asked for help (fe3a5434 t-6 -> candidate quit). Clarify/answer instead of probing.
            # Gated on the BRAIN's original move so a Gate-1 knockout->probe (keep testing the
            # OR-alternatives) is exempt — that probe legitimately has no answer to grade.
            violations.append("probe_without_answer")
            move = BrainMove.clarify
        elif decision.grade == "strong" or sufficient:
            violations.append("incoherent_probe_on_sufficient")
            move = BrainMove.advance
        else:
            checks.append("coherent_probe")

    # --- Gate 3: no-leak pre-check on composed text -----------------------
    if _leaks(say):
        violations.append("no_leak")
        say = None                           # drop leaking text; mouth composes from the act prompt
    elif say is not None:
        checks.append("no_leak_ok")

    sanitized = say

    # --- Gate 3b: no-leak on the optional orienting setup ---
    setup = decision.spoken_setup
    if _leaks(setup):
        violations.append("setup_leak")
        setup = None

    # Always record at least one gate so callers can assert res.checks is non-empty.
    if not checks and not violations:
        checks.append("clean_pass")

    return PolicyResult(
        ok=not violations,
        effective_move=move,
        sanitized_say=sanitized,
        sanitized_setup=setup,
        checks=checks,
        violations=violations,
    )
