"""Pure helper: derive per-question asked-at timestamps from a persisted transcript.

The engine stamps agent transcript turns with the bank question_id on the floor
when the turn was spoken (question-bearing acts only). The FIRST such turn for a
question is when it was asked. Consumed by the vision worker (to choose
thumbnail frames). The reporting builder has its own typed equivalent over
``SessionEvidence`` Pydantic models (``reporting.service.asked_at_ms_by_question``).
"""
from __future__ import annotations

from app.modules.interview_runtime.models import WordTiming

RawWord = tuple[str, float, float, float]  # (text, start_s, end_s, confidence)


def asked_at_ms_by_question_evidence(transcript: list[dict]) -> dict[str, int]:
    """Map question_id -> earliest agent ``span.start_ms`` that delivered it.

    Reads the gen-3 transcript shape persisted in
    ``sessions.session_evidence_json["transcript"]`` — a list of turn dicts:
    ``{"speaker": "agent"|"candidate", "question_id": str|None,
       "span": {"start_ms": int, "end_ms": int}, "words": [...]}``.

    Candidate turns and untagged agent turns (bridges / holds / close) are
    ignored, as are agent turns without a usable ``span.start_ms``. Timestamps
    are milliseconds since session start. Pure — no IO.
    """
    out: dict[str, int] = {}
    for turn in transcript:
        if turn.get("speaker") != "agent":
            continue
        qid = turn.get("question_id")
        if not qid:
            continue
        span = turn.get("span") or {}
        start = span.get("start_ms")
        if start is None:
            continue
        start = int(start)
        if qid not in out or start < out[qid]:
            out[qid] = start
    return out


def relative_words(raw: list[RawWord]) -> list[WordTiming]:
    """Convert STT word tuples (seconds, stream clock) into WordTiming whose
    offsets are milliseconds relative to the first word (first word start = 0).
    Negative offsets from clock jitter are clamped to 0.
    """
    if not raw:
        return []
    base = raw[0][1]
    out: list[WordTiming] = []
    for text, start_s, end_s, conf in raw:
        start_ms = max(0, round((start_s - base) * 1000))
        end_ms = max(start_ms, round((end_s - base) * 1000))
        out.append(
            WordTiming(text=text, start_ms=start_ms, end_ms=end_ms, confidence=conf)
        )
    return out


def turn_bounds(*, anchor_ms: int, words: list[WordTiming]) -> tuple[int, int]:
    """Best-effort turn speech bounds on the session clock.

    ``anchor_ms`` is the turn's commit timestamp (the existing ``timestamp_ms``).
    We treat it as the turn END and walk back by the spoken duration
    (last word's relative end). With no words, both bounds collapse to the
    anchor. Never returns a negative start.

    Intentionally approximate (commit fires after the endpointing silence, so
    the true speech end is slightly earlier); the reel render adds a safety pad
    and Phase 2 refines the absolute mapping against a real recording.
    """
    if not words:
        return anchor_ms, anchor_ms
    duration_ms = words[-1].end_ms
    start_ms = max(0, anchor_ms - duration_ms)
    return start_ms, anchor_ms
