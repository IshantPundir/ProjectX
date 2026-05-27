"""Parse the interview engine's persisted outputs for the report scorer (pure).

Inputs:
- coverage_summary: dict[signal -> sufficient|partial|failed|none] (sessions.raw_result_json)
- audit envelope: {"events": [...]} with turn.decision / directive.delivered / triage events
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.reporting.scoring.types import CovState, SignalDef, SignalTurn

_VALID_STATES: frozenset[str] = frozenset({"exceeded", "sufficient", "partial", "failed", "none"})


def build_engine_states(
    coverage_summary: dict[str, str], signals: list[SignalDef]
) -> dict[str, CovState]:
    """Project the engine coverage map onto the role's signals; default `none`."""
    states: dict[str, CovState] = {}
    for sig in signals:
        raw = coverage_summary.get(sig.value, "none")
        states[sig.value] = raw if raw in _VALID_STATES else "none"  # type: ignore[assignment]
    return states


@dataclass(frozen=True)
class KnockoutClose:
    signal: str | None       # the must-have the candidate failed/disclaimed
    quote: str
    reason: str


def detect_knockout_close(envelope: dict[str, Any]) -> KnockoutClose | None:
    """Return a KnockoutClose if the engine ended on a knockout, else None.

    Trigger: a turn.decision with move == 'knockout_close'. The triggering signal
    is the most-recent signal marked `failed` in a turn.decision at/ before that
    close (its own attributed_signals first, else the last failed coverage_delta).
    """
    events: list[dict] = envelope.get("events") or []
    last_failed: str | None = None
    for e in events:
        if e.get("kind") != "turn.decision":
            continue
        p = e.get("payload") or {}
        for sig, st in (p.get("coverage_delta") or {}).items():
            if st == "failed":
                last_failed = sig
        if p.get("move") == "knockout_close":
            trigger = None
            attributed = p.get("attributed_signals") or []
            if attributed:
                trigger = attributed[0]
            trigger = trigger or last_failed
            quote = (p.get("candidate_quote") or "").strip()
            reason = (
                f"Interview closed on a must-have gap: '{trigger}'."
                if trigger else "Interview closed early on a knockout."
            )
            return KnockoutClose(signal=trigger, quote=quote, reason=reason)
    return None


def collect_signal_evidence(envelope: dict[str, Any], signal: str) -> list[SignalTurn]:
    """Every turn.decision that attributed evidence to `signal`, in order."""
    out: list[SignalTurn] = []
    for e in envelope.get("events") or []:
        if e.get("kind") != "turn.decision":
            continue
        p = e.get("payload") or {}
        touches = signal in (p.get("attributed_signals") or []) or signal in (
            p.get("coverage_delta") or {}
        )
        if not touches:
            continue
        out.append(SignalTurn(
            candidate_quote=(p.get("candidate_quote") or "").strip(),
            grade=p.get("grade"),
            reasoning=p.get("reasoning") or "",
            question_id=p.get("active_question_id"),
        ))
    return out
