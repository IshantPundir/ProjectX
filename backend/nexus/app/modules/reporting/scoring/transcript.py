"""Envelope-driven segmentation for the post-session report scorer.

The v2 engine writes ``sessions.transcript`` with ``question_id = null`` on
every turn — transcript question_id tagging was never wired.  This module
therefore drives segmentation exclusively from the **audit envelope** (the
engine's authoritative per-turn record), which *does* carry the structure
needed to reconstruct per-question answer buckets.

Design — dual-mode
------------------
``segment`` detects which mode to use by inspecting the ``turn.decision``
events in the envelope:

* **Logged-id mode** (preferred): if ANY ``turn.decision`` event has a
  non-null ``active_question_id`` in its payload, the function maps each
  answer directly to the bank question named by that field — no ordering
  assumptions required.  This is robust to non-sequential advancement (the
  brain can SKIP optional questions).

* **Pointer mode** (fallback): for sessions recorded before the
  ``active_question_id`` field was added (e.g. ``e4072361``), the original
  ordered-pointer walk is preserved unchanged.

Pointer-mode walk (unchanged from original):
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

    Dual-mode: prefers logged-id mode when any ``turn.decision`` event carries
    a non-null ``active_question_id``; falls back to the pointer walk for older
    sessions that lack the field.

    Parameters
    ----------
    envelope:
        The session audit envelope dict with an ``events`` key whose value is a
        time-ordered list of event dicts.  Relevant event kinds:

        * ``directive.delivered`` — ``payload`` carries ``act`` (INTRO / ASK /
          ACK_ADVANCE / PROBE / CLARIFY / REPEAT / CLOSE).
        * ``turn.decision`` — ``payload`` carries ``candidate_quote``,
          ``turn_ref``, ``grade``, ``attributed_signals``, and (in new sessions)
          ``active_question_id``.
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
        One entry per question that received ≥ 1 candidate answer.

        * Logged-id mode: in the order questions were first answered (which may
          differ from bank order when the engine skips optional questions).
        * Pointer mode: in mandatory-first, then position order.
    """
    # Resolve questions: explicit arg wins over legacy bank_questions alias.
    resolved_questions: list[dict[str, Any]] = (
        questions if questions is not None else (bank_questions or [])
    )

    events: list[dict[str, Any]] = envelope.get("events") or []

    if not resolved_questions:
        return []

    # ------------------------------------------------------------------
    # Mode detection: if ANY turn.decision has a non-null active_question_id,
    # use logged-id mode; otherwise fall through to the pointer walk.
    # ------------------------------------------------------------------
    use_logged_id = any(
        (e.get("payload") or {}).get("active_question_id") is not None
        for e in events
        if e.get("kind") == "turn.decision"
    )

    # ------------------------------------------------------------------
    # Step 1 — Triage kind lookup (shared by both modes).
    # ``engine.v2.triage.decision`` fires once per candidate turn (same
    # turn_ref as the turn.decision for that turn).
    # ------------------------------------------------------------------
    triage_kind_by_turn: dict[str, str] = {}
    for e in events:
        if e.get("kind") == "engine.v2.triage.decision":
            payload = e.get("payload") or {}
            tr = payload.get("turn_ref")
            kd = payload.get("kind")
            if tr is not None and kd is not None:
                triage_kind_by_turn[tr] = kd

    if use_logged_id:
        return _segment_logged_id(
            events=events,
            resolved_questions=resolved_questions,
            triage_kind_by_turn=triage_kind_by_turn,
        )
    return _segment_pointer(
        events=events,
        resolved_questions=resolved_questions,
        triage_kind_by_turn=triage_kind_by_turn,
    )


# ---------------------------------------------------------------------------
# Logged-id mode
# ---------------------------------------------------------------------------

