"""Director EDL validation — pure-function table tests (lean nexus image).

gen-3 fit-pitch + answer-run model: a clip references an ANSWER RUN (a contiguous
group of candidate turns) by ``ref`` (a RUN INDEX) + [in_word, out_word] over the
run's continuous word index; validation resolves to a per-word list carrying
(``turn_ref``, ``turn_start_ms``, rel ms) so a multi-turn clip maps to one
contiguous video cut. These tests cover the deterministic guardrails: bounds,
edge-disfluency trim, duplicate dedup, the duration budget + group-by-lead-card
drop order, and >=1-clip-or-fail.

NOTE: ``source_turn_ref`` is the RUN INDEX (0-based) — runs are emitted in
transcript order, so the Nth answer run has ref N-1.
"""
import pytest
from pydantic import ValidationError

from app.config import settings
from app.modules.reel.director import (
    NoClipBeatsError,
    ReelBeat,
    ReelEdlOut,
    validate_edl,
)


def _cand(turn_ref, n, *, start_ms=0, step=1000, dur=900, qid="q1", prefix="w"):
    return {"speaker": "candidate", "turn_ref": turn_ref, "question_id": qid,
            "span": {"start_ms": start_ms, "end_ms": start_ms + 1},
            "words": [{"text": f"{prefix}{i}", "start_ms": i * step,
                       "end_ms": i * step + dur} for i in range(n)]}


def _cand_words(turn_ref, texts, *, start_ms=0, step=1000, dur=900, qid="q1"):
    return {"speaker": "candidate", "turn_ref": turn_ref, "question_id": qid,
            "span": {"start_ms": start_ms, "end_ms": start_ms + 1},
            "words": [{"text": t, "start_ms": i * step, "end_ms": i * step + dur}
                      for i, t in enumerate(texts)]}


def _agent():
    return {"speaker": "agent", "turn_ref": "a", "span": {"start_ms": 0, "end_ms": 1},
            "text": "ok", "words": []}


def _clip(ref, iw, ow, **kw):
    return ReelBeat(kind="clip", source_turn_ref=ref, in_word=iw, out_word=ow, **kw)


def _by_kind(vedl, kind):
    return [b for b in vedl.beats if b.kind == kind]


def _texts(beat):
    return [w["text"] for w in beat.words]


# --- word-index resolution over a run -------------------------------------

def test_clip_resolves_to_words_with_turn_and_rel_ms():
    # one run -> ref 0; turn_start_ms 7000
    edl = ReelEdlOut(beats=[_clip(0, 3, 8)])
    clip = _by_kind(validate_edl(edl, [_cand("t-1", 20, start_ms=7000)]), "clip")[0]
    assert clip.source_turn_ref == 0
    assert clip.words[0]["rel_start_ms"] == 3000          # word 3 start
    assert clip.words[-1]["rel_end_ms"] == 8 * 1000 + 900  # word 8 end
    assert all(w["turn_ref"] == "t-1" for w in clip.words)
    assert all(w["turn_start_ms"] == 7000 for w in clip.words)


def test_experience_beat_is_resolved_like_a_clip():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="experience", source_turn_ref=0, in_word=0, out_word=2),
        _clip(0, 4, 6),
    ])
    vedl = validate_edl(edl, [_cand("t-9", 10)])
    assert _by_kind(vedl, "experience")[0].words[0]["rel_start_ms"] == 0


# --- the #1 fix: a clip can span continuation turns (one run) --------------

def test_clip_spans_multiple_continuation_turns():
    # two consecutive candidate turns (no agent between) = one run, ref=0,
    # continuous indices 0..3 (turn t-1) then 4..6 (turn t-2).
    transcript = [_cand("t-1", 4, start_ms=1000),
                  _cand("t-2", 3, start_ms=8000, prefix="x")]
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(0, 2, 5)]), transcript),
                    "clip")[0]
    refs = {w["turn_ref"] for w in clip.words}
    assert refs == {"t-1", "t-2"}        # spans both turns
    # each word carries its OWN turn_start_ms
    by_text = {w["text"]: w["turn_start_ms"] for w in clip.words}
    assert by_text["w2"] == 1000 and by_text["x0"] == 8000
    assert [w["text"] for w in clip.words] == ["w2", "w3", "x0", "x1"]
    # duration includes the boundary-pause estimate (>0 added at the turn edge)
    assert clip.duration_ms > 0


# --- hallucination rejection ----------------------------------------------

def test_unknown_run_ref_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(0, 0, 3), _clip(999999, 0, 3)])
    vedl = validate_edl(edl, [_cand("t-1", 10)])
    assert [b.source_turn_ref for b in _by_kind(vedl, "clip")] == [0]


def test_out_of_bounds_word_index_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(0, 0, 2), _clip(0, 0, 99)])
    assert len(_by_kind(validate_edl(edl, [_cand("t-1", 5)]), "clip")) == 1


def test_inverted_word_range_drops_the_beat():
    edl = ReelEdlOut(beats=[_clip(0, 7, 2), _clip(0, 1, 4)])
    kept = _by_kind(validate_edl(edl, [_cand("t-1", 10)]), "clip")
    assert len(kept) == 1 and kept[0].words[0]["text"] == "w1"


# --- #2: edge-disfluency trim ---------------------------------------------

def test_edge_disfluencies_trimmed_from_clip_ends():
    transcript = [_cand_words("t-1", ["so", "like", "i", "designed", "this", "uh"])]
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(0, 0, 5)]), transcript),
                    "clip")[0]
    assert _texts(clip) == ["i", "designed", "this"]   # so/like leading, uh trailing


