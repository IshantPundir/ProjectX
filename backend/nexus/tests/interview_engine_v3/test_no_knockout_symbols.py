"""No-stale-code guard: the verified-knockout feature is gone.

Only knockout *behavior* symbols are forbidden. The JD signal DATA attribute
`knockout` (SignalSpec.knockout, SignalEvidence.knockout, SignalMetadata.knockout,
jd schema, reporting must-have use) is intentionally KEPT and not checked here.
"""
from __future__ import annotations

import pathlib

_ROOTS = [
    "app/modules/interview_engine",
    "app/modules/interview_runtime",
    "app/modules/reporting",
    "app/modules/tenant_settings",
]
_FORBIDDEN = [
    "KnockoutOutcome", "knockout_close", "KnockoutFailure", "knockout_failures",
    "knockout_results", "gate_knockout", "KnockoutTracker", "KnockoutStep",
    "knockout_pending", "knockout_reflected", "knockout_confirmed",
    "confirmed_knockout_signals", "engine_knockout_policy", "KnockoutPolicy",
]


def test_no_knockout_behavior_symbols_remain():
    repo = pathlib.Path(__file__).resolve().parents[2]  # backend/nexus
    offenders: list[str] = []
    for root in _ROOTS:
        for py in (repo / root).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for sym in _FORBIDDEN:
                if sym in text:
                    offenders.append(f"{py.relative_to(repo)}: {sym}")
    assert not offenders, "stale knockout-behavior symbols:\n" + "\n".join(offenders)


def test_signal_knockout_data_attribute_is_kept():
    # The DATA attribute survives — sanity-check one canonical home.
    from app.modules.interview_engine.contracts import SignalSpec
    assert "knockout" in SignalSpec.model_fields
