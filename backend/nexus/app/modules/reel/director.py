"""Reel Director — LLM -> validated EDL (ordered beats) for the candidate reel.

Two layers:

  * ``generate_edl`` (LLM, manual-tested): reads the report ground truth + the
    word-timed transcript and emits a raw ``ReelEdlOut`` whose ``clip``/
    ``experience`` beats reference a candidate turn by its ``turn.captured``
    COMMIT (``source_turn_ref``) and a turn-relative WORD-INDEX range
    ``[in_word, out_word]``. The LLM never sees video ms / VAD spans.
  * ``validate_edl`` (pure, deterministic guardrails — this file): resolves the
    word indices to turn-relative ms (``in_ms``/``out_ms``), rejects
    hallucinations (unknown ref / out-of-bounds index), enforces the duration
    budget (per-clip soft cap, then drop trailing question groups), and fails
    honestly if no clip survives.

The renderer maps a validated beat to video exactly as the clips-core spike does:
``video = answer_span(commit) + wall_anchor - pipeline_lag + [in_ms, out_ms]``
(see ``timing.py``); the Director stays purely in transcript space.

Keep this import-light enough for the lean image — the LLM call imports ``app.ai``
lazily so the pure validation path (and its tests) need no OpenAI/ffmpeg deps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

# --- tuning constants (transcript-space; ms) ------------------------------
MAX_TOTAL_MS = 60_000      # hard cap (design D2)
TARGET_MS = 45_000         # aim for ~45s
CLIP_SOFT_CAP_MS = 12_000  # no single answer slice longer than this
SPEAK_WPS = 2.75           # ~165 wpm, Arjun narration, for card duration estimate
_CARD_FLOOR_MS = {"title": 3_000, "ask": 2_000, "credit": 3_500, "outro": 4_000}

TIMED_KINDS = {"clip", "experience"}   # beats cut from the recording (carry timing)
BeatKind = Literal["title", "experience", "ask", "credit", "clip", "outro"]


class NoClipBeatsError(Exception):
    """No clip/experience beat survived validation — the reel cannot be built."""


# --- LLM output schema -----------------------------------------------------
class ReelBeat(BaseModel):
    kind: BeatKind
    source_turn_ref: int | None = None   # commit (timestamp_ms); clip/experience only
    in_word: int | None = None           # index into the turn's words[]
    out_word: int | None = None
    on_screen_text: str | None = None    # card copy (title/ask/credit/outro)
    caption: str | None = None           # optional hint; words[] is the caption truth
    narration_text: str | None = None    # Arjun TTS script for card beats


class ReelEdlOut(BaseModel):
    beats: list[ReelBeat]


# --- validated (renderable) EDL -------------------------------------------
@dataclass
class ValidatedBeat:
    kind: str
    duration_ms: int
    source_turn_ref: int | None = None
    in_ms: int | None = None             # turn-relative; renderer anchors to video
    out_ms: int | None = None
    on_screen_text: str | None = None
    caption: str | None = None
    narration_text: str | None = None


@dataclass
class ValidatedEdl:
    beats: list[ValidatedBeat]
    duration_ms: int


def _candidate_turns(transcript: list[dict]) -> dict[int, list[dict]]:
    """Map each candidate turn's commit (``timestamp_ms``) -> its word list."""
    out: dict[int, list[dict]] = {}
    for turn in transcript:
        if turn.get("role") != "candidate":
            continue
        commit = turn.get("timestamp_ms")
        if commit is None:
            continue
        out[int(commit)] = turn.get("words") or []
    return out


