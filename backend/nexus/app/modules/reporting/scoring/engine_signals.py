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
# NOTE: `exceeded` is assigned only by the post-interview LLM re-check (Task 6),
# never emitted by the engine's coverage_summary (which is sufficient/partial/failed/none).


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
    is the most-recent signal marked `failed` (its own attributed_signals first,
    else the last failed coverage_delta). The evidence quote is the candidate's
    answer on the turn that FAILED the signal — NOT the close event's own quote,
    which is frequently a filler/backchannel ("Hello?") rather than the disclaimer.

    Only the first knockout_close is meaningful; the engine terminates after it.
    """
    events: list[dict] = envelope.get("events") or []
    last_failed: str | None = None
    failed_quote_by_signal: dict[str, str] = {}
    for e in events:
        if e.get("kind") != "turn.decision":
            continue
        p = e.get("payload") or {}
        quote = (p.get("candidate_quote") or "").strip()
        for sig, st in (p.get("coverage_delta") or {}).items():
            if st == "failed":
                last_failed = sig
                if quote:
                    failed_quote_by_signal[sig] = quote
        if p.get("move") == "knockout_close":
            attributed = p.get("attributed_signals") or []
            trigger = attributed[0] if attributed else last_failed
            evidence_quote = (failed_quote_by_signal.get(trigger or "") or quote)
            reason = (
                f"Interview closed on a must-have gap: '{trigger}'."
                if trigger else "Interview closed early on a knockout."
            )
            return KnockoutClose(signal=trigger, quote=evidence_quote, reason=reason)
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
