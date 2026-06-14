"""No-stale-code guard: the session-clock / resolver-tier subsystem is gone.

Fails if any deleted clock/tier symbol reappears in the engine or runtime code.
"""
from __future__ import annotations

import pathlib

_ROOTS = [
    "app/modules/interview_engine",
    "app/modules/interview_runtime",
]
_FORBIDDEN = [
    "BudgetPhase", "BudgetConfig", "compute_budget_phase",
    "budget_config_from_ai_config", "budget_phase",
    "time_remaining_s", "_budget_cfg",
    "engine_close_reserve_s", "engine_winding_down_s",
    "QuestionTier", "questions_core_total", "questions_overflow_asked",
]


def test_no_clock_or_tier_symbols_remain():
    repo = pathlib.Path(__file__).resolve().parents[2]  # backend/nexus
    offenders: list[str] = []
    for root in _ROOTS:
        for py in (repo / root).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for sym in _FORBIDDEN:
                if sym in text:
                    offenders.append(f"{py.relative_to(repo)}: {sym}")
    assert not offenders, "stale clock/tier symbols:\n" + "\n".join(offenders)
