from app.modules.interview_engine_v2.coverage import (
    CoverageState,
    CoverageTracker,
    ThreadStatus,
)


def _tracker():
    return CoverageTracker(
        signals=["python", "kafka", "leadership"],
        mandatory_signals=["python", "kafka"],
        soft_probe_cap=2,
    )


def test_initial_state_all_none():
    t = _tracker()
    assert t.state("python") is CoverageState.none
    assert t.uncovered_mandatory() == ["python", "kafka"]
    assert t.summary_for_result() == {"python": "none", "kafka": "none", "leadership": "none"}


def test_apply_delta_is_monotonic_by_rank():
    t = _tracker()
    t.apply_delta({"python": "partial"})
    assert t.state("python") is CoverageState.partial
    # a lower-rank proposal does NOT downgrade
    t.apply_delta({"python": "none"})
    assert t.state("python") is CoverageState.partial
    t.apply_delta({"python": "sufficient"})
    assert t.state("python") is CoverageState.sufficient


def test_sufficient_is_sticky():
    """A covered signal cannot be un-covered by a later weaker turn."""
    t = _tracker()
    t.apply_delta({"python": "sufficient"})
    t.apply_delta({"python": "partial"})
    assert t.state("python") is CoverageState.sufficient
    t.apply_delta({"python": "failed"})
    assert t.state("python") is CoverageState.sufficient


def test_failed_is_revisable_on_new_evidence():
    """Decision C / re-open-closed-thread: a knockout-candidate `failed` is NOT locked — if the
    candidate later volunteers contradicting evidence, partial/sufficient re-opens it. But a bare
    'none'/'failed' delta leaves it failed (no spurious downgrade)."""
    t = _tracker()
    t.apply_delta({"kafka": "failed"})
    assert t.state("kafka") is CoverageState.failed
    t.apply_delta({"kafka": "none"})                 # no new evidence -> stays failed
    assert t.state("kafka") is CoverageState.failed
    t.apply_delta({"kafka": "partial"})              # "actually, I have done X" -> re-opens
    assert t.state("kafka") is CoverageState.partial
    t.apply_delta({"kafka": "sufficient"})
    assert t.state("kafka") is CoverageState.sufficient


def test_cross_question_crediting_updates_any_signal():
    """An answer to Q2 that demonstrates Q4's signal updates Q4's coverage now (doc 09 §1)."""
    t = _tracker()
    # answering about leadership while on the python question still credits leadership
    applied = t.apply_delta({"python": "sufficient", "leadership": "partial"})
    assert applied == {"python": CoverageState.sufficient, "leadership": CoverageState.partial}
    assert t.state("leadership") is CoverageState.partial


def test_uncovered_mandatory_excludes_terminal():
    t = _tracker()
    t.apply_delta({"python": "sufficient"})  # covered
    t.apply_delta({"kafka": "failed"})       # also closed (absent)
    assert t.uncovered_mandatory() == []     # both threads closed
    assert t.is_covered("python") and t.is_covered("kafka")


def test_thread_status_sufficient():
    t = _tracker()
    t.apply_delta({"python": "sufficient"})
    assert t.thread_status(primary_signal="python", question_id="q1", tapped_out=False) is ThreadStatus.sufficient


def test_thread_status_absent_on_failed():
    t = _tracker()
    t.apply_delta({"kafka": "failed"})
    assert t.thread_status(primary_signal="kafka", question_id="q2", tapped_out=False) is ThreadStatus.absent


def test_thread_status_tapped_out_when_brain_says_so():
    t = _tracker()
    t.apply_delta({"python": "partial"})
    assert t.thread_status(primary_signal="python", question_id="q1", tapped_out=True) is ThreadStatus.tapped_out


def test_thread_status_tapped_out_on_soft_cap():
    t = _tracker()
    t.apply_delta({"python": "partial"})
    t.record_probe("q1")
    assert t.thread_status(primary_signal="python", question_id="q1", tapped_out=False) is ThreadStatus.in_progress
    t.record_probe("q1")  # 2 probes == soft cap
    assert t.thread_status(primary_signal="python", question_id="q1", tapped_out=False) is ThreadStatus.tapped_out


def test_thread_status_in_progress_otherwise():
    t = _tracker()
    t.apply_delta({"python": "partial"})
    assert t.thread_status(primary_signal="python", question_id="q1", tapped_out=False) is ThreadStatus.in_progress


def test_summary_for_prompt_is_compact_and_bounded():
    t = _tracker()
    t.apply_delta({"python": "sufficient", "kafka": "partial"})
    s = t.summary_for_prompt()
    assert "python=sufficient" in s and "kafka=partial" in s and "leadership=none" in s
    assert "\n" not in s  # single compact line (bounded dynamic suffix)


def test_unknown_signal_in_delta_is_tracked():
    """Cross-question crediting may name a signal not in the initial set (defensive)."""
    t = _tracker()
    t.apply_delta({"docker": "partial"})
    assert t.state("docker") is CoverageState.partial
