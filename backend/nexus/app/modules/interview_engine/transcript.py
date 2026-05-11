"""Render a clean chronological transcript from an engine-events envelope.

Why this exists
---------------
LiveKit's framework-level `chat_history` (the one surfaced in the LK
Cloud dashboard) drops conversation items when `_tts_task_impl` returns
early on interrupted/retried `session.say` calls — observed in session
0931c162-2c0e-4581-8a20-1717dae4501b, where 2 agent bodies and 3 openers
that demonstrably played (per OTel `agent_turn` spans with
`interrupted=False`) never made it into the chat_history JSON.

Our own engine audit envelope (`engine-events/<session_id>.json`) is the
source of truth — it records every Speaker output, opener playback, and
user STT final independently of the framework's pipeline. This module
reconstructs a clean speaker-ordered transcript from those events for
human review and downstream tooling (e.g., post-session reports, eval
runs).

Use it like this::

    from app.modules.interview_engine.transcript import (
        render_transcript_from_envelope,
        load_envelope,
    )

    envelope = load_envelope("engine-events/<session_id>.json")
    items = render_transcript_from_envelope(envelope)
    for item in items:
        print(f"[{item.role:9}] {item.text}")

Or via CLI::

    python -m app.modules.interview_engine.transcript \\
        engine-events/<session_id>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

TranscriptRole = Literal["agent", "user"]
TranscriptKind = Literal["opener", "body", "repeat", "user_stt"]


@dataclass(frozen=True, slots=True)
class TranscriptItem:
    """One logical line in the rendered transcript.

    * ``role`` — "agent" or "user".
    * ``kind`` — provenance signal: "opener" (Speaker opener that played),
      "body" (Speaker body LLM output), "repeat" (cached-question replay),
      "user_stt" (user STT final). Useful for filtering when building
      analytics or human-review surfaces.
    * ``text`` — what was said (already redacted by the upstream audit
      envelope according to its redaction_mode).
    * ``wall_ms`` — wall-clock timestamp from the source event.
    * ``turn_id`` — the orchestrator turn this item belongs to. Multiple
      transcript items can share a turn_id (e.g., opener + body) when
      the orchestrator emitted both within one turn.
    """
    role: TranscriptRole
    kind: TranscriptKind
    text: str
    wall_ms: int
    turn_id: str


# Event-kind constants. Duplicated here intentionally — this module
# should be runnable as a CLI without dragging in audit_events.py's
# import graph (which pulls Pydantic + module-public-API discipline).
_KIND_OPENER_PLAYED = "speaker.opener.played"
_KIND_SPEAKER_OUTPUT = "speaker.output"
_KIND_SPEAKER_CACHED = "speaker.cached"
_KIND_STT_TRANSCRIBED = "audio.stt.transcribed"


def load_envelope(path: str | Path) -> dict[str, Any]:
    """Read and parse an engine-events envelope JSON file. Thin wrapper
    so callers don't need to know the file layout."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def render_transcript_from_envelope(
    envelope: dict[str, Any],
) -> list[TranscriptItem]:
    """Render a chronological transcript from the engine envelope.

    The returned list is sorted by ``wall_ms`` ascending. Items with
    empty text (e.g., a SPEAKER_OUTPUT whose body was interrupted before
    any text was produced) are dropped. Non-final STT events are
    ignored.

    This is a pure function: no IO, no globals. The envelope must
    already be parsed (use :func:`load_envelope` to read from disk).
    """
    events = envelope.get("events", [])
    items: list[TranscriptItem] = []

    for ev in events:
        kind = ev.get("kind")
        wall_ms = ev.get("wall_ms")
        payload = ev.get("payload") or {}
        if not isinstance(wall_ms, int):
            continue
        # Skip empty-text items — they don't contribute to the
        # human-readable transcript.
        if kind == _KIND_OPENER_PLAYED:
            text = (payload.get("opener_text") or "").strip()
            if not text:
                continue
            items.append(TranscriptItem(
                role="agent",
                kind="opener",
                text=text,
                wall_ms=wall_ms,
                turn_id=payload.get("turn_id") or "",
            ))
        elif kind == _KIND_SPEAKER_OUTPUT:
            text = (payload.get("final_utterance") or "").strip()
            if not text:
                continue
            items.append(TranscriptItem(
                role="agent",
                kind="body",
                text=text,
                wall_ms=wall_ms,
                turn_id=payload.get("turn_id") or "",
            ))
        elif kind == _KIND_SPEAKER_CACHED:
            text = (payload.get("final_utterance") or "").strip()
            if not text:
                continue
            items.append(TranscriptItem(
                role="agent",
                kind="repeat",
                text=text,
                wall_ms=wall_ms,
                turn_id=payload.get("turn_id") or "",
            ))
        elif kind == _KIND_STT_TRANSCRIBED:
            if not payload.get("is_final"):
                continue
            text = (payload.get("transcript") or "").strip()
            if not text:
                continue
            items.append(TranscriptItem(
                role="user",
                kind="user_stt",
                text=text,
                wall_ms=wall_ms,
                # STT events aren't bound to a turn_id; downstream
                # tooling that wants turn correlation can join on
                # wall_ms vs turn.started boundaries.
                turn_id="",
            ))

    items.sort(key=lambda it: it.wall_ms)
    return items


def write_transcript_artifact(
    envelope_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Render the envelope and write the transcript artifact alongside it.

    By default the artifact lands at ``<envelope_path>.transcript.json``.
    The artifact's schema is a JSON object with ``session_id``,
    ``started_at``, ``closed_at``, and an ``items`` array of
    :class:`TranscriptItem` records (as dicts).
    """
    env_path = Path(envelope_path)
    envelope = load_envelope(env_path)
    items = render_transcript_from_envelope(envelope)

    if output_path is None:
        out_path = env_path.with_suffix(env_path.suffix + ".transcript.json")
    else:
        out_path = Path(output_path)

    artifact = {
        "session_id": envelope.get("session_id"),
        "started_at": envelope.get("started_at"),
        "closed_at": envelope.get("closed_at"),
        "items": [asdict(it) for it in items],
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)
    return out_path


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.interview_engine.transcript",
        description="Render a clean chronological transcript from an engine-events envelope.",
    )
    parser.add_argument(
        "envelope_path",
        help="Path to engine-events/<session_id>.json",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output path for the transcript JSON. Defaults to "
        "<envelope_path>.transcript.json next to the envelope.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Also print a human-readable transcript to stdout.",
    )
    args = parser.parse_args(argv)

    out_path = write_transcript_artifact(args.envelope_path, output_path=args.output)
    print(f"Wrote: {out_path}", file=sys.stderr)

    if args.print:
        envelope = load_envelope(args.envelope_path)
        items = render_transcript_from_envelope(envelope)
        for it in items:
            tag = "agent" if it.role == "agent" else "USER"
            print(f"[{tag:5}] ({it.kind:8}) {it.text}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
