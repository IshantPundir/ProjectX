# tests/vision/test_estimator_singleton.py
from app.modules.vision import actors as vision_actors


def test_get_estimator_builds_once(monkeypatch):
    vision_actors._estimator = None  # reset process singleton
    calls = {"n": 0}

    class _FakeEstimator:
        def __init__(self, **kwargs):
            calls["n"] += 1

    import app.modules.vision.gaze.mobilegaze as mg
    monkeypatch.setattr(mg, "MobileGazeEstimator", _FakeEstimator)

    a = vision_actors._get_estimator()
    b = vision_actors._get_estimator()
    assert a is b
    assert calls["n"] == 1
    vision_actors._estimator = None  # clean up for other tests


def test_get_estimator_passes_thread_cap(monkeypatch):
    vision_actors._estimator = None
    captured = {}

    class _FakeEstimator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import app.modules.vision.gaze.mobilegaze as mg
    monkeypatch.setattr(mg, "MobileGazeEstimator", _FakeEstimator)

    vision_actors._get_estimator()
    assert captured["intra_op_threads"] == 1  # vision_config.ort_intra_op_threads
    vision_actors._estimator = None
