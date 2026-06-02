"""Director EDL validation — pure-function table tests (lean nexus image).

v2 fit-pitch model: title -> §1 `match` -> §2 (`point` -> clip[+clip])xN -> outro.
The Director's LLM call is exercised manually on 5e004a4d (see the design doc);
these tests cover the deterministic guardrails that run AFTER the LLM:
turn-ref check, word-index bounds -> ms resolution, duplicate-span dedup, the
duration budget + group-by-lead-card drop order, and >=1-clip-or-fail.
"""
import pytest

from app.modules.reel.director import (
    MAX_TOTAL_MS,
    NoClipBeatsError,
    ReelBeat,
    ReelEdlOut,
    validate_edl,
)


def _turn(commit, n_words, *, step_ms=1000, dur_ms=900, role="candidate",
          question_id="q1", prefix="w"):
    """Candidate turn with ``n_words`` turn-relative words on a fixed grid.

    word i: start_ms = i*step_ms, end_ms = i*step_ms + dur_ms.
    """
    words = [
        {"text": f"{prefix}{i}", "start_ms": i * step_ms, "end_ms": i * step_ms + dur_ms,
         "confidence": 1.0}
        for i in range(n_words)
    ]
    return {
        "role": role, "timestamp_ms": commit, "question_id": question_id,
        "words": words, "text": " ".join(w["text"] for w in words),
        "start_ms": 0, "end_ms": (n_words - 1) * step_ms + dur_ms if n_words else 0,
    }


def _clip(commit, in_word, out_word, **kw):
    return ReelBeat(kind="clip", source_turn_ref=commit, in_word=in_word,
                    out_word=out_word, **kw)


def _by_kind(vedl, kind):
    return [b for b in vedl.beats if b.kind == kind]


# --- word-index resolution ------------------------------------------------

def test_clip_resolves_indices_to_word_boundary_ms():
    transcript = [_turn(203401, 20)]
    edl = ReelEdlOut(beats=[_clip(203401, 3, 8, caption="hi")])
    vedl = validate_edl(edl, transcript)
    clip = _by_kind(vedl, "clip")[0]
    assert clip.in_ms == 3000           # words[3].start_ms
    assert clip.out_ms == 8 * 1000 + 900  # words[8].end_ms
    assert clip.duration_ms == clip.out_ms - clip.in_ms
    assert clip.source_turn_ref == 203401
    assert clip.caption == "hi"


def test_experience_beat_is_resolved_like_a_clip():
    transcript = [_turn(500, 10)]
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="experience", source_turn_ref=500, in_word=0, out_word=2),
        _clip(500, 4, 6),
    ])
    vedl = validate_edl(edl, transcript)
    exp = _by_kind(vedl, "experience")[0]
    assert exp.in_ms == 0
    assert exp.out_ms == 2 * 1000 + 900


# --- hallucination rejection ----------------------------------------------

def test_unknown_source_turn_ref_drops_the_beat():
    transcript = [_turn(100, 10)]
    edl = ReelEdlOut(beats=[_clip(100, 0, 3), _clip(999999, 0, 3)])
    vedl = validate_edl(edl, transcript)
    refs = [b.source_turn_ref for b in _by_kind(vedl, "clip")]
    assert refs == [100]            # the hallucinated 999999 ref is gone


def test_out_of_bounds_word_index_drops_the_beat():
    transcript = [_turn(100, 5)]    # valid indices 0..4
    edl = ReelEdlOut(beats=[_clip(100, 0, 2), _clip(100, 0, 99)])
    vedl = validate_edl(edl, transcript)
    assert len(_by_kind(vedl, "clip")) == 1


def test_inverted_word_range_drops_the_beat():
    transcript = [_turn(100, 10)]
    edl = ReelEdlOut(beats=[_clip(100, 7, 2), _clip(100, 1, 4)])
    vedl = validate_edl(edl, transcript)
    kept = _by_kind(vedl, "clip")
    assert len(kept) == 1 and kept[0].in_ms == 1000


def test_single_word_clip_is_valid():
    transcript = [_turn(100, 10)]
    vedl = validate_edl(ReelEdlOut(beats=[_clip(100, 3, 3)]), transcript)
    clip = _by_kind(vedl, "clip")[0]
    assert clip.in_ms == 3000 and clip.out_ms == 3900


# --- duplicate-span dedup backstop ----------------------------------------

def test_duplicate_overlapping_clip_span_is_dropped():
    transcript = [_turn(100, 12)]
    # second clip [2,6] overlaps the first [0,5] on the same turn -> dropped
    edl = ReelEdlOut(beats=[_clip(100, 0, 5), _clip(100, 2, 6)])
    vedl = validate_edl(edl, transcript)
    clips = _by_kind(vedl, "clip")
    assert len(clips) == 1 and clips[0].in_ms == 0


