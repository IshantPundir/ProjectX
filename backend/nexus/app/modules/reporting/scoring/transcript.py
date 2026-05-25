"""Envelope-driven segmentation for the post-session report scorer.

The v2 engine writes ``sessions.transcript`` with ``question_id = null`` on
every turn — transcript question_id tagging was never wired.  This module
therefore drives segmentation exclusively from the **audit envelope** (the
engine's authoritative per-turn record), which *does* carry the structure
needed to reconstruct per-question answer buckets.

Design
------
``segment`` replaces the old transcript-question_id approach with an
envelope-pointer walk:

1. Order the bank questions the way the engine asks them (mandatory-first,
   then by ``position``).
2. Walk ``events`` in chronological order maintaining ``q_idx`` (an index into
   the ordered question list):

   * ``directive.delivered`` with act ``ASK`` or ``ACK_ADVANCE`` → advance
     ``q_idx`` to the next question.
   * ``directive.delivered`` with act ``PROBE`` → increment the current
     question's ``probes_fired``.
   * ``directive.delivered`` with act ``CLARIFY`` or ``REPEAT`` → increment
     the current question's ``clarifies``.
   * ``turn.decision`` (a candidate answer) → attribute the ``candidate_quote``
     and triage kind to ``ordered[q_idx]``.
   * ``INTRO`` / ``CLOSE`` / all other event kinds → ignored.

3. Emit one ``ScoredUnit`` per question that received ≥1 ``turn.decision``
   event (question reached *and* answered), in ask order.  Questions never
   reached produce no unit; their signals stay ``not_assessed``, which is
   correct.

This module is deliberately **pure** (no I/O, no async).  All data is passed
as plain dicts so it can be called from tests without any database plumbing.
"""
from __future__ import annotations

from typing import Any

from app.modules.reporting.scoring.types import ScoredUnit

# ---------------------------------------------------------------------------
# Directive act constants
# ---------------------------------------------------------------------------
_ADVANCE_ACTS: frozenset[str] = frozenset({"ASK", "ACK_ADVANCE"})
_PROBE_ACT = "PROBE"
_CLARIFY_ACTS: frozenset[str] = frozenset({"CLARIFY", "REPEAT"})

