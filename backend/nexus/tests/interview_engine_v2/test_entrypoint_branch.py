"""The engine entrypoint routes to v2 iff SessionConfig.interview_engine_version == 'v2'.

Imports the predicate through the package PUBLIC API (not the livekit-bearing agent
module) — the same surface the legacy entrypoint uses.
"""

from app.modules.interview_engine_v2 import should_run_v2


def test_should_run_v2_true_for_v2():
    class _Cfg:
        interview_engine_version = "v2"
    assert should_run_v2(_Cfg()) is True


def test_should_run_v2_false_for_v1():
    class _Cfg:
        interview_engine_version = "v1"
    assert should_run_v2(_Cfg()) is False


def test_should_run_v2_false_when_missing():
    class _Cfg:
        pass  # defensive: a config without the field stays on v1
    assert should_run_v2(_Cfg()) is False
