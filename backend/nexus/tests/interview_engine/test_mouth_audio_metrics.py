"""Perceived-latency aggregation from per-turn ChatMessage.metrics events (CMI-3)."""

from app.modules.interview_engine.audio_metrics import (
    compute_audio_summary,
    summarize_perceived_latency,
)


def _turn_events():
    # The agent records one event per assistant/user turn from ChatMessage.metrics (seconds).
    return [
        {"kind": "turn.latency.assistant",
         "payload": {"llm_node_ttft": 0.55, "tts_node_ttfb": 0.30, "e2e_latency": 1.10}},
        {"kind": "turn.latency.assistant",
         "payload": {"llm_node_ttft": 0.65, "tts_node_ttfb": 0.40, "e2e_latency": 1.40}},
        {"kind": "turn.latency.user",
         "payload": {"end_of_turn_delay": 0.90, "transcription_delay": 0.20}},
    ]


def test_summarize_perceived_latency_blocks():
    out = summarize_perceived_latency(_turn_events())
    # perceived_response = llm_node_ttft + tts_node_ttfb, per turn -> [850, 1050] ms
    assert out["perceived_response_ms"]["p50"] == 950
    assert out["perceived_response_ms"]["max"] == 1050
    assert out["llm_ttft_ms"]["p50"] == 600          # (550, 650) -> mean = 600
    assert out["tts_ttfb_ms"]["max"] == 400
    assert out["e2e_latency_ms"]["max"] == 1400
    assert out["eou_delay_ms"]["p50"] == 900          # working EOU from user end_of_turn_delay


def test_compute_audio_summary_includes_perceived_block():
    summary = compute_audio_summary(events=_turn_events(), config_snapshot={"endpointing_mode": "dynamic"})
    assert "perceived" in summary
    assert summary["perceived"]["perceived_response_ms"]["p50"] == 950
    # M3 keys still present (back-compat).
    assert "latency" in summary and "config" in summary


def test_perceived_block_empty_when_no_turn_events():
    out = summarize_perceived_latency([{"kind": "audio.user.state", "payload": {}}])
    assert out["perceived_response_ms"] == {"p50": 0, "p95": 0, "max": 0, "n": 0}
