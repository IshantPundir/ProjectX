"""audio_metrics.py — percentile math + v2 audio summary (pure; CMI-3)."""

from app.modules.interview_engine.audio_metrics import (
    compute_audio_summary,
    extract_ms,
    percentile_stats,
)


def test_percentile_stats_empty():
    assert percentile_stats([]) == {"p50": 0, "p95": 0, "max": 0, "n": 0}


def test_percentile_stats_odd_and_even():
    assert percentile_stats([100, 200, 300]) == {"p50": 200, "p95": 300, "max": 300, "n": 3}
    # even n -> mean of the two middle values (matches v1 _percentile_stats)
    out = percentile_stats([100, 200, 300, 400])
    assert out["p50"] == 250 and out["max"] == 400 and out["n"] == 4


def test_extract_ms_filters_and_scales():
    events = [
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": 0.9}},
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": 0}},   # dropped
        {"kind": "audio.metrics.eou_metrics", "payload": {"end_of_utterance_delay": None}}, # dropped
    ]
    assert extract_ms(events, "end_of_utterance_delay") == [900]


def test_compute_audio_summary_shape():
    events = [
        {"kind": "audio.metrics.eou_metrics",
         "payload": {"end_of_utterance_delay": 1.1, "transcription_delay": 0.2}},
        {"kind": "audio.metrics.tts_metrics", "payload": {"ttfb": 0.3}},
        {"kind": "audio.metrics.llm_metrics", "payload": {"ttft": 0.15}},
    ]
    summary = compute_audio_summary(events=events, config_snapshot={"endpointing_mode": "dynamic"})
    assert summary["latency"]["end_of_utterance_delay_ms"]["p50"] == 1100
    assert summary["latency"]["tts_ttfb_ms"]["p50"] == 300
    assert summary["latency"]["llm_ttft_ms"]["p50"] == 150
    assert summary["config"] == {"endpointing_mode": "dynamic"}
