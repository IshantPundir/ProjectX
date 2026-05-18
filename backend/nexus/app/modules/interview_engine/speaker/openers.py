"""Opener slug computation + per-turn filtering.

Single source of truth for the 3-word slug used by both:

- ``naturalness.detect_repeated_opener`` (post-hoc flagging)
- ``input_builder.build_speaker_input`` (pre-prompt filtering of
  PersonaSpec.opener_rotation against recent_reply_starts)

Lifting this here removes the duplication that previously had
``naturalness.py`` owning the slug definition while ``input_builder.py``
needed its own copy.
"""
from __future__ import annotations


def opener_slug(text: str) -> str:
    """Lowercase first-3-word slug.

    Matches naturalness.detect_repeated_opener's slug definition (3
    words, post LiveKit/Vapi guidance — see naturalness.py docstring).
    Whitespace-normalized; empty input returns empty string.
    """
    return " ".join(text.strip().split()[:3]).lower()


def filter_available_openers(
    rotation: tuple[str, ...],
    recent_reply_starts: list[str],
) -> list[str]:
    """Return ``rotation`` pruned of openers whose 3-word slug matches
    any entry in ``recent_reply_starts``.

    Safety: at current dimensions (9 openers in
    ``PersonaSpec.opener_rotation``, 3 entries in
    ``recent_reply_starts`` per StateEngine._RECENT_REPLY_WINDOW), the
    filter cannot empty the rotation. If a future change inverts this
    (rotation shrinks or window grows), the safety branch returns the
    full rotation rather than emitting an empty bullet list — the
    Speaker prompt requires non-empty available_openers.
    """
    used = {opener_slug(s) for s in recent_reply_starts if s.strip()}
    fresh = [op for op in rotation if opener_slug(op) not in used]
    return fresh or list(rotation)