# ---------------------------------------------------------------------------
# Triage kinds that indicate the candidate was NOT engaged with the question.
# ---------------------------------------------------------------------------
_NOT_ENGAGED_KINDS: frozenset[str] = frozenset(
    {"no_experience", "off_topic", "backchannel", "injection", "indirect_no"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def segment(
    *,
    envelope: dict[str, Any],
    questions: list[dict[str, Any]],
    # Legacy compat: transcript is still accepted (used by build_report for the
    # communication dimension) but is no longer used for question mapping.
    transcript: list[dict[str, Any]] | None = None,
    # Historical alias kept for callers that used bank_questions= kwarg.
    bank_questions: list[dict[str, Any]] | None = None,
) -> list[ScoredUnit]:
    """Return one :class:`ScoredUnit` per delivered question, in ask order.

    Parameters
    ----------
    envelope:
        The session audit envelope dict with an ``events`` key whose value is a
        time-ordered list of event dicts.  Relevant event kinds:

        * ``directive.delivered`` — ``payload`` carries ``act`` (INTRO / ASK /
          ACK_ADVANCE / PROBE / CLARIFY / REPEAT / CLOSE).
        * ``turn.decision`` — ``payload`` carries ``candidate_quote``,
          ``turn_ref``, ``grade``, ``attributed_signals``.
        * ``engine.v2.triage.decision`` — ``payload`` carries ``turn_ref`` and
          ``kind`` (answering / no_experience / off_topic / …).

    questions:
        The ordered list of question dicts from the question bank.  Each entry
        must have at minimum ``id`` (str), ``text`` (str), ``is_mandatory``
        (bool), and ``position`` (int).

    transcript:
        Accepted for backward compatibility and still used by ``build_report``
        to build the full-transcript text for the communication dimension.
        **Not used for question mapping.**

    bank_questions:
        Legacy alias for ``questions``; ignored when ``questions`` is provided.

    Returns
    -------
    list[ScoredUnit]
        One entry per question that received ≥ 1 candidate answer, in the order
        the engine asked them (mandatory-first, then by ``position``).
    """
    # Resolve questions: explicit arg wins over legacy bank_questions alias.
    resolved_questions: list[dict[str, Any]] = (
        questions if questions is not None else (bank_questions or [])
    )

    events: list[dict[str, Any]] = envelope.get("events") or []

    # ------------------------------------------------------------------
    # Step 1 — Order questions the way the engine asks them.
    # Mandatory-first, then ascending position.
    # This mirrors the engine's bank-advancement order exactly.
    # ------------------------------------------------------------------
    ordered: list[dict[str, Any]] = sorted(
        resolved_questions,
        key=lambda q: (not q.get("is_mandatory", False), q.get("position", 0)),
    )

    if not ordered:
        return []

    # ------------------------------------------------------------------
    # Step 2 — Build per-turn triage kind lookup.
    # ``engine.v2.triage.decision`` fires once per candidate turn (same
    # turn_ref as the turn.decision for that turn).
    # ------------------------------------------------------------------
    triage_kind_by_turn: dict[str, str] = {}
    for e in events:
        if e.get("kind") == "engine.v2.triage.decision":
            payload = e.get("payload") or {}
            turn_ref = payload.get("turn_ref")
            kind = payload.get("kind")
            if turn_ref is not None and kind is not None:
                triage_kind_by_turn[turn_ref] = kind

    # ------------------------------------------------------------------
    # Step 3 — Walk events in order, maintaining q_idx and per-question
    # accumulators.
    #
    # Engagement is computed via a separate post-walk pass (Step 3b) to
    # avoid the complexity of distinguishing "no triage seen yet" from
    # "triage confirms engaging" in a single pass.
    # ------------------------------------------------------------------
    # Per-question accumulators (indexed by position in ``ordered``).
    answer_parts: list[list[str]] = [[] for _ in ordered]
    probes_fired: list[int] = [0] * len(ordered)
    clarifies: list[int] = [0] * len(ordered)
    first_ts: list[int | None] = [None] * len(ordered)
    has_answer: list[bool] = [False] * len(ordered)
    # Per-question: turn_refs seen (for engagement computation).
    per_q_turn_refs: list[list[str]] = [[] for _ in ordered]

    q_idx: int = -1  # -1 = before any question is on the floor

    for event in events:
        kind = event.get("kind")
        payload: dict[str, Any] = event.get("payload") or {}
        t_ms: int = int(event.get("t_ms", 0))

        if kind == "directive.delivered":
            act: str = payload.get("act", "")

            if act in _ADVANCE_ACTS:
                # Advance to the next question in the ordered list.
                # Guard: more advances than questions (e.g. a trailing
                # ACK_ADVANCE after the last question) → clamp at end.
                q_idx += 1

            elif act == _PROBE_ACT and 0 <= q_idx < len(ordered):
                probes_fired[q_idx] += 1

            elif act in _CLARIFY_ACTS and 0 <= q_idx < len(ordered):
                clarifies[q_idx] += 1

            # INTRO, CLOSE → no-op.

        elif kind == "turn.decision":
            # A graded candidate turn.  Attribute to the current question.
            if 0 <= q_idx < len(ordered):
                turn_ref: str | None = payload.get("turn_ref")
                quote: str = (payload.get("candidate_quote") or "").strip()

                if quote:
                    answer_parts[q_idx].append(quote)

                # Track timestamp from the event (first answer for this question).
                if first_ts[q_idx] is None:
                    first_ts[q_idx] = t_ms

                has_answer[q_idx] = True

                if turn_ref is not None:
                    per_q_turn_refs[q_idx].append(turn_ref)

    # ------------------------------------------------------------------
    # Step 3b — Compute engagement per question via a clean post-walk.
    #
    # engaged = True if ANY candidate turn for this question has a triage
    # kind NOT in _NOT_ENGAGED_KINDS.  If there is no triage signal at
    # all, default to True (candidate said something, no evidence otherwise).
    # ------------------------------------------------------------------
    engaged: list[bool] = []
    for idx in range(len(ordered)):
        refs = per_q_turn_refs[idx]
        if not refs:
            engaged.append(True)
            continue

        any_engaged = False
        any_triage = False
        for ref in refs:
            tk = triage_kind_by_turn.get(ref)
            if tk is not None:
                any_triage = True
                if tk not in _NOT_ENGAGED_KINDS:
                    any_engaged = True
                    break  # short-circuit — one engaging turn is enough

        # No triage signal for any turn → default True.
        engaged.append(any_engaged if any_triage else True)

    # ------------------------------------------------------------------
    # Step 4 — Emit one ScoredUnit per question that received ≥1 answer.
    # ------------------------------------------------------------------
    units: list[ScoredUnit] = []

    for idx, q in enumerate(ordered):
        if not has_answer[idx]:
            # Question never reached / never answered → no unit.
            continue

        candidate_answer = " ".join(answer_parts[idx]).strip()
        wc = len(candidate_answer.split()) if candidate_answer else 0

        units.append(
            ScoredUnit(
                question_id=q["id"],
                question_text=q.get("text", ""),
                candidate_answer=candidate_answer,
                answer_start_ms=first_ts[idx] or 0,
                probes_fired=probes_fired[idx],
                clarifies=clarifies[idx],
                word_count=wc,
                candidate_engaged=engaged[idx],
                question_kind=q.get("question_kind"),
            )
        )

    return units