def test_disjoint_clips_same_turn_both_kept():
    transcript = [_turn(100, 12)]
    edl = ReelEdlOut(beats=[_clip(100, 0, 2), _clip(100, 6, 8)])
    vedl = validate_edl(edl, transcript)
    assert len(_by_kind(vedl, "clip")) == 2


# --- per-clip soft cap ----------------------------------------------------

def test_overlong_clip_is_trimmed_to_the_soft_cap():
    transcript = [_turn(100, 20)]   # a 0..19 clip would be ~19.9s; soft cap 12s.
    edl = ReelEdlOut(beats=[_clip(100, 0, 19)])
    vedl = validate_edl(edl, transcript)
    clip = _by_kind(vedl, "clip")[0]
    assert clip.in_ms == 0
    assert clip.out_ms == 11900          # last word with end_ms <= 12000
    assert clip.duration_ms <= 12000


# --- total budget: group-by-lead-card -------------------------------------

def test_total_within_budget_keeps_all_beats():
    transcript = [_turn(1, 6), _turn(2, 6)]
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="title", on_screen_text="t"),
        ReelBeat(kind="match", on_screen_text="great match"),
        ReelBeat(kind="point", on_screen_text="p1"), _clip(1, 0, 5),
        ReelBeat(kind="point", on_screen_text="p2"), _clip(2, 0, 5),
        ReelBeat(kind="outro", on_screen_text="o"),
    ])
    vedl = validate_edl(edl, transcript)
    assert len(_by_kind(vedl, "clip")) == 2
    assert len(_by_kind(vedl, "point")) == 2
    assert vedl.duration_ms <= MAX_TOTAL_MS


def test_point_and_its_clips_drop_together_as_a_group():
    # four points, each with TWO ~11.9s clips -> way over 60s, forcing trailing
    # point-groups (card + both clips) to drop as units.
    transcript = [_turn(i, 12) for i in range(1, 9)]
    beats = [ReelBeat(kind="title", on_screen_text="t"),
             ReelBeat(kind="match", on_screen_text="m")]
    for p in range(4):
        a, b = 2 * p + 1, 2 * p + 2
        beats += [ReelBeat(kind="point", on_screen_text=f"p{p}"),
                  _clip(a, 0, 11), _clip(b, 0, 11)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)

    kinds = [b.kind for b in vedl.beats]
    assert kinds[0] == "title" and kinds[-1] == "outro"
    assert vedl.duration_ms <= MAX_TOTAL_MS
    # each surviving point keeps BOTH of its clips (groups drop whole)
    points = _by_kind(vedl, "point")
    clips = _by_kind(vedl, "clip")
    assert 1 <= len(points) < 4
    assert len(clips) == 2 * len(points)


def test_match_section_survives_when_trailing_points_dropped():
    transcript = [_turn(i, 12) for i in range(1, 7)]
    beats = [
        ReelBeat(kind="title", on_screen_text="t"),
        ReelBeat(kind="match", on_screen_text="great match"),
        ReelBeat(kind="experience", source_turn_ref=1, in_word=0, out_word=11),
    ]
    for i in (2, 3, 4, 5):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 11)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    # §1 (match + establishing experience clip) is first -> dropped last; survives
    assert _by_kind(vedl, "match")
    assert _by_kind(vedl, "experience")
    assert vedl.duration_ms <= MAX_TOTAL_MS


# --- >=1 clip or fail ------------------------------------------------------

def test_zero_clip_beats_raises():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="title", on_screen_text="t"),
        ReelBeat(kind="match", on_screen_text="m"),
        ReelBeat(kind="outro", on_screen_text="o"),
    ])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_turn(1, 5)])


def test_all_clips_hallucinated_raises():
    edl = ReelEdlOut(beats=[_clip(777, 0, 3), _clip(888, 0, 3)])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_turn(1, 5)])


# --- card duration estimate ------------------------------------------------

def test_match_card_duration_estimated_from_narration_word_count():
    transcript = [_turn(1, 6)]
    narration = " ".join(["word"] * 22)   # 22 / 2.75 = 8s, above the match floor
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="match", on_screen_text="m", narration_text=narration),
        _clip(1, 0, 5),
    ])
    vedl = validate_edl(edl, transcript)
    match = _by_kind(vedl, "match")[0]
    assert match.duration_ms >= 8000


def test_point_card_uses_its_floor_when_narration_short():
    transcript = [_turn(1, 6)]
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="point", on_screen_text="p", narration_text="short"),
        _clip(1, 0, 5),
    ])
    vedl = validate_edl(edl, transcript)
    assert _by_kind(vedl, "point")[0].duration_ms == 3500   # point floor
