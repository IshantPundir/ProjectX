"""Tests for the audio tuning summary helper."""

from __future__ import annotations

from app.modules.interview_engine.agent import _compute_audio_tuning_summary


def _ev(kind: str, payload: dict, wall_ms: int) -> dict:
    return {"kind": kind, "payload": payload, "wall_ms": wall_ms}


class TestComputeAudioTuningSummary:
    def test_empty_events_returns_minimal_skeleton(self) -> None:
        summary = _compute_audio_tuning_summary(events=[], config_snapshot={"foo": "bar"})
        assert summary["pauses"]["between_utterance_ms"]["n"] == 0
        assert summary["interruptions"]["total"] == 0
        assert summary["interruptions"]["true"] == 0
        assert summary["interruptions"]["ignored_as_backchannel"] == 0
        assert summary["interruptions"]["false_recovered"] == 0
        assert summary["config_snapshot"] == {"foo": "bar"}

    def test_pause_percentiles_computed_from_user_state_events(self) -> None:
        events = [
            _ev("audio.user.state", {"old_state": "speaking", "new_state": "listening"}, 1000),
            _ev("audio.user.state", {"old_state": "listening", "new_state": "speaking"}, 1500),  # 500ms pause
            _ev("audio.user.state", {"old_state": "speaking", "new_state": "listening"}, 2000),
            _ev("audio.user.state", {"old_state": "listening", "new_state": "speaking"}, 3000),  # 1000ms pause
        ]
        summary = _compute_audio_tuning_summary(events=events, config_snapshot={})
        assert summary["pauses"]["between_utterance_ms"]["n"] == 2
        assert summary["pauses"]["between_utterance_ms"]["p50"] == 750
        assert summary["pauses"]["between_utterance_ms"]["max"] == 1000

    def test_interruption_tally(self) -> None:
        events = [
            # Two backchannel attempts the classifier correctly suppressed
            _ev("audio.overlap", {"is_interruption": False, "probability": 0.2}, 1000),
            _ev("audio.overlap", {"is_interruption": False, "probability": 0.15}, 1500),
            # One real interruption the classifier flagged
            _ev("audio.overlap", {"is_interruption": True, "probability": 0.92}, 2000),
            # Plus one post-hoc recovery (agent yielded but user fell silent)
            _ev("audio.interruption.false", {"resumed": True}, 3000),
        ]
        summary = _compute_audio_tuning_summary(events=events, config_snapshot={})
        assert summary["interruptions"]["total"] == 3
        assert summary["interruptions"]["true"] == 1
        assert summary["interruptions"]["ignored_as_backchannel"] == 2
        assert summary["interruptions"]["false_recovered"] == 1
        assert summary["interruptions"]["agent_yielded"] == 1

    def test_config_snapshot_passed_through_verbatim(self) -> None:
        snapshot = {
            "interruption_mode": "adaptive",
            "noise_cancellation": "ai_coustics_quail",
            "nc_enhancement_level": 0.5,
            "unlikely_threshold": 0.15,
            "endpointing_max_delay": 6.0,
            "silero_min_silence_duration": 0.8,
            "silero_activation_threshold": 0.5,
        }
        summary = _compute_audio_tuning_summary(events=[], config_snapshot=snapshot)
        assert summary["config_snapshot"] == snapshot
