from app.modules.interview_runtime import question_asked_at_ms


def test_picks_earliest_agent_timestamp_per_question():
    transcript = [
        {"role": "agent", "text": "Q1?", "timestamp_ms": 1000, "question_id": "q1"},
        {"role": "candidate", "text": "...", "timestamp_ms": 1500, "question_id": "q1"},
        {"role": "agent", "text": "probe q1", "timestamp_ms": 2000, "question_id": "q1"},
        {"role": "agent", "text": "Q2?", "timestamp_ms": 3000, "question_id": "q2"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 1000, "q2": 3000}


def test_ignores_candidate_and_untagged_lines():
    transcript = [
        {"role": "agent", "text": "filler", "timestamp_ms": 100, "question_id": None},
        {"role": "candidate", "text": "hi", "timestamp_ms": 200, "question_id": "q1"},
        {"role": "agent", "text": "Q1?", "timestamp_ms": 300, "question_id": "q1"},
    ]
    assert question_asked_at_ms(transcript) == {"q1": 300}


def test_empty_transcript_returns_empty():
    assert question_asked_at_ms([]) == {}