def _resolve_timed(beat: ReelBeat, turns: dict[int, list[dict]]) -> ValidatedBeat | None:
    """Resolve a clip/experience beat's word indices -> ms, or None to drop it.

    Drops the beat on a hallucinated turn ref or an out-of-bounds / inverted
    word range. Trims an over-cap slice inward to ``CLIP_SOFT_CAP_MS``.
    """
    ref = beat.source_turn_ref
    if ref is None or ref not in turns:
        return None
    words = turns[ref]
    iw, ow = beat.in_word, beat.out_word
    if iw is None or ow is None or not (0 <= iw <= ow < len(words)):
        return None

    in_ms = int(words[iw]["start_ms"])
    # per-clip soft cap: pull out_word back to the last word fitting the cap.
    while ow > iw and int(words[ow]["end_ms"]) - in_ms > CLIP_SOFT_CAP_MS:
        ow -= 1
    out_ms = int(words[ow]["end_ms"])
    return ValidatedBeat(
        kind=beat.kind, duration_ms=out_ms - in_ms, source_turn_ref=ref,
        in_ms=in_ms, out_ms=out_ms, on_screen_text=beat.on_screen_text,
        caption=beat.caption, narration_text=beat.narration_text,
    )


def _estimate_card(beat: ReelBeat) -> ValidatedBeat:
    """Estimate a card beat's duration from its narration (the render recomputes)."""
    n_words = len((beat.narration_text or "").split())
    est = math.ceil(n_words / SPEAK_WPS * 1000)
    dur = max(_CARD_FLOOR_MS.get(beat.kind, 2_000), est)
    return ValidatedBeat(
        kind=beat.kind, duration_ms=dur, on_screen_text=beat.on_screen_text,
        caption=beat.caption, narration_text=beat.narration_text,
    )


def _has_clip(group: list[ValidatedBeat]) -> bool:
    return any(b.kind in TIMED_KINDS for b in group)


def _group_body(body: list[ValidatedBeat]) -> list[list[ValidatedBeat]]:
    """Group body beats so each group ends at a timed beat (its ask/credit ride along)."""
    groups: list[list[ValidatedBeat]] = []
    cur: list[ValidatedBeat] = []
    for b in body:
        cur.append(b)
        if b.kind in TIMED_KINDS:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)   # trailing non-timed beats (e.g. an orphaned ask)
    return groups


def _fit_budget(beats: list[ValidatedBeat]) -> list[ValidatedBeat]:
    """Drop trailing groups until total <= ``MAX_TOTAL_MS``, preserving narrative.

    title (leading) and outro (trailing) are pinned; the body is grouped so a
    dropped clip takes its ask/credit with it. The last clip-bearing group is
    never dropped (>=1 clip is guaranteed by validate_edl).
    """
    title = [beats[0]] if beats and beats[0].kind == "title" else []
    outro = [beats[-1]] if beats and beats[-1].kind == "outro" else []
    body = beats[len(title): len(beats) - len(outro)]
    groups = _group_body(body)

    def total() -> int:
        return sum(b.duration_ms for g in groups for b in g) + \
            sum(b.duration_ms for b in title) + sum(b.duration_ms for b in outro)

    while groups and total() > MAX_TOTAL_MS:
        clip_groups = sum(1 for g in groups if _has_clip(g))
        if _has_clip(groups[-1]) and clip_groups <= 1:
            break   # would drop the only clip group — stop
        groups.pop()

    return title + [b for g in groups for b in g] + outro


def validate_edl(edl: ReelEdlOut, transcript: list[dict]) -> ValidatedEdl:
    """Resolve + guard a raw LLM EDL into a renderable, budget-fitting EDL.

    Raises ``NoClipBeatsError`` if no clip/experience beat survives resolution.
    """
    turns = _candidate_turns(transcript)
    resolved: list[ValidatedBeat] = []
    for beat in edl.beats:
        if beat.kind in TIMED_KINDS:
            vb = _resolve_timed(beat, turns)
            if vb is not None:
                resolved.append(vb)
        else:
            resolved.append(_estimate_card(beat))

    if not any(b.kind in TIMED_KINDS for b in resolved):
        raise NoClipBeatsError("EDL has no valid clip/experience beat")

    fitted = _fit_budget(resolved)
    return ValidatedEdl(beats=fitted, duration_ms=sum(b.duration_ms for b in fitted))
