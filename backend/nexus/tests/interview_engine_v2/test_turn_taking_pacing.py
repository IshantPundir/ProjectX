"""pacing.py — endpointing config + hold-space pacer (pure, no livekit)."""

from app.modules.interview_engine_v2.turn_taking.pacing import (
    EndpointingSettings,
    HoldSpacePacer,
    build_endpointing_options,
)


def test_build_endpointing_options_shape():
    opts = build_endpointing_options(
        EndpointingSettings(mode="dynamic", min_delay=0.8, max_delay=4.5)
    )
    assert opts == {"mode": "dynamic", "min_delay": 0.8, "max_delay": 4.5}


def test_hold_space_fires_once_after_threshold():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    pacer.on_pause_started(at_s=10.0)
    assert pacer.cue_due(now_s=12.0) is False        # 2.0s < 2.5s
    assert pacer.cue_due(now_s=12.6) is True          # crossed 2.5s
    pacer.mark_cued()
    assert pacer.cue_due(now_s=20.0) is False          # only once per pause


def test_hold_space_resets_on_resume():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    pacer.on_pause_started(at_s=10.0)
    assert pacer.cue_due(now_s=13.0) is True
    pacer.mark_cued()
    pacer.on_resume()                                   # candidate spoke again
    pacer.on_pause_started(at_s=20.0)                   # a new pause
    assert pacer.cue_due(now_s=23.0) is True            # cue owed again


def test_hold_space_disabled_never_fires():
    pacer = HoldSpacePacer(enabled=False, delay_s=2.5)
    pacer.on_pause_started(at_s=0.0)
    assert pacer.cue_due(now_s=100.0) is False


def test_cue_due_false_when_no_pause_open():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    assert pacer.cue_due(now_s=5.0) is False            # no pause started
