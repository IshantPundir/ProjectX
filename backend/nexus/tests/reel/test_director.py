"""Director EDL validation — pure-function table tests (lean nexus image).

The Director's LLM call is exercised manually on 5e004a4d (see the design doc);
these tests cover the deterministic guardrails that run AFTER the LLM:
turn-ref check, word-index bounds -> ms resolution, the duration budget +
trim/drop fit order, and the >=1-clip-or-fail rule.
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


# --- per-clip soft cap (fit step a) ---------------------------------------

def test_overlong_clip_is_trimmed_to_the_soft_cap():
    # 20 words on a 1000ms grid -> a 0..19 clip would be ~19.9s; soft cap 12s.
    transcript = [_turn(100, 20)]
    edl = ReelEdlOut(beats=[_clip(100, 0, 19)])
    vedl = validate_edl(edl, transcript)
    clip = _by_kind(vedl, "clip")[0]
    assert clip.in_ms == 0
    # last word whose end_ms <= in_ms + 12000: word 11 ends 11900, word 12 ends 12900
    assert clip.out_ms == 11900
    assert clip.duration_ms <= 12000


# --- total budget (fit steps b/c) -----------------------------------------

def test_total_within_budget_keeps_all_beats():
    transcript = [_turn(1, 6), _turn(2, 6), _turn(3, 6)]
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="title", on_screen_text="t"),
        _clip(1, 0, 5), _clip(2, 0, 5), _clip(3, 0, 5),
        ReelBeat(kind="outro", on_screen_text="o"),
    ])
    vedl = validate_edl(edl, transcript)
    assert len(_by_kind(vedl, "clip")) == 3
    assert vedl.duration_ms <= MAX_TOTAL_MS


def test_over_budget_drops_trailing_question_groups_keeping_title_outro_and_one_clip():
    # four ~11.9s clips (each under the 12s cap) = ~47.6s of clips alone; with
    # title+outro the total exceeds 60s, forcing trailing groups to drop.
    transcript = [_turn(i, 12) for i in (1, 2, 3, 4)]
    beats = [ReelBeat(kind="title", on_screen_text="t")]
    for i in (1, 2, 3, 4):
        beats += [
            ReelBeat(kind="ask", on_screen_text=f"ask{i}"),
            ReelBeat(kind="credit", on_screen_text=f"credit{i}"),
            _clip(i, 0, 11),
        ]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)

    kinds = [b.kind for b in vedl.beats]
    assert kinds[0] == "title" and kinds[-1] == "outro"
    clips = _by_kind(vedl, "clip")
    assert 1 <= len(clips) < 4                      # at least one, but trimmed down
    assert vedl.duration_ms <= MAX_TOTAL_MS
    # earliest clip (turn 1) is the highest priority -> survives; trailing dropped
    assert clips[0].source_turn_ref == 1
    # a dropped clip's ask/credit go with it (no orphans): counts stay aligned
    assert len(_by_kind(vedl, "ask")) == len(clips)
    assert len(_by_kind(vedl, "credit")) == len(clips)


def test_experience_survives_longer_than_trailing_question_groups():
    transcript = [_turn(i, 12) for i in (1, 2, 3, 4, 5)]
    beats = [
        ReelBeat(kind="title", on_screen_text="t"),
        ReelBeat(kind="experience", source_turn_ref=1, in_word=0, out_word=11),
    ]
    for i in (2, 3, 4, 5):
        beats += [ReelBeat(kind="ask", on_screen_text=f"a{i}"), _clip(i, 0, 11)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    # experience is first in order -> dropped last; it must remain
    assert _by_kind(vedl, "experience")
    assert vedl.duration_ms <= MAX_TOTAL_MS


# --- >=1 clip or fail ------------------------------------------------------

def test_zero_clip_beats_raises():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="title", on_screen_text="t"),
        ReelBeat(kind="outro", on_screen_text="o"),
    ])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_turn(1, 5)])


def test_all_clips_hallucinated_raises():
    edl = ReelEdlOut(beats=[_clip(777, 0, 3), _clip(888, 0, 3)])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_turn(1, 5)])


# --- card duration estimate ------------------------------------------------

def test_card_duration_estimated_from_narration_word_count():
    transcript = [_turn(1, 6)]
    # ~14 words of narration / 2.75 wps ~= 5.1s, above the title floor.
    narration = " ".join(["word"] * 14)
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="title", on_screen_text="t", narration_text=narration),
        _clip(1, 0, 5),
    ])
    vedl = validate_edl(edl, transcript)
    title = _by_kind(vedl, "title")[0]
    assert title.duration_ms >= 5000