def test_all_filler_clip_is_dropped():
    # agent between -> two separate runs (refs 0 and 1)
    transcript = [_cand_words("t-1", ["so", "like", "uh"]), _agent(),
                  _cand("t-2", 4, prefix="x")]
    edl = ReelEdlOut(beats=[_clip(0, 0, 2), _clip(1, 0, 3)])
    kept = _by_kind(validate_edl(edl, transcript), "clip")
    assert len(kept) == 1 and kept[0].source_turn_ref == 1


# --- dedup by overlapping word range --------------------------------------

def test_overlapping_word_range_is_deduped():
    edl = ReelEdlOut(beats=[_clip(0, 0, 5), _clip(0, 3, 8)])
    kept = _by_kind(validate_edl(edl, [_cand("t-1", 12)]), "clip")
    assert len(kept) == 1 and kept[0].words[0]["idx"] == 0


def test_disjoint_word_ranges_both_kept():
    edl = ReelEdlOut(beats=[_clip(0, 0, 2), _clip(0, 6, 8)])
    assert len(_by_kind(validate_edl(edl, [_cand("t-1", 12)]), "clip")) == 2


# --- per-clip soft cap (config-driven) ------------------------------------

def test_overlong_clip_is_trimmed_when_cap_configured_low(monkeypatch):
    # Mechanism-preserved: with the per-clip soft cap configured LOW (16s), a
    # ~24.9s clip (0..24 on a 1000ms grid) is still trimmed inward to fit.
    monkeypatch.setattr(settings, "reel_clip_soft_cap_ms", 16_000)
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(0, 0, 24)]),
                                 [_cand("t-1", 25)]), "clip")[0]
    assert clip.duration_ms <= 16_000
    assert clip.words[0]["text"] == "w0"


def test_long_clip_not_trimmed_at_default_high_cap():
    # At the relaxed default (~10 min), a >16s clip survives un-trimmed so the
    # candidate's full evidence is shown.
    assert settings.reel_clip_soft_cap_ms >= 600_000
    clip = _by_kind(validate_edl(ReelEdlOut(beats=[_clip(0, 0, 24)]),
                                 [_cand("t-1", 25)]), "clip")[0]
    assert clip.words[0]["text"] == "w0"
    assert clip.words[-1]["text"] == "w24"   # nothing trimmed off the tail
    assert clip.duration_ms > 16_000


# --- total budget: group-by-lead-card (config-driven) ---------------------

def test_over_budget_drops_trailing_point_groups_keeping_one(monkeypatch):
    # Mechanism-preserved: with the total budget configured LOW (80s), seven
    # ~15s clips far exceed it, forcing trailing point-groups (card + clip) to
    # drop. agents between -> separate runs (refs 0..6). No title/match intro.
    monkeypatch.setattr(settings, "reel_max_total_ms", 80_000)
    n = 7
    transcript = []
    for i in range(n):
        transcript += [_cand(f"t-{i}", 16), _agent()]
    beats = []
    for i in range(n):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 15)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    kinds = [b.kind for b in vedl.beats]
    assert kinds[0] == "point" and kinds[-1] == "outro"
    clips = _by_kind(vedl, "clip")
    assert 1 <= len(clips) < 7
    assert len(_by_kind(vedl, "point")) == len(clips)
    assert vedl.duration_ms <= settings.reel_max_total_ms


def test_no_clips_dropped_at_default_high_budget():
    # At the relaxed default (~1 h), many clips all survive — none dropped — so
    # the candidate's full evidence is shown. No title/match intro.
    assert settings.reel_max_total_ms >= 3_600_000
    n = 7
    transcript = []
    for i in range(n):
        transcript += [_cand(f"t-{i}", 16), _agent()]
    beats = []
    for i in range(n):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 15)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    assert len(_by_kind(vedl, "clip")) == n
    assert len(_by_kind(vedl, "point")) == n


# --- >=1 clip or fail ------------------------------------------------------

def test_zero_clip_beats_raises():
    edl = ReelEdlOut(beats=[ReelBeat(kind="point", on_screen_text="p"),
                            ReelBeat(kind="outro", on_screen_text="o")])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_cand("t-1", 5)])


def test_all_clips_hallucinated_raises():
    edl = ReelEdlOut(beats=[_clip(777, 0, 3), _clip(888, 0, 3)])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_cand("t-1", 5)])


# --- card duration estimate ------------------------------------------------

def test_point_card_duration_estimated_from_narration():
    narration = " ".join(["word"] * 22)   # 22 / 2.75 = 8s, above the point floor
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="point", on_screen_text="p", narration_text=narration),
        _clip(0, 0, 5)])
    point = _by_kind(validate_edl(edl, [_cand("t-1", 6)]), "point")[0]
    assert point.duration_ms >= 8000


def test_point_card_uses_its_floor_when_narration_short():
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="point", on_screen_text="p", narration_text="short"),
        _clip(0, 0, 5)])
    assert _by_kind(validate_edl(edl, [_cand("t-1", 6)]), "point")[0].duration_ms == 3500


@pytest.mark.parametrize("dead_kind", ["title", "match"])
def test_removed_intro_kinds_are_rejected_by_schema(dead_kind):
    with pytest.raises(ValidationError):
        ReelBeat(kind=dead_kind, on_screen_text="x")
