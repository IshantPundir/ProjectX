import pytest

from app.modules.reporting.schemas import QuestionOut


def test_questionout_has_asked_at_ms_and_thumbnail_url_defaults():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="Q?", candidate_quote="a")
    assert q.asked_at_ms is None
    assert q.thumbnail_url is None


def test_report_builder_derives_asked_at_ms_from_agent_transcript_spans():
    # The gen-3 report builder derives QuestionOut.asked_at_ms from the engine's
    # SessionEvidence transcript: the EARLIEST agent TranscriptTurn tagged with the
    # question_id, using span.start_ms (session-relative). See reporting/service.py.
    # (The dict-shape `question_asked_at_ms` helper is the LIVE vision-path helper over
    # sessions.transcript and is unit-tested elsewhere — not the reporting path.)
    from app.modules.interview_runtime.evidence import (
        Speaker,
        TimeSpan,
        TranscriptTurn,
    )
    from app.modules.reporting.service import asked_at_ms_by_question

    transcript = [
        TranscriptTurn(
            turn_ref="a1",
            speaker=Speaker.agent,
            text="Q1?",
            span=TimeSpan(start_ms=4200, end_ms=5200),
            pre_turn_gap_ms=0,
            question_id="q1",
        ),
    ]
    assert asked_at_ms_by_question(transcript) == {"q1": 4200}
