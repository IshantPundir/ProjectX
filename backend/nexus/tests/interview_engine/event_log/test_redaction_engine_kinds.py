from app.modules.interview_engine.event_log.redaction import (
    _ENGINE_PASSTHROUGH_KINDS,
    redact_payload,
)


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


def test_turn_coalesced_kept_verbatim_both_modes():
    """Per spec §6.4 + turn-text convention: turn.coalesced is registered in
    _ENGINE_PASSTHROUGH_KINDS alongside turn.started and turn.completed.

    Policy decision: the spec's "length+hash" wording described the
    *desired* outcome, but the existing convention for turn-text events
    (turn.started carries stt_text_raw / stt_text_used; turn.completed
    carries stt_text) is verbatim passthrough — the candidate utterance is
    the audit-grade artifact (§6.4). turn.coalesced carries prior_text,
    current_text, and combined_text — all candidate-utterance content of
    the same kind. Registering it in _ENGINE_PASSTHROUGH_KINDS keeps the
    policy symmetric with its sibling turn-text events.
    """
    payload = {
        "turn_id": "turn-b",
        "prior_turn_id": "turn-a",
        "prior_text": "I worked on distributed",
        "current_text": "systems at scale.",
        "combined_text": "I worked on distributed systems at scale.",
        "window_ms": 420,
        "prior_speaker_delivered": False,
    }
    out_meta = redact_payload(kind="turn.coalesced", payload=payload, mode="metadata")
    out_full = redact_payload(kind="turn.coalesced", payload=payload, mode="full")

    # All three content fields must be present verbatim in metadata mode —
    # passthrough, not redacted.
    assert out_meta["prior_text"] == "I worked on distributed"
    assert out_meta["current_text"] == "systems at scale."
    assert out_meta["combined_text"] == "I worked on distributed systems at scale."

    # Full mode: identical expectation (passthrough by definition).
    assert out_full["prior_text"] == "I worked on distributed"
    assert out_full["current_text"] == "systems at scale."
    assert out_full["combined_text"] == "I worked on distributed systems at scale."

    # Non-content fields preserved too.
    assert out_meta["window_ms"] == 420
    assert out_meta["prior_speaker_delivered"] is False

    # Verify explicit passthrough registration — not falling through the
    # unknown-kind default path.  This is the load-bearing assertion: without
    # it the test would pass even before the fix (unknown kinds also passthrough
    # today), hiding the policy gap.
    assert "turn.coalesced" in _ENGINE_PASSTHROUGH_KINDS, (
        "turn.coalesced must be explicitly registered in _ENGINE_PASSTHROUGH_KINDS "
        "so the audit contract is intentional, not accidental."
    )


def test_turn_dropped_and_drain_replayed_passthrough_both_modes():
    """Stale-turn drop-and-drain events (2026-05-11) carry candidate
    utterance text just like turn.coalesced. Same passthrough semantics:
    the candidate's words are the audit-grade artifact, not redactable.
    """
    dropped_payload = {
        "turn_id": "dropped-1",
        "candidate_text": "stale fragment text",
        "stopped_speaking_at": 100.0,
        "staleness_ms": 12000,
        "buffer_size_after": 1,
    }
    drained_payload = {
        "current_turn_id": "turn-x",
        "dropped_count": 2,
        "dropped_texts": ["fragment A.", "fragment B."],
        "combined_text": "fragment A. fragment B. fresh text.",
    }

    for mode in ("metadata", "full"):
        out_d = redact_payload(kind="turn.dropped", payload=dropped_payload, mode=mode)
        out_r = redact_payload(kind="turn.drain_replayed", payload=drained_payload, mode=mode)
        assert out_d["candidate_text"] == "stale fragment text"
        assert out_r["dropped_texts"] == ["fragment A.", "fragment B."]
        assert out_r["combined_text"] == "fragment A. fragment B. fresh text."

    # Explicit registration check, same pattern as turn.coalesced.
    assert "turn.dropped" in _ENGINE_PASSTHROUGH_KINDS
    assert "turn.drain_replayed" in _ENGINE_PASSTHROUGH_KINDS
