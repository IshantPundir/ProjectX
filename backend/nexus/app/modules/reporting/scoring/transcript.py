"""Transcript ↔ envelope segmentation.

Joins a frozen session transcript (agent turns + candidate turns) with the
audit envelope (per-turn triage decisions, directive acts, word-count captures)
to produce one :class:`ScoredUnit` per delivered question.

This module is deliberately pure (no I/O, no async).  All data is passed as
plain dicts so it can be called from tests without any database plumbing.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.modules.reporting.scoring.types import ScoredUnit

# ---------------------------------------------------------------------------
# Triage kinds that indicate the candidate was NOT engaged with the question.
# ---------------------------------------------------------------------------
_NOT_ENGAGED_KINDS: frozenset[str] = frozenset(
    {"no_experience", "off_topic", "backchannel", "injection", "indirect_no"}
)

# Directive acts that are PROBE / CLARIFY (count toward probes_fired / clarifies).
_PROBE_ACT = "PROBE"
_CLARIFY_ACT = "CLARIFY"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def segment(
    *,
    transcript: list[dict[str, Any]],
    envelope: dict[str, Any],
    bank_questions: list[dict[str, Any]] | None = None,  # reserved for future use
) -> list[ScoredUnit]:
    """Return one :class:`ScoredUnit` per delivered question, in question order.

    Parameters
    ----------
    transcript:
        List of turn dicts with keys ``role`` ("agent"|"candidate"), ``text``,
        ``timestamp_ms`` (int), and ``question_id`` (str | None).
    envelope:
        The session audit envelope dict with an ``events`` key whose value is a
        list of event dicts.  Relevant event kinds:

        * ``directive.delivered`` — carries ``act`` (INTRO/ASK/PROBE/ACK_ADVANCE/
          CLARIFY/REPEAT/CLOSE) and ``turn_ref``.
        * ``turn.captured`` — carries ``word_count``.
        * ``engine.v2.triage.decision`` — carries ``turn_ref`` and ``kind``
          (answering/no_experience/off_topic/backchannel/…).

    bank_questions:
        Not used in the current implementation; reserved so callers can pass
        question-bank metadata for future enrichment.

    Returns
    -------
    list[ScoredUnit]
        One entry per unique ``question_id``, in the order the questions first
        appear in the transcript.
    """
    events: list[dict[str, Any]] = envelope.get("events") or []

    # ------------------------------------------------------------------
    # Phase 1 — Build per-question buckets from the transcript.
    # ------------------------------------------------------------------
    # question_order: unique question_ids in first-appearance order
    question_order: list[str] = []
    # question_text: first agent-turn text that carried each question_id
    question_text: dict[str, str] = {}
    # candidate_turns: all candidate turn dicts per question_id (accumulated)
    candidate_turns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # answer_start_ms: timestamp_ms of the first candidate turn per question_id
    answer_start_ms: dict[str, int] = {}
    # agent turns indexed by position for later timestamp matching
    agent_turns: list[dict[str, Any]] = []

    # Track the current "open" question_id as we walk the transcript.
    current_qid: str | None = None

    for turn in transcript:
        role = turn.get("role", "")
        qid = turn.get("question_id") or None
        t_ms: int = int(turn.get("timestamp_ms", 0))

        if role == "agent":
            agent_turns.append(turn)
            if qid is not None:
                if qid not in question_text:
                    # First agent turn for this question — it is the question delivery.
                    question_order.append(qid)
                    question_text[qid] = turn.get("text", "")
                current_qid = qid

        elif role == "candidate":
            # Candidate turns are attributed to the most-recently-opened qid.
            # The transcript sometimes carries the qid directly on the candidate
            # turn; we prefer that over the inferred current_qid.
            effective_qid = qid if qid is not None else current_qid
            if effective_qid is not None:
                candidate_turns[effective_qid].append(turn)
                if effective_qid not in answer_start_ms:
                    answer_start_ms[effective_qid] = t_ms

    # ------------------------------------------------------------------
    # Phase 2 — Derive probe / clarify counts from the envelope.
    #
    # Strategy: match each directive.delivered event to the nearest agent
    # turn by timestamp (they are nearly simultaneous — within ~2 ms in
    # real sessions).  The matched agent turn's question_id tells us which
    # question the act belongs to.
    # ------------------------------------------------------------------
    probes_per_q: dict[str, int] = defaultdict(int)
    clarifies_per_q: dict[str, int] = defaultdict(int)

    if agent_turns:
        agent_ts: list[int] = [int(t.get("timestamp_ms", 0)) for t in agent_turns]

        for event in events:
            if event.get("kind") != "directive.delivered":
                continue
            payload = event.get("payload") or {}
            act: str = payload.get("act", "")
            if act not in (_PROBE_ACT, _CLARIFY_ACT):
                continue
            e_t_ms = int(event.get("t_ms", 0))
            # Find the agent turn closest in time to this directive.
            closest_idx = min(range(len(agent_ts)), key=lambda i: abs(agent_ts[i] - e_t_ms))
            matched_qid = agent_turns[closest_idx].get("question_id")
            if matched_qid is not None:
                if act == _PROBE_ACT:
                    probes_per_q[matched_qid] += 1
                else:
                    clarifies_per_q[matched_qid] += 1

    # ------------------------------------------------------------------
    # Phase 3 — Derive candidate_engaged per question.
    #
    # Strategy: match each candidate turn to the nearest triage decision
    # by timestamp (triage fires ~1–6 s before the transcript records the
    # turn; always the closest event when no other candidate turn is nearby).
    # engaged = kind NOT IN _NOT_ENGAGED_KINDS.
    # A question is engaged if AT LEAST ONE of its candidate turns is engaged,
    # OR (fallback) if the candidate said something non-empty and there is no
    # triage signal to contradict it.
    # ------------------------------------------------------------------
    triage_events = sorted(
        [e for e in events if e.get("kind") == "engine.v2.triage.decision"],
        key=lambda e: int(e.get("t_ms", 0)),
    )
    triage_ts: list[int] = [int(e.get("t_ms", 0)) for e in triage_events]

    # Build per-question engagement flag.
    engaged_per_q: dict[str, bool] = {}

    for qid in question_order:
        turns_for_q = candidate_turns.get(qid, [])
        if not turns_for_q:
            # No candidate turns → not engaged.
            engaged_per_q[qid] = False
            continue

        if not triage_ts:
            # No triage signal at all → default True if candidate said something.
            has_content = any(t.get("text", "").strip() for t in turns_for_q)
            engaged_per_q[qid] = has_content
            continue

        # Per-turn engagement, then aggregate with OR (any engaged = True).
        any_engaged = False
        for ct in turns_for_q:
            ct_t = int(ct.get("timestamp_ms", 0))
            closest_i = min(range(len(triage_ts)), key=lambda i: abs(triage_ts[i] - ct_t))
            kind = triage_events[closest_i].get("payload", {}).get("kind", "")
            if kind not in _NOT_ENGAGED_KINDS:
                any_engaged = True
                break  # short-circuit — one engaged turn is enough
        engaged_per_q[qid] = any_engaged

    # ------------------------------------------------------------------
    # Phase 4 — Derive word_count from turn.captured events when possible.
    #
    # turn.captured fires slightly before the candidate turn is recorded in
    # the transcript.  We match by timestamp proximity and attribute the
    # word_count to the question_id of the nearest candidate turn.
    #
    # If alignment fails (no captured events, or sparse coverage), we fall
    # back to len(text.split()) on each candidate turn.
    # ------------------------------------------------------------------
    all_cand_turns: list[dict[str, Any]] = [
        t for t in transcript if t.get("role") == "candidate"
    ]
    all_cand_ts: list[int] = [int(t.get("timestamp_ms", 0)) for t in all_cand_turns]

    captured_wc_per_q: dict[str, int] = defaultdict(int)
    captured_match_counts: dict[str, int] = defaultdict(int)

    if all_cand_turns:
        for event in events:
            if event.get("kind") != "turn.captured":
                continue
            payload = event.get("payload") or {}
            wc = int(payload.get("word_count", 0))
            e_t_ms = int(event.get("t_ms", 0))
            # Find nearest candidate turn.
            closest_i = min(range(len(all_cand_ts)), key=lambda i: abs(all_cand_ts[i] - e_t_ms))
            matched_ct = all_cand_turns[closest_i]
            # Attribute to the question the candidate turn belongs to.
            # We use effective_qid: turn's own qid or fall back to the question
            # that the candidate was answering (same logic as Phase 1).
            c_qid = matched_ct.get("question_id") or None
            if c_qid is None:
                # If the candidate turn had no qid, find which question it was
                # added to in Phase 1 by scanning candidate_turns dicts.
                for q, turns in candidate_turns.items():
                    if matched_ct in turns:
                        c_qid = q
                        break
            if c_qid is not None:
                captured_wc_per_q[c_qid] += wc
                captured_match_counts[c_qid] += 1

    # ------------------------------------------------------------------
    # Phase 5 — Assemble ScoredUnit list.
    # ------------------------------------------------------------------
    units: list[ScoredUnit] = []

    for qid in question_order:
        turns_for_q = candidate_turns.get(qid, [])

        # Candidate answer: join all candidate turn texts.
        candidate_answer = " ".join(t.get("text", "").strip() for t in turns_for_q).strip()

        # word_count: prefer turn.captured aggregate; fall back to text split.
        if captured_match_counts.get(qid, 0) > 0:
            wc = captured_wc_per_q[qid]
        else:
            wc = sum(len(t.get("text", "").split()) for t in turns_for_q)

        units.append(
            ScoredUnit(
                question_id=qid,
                question_text=question_text.get(qid, ""),
                candidate_answer=candidate_answer,
                answer_start_ms=answer_start_ms.get(qid, 0),
                probes_fired=probes_per_q.get(qid, 0),
                clarifies=clarifies_per_q.get(qid, 0),
                word_count=wc,
                candidate_engaged=engaged_per_q.get(qid, True),
            )
        )

    return units
