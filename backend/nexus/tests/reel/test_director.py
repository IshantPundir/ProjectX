"""Director EDL validation — pure-function table tests (lean nexus image).

v2 fit-pitch + answer-run model: a clip references an ANSWER RUN (a contiguous
group of candidate turns) by ref + [in_word, out_word] over the run's continuous
word index; validation resolves to a per-word list carrying (turn_commit, rel ms)
so a multi-turn clip maps to one contiguous video cut. These tests cover the
deterministic guardrails: bounds, edge-disfluency trim, duplicate dedup, the
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


def _cand(commit, n, *, step=1000, dur=900, qid="q1", prefix="w"):
    return {"role": "candidate", "timestamp_ms": commit, "question_id": qid,
            "words": [{"text": f"{prefix}{i}", "start_ms": i * step,
                       "end_ms": i * step + dur} for i in range(n)]}


def _cand_words(commit, texts, *, step=1000, dur=900, qid="q1"):
    return {"role": "candidate", "timestamp_ms": commit, "question_id": qid,
            "words": [{"text": t, "start_ms": i * step, "end_ms": i * step + dur}
                      for i, t in enumerate(texts)]}


def _agent():
    return {"role": "agent", "text": "ok"}


def _clip(ref, iw, ow, **kw):
    return ReelBeat(kind="clip", source_turn_ref=ref, in_word=iw, out_word=ow, **kw)


def _by_kind(vedl, kind):
    return [b for b in vedl.beats if b.kind == kind]


def _texts(beat):
    return [w["text"] for w in beat.words]


# --- word-index resolution over a run -------------------------------------

def test_clip_resolves_to_words_with_turn_and_rel_ms():
    edl = ReelEdlOut(beats=[_clip(100, 3, 8, caption="hi")])
    clip = _by_kind(validate_edl(edl, [_cand(100, 20)]), "clip")[0]
    assert clip.source_turn_ref == 100
    assert clip.words[0]["rel_start_ms"] == 3000          # word 3 start
    assert clip.words[-1]["rel_end_ms"] == 8 * 1000 + 900  # word 8 end
    assert all(w["turn_commit"] == 100 for w in clip.words)
    assert clip.caption == "hi"


def test_experience_beat_is_resolved_like_a_clip():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="experience", source_turn_ref=500, in_word=0, out_word=2),
        _clip(500, 4, 6),
    ])
    vedl = validate_edl(edl, [_cand(500, 10)])
    assert _by_kind(vedl, "experience")[0].words[0]["rel_start_ms"] == 0


# --- the #1 fix: a clip can span continuation turns (one run) --------------

def test_clip_spans_multiple_continuation_turns():
    # two consecutive candidate turns (no agent between) = one run, ref=100,
    # continuous indices 0..3 (turn 100) then 4..6 (turn 200).
    transcript = [_cand(100, 4), _cand(200, 3, prefix="x")]
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(100, 2, 5)]), transcript),
                    "clip")[0]
    commits = [w["turn_commit"] for w in clip.words]
    assert 100 in commits and 200 in commits        # spans both turns
    assert [w["text"] for w in clip.words] == ["w2", "w3", "x0", "x1"]
    # duration includes the boundary-pause estimate (>0 added at the turn edge)
    assert clip.duration_ms > 0


# --- hallucination rejection ----------------------------------------------

def test_unknown_run_ref_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(100, 0, 3), _clip(999999, 0, 3)])
    vedl = validate_edl(edl, [_cand(100, 10)])
    assert [b.source_turn_ref for b in _by_kind(vedl, "clip")] == [100]


def test_out_of_bounds_word_index_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(100, 0, 2), _clip(100, 0, 99)])
    assert len(_by_kind(validate_edl(edl, [_cand(100, 5)]), "clip")) == 1


def test_inverted_word_range_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(100, 7, 2), _clip(100, 1, 4)])
    kept = _by_kind(validate_edl(edl, [_cand(100, 10)]), "clip")
    assert len(kept) == 1 and kept[0].words[0]["text"] == "w1"


# --- #2: edge-disfluency trim ---------------------------------------------

def test_edge_disfluencies_trimmed_from_clip_ends():
    transcript = [_cand_words(100, ["so", "like", "i", "designed", "this", "uh"])]
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(100, 0, 5)]), transcript),
                    "clip")[0]
    assert _texts(clip) == ["i", "designed", "this"]   # so/like leading, uh trailing


def test_all_filler_clip_is_dropped():
    # agent between -> two separate runs (refs 100 and 200)
    transcript = [_cand_words(100, ["so", "like", "uh"]), _agent(),
                  _cand(200, 4, prefix="x")]
    edl = ReelEdlOut(beats=[_clip(100, 0, 2), _clip(200, 0, 3)])
    kept = _by_kind(validate_edl(edl, transcript), "clip")
    assert len(kept) == 1 and kept[0].source_turn_ref == 200


# --- dedup by overlapping word range --------------------------------------

def test_overlapping_word_range_is_deduped():
    edl = ReelEdlOut(beats=[_clip(100, 0, 5), _clip(100, 3, 8)])
    kept = _by_kind(validate_edl(edl, [_cand(100, 12)]), "clip")
    assert len(kept) == 1 and kept[0].words[0]["idx"] == 0


def test_disjoint_word_ranges_both_kept():
    edl = ReelEdlOut(beats=[_clip(100, 0, 2), _clip(100, 6, 8)])
    assert len(_by_kind(validate_edl(edl, [_cand(100, 12)]), "clip")) == 2


# --- per-clip soft cap ----------------------------------------------------

def test_overlong_clip_is_trimmed_to_the_soft_cap():
    # 0..24 on a 1000ms grid ~= 24.9s; soft cap 16s.
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(100, 0, 24)]),
                                 [_cand(100, 25)]), "clip")[0]
    assert clip.duration_ms <= 16_000
    assert clip.words[0]["text"] == "w0"


# --- total budget: group-by-lead-card -------------------------------------

def test_over_budget_drops_trailing_point_groups_keeping_one():
    # agents between -> four separate runs (refs 1..4)
    transcript = []
    for i in (1, 2, 3, 4):
        transcript += [_cand(i, 12), _agent()]
    beats = [ReelBeat(kind="title", on_screen_text="t"),
             ReelBeat(kind="match", on_screen_text="m")]
    for i in (1, 2, 3, 4):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 11)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    kinds = [b.kind for b in vedl.beats]
    assert kinds[0] == "title" and kinds[-1] == "outro"
    clips = _by_kind(vedl, "clip")
    assert 1 <= len(clips) < 4
    assert len(_by_kind(vedl, "point")) == len(clips)
    assert vedl.duration_ms <= MAX_TOTAL_MS


# --- >=1 clip or fail ------------------------------------------------------

def test_zero_clip_beats_raises():
    edl = ReelEdlOut(beats=[ReelBeat(kind="title", on_screen_text="t"),
                            ReelBeat(kind="outro", on_screen_text="o")])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_cand(1, 5)])


def test_all_clips_hallucinated_raises():
    edl = ReelEdlOut(beats=[_clip(777, 0, 3), _clip(888, 0, 3)])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_cand(1, 5)])


# --- card duration estimate ------------------------------------------------

def test_match_card_duration_estimated_from_narration():
    narration = " ".join(["word"] * 22)   # 22 / 2.75 = 8s, above the match floor
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="match", on_screen_text="m", narration_text=narration),
        _clip(1, 0, 5)])
    match = _by_kind(validate_edl(edl, [_cand(1, 6)]), "match")[0]
    assert match.duration_ms >= 8000


def test_point_card_uses_its_floor_when_narration_short():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="point", on_screen_text="p", narration_text="short"),
        _clip(1, 0, 5)])
    assert _by_kind(validate_edl(edl, [_cand(1, 6)]), "point")[0].duration_ms == 3500
