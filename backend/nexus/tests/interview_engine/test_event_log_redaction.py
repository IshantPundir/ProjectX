"""Phase 1 — event log redaction.

Asserts the per-kind boundary in spec §5.2: every content field
enumerated there must be stripped in `metadata` mode and preserved in
`full` mode.
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.event_log.redaction import redact_payload


def test_metadata_mode_strips_stt_transcript() -> None:
    payload = {"transcript": "I have ten years experience", "transcript_chars": 28, "is_final": True}
    out = redact_payload("audio.stt.transcribed", payload, mode="metadata")
    assert "transcript" not in out
    assert out["transcript_chars"] == 28
    assert out["is_final"] is True


def test_full_mode_keeps_stt_transcript() -> None:
    payload = {"transcript": "I have ten years experience", "transcript_chars": 28}
    out = redact_payload("audio.stt.transcribed", payload, mode="full")
    assert out["transcript"] == "I have ten years experience"


def test_metadata_mode_strips_llm_message_content() -> None:
    payload = {"role": "assistant", "content": "How would you design X?", "content_chars": 24}
    out = redact_payload("llm.message.added", payload, mode="metadata")
    assert "content" not in out
    assert out["role"] == "assistant"
    assert out["content_chars"] == 24


def test_metadata_mode_strips_tool_args_and_output() -> None:
    payload = {
        "tool_name": "record_observation",
        "tool_call_id": "call_abc",
        "arguments": {"answer_summary": "Candidate said X"},
        "output": "next question text",
        "argument_keys": ["answer_summary", "wants_to_probe"],
    }
    out = redact_payload("llm.tool.executed", payload, mode="metadata")
    assert "arguments" not in out
    assert "output" not in out
    assert out["tool_name"] == "record_observation"
    assert out["argument_keys"] == ["answer_summary", "wants_to_probe"]


def test_metadata_mode_strips_disqualify_reason() -> None:
    payload = {"question_id": "q1", "reason": "candidate refused to answer"}
    out = redact_payload("disqualify.knockout", payload, mode="metadata")
    assert "reason" not in out
    assert out["question_id"] == "q1"


def test_metadata_mode_keeps_audio_metrics_payload() -> None:
    """Audio metrics carry no content — pass through unchanged."""
    payload = {"ttft": 0.312, "tokens_in": 850, "tokens_out": 42}
    out = redact_payload("audio.metrics.llm", payload, mode="metadata")
    assert out == payload


def test_unknown_kind_passes_through_in_metadata_mode() -> None:
    """Unknown kinds default to passthrough — the redaction layer is
    additive: every NEW event kind that carries content MUST add a rule
    here, but absence of a rule shouldn't crash production."""
    payload = {"foo": "bar"}
    out = redact_payload("future.unknown.kind", payload, mode="metadata")
    assert out == payload


def test_metadata_mode_strips_pipeline_error_message() -> None:
    """Plugin error strings can leak LLM response excerpts or partial STT
    transcripts. Stripped in metadata; preserved in full for forensic review."""
    payload = {
        "source": "DeepgramSTT",
        "error": "openai response: 'I cannot share the answer'",
        "error_type": "PluginError",
    }
    out = redact_payload("audio.pipeline.error", payload, mode="metadata")
    assert "error" not in out
    assert out["source"] == "DeepgramSTT"
    assert out["error_type"] == "PluginError"


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        redact_payload("audio.stt.transcribed", {}, mode="enterprise")  # type: ignore[arg-type]
