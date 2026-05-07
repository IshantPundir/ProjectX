from app.modules.interview_engine.event_log.redaction import redact_payload


def test_judge_call_input_summary_kept_metadata_mode():
    """Per spec §6.4: candidate utterance NOT redacted in either mode."""
    payload = {
        "turn_id": "t", "model": "m", "prompt_hash": "h",
        "input_summary": {"candidate_utterance": "I worked on JQL"},
        "output": {"thought": "x"},
        "latency_ms": 100,
    }
    out = redact_payload(kind="judge.call", payload=payload, mode="metadata")
    assert out["input_summary"]["candidate_utterance"] == "I worked on JQL"


def test_speaker_call_final_utterance_kept_both_modes():
    payload = {
        "turn_id": "t", "model": "m", "prompt_hash": "h",
        "instruction_kind": "deliver_question", "bank_text_present": True,
        "latency_ms_first_token": 100, "latency_ms_total": 500,
        "final_utterance": "Tell me about your work.",
    }
    out_meta = redact_payload(kind="speaker.call", payload=payload, mode="metadata")
    out_full = redact_payload(kind="speaker.call", payload=payload, mode="full")
    assert out_meta["final_utterance"] == "Tell me about your work."
    assert out_full["final_utterance"] == "Tell me about your work."


def test_state_mutation_keeps_full_payload():
    payload = {
        "turn_id": "t", "seq": 1, "kind": "ledger.append",
        "before": None, "after": {"signal_value": "S1", "coverage_after": "partial"},
    }
    out = redact_payload(kind="state.mutation", payload=payload, mode="metadata")
    assert out == payload