def _segment_logged_id(
    *,
    events: list[dict[str, Any]],
    resolved_questions: list[dict[str, Any]],
    triage_kind_by_turn: dict[str, str],
) -> list[ScoredUnit]:
    """Segment using the brain's logged ``active_question_id`` as the ground truth.

    Maps each ``turn.decision`` to the bank question named by its
    ``active_question_id`` field.  PROBE/CLARIFY/REPEAT directives are also
    mapped via their ``turn_ref`` → the ``active_question_id`` of the
    ``turn.decision`` with the same ``turn_ref``.

    Robust to non-sequential advancement: the pointer walk would misattribute
    answers when the brain SKIPs covered optional questions.
    """
    # Build a lookup: question id → question dict.
    q_by_id: dict[str, dict[str, Any]] = {q["id"]: q for q in resolved_questions}

    # First pass: map turn_ref → active_question_id from turn.decision events.
    aqid_by_turn_ref: dict[str, str] = {}
    for e in events:
        if e.get("kind") == "turn.decision":
            payload = e.get("payload") or {}
            tr = payload.get("turn_ref")
            aqid = payload.get("active_question_id")
            if tr is not None and aqid is not None:
                aqid_by_turn_ref[tr] = aqid

    # Per-question accumulators keyed by question id.
    answer_parts: dict[str, list[str]] = {}
    probes_fired: dict[str, int] = {}
    clarifies: dict[str, int] = {}
    first_ts: dict[str, int] = {}
    per_q_turn_refs: dict[str, list[str]] = {}
    # Track insertion order (first-answered order) for emit ordering.
    first_answered_order: list[str] = []

    def _ensure(qid: str) -> None:
        if qid not in answer_parts:
            answer_parts[qid] = []
            probes_fired[qid] = 0
            clarifies[qid] = 0
            per_q_turn_refs[qid] = []

    for e in events:
        kind = e.get("kind")
        payload: dict[str, Any] = e.get("payload") or {}
        t_ms: int = int(e.get("t_ms", 0))

        if kind == "turn.decision":
            tr = payload.get("turn_ref")
            aqid = payload.get("active_question_id")
            if aqid is None or aqid not in q_by_id:
                continue  # logged id unknown — skip (shouldn't happen in well-formed sessions)
            _ensure(aqid)
            quote: str = (payload.get("candidate_quote") or "").strip()
            if quote:
                answer_parts[aqid].append(quote)
            if aqid not in first_ts:
                first_ts[aqid] = t_ms
            if aqid not in [x for x in first_answered_order]:
                first_answered_order.append(aqid)
            if tr is not None:
                per_q_turn_refs[aqid].append(tr)

        elif kind == "directive.delivered":
            act: str = payload.get("act", "")
            tr = payload.get("turn_ref")
            # Resolve the question this directive is associated with via its turn_ref.
            aqid = aqid_by_turn_ref.get(tr or "") if tr else None
            if aqid is None or aqid not in q_by_id:
                continue
            _ensure(aqid)
            if act == _PROBE_ACT:
                probes_fired[aqid] += 1
            elif act in _CLARIFY_ACTS:
                clarifies[aqid] += 1
            # ASK / ACK_ADVANCE / INTRO / CLOSE / other → no-op in logged-id mode

    # Engagement per question (same logic as pointer mode).
    def _engaged(qid: str) -> bool:
        refs = per_q_turn_refs.get(qid, [])
        if not refs:
            return True
        any_engaged = False
        any_triage = False
        for ref in refs:
            tk = triage_kind_by_turn.get(ref)
            if tk is not None:
                any_triage = True
                if tk not in _NOT_ENGAGED_KINDS:
                    any_engaged = True
                    break
        return any_engaged if any_triage else True

    # Emit one ScoredUnit per answered question in first-answered order.
    units: list[ScoredUnit] = []
    for qid in first_answered_order:
        q = q_by_id[qid]
        parts = answer_parts.get(qid, [])
        candidate_answer = " ".join(parts).strip()
        wc = len(candidate_answer.split()) if candidate_answer else 0
        units.append(
            ScoredUnit(
                question_id=qid,
                question_text=q.get("text", ""),
                candidate_answer=candidate_answer,
                answer_start_ms=first_ts.get(qid, 0),
                probes_fired=probes_fired.get(qid, 0),
                clarifies=clarifies.get(qid, 0),
                word_count=wc,
                candidate_engaged=_engaged(qid),
                question_kind=q.get("question_kind"),
            )
        )
    return units


# ---------------------------------------------------------------------------
# Pointer mode (original implementation, preserved exactly)
# ---------------------------------------------------------------------------

def _segment_pointer(
    *,
    events: list[dict[str, Any]],
    resolved_questions: list[dict[str, Any]],
    triage_kind_by_turn: dict[str, str],
) -> list[ScoredUnit]:
    """Original pointer walk — used for sessions without logged active_question_id."""
    # ------------------------------------------------------------------
    # Order questions the way the engine asks them.
    # Mandatory-first, then ascending position.
    # ------------------------------------------------------------------
    ordered: list[dict[str, Any]] = sorted(
        resolved_questions,
        key=lambda q: (not q.get("is_mandatory", False), q.get("position", 0)),
    )

    # ------------------------------------------------------------------
    # Walk events in order, maintaining q_idx and per-question accumulators.
    # ------------------------------------------------------------------
    answer_parts: list[list[str]] = [[] for _ in ordered]
    probes_fired: list[int] = [0] * len(ordered)
    clarifies: list[int] = [0] * len(ordered)
    first_ts: list[int | None] = [None] * len(ordered)
    has_answer: list[bool] = [False] * len(ordered)
    per_q_turn_refs: list[list[str]] = [[] for _ in ordered]

    q_idx: int = -1  # -1 = before any question is on the floor

    for event in events:
        kind = event.get("kind")
        payload: dict[str, Any] = event.get("payload") or {}
        t_ms: int = int(event.get("t_ms", 0))

        if kind == "directive.delivered":
            act: str = payload.get("act", "")

            if act in _ADVANCE_ACTS:
                q_idx += 1

            elif act == _PROBE_ACT and 0 <= q_idx < len(ordered):
                probes_fired[q_idx] += 1

            elif act in _CLARIFY_ACTS and 0 <= q_idx < len(ordered):
                clarifies[q_idx] += 1

            # INTRO, CLOSE → no-op.

        elif kind == "turn.decision":
            if 0 <= q_idx < len(ordered):
                turn_ref: str | None = payload.get("turn_ref")
                quote: str = (payload.get("candidate_quote") or "").strip()

                if quote:
                    answer_parts[q_idx].append(quote)

                if first_ts[q_idx] is None:
                    first_ts[q_idx] = t_ms

                has_answer[q_idx] = True

                if turn_ref is not None:
                    per_q_turn_refs[q_idx].append(turn_ref)

    # ------------------------------------------------------------------
    # Engagement per question.
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
                    break

        engaged.append(any_engaged if any_triage else True)

    # ------------------------------------------------------------------
    # Emit one ScoredUnit per question that received ≥1 answer.
    # ------------------------------------------------------------------
    units: list[ScoredUnit] = []

    for idx, q in enumerate(ordered):
        if not has_answer[idx]:
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
