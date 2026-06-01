"""Pure helper: derive per-question asked-at timestamps from a persisted transcript.

The engine stamps agent transcript lines with the bank question_id on the floor
when the line was spoken (question-bearing acts only — see
interview_engine/mouth/input_builder.is_question_bearing). The FIRST such line
for a question is when it was asked. Consumed by the reporting builder (to set
QuestionOut.asked_at_ms) and the vision worker (to choose thumbnail frames).
"""
from __future__ import annotations

from app.modules.interview_runtime.models import WordTiming

RawWord = tuple[str, float, float, float]  # (text, start_s, end_s, confidence)


def question_asked_at_ms(transcript: list[dict]) -> dict[str, int]:
    """Map question_id -> earliest agent ``timestamp_ms`` that delivered it.

    Candidate lines and untagged agent lines (fillers / holds / close) are
    ignored. Timestamps are milliseconds since session start.
    """
    out: dict[str, int] = {}
    for entry in transcript:
        if entry.get("role") != "agent":
            continue
        qid = entry.get("question_id")
        if not qid:
            continue
        ts = entry.get("timestamp_ms")
        if ts is None:
            continue
        ts = int(ts)
        if qid not in out or ts < out[qid]:
            out[qid] = ts
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
