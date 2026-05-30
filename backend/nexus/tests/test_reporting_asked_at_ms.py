import pytest

from app.modules.reporting.schemas import QuestionOut


def test_questionout_has_asked_at_ms_and_thumbnail_url_defaults():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="Q?", candidate_quote="a")
    assert q.asked_at_ms is None
    assert q.thumbnail_url is None


def test_question_asked_at_ms_derivation():
    # The report builder derives asked_at_ms from the transcript's question_id tags.
    from app.modules.interview_runtime import question_asked_at_ms
    transcript = [{"role": "agent", "text": "Q1?", "timestamp_ms": 4200, "question_id": "q1"}]
    assert question_asked_at_ms(transcript) == {"q1": 4200}
