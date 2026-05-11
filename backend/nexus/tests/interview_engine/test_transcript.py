"""Tests for the engine-envelope → transcript renderer.

Renderer exists because LiveKit's chat_history drops conversation items
on interrupted/retried `session.say` calls. Our engine envelope is the
authoritative record; this module reconstructs a clean transcript from
it for the LK Cloud-dashboard replacement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.interview_engine.transcript import (
    TranscriptItem,
    load_envelope,
    render_transcript_from_envelope,
    write_transcript_artifact,
)


def _make_envelope(events: list[dict]) -> dict:
    """Minimal envelope wrapper for renderer tests."""
    return {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "started_at": "2026-05-11T00:00:00Z",
        "closed_at": "2026-05-11T00:01:00Z",
        "events": events,
    }


class TestRenderTranscript:
    def test_empty_envelope_returns_empty_list(self):
        assert render_transcript_from_envelope(_make_envelope([])) == []

    def test_renders_opener_then_body_for_one_turn(self):
        envelope = _make_envelope([
            {
                "kind": "speaker.opener.played",
                "wall_ms": 1000,
                "payload": {
                    "turn_id": "turn-a",
                    "opener_text": "Hi, I'm Punar.",
                },
            },
            {
                "kind": "speaker.output",
                "wall_ms": 2000,
                "payload": {
                    "turn_id": "turn-a",
                    "final_utterance": "Walk me through the architecture.",
                },
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert items == [
            TranscriptItem(
                role="agent", kind="opener", text="Hi, I'm Punar.",
                wall_ms=1000, turn_id="turn-a",
            ),
            TranscriptItem(
                role="agent", kind="body",
                text="Walk me through the architecture.",
                wall_ms=2000, turn_id="turn-a",
            ),
        ]

    def test_renders_user_stt_finals_only(self):
        """Non-final STT events MUST be filtered. They're partial
        transcripts that get superseded."""
        envelope = _make_envelope([
            {
                "kind": "audio.stt.transcribed",
                "wall_ms": 500,
                "payload": {"is_final": False, "transcript": "Hi"},
            },
            {
                "kind": "audio.stt.transcribed",
                "wall_ms": 800,
                "payload": {"is_final": True, "transcript": "Hi. How are you?"},
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert len(items) == 1
        assert items[0].role == "user"
        assert items[0].kind == "user_stt"
        assert items[0].text == "Hi. How are you?"

    def test_renders_repeat_cache_as_repeat_kind(self):
        envelope = _make_envelope([
            {
                "kind": "speaker.cached",
                "wall_ms": 5000,
                "payload": {
                    "turn_id": "turn-c",
                    "final_utterance": "Walk me through the architecture.",
                    "source_turn_id": "turn-a",
                    "instruction_kind": "repeat",
                },
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert items == [
            TranscriptItem(
                role="agent", kind="repeat",
                text="Walk me through the architecture.",
                wall_ms=5000, turn_id="turn-c",
            ),
        ]

    def test_drops_empty_text_events(self):
        """Interrupted Speaker bodies write SPEAKER_OUTPUT with empty
        final_utterance. Those don't belong in a human-readable transcript."""
        envelope = _make_envelope([
            {
                "kind": "speaker.output",
                "wall_ms": 1000,
                "payload": {"turn_id": "t", "final_utterance": ""},
            },
            {
                "kind": "speaker.opener.played",
                "wall_ms": 2000,
                "payload": {"turn_id": "t", "opener_text": "   "},
            },
            {
                "kind": "audio.stt.transcribed",
                "wall_ms": 3000,
                "payload": {"is_final": True, "transcript": ""},
            },
        ])
        assert render_transcript_from_envelope(envelope) == []

    def test_sorts_by_wall_ms_ascending(self):
        """The renderer must produce strict chronological order even when
        events appear out-of-order in the envelope (defensive — the
        EventCollector appends in order but downstream merges or replays
        may reorder)."""
        envelope = _make_envelope([
            {
                "kind": "speaker.output",
                "wall_ms": 5000,
                "payload": {"turn_id": "t2", "final_utterance": "Second."},
            },
            {
                "kind": "speaker.output",
                "wall_ms": 1000,
                "payload": {"turn_id": "t1", "final_utterance": "First."},
            },
            {
                "kind": "speaker.output",
                "wall_ms": 3000,
                "payload": {"turn_id": "tm", "final_utterance": "Middle."},
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert [it.text for it in items] == ["First.", "Middle.", "Second."]

    def test_ignores_non_transcript_events(self):
        """Audit events that aren't transcript-bearing must pass through
        without producing rows: turn.started, judge.call, etc."""
        envelope = _make_envelope([
            {
                "kind": "turn.started",
                "wall_ms": 1000,
                "payload": {"turn_id": "t1", "turn_index": 1},
            },
            {
                "kind": "judge.call",
                "wall_ms": 1500,
                "payload": {"action": "advance"},
            },
            {
                "kind": "audio.user.state",
                "wall_ms": 1600,
                "payload": {"new_state": "speaking"},
            },
            {
                "kind": "speaker.output",
                "wall_ms": 2000,
                "payload": {"turn_id": "t1", "final_utterance": "Real content."},
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert len(items) == 1
        assert items[0].text == "Real content."

    def test_skips_events_with_invalid_wall_ms(self):
        """Defensive: events without a numeric wall_ms can't be sorted
        correctly, so drop them. Should never happen in production
        envelopes but the renderer must not crash."""
        envelope = _make_envelope([
            {
                "kind": "speaker.output",
                "wall_ms": None,
                "payload": {"turn_id": "t", "final_utterance": "Bad event."},
            },
            {
                "kind": "speaker.output",
                "wall_ms": 1000,
                "payload": {"turn_id": "ok", "final_utterance": "Good event."},
            },
        ])
        items = render_transcript_from_envelope(envelope)
        assert len(items) == 1
        assert items[0].text == "Good event."


class TestWriteTranscriptArtifact:
    def test_writes_artifact_next_to_envelope_by_default(self, tmp_path: Path):
        env_path = tmp_path / "session-x.json"
        env_path.write_text(json.dumps(_make_envelope([
            {
                "kind": "speaker.output",
                "wall_ms": 100,
                "payload": {"turn_id": "t1", "final_utterance": "Hello."},
            },
        ])))
        out_path = write_transcript_artifact(env_path)
        assert out_path == env_path.with_suffix(env_path.suffix + ".transcript.json")
        assert out_path.exists()

        data = json.loads(out_path.read_text())
        assert data["session_id"] == "00000000-0000-0000-0000-000000000000"
        assert data["started_at"] == "2026-05-11T00:00:00Z"
        assert data["closed_at"] == "2026-05-11T00:01:00Z"
        assert len(data["items"]) == 1
        assert data["items"][0]["role"] == "agent"
        assert data["items"][0]["kind"] == "body"
        assert data["items"][0]["text"] == "Hello."

    def test_writes_artifact_to_explicit_output_path(self, tmp_path: Path):
        env_path = tmp_path / "session-y.json"
        env_path.write_text(json.dumps(_make_envelope([])))
        explicit_out = tmp_path / "subdir" / "custom.json"
        explicit_out.parent.mkdir()
        out_path = write_transcript_artifact(env_path, output_path=explicit_out)
        assert out_path == explicit_out
        assert out_path.exists()


class TestRendersRealSession:
    """Smoke test against the real session 0931c162 envelope to verify
    the renderer recovers messages that LK's chat_history dropped.

    Specifically, this session's chat_history is missing 2 agent bodies
    and 3 openers that demonstrably played per the OTel agent_turn spans.
    Our envelope-based renderer must surface them all.
    """

    REAL_SESSION_PATH = Path(
        "/app/engine-events/0931c162-2c0e-4581-8a20-1717dae4501b.json"
    )

    @pytest.mark.skipif(
        not REAL_SESSION_PATH.exists(),
        reason="Real session envelope not present in this environment",
    )
    def test_real_session_includes_compare_frontend_question(self):
        envelope = load_envelope(self.REAL_SESSION_PATH)
        items = render_transcript_from_envelope(envelope)
        agent_bodies = [it.text for it in items if it.role == "agent" and it.kind == "body"]
        # This body was missing from LK's chat_history.json but our
        # renderer must surface it from the speaker.output event.
        assert any("Compare how your frontend approach" in t for t in agent_bodies)

    @pytest.mark.skipif(
        not REAL_SESSION_PATH.exists(),
        reason="Real session envelope not present in this environment",
    )
    def test_real_session_includes_switch_gears_opener(self):
        envelope = load_envelope(self.REAL_SESSION_PATH)
        items = render_transcript_from_envelope(envelope)
        openers = [it.text for it in items if it.role == "agent" and it.kind == "opener"]
        # This opener was missing from LK's chat_history.json.
        assert any("switch gears" in t for t in openers)
