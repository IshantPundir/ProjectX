"""Pure helper: derive per-question asked-at timestamps from a persisted transcript.

The engine stamps agent transcript lines with the bank question_id on the floor
when the line was spoken (question-bearing acts only — see
interview_engine/mouth/input_builder.is_question_bearing). The FIRST such line
for a question is when it was asked. Consumed by the reporting builder (to set
QuestionOut.asked_at_ms) and the vision worker (to choose thumbnail frames).
"""
from __future__ import annotations


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
