"""The v2 engine package imports cleanly and exposes its public API."""


def test_package_imports():
    import app.modules.interview_engine as m2

    assert hasattr(m2, "__all__")


def test_public_api_lists_pure_artifacts():
    from app.modules.interview_engine import __all__

    # Pure artifacts land in later tasks; `run` is exported lazily (Task 11).
    for name in ("Directive", "DirectiveAct", "DirectiveTone",
                 "DirectiveController", "TurnDecisionRecord", "run"):
        assert name in __all__
