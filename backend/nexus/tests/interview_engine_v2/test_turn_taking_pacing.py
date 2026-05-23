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


# ---------------------------------------------------------------------------
# M5 Task 8b — incompleteness gate (pure cue-due logic)
# The live "never on a complete answer" property is enforced in agent.py via
# the delay-above-commit-latency proxy and is validated by the Task 10
# talk-test, not unit-tested here.  These tests cover the pure gate contract.
# ---------------------------------------------------------------------------


def test_hold_space_cue_fires_on_open_incomplete_pause():
    pacer = HoldSpacePacer(enabled=True, delay_s=2.5)
    pacer.on_resume()
    pacer.on_pause_started(at_s=100.0)          # candidate paused mid-thought
    assert pacer.cue_due(now_s=101.0) is False  # < delay -> wait
    assert pacer.cue_due(now_s=103.0) is True   # >= delay, turn still open -> cue


def test_hold_space_cue_suppressed_when_disabled():
    pacer = HoldSpacePacer(enabled=False, delay_s=2.5)
    pacer.on_resume()
    pacer.on_pause_started(at_s=100.0)
    assert pacer.cue_due(now_s=110.0) is False


def test_hold_space_cue_not_due_before_delay():
    """Cue must not fire before the configured delay elapses (proxy gate margin)."""
    pacer = HoldSpacePacer(enabled=True, delay_s=3.0)
    pacer.on_pause_started(at_s=0.0)
    assert pacer.cue_due(now_s=2.9) is False   # just under delay
    assert pacer.cue_due(now_s=3.0) is True    # exactly at delay


def test_hold_space_cue_cleared_on_resume_simulates_complete_answer():
    """Simulates the agent.py proxy: on_user_turn_completed triggers on_resume()
    for the next question, clearing any pending cue — ensuring the cue can never
    fire after a completed turn's floor is handed back to the agent."""
    pacer = HoldSpacePacer(enabled=True, delay_s=3.0)
    pacer.on_pause_started(at_s=0.0)
    # Turn completes before delay elapses: harness calls on_resume via _pose_question
    pacer.on_resume()
    # Even if we somehow reached the delay, cue is not due (no active pause)
    assert pacer.cue_due(now_s=10.0) is False
